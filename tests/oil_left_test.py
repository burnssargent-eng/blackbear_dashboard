#!/usr/bin/env python3
"""
Oil Left in the projection builder (analysis/build_projection_table.py), on a
made-up customer, no files read. Run from the repo root:

    python3 tests/oil_left_test.py

The two rules: a gap's oil is collected + left after it - left after the
previous pickup, and a check on or after the last pickup starts the barrel at
what was left. Both rules act on the straight line (line_projected_gallons);
the cycle blend on top of it is tested in tests/cycle_blend_test.py.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

import build_projection_table as bp  # noqa: E402

failures = 0


def ok(cond, label):
    global failures
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


CID = 999999
START = date(2025, 1, 6)


def history(n=20, gallons=60, every=30):
    """n pickups `every` days apart: a steady 2 gal/day by default."""
    return [{"date": START + timedelta(days=every * k), "gallons": gallons,
             "name": "Test Diner", "town": "Stowe"} for k in range(n)]


def row(pickups, asof, oil_left=(), empty_checks=(), capacity=300):
    return bp.build_row(CID, pickups, asof, capacity, {}, {}, empty_checks, (), oil_left)


plain = history()
last = plain[-1]["date"]
ok(all(bp.gap_oil(plain, i) == 60 for i in range(1, len(plain))), "without Oil Left a gap's oil is the gallons collected")
base = row(plain, last + timedelta(days=10))
ok(abs(base["oil_rate_gpd_projected"] - 2.0) < 1e-9, "the steady customer projects 2 gal/day")
ok(abs(base["line_projected_gallons"] - 20.0) < 1e-9, "10 days after the last pickup: 20 gal")
ok(base["oil_left_date"] is None and base["oil_left_gallons"] is None, "no Oil Left: the new columns are empty")

# Rule 2: a part pickup (30 collected, 30 left) does not read as low production.
part = history()
part[-1]["gallons"] = 30
short = row([dict(p) for p in part], last + timedelta(days=10))
ok(short["oil_rate_gpd_projected"] < 2.0, "without Oil Left the part pickup drags the rate down")
anchors = bp.apply_oil_left({CID: part}, {CID: {last: 30}})
ok(part[-1].get("left") == 30, "a check on a pickup's day is what that pickup left")
ok(bp.gap_oil(part, len(part) - 1) == 60, "collected 30 + left 30 = 60 gal of oil over the gap")
fixed = row(part, last + timedelta(days=10), anchors[CID])
ok(abs(fixed["oil_rate_gpd_projected"] - 2.0) < 1e-9, "with Oil Left the rate is 2 gal/day again")
# Rule 1 on the same day: the barrel starts at 30, not 0.
ok(abs(fixed["line_projected_gallons"] - 50.0) < 1e-9, "10 days on: 30 left + 2 x 10 = 50 gal")
ok(fixed["oil_left_date"] == last.isoformat() and fixed["oil_left_gallons"] == 30, "the check used is exported")
ok("30 gal left on" in fixed["routing_reason"], "the reason says where the projection started")

# The next full pickup collects the 30 left plus 60 new; its gap is still 60.
after = part + [{"date": last + timedelta(days=30), "gallons": 90, "name": "Test Diner", "town": "Stowe"}]
ok(bp.gap_oil(after, len(after) - 1) == 60, "the pickup after collects 90, less the 30 left before: 60")
r = row(after, last + timedelta(days=40), anchors[CID])
ok(abs(r["oil_rate_gpd_projected"] - 2.0) < 1e-9, "both gaps around the part pickup read 2 gal/day")
ok(r["oil_left_date"] is None and abs(r["line_projected_gallons"] - 20.0) < 1e-9,
   "a check before the last pickup is not a starting level")

# Rule 1 on a day with no pickup: a full truck checks a stop it skips.
r = row(plain, last + timedelta(days=12), [(last + timedelta(days=8), 50)])
ok(abs(r["line_projected_gallons"] - 58.0) < 1e-9 and r["days_accumulating"] == 4,
   "50 left on day 8, projected on day 12: 50 + 2 x 4 = 58")
ok(abs(r["oil_rate_gpd_projected"] - 2.0) < 1e-9, "a check between pickups leaves the rate alone")
r = row(plain, last + timedelta(days=12), [(last + timedelta(days=8), 0)])
ok(abs(r["line_projected_gallons"] - 8.0) < 1e-9, "0 left is a level too: like an empty check")
r = row(plain, last + timedelta(days=12), [(last + timedelta(days=5), 50)],
        empty_checks=[last + timedelta(days=8)])
ok(abs(r["line_projected_gallons"] - 8.0) < 1e-9 and r["oil_left_date"] is None,
   "a later empty check wins over an earlier Oil Left")
r = row(plain, last + timedelta(days=12), [(last + timedelta(days=8), 50)],
        empty_checks=[last + timedelta(days=8)])
ok(abs(r["line_projected_gallons"] - 58.0) < 1e-9, "Oil Left on the same day as an empty check wins (it has a level)")
r = row(plain, last + timedelta(days=12), [(last + timedelta(days=8), 900)])
ok(abs(r["line_projected_gallons"] - 308.0) < 1e-9 and r["current_projected_gallons"] == 300,
   "a level above capacity starts at capacity; the shown figure is capped as always")
ok(r["days_past_full"] > 0, "a barrel left full is past full")

# Shared barrels and new owners.
group = bp.SHARED_CONTAINERS[0]
lo, hi = sorted(group.members)[:2]
shared = {lo: [{"date": last, "gallons": 30, "name": group.label, "town": ""}]}
got = bp.apply_oil_left(shared, {hi: {last: 25}, lo: {last: 10}})
ok(got == {lo: [(last, 25)]} and shared[lo][0]["left"] == 25,
   "a shared member's check lands on its group, the larger when both record the same day")
hs_id, hs = next(iter(bp.HISTORY_STARTS.items()))
got = bp.apply_oil_left({}, {hs_id: {hs.start - timedelta(days=1): 40, hs.start: 20}})
ok(got == {hs_id: [(hs.start, 20)]}, "checks before a new owner's start are dropped")

print(f"\n{'FAILED' if failures else 'All passed'} ({failures} failure(s))")
sys.exit(1 if failures else 0)
