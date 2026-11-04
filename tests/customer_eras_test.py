#!/usr/bin/env python3
"""
customer_eras.py on made-up accounts, no network. Run from the repo root:

    python3 tests/customer_eras_test.py

Eras split an account's pickups at their end dates; moved/continued chain eras
into one business; changed_hands makes the successor a gain and the old
business a handover, never a loss; an inactive account with no reason is
unknown; bad rows are listed, never fatal.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import customer_eras as ce  # noqa: E402

fails = 0


def ok(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


def registry(text):
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write("customer_id,era,business,end,end_reason,next,note,source\n" + text)
    try:
        return ce.load_registry(fh.name)
    finally:
        os.unlink(fh.name)


def cust(cid, active=True, ended=False, name=None):
    return {"customer_id": cid, "name": name or f"Acct {cid}", "town": "Town",
            "is_active": active, "lost_reason": "Source-site inactive" if ended else None}


customers = [cust(1), cust(2, active=False, ended=True), cust(3, active=False, ended=True), cust(4), cust(5),
             cust(6, active=False, ended=True)]
records = [
    (1, "2016-03-01", 50), (1, "2018-06-01", 40), (1, "2020-02-01", 60), (1, "2021-05-01", 70),
    (2, "2016-05-01", 30), (2, "2019-01-01", 30),           # moved to #3 in 2019
    (3, "2019-06-01", 25), (3, "2022-02-01", 25),           # ...then stopped: lost
    (4, "2017-01-01", 10), (4, "2023-01-01", 10),           # untouched, current
    (5, "2020-03-01", 80),                                  # took over #6's site
    (6, "2016-01-01", 15), (6, "2019-12-01", 15),           # inactive, no row
]
reg, problems = registry(
    "1,1,Old Diner,2019-12-31,changed_hands,,,\n"
    "1,2,New Bistro,,,,,\n"
    "2,1,Mover,,moved,3/1,,\n"
    "3,1,Mover (new spot),,lost,,,\n"
)
ok(not problems, "a clean registry has no problems")
eras = ce.build_eras(customers, records, reg, problems)
e = {x["key"]: x for x in eras}
ok(e["1/1"]["pickups"] == 2 and e["1/2"]["pickups"] == 2, "the end date splits the account's pickups")
ok(e["1/2"]["window_start"] == "2020-01-01", "the next era starts the day after the end")
ok(e["1/1"]["site_next"] == "1/2", "changed_hands with no next hands the site to the account's next era")
ok(e["6/1"]["end_reason"] == "unknown", "an inactive account with no row ends as unknown, not lost")
ok(e["4/1"]["ended"] is False, "an active account with no row is current")

biz = {b["id"]: b for b in ce.build_businesses(eras)}
ok(biz["2/1"]["eras"] == ["2/1", "3/1"] and biz["2/1"]["end_reason"] == "lost",
   "a move chains two accounts into one business, ending with the last era's reason")
ok(biz["2/1"]["gallons"] == 110 and biz["2/1"]["gained_year"] == 2016, "the chain sums both accounts")
ok(biz["1/2"]["gain_type"] == "successor" and biz["1/1"]["end_reason"] == "changed_hands",
   "a successor is a gain and its predecessor a handover")
ok(biz["1/1"]["followed_by"] == ["1/2"] and biz["1/2"]["preceded_by"] == ["1/1"], "both ends link")
ok(sum(b["gallons"] for b in biz.values()) == sum(r[2] for r in records), "every gallon in one business")

rows = ce.retention_by_year(list(biz.values()), eras, 2016, 2023)
by = {r["year"]: r for r in rows}
ok(all(b["active_jan1"] == a["active_jan1"] + a["gained"] - a["ended"] for a, b in zip(rows, rows[1:])),
   "active on 1 January rolls forward: last year's + gained - ended")
ok(by[2022]["lost"] == 1 and by[2018]["changed_hands"] == 1 and by[2019]["unknown"] == 1,
   "endings land in the year of the last pickup, by reason")
ok(by[2019]["moved_followed"] == 1 and by[2019]["moved"] == 0, "a followed move is not an ending")
ok(by[2022]["churn"] == round(1 / by[2022]["active_jan1"], 4), "churn = lost / active on 1 January")
summary = ce.retention_summary(rows, list(biz.values()))
ok(summary["site_retention"] == 1.0 and summary["lost_total"] == 1, "site retention counts handovers over closures")

# A successor on ANOTHER account, a non_recurring event, and a continued duplicate.
reg2, p2 = registry("6,1,Old Spot,,changed_hands,5/1,,\n4,1,Fair,,non_recurring,,,\n")
eras2 = ce.build_eras(customers, records, reg2, p2)
b2 = {b["id"]: b for b in ce.build_businesses(eras2)}
ok(b2["5/1"]["gain_type"] == "successor" and b2["6/1"]["followed_by"] == ["5/1"],
   "changed_hands with next makes the other account's business the successor")
rows2 = ce.retention_by_year(list(b2.values()), eras2, 2016, 2023)
ok(sum(r["gained"] for r in rows2) == len(b2) - 1, "non_recurring is left out of the counts")

# Bad rows: listed, skipped, never raised.
_, bad = registry("7,1,A,,,,,\n7,3,B,,,,,\n8,1,C,,,,,\n8,2,D,,,,,\n9,1,E,2020-01-01,,,,\n10,1,F,,gone,,,\n")
ok(len(bad) == 4, f"four bad rows are reported, not raised ({len(bad)})")
_, after = None, []
reg3, p3 = registry("4,1,Short,2020-01-01,closed,,,\n")
ce.build_eras(customers, records, reg3, p3)
ok(any("after its last era's end" in p for p in p3), "pickups after an account's last era's end are a problem")

print(f"\n{'FAILED' if fails else 'All passed'} ({fails} failure(s))")
sys.exit(1 if fails else 0)
