#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Town-name mismatch checker (Phase 1).

The heatmap in index.html colors a town only when the town name in our data
exactly matches a town name in vermont_towns.geojson. Any name that does not
match is silently drawn as zero, which quietly hides real gallons.

This script reports:
  * Vermont places whose normalized name still does not match the GeoJSON
    (these are BUGS — they will not color on the map)
  * out-of-state places, listed separately and NOT treated as errors
  * whether every target in GEO_TOWN_NAME_MAP actually exists in the GeoJSON
  * whether the town names in oil_data.json match the GeoJSON

Run:
    python check_town_mismatches.py

Exits non-zero if a real mismatch is found, so it can be wired into CI later.
"""

import collections
import csv
import json
import os
import sys

from oil_scraper import (
    COUNTY_MAP,
    GEO_TOWN_NAME_MAP,
    OUT_OF_STATE_CITIES,
    normalize_geo_town,
)

CSV_PATH = "oil_collections_raw.csv"
GEOJSON_PATH = "vermont_towns.geojson"
JSON_PATH = "oil_data.json"


def load_geojson_towns(path):
    """Official town names, using TOWNNAMEMC with TOWNNAME as the fallback."""
    with open(path) as f:
        geo = json.load(f)

    if geo.get("type") != "FeatureCollection":
        raise SystemExit(f"ERROR: {path} is not a GeoJSON FeatureCollection.")

    names = set()
    for feature in geo["features"]:
        props = feature.get("properties", {})
        name = props.get("TOWNNAMEMC") or props.get("TOWNNAME")
        if name:
            names.add(name)
    return names


def heading(text):
    print(f"\n{'=' * 74}\n{text}\n{'=' * 74}")


def main():
    for path in (CSV_PATH, GEOJSON_PATH):
        if not os.path.exists(path):
            raise SystemExit(f"ERROR: missing required file: {path}")

    geo_towns = load_geojson_towns(GEOJSON_PATH)
    print(f"Loaded {len(geo_towns)} official town names from {GEOJSON_PATH}.")

    problems = 0

    # ── 1. Every mapping target must be a real GeoJSON town ──────────────────
    heading("1. GEO_TOWN_NAME_MAP target validity")
    bad_targets = sorted(
        {t for t in GEO_TOWN_NAME_MAP.values() if t not in geo_towns}
    )
    if bad_targets:
        problems += len(bad_targets)
        print("FAIL — these targets do not exist in the GeoJSON:")
        for t in bad_targets:
            print(f"   - {t!r}")
    else:
        print(f"OK — all {len(set(GEO_TOWN_NAME_MAP.values()))} targets exist in the GeoJSON.")

    # ── 2. Normalize every row of the raw CSV ────────────────────────────────
    matched = collections.Counter()
    unmatched = collections.Counter()
    out_of_state = collections.Counter()
    total_gallons = 0

    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            gallons = int(row["gallons"])
            total_gallons += gallons
            city = row["city"]

            if city in OUT_OF_STATE_CITIES:
                out_of_state[city] += gallons
                continue

            town = normalize_geo_town(city, row.get("customer_id"))
            if town and town in geo_towns:
                matched[town] += gallons
            else:
                unmatched[city] += gallons

    heading("2. Vermont towns that still do NOT match the GeoJSON")
    if unmatched:
        problems += len(unmatched)
        print(f"FAIL — {len(unmatched)} unmatched place(s), "
              f"{sum(unmatched.values()):,} gallons will not color on the map:\n")
        for city, gallons in unmatched.most_common():
            print(f"   {city!r:<28} {gallons:>9,} gal")
    else:
        print("OK — every Vermont place resolves to an official GeoJSON town.")

    heading("3. Out-of-state places (excluded from the Vermont map by design)")
    if out_of_state:
        for city, gallons in out_of_state.most_common():
            county = COUNTY_MAP.get(city, "Unknown")
            print(f"   {city!r:<20} {gallons:>9,} gal   ({county})")
        print(f"\n   Subtotal: {sum(out_of_state.values()):,} gallons "
              f"({sum(out_of_state.values()) / total_gallons * 100:.1f}% of all gallons)")
    else:
        print("   none found.")

    # ── 4. Coverage summary ──────────────────────────────────────────────────
    heading("4. Map coverage summary")
    mapped = sum(matched.values())
    print(f"   Total gallons in CSV      {total_gallons:>12,}")
    print(f"   Mapped to a VT town       {mapped:>12,}  ({mapped / total_gallons * 100:.1f}%)")
    print(f"   Out-of-state (expected)   {sum(out_of_state.values()):>12,}"
          f"  ({sum(out_of_state.values()) / total_gallons * 100:.1f}%)")
    print(f"   Unmatched (BUG)           {sum(unmatched.values()):>12,}"
          f"  ({sum(unmatched.values()) / total_gallons * 100:.1f}%)")
    print(f"   Distinct VT towns colored {len(matched):>12,}")

    # ── 5. oil_data.json agreement ───────────────────────────────────────────
    heading("5. oil_data.json town names vs GeoJSON")
    if not os.path.exists(JSON_PATH):
        print(f"   SKIPPED — {JSON_PATH} not found. Run the scraper first.")
    else:
        with open(JSON_PATH) as f:
            data = json.load(f)

        rows = data.get("monthly_by_town", [])
        json_towns = {r.get("geo_town") or r.get("city") for r in rows}
        bad = sorted(t for t in json_towns if t not in geo_towns)

        if bad:
            problems += len(bad)
            print(f"FAIL — {len(bad)} town name(s) in {JSON_PATH} match no polygon:")
            for t in bad:
                print(f"   - {t!r}")
        else:
            print(f"OK — all {len(json_towns)} town names in {JSON_PATH} match the GeoJSON.")

        if "unmapped_total" in data:
            print(f"   Reported unmapped_total: {data['unmapped_total']:,} gallons")

    # ── verdict ──────────────────────────────────────────────────────────────
    heading("VERDICT")
    if problems:
        print(f"{problems} problem(s) found.")
        return 1

    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
