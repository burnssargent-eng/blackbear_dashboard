#!/usr/bin/env python3
"""
The cycle blend in the projection builder (analysis/build_projection_table.py,
CYCLE_BLEND / CYCLE_FACTOR), on a made-up customer, no files read. Run from the
repo root:

    python3 tests/cycle_blend_test.py

    w = CYCLE_BLEND x min(1, days / usual gap)
    projected = CYCLE_FACTOR x ((1 - w) x straight line + w x typical pickup)
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


F, B = bp.CYCLE_FACTOR, bp.CYCLE_BLEND
CID = 999999
START = date(2025, 1, 6)
# A steady 2 gal/day: 60 gal every 30 days.
plain = [{"date": START + timedelta(days=30 * k), "gallons": 60,
          "name": "Test Diner", "town": "Stowe"} for k in range(20)]
last = plain[-1]["date"]


def row(days, capacity=300, oil_left=()):
    return bp.build_row(CID, plain, last + timedelta(days=days), capacity, {}, {}, (), (), oil_left)


ok(bp.usual_gap(plain) == 30, "usual gap: the median of the last 6 gaps")
ok(bp.cycle_projection(0.0, 0, 30, 60) == 0.0, "a just-emptied barrel starts at zero")
r = row(10)
ok(abs(r["line_projected_gallons"] - 20) < 1e-9, "the straight line is still rate x days: 2 x 10 = 20")
w = B * 10 / 30
ok(abs(r["model_projected_gallons"] - F * ((1 - w) * 20 + w * 60)) < 1e-9,
   "10 days into a 30-day cycle: leans a sixth of the way toward the typical 60")
r = row(30)
ok(abs(r["model_projected_gallons"] - F * (0.5 * 60 + 0.5 * 60)) < 1e-9,
   "at the usual gap a steady customer reads its typical pickup, x the factor")
r45, r60 = row(45), row(60)
ok(abs((r60["model_projected_gallons"] - r45["model_projected_gallons"]) - F * (1 - B) * 2 * 15) < 1e-9,
   "past the usual gap it grows at half the rate (x the factor)")
# Gondola's Downtown Morrisville on 2026-10-27: 3.622 gal/day x 35 days, typical 76.7, gap 23.5.
ok(abs(bp.cycle_projection(3.622 * 35, 35, 23.5, 76.67) - 105.8) < 0.1, "Gondola's example: 127 -> 106")
ok(bp.cycle_projection(50.0, 10, None, 60) == 50.0, "no usual gap: the straight line unchanged")

# days_to_reach inverts the curve, before and after the gap, with and without a level.
for gallons, level in ((30, 0), (225, 0), (40, 10), (200, 25)):
    d = bp.days_to_reach(gallons, level, 2.0, 30, 60)
    ok(abs(bp.cycle_projection(level + 2.0 * d, d, 30, 60) - gallons) < 1e-6,
       f"days_to_reach({gallons}, level {level}) lands on {gallons} gal")
r = row(10)
d75 = bp.days_to_reach(225, 0, 2.0, 30, 60)
ok(abs(r["days_until_75pct"] - (d75 - 10)) < 1e-6, "days to 75% follow the curve, not the straight line")
ok(abs(r["implied_periodicity_days"] - d75) < 1e-6, "periodicity: an empty barrel's days to 75%")
ok(r["days_past_full"] < 0 and abs(-r["days_past_full"] - (bp.days_to_reach(300, 0, 2.0, 30, 60) - 10)) < 1e-6,
   "days to full follow the curve too")
r = row(10, oil_left=[(last + timedelta(days=4), 30)])
ok(abs(r["line_projected_gallons"] - 42) < 1e-9 and r["exposure_days"] == 6,
   "Oil Left: the line starts from the level, the blend's clock from the check")

print(f"\n{'FAILED' if failures else 'All passed'} ({failures} failure(s))")
sys.exit(1 if failures else 0)
