"""
Route optimizer: given a bc.godfat.org tracks CSV (as produced by data_download.py's
cells_to_csv / the server's /tracks endpoint) and a set of target unit names, find the
sequence of single rolls and guaranteed-11 rolls that collects the most distinct target
units within a roll budget.

Roll rules (see readme.md):
  - Single roll: draw the "normal" cell at the current (track, roll), then advance to
    (track, roll + 1).
  - Guaranteed-11: draw the 10 "normal" cells at (track, roll .. roll+9) PLUS the
    "guaranteed" cell at (track, roll) as an 11th, forced unit (11 units total). Then
    jump to whatever (track, roll) the guaranteed cell's `link` column names.

We deliberately do NOT re-derive the "+10 for A->B, +11 for B->A" landing-position math,
or hand-implement the "two dupes in a row switches track" rule from readme.md. BC Godfat
has already computed both correctly for the real game and encoded the result in the
`link` column, so we just follow it. This also means search state only needs
(track, roll) as position -- no "previous unit drawn" is needed, and no per-track offset
constants appear anywhere below.

Search: DFS memoized on (track, roll, bitmask of target units already collected). Only
DISTINCT target units count toward the score, so two different roll sequences that reach
the same (track, roll) having found the same targets have the same best future outcome
regardless of how they got there -- memoizing on that triple collapses the large number
of roll orderings that differ only in non-target ("wasted") picks along the way.

We looked at also adding upper-bound branch-and-bound pruning on top of this, but it
does not compose safely with *exact* memoization: a subtree pruned because it can't beat
the current global best would memoize a possibly-underestimated value, which a different,
unrelated caller could then reuse incorrectly. So this is memoization-only -- correct and
simple. If it's ever too slow for very large target lists, that's the next thing to add
(likely as an incumbent-tracking traversal instead of a bottom-up memoized one).
"""

import csv
import io
import re
from dataclasses import dataclass

LINK_TARGET_RE = re.compile(r"(\d+)([AB])")
TRACKS = ("A", "B")


@dataclass(frozen=True)
class Cell:
    cat_name: str
    rarity: str
    link_target: tuple | None = None  # (track, roll) a guaranteed cell jumps to


@dataclass(frozen=True)
class Pool:
    """One event's tracks, indexed by (track, roll) for O(1) lookup."""
    normal: dict      # track -> {roll: Cell}
    guaranteed: dict  # track -> {roll: Cell}

    def has_single(self, track, roll):
        return roll in self.normal[track]

    def has_guaranteed_eleven(self, track, roll):
        """True if a guaranteed-11 can be started at (track, roll): the 10 interim
        single-roll cells all exist, and the guaranteed cell has a parsed destination."""
        cell = self.guaranteed[track].get(roll)
        if cell is None or cell.link_target is None:
            return False
        return all(self.has_single(track, roll + i) for i in range(10))


REQUIRED_COLUMNS = {"track", "roll", "guaranteed", "cat_name", "rarity", "link"}


def parse_pool(csv_text):
    """Build a Pool from tracks.csv text (as returned by cells_to_csv / GET /tracks).
    Raises ValueError if the required columns aren't present (e.g. an empty or
    unrelated CSV) rather than silently returning an empty, useless Pool."""
    normal = {t: {} for t in TRACKS}
    guaranteed = {t: {} for t in TRACKS}
    reader = csv.DictReader(io.StringIO(csv_text))
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"csv is missing required column(s): {', '.join(sorted(missing))}")

    for row in reader:
        track = row["track"]
        if track not in TRACKS:
            continue
        roll = int(row["roll"])
        is_guaranteed = row["guaranteed"] == "True"
        link_target = None
        if is_guaranteed and row.get("link"):
            m = LINK_TARGET_RE.search(row["link"])
            if m:
                link_target = (m.group(2), int(m.group(1)))
        cell = Cell(cat_name=row["cat_name"], rarity=row["rarity"], link_target=link_target)
        (guaranteed if is_guaranteed else normal)[track][roll] = cell
    return Pool(normal=normal, guaranteed=guaranteed)


@dataclass(frozen=True)
class OptimizationResult:
    score: int              # number of distinct target units collected
    collected: list         # their names
    route: list              # ordered list of steps taken, each a dict


def solve(pool, target_units, max_rolls, start_track="A", start_roll=1):
    """Find the best route. Returns an OptimizationResult.

    max_rolls bounds the *roll number reached*, matching how bc.godfat.org numbers
    positions (a guaranteed-11 advances the position by ~10-11, consistent with it
    drawing 11 units) -- not a separate ticket/cat-food currency budget.
    """
    targets = sorted(target_units)
    bit_of = {name: 1 << i for i, name in enumerate(targets)}

    memo = {}  # (track, roll, mask) -> (score, action)  action is None or a step dict

    def gained_mask(mask, names):
        for name in names:
            mask |= bit_of.get(name, 0)
        return mask

    def visit(track, roll, mask):
        key = (track, roll, mask)
        cached = memo.get(key)
        if cached is not None:
            return cached

        best = (bin(mask).count("1"), None)  # option: stop here

        if roll < max_rolls and pool.has_single(track, roll):
            cell = pool.normal[track][roll]
            new_mask = gained_mask(mask, [cell.cat_name])
            sub_score, _ = visit(track, roll + 1, new_mask)
            if sub_score > best[0]:
                best = (sub_score, {
                    "type": "single", "track": track, "roll": roll,
                    "units": [cell.cat_name], "next": (track, roll + 1),
                })

        if roll < max_rolls and pool.has_guaranteed_eleven(track, roll):
            picks = [pool.normal[track][roll + i].cat_name for i in range(10)]
            picks.append(pool.guaranteed[track][roll].cat_name)
            new_mask = gained_mask(mask, picks)
            dest_track, dest_roll = pool.guaranteed[track][roll].link_target
            sub_score, _ = visit(dest_track, dest_roll, new_mask)
            if sub_score > best[0]:
                best = (sub_score, {
                    "type": "guaranteed_eleven", "track": track, "roll": roll,
                    "units": picks, "next": (dest_track, dest_roll),
                })

        memo[key] = best
        return best

    final_score, _ = visit(start_track, start_roll, 0)

    route = []
    track, roll, mask = start_track, start_roll, 0
    while True:
        _, action = memo[(track, roll, mask)]
        if action is None:
            break
        route.append({"type": action["type"], "track": action["track"],
                       "roll": action["roll"], "units": action["units"]})
        mask = gained_mask(mask, action["units"])
        track, roll = action["next"]

    collected = [name for name in targets if mask & bit_of[name]]
    return OptimizationResult(score=final_score, collected=collected, route=route)


def solve_from_csv(csv_text, target_units, max_rolls, **kwargs):
    return solve(parse_pool(csv_text), target_units, max_rolls, **kwargs)
