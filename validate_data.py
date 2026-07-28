#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data contract validator (Phase 2).

The dashboard is a static site: index.html reads oil_data.json and
vermont_towns.geojson straight off disk. Nothing at runtime checks that those
files actually agree with each other, so a bad export can look completely
normal in the browser while quietly showing wrong numbers.

This script is that check. It answers, in plain language:

  * Are the data files readable and shaped the way the page expects?
  * How fresh is the data?
  * Do the summary totals actually add up to each other?
  * Will every town in the data color in on the map?
  * Is the page still loading static files, and is it loading FRESH ones?

How to read the output
----------------------
  PASS  this is fine
  WARN  not broken, but worth knowing about
  FAIL  this will show wrong or missing data on the site
  INFO  a number for context; nothing to judge

Run:
    python3 validate_data.py

Exit code is 0 unless there is at least one FAIL, so WARNs never break CI.

Related: check_town_mismatches.py goes deeper on town-name normalization
specifically. This script is the broader whole-contract check.
"""

import collections
import json
import os
import re
import sys

from check_town_mismatches import load_geojson_towns
from oil_scraper import COUNTY_MAP, OUT_OF_STATE_CITIES, REGION_NAMES

DATA_PATH = "oil_data.json"
GEOJSON_PATH = "vermont_towns.geojson"
INDEX_PATH = "index.html"
CSV_PATH = "oil_collections_raw.csv"

# How stale the newest collection date can get before we mention it.
STALE_AFTER_DAYS = 21


# ─────────────────────────────────────────────
# Tiny reporting helpers
# ─────────────────────────────────────────────

class Report:
    """Collects PASS/WARN/FAIL lines and remembers the worst outcome."""

    def __init__(self):
        self.passes = 0
        self.warns = 0
        self.fails = 0

    def section(self, number, title):
        print(f"\n{'─' * 74}")
        print(f"{number}. {title}")
        print("─" * 74)

    def ok(self, message):
        self.passes += 1
        print(f"  PASS  {message}")

    def warn(self, message):
        self.warns += 1
        print(f"  WARN  {message}")

    def fail(self, message):
        self.fails += 1
        print(f"  FAIL  {message}")

    def info(self, message):
        print(f"  INFO  {message}")

    def detail(self, message):
        print(f"        {message}")


def gallons_of(rows):
    """Sum the 'gallons' field over a list of dict rows."""
    return sum(row.get("gallons", 0) for row in rows)


def check_numeric(report, label, rows, fields):
    """Every named field must be a real number, not a string."""
    bad = []
    for row in rows[:2000]:          # a sample is enough to catch a type bug
        for field in fields:
            value = row.get(field)
            if value is not None and not isinstance(value, (int, float)):
                bad.append((field, value))
    if bad:
        field, value = bad[0]
        report.fail(
            f"{label}: {len(bad)} value(s) are not numbers "
            f"(e.g. {field}={value!r}, type {type(value).__name__})."
        )
        report.detail("A numpy type slipping through json.dump causes this.")
    else:
        report.ok(f"{label}: numeric fields are numbers, not strings.")


# ─────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────

def check_1_json_valid(report):
    report.section(1, "oil_data.json is valid JSON")

    if not os.path.exists(DATA_PATH):
        report.fail(f"{DATA_PATH} does not exist. Run: python3 oil_scraper.py")
        return None

    try:
        with open(DATA_PATH) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        report.fail(f"{DATA_PATH} is not valid JSON: {exc}")
        return None

    size_mb = os.path.getsize(DATA_PATH) / 1_000_000
    report.ok(f"{DATA_PATH} parsed cleanly ({size_mb:.2f} MB, "
              f"{len(data)} top-level fields).")

    # The page breaks outright without these.
    required = [
        "last_updated", "monthly_by_town", "monthly_by_county",
        "yearly_totals", "all_time_total", "current_year_total",
        "customer_count",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        report.fail(f"index.html needs these missing field(s): {missing}")
    else:
        report.ok("All fields index.html depends on are present.")

    # Added in Phase 2 so future pages can be built from this file.
    added = [
        "monthly_totals", "collections_file", "monthly_by_region",
        "yearly_by_region", "region_names",
    ]
    absent = [k for k in added if k not in data]
    if absent:
        report.warn(f"Phase 2 field(s) not exported yet: {absent}")
    else:
        report.ok(f"All {len(added)} Phase 2 contract fields are present.")

    return data


def check_2_geojson(report):
    report.section(2, "vermont_towns.geojson is a valid FeatureCollection")

    if not os.path.exists(GEOJSON_PATH):
        report.fail(f"{GEOJSON_PATH} does not exist.")
        return set()

    try:
        with open(GEOJSON_PATH) as f:
            geo = json.load(f)
    except json.JSONDecodeError as exc:
        report.fail(f"{GEOJSON_PATH} is not valid JSON: {exc}")
        return set()

    if geo.get("type") != "FeatureCollection":
        report.fail(f"type is {geo.get('type')!r}, expected 'FeatureCollection'.")
        return set()

    features = geo.get("features") or []
    if not features:
        report.fail("FeatureCollection contains no features.")
        return set()

    towns = load_geojson_towns(GEOJSON_PATH)
    report.ok(f"Valid FeatureCollection with {len(features)} features.")

    # index.html joins on TOWNNAMEMC, so that property specifically must exist.
    without_name = sum(
        1 for f in features if not f.get("properties", {}).get("TOWNNAMEMC")
    )
    if without_name:
        report.fail(f"{without_name} feature(s) have no TOWNNAMEMC property; "
                    "index.html joins town names on that property.")
    else:
        report.ok(f"All features have TOWNNAMEMC ({len(towns)} distinct names).")

    return towns


def load_collections_payload(data):
    """Read the whole detail file without reporting anything. Returns {} on any
    problem; check 5 is what actually reports on it."""
    path = data.get("collections_file")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def load_collections(data):
    return load_collections_payload(data).get("records", [])


def check_3_latest_date(report, data, coll):
    report.section(3, "Latest collection date")

    dates = []
    if coll:
        dates = [r["date"] for r in coll if r.get("date")]
    elif os.path.exists(CSV_PATH):
        import csv
        with open(CSV_PATH) as f:
            dates = [r["date"] for r in csv.DictReader(f) if r.get("date")]

    if not dates:
        report.warn("No collection dates available to check.")
        return

    latest = max(dates)[:10]
    report.info(f"Latest collection date: {latest}")

    from datetime import date
    try:
        y, m, d = (int(x) for x in latest.split("-"))
        age = (date.today() - date(y, m, d)).days
    except ValueError:
        report.warn(f"Could not parse {latest!r} as a date.")
        return

    if age > STALE_AFTER_DAYS:
        report.warn(f"Newest record is {age} days old "
                    f"(over the {STALE_AFTER_DAYS}-day threshold). "
                    "Has the nightly scrape been running?")
    else:
        report.ok(f"Data is current — newest record is {age} day(s) old.")

    if data.get("last_updated"):
        report.info(f"oil_data.json last written: {data['last_updated'][:19]}")


def check_4_latest_month(report, data):
    report.section(4, "Latest month and month total")

    monthly = data.get("monthly_totals")
    if not monthly:
        report.warn("monthly_totals is not present — cannot report a "
                    "month total directly from the contract.")
        return

    check_numeric(report, "monthly_totals", monthly, ["gallons"])

    latest = max(monthly, key=lambda r: r["month"])
    report.info(f"Latest month: {latest['month']}  "
                f"{latest['gallons']:,} gallons")
    report.info(f"{len(monthly)} months of history "
                f"({min(r['month'] for r in monthly)} to {latest['month']}).")


def check_5_collections_rows(report, data):
    report.section(5, "collections detail records")

    path = data.get("collections_file")
    if not path:
        report.warn("No collections_file field — per-record detail is not "
                    "exported. Future detail pages would have no source.")
        return None

    if not os.path.exists(path):
        report.fail(f"collections_file points to {path!r}, which does not "
                    "exist. Run: python3 oil_scraper.py")
        return None

    try:
        with open(path) as f:
            payload = json.load(f)
    except json.JSONDecodeError as exc:
        report.fail(f"{path} is not valid JSON: {exc}")
        return None

    rows = payload.get("records", [])
    size_mb = os.path.getsize(path) / 1_000_000
    report.ok(f"{path}: {len(rows):,} records ({size_mb:.1f} MB).")

    declared = data.get("collections_count")
    if declared is not None and declared != len(rows):
        report.fail(f"collections_count says {declared:,} but {path} holds "
                    f"{len(rows):,} records.")
    elif declared is not None:
        report.ok(f"collections_count matches the file ({declared:,}).")

    check_numeric(report, "collections", rows, ["gallons", "year", "customer_id"])
    return rows


def check_6_monthly_totals_match(report, data, coll):
    report.section(6, "monthly_totals equals the sum of collections")

    monthly = data.get("monthly_totals")
    if not monthly or not coll:
        report.warn("Skipped — needs both monthly_totals and collections.")
        return

    from_coll = collections.Counter()
    for row in coll:
        from_coll[row["month"]] += row["gallons"]

    from_json = {row["month"]: row["gallons"] for row in monthly}

    mismatches = []
    for month in sorted(set(from_coll) | set(from_json)):
        a, b = from_json.get(month, 0), from_coll.get(month, 0)
        if a != b:
            mismatches.append((month, a, b))

    if mismatches:
        report.fail(f"{len(mismatches)} month(s) disagree between "
                    "monthly_totals and collections:")
        for month, a, b in mismatches[:10]:
            report.detail(f"{month}  monthly_totals={a:,}  collections={b:,}")
        if len(mismatches) > 10:
            report.detail(f"... and {len(mismatches) - 10} more")
    else:
        report.ok(f"All {len(from_json)} months agree exactly "
                  f"({sum(from_json.values()):,} gallons).")


def check_7_town_names(report, data, geo_towns):
    report.section(7, "monthly_by_town names match GeoJSON TOWNNAMEMC")

    rows = data.get("monthly_by_town") or []
    if not rows:
        report.fail("monthly_by_town is empty — the map will be blank.")
        return

    check_numeric(report, "monthly_by_town", rows, ["gallons"])

    names = {row.get("geo_town") or row.get("city") for row in rows}
    names.discard(None)
    names.discard("")

    unmatched = sorted(n for n in names if n not in geo_towns)
    if unmatched:
        report.fail(f"{len(unmatched)} town name(s) match no polygon and will "
                    "silently draw as zero on the map:")
        for name in unmatched:
            lost = gallons_of([r for r in rows
                               if (r.get("geo_town") or r.get("city")) == name])
            report.detail(f"{name!r:<30} {lost:>9,} gal at stake")
    else:
        report.ok(f"All {len(names)} town names in monthly_by_town match "
                  "a Vermont polygon.")

    report.info(f"{len(names)} of {len(geo_towns)} Vermont towns have data.")


def check_8_remaining_mismatches(report, data, geo_towns, coll):
    report.section(8, "Remaining town mismatches and out-of-state handling")

    unmapped = data.get("unmapped_total")
    if unmapped is not None:
        report.info(f"unmapped_total (kept in raw data, off the map): "
                    f"{unmapped:,} gallons")

    oos = data.get("out_of_state_total")
    if oos is not None:
        report.info(f"out_of_state_total (non-Vermont counties): {oos:,} gallons")

    if not coll:
        report.warn("Skipped per-record mismatch scan — collections not available.")
        return

    # An empty geo_town is expected for genuinely out-of-state places, and a bug
    # for anything else. Separate the two so real problems are not buried.
    expected_blank = collections.Counter()
    unexpected_blank = collections.Counter()
    wrong_name = collections.Counter()

    for row in coll:
        town = (row.get("geo_town") or "").strip()
        city = row.get("city") or ""
        gallons = row.get("gallons", 0)

        if not town:
            if city in OUT_OF_STATE_CITIES:
                expected_blank[city] += gallons
            else:
                unexpected_blank[city] += gallons
        elif town not in geo_towns:
            wrong_name[town] += gallons

    if expected_blank:
        report.ok(f"{len(expected_blank)} known out-of-state place(s) correctly "
                  f"excluded from the map "
                  f"({sum(expected_blank.values()):,} gallons):")
        for city, gal in expected_blank.most_common():
            report.detail(f"{city!r:<22} {gal:>9,} gal  "
                          f"({COUNTY_MAP.get(city, 'Unknown')})")

    if unexpected_blank:
        report.fail(f"{len(unexpected_blank)} place(s) have no geo_town but are "
                    "not on the known out-of-state list:")
        for city, gal in unexpected_blank.most_common(15):
            report.detail(f"{city!r:<22} {gal:>9,} gal")
    else:
        report.ok("Every record without a geo_town is a known out-of-state place.")

    if wrong_name:
        report.fail(f"{len(wrong_name)} geo_town value(s) match no polygon:")
        for town, gal in wrong_name.most_common(15):
            report.detail(f"{town!r:<22} {gal:>9,} gal")
    else:
        report.ok("Every non-empty geo_town matches an official Vermont town.")

    # A Vermont-mapped record sitting in an out-of-state county means the two
    # summaries on the page contradict each other.
    conflict = collections.Counter()
    for row in coll:
        if (row.get("geo_town") or "").strip() and \
                str(row.get("county", "")).startswith("Out-of-State"):
            conflict[(row["city"], row["county"], row["geo_town"])] += row["gallons"]
    if conflict:
        report.warn(f"{len(conflict)} place(s) are drawn on the Vermont map but "
                    "counted as out-of-state in the county summary:")
        for (city, county, town), gal in conflict.most_common():
            report.detail(f"{city!r} -> map:{town!r}  county:{county!r}  {gal:,} gal")
    else:
        report.ok("No record is mapped to Vermont while counted as out-of-state.")


def check_9_geo_town_present(report, coll):
    report.section(9, "collections include geo_town")

    if not coll:
        report.warn("Skipped — collections not available.")
        return

    if "geo_town" not in coll[0]:
        report.fail("collections records have no geo_town field; a detail page "
                    "could not roll places up to official towns.")
        return

    blank = sum(1 for r in coll if not (r.get("geo_town") or "").strip())
    report.ok(f"geo_town present on all {len(coll):,} records "
              f"({blank:,} intentionally blank for non-Vermont places).")

    if "city" in coll[0]:
        report.ok("Original raw 'city' value is preserved alongside geo_town.")
    else:
        report.warn("Raw 'city' value is not preserved in collections.")


def check_10_active_status(report, coll):
    report.section(10, "Source-site active/inactive status")

    if not coll:
        report.warn("Skipped — collections not available.")
        return

    if "is_active" not in coll[0]:
        report.warn("No is_active field. Source-site status is not captured yet.")
        report.detail("Next scrape will add it; see the note below.")
        return

    known = [r for r in coll if r.get("is_active") is not None]
    if not known:
        report.warn("is_active field exists but is null on every record.")
        report.detail("Status can only be captured during a live scrape. Run")
        report.detail("`python3 oil_scraper.py --rescrape`, or wait for the")
        report.detail("nightly job, to backfill it.")
        report.detail("Do NOT infer status from the last pickup date.")
        return

    active = sum(1 for r in known if r["is_active"])
    report.ok(f"Status captured on {len(known):,} of {len(coll):,} records.")
    report.info(f"{active:,} active / {len(known) - active:,} inactive records.")


def check_11_static_fetches(report, text):
    report.section(11, "Frontend loads static files, not Flask API routes")

    if text is None:
        report.fail(f"{INDEX_PATH} not found.")
        return []

    fetches = re.findall(r"""fetch\(\s*[`'"]([^`'"]+)[`'"]""", text)
    if not fetches:
        report.fail("No fetch() calls found in index.html.")
        return []

    api_like = [u for u in fetches
                if u.startswith(("/api", "http://", "https://")) or "/api/" in u]
    if api_like:
        report.fail("These look like server routes, not static files. "
                    "GitHub Pages cannot serve them:")
        for url in api_like:
            report.detail(url)
    else:
        report.ok(f"All {len(fetches)} fetch(es) use relative static paths.")

    for url in fetches:
        base = url.split("?")[0]
        exists = "found" if os.path.exists(base) else "MISSING"
        report.detail(f"{url:<40} -> {base} ({exists})")
        if not os.path.exists(base):
            report.fail(f"index.html fetches {base!r}, which is not in the repo.")

    return fetches


def check_12_cache_busting(report, fetches, text):
    report.section(12, "Frontend fetches are cache-busted")

    if not fetches:
        report.warn("Skipped — no fetch calls to inspect.")
        return

    plain = [u for u in fetches if "?" not in u]
    if plain:
        report.fail(f"{len(plain)} fetch(es) have no cache-busting query "
                    "string. The nightly job rewrites these same URLs, so "
                    "returning visitors can be served stale data:")
        for url in plain:
            report.detail(url)
    else:
        report.ok(f"All {len(fetches)} fetch(es) carry a query string.")

    # A hardcoded ?v=1 would pass the check above but never change, so confirm
    # the value is generated at page load.
    if text and "Date.now()" in text:
        report.ok("Cache-buster is generated at page load (Date.now()).")
    elif not plain:
        report.warn("Query strings look hardcoded — they will not change when "
                    "the nightly job rewrites the data files.")


def check_quantity_rule(report, coll):
    report.section("Q", "Counted-quantity rule")

    from oil_scraper import EMPTY_QTYS

    expected = {0, 1, 2, 3}
    if set(EMPTY_QTYS) == expected:
        report.ok(f"EMPTY_QTYS is {sorted(EMPTY_QTYS)} — unchanged.")
    else:
        report.fail(f"EMPTY_QTYS is {sorted(EMPTY_QTYS)}, expected {sorted(expected)}. "
                    "Counted gallons would move.")

    if 4 in EMPTY_QTYS:
        report.fail("4 is excluded. 4-gallon pickups must stay counted as 4.")
    else:
        report.ok("4 is NOT excluded — 4-gallon pickups stay counted as 4.")

    if not coll:
        report.warn("Skipped record scan — collections not available.")
        return

    smallest = min(r.get("gallons", 0) for r in coll)
    if smallest >= 4:
        report.ok(f"Lowest retained quantity is {smallest} — the retained records "
                  "are the qualifying-pickup set (gallons >= 4).")
    else:
        report.fail(f"Lowest retained quantity is {smallest}, below 4.")

    fours = sum(1 for r in coll if r.get("gallons") == 4)
    report.info(f"{fours} record(s) at exactly 4 gallons, counted as 4 (not rounded).")


def check_projection(report, data):
    report.section("P", "Current-year projection")

    proj = data.get("projection")
    if not proj:
        report.warn("No projection exported. The homepage will show "
                    "'Projection unavailable'.")
        return

    required = ["latest_data_date", "current_ytd", "previous_year_full",
                "previous_through_prior_month", "previous_same_month_total",
                "previous_prorated_ytd", "projected_current_year",
                "percent_vs_previous_year"]
    missing = [k for k in required if k not in proj]
    if missing:
        report.fail(f"Projection is missing field(s): {missing}")
        return
    report.ok("All projection fields present.")

    # Recompute independently from the stored inputs.
    import calendar as _cal
    day = int(proj["latest_data_date"][8:10])
    month = int(proj["latest_data_date"][5:7])
    dim = _cal.monthrange(int(proj["previous_year"]), month)[1]

    prorated = (proj["previous_through_prior_month"]
                + (day / dim) * proj["previous_same_month_total"])
    if abs(prorated - proj["previous_prorated_ytd"]) > 1:
        report.fail(f"previous_prorated_ytd is {proj['previous_prorated_ytd']:,}, "
                    f"recomputed {prorated:,.2f}")
    else:
        report.ok(f"previous_prorated_ytd checks out ({prorated:,.1f}).")

    projected = proj["previous_year_full"] / prorated * proj["current_ytd"]
    if abs(projected - proj["projected_current_year"]) > 1:
        report.fail(f"projected_current_year is {proj['projected_current_year']:,}, "
                    f"recomputed {projected:,.0f}")
    else:
        report.ok(f"projected_current_year checks out "
                  f"({proj['projected_current_year']:,}).")

    pct = proj["percent_vs_previous_year"]
    report.info(f"Projected {proj['projected_current_year']:,} for "
                f"{proj['current_year']} — {pct * 100:+.1f}% vs "
                f"{proj['previous_year']} ({proj['previous_year_full']:,}).")
    report.info(f"Anchored to latest data date {proj['latest_data_date']}, "
                "not today's date.")


def check_active_customers(report, data, coll):
    report.section("A", "Source-site active customers")

    count = data.get("active_customer_count")
    if count is None:
        report.warn("active_customer_count is null — status not captured yet. "
                    "The card will say so rather than show a wrong number.")
    else:
        report.ok(f"active_customer_count = {count:,} of "
                  f"{data.get('customer_count', 0):,} total.")

        if coll:
            status = {}
            for r in coll:
                status[r["customer_id"]] = r.get("is_active")
            recomputed = sum(1 for v in status.values() if v is True)
            if recomputed != count:
                report.fail(f"Recomputed active count is {recomputed:,}, "
                            f"export says {count:,}.")
            else:
                report.ok("Recomputed from collections and it matches.")

    stats = data.get("region_stats")
    if not stats:
        report.warn("No region_stats exported — the region page will show "
                    "'Active unavailable'.")
        return

    report.ok(f"region_stats present for {len(stats)} region(s).")
    report.info("Regions are not exclusive, so these counts do not sum to the "
                "company total.")


def check_lifecycle(report, data, coll_payload):
    report.section("L", "Customer lifecycle")

    rows = data.get("lifecycle_by_year")
    if not rows:
        report.warn("No lifecycle_by_year exported — the customers page will "
                    "say lifecycle data is unavailable.")
        return

    cutoff = data.get("dormant_cutoff")
    report.ok(f"lifecycle_by_year present for {len(rows)} year(s).")
    report.info(f"Dormant cutoff: {cutoff}")

    customers = (coll_payload or {}).get("customers") or []
    if not customers:
        report.warn("Per-customer lifecycle not present in the collections file.")
        return
    report.ok(f"Per-customer lifecycle present for {len(customers):,} customer(s).")

    # No impossible values.
    bad = []
    for r in rows:
        for field in ("places_serviced", "gained", "lost"):
            v = r.get(field)
            if not isinstance(v, int) or v < 0:
                bad.append((r.get("year"), field, v))
        if r.get("net") != r.get("gained", 0) - r.get("lost", 0):
            bad.append((r.get("year"), "net", r.get("net")))
    if bad:
        report.fail(f"{len(bad)} impossible lifecycle value(s):")
        for y, f, v in bad[:10]:
            report.detail(f"{y} {f} = {v!r}")
    else:
        report.ok("All counts are non-negative integers and Net = Gained - Lost.")

    # Gained/lost years must match the customer's own first/last dates.
    mismatched_gain = [c for c in customers
                       if c.get("gained_year") != int(str(c.get("first_qualifying_pickup"))[:4])]
    mismatched_lost = [c for c in customers
                       if c.get("lost_year") is not None
                       and c["lost_year"] != int(str(c.get("last_qualifying_pickup"))[:4])]
    if mismatched_gain:
        report.fail(f"{len(mismatched_gain)} customer(s) whose gained_year is not "
                    "the year of their first qualifying pickup.")
    else:
        report.ok("Every gained_year matches the first qualifying pickup.")
    if mismatched_lost:
        report.fail(f"{len(mismatched_lost)} customer(s) whose lost_year is not "
                    "the year of their last qualifying pickup.")
    else:
        report.ok("Every lost_year matches the last qualifying pickup.")

    # Lost membership must equal inactive + dormant-active.
    inactive = [c for c in customers if c.get("is_active") is False]
    dormant = [c for c in customers
               if c.get("is_active") is True
               and str(c.get("last_qualifying_pickup", "")) < str(cutoff)]
    flagged = [c for c in customers if c.get("lost_year") is not None]

    if len(flagged) != len(inactive) + len(dormant):
        report.fail(f"{len(flagged)} customers flagged lost, but inactive "
                    f"({len(inactive)}) + dormant-active ({len(dormant)}) = "
                    f"{len(inactive) + len(dormant)}.")
    else:
        report.ok(f"{len(flagged)} lost = {len(inactive)} source-site inactive "
                  f"+ {len(dormant)} dormant active-list.")

    total_gained = sum(r["gained"] for r in rows)
    total_lost = sum(r["lost"] for r in rows)
    if total_gained != len(customers):
        report.fail(f"Gained totals {total_gained} but there are "
                    f"{len(customers)} customers; every customer is gained once.")
    else:
        report.ok(f"Gained sums to {total_gained:,}, one per customer.")
    report.info(f"Lost sums to {total_lost:,}. Net across all years: "
                f"{total_gained - total_lost:+,}")

    first_year = rows[0]["year"]
    report.info(f"{first_year} shows {rows[0]['gained']} gained because it is the "
                "first year of data, not that many genuinely new customers.")


def check_region_customers(report, data, coll_payload):
    report.section("RC", "Region customer membership")

    membership = data.get("region_customers")
    if not membership:
        report.warn("No region_customers exported — the region page will show "
                    "no customer table.")
        return

    report.ok(f"region_customers present for {len(membership)} region(s).")

    customers = {c["customer_id"]: c for c in (coll_payload or {}).get("customers") or []}
    if not customers:
        report.warn("Skipped reconciliation — per-customer lifecycle unavailable.")
        return

    monthly = data.get("monthly_by_region") or []
    region_totals = {}
    for r in monthly:
        region_totals[r["region"]] = region_totals.get(r["region"], 0) + r["gallons"]

    mismatches = []
    for region, ids in membership.items():
        derived = sum(customers[i]["gallons"] for i in ids if i in customers)
        expected = region_totals.get(region, 0)
        if derived != expected:
            mismatches.append((region, derived, expected))

    if mismatches:
        report.fail(f"{len(mismatches)} region(s) whose member gallons do not "
                    "match the aggregate export:")
        for region, d, e in mismatches[:10]:
            report.detail(f"{region}: members {d:,} vs monthly_by_region {e:,}")
    else:
        report.ok("Every region's member gallons reconcile exactly with "
                  "monthly_by_region.")

    unknown = {r: [i for i in ids if i not in customers]
               for r, ids in membership.items()}
    stray = sum(len(v) for v in unknown.values())
    if stray:
        report.fail(f"{stray} region member id(s) have no customer record.")
    else:
        report.ok("Every region member id resolves to a known customer.")


def check_schmootz(report):
    report.section("S", "Schmootz displacement data")

    path = "schmootz_data.json"
    if not os.path.exists(path):
        report.warn(f"{path} not found. The Schmootz page will say the data is "
                    "unavailable. Run: python3 export_schmootz.py")
        return

    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        report.fail(f"{path} is not valid JSON: {exc}")
        return

    size_kb = os.path.getsize(path) / 1000
    report.ok(f"{path} parsed cleanly ({size_kb:.1f} KB).")

    # These come from the Phase 4A audit of the workbook and must not drift.
    expected = {
        "total_barr_hill_to_gebbie": 2_134_668,
        "total_bbb_shop_to_gebbie": 133_475,
    }
    for field, want in expected.items():
        got = data.get(field)
        if got == want:
            report.ok(f"{field} = {want:,} — matches the workbook audit.")
        else:
            report.fail(f"{field} is {got!r}, expected {want:,}. "
                        "The workbook or the exporter has changed.")

    combined = data.get("combined_total")
    parts = sum(data.get(f) or 0 for f in expected)
    if combined == parts:
        report.ok(f"combined_total = {combined:,} = the sum of both sources.")
    else:
        report.fail(f"combined_total is {combined!r}, but the sources sum to {parts:,}.")

    # Per-block internal consistency.
    for block in data.get("blocks", []):
        by_year = sum(e["gallons"] for e in block.get("by_year", []))
        monthly = sum(m["gallons"] for m in block.get("monthly", []))
        if by_year == monthly == block.get("total_gallons"):
            report.ok(f"{block['label']}: yearly, monthly and total all agree "
                      f"({monthly:,}).")
        else:
            report.fail(f"{block['label']} disagrees — total "
                        f"{block.get('total_gallons'):,}, by_year {by_year:,}, "
                        f"monthly {monthly:,}.")

    # Every month key must parse.
    bad_months = []
    for block in data.get("blocks", []):
        for m in block.get("monthly", []):
            text = str(m.get("month", ""))
            if len(text) != 7 or text[4] != "-" or not text[:4].isdigit() \
                    or not text[5:].isdigit() or not 1 <= int(text[5:]) <= 12:
                bad_months.append(text)
    if bad_months:
        report.fail(f"{len(bad_months)} unparseable month key(s), e.g. {bad_months[0]!r}")
    else:
        report.ok("Every month key parses as YYYY-MM.")

    # Nothing null-like should reach the page as a displayed value.
    raw = open(path).read()
    for token in ("NaN", "Infinity", '"null"', '"undefined"', '"-"'):
        if token in raw:
            report.fail(f"{path} contains {token}, which could render as text.")
            break
    else:
        report.ok("No NaN, Infinity or null-like strings in the export.")

    report.info(f"Generated {data.get('generated_at', '—')} from "
                f"{data.get('source_file', '—')} · sheet "
                f"{data.get('source_sheet', '—')}.")
    report.info("Schmootz is site-to-site displacement and is deliberately NOT "
                "part of any oil collection total.")


def check_schmootz_isolation(report, data):
    """Schmootz gallons must never leak into the collection totals."""
    leaked = [k for k in data if "schmootz" in k.lower()]
    if leaked:
        report.fail(f"oil_data.json contains Schmootz field(s): {leaked}. "
                    "Schmootz must stay separate from collection totals.")
    else:
        report.ok("oil_data.json contains no Schmootz fields — kept separate.")


def check_reconciliation(report, data):
    report.section("R", "Totals reconcile against all_time_total")

    total = data.get("all_time_total")
    if total is None:
        report.fail("all_time_total is missing; nothing to reconcile against.")
        return

    report.info(f"all_time_total: {total:,} gallons")

    def compare(label, value, expected=None, note=""):
        expected = total if expected is None else expected
        if value == expected:
            report.ok(f"{label} = {value:,}  (matches){note}")
        else:
            report.fail(f"{label} = {value:,}, expected {expected:,} "
                        f"(off by {value - expected:+,}){note}")

    if data.get("monthly_totals"):
        compare("sum(monthly_totals)", gallons_of(data["monthly_totals"]))

    if data.get("monthly_by_county"):
        compare("sum(monthly_by_county)", gallons_of(data["monthly_by_county"]))

    if data.get("yearly_totals"):
        compare("sum(yearly_totals)", gallons_of(data["yearly_totals"]))

    if data.get("monthly_by_town") is not None and "unmapped_total" in data:
        mapped = gallons_of(data["monthly_by_town"])
        compare("sum(monthly_by_town) + unmapped_total",
                mapped + data["unmapped_total"],
                note="  [normalization must not lose gallons]")
        report.info(f"On the Vermont map: {mapped:,} gallons "
                    f"({mapped / total * 100:.1f}% of all gallons)")


def check_regions(report, data):
    report.section("R2", "Region summaries (context only)")

    names = data.get("region_names")
    monthly = data.get("monthly_by_region")
    yearly = data.get("yearly_by_region")

    if not names or not monthly:
        report.warn("Region exports not present.")
        return

    report.ok(f"{len(names)} named regions exported: {', '.join(names)}")
    check_numeric(report, "monthly_by_region", monthly, ["gallons"])

    m_total = gallons_of(monthly)
    y_total = gallons_of(yearly or [])
    if yearly and m_total != y_total:
        report.fail(f"monthly_by_region ({m_total:,}) and yearly_by_region "
                    f"({y_total:,}) disagree.")
    elif yearly:
        report.ok(f"monthly_by_region and yearly_by_region agree ({m_total:,}).")

    all_time = data.get("all_time_total", 0)
    overlap = m_total - all_time
    report.info(f"Region gallons total {m_total:,} vs all_time_total "
                f"{all_time:,} (difference {overlap:+,}).")
    if overlap:
        report.info("That difference is EXPECTED, not an error: regions are not "
                    "exclusive, so a record matching two regions is counted in "
                    "both.")
    else:
        report.info("No record currently matches more than one region, so the "
                    "two happen to agree. Regions are still not exclusive by "
                    "design, so this can change.")
    report.info("Do not 'fix' this by forcing one region per record — that "
                "would change the Excel report.")

    labels = {r["region"] for r in monthly}
    unexpected = sorted(labels - set(names) - {"Other"})
    if unexpected:
        report.fail(f"Unexpected region label(s): {unexpected}")
    else:
        report.ok("All region labels are a known region or 'Other'.")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    print("=" * 74)
    print("BLACK BEAR DASHBOARD — DATA VALIDATION")
    print("=" * 74)
    print("  PASS = fine   WARN = worth knowing   FAIL = wrong data on the site")

    report = Report()

    data = check_1_json_valid(report)
    geo_towns = check_2_geojson(report)

    if data is None:
        print("\nCannot continue without oil_data.json.")
        return 1

    coll = load_collections(data)

    check_3_latest_date(report, data, coll)
    check_4_latest_month(report, data)
    coll = check_5_collections_rows(report, data) or []
    check_6_monthly_totals_match(report, data, coll)
    check_7_town_names(report, data, geo_towns)
    check_8_remaining_mismatches(report, data, geo_towns, coll)
    check_9_geo_town_present(report, coll)
    check_10_active_status(report, coll)

    text = None
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH) as f:
            text = f.read()
    fetches = check_11_static_fetches(report, text)
    check_12_cache_busting(report, fetches, text)

    coll_payload = load_collections_payload(data)

    check_quantity_rule(report, coll)
    check_projection(report, data)
    check_active_customers(report, data, coll)
    check_lifecycle(report, data, coll_payload)
    check_region_customers(report, data, coll_payload)
    check_schmootz(report)
    check_schmootz_isolation(report, data)
    check_reconciliation(report, data)
    check_regions(report, data)

    print(f"\n{'=' * 74}")
    print("SUMMARY")
    print("=" * 74)
    print(f"  PASS: {report.passes}")
    print(f"  WARN: {report.warns}")
    print(f"  FAIL: {report.fails}")

    if report.fails:
        print("\n  RESULT: FAILED — fix the FAIL lines above.")
        return 1
    if report.warns:
        print("\n  RESULT: PASSED WITH WARNINGS — nothing is broken.")
        return 0
    print("\n  RESULT: ALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
