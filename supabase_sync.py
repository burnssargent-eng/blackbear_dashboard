#!/usr/bin/env python3
"""
Keep the shared database (Supabase, db/schema.sql) in step with the source site.

The site and the source site are both sources (Sarge, 2026-10-21): gallons
are entered in both, and the nightly never overwrites a route or a pickup.
Routes are the one exception, by --routes (below). The nightly:

  customers  upserts the source site's active customers (name, town, capacity,
             periodicity, address, map position) and marks ones no longer
             active. Notes go in only for a customer new to the database (all
             of them on --seed): they
             are the full notes, codes included, from customer_notes.local.json
             (written by jamaal_customers.py on this machine, never committed),
             else the published ones.
  routes     upserts the source site's route ids and names.
  stops      --seed only, and only into an empty table: the source site's
             assigned routes as the starting point.
  history    the source site's whole record (Sarge, 2026-10-24): every pickup
             and 0-3 code from the scrape, and its outgoing log, as rows with
             source = 'jamaal'. The first run copies everything; after that the
             last HISTORY_DAYS are refreshed each night (new entries added, edits
             and removals on the source site followed); --history redoes it all.
             Customers seen only in that history are added, not active.
  report     where the site's routes differ from the source site's, and
             pickups entered on one but not the other, saved to sync_reports.

--routes sets the site's routes to the source site's Today's Routes
(Sarge, 2026-10-26: during the trial the two must start each day the same).
refresh-routes.yml runs it at 7am Vermont time, after a fresh read of the
source site; edits made on the site after that stand until the next morning.
Every route the source site has is set to its stops, in its order, with its
Must Get; one with no stops there is emptied. Routes made on the site
(negative ids) are left alone. A stop whose customer the database does not
have yet is skipped and listed: the nightly adds the customer, and the next
morning's run the stop. What the site had that differed is kept in the
sync_reports row, so an overwritten edit can be seen.

Addresses and notes live only in the database (step 6, 2026-10-24):
customer_details.json is no longer committed. --export-details writes it, and
customer_notes.local.json, from the database at the start of the nightly, so
jamaal_customers.py reads only customers new to the database and the sync
writes everyone's address back. Without that file the sync refuses to run,
rather than blank every address.

--sweep (weekly, Sundays; Sarge, 2026-11-01) brings in address, notes and
phone edits made on the source site after a customer reached the database, without
undoing edits made here (the reviewed contact lookup, fixes on the site).
jamaal_customers.py --sweep reads every page first. `jamaal_address`,
`jamaal_notes` and `jamaal_phone` hold what the source site said at the last
sweep; per field:
  none yet                  the first sweep: recorded, nothing changed
  the source site unchanged nothing
  the source site changed   ours still matches its old value: ours follows (an
                            address moves the pin: the site's position, else the
                            Census geocoder, else the town centre); ours was
                            changed here: ours kept, listed in the report
  the source site now blank nothing; listed
A customer with no phone takes the source site's whenever it has one, first
sweep included (phone_source 'jamaal'): that is how new customers get one.
Must run AFTER the nightly sync, which writes back the addresses exported
before the sweep.

--export-site-edits (nightly, BEFORE the scrape; Sarge, 2026-11-02) writes
customer_edits.csv: the names, towns and capacities edited on the site's
customer page (customers.site_*: name, town, capacity, region, themes,
status), which beat the source site's and the config's. Ids, names, towns and capacities only, so it is committed; the
scraper applies the towns (regions, map, counties) and roster names, the
projection builder the capacities and names. The nightly keeps writing the
source site's own values into name, city and capacity beside them.

--place-pins (nightly, after the sync) moves the map pin of each address
edited on the site (the page empties `loc`), and places customers added on
the site: the Census geocoder, else the town centre, at most PIN_LIMIT a night.

Oil Left (gallons a driver saw left in a barrel and did not collect) is
entered only on the site. --export-oil-left writes it to oil_left.csv
(customer id, day, gallons: nothing private), which is committed so the
projection builder can read it; the builder never contacts the database.
Thefts marked on the site (collections.theft) go the same way:
--export-thefts writes thefts.csv (customer id, day, gallons collected; never
the note), and the builder treats each as a confirmed theft (THEFTS).

--export-collections --since DAY (Sarge, 2026-11-13: the step that lets the
site run without the source site) replaces the scrape's pickups and 0-3 codes
from DAY on, in oil_collections_raw.csv and oil_non_pickups.csv, with the
site's; rows before DAY, the source site's history, are kept as they are. A
plain `python3 oil_scraper.py` (no network) then rebuilds the dashboard files
and the projection builder runs as usual. It refuses, writing nothing, when
the database returns under half the pickups the file holds for the window.
Not in the nightly yet: until the switch the scrape stays the source.

    python3 supabase_sync.py --seed  # first fill (run after jamaal_customers.py --refresh)
    python3 supabase_sync.py --export-details   # nightly, first: the details files from the database
    python3 supabase_sync.py --export-oil-left  # nightly, before the projections: oil_left.csv
    python3 supabase_sync.py --export-thefts    # nightly, before the projections: thefts.csv
    python3 supabase_sync.py --export-collections --since 2026-10-26   # the site's pickups from that day on
    python3 supabase_sync.py          # nightly
    python3 supabase_sync.py --routes   # 7am: the site's routes set to the source site's
    python3 supabase_sync.py --history  # also re-copy the source site's whole history
    python3 supabase_sync.py --sweep    # weekly, after the sync: source-site address and notes edits
    python3 supabase_sync.py --dry-run  # print what would be sent, touch nothing

Needs SUPABASE_SERVICE_KEY (a secret key: the nightly reads it from GitHub
secrets). Reads only committed files plus the two details files; it never
contacts the source site itself.
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://gaxpuvcfgtebklvwfpik.supabase.co")
ROUTES_FILE = "jamaal_routes.json"
DETAILS_FILE = "customer_details.json"
LOCAL_NOTES = "customer_notes.local.json"
CAPACITY_FILE = "capacity_cache.csv"
COLLECTIONS_FILE = "oil_collections.json"
CODES_FILE = "oil_non_pickups.csv"
RAW_FILE = "oil_collections_raw.csv"
OUTGOING_FILE = "outgoing.json"
EDITS_FILE = "customer_edits.csv"   # names, towns, capacities edited on the site
PIN_LIMIT = 60                     # geocoder calls a night for --place-pins
OIL_LEFT_FILE = "oil_left.csv"
THEFTS_FILE = "thefts.csv"
SWEEP_FILE = "jamaal_sweep.local.json"
SWEPT = ("address", "notes", "phone")   # fields --sweep follows
ROUTES_MAX_AGE = timedelta(hours=1)   # --routes refuses an older jamaal_routes.json
HISTORY_DAYS = 90         # how far back the nightly follows edits on the source site
REPORT_DAYS = 30          # how far back pickups are compared
BATCH = 500
VERMONT = ZoneInfo("America/New_York")


class SyncError(Exception):
    pass


# ── Reading the committed files ─────────────────────────────

def load_json(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        raise SyncError(f"{path} is missing")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_capacities(path=CAPACITY_FILE):
    """customer id -> (capacity or None, periodicity_days or None)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            num = lambda v: int(float(v)) if v not in (None, "") else None
            out[int(row["customer_id"])] = (num(row.get("capacity")), num(row.get("periodicity_days")))
    return out


def route_rows(snap):
    """
    One row per source-site route id, as the shadow site lists them
    (ops-common.js opsRouteList): Today's Routes' name where the route has
    stops, the Assign dropdown's other names in other_names, dropdown order.
    """
    todays = {r["route_id"]: r["name"] for r in snap.get("routes", [])}
    rows = {}
    for r in snap.get("route_names", []):
        rid = r["route_id"]
        if rid in rows:
            if r["name"] not in rows[rid]["other_names"] and r["name"] != rows[rid]["name"]:
                rows[rid]["other_names"].append(r["name"])
            continue
        name = todays.get(rid, r["name"])
        rows[rid] = {"id": rid, "name": name, "other_names": [] if name == r["name"] else [r["name"]],
                     "sort": len(rows)}
    for r in snap.get("routes", []):
        if r["route_id"] not in rows:
            rows[r["route_id"]] = {"id": r["route_id"], "name": r["name"], "other_names": [], "sort": len(rows)}
    return list(rows.values())


def customer_rows(snap, details, capacities):
    """
    The source site's customers, keyed by id: everyone on the Assign list
    (active) plus anyone on an assigned route but missing from it (as SNORLAX,
    1524, on 2026-10-24), who is kept but not marked active.
    """
    rows = {}

    def base(cid, name, city, active):
        d = details.get(str(cid), {})
        cap, period = capacities.get(cid, (None, None))
        return {"id": cid, "name": name, "city": city or None, "address": d.get("address") or None,
                "lat": d.get("lat"), "lon": d.get("lon"), "loc": d.get("loc"),
                "capacity": cap, "periodicity_days": period, "active": active}

    for a in snap.get("assign_list", []):
        rows[a["customer_id"]] = base(a["customer_id"], a["name"], a.get("city"), True)
    for r in snap.get("routes", []):
        for s in r["stops"]:
            cid = s.get("customer_id")
            if cid is None or cid in rows:
                continue
            row = base(cid, s["name"], None, False)
            if row["capacity"] is None:
                row["capacity"] = s.get("capacity")
            rows[cid] = row
    return rows


def replaced_map(db):
    """
    {old id: new id} for customers taken over by a new account on the site
    (customers.replaced_by). Empty before db/schema.sql adds the column, so the
    nightly keeps working until it is pasted.
    """
    try:
        rows = db.select("customers", "select=id,replaced_by&replaced_by=not.is.null")
    except SyncError:
        return {}
    return {r["id"]: r["replaced_by"] for r in rows if r.get("replaced_by")}


def successor(cid, replaced):
    """The account that now stands for `cid`, following takeovers (A -> B -> C)."""
    seen = set()
    while cid in replaced and cid not in seen:
        seen.add(cid)
        cid = replaced[cid]
    return cid


def take_over_stops(stops, replaced):
    """Route stops with each replaced customer swapped for its new account, once per route."""
    out, seen = [], set()
    for s in stops:
        cid = successor(s["customer_id"], replaced)
        if (s["route_id"], cid) in seen:
            continue
        seen.add((s["route_id"], cid))
        out.append({**s, "customer_id": cid,
                    **({"jamaal_assignment_id": None} if cid != s["customer_id"] and "jamaal_assignment_id" in s else {})})
    return out


def snap_taken_over(snap, replaced):
    """The routes snapshot with each replaced customer read as its new account, for comparing."""
    if not replaced:
        return snap
    return {**snap, "routes": [{**r, "stops": [
        {**s, "customer_id": successor(s.get("customer_id"), replaced)} for s in r["stops"]]}
        for r in snap.get("routes", [])]}


def full_notes(cid, details, local_notes):
    """A customer's notes with codes and phones, when this machine has them."""
    return local_notes.get(str(cid)) or details.get(str(cid), {}).get("notes") or None


def stop_rows(snap):
    """The source site's assigned routes as route_stops rows (--seed and --routes)."""
    out = []
    for r in snap.get("routes", []):
        seen = set()
        for s in r["stops"]:
            cid = s.get("customer_id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            out.append({"route_id": r["route_id"], "customer_id": cid, "position": len(seen),
                        "must_get": bool(s.get("must_get")), "jamaal_assignment_id": s.get("assignment_id")})
    return out


# ── Comparing ───────────────────────────────────────────────

def route_differences(snap, db_stops, names):
    """
    Per source-site route: customers on it there but not on the site, on the
    site but not there, Must Get set differently, and whether the shared
    customers run in a different order. Only routes that differ are listed.
    """
    site = {}
    for s in sorted(db_stops, key=lambda s: (s["route_id"], s["position"])):
        site.setdefault(s["route_id"], []).append(s)
    out = []
    for r in snap.get("routes", []):
        rid = r["route_id"]
        theirs = [s for s in r["stops"] if s.get("customer_id") is not None]
        ours = site.get(rid, [])
        their_ids = [s["customer_id"] for s in theirs]
        our_ids = [s["customer_id"] for s in ours]
        only_jamaal = [c for c in dict.fromkeys(their_ids) if c not in our_ids]
        only_site = [c for c in our_ids if c not in their_ids]
        their_must = {s["customer_id"]: bool(s.get("must_get")) for s in theirs}
        must = [s["customer_id"] for s in ours
                if s["customer_id"] in their_must and their_must[s["customer_id"]] != bool(s["must_get"])]
        common_theirs = [c for c in dict.fromkeys(their_ids) if c in our_ids]
        common_ours = [c for c in our_ids if c in their_ids]
        order = common_theirs != common_ours
        if only_jamaal or only_site or must or order:
            label = lambda c: {"id": c, "name": names.get(c, f"#{c}")}
            out.append({"route_id": rid, "route": r["name"],
                        "only_jamaal": [label(c) for c in only_jamaal],
                        "only_site": [label(c) for c in only_site],
                        "must_get_differs": [label(c) for c in must],
                        "order_differs": order})
    return out


def jamaal_entries(collections, codes_path, since):
    """(customer id, date) -> gallons for every source-site entry on or after `since`, codes included."""
    out = {}
    for r in collections.get("records", []):
        if r["date"] >= since:
            out[(r["customer_id"], r["date"])] = out.get((r["customer_id"], r["date"]), 0) + r["gallons"]
    if os.path.exists(codes_path):
        with open(codes_path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["date"] >= since:
                    out.setdefault((int(row["customer_id"]), row["date"]), int(row["qty"]))
    return out


def jamaal_collection_rows(raw_path=None, codes_path=None):
    """
    Every source-site pickup (the scrape's cache) and 0-3 code, as collections
    rows keyed customer:date:n. The source site has no pickup ids, so n numbers
    a customer's entries on one day in order of gallons, which keeps the key of
    an unchanged entry the same from night to night.
    """
    raw_path, codes_path = raw_path or RAW_FILE, codes_path or CODES_FILE
    if not os.path.exists(raw_path):
        raise SyncError(f"{raw_path} is missing")
    entries = {}
    for path, col in ((raw_path, "gallons"), (codes_path, "qty")):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                entries.setdefault((int(row["customer_id"]), row["date"]), []).append(int(float(row[col])))
    out = {}
    for (cid, day), gallons in sorted(entries.items()):
        for n, g in enumerate(sorted(gallons)):
            key = f"{cid}:{day}:{n}"
            out[key] = {"jamaal_key": key, "source": "jamaal", "customer_id": cid, "picked_on": day,
                        "gallons": g, "entered_at": None}
    return out


def jamaal_outgoing_rows(path=None):
    """The source site's outgoing log, one row per stillage run or Schmutz volume."""
    out = {}
    for day, d in sorted(load_json(path or OUTGOING_FILE, {"days": {}}).get("days", {}).items()):
        for truck, count in sorted(d.get("stillage", {}).items()):
            for n in range(count):
                key = f"{day}:stillage:{truck}:{n}"
                out[key] = {"jamaal_key": key, "source": "jamaal", "day": day, "kind": "stillage",
                            "truck": truck, "gallons": None, "entered_at": None}
        for n, g in enumerate(sorted(d.get("schmutz", []))):
            key = f"{day}:schmutz:{n}"
            out[key] = {"jamaal_key": key, "source": "jamaal", "day": day, "kind": "schmutz",
                        "truck": None, "gallons": g, "entered_at": None}
    return out


def history_customers(ids, roster, known):
    """Customers in the source site's history but not in the database, as inactive rows."""
    rows = []
    for cid in sorted(set(ids) - set(known)):
        r = roster.get(cid) or {}
        rows.append({"id": cid, "name": r.get("name") or f"Customer {cid} (name not on file)",
                     "city": r.get("city") or None, "active": False})
    return rows


def sync_history(db, table, date_col, wanted, since=None):
    """
    Make the database's source-site rows in `table` match `wanted`: on or after
    `since` only, or all of them when `since` is None. Rows entered on this site
    are never touched.
    """
    if since:
        wanted = {k: r for k, r in wanted.items() if r[date_col] >= since}
    query = "select=jamaal_key&source=eq.jamaal" + (f"&{date_col}=gte.{since}" if since else "")
    have = {r["jamaal_key"] for r in db.select(table, query)}
    stale = sorted(have - set(wanted))
    if stale:
        db.delete(table, "jamaal_key", stale)
    if wanted:
        db.upsert(table, list(wanted.values()), on_conflict="jamaal_key")
    return {"rows": len(wanted), "removed": len(stale), "since": since or "all"}


def pickup_differences(site_rows, jamaal, through, names):
    """
    Pickups entered on one system and not the other, up to `through` (the last
    day the nightly scrape covers). A source-site entry counts as missing from
    the site only on days the site was used, so the report does not list every
    pickup from before the site existed. The source site stamps an entry with
    the day it was typed, so a match one day either side counts.
    """
    site = {}
    for c in site_rows:
        if c["picked_on"] <= through:
            key = (c["customer_id"], c["picked_on"])
            site[key] = site.get(key, 0) + c["gallons"]
    used_days = {d for (_, d) in site}

    def near(table, cid, day):
        d = date.fromisoformat(day)
        for delta in (0, -1, 1):
            k = (cid, (d + timedelta(days=delta)).isoformat())
            if k in table:
                return k
        return None

    label = lambda cid, day, gal: {"id": cid, "name": names.get(cid, f"#{cid}"), "date": day, "gallons": gal}
    only_site, only_jamaal, gallons_differ = [], [], []
    for (cid, day), gal in sorted(site.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        k = near(jamaal, cid, day)
        if k is None:
            only_site.append(label(cid, day, gal))
        elif jamaal[k] != gal:
            gallons_differ.append({**label(cid, day, gal), "jamaal_gallons": jamaal[k]})
    for (cid, day), gal in sorted(jamaal.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if day in used_days and day <= through and near(site, cid, day) is None:
            only_jamaal.append(label(cid, day, gal))
    return {"only_site": only_site, "only_jamaal": only_jamaal, "gallons_differ": gallons_differ}


def renamed_customers(existing, wanted):
    """
    Source-site customers whose name changed since the database last saw it,
    as [{"id", "was", "now"}]. The source site keeps no name history, so a new
    owner keeping the old barrel shows up only as a rename (Blue Stone became
    "Mad Moose/Blue Stone"). Listed in the nightly report as a POSSIBLE
    handover for someone to check against customer_eras.csv; never decided here.
    Spacing and case alone are not a rename.
    """
    norm = lambda n: " ".join((n or "").split()).casefold()
    return [{"id": cid, "was": existing[cid]["name"], "now": r["name"]}
            for cid, r in sorted(wanted.items())
            if cid in existing and existing[cid].get("name") and norm(existing[cid]["name"]) != norm(r["name"])]


def summary_text(routes, pickups, renames=()):
    parts = [f"{len(routes)} route(s) differ from the source site" if routes else "routes match the source site"]
    n = {k: len(v) for k, v in pickups.items()}
    if any(n.values()):
        parts.append(f"pickups: {n['only_site']} only on the site, {n['only_jamaal']} only on the source site, "
                     f"{n['gallons_differ']} with different gallons")
    else:
        parts.append("pickups match")
    if renames:
        parts.append(f"{len(renames)} customer(s) renamed on the source site (possible new owner: "
                     "check customer_eras.csv)")
    return "; ".join(parts)


# ── Talking to the database ─────────────────────────────────

class Db:
    def __init__(self, url, key, dry_run=False):
        self.base = url.rstrip("/") + "/rest/v1"
        self.dry_run = dry_run
        self.s = requests.Session()
        self.s.headers.update({"apikey": key, "Content-Type": "application/json"})
        if key.startswith("eyJ"):        # a legacy service_role JWT also needs the bearer header
            self.s.headers["Authorization"] = f"Bearer {key}"

    def _check(self, resp, what):
        if resp.status_code >= 300:
            raise SyncError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
        return resp

    def select(self, table, query="select=*"):
        rows, offset = [], 0
        while True:
            resp = self._check(self.s.get(f"{self.base}/{table}?{query}&limit=1000&offset={offset}",
                                          timeout=30), f"read {table}")
            page = resp.json()
            rows += page
            if len(page) < 1000:
                return rows
            offset += 1000

    def upsert(self, table, rows, on_conflict="id"):
        if self.dry_run:
            print(f"[dry run] upsert {len(rows)} row(s) into {table}")
            return
        for i in range(0, len(rows), BATCH):
            self._check(self.s.post(f"{self.base}/{table}?on_conflict={on_conflict}", json=rows[i:i + BATCH],
                                    headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                                    timeout=60), f"upsert {table}")

    def exists(self, table, filters):
        resp = self._check(self.s.get(f"{self.base}/{table}?select=id&{filters}&limit=1", timeout=30), f"read {table}")
        return bool(resp.json())

    def delete(self, table, column, values):
        if self.dry_run:
            print(f"[dry run] delete {len(values)} row(s) from {table}")
            return
        for i in range(0, len(values), 100):
            quoted = ",".join('"' + str(v).replace('"', '') + '"' for v in values[i:i + 100])
            self._check(self.s.delete(f"{self.base}/{table}?{column}=in.({quoted})",
                                      headers={"Prefer": "return=minimal"}, timeout=60), f"delete {table}")

    def insert(self, table, rows):
        if self.dry_run:
            print(f"[dry run] insert {len(rows)} row(s) into {table}")
            return
        for i in range(0, len(rows), BATCH):
            self._check(self.s.post(f"{self.base}/{table}", json=rows[i:i + BATCH],
                                    headers={"Prefer": "return=minimal"}, timeout=60), f"insert {table}")

    def update(self, table, filters, values):
        if self.dry_run:
            print(f"[dry run] update {table} where {filters}: {values}")
            return
        self._check(self.s.patch(f"{self.base}/{table}?{filters}", json=values,
                                 headers={"Prefer": "return=minimal"}, timeout=60), f"update {table}")


def details_from_rows(rows, scrub):
    """
    The database's customers as the two files jamaal_customers.py keeps:
    customer_details.json (notes with codes and phones cut) and the full notes
    where cutting changed them. Customers made on the site (negative ids) are
    not on the source site, so they are left out.
    """
    customers, local = {}, {}
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["id"] <= 0:
            continue
        cid, full = str(r["id"]), r.get("notes") or ""
        cut = scrub(full)
        if cut != full:
            local[cid] = full
        customers[cid] = {"address": r.get("address") or "", "lat": r.get("lat"), "lon": r.get("lon"),
                          "loc": r.get("loc"), "notes": cut, "photos": []}
    return customers, local


def export_details(db, out=DETAILS_FILE, notes_out=LOCAL_NOTES):
    from jamaal_customers import scrub
    from jamaal_routes import write_atomic
    rows = db.select("customers", "select=id,address,lat,lon,loc,notes")
    if not any(r["id"] > 0 for r in rows):
        raise SyncError("the database has no source-site customers: nothing exported")
    customers, local = details_from_rows(rows, scrub)
    write_atomic(out, {"scraped_at": datetime.now(VERMONT).isoformat(timespec="seconds"),
                       "source": "the shared database (supabase_sync.py --export-details)",
                       "customers": customers})
    write_atomic(notes_out, local)
    print(f"Exported {len(customers)} customers' details from the database ({len(local)} with full notes)")
    return customers, local


def oil_left_lines(rows):
    """The database's Oil Left rows as oil_left.csv rows, by customer then day."""
    return sorted((int(r["customer_id"]), r["checked_on"], int(r["gallons_left"])) for r in rows)


def theft_lines(rows):
    """The database's theft collections as thefts.csv rows (no notes), by customer then day."""
    return sorted({(int(r["customer_id"]), r["picked_on"], int(r["gallons"])) for r in rows})


RAW_FILE = "oil_collections_raw.csv"
RAW_FIELDS = ["customer_id", "name", "city", "county", "date", "year", "month", "gallons", "is_active", "geo_town"]
NON_PICKUP_FILE = "oil_non_pickups.csv"
NON_PICKUP_FIELDS = ["customer_id", "date", "qty"]
EMPTY_QTYS = {0, 1, 2, 3}          # must match oil_scraper.EMPTY_QTYS (tests/export_collections_test.py)
EXPORT_MIN_SHARE = 0.5             # refuse when the database has under half the window's pickups


def site_collection_files(collections, customers, raw_rows, code_rows, since):
    """
    --export-collections, without files or network: the scrape's two outputs
    with every entry from `since` (a "YYYY-MM-DD" day) on replaced by the
    site's. Returns (raw rows, code rows, window counts), rows as dicts in the
    files' own columns.

      collections  the database's site rows: customer_id, picked_on, gallons
      customers    the database's customers: id, name, city, active
      raw_rows     oil_collections_raw.csv as it stands (dicts of strings)
      code_rows    oil_non_pickups.csv as it stands

    Rows before `since` are kept as they are: that is the source site's
    history, and nothing after the switch can rebuild it. A pickup over 3
    gallons goes to the raw file and a 0-3 code to the non-pickups file, as
    the scraper splits them. Each customer keeps its block in the raw file
    (newest first, as the scraper writes it) with the site's rows at the top;
    a customer new to the file goes at the end. name and is_active follow the
    database on every row, as a scrape stamps the current ones (after the
    switch the site decides who is active). A new row's city is the
    customer's newest row's, else the database's; customer_edits.csv corrects
    towns, and a plain oil_scraper.py run re-derives county and geo_town.
    """
    by_id = {int(c["id"]): c for c in customers}
    order, kept, newest = [], {}, {}
    for r in raw_rows:
        cid = int(r["customer_id"])
        if cid not in newest:
            order.append(cid)
            newest[cid] = r
        if r["date"] < since:
            kept.setdefault(cid, []).append(dict(r))

    fresh, codes = {}, []
    for row in collections:
        day, gal, cid = row["picked_on"], int(row["gallons"]), int(row["customer_id"])
        if day < since:
            continue
        if gal in EMPTY_QTYS:
            codes.append({"customer_id": str(cid), "date": day, "qty": str(gal)})
            continue
        old = newest.get(cid) or {}
        db_city = (by_id.get(cid) or {}).get("city") or ""
        fresh.setdefault(cid, []).append({
            "customer_id": str(cid), "city": old.get("city") or db_city, "county": old.get("county", ""),
            "date": day, "year": day[:4], "month": day[:7], "gallons": str(gal),
            "geo_town": old.get("geo_town", "")})
    for rows in fresh.values():
        rows.sort(key=lambda r: (r["date"], int(r["gallons"])), reverse=True)

    out = []
    for cid in order:
        out += fresh.get(cid, []) + kept.get(cid, [])
    for cid in sorted(set(fresh) - set(order)):
        out += fresh[cid]
    for r in out:
        cid = int(r["customer_id"])
        c, old = by_id.get(cid), newest.get(cid)
        r["name"] = (c and c.get("name")) or (old and old["name"]) or f"#{cid}"
        if c and c.get("active") is not None:
            r["is_active"] = str(bool(c["active"]))
        else:
            r["is_active"] = old["is_active"] if old else ""

    codes_out = [dict(r) for r in code_rows if r["date"] < since] + codes
    codes_out.sort(key=lambda r: (int(r["customer_id"]), r["date"], int(r["qty"])))
    window = {"file_pickups": sum(1 for r in raw_rows if r["date"] >= since),
              "file_codes": sum(1 for r in code_rows if r["date"] >= since),
              "site_pickups": sum(len(v) for v in fresh.values()), "site_codes": len(codes)}
    return out, codes_out, window


def write_collection_files(raw, codes, raw_file=RAW_FILE, codes_file=NON_PICKUP_FILE):
    """The two files in the scraper's own formats (pandas' \\n; csv's \\r\\n for the codes)."""
    for path, fields, rows, term in ((raw_file, RAW_FIELDS, raw, "\n"),
                                     (codes_file, NON_PICKUP_FIELDS, codes, "\r\n")):
        tmp = path + ".tmp"
        with open(tmp, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator=term, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)


def read_csv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def export_collections(db, since, raw_file=RAW_FILE, codes_file=NON_PICKUP_FILE):
    """
    --export-collections --since DAY: the site's pickups and 0-3 codes replace
    the scrape's from DAY on, in oil_collections_raw.csv and oil_non_pickups.csv.
    Then a plain `python3 oil_scraper.py` (no network) rebuilds the dashboard
    files from them, and the projection builder runs as usual. Refuses,
    writing nothing, when the database returns under EXPORT_MIN_SHARE of the
    pickups the file already holds for the window: an empty or partial read
    must never replace real history.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since or ""):
        raise SyncError(f"--since must be a YYYY-MM-DD day, not {since!r}")
    collections = db.select("collections", "select=customer_id,picked_on,gallons"
                                           f"&source=eq.site&picked_on=gte.{since}&order=id")
    customers = db.select("customers", "select=id,name,city,active&order=id")
    raw, codes, window = site_collection_files(collections, customers, read_csv_rows(raw_file),
                                               read_csv_rows(codes_file), since)
    if window["site_pickups"] < EXPORT_MIN_SHARE * window["file_pickups"]:
        raise SyncError(f"the database has {window['site_pickups']} site pickup(s) since {since}, "
                        f"the file {window['file_pickups']}: nothing written")
    write_collection_files(raw, codes, raw_file, codes_file)
    print(f"Site collections since {since}: {window['site_pickups']} pickup(s) and {window['site_codes']} "
          f"code(s), in place of the file's {window['file_pickups']} and {window['file_codes']}")
    return window


def export_thefts(db, out=THEFTS_FILE):
    """--export-thefts: thefts marked on the site, for the projection builder."""
    rows = theft_lines(db.select("collections", "select=customer_id,picked_on,gallons&theft=is.true"))
    tmp = out + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["customer_id", "date", "collected"])
        w.writerows(rows)
    os.replace(tmp, out)
    print(f"Exported {len(rows)} theft(s) to {out}")
    return rows


def export_oil_left(db, out=OIL_LEFT_FILE):
    rows = oil_left_lines(db.select("oil_left", "select=customer_id,checked_on,gallons_left"))
    tmp = out + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["customer_id", "date", "gallons_left"])
        w.writerows(rows)
    os.replace(tmp, out)
    print(f"Exported {len(rows)} Oil Left check(s) to {out}")
    return rows


EDIT_COLUMNS = ["customer_id", "name", "town", "capacity", "region", "themes", "status", "status_since",
                "model", "season"]
EDIT_SELECT = "select=id,site_name,site_town,site_capacity,site_region,site_themes,site_status,site_status_since"
# Projection type (db/schema.sql, site_model): read separately so the export
# still runs before the schema adds the columns.
EDIT_SELECT_MODEL = EDIT_SELECT + ",site_model,site_season"


def edit_lines(rows):
    """
    The customers edited on the site's customer page, as customer_edits.csv
    rows (EDIT_COLUMNS; blank = the source site's or the config's). Themes are
    joined with ";", and an explicit empty list is written "none". Source-site
    customers only: one added on the site is not in the scrape, and its own
    fields are already the site's.
    """
    out = []
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["id"] <= 0:
            continue
        cap, themes = r.get("site_capacity"), r.get("site_themes")
        line = [int(r["id"]), (r.get("site_name") or "").strip(), (r.get("site_town") or "").strip(),
                "" if cap is None else int(cap), (r.get("site_region") or "").strip(),
                "" if themes is None else (";".join(themes) or "none"),
                r.get("site_status") or "", r.get("site_status_since") or "",
                r.get("site_model") or "", r.get("site_season") or ""]
        if any(v != "" for v in line[1:]):
            out.append(line)
    return out


def lapsed_statuses(rows, pickups):
    """
    Ids whose site status a later pickup has ended: `rows` hold site_status and
    site_status_since, `pickups` maps id -> latest pickup day (ISO, over 3 gallons).
    """
    return sorted(r["id"] for r in rows
                  if r.get("site_status") and r.get("site_status_since")
                  and (pickups.get(r["id"]) or "") > r["site_status_since"])


def clear_lapsed_statuses(db):
    """After the sync: a pickup after a site status (inactive, closed, open) ends it."""
    rows = db.select("customers", "select=id,site_status,site_status_since&site_status=not.is.null")
    latest = {}
    for r in rows:
        hit = db.select("collections", f"select=picked_on&customer_id=eq.{r['id']}&gallons=gt.3"
                                       f"&picked_on=gt.{r['site_status_since']}&order=picked_on.desc"
                        ) if r.get("site_status_since") else []
        if hit:
            latest[r["id"]] = hit[0]["picked_on"]
    gone = lapsed_statuses(rows, latest)
    if gone:
        db.update("customers", f"id=in.({','.join(map(str, gone))})", {"site_status": None, "site_status_since": None})
    print(f"Site statuses ended by a pickup: {len(gone)}")
    return gone


def export_site_edits(db, out=EDITS_FILE):
    """--export-site-edits: write customer_edits.csv (nothing private) for the scraper and the builder."""
    try:
        rows = edit_lines(db.select("customers", EDIT_SELECT_MODEL))
    except SyncError as exc:   # before db/schema.sql adds site_model
        print(f"Projection types not exported: {exc}")
        rows = edit_lines(db.select("customers", EDIT_SELECT))
    tmp = out + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(EDIT_COLUMNS)
        w.writerows(rows)
    os.replace(tmp, out)
    print(f"Exported {len(rows)} customer edit(s) to {out}")
    return rows


def pin_query(row):
    """
    What to send the geocoder for a customer's address: the address itself when
    it already names a town and state ("12 Main St, Barre, VT 05641"), else the
    street with the customer's town added, or None when there is no street.
    """
    from jamaal_customers import has_street
    address = (row.get("address") or "").strip()
    if has_street(address):
        return address
    town = (row.get("site_town") or row.get("city") or "").strip()
    if not address or not town or not re.match(r"\s*\d", address):
        return None
    return f"{address}, {town}" if re.search(r", [A-Z]{2}$", town) else f"{address}, {town}, VT"


def pins_wanted(rows):
    """
    Customers whose pin must move: `loc` emptied by an address edit on the site
    (ours no longer the source site's address), or a customer added on the site
    with an address. A customer the source site never placed keeps no pin, as before.
    """
    return [r for r in sorted(rows, key=lambda r: r["id"])
            if r.get("loc") is None and (r.get("address") or "").strip()
            and (r["id"] < 0 or (r.get("address") or "").strip() != (r.get("jamaal_address") or "").strip())]


def place_pins(db, limit=PIN_LIMIT):
    """
    --place-pins (nightly, after the sync): a pin for each address edited on the
    site, from the Census geocoder, else the town centre. One the geocoder
    cannot reach waits for the next night, at most `limit` a night.
    """
    from jamaal_customers import census_geocode, town_centres
    rows = db.select("customers", "select=id,name,city,site_town,address,jamaal_address,loc&loc=is.null")
    census, centres = requests.Session(), town_centres()
    placed = {"census": 0, "town": 0, "none": 0}
    for r in pins_wanted(rows)[:limit]:
        pin, query = None, pin_query(r)
        if query:
            try:
                hit = census_geocode(census, query)
            except requests.RequestException:
                hit = None
            if hit:
                pin = (*hit, "census")
        if not pin:
            town = (r.get("site_town") or r.get("city") or "").lower()
            pin = (*centres[town], "town") if town in centres else None
        if not pin:
            placed["none"] += 1
            continue
        db.update("customers", f"id=eq.{r['id']}", {"lat": pin[0], "lon": pin[1], "loc": pin[2]})
        placed[pin[2]] += 1
    print(f"Pins: {placed['census']} from the street address, {placed['town']} at the town centre, "
          f"{placed['none']} not placed")
    return placed


def sync(db, seed=False, today=None, history=False):
    if not os.path.exists(DETAILS_FILE):
        raise SyncError(f"{DETAILS_FILE} is missing (run --export-details first): nothing synced, "
                        "so no address is blanked")
    snap = load_json(ROUTES_FILE)
    details = load_json(DETAILS_FILE, {"customers": {}}).get("customers", {})
    local_notes = load_json(LOCAL_NOTES, {})
    capacities = load_capacities()
    names = {}

    # Customers: the source site's fields always; notes only for new ones.
    wanted = customer_rows(snap, details, capacities)
    # A customer taken over by a new account stays inactive whatever the source site says.
    for cid in replaced_map(db):
        if cid in wanted:
            wanted[cid]["active"] = False
    names.update({cid: r["name"] for cid, r in wanted.items()})
    existing = {r["id"]: r for r in db.select("customers", "select=id,active,name")}
    renames = renamed_customers(existing, wanted)
    names.update({r["id"]: r["name"] for r in existing.values()})
    # A seed writes everyone's notes (it runs after a full re-read of the customer pages).
    with_notes = lambda cid: seed or cid not in existing
    new = [{**r, "notes": full_notes(cid, details, local_notes)} for cid, r in wanted.items() if with_notes(cid)]
    known = [r for cid, r in wanted.items() if not with_notes(cid)]
    if new:
        db.upsert("customers", new)
    if known:
        db.upsert("customers", known)
    gone = sorted(cid for cid, r in existing.items() if cid > 0 and r["active"] and cid not in wanted)
    if gone:
        db.update("customers", f"id=in.({','.join(map(str, gone))})", {"active": False})

    # Routes: ids and names from the source site; routes made on the site (negative ids) untouched.
    db.upsert("routes", route_rows(snap))

    # The source site's history: everything on the first run (or --history), then the last HISTORY_DAYS.
    now = today or datetime.now(VERMONT).date()
    full = history or not db.exists("collections", "source=eq.jamaal")
    since = None if full else (now - timedelta(days=HISTORY_DAYS)).isoformat()
    pickups_wanted = jamaal_collection_rows()
    roster = {c["customer_id"]: c for c in load_json(COLLECTIONS_FILE, {}).get("customers", [])}
    added = history_customers({r["customer_id"] for r in pickups_wanted.values()}, roster,
                              set(existing) | set(wanted))
    if added:
        db.insert("customers", added)
    copied = {"collections": sync_history(db, "collections", "picked_on", pickups_wanted, since),
              "outgoing": sync_history(db, "outgoing", "day", jamaal_outgoing_rows(), since),
              "customers_added": len(added)}

    seeded = 0
    db_stops = db.select("route_stops", "select=route_id,customer_id,position,must_get")
    if seed:
        if db_stops:
            print(f"route_stops already holds {len(db_stops)} stop(s): not seeding over the site's routes")
        else:
            stops = stop_rows(snap)
            db.insert("route_stops", stops)
            seeded = len(stops)
            db_stops = stops

    # The report: this site's entries against the source site's.
    through = (now - timedelta(days=1)).isoformat()
    since = (now - timedelta(days=REPORT_DAYS)).isoformat()
    site_pickups = db.select("collections", f"select=customer_id,picked_on,gallons&source=eq.site&picked_on=gte.{since}")
    pickups = pickup_differences(site_pickups, jamaal_entries(load_json(COLLECTIONS_FILE, {}), CODES_FILE, since),
                                 through, names)
    # A taken-over customer's new account on a route is not a difference.
    routes = route_differences(snap_taken_over(snap, replaced_map(db)), db_stops, names)
    summary = summary_text(routes, pickups, renames)
    report = {"routes_read_at": snap.get("scraped_at"), "pickups_since": since, "pickups_through": through,
              "routes": routes, "pickups": pickups, "renames": renames,
              "customers": {"with_notes": len(new), "without_notes": len(known), "marked_inactive": len(gone)},
              "stops_seeded": seeded, "history": copied}
    db.insert("sync_reports", [{"kind": "seed" if seed else "nightly", "summary": summary, "report": report}])
    print(f"Customers: {len(new)} with notes, {len(known)} without, {len(gone)} marked inactive. "
          f"Routes: {len(route_rows(snap))}. Stops seeded: {seeded}. "
          f"Source-site history ({copied['collections']['since']}): {copied['collections']['rows']} pickups and codes, "
          f"{copied['outgoing']['rows']} outgoing, {copied['collections']['removed'] + copied['outgoing']['removed']} "
          f"removed, {copied['customers_added']} customers added. {summary}.")
    return report


def sweep_changes(rows, theirs, place, today=None):
    """
    The writes --sweep makes (module docstring) and its report. `rows` are the
    database's customers, `theirs` the sweep file's customers by id string, and
    `place(row, seen)` gives (lat, lon, loc) for a changed address, or None to
    leave the pin; `today` dates a phone taken from the source site.
    Returns ({id: {column: value}}, report).
    """
    updates = {}
    report = {"recorded_first_time": 0, "differs_at_first_sweep": [], "followed": [], "kept_ours": [],
              "blank_on_source_site": []}
    for r in sorted(rows, key=lambda r: r["id"]):
        seen = theirs.get(str(r["id"]))
        if r["id"] <= 0 or not seen:
            continue
        up, who, first = {}, {"id": r["id"], "name": r.get("name")}, False
        for f in SWEPT:
            now, last, ours = (seen.get(f) or "").strip(), r.get(f"jamaal_{f}"), r.get(f) or ""
            if f == "phone" and now and (not ours or (ours == last and now != last)):
                up.update({"jamaal_phone": now, "phone": now, "phone_source": "jamaal", "phone_checked": today})
                first = first or last is None
                report["followed"].append({**who, "field": f, "was": ours, "now": now})
                continue
            if last is None:   # blank is recorded too, so notes added there later are followed
                up[f"jamaal_{f}"], first = now, True
                if now and ours != now:
                    report["differs_at_first_sweep"].append({**who, "field": f})
                continue
            if now == last:
                continue
            if not now:
                report["blank_on_source_site"].append({**who, "field": f})
                continue
            up[f"jamaal_{f}"] = now
            if ours == last:
                up[f] = now
                if f == "address":
                    pin = place(r, seen)
                    if pin:
                        up["lat"], up["lon"], up["loc"] = pin
                report["followed"].append({**who, "field": f, "was": ours, "now": now})
            elif ours != now:
                report["kept_ours"].append({**who, "field": f, "ours": ours, "source_site_was": last,
                                            "source_site_now": now})
        report["recorded_first_time"] += first
        if up:
            updates[r["id"]] = up
    return updates, report


def sweep_details(db, path=SWEEP_FILE):
    """--sweep: apply the sweep file (sweep_changes) and leave a sync_reports row."""
    from jamaal_customers import census_geocode, has_street, town_centres
    sweep = load_json(path)
    census, centres = requests.Session(), town_centres()

    def place(row, seen):
        if seen.get("lat") is not None:
            return seen["lat"], seen["lon"], "site"
        if has_street(seen["address"]):
            try:
                hit = census_geocode(census, seen["address"])
            except requests.RequestException:
                hit = None
            if hit:
                return hit[0], hit[1], "census"
        town = (row.get("city") or "").lower()
        return (*centres[town], "town") if town in centres else None

    rows = db.select("customers", "select=id,name,city,address,notes,phone,lat,lon,loc,"
                                  "jamaal_address,jamaal_notes,jamaal_phone")
    updates, report = sweep_changes(rows, sweep.get("customers", {}), place,
                                    datetime.now(VERMONT).date().isoformat())
    for cid, values in updates.items():
        db.update("customers", f"id=eq.{cid}", values)
    report["read_at"] = sweep.get("read_at")
    count = lambda k, f: sum(1 for x in report[k] if x["field"] == f)
    summary = (f"{count('followed', 'address')} address(es), {count('followed', 'notes')} note(s) and "
               f"{count('followed', 'phone')} phone(s) followed the "
               f"source site; {len(report['kept_ours'])} kept ours; {report['recorded_first_time']} recorded "
               f"for the first time")
    db.insert("sync_reports", [{"kind": "sweep", "summary": summary, "report": report}])
    # Names and ids only: the notes carry door codes and the Actions log is not private.
    print(f"Sweep: {summary}.")
    for x in report["kept_ours"]:
        print(f"  kept ours: {x['name']} ({x['id']}) {x['field']}")
    return report


def mirror_routes(db, now=None):
    """
    --routes: the site's routes set to the source site's (module docstring).
    Writes the wanted stops first, then removes the rest, so a failure part
    way leaves extra stops rather than an emptied route.
    """
    snap = load_json(ROUTES_FILE)
    now = now or datetime.now(VERMONT)
    read_at = datetime.fromisoformat(snap.get("scraped_at") or "1970-01-01T00:00:00+00:00")
    if now - read_at > ROUTES_MAX_AGE:
        raise SyncError(f"{ROUTES_FILE} was read at {snap.get('scraped_at')}, over an hour ago: "
                        "run jamaal_routes.py first; routes left as they are")
    wanted = stop_rows(snap)
    if not wanted:
        raise SyncError(f"{ROUTES_FILE} has no assigned stops at all: likely a bad read; routes left as they are")
    # A customer taken over on the site: its new account takes the stop.
    replaced = replaced_map(db)
    wanted = take_over_stops(wanted, replaced)

    routes = route_rows(snap)
    db.upsert("routes", routes)
    jamaal_ids = {r["id"] for r in routes}
    customers = {r["id"]: r["name"] for r in db.select("customers", "select=id,name")}
    waiting = [s for s in wanted if s["customer_id"] not in customers]
    wanted = [s for s in wanted if s["customer_id"] in customers]
    before = [s for s in db.select("route_stops", "select=id,route_id,customer_id,position,must_get")
              if s["route_id"] in jamaal_ids]
    names = dict(customers)
    for r in snap.get("routes", []):
        for s in r["stops"]:
            names.setdefault(s.get("customer_id"), s["name"])
    differed = route_differences(snap_taken_over(snap, replaced), before, names)
    keep = {(s["route_id"], s["customer_id"]) for s in wanted}
    stale = sorted(s["id"] for s in before if (s["route_id"], s["customer_id"]) not in keep)
    if wanted:
        db.upsert("route_stops", wanted, on_conflict="route_id,customer_id")
    if stale:
        db.delete("route_stops", "id", stale)

    on_routes = len({s["route_id"] for s in wanted})
    summary = (f"routes set to the source site's: {len(wanted)} stop(s) on {on_routes} route(s); "
               f"{len(differed)} route(s) had differed, {len(stale)} stop(s) removed")
    if waiting:
        summary += f"; {len(waiting)} stop(s) wait for the nightly to add their customer"
    report = {"routes_read_at": snap.get("scraped_at"), "stops": len(wanted), "removed": len(stale),
              "differed_before": differed,
              "waiting": [{"id": s["customer_id"], "name": names.get(s["customer_id"], f"#{s['customer_id']}"),
                           "route_id": s["route_id"]} for s in waiting]}
    db.insert("sync_reports", [{"kind": "routes", "summary": summary, "report": report}])
    print(summary[0].upper() + summary[1:] + ".")
    for r in differed:
        print(f"  {r['route']}: {len(r['only_jamaal'])} added, {len(r['only_site'])} removed"
              + (", order reset" if r["order_differs"] else "")
              + (f", Must Get reset on {len(r['must_get_differs'])}" if r["must_get_differs"] else ""))
    for w in report["waiting"]:
        print(f"  waiting: {w['name']} ({w['id']}) on route {w['route_id']}")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", action="store_true", help="also copy the source site's routes into an empty route_stops")
    ap.add_argument("--dry-run", action="store_true", help="read the database, write nothing")
    ap.add_argument("--routes", action="store_true",
                    help="set the site's routes to the source site's Today's Routes, and nothing else")
    ap.add_argument("--history", action="store_true", help="re-copy the source site's whole history, not just the last 90 days")
    ap.add_argument("--export-details", action="store_true",
                    help=f"write {DETAILS_FILE} and {LOCAL_NOTES} from the database, and nothing else")
    ap.add_argument("--sweep", action="store_true",
                    help=f"apply {SWEEP_FILE} (address and notes edits on the source site), and nothing else")
    ap.add_argument("--export-oil-left", action="store_true",
                    help=f"write {OIL_LEFT_FILE} from the database, and nothing else")
    ap.add_argument("--export-thefts", action="store_true",
                    help=f"write {THEFTS_FILE} (thefts marked on the site) from the database, and nothing else")
    ap.add_argument("--export-site-edits", action="store_true",
                    help=f"write {EDITS_FILE} (names, towns, capacities edited on the site), and nothing else")
    ap.add_argument("--export-collections", action="store_true",
                    help=f"replace the entries from --since on in {RAW_FILE} and {NON_PICKUP_FILE} "
                         "with the site's, and nothing else")
    ap.add_argument("--since", help="--export-collections: the first day taken from the site (YYYY-MM-DD)")
    ap.add_argument("--place-pins", action="store_true",
                    help="move the map pin of each address edited on the site, and nothing else")
    args = ap.parse_args()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        print("SUPABASE_SERVICE_KEY is not set: nothing synced", file=sys.stderr)
        return 1
    try:
        db = Db(SUPABASE_URL, key, dry_run=args.dry_run)
        if args.export_details:
            export_details(db)
        elif args.export_oil_left:
            export_oil_left(db)
        elif args.export_thefts:
            export_thefts(db)
        elif args.export_site_edits:
            export_site_edits(db)
        elif args.export_collections:
            export_collections(db, args.since)
        elif args.place_pins:
            place_pins(db)
        elif args.routes:
            mirror_routes(db)
        elif args.sweep:
            sweep_details(db)
        else:
            sync(db, seed=args.seed, history=args.history)
            try:
                clear_lapsed_statuses(db)
            except SyncError as exc:   # before db/schema.sql adds site_status
                print(f"Site statuses not checked: {exc}")
    except (SyncError, requests.RequestException) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
