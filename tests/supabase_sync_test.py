#!/usr/bin/env python3
"""
supabase_sync.py against the real committed data files and a stand-in
database (no network, no key). Run from the repo root:

    python3 tests/supabase_sync_test.py
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

import supabase_sync as ss  # noqa: E402

failures = 0


def ok(cond, label):
    global failures
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


class FakeDb:
    """Tables as lists of dicts; records every write."""

    def __init__(self, tables=None):
        self.t = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.writes = []

    def select(self, table, query="select=*"):
        rows = self.t.get(table, [])
        if "picked_on=gte." in query:
            since = query.split("picked_on=gte.")[1].split("&")[0]
            rows = [r for r in rows if r["picked_on"] >= since]
        return [dict(r) for r in rows]

    def upsert(self, table, rows, on_conflict="id"):
        self.writes.append(("upsert", table, rows))
        keys = set(rows[0]) if rows else set()
        ok(all(set(r) == keys for r in rows), f"upsert {table}: every row has the same keys (PostgREST bulk rule)")
        cur = {r["id"]: r for r in self.t.setdefault(table, [])}
        for r in rows:
            cur[r["id"]] = {**cur.get(r["id"], {}), **r}
        self.t[table] = list(cur.values())

    def insert(self, table, rows):
        self.writes.append(("insert", table, rows))
        self.t.setdefault(table, []).extend(dict(r) for r in rows)

    def update(self, table, filters, values):
        self.writes.append(("update", table, filters, values))
        ids = {int(x) for x in filters.split("in.(")[1].rstrip(")").split(",")}
        for r in self.t.get(table, []):
            if r["id"] in ids:
                r.update(values)


snap = json.load(open(ss.ROUTES_FILE))
assign_ids = {a["customer_id"] for a in snap["assign_list"]}
stop_ids = {s["customer_id"] for r in snap["routes"] for s in r["stops"] if s["customer_id"] is not None}

# ── Seed into an empty database ──
db = FakeDb()
report = ss.sync(db, seed=True, today=date(2026, 10, 24))
cust = {r["id"]: r for r in db.t["customers"]}
ok(set(cust) == assign_ids | stop_ids, "every Assign-list and on-route customer is copied")
ok(all(cust[c]["active"] for c in assign_ids), "Assign-list customers are active")
ok(all(not cust[c]["active"] for c in stop_ids - assign_ids), "an on-route customer missing from Assign is kept, not active")
ok(all("notes" in r for r in cust.values()), "a seed writes notes for everyone")
ok(len(db.t["route_stops"]) == len(ss.stop_rows(snap)) > 0, "the seed copies the source site's stops")
first = snap["routes"][0]
seeded = sorted((s for s in db.t["route_stops"] if s["route_id"] == first["route_id"]), key=lambda s: s["position"])
ok([s["customer_id"] for s in seeded] == [s["customer_id"] for s in first["stops"] if s["customer_id"]],
   "stops keep the source site's order")
ok(report["routes"] == [], "right after a seed the routes match")
ok({r["id"] for r in db.t["routes"]} >= {r["route_id"] for r in snap["routes"]}, "every route id is copied")
dup = [r for r in db.t["routes"] if r["id"] == 15]
ok(len(dup) == 1, "route 15, listed twice by the source site, is one route")
ok(db.t["sync_reports"][-1]["kind"] == "seed", "the seed leaves a report")

# ── A second seed never writes over the site's routes ──
db.t["route_stops"] = [s for s in db.t["route_stops"] if s is not seeded[0]]
n_before = len(db.t["route_stops"])
ss.sync(db, seed=True, today=date(2026, 10, 24))
ok(len(db.t["route_stops"]) == n_before, "a seed into a non-empty table adds nothing")

# ── Nightly: notes only for new customers; differences reported ──
db.writes = []
for r in db.t["customers"]:
    if r["id"] == seeded[1]["customer_id"]:
        r["notes"] = "edited on the site"
db.t["customers"].append({"id": 99999, "name": "Gone Diner", "active": True})
db.t["customers"].append({"id": -5, "name": "Site-made", "active": True})
report = ss.sync(db, today=date(2026, 10, 24))
cust = {r["id"]: r for r in db.t["customers"]}
ok(cust[seeded[1]["customer_id"]]["notes"] == "edited on the site", "the nightly never overwrites existing notes")
ok(cust[99999]["active"] is False, "a customer gone from the source site is marked inactive")
ok(cust[-5]["active"] is True, "a customer made on the site is left alone")
ok(not any(w[0] == "insert" and w[1] == "route_stops" for w in db.writes), "the nightly never writes stops")
r0 = next(r for r in report["routes"] if r["route_id"] == first["route_id"])
ok([c["id"] for c in r0["only_jamaal"]] == [seeded[0]["customer_id"]], "a stop removed on the site is reported")

# ── Pickup comparison ──
jam = {(1, "2026-10-20"): 80, (2, "2026-10-20"): 40, (3, "2026-10-19"): 60, (4, "2026-10-21"): 30}
site = [{"customer_id": 1, "picked_on": "2026-10-20", "gallons": 80},     # matches
        {"customer_id": 2, "picked_on": "2026-10-21", "gallons": 40},     # Jamaal stamped a day earlier
        {"customer_id": 5, "picked_on": "2026-10-20", "gallons": 25},     # only on the site
        {"customer_id": 4, "picked_on": "2026-10-21", "gallons": 35},     # gallons differ
        {"customer_id": 6, "picked_on": "2026-10-24", "gallons": 50}]     # today: not compared yet
p = ss.pickup_differences(site, jam, "2026-10-23", {})
ok([x["id"] for x in p["only_site"]] == [5], "a pickup only on the site is listed; a day either side matches")
ok([x["id"] for x in p["gallons_differ"]] == [4] and p["gallons_differ"][0]["jamaal_gallons"] == 30,
   "different gallons on the same pickup are listed")
ok([x["id"] for x in p["only_jamaal"]] == [], "a source-site pickup on a day the site was not used is not listed")
p = ss.pickup_differences(site, {**jam, (7, "2026-10-20"): 90}, "2026-10-23", {})
ok([x["id"] for x in p["only_jamaal"]] == [7], "a source-site pickup on a day the site was used is listed")

print(f"\n{'FAILED' if failures else 'All passed'} ({failures} failure(s))")
sys.exit(1 if failures else 0)
