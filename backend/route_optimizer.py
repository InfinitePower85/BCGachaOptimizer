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

solve() optionally takes max_elevens, capping how many guaranteed-11s the route may use
in total. It's tracked as a fourth memo dimension, (track, roll, mask, elevens_used):
raising the cap only ever *adds* an available action at some states (the branch is
skipped once elevens_used == max_elevens, same "extra" gate as the roll < max_rolls one),
so it's a strict superset/subset relationship on the reachable-state and action space,
same as max_rolls -- score(..., max_rolls, max_elevens) is monotonic non-decreasing in
EACH of max_rolls and max_elevens individually, with the other held fixed. That's what
justifies the two binary-search helpers below.

It's important that it's "each individually, other held fixed" and not a single joint
minimum: rolls and elevens are two independent resources, and there is in general a real
trade-off frontier between them (e.g. full collection might need either 90 rolls with 5
elevens, or 120 rolls with 2, or 200 rolls with 0) rather than one dominant answer. So
"minimum rolls needed" and "minimum elevens needed" are two separate questions, each
answered with the *other* pinned to a specific value (or left unlimited) --
min_rolls_for_full_collection() and min_elevens_for_full_collection() below, not a single
"minimize both."
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
    elevens_used: int = 0    # number of guaranteed-11 steps in route


def solve(pool, target_units, max_rolls, start_track="A", start_roll=1, max_elevens=None):
    """Find the best route. Returns an OptimizationResult.

    max_rolls bounds the *roll number reached*, matching how bc.godfat.org numbers
    positions (a guaranteed-11 advances the position by ~10-11, consistent with it
    drawing 11 units) -- not a separate ticket/cat-food currency budget.

    max_elevens, if given, caps how many guaranteed-11s the route may use in total
    (unlimited if None/omitted, the default). See the module docstring for why this and
    max_rolls are two independent resources, not one.
    """
    targets = sorted(target_units)
    bit_of = {name: 1 << i for i, name in enumerate(targets)}

    memo = {}  # (track, roll, mask, elevens_used) -> (score, action)  action is None or a step dict

    def gained_mask(mask, names):
        for name in names:
            mask |= bit_of.get(name, 0)
        return mask

    def visit(track, roll, mask, elevens_used):
        key = (track, roll, mask, elevens_used)
        cached = memo.get(key)
        if cached is not None:
            return cached

        best = (bin(mask).count("1"), None)  # option: stop here

        if roll < max_rolls and pool.has_single(track, roll):
            cell = pool.normal[track][roll]
            new_mask = gained_mask(mask, [cell.cat_name])
            sub_score, _ = visit(track, roll + 1, new_mask, elevens_used)
            if sub_score > best[0]:
                best = (sub_score, {
                    "type": "single", "track": track, "roll": roll,
                    "units": [cell.cat_name], "next": (track, roll + 1),
                })

        eleven_allowed = max_elevens is None or elevens_used < max_elevens
        if eleven_allowed and roll < max_rolls and pool.has_guaranteed_eleven(track, roll):
            picks = [pool.normal[track][roll + i].cat_name for i in range(10)]
            picks.append(pool.guaranteed[track][roll].cat_name)
            new_mask = gained_mask(mask, picks)
            dest_track, dest_roll = pool.guaranteed[track][roll].link_target
            sub_score, _ = visit(dest_track, dest_roll, new_mask, elevens_used + 1)
            if sub_score > best[0]:
                best = (sub_score, {
                    "type": "guaranteed_eleven", "track": track, "roll": roll,
                    "units": picks, "next": (dest_track, dest_roll),
                })

        memo[key] = best
        return best

    final_score, _ = visit(start_track, start_roll, 0, 0)

    route = []
    track, roll, mask, elevens_used = start_track, start_roll, 0, 0
    while True:
        _, action = memo[(track, roll, mask, elevens_used)]
        if action is None:
            break
        route.append({"type": action["type"], "track": action["track"],
                       "roll": action["roll"], "units": action["units"]})
        mask = gained_mask(mask, action["units"])
        if action["type"] == "guaranteed_eleven":
            elevens_used += 1
        track, roll = action["next"]

    collected = [name for name in targets if mask & bit_of[name]]
    used = sum(1 for step in route if step["type"] == "guaranteed_eleven")
    return OptimizationResult(score=final_score, collected=collected, route=route, elevens_used=used)


def solve_from_csv(csv_text, target_units, max_rolls, **kwargs):
    return solve(parse_pool(csv_text), target_units, max_rolls, **kwargs)


def _pool_extent(pool):
    """The highest roll number appearing anywhere in the pool (either track, normal or
    guaranteed cells). Used as a default upper bound below -- there's nothing to gain by
    searching past it."""
    rolls = [roll for track in TRACKS for cells in (pool.normal[track], pool.guaranteed[track]) for roll in cells]
    return max(rolls, default=0)


def min_rolls_for_full_collection(pool, target_units, max_elevens=None, max_rolls_cap=None,
                                   start_track="A", start_roll=1):
    """Binary search for the fewest rolls needed to collect every target unit, with
    max_elevens held fixed (unlimited if None). Returns (min_rolls, OptimizationResult),
    or (None, None) if even max_rolls_cap rolls (default: the highest roll number
    anywhere in the pool) isn't enough.

    Valid because score(max_rolls, max_elevens) is monotonic non-decreasing in max_rolls
    for any fixed max_elevens (see the module docstring) -- so "does this roll count
    collect everything" is a step function that only turns from False to True once, and
    binary search finds that point in O(log max_rolls_cap) calls to solve() instead of a
    linear scan.
    """
    targets = set(target_units)
    if max_rolls_cap is None:
        max_rolls_cap = _pool_extent(pool) + 1

    def try_rolls(r):
        result = solve(pool, targets, r, start_track=start_track, start_roll=start_roll, max_elevens=max_elevens)
        return result if result.score == len(targets) else None

    best = try_rolls(max_rolls_cap)
    if best is None:
        return None, None  # not achievable even with the most rolls we're willing to try

    lo, hi = start_roll, max_rolls_cap
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = try_rolls(mid)
        if candidate is not None:
            hi = mid
            best = candidate
        else:
            lo = mid + 1
    return hi, best


def min_elevens_for_full_collection(pool, target_units, max_rolls, max_elevens_cap=None,
                                     start_track="A", start_roll=1):
    """Binary search for the fewest guaranteed-11s needed to collect every target unit,
    with max_rolls held fixed. Returns (min_elevens, OptimizationResult), or
    (None, None) if even max_elevens_cap elevens (default: max_rolls, always more than
    could ever usefully be needed) isn't enough within max_rolls.

    Valid for the same reason as min_rolls_for_full_collection(): score is monotonic
    non-decreasing in max_elevens for any fixed max_rolls too (allowing one more
    guaranteed-11 only ever adds an available action, never removes one -- the search
    can always just choose not to use it), so the same binary search applies here.
    """
    targets = set(target_units)
    if max_elevens_cap is None:
        max_elevens_cap = max_rolls

    def try_elevens(x):
        result = solve(pool, targets, max_rolls, start_track=start_track, start_roll=start_roll, max_elevens=x)
        return result if result.score == len(targets) else None

    best = try_elevens(max_elevens_cap)
    if best is None:
        return None, None

    lo, hi = 0, max_elevens_cap
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = try_elevens(mid)
        if candidate is not None:
            hi = mid
            best = candidate
        else:
            lo = mid + 1
    return hi, best
