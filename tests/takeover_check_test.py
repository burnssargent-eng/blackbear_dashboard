#!/usr/bin/env python3
"""
The takeover dry run's report (takeover_check.py), on made-up builds, no
network. Run from the repo root:

    python3 tests/takeover_check_test.py

Each kind of difference lands in its own list; the source site's own 0s are
never a difference; today is "not yet in Jamaal", never a difference; a day
with nothing on the site is called out; and matching builds read MATCH.
"""

import csv
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

import takeover_check as tc  # noqa: E402

fails = 0


def ok(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


SINCE, THROUGH = "2026-11-01", "2026-11-05"

print("compare")
jamaal = {(1, "2026-11-02"): [40], (2, "2026-11-02"): [60], (3, "2026-11-03"): [80],
          (4, "2026-11-03"): [25], (5, "2026-11-04"): [30]}
site = {(1, "2026-11-02"): [40], (2, "2026-11-02"): [65], (4, "2026-11-04"): [25],
        (6, "2026-11-04"): [10], (7, "2026-11-06"): [55]}
out = tc.compare(jamaal, site, THROUGH)
ok(out["differ"] == [((2, "2026-11-02"), [60], [65])], "different gallons")
ok(out["only_jamaal"] == [((3, "2026-11-03"), [80]), ((5, "2026-11-04"), [30])], "missing on the site")
ok(out["only_site"] == [((6, "2026-11-04"), [10])], "only on the site")
ok(out["day_off"] == [((4, "2026-11-03"), "2026-11-04", [25])], "same gallons a day off is one entry, not two")
ok(out["not_yet"] == [((7, "2026-11-06"), [55])], "after --through: not yet in Jamaal, not a difference")
ok(tc.compare(jamaal, dict(jamaal), THROUGH) == {k: [] for k in out}, "identical: nothing listed")

print("entries")
raw = [{"customer_id": "1", "date": "2026-11-02", "gallons": "40"}, {"customer_id": "1", "date": "2026-10-20", "gallons": "9"}]
codes = [{"customer_id": "1", "date": "2026-11-03", "qty": "0"}, {"customer_id": "1", "date": "2026-11-03", "qty": "1"}]
p, c = tc.entries(raw, codes, SINCE)
ok(p == {(1, "2026-11-02"): [40]} and c == {(1, "2026-11-03"): [1]}, "from --since on; the source site's own 0s left out")

print("per day")
rows = tc.per_day({(1, "2026-11-02"): [40], (2, "2026-11-03"): [5]}, {(1, "2026-11-02"): [40]}, SINCE, THROUGH)
ok(rows == [("2026-11-02", 1, 1), ("2026-11-03", 1, 0)], "a day with nothing on the site still shows")


def build(d, raw_rows, code_rows, proj):
    os.makedirs(d)
    with open(os.path.join(d, "oil_collections_raw.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["customer_id", "name", "date", "gallons"])
        w.writeheader()
        w.writerows(raw_rows)
    with open(os.path.join(d, "oil_non_pickups.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["customer_id", "date", "qty"])
        w.writeheader()
        w.writerows(code_rows)
    with open(os.path.join(d, "oil_projections.json"), "w") as fh:
        json.dump({"customers": proj}, fh)


print("report")
with tempfile.TemporaryDirectory() as tmp:
    r = lambda cid, day, g: {"customer_id": str(cid), "name": "x", "date": day, "gallons": str(g)}
    proj = [{"id": 1, "name": "Diner", "projected_gal": 30.0}, {"id": 2, "name": "Pub", "projected_gal": 50.0}]
    j_raw = [r(1, "2026-11-02", 40), r(2, "2026-11-03", 60)]
    j_codes = [{"customer_id": "9", "date": "2026-11-03", "qty": "0"}]
    build(os.path.join(tmp, "j"), j_raw, j_codes, proj)
    build(os.path.join(tmp, "s"), j_raw + [r(1, "2026-11-06", 20)], [],
          [proj[0] | {"projected_gal": 0.0}, proj[1]])
    text, n = tc.report(os.path.join(tmp, "j"), os.path.join(tmp, "s"), SINCE, THROUGH)
    ok(n == 0 and text.startswith("# Takeover dry run: MATCH"), "same pickups through --through: MATCH")
    ok("1 projection difference(s)" in text and "Diner (1): Jamaal 30.0, site 0.0" in text,
       "a projection moved by today's entry is listed, with why")
    ok("| Site entries after 2026-11-05 (not yet in Jamaal) | 1 | 0 |" in text, "today's site entry counted apart")

    build(os.path.join(tmp, "s2"), [r(1, "2026-11-02", 40)], [], proj)
    text, n = tc.report(os.path.join(tmp, "j"), os.path.join(tmp, "s2"), SINCE, THROUGH)
    ok(n == 1 and "# Takeover dry run: 1 difference(s)" in text and "- 2026-11-03: Pub (2), 60 gal" in text,
       "a missing pickup is a difference, named")
    ok("| 2026-11-03 | 1 | 0 | **none on the site** |" in text, "the day the site has nothing is called out")

print("The workflow")
wf = open(".github/workflows/takeover-dry-run.yml").read()
ok("Nightly Oil Data Update" in wf and "workflow_dispatch" in wf, "runs after each nightly, and by hand")
ok("--export-collections --since" in wf and "contents: read" in wf and "git push" not in wf,
   "builds from the site in a copy and never commits")

print(f"\n{'All passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
