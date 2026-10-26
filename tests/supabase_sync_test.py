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

    def _filter(self, rows, query):
        for part in query.split("&"):
            col, _, cond = part.partition("=")
            if cond.startswith("eq."):
                rows = [r for r in rows if str(r.get(col, "site" if col == "source" else None)) == cond[3:]]
            elif cond.startswith("gte."):
                rows = [r for r in rows if r.get(col) is not None and r[col] >= cond[4:]]
        return rows

    def select(self, table, query="select=*"):
        return [dict(r) for r in self._filter(self.t.get(table, []), query)]

    def exists(self, table, filters):
        return bool(self._filter(self.t.get(table, []), filters))

    def delete(self, table, column, values):
        self.writes.append(("delete", table, values))
        self.t[table] = [r for r in self.t.get(table, []) if r.get(column) not in set(values)]

    def upsert(self, table, rows, on_conflict="id"):
        self.writes.append(("upsert", table, rows))
        keys = set(rows[0]) if rows else set()
        ok(all(set(r) == keys for r in rows), f"upsert {table}: every row has the same keys (PostgREST bulk rule)")
        cols = on_conflict.split(",")
        key = lambda r: tuple(r.get(c) for c in cols) if all(c in r for c in cols) else id(r)
        cur = {key(r): r for r in self.t.setdefault(table, [])}
        for r in rows:
            cur[key(r)] = self._numbered(table, {**cur.get(key(r), {}), **r})
        self.t[table] = list(cur.values())

    def _numbered(self, table, r):
        """route_stops rows get an id, as the real table's identity column gives them."""
        if table == "route_stops" and "id" not in r:
            self.next_id = getattr(self, "next_id", 0) + 1
            r = {**r, "id": self.next_id}
        return r

    def insert(self, table, rows):
        self.writes.append(("insert", table, rows))
        self.t.setdefault(table, []).extend(self._numbered(table, dict(r)) for r in rows)

    def update(self, table, filters, values):
        self.writes.append(("update", table, filters, values))
        ids = {int(x) for x in filters.split("in.(")[1].rstrip(")").split(",")}
        for r in self.t.get(table, []):
            if r["id"] in ids:
                r.update(values)


import tempfile  # noqa: E402
# customer_details.json is written from the database and never committed, so
# the test brings its own (empty: addresses are not what is tested here).
_tmp = tempfile.mkdtemp()
ss.DETAILS_FILE = os.path.join(_tmp, "customer_details.json")
json.dump({"customers": {}}, open(ss.DETAILS_FILE, "w"))

snap = json.load(open(ss.ROUTES_FILE))
assign_ids = {a["customer_id"] for a in snap["assign_list"]}
stop_ids = {s["customer_id"] for r in snap["routes"] for s in r["stops"] if s["customer_id"] is not None}

# ── Seed into an empty database ──
db = FakeDb()
report = ss.sync(db, seed=True, today=date(2026, 10, 24))
cust = {r["id"]: r for r in db.t["customers"]}
ok(set(cust) >= assign_ids | stop_ids, "every Assign-list and on-route customer is copied")
ok(all(not cust[c]["active"] for c in set(cust) - assign_ids - stop_ids),
   "the others (customers only in the source site's history) are not active")
ok(all(cust[c]["active"] for c in assign_ids), "Assign-list customers are active")
ok(all(not cust[c]["active"] for c in stop_ids - assign_ids), "an on-route customer missing from Assign is kept, not active")
ok(all("notes" in cust[c] for c in assign_ids | stop_ids), "a seed writes notes for every current customer")
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

# ── The source site's history (2026-10-24) ──
import csv as _csv  # noqa: E402
pickups = ss.jamaal_collection_rows()
raw_n = sum(1 for _ in _csv.DictReader(open(ss.RAW_FILE)))
codes_n = sum(1 for _ in _csv.DictReader(open(ss.CODES_FILE)))
ok(len(pickups) == raw_n + codes_n, f"every source-site pickup and code becomes one row ({raw_n} + {codes_n})")
ok(len({r["jamaal_key"] for r in pickups.values()}) == len(pickups), "keys are unique, two pickups on one day included")
ok(pickups == ss.jamaal_collection_rows(), "keys are the same from run to run")
out = ss.jamaal_outgoing_rows()
days = json.load(open(ss.OUTGOING_FILE))["days"]
ok(sum(1 for r in out.values() if r["kind"] == "stillage") == sum(sum(d.get("stillage", {}).values()) for d in days.values()),
   "one outgoing row per stillage run")
ok(all(r["truck"] in ("mammabear", "mackdaddy", "babybird", "newbiedoo") for r in out.values() if r["kind"] == "stillage"),
   "every run's truck is one the database accepts")
cust = set(ss.customer_rows(snap, {}, {}))
missing = {r["customer_id"] for r in pickups.values()} - cust
ok(missing and all(r["active"] is False for r in ss.history_customers(missing, {}, cust)),
   "customers only in the history are added, not active")
ok(ss.history_customers({123456}, {}, set())[0]["name"] == "Customer 123456 (name not on file)",
   "a customer with no name on file gets a placeholder name")

hist = FakeDb({k: [dict(r) for r in v] for k, v in db.t.items()})
hist.t["collections"] = [{"id": 1, "source": "site", "customer_id": seeded[0]["customer_id"], "picked_on": "2026-10-23",
                          "gallons": 50, "client_id": "c1"}]
ss.sync(hist, today=date(2026, 10, 24))
jam = [r for r in hist.t["collections"] if r.get("source") == "jamaal"]
ok(len(jam) == len(pickups), "the first run copies the whole history")
ok(any(r.get("source", "site") == "site" for r in hist.t["collections"]), "the site's own pickup is untouched")
ok({r["customer_id"] for r in jam} <= {r["id"] for r in hist.t["customers"]}, "every copied pickup's customer exists")
old = next(r for r in jam if r["picked_on"] < "2026-01-01")
recent = max(jam, key=lambda r: r["picked_on"])
old["gallons"] = 9999          # stands in for an edit older than the nightly window
hist.writes = []
saved_raw = ss.RAW_FILE
tmp2 = tempfile.mkdtemp()
rows_raw = list(_csv.DictReader(open(ss.RAW_FILE)))
drop = next(r for r in rows_raw if r["date"] == recent["picked_on"] and int(r["customer_id"]) == recent["customer_id"])
ss.RAW_FILE = os.path.join(tmp2, "raw.csv")
with open(ss.RAW_FILE, "w", newline="") as fh:
    w = _csv.DictWriter(fh, fieldnames=rows_raw[0].keys())
    w.writeheader()
    w.writerows(r for r in rows_raw if r is not drop)
rep = ss.sync(hist, today=date(2026, 10, 24))
ss.RAW_FILE = saved_raw
ok(rep["history"]["collections"]["since"] != "all", "after the first run the nightly refreshes only recent days")
day_keys = lambda: sorted(r["jamaal_key"] for r in hist.t["collections"]
                         if r.get("jamaal_key", "").startswith(f"{recent['customer_id']}:{recent['picked_on']}:"))
ok(rep["history"]["collections"]["removed"] == 1 and len(day_keys()) == sum(
   1 for r in rows_raw if r["date"] == recent["picked_on"] and int(r["customer_id"]) == recent["customer_id"]) - 1,
   "a recent pickup gone from the source site is removed")
ok(next(r for r in hist.t["collections"] if r.get("jamaal_key") == old["jamaal_key"])["gallons"] == 9999,
   "older history is left as it is by the nightly")
q = [w for w in hist.writes if w[0] == "upsert" and w[1] == "collections"]
ok(q and all(r["picked_on"] >= "2026-07-26" for r in q[0][2]), "the nightly sends only the last 90 days")

# ── Details only in the database (step 6) ──
from jamaal_customers import scrub  # noqa: E402
rows = [{"id": 5, "address": "1 Main St., Stowe, VT", "lat": 44.4, "lon": -72.7, "loc": "census",
         "notes": "Barrel out back, code 4321"},
        {"id": 6, "address": None, "lat": None, "lon": None, "loc": None, "notes": None},
        {"id": -3, "address": "made on the site", "notes": "x"}]
customers, local = ss.details_from_rows(rows, scrub)
ok(set(customers) == {"5", "6"}, "the export leaves out customers made on the site")
ok("4321" not in customers["5"]["notes"] and local == {"5": "Barrel out back, code 4321"},
   "the export cuts codes from the details file and keeps the full notes in the local file")
ok(customers["6"]["address"] == "" and customers["6"]["notes"] == "", "a customer with nothing stored exports as blanks")
tmp = tempfile.mkdtemp()
out, notes_out = os.path.join(tmp, "d.json"), os.path.join(tmp, "n.json")
ss.export_details(FakeDb({"customers": rows}), out, notes_out)
ok(json.load(open(out))["customers"]["5"]["lat"] == 44.4 and json.load(open(notes_out)) == local, "the export writes both files")
try:
    ss.export_details(FakeDb({"customers": []}), out, notes_out)
    ok(False, "an empty database exports nothing")
except ss.SyncError:
    ok(True, "an empty database exports nothing (the nightly then skips the details read and the sync)")
saved = ss.DETAILS_FILE
ss.DETAILS_FILE = os.path.join(tmp, "missing.json")
try:
    ss.sync(FakeDb(), today=date(2026, 10, 24))
    ok(False, "without the details file the sync refuses")
except ss.SyncError:
    ok(True, "without the details file the sync refuses, so no address is blanked")
ss.DETAILS_FILE = saved

# ── Morning routes: the site's routes set to the source site's (--routes) ──
from datetime import datetime, timedelta  # noqa: E402
read_at = datetime.fromisoformat(snap["scraped_at"])
mdb = FakeDb()
ss.sync(mdb, seed=True, today=date(2026, 10, 24))
by_route = lambda d, rid: [s["customer_id"] for s in sorted((s for s in d.t["route_stops"] if s["route_id"] == rid),
                                                            key=lambda s: s["position"])]
want = {r["route_id"]: [s["customer_id"] for s in r["stops"] if s["customer_id"] is not None] for r in snap["routes"]}
r1, r2 = snap["routes"][0]["route_id"], snap["routes"][1]["route_id"]
# The site's day: a stop removed, one reordered, one moved to another route,
# Must Get changed, a stranger added, and a route made on the site.
s1 = sorted((s for s in mdb.t["route_stops"] if s["route_id"] == r1), key=lambda s: s["position"])
mdb.t["route_stops"].remove(s1[0])
s1[1]["position"], s1[2]["position"] = s1[2]["position"], s1[1]["position"]
s1[3]["route_id"] = r2
s1[4]["must_get"] = not s1[4]["must_get"]
stranger = next(c for c in assign_ids if c not in stop_ids)
mdb.insert("route_stops", [{"route_id": r1, "customer_id": stranger, "position": 99, "must_get": False}])
mdb.t["routes"].append({"id": -3, "name": "Site route"})
mdb.insert("route_stops", [{"route_id": -3, "customer_id": stranger, "position": 1, "must_get": False}])
idle = next(r["id"] for r in ss.route_rows(snap) if r["id"] not in want)
mdb.insert("route_stops", [{"route_id": idle, "customer_id": stranger, "position": 1, "must_get": False}])
mdb.writes = []
rep_r = ss.mirror_routes(mdb, now=read_at + timedelta(minutes=5))
ok(all(by_route(mdb, rid) == ids for rid, ids in want.items()), "every source-site route is set to its stops, in its order")
mine = {(s["route_id"], s["customer_id"]): s for s in mdb.t["route_stops"]}
ok(all(mine[(r["route_id"], s["customer_id"])]["must_get"] == bool(s["must_get"])
       for r in snap["routes"] for s in r["stops"] if s["customer_id"] is not None), "Must Get is set to the source site's")
ok(by_route(mdb, -3) == [stranger], "a route made on the site is left alone")
ok(by_route(mdb, idle) == [], "a route with no stops on the source site is emptied")
ok({r["route_id"] for r in rep_r["differed_before"]} >= {r1, r2}, "what the site had that differed is kept in the report")
ok(mdb.t["sync_reports"][-1]["kind"] == "routes", "the morning run leaves a 'routes' report")
ok([w[0] for w in mdb.writes if w[1] == "route_stops"] == ["upsert", "delete"],
   "the wanted stops are written before the rest are removed")
again = ss.mirror_routes(mdb, now=read_at + timedelta(minutes=5))
ok(again["differed_before"] == [] and again["removed"] == 0, "a second run changes nothing")
# A stop whose customer is new to the source site waits for the nightly.
newcomer = want[r1][0]
mdb.t["customers"] = [c for c in mdb.t["customers"] if c["id"] != newcomer]
w = ss.mirror_routes(mdb, now=read_at + timedelta(minutes=5))
ok([x["id"] for x in w["waiting"]] == [newcomer] and newcomer not in by_route(mdb, r1),
   "a stop whose customer is not in the database yet is skipped and listed")
for label, when, bad in [("an old read", read_at + timedelta(hours=2), None),
                         ("a read with no stops", read_at, {**snap, "routes": [{**r, "stops": []} for r in snap["routes"]]})]:
    before = [dict(s) for s in mdb.t["route_stops"]]
    saved_file = ss.ROUTES_FILE
    if bad:
        ss.ROUTES_FILE = os.path.join(_tmp, "bad_routes.json")
        json.dump(bad, open(ss.ROUTES_FILE, "w"))
    try:
        ss.mirror_routes(mdb, now=when)
        ok(False, f"{label} is refused")
    except ss.SyncError:
        ok(mdb.t["route_stops"] == before, f"{label} is refused and the routes are left as they are")
    ss.ROUTES_FILE = saved_file

# ── Oil Left export (for the projections) ──
left_db = FakeDb({"oil_left": [
    {"customer_id": 325, "checked_on": "2026-10-23", "gallons_left": 40},
    {"customer_id": 12, "checked_on": "2026-10-24", "gallons_left": 0},
    {"customer_id": 12, "checked_on": "2026-10-20", "gallons_left": 15}]})
left_out = os.path.join(tmp, "oil_left.csv")
ss.export_oil_left(left_db, left_out)
ok(open(left_out).read() == "customer_id,date,gallons_left\n12,2026-10-20,15\n12,2026-10-24,0\n325,2026-10-23,40\n",
   "the Oil Left export is id, day and gallons only, by customer then day (0 left kept)")
ss.export_oil_left(FakeDb({"oil_left": []}), left_out)
ok(open(left_out).read() == "customer_id,date,gallons_left\n", "no checks yet exports just the header")
ok(not left_db.writes, "the Oil Left export writes nothing to the database")
ok(open(ss.OIL_LEFT_FILE).readline().strip() == "customer_id,date,gallons_left", "the committed oil_left.csv has the export's header")

print(f"\n{'FAILED' if failures else 'All passed'} ({failures} failure(s))")
sys.exit(1 if failures else 0)
