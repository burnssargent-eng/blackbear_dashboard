#!/usr/bin/env python3
"""
Keep the shared database (Supabase, db/schema.sql) in step with the source site.

The site and the source site are both sources (Sarge, 2026-10-21): routes are
copied in once, then edited on the site; gallons are entered in both. So this
script never overwrites a route or a pickup. It:

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

Addresses and notes live only in the database (step 6, 2026-10-24):
customer_details.json is no longer committed. --export-details writes it, and
customer_notes.local.json, from the database at the start of the nightly, so
jamaal_customers.py reads only customers new to the database and the sync
writes everyone's address back. Without that file the sync refuses to run,
rather than blank every address.

Oil Left (gallons a driver saw left in a barrel and did not collect) is
entered only on the site. --export-oil-left writes it to oil_left.csv
(customer id, day, gallons: nothing private), which is committed so the
projection builder can read it; the builder never contacts the database.

    python3 supabase_sync.py --seed   # first fill (run after jamaal_customers.py --refresh)
    python3 supabase_sync.py --export-details   # nightly, first: the details files from the database
    python3 supabase_sync.py --export-oil-left  # nightly, before the projections: oil_left.csv
    python3 supabase_sync.py          # nightly
    python3 supabase_sync.py --history  # also re-copy the source site's whole history
    python3 supabase_sync.py --dry-run  # print what would be sent, touch nothing

Needs SUPABASE_SERVICE_KEY (a secret key: the nightly reads it from GitHub
secrets). Reads only committed files plus the two details files; it never
contacts the source site itself.
"""

import argparse
import csv
import json
import os
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
OIL_LEFT_FILE = "oil_left.csv"
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


def full_notes(cid, details, local_notes):
    """A customer's notes with codes and phones, when this machine has them."""
    return local_notes.get(str(cid)) or details.get(str(cid), {}).get("notes") or None


def stop_rows(snap):
    """The source site's assigned routes as route_stops rows (seed only)."""
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


def summary_text(routes, pickups):
    parts = [f"{len(routes)} route(s) differ from the source site" if routes else "routes match the source site"]
    n = {k: len(v) for k, v in pickups.items()}
    if any(n.values()):
        parts.append(f"pickups: {n['only_site']} only on the site, {n['only_jamaal']} only on the source site, "
                     f"{n['gallons_differ']} with different gallons")
    else:
        parts.append("pickups match")
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
    names.update({cid: r["name"] for cid, r in wanted.items()})
    existing = {r["id"]: r for r in db.select("customers", "select=id,active,name")}
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
    routes = route_differences(snap, db_stops, names)
    summary = summary_text(routes, pickups)
    report = {"routes_read_at": snap.get("scraped_at"), "pickups_since": since, "pickups_through": through,
              "routes": routes, "pickups": pickups,
              "customers": {"with_notes": len(new), "without_notes": len(known), "marked_inactive": len(gone)},
              "stops_seeded": seeded, "history": copied}
    db.insert("sync_reports", [{"kind": "seed" if seed else "nightly", "summary": summary, "report": report}])
    print(f"Customers: {len(new)} with notes, {len(known)} without, {len(gone)} marked inactive. "
          f"Routes: {len(route_rows(snap))}. Stops seeded: {seeded}. "
          f"Source-site history ({copied['collections']['since']}): {copied['collections']['rows']} pickups and codes, "
          f"{copied['outgoing']['rows']} outgoing, {copied['collections']['removed'] + copied['outgoing']['removed']} "
          f"removed, {copied['customers_added']} customers added. {summary}.")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", action="store_true", help="also copy the source site's routes into an empty route_stops")
    ap.add_argument("--dry-run", action="store_true", help="read the database, write nothing")
    ap.add_argument("--history", action="store_true", help="re-copy the source site's whole history, not just the last 90 days")
    ap.add_argument("--export-details", action="store_true",
                    help=f"write {DETAILS_FILE} and {LOCAL_NOTES} from the database, and nothing else")
    ap.add_argument("--export-oil-left", action="store_true",
                    help=f"write {OIL_LEFT_FILE} from the database, and nothing else")
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
        else:
            sync(db, seed=args.seed, history=args.history)
    except (SyncError, requests.RequestException) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
