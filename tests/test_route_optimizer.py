"""
Tests for route_optimizer.py.

Small synthetic CSVs (never real Godfat data, no network) exercise parsing, single
rolls, guaranteed-11s, track switching via the `link` column, and boundary cases.
A brute-force reference search cross-checks the memoized solver's score on random
small pools, since the memoization is the part most likely to silently drop a path.
"""

import io
import itertools
import random

import pytest

from route_optimizer import (
    Pool,
    min_elevens_for_full_collection,
    min_rolls_for_full_collection,
    parse_pool,
    solve,
    solve_from_csv,
)

FIELDS = ["position", "roll", "track", "guaranteed", "cat_id", "cat_name", "rarity", "link"]


def make_csv(rows):
    """rows: list of dicts with any subset of FIELDS; missing fields default sensibly."""
    lines = [",".join(FIELDS)]
    for r in rows:
        full = {
            "position": f"{r['roll']}{r['track']}", "cat_id": "", "rarity": "rare", "link": "", **r,
        }
        lines.append(",".join(str(full[f]) for f in FIELDS))
    return "\n".join(lines) + "\n"


def normal_row(track, roll, name):
    return {"roll": roll, "track": track, "guaranteed": "False", "cat_name": name}


def guaranteed_row(track, roll, name, link):
    return {"roll": roll, "track": track, "guaranteed": "True", "cat_name": name, "link": link}


# ---------- parse_pool ----------

def test_parses_normal_and_guaranteed_cells():
    csv_text = make_csv([
        normal_row("A", 1, "Cat1"),
        guaranteed_row("A", 1, "Uber1", "-> 11B"),
    ])
    pool = parse_pool(csv_text)
    assert pool.normal["A"][1].cat_name == "Cat1"
    assert pool.guaranteed["A"][1].cat_name == "Uber1"
    assert pool.guaranteed["A"][1].link_target == ("B", 11)


def test_guaranteed_cell_without_link_has_no_target():
    csv_text = make_csv([guaranteed_row("A", 1, "Uber1", "")])
    pool = parse_pool(csv_text)
    assert pool.guaranteed["A"][1].link_target is None


@pytest.mark.parametrize("csv_text", ["not,a,valid,tracks,csv\n", "", "just one column\nvalue\n"])
def test_parse_pool_rejects_csv_missing_required_columns(csv_text):
    with pytest.raises(ValueError, match="missing required column"):
        parse_pool(csv_text)


def test_link_arrow_direction_is_ignored():
    # only the destination number+track is used, matching either arrow direction
    csv_text = make_csv([guaranteed_row("B", 1, "Uber1", "<- 12A")])
    pool = parse_pool(csv_text)
    assert pool.guaranteed["B"][1].link_target == ("A", 12)


# ---------- Pool.has_single / has_guaranteed_eleven ----------

def test_has_single_false_when_missing():
    pool = parse_pool(make_csv([normal_row("A", 1, "Cat1")]))
    assert pool.has_single("A", 1) is True
    assert pool.has_single("A", 2) is False


def test_guaranteed_eleven_needs_all_ten_interim_cells():
    rows = [normal_row("A", r, f"c{r}") for r in range(1, 10)]  # only 1..9, missing 10
    rows.append(guaranteed_row("A", 1, "Uber", "-> 11B"))
    pool = parse_pool(make_csv(rows))
    assert pool.has_guaranteed_eleven("A", 1) is False  # roll 10 (1+9) is missing

    rows.append(normal_row("A", 10, "c10"))
    pool = parse_pool(make_csv(rows))
    assert pool.has_guaranteed_eleven("A", 1) is True


def test_guaranteed_eleven_false_without_link_target():
    rows = [normal_row("A", r, f"c{r}") for r in range(1, 11)]
    rows.append(guaranteed_row("A", 1, "Uber", ""))  # no parseable link
    pool = parse_pool(make_csv(rows))
    assert pool.has_guaranteed_eleven("A", 1) is False


# ---------- solve(): single-roll-only straight line ----------

def straight_pool(track, names, start=1):
    return parse_pool(make_csv([normal_row(track, start + i, n) for i, n in enumerate(names)]))


def test_collects_targets_within_budget():
    pool = straight_pool("A", ["Junk", "Target1", "Junk", "Target2", "Junk"])
    result = solve(pool, {"Target1", "Target2"}, max_rolls=6, start_track="A", start_roll=1)
    assert result.score == 2
    assert sorted(result.collected) == ["Target1", "Target2"]


def test_stops_at_roll_budget():
    pool = straight_pool("A", ["Junk", "Target1"])  # Target1 is at roll 2
    result = solve(pool, {"Target1"}, max_rolls=1, start_track="A", start_roll=1)
    assert result.score == 0  # budget runs out before reaching roll 2


def test_target_never_appearing_scores_zero():
    pool = straight_pool("A", ["Junk", "Junk2"])
    result = solve(pool, {"Nowhere"}, max_rolls=5, start_track="A", start_roll=1)
    assert result.score == 0
    assert result.collected == []


def test_no_rolls_available_from_the_start():
    pool = straight_pool("A", ["Target1"])
    result = solve(pool, {"Target1"}, max_rolls=1, start_track="A", start_roll=1)
    assert result.score == 0
    assert result.route == []


# ---------- solve(): guaranteed-11 and track switching ----------

def test_guaranteed_eleven_is_taken_when_it_reaches_an_otherwise_unreachable_target():
    # Track A has no path to "UberOnly" via single rolls; only the guaranteed-11 gets it.
    rows = [normal_row("A", r, f"junkA{r}") for r in range(1, 10)]
    rows += [normal_row("A", 10, "junkA10")]
    rows += [guaranteed_row("A", 1, "UberOnly", "-> 11B")]
    rows += [normal_row("B", 11, "junkB11")]
    pool = parse_pool(make_csv(rows))

    result = solve(pool, {"UberOnly"}, max_rolls=12, start_track="A", start_roll=1)
    assert result.score == 1
    assert result.route[0]["type"] == "guaranteed_eleven"
    assert result.route[0]["track"] == "A" and result.route[0]["roll"] == 1
    assert "UberOnly" in result.route[0]["units"]


def test_guaranteed_eleven_collects_ten_interim_plus_the_uber():
    # roll..roll+9 is 10 positions (1..10 here); the guaranteed cell's own cat_name is an
    # 11th, additional pick -- it does not overwrite position 10's normal result.
    rows = [normal_row("A", r, f"c{r}") for r in range(1, 11)]
    rows += [guaranteed_row("A", 1, "Uber", "-> 11B")]
    pool = parse_pool(make_csv(rows))
    targets = {f"c{r}" for r in range(1, 11)} | {"Uber"}
    result = solve(pool, targets, max_rolls=11, start_track="A", start_roll=1)
    assert result.score == 11
    assert "c10" in result.collected


def test_route_follows_link_to_the_other_track():
    rows = [normal_row("A", r, f"junkA{r}") for r in range(1, 11)]  # 1..10: all 10 interim cells
    rows += [guaranteed_row("A", 1, "Uber", "-> 11B")]
    rows += [normal_row("B", 11, "Target")]
    pool = parse_pool(make_csv(rows))
    result = solve(pool, {"Target"}, max_rolls=12, start_track="A", start_roll=1)
    assert result.score == 1
    assert result.route[-1] == {"type": "single", "track": "B", "roll": 11, "units": ["Target"]}


def test_single_rolls_are_needed_when_the_eleven_switches_past_the_target():
    # Target sits at position 11 on track A. Taking the guaranteed-11 at position 1
    # switches to track B after position 10, making position 11 on A unreachable, so
    # only an all-single-rolls path can collect it.
    rows = [normal_row("A", r, f"junk{r}") for r in range(1, 11)]
    rows += [guaranteed_row("A", 1, "JunkUber", "-> 11B")]
    rows += [normal_row("A", 11, "Target")]
    pool = parse_pool(make_csv(rows))
    result = solve(pool, {"Target"}, max_rolls=12, start_track="A", start_roll=1)
    assert result.score == 1
    assert all(step["type"] == "single" for step in result.route)


# ---------- solve_from_csv ----------

def test_solve_from_csv_matches_solve():
    csv_text = make_csv([normal_row("A", 1, "Target")])
    a = solve_from_csv(csv_text, {"Target"}, max_rolls=2)
    b = solve(parse_pool(csv_text), {"Target"}, max_rolls=2)
    assert a == b


# ---------- brute-force cross-check ----------

def brute_force_best(pool, target_units, max_rolls, start_track="A", start_roll=1):
    """Unmemoized reference search over the same action space, for cross-checking."""
    best = 0

    def recurse(track, roll, collected):
        nonlocal best
        best = max(best, len(collected))
        if roll >= max_rolls:
            return
        if pool.has_single(track, roll):
            cell = pool.normal[track][roll]
            nxt = collected | {cell.cat_name} if cell.cat_name in target_units else collected
            recurse(track, roll + 1, nxt)
        if pool.has_guaranteed_eleven(track, roll):
            picks = [pool.normal[track][roll + i].cat_name for i in range(10)]
            picks.append(pool.guaranteed[track][roll].cat_name)
            nxt = collected | ({p for p in picks if p in target_units})
            dest_track, dest_roll = pool.guaranteed[track][roll].link_target
            recurse(dest_track, dest_roll, nxt)

    recurse(start_track, start_roll, frozenset())
    return best


def random_pool(rng, length=14, n_guaranteed=3):
    names = [f"u{i}" for i in range(6)]  # small alphabet so repeats/targets actually occur
    rows = []
    for track in ("A", "B"):
        for roll in range(1, length + 1):
            rows.append(normal_row(track, roll, rng.choice(names)))
    for _ in range(n_guaranteed):
        track = rng.choice(["A", "B"])
        roll = rng.randint(1, length - 10)
        other = "B" if track == "A" else "A"
        dest = rng.randint(roll + 9, length)
        rows.append(guaranteed_row(track, roll, rng.choice(names), f"-> {dest}{other}"))
    return parse_pool(make_csv(rows)), names


@pytest.mark.parametrize("seed", range(15))
def test_matches_brute_force_on_random_small_pools(seed):
    rng = random.Random(seed)
    pool, names = random_pool(rng)
    targets = set(rng.sample(names, k=3))
    max_rolls = rng.randint(3, 14)
    expected = brute_force_best(pool, targets, max_rolls)
    actual = solve(pool, targets, max_rolls).score
    assert actual == expected


# ---------- solve(): max_elevens ----------

def test_elevens_used_counts_guaranteed_eleven_steps():
    rows = [normal_row("A", r, f"c{r}") for r in range(1, 11)]
    rows += [guaranteed_row("A", 1, "Uber", "-> 11B")]
    rows += [normal_row("B", 11, "Target")]
    pool = parse_pool(make_csv(rows))
    result = solve(pool, {"Target"}, max_rolls=12, start_track="A", start_roll=1)
    assert result.elevens_used == 1
    assert sum(1 for s in result.route if s["type"] == "guaranteed_eleven") == 1


def test_single_roll_only_route_has_zero_elevens_used():
    pool = straight_pool("A", ["Target"])
    result = solve(pool, {"Target"}, max_rolls=2, start_track="A", start_roll=1)
    assert result.elevens_used == 0


def test_max_elevens_zero_blocks_a_target_only_reachable_via_a_guaranteed_eleven():
    # same pool as test_guaranteed_eleven_is_taken_when_it_reaches_an_otherwise_unreachable_target
    rows = [normal_row("A", r, f"junkA{r}") for r in range(1, 10)]
    rows += [normal_row("A", 10, "junkA10")]
    rows += [guaranteed_row("A", 1, "UberOnly", "-> 11B")]
    rows += [normal_row("B", 11, "junkB11")]
    pool = parse_pool(make_csv(rows))

    unlimited = solve(pool, {"UberOnly"}, max_rolls=12, start_track="A", start_roll=1)
    capped = solve(pool, {"UberOnly"}, max_rolls=12, start_track="A", start_roll=1, max_elevens=0)
    assert unlimited.score == 1
    assert capped.score == 0
    assert capped.elevens_used == 0


def test_max_elevens_caps_a_chain_of_two_guaranteed_elevens():
    # A's guaranteed-11 (only way onto track B) must be used before B's guaranteed-11
    # (the only way to draw Target) becomes reachable -- two elevens are required.
    rows = [normal_row("A", r, f"a{r}") for r in range(1, 11)]
    rows += [guaranteed_row("A", 1, "UberA", "-> 11B")]
    rows += [normal_row("B", r, f"b{r}") for r in range(11, 21)]
    rows += [guaranteed_row("B", 11, "Target", "-> 21A")]
    rows += [normal_row("A", 21, "a21")]
    pool = parse_pool(make_csv(rows))

    one = solve(pool, {"Target"}, max_rolls=22, start_track="A", start_roll=1, max_elevens=1)
    two = solve(pool, {"Target"}, max_rolls=22, start_track="A", start_roll=1, max_elevens=2)
    assert one.score == 0
    assert two.score == 1
    assert two.elevens_used == 2


@pytest.mark.parametrize("seed", range(15))
def test_max_elevens_matches_brute_force_on_random_small_pools(seed):
    rng = random.Random(seed)
    pool, names = random_pool(rng)
    targets = set(rng.sample(names, k=3))
    max_rolls = rng.randint(3, 14)
    max_elevens = rng.randint(0, 2)
    expected = brute_force_best_capped(pool, targets, max_rolls, max_elevens)
    actual = solve(pool, targets, max_rolls, max_elevens=max_elevens).score
    assert actual == expected


def brute_force_best_capped(pool, target_units, max_rolls, max_elevens, start_track="A", start_roll=1):
    """Unmemoized reference search that also respects a guaranteed-11 cap, for
    cross-checking solve()'s max_elevens against brute_force_best()'s uncapped search."""
    best = 0

    def recurse(track, roll, collected, elevens_used):
        nonlocal best
        best = max(best, len(collected))
        if roll >= max_rolls:
            return
        if pool.has_single(track, roll):
            cell = pool.normal[track][roll]
            nxt = collected | {cell.cat_name} if cell.cat_name in target_units else collected
            recurse(track, roll + 1, nxt, elevens_used)
        if elevens_used < max_elevens and pool.has_guaranteed_eleven(track, roll):
            picks = [pool.normal[track][roll + i].cat_name for i in range(10)]
            picks.append(pool.guaranteed[track][roll].cat_name)
            nxt = collected | ({p for p in picks if p in target_units})
            dest_track, dest_roll = pool.guaranteed[track][roll].link_target
            recurse(dest_track, dest_roll, nxt, elevens_used + 1)

    recurse(start_track, start_roll, frozenset(), 0)
    return best


# ---------- min_rolls_for_full_collection ----------

def test_min_rolls_for_full_collection_finds_the_minimum():
    pool = straight_pool("A", ["Junk", "Target1", "Junk", "Junk", "Target2", "Junk", "Junk"])
    targets = {"Target1", "Target2"}
    min_rolls, result = min_rolls_for_full_collection(pool, targets, start_track="A", start_roll=1)

    assert min_rolls is not None
    assert result.score == 2
    assert sorted(result.collected) == sorted(targets)
    # it's really the minimum: one fewer roll can't collect everything...
    assert solve(pool, targets, max_rolls=min_rolls - 1, start_track="A", start_roll=1).score < 2
    # ...but this many can, matching what min_rolls_for_full_collection reported.
    assert solve(pool, targets, max_rolls=min_rolls, start_track="A", start_roll=1).score == 2


def test_min_rolls_for_full_collection_none_when_unreachable():
    pool = straight_pool("A", ["Junk", "Junk2"])
    min_rolls, result = min_rolls_for_full_collection(pool, {"Nowhere"}, start_track="A", start_roll=1)
    assert (min_rolls, result) == (None, None)


def test_min_rolls_for_full_collection_zero_targets_needs_no_rolls():
    pool = straight_pool("A", ["Junk"])
    min_rolls, result = min_rolls_for_full_collection(pool, set(), start_track="A", start_roll=1)
    assert min_rolls == 1  # start_roll itself: no action is ever needed
    assert result.score == 0
    assert result.route == []


def test_min_rolls_for_full_collection_respects_max_elevens():
    # same "two chained elevens" pool as the max_elevens solve() test above
    rows = [normal_row("A", r, f"a{r}") for r in range(1, 11)]
    rows += [guaranteed_row("A", 1, "UberA", "-> 11B")]
    rows += [normal_row("B", r, f"b{r}") for r in range(11, 21)]
    rows += [guaranteed_row("B", 11, "Target", "-> 21A")]
    rows += [normal_row("A", 21, "a21")]
    pool = parse_pool(make_csv(rows))

    blocked = min_rolls_for_full_collection(pool, {"Target"}, max_elevens=1, max_rolls_cap=22)
    allowed = min_rolls_for_full_collection(pool, {"Target"}, max_elevens=2, max_rolls_cap=22)
    assert blocked == (None, None)
    assert allowed[0] is not None
    assert allowed[1].score == 1


# ---------- min_elevens_for_full_collection ----------

def test_min_elevens_for_full_collection_finds_the_minimum():
    rows = [normal_row("A", r, f"a{r}") for r in range(1, 11)]
    rows += [guaranteed_row("A", 1, "UberA", "-> 11B")]
    rows += [normal_row("B", r, f"b{r}") for r in range(11, 21)]
    rows += [guaranteed_row("B", 11, "Target", "-> 21A")]
    rows += [normal_row("A", 21, "a21")]
    pool = parse_pool(make_csv(rows))

    min_elevens, result = min_elevens_for_full_collection(pool, {"Target"}, max_rolls=22)
    assert min_elevens == 2
    assert result.score == 1
    assert result.elevens_used == 2


def test_min_elevens_for_full_collection_zero_when_single_rolls_suffice():
    pool = straight_pool("A", ["Junk", "Target"])
    min_elevens, result = min_elevens_for_full_collection(pool, {"Target"}, max_rolls=5)
    assert min_elevens == 0
    assert result.score == 1


def test_min_elevens_for_full_collection_none_when_unreachable_within_max_rolls():
    rows = [normal_row("A", r, f"a{r}") for r in range(1, 11)]
    rows += [guaranteed_row("A", 1, "UberA", "-> 11B")]
    rows += [normal_row("B", r, f"b{r}") for r in range(11, 21)]
    rows += [guaranteed_row("B", 11, "Target", "-> 21A")]
    pool = parse_pool(make_csv(rows))
    # max_rolls too small to ever reach B's guaranteed-11 at all, regardless of elevens allowed
    min_elevens, result = min_elevens_for_full_collection(pool, {"Target"}, max_rolls=5)
    assert (min_elevens, result) == (None, None)
