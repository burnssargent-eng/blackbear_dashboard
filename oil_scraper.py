#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Apr  2 08:53:19 2026

@author: sargentburns
"""

import os
import requests
import re
import time
import calendar
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
import json
import argparse

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BASE_URL     = "https://jammal28.dreamhosters.com/tracking"
LIST_URL     = f"{BASE_URL}/assign_routes.php"
INACTIVE_URL = f"{BASE_URL}/inactive.php"
DELAY_SEC    = 0.3

# Per-record detail, written alongside the summary oil_data.json.
COLLECTIONS_JSON = "oil_collections.json"

# Quantities meaning "we collected nothing" — excluded from all totals.
#   2 = customer call received / entered in the system, not an oil pickup
#   3 = barrel or tote delivery, not an oil pickup
# 4 is NOT excluded: it is a data-entry typo but counts as 4 gallons exactly,
# and is never rounded up.
EMPTY_QTYS = {0, 1, 2, 3}

# A source-site ACTIVE customer with no qualifying pickup since this date counts
# as lost, attributed to the year of their last qualifying pickup. Fixed rather
# than rolling so the numbers are reproducible against past reports; change this
# one line to move it.
DORMANT_CUTOFF = "2025-01-01"

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,  "May": 5,  "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# ─────────────────────────────────────────────
# COUNTY MAP  (city -> VT county)
# ─────────────────────────────────────────────
COUNTY_MAP = {
    "Burlington":        "Chittenden",
    "South Burlington":  "Chittenden",
    "Winooski":          "Chittenden",
    "Colchester":        "Chittenden",
    "Williston":         "Chittenden",
    "Essex Junction":    "Chittenden",
    "Essex":             "Chittenden",
    "Shelburne":         "Chittenden",
    "Milton":            "Chittenden",
    "Richmond":          "Chittenden",
    "Hinesburg":         "Chittenden",
    "Hinesburgh":        "Chittenden",
    "Charlotte":         "Chittenden",
    "Jericho Center":    "Chittenden",
    "Westford":          "Chittenden",
    "Underhill":         "Chittenden",
    "Bolton Valley":     "Chittenden",
    "Montpelier":        "Washington",
    "Barre":             "Washington",
    "Northfield":        "Washington",
    "Waterbury":         "Washington",
    "Waitsfield":        "Washington",
    "Warren":            "Washington",
    "Berlin":            "Washington",
    "Middlesex":         "Washington",
    "Moretown":          "Washington",
    "Fayston":           "Washington",
    "Duxbury":           "Washington",
    "East Montpelier":   "Washington",
    "Graniteville":      "Washington",
    "East Barre":        "Washington",
    "Braintree":         "Washington",
    "Roxbury":           "Washington",
    "S. Barre":          "Washington",
    "Betlin":            "Washington",
    "Orange":            "Washington",
    "Williamstown":      "Washington",
    "Plainfield":        "Washington",
    "Stowe":             "Lamoille",
    "Morrisville":       "Lamoille",
    "Morristown":        "Lamoille",
    "Johnson":           "Lamoille",
    "Hyde Park":         "Lamoille",
    "Jeffersonville":    "Lamoille",
    "Cambridge":         "Lamoille",
    "Wolcott":           "Lamoille",
    "Eden":              "Lamoille",
    "Eden Mills":        "Lamoille",
    "St. Johnsbury":     "Caledonia",
    "Saint Johnsbury":   "Caledonia",
    "Lyndonville":       "Caledonia",
    "Danville":          "Caledonia",
    "Cabot":             "Caledonia",
    "Hardwick":          "Caledonia",
    "East Burke":        "Caledonia",
    "Burke":             "Caledonia",
    "Craftsbury":        "Caledonia",
    "Craftsbury Common": "Caledonia",
    "Concord":           "Caledonia",
    "East Haven":        "Caledonia",
    "Greensboro":        "Caledonia",
    "Walden":            "Caledonia",
    "Marshfield":        "Caledonia",
    "Lower Waterford":   "Caledonia",
    "Ryegate":           "Caledonia",
    "Newport":           "Orleans",
    "Jay":               "Orleans",
    "Derby":             "Orleans",
    "North Troy":        "Orleans",
    "Orleans":           "Orleans",
    "Barton":            "Orleans",
    "Montgomery":        "Orleans",
    "Montgomery Center": "Orleans",
    "Enosburgh Falls":   "Orleans",
    "Enosburg Falls":    "Orleans",
    "West Glover":       "Orleans",
    "Island Pond":       "Essex",
    "Gilman":            "Essex",
    "Middlebury":        "Addison",
    "Vergennes":         "Addison",
    "Bristol":           "Addison",
    "Lincoln":           "Addison",
    "Shoreham":          "Addison",
    "Ferrisburgh":       "Addison",
    "Whiting":           "Addison",
    "Rutland":           "Rutland",
    "Pittsford":         "Rutland",
    "Killington":        "Rutland",
    "Pittsfield":        "Rutland",
    "Rochester":         "Rutland",
    "Woodstock":         "Windsor",
    "Quechee":           "Windsor",
    "Ludlow":            "Windsor",
    "South Royalton":    "Windsor",
    "Bethel":            "Windsor",
    "Randolph":          "Windsor",
    "Randolph Center":   "Windsor",
    "Barnard":           "Windsor",
    "South Pomfret":     "Windsor",
    "Pomfret":           "Windsor",
    "Perkinsville":      "Windsor",
    "Tunbridge":         "Windsor",
    "Wells River":       "Windsor",
    "Brookfield":        "Windsor",
    "Chelsea":           "Windsor",
    "Royalton":          "Windsor",
    "Fairlee":           "Orange",
    "Bradford":          "Orange",
    "Brattleboro":       "Windham",
    "West Dover":        "Windham",
    "Wilmington":        "Windham",
    "Dover":             "Windham",
    "Jacksonville":      "Windham",
    "Bellows Falls":     "Windham",
    "Readsboro":         "Windham",
    "Sunderland":        "Windham",
    "Arlington":         "Windham",
    "Manchester":        "Bennington",
    "Manchester Center": "Bennington",
    "Bennington":        "Bennington",
    "St. Albans":        "Franklin",
    "St. Albans City":   "Franklin",
    "St Albans":         "Franklin",
    "Swanton":           "Franklin",
    "Richford":          "Franklin",
    "Enosburg":          "Franklin",
    "Georgia":           "Franklin",
    "Fairfax":           "Franklin",
    "North Hero":        "Grand Isle",
    "South Hero":        "Grand Isle",
    "Albany":            "Orleans",
    "Plattsburgh":       "Out-of-State (NY)",
    "Claremont":         "Out-of-State (NH)",
    "Hanover":           "Out-of-State (NH)",
    "West Lebanon":      "Out-of-State (NH)",
    "Pawtucket":         "Out-of-State (RI)",
    "Dalton":            "Out-of-State (NH)",
    "Belmont":           "Rutland",
}

# Longest-first helps avoid partial matches like Essex before Essex Junction.
KNOWN_CITIES = sorted(COUNTY_MAP.keys(), key=len, reverse=True)

# ─────────────────────────────────────────────
# OFFICIAL VERMONT TOWN NORMALIZATION (for the map)
# ─────────────────────────────────────────────
# The heatmap is drawn from vermont_towns.geojson, whose town names live in the
# TOWNNAMEMC property. Our scraped "city" values are mailing/village names, which
# often are NOT official towns. Every value on the right-hand side below has been
# verified to exist in vermont_towns.geojson.
#
# Rule: villages roll up to the official town whose borders contain them.
GEO_TOWN_NAME_MAP = {
    # --- spelling variants ---
    "St. Johnsbury":     "Saint Johnsbury",
    "Hinesburgh":        "Hinesburg",
    "Betlin":            "Berlin",
    # The official town is spelled "Enosburgh" (with the h) in the state GeoJSON.
    "Enosburg":          "Enosburgh",
    "Enosburg Falls":    "Enosburgh",
    "Enosburgh Falls":   "Enosburgh",

    # --- village / place names that roll up to their official town ---
    "Montgomery Center": "Montgomery",
    "Craftsbury Common": "Craftsbury",
    "Jericho Center":    "Jericho",
    "Eden Mills":        "Eden",
    "Randolph Center":   "Randolph",
    "South Pomfret":     "Pomfret",
    "South Royalton":    "Royalton",
    "Manchester Center": "Manchester",
    "Bolton Valley":     "Bolton",
    "West Dover":        "Dover",
    "Quechee":           "Hartford",
    "Morrisville":       "Morristown",
    "West Glover":       "Glover",
    "Jeffersonville":    "Cambridge",
    "North Troy":        "Troy",
    "Island Pond":       "Brighton",
    "Wells River":       "Newbury",
    "Lyndonville":       "Lyndon",
    "Orleans":           "Barton",
    "East Burke":        "Burke",
    "Jacksonville":      "Whitingham",
    "Lower Waterford":   "Waterford",
    "Bellows Falls":     "Rockingham",
    "Gilman":            "Lunenburg",
    "Perkinsville":      "Weathersfield",
    "Belmont":           "Mount Holly",

    # --- villages inside Barre Town (NOT Barre City) ---
    "East Barre":        "Barre Town",
    "Graniteville":      "Barre Town",
    "S. Barre":          "Barre Town",

    # --- ambiguous City/Town pairs -------------------------------------------
    # Vermont splits each of these into two separate municipalities with two
    # separate polygons, but our source data only says e.g. "Barre". These are
    # the DEFAULTS; individual customers whose street address places them in the
    # Town are corrected by CITY_TOWN_BY_CUSTOMER below.
    "Barre":             "Barre City",
    "Newport":           "Newport City",
    "Rutland":           "Rutland City",
    "St. Albans":        "Saint Albans City",
    "St Albans":         "Saint Albans City",
    "St. Albans City":   "Saint Albans City",
}

# Places that are genuinely not in Vermont. These are kept in the raw data and
# in the county summaries, but are deliberately never mapped onto a Vermont
# town polygon.
OUT_OF_STATE_CITIES = {
    "Plattsburgh",   # NY
    "Claremont",     # NH
    "Hanover",       # NH
    "West Lebanon",  # NH
    "Dalton",        # NH
    "Pawtucket",     # RI
}

# Per-customer City-vs-Town corrections, resolved from each customer's street
# address on the source site. Addresses themselves are intentionally NOT stored
# in this repo; only the resulting official town name is kept.
# Only customers that differ from the GEO_TOWN_NAME_MAP default are listed.
CITY_TOWN_BY_CUSTOMER = {
    119:  "Barre Town",         # Thunder Road, Fisher Rd — outside the city line
    1128: "Barre Town",         # Hannaford, South Barre
    111:  "Barre Town",         # Gunner Brook, East Montpelier Rd
    110:  "Barre Town",         # Canadian Club, East Montpelier Rd
    1363: "Barre Town",         # GE gas station, South Barre Rd
    1258: "Rutland Town",       # Denny's, US-7 strip
    1493: "Saint Albans Town",  # Pizza Hut, Highgate Commons
    417:  "Saint Albans Town",  # Bayside Pavilion — St Albans Bay; the City is landlocked
}


def normalize_geo_town(city, customer_id=None):
    """
    Map a scraped city/village name to an official Vermont town name that exists
    in vermont_towns.geojson.

    Returns None for out-of-state places and for anything we cannot confidently
    place, so callers can exclude them from the Vermont map instead of guessing.
    """
    if not city:
        return None

    city = str(city).strip()
    if city in OUT_OF_STATE_CITIES:
        return None

    # A per-customer address-based correction always wins over the default.
    if customer_id is not None:
        try:
            override = CITY_TOWN_BY_CUSTOMER.get(int(customer_id))
        except (TypeError, ValueError):
            override = None
        if override:
            return override

    return GEO_TOWN_NAME_MAP.get(city, city)


def prepare_dataframe(df):
    """
    Apply data-correctness rules that must hold no matter whether the rows were
    freshly scraped or reloaded from oil_collections_raw.csv.

      1. Drop quantities that mean "nothing was collected".
      2. Add the normalized official-town column used by the map.
      3. Re-derive 'county' from COUNTY_MAP.
      4. Guarantee an 'is_active' column exists.

    The original 'city' value is always preserved untouched.
    """
    if df.empty:
        return df

    df = df[~df["gallons"].isin(EMPTY_QTYS)].copy()

    df["geo_town"] = [
        normalize_geo_town(city, cid) or ""
        for city, cid in zip(df["city"], df["customer_id"])
    ]

    # County is derived, not stored data. Recomputing it here means a correction
    # to COUNTY_MAP takes effect on the next plain run, instead of waiting for a
    # full --rescrape. Without this the county summary can disagree with the map:
    # a town could be mapped to a Vermont polygon while its stale county column
    # still said "Out-of-State".
    df["county"] = [COUNTY_MAP.get(city, "Unknown") for city in df["city"]]

    # Source-site active/inactive status is only knowable during a live scrape.
    # A CSV written before that field existed has no such column, so create it
    # as null rather than guessing a status from the last pickup date.
    if "is_active" not in df.columns:
        df["is_active"] = pd.NA

    return df

# ─────────────────────────────────────────────
# CUSTOM SUBREGION DEFINITIONS
# ─────────────────────────────────────────────
# Region membership is CONFIGURATION, not code. Add a town to a set below and
# every consumer follows: the Excel report, the five region keys in
# oil_data.json, region.html, and the homepage Top Regions list.
#
# Towns are matched on the NORMALIZED geo_town from normalize_geo_town, falling
# back to the raw city when geo_town is empty. That fallback is what reaches
# out-of-state places such as Plattsburgh, which deliberately have no Vermont
# town. Matching on geo_town also inherits every village spelling already in
# GEO_TOWN_NAME_MAP for free — "West Dover" reaches Dover, "Jericho Center"
# reaches Jericho, "Morrisville" reaches Morristown — so a new village variant
# only ever has to be added in one place.
#
# Regions are deliberately NOT mutually exclusive; see assign_region_labels.

UVM_IDS = {353, 354, 355, 356, 358}

# Region name -> official town names. This order is the display order, and the
# first entry becomes region_names[0], which drives the default Regions nav link.
# Town order inside each region is the order the website lists them in, so keep
# these tuples readable — they are user-facing as well as functional.
REGION_TOWNS = {
    "Route 7": (
        "Middlebury", "Vergennes", "Bristol", "Charlotte", "Hinesburg",
        "New Haven", "Shelburne",
    ),
    "Waterbury / Stowe": ("Waterbury", "Stowe"),
    # Dover covers West Dover and every Mt. Snow entity; Ludlow covers Okemo.
    # Town matching alone reaches all of them, including TCs in Dover, which the
    # old customer-name matcher missed. Do NOT add name matching back:
    # "Snow Shoe Lodge-Montgomery" is a false positive for any "snow" rule, and
    # "Killington Diner" is addressed in Burlington.
    "Southern Ski Slopes": ("Dover", "Ludlow", "Killington"),
    "South": ("Bennington", "Brattleboro", "Manchester", "Sunderland"),
    # Vermont splits Barre into a City and a Town; both belong here.
    "Central": ("Berlin", "Barre City", "Barre Town", "Northfield", "Montpelier"),
    # Essex and Essex Junction are two separate municipalities.
    "Outer Burlington": (
        "Winooski", "Essex", "Essex Junction", "Williston", "Richmond",
        "Jericho",
    ),
    # Plattsburgh NY has no geo_town and is reached by the raw-city fallback.
    "Northwest": (
        "Plattsburgh", "Enosburgh", "North Hero", "South Hero", "Colchester",
        "Milton",
    ),
    "Burlington / South Burlington": ("Burlington", "South Burlington"),
    "Route 15": (
        "Hardwick", "Wolcott", "Johnson", "Hyde Park", "Morristown",
        "Cambridge",
    ),
    # Jim: "Quechee and Woodstock and Bethel & Randolph and West Leb".
    # Hartford is the official town for Quechee, and Pomfret rides with
    # Woodstock. West Lebanon is in New Hampshire, so it has no geo_town and is
    # reached by the raw-city fallback in region_key.
    "Central South": (
        "Hartford", "Woodstock", "Pomfret", "Bethel", "Randolph",
        "West Lebanon",
    ),
    "Route 2 East": (
        "Cabot", "Plainfield", "Danville", "Saint Johnsbury", "Concord",
    ),
    "Warren / Waitsfield": ("Warren", "Waitsfield"),
    "Jay / Montgomery / Troy": ("Jay", "Montgomery", "Troy"),
}

# Regions keyed on customer id rather than town. UVM overlaps
# Burlington / South Burlington on purpose: those five customers count in both,
# which keeps the Burlington figure a true town total.
REGION_CUSTOMER_IDS = {
    "UVM": UVM_IDS,
}

# Some towns are better known by a village name than by their official one.
# DISPLAY ONLY — membership still matches on the official town above.
TOWN_DISPLAY_NAMES = {
    "Hartford": "Hartford/Quechee",
}

# Regions that need no "Includes:" line: their own name already lists their
# contents, so repeating it under the dropdown is noise. UVM is here too — the
# name is self-evident to the business.
SELF_DESCRIBING_REGIONS = {
    "Burlington / South Burlington",
    "Warren / Waitsfield",
    "Jay / Montgomery / Troy",
    "Waterbury / Stowe",
    "UVM",
}

# The catch-all. Not a named region, but region.html offers it in the dropdown,
# so it needs a description too.
OTHER_REGION_DESCRIPTION = "customers not assigned to a named region"


def region_key(row):
    """
    The value a region's town set is matched against.

    The normalized official town, falling back to the raw scraped city when
    normalize_geo_town returned nothing — which it does for out-of-state places.
    Non-string values (a NaN from a CSV reload) are treated as absent.
    """
    town = row.get("geo_town")
    town = town.strip() if isinstance(town, str) else ""
    if town:
        return town
    city = row.get("city")
    return city.strip() if isinstance(city, str) else ""


def _town_matcher(towns):
    return lambda r: region_key(r) in towns


def _customer_matcher(ids):
    return lambda r: r["customer_id"] in ids


CUSTOM_REGIONS = (
    [(name, _town_matcher(towns)) for name, towns in REGION_TOWNS.items()]
    + [(name, _customer_matcher(ids)) for name, ids in REGION_CUSTOMER_IDS.items()]
)

REGION_NAMES = [name for name, _ in CUSTOM_REGIONS]

# Excel forbids  [ ] : * ? / \  in a sheet title and caps it at 31 characters,
# so a display name like "Waterbury / Stowe" cannot be used as one directly.
# oil_data.json and the website keep the real names; only the workbook uses these.
_EXCEL_ILLEGAL = set("[]:*?/\\")


def excel_sheet_name(name, taken=None):
    """Sanitize a region name into a legal — and optionally unique — sheet title."""
    clean = "".join("-" if c in _EXCEL_ILLEGAL else c for c in name)
    clean = " ".join(clean.split()).strip(" -") or "Region"
    clean = clean[:31]
    if taken is None:
        return clean
    candidate, n = clean, 2
    while candidate in taken:
        suffix = f" {n}"
        candidate = clean[:31 - len(suffix)].rstrip() + suffix
        n += 1
    taken.add(candidate)
    return candidate


def region_sheet_names():
    """Region display name -> workbook sheet title, collision-free."""
    taken = set()
    return {name: excel_sheet_name(name, taken) for name in REGION_NAMES}


def region_descriptions():
    """
    Human-readable contents for each region — the website's "Includes:" line.

    Derived from the same REGION_TOWNS that decides membership, so the label a
    user reads cannot drift from the rule that produced the number. Town order
    follows the config, and a few towns are shown under a better-known village
    name. Regions whose name already lists their contents are omitted, so the
    page simply shows no line for them. "Other" is included even though it is
    not a named region.
    """
    out = {}
    for name in REGION_NAMES:
        if name in SELF_DESCRIBING_REGIONS:
            continue
        towns = REGION_TOWNS.get(name, ())
        if not towns:
            continue
        out[name] = ", ".join(TOWN_DISPLAY_NAMES.get(t, t) for t in towns)
    out["Other"] = OTHER_REGION_DESCRIPTION
    return out


def current_calendar_year():
    """
    The year the site treats as "current".

    Kept in one place so the region display order and current_year_total are
    always computed on the same basis.
    """
    return datetime.today().year


def region_display_order(df_region, current_year=None):
    """
    Named regions ordered by current-year gallons, highest first.

    The order is data-driven rather than declaration order, so the site leads
    with the regions carrying this year's volume. Ties break alphabetically so
    the output is stable between runs. "Other" is deliberately absent: it is a
    catch-all rather than a place, and region.html appends it after the named
    regions on its own.
    """
    if current_year is None:
        current_year = current_calendar_year()
    current = df_region[df_region["year"] == current_year]
    gallons = current.groupby("region")["gallons"].sum()
    return sorted(REGION_NAMES, key=lambda name: (-int(gallons.get(name, 0)), name))

# ─────────────────────────────────────────────
# SCRAPING HELPERS
# ─────────────────────────────────────────────

def get_soup(url, session):
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_customers(soup):
    """Extract customer list from either the active or inactive page."""
    customers = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        link = cells[1].find("a", href=True)
        if not link or "customer.php" not in link["href"]:
            continue
        name = link.get_text(strip=True)
        cid = re.search(r"id=(\d+)", link["href"])
        city = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        if cid:
            customers.append({
                "customer_id": int(cid.group(1)),
                "name": name,
                "city": city,
            })

    # Inactive page sometimes uses plain <a> links instead of a table.
    if not customers:
        for link in soup.find_all("a", href=True):
            if "customer.php" not in link["href"]:
                continue
            cid = re.search(r"id=(\d+)", link["href"])
            if cid:
                customers.append({
                    "customer_id": int(cid.group(1)),
                    "name": link.get_text(strip=True),
                    "city": "",
                })
    return customers


def normalize_space(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_city_from_page(soup, customer):
    """
    Resolve a customer's town from their customer page.

    This fixes inactive customers whose list page has no town. The old code used
    a generic capitalized-line regex, which often captured the business name
    (for example, 'Asian Bistro') instead of the actual town ('Williston').
    """
    if customer.get("city") in COUNTY_MAP:
        return customer["city"]

    lines = [normalize_space(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    full_text = "\n".join(lines)

    # 1) Exact line match: many pages show the town on its own line.
    for line in lines:
        for city in KNOWN_CITIES:
            if line.lower() == city.lower():
                return city

    # 2) Look for address-style lines containing a known town.
    for line in lines:
        for city in KNOWN_CITIES:
            if re.search(rf"\b{re.escape(city)}\b", line, flags=re.IGNORECASE):
                return city

    # 3) Fall back to scanning the full page text.
    for city in KNOWN_CITIES:
        if re.search(rf"\b{re.escape(city)}\b", full_text, flags=re.IGNORECASE):
            return city

    return customer.get("city", "")


def parse_collections(soup, customer):
    """
    Parse both data formats on customer pages:
      NEW (2021+):    "10/24/2025 : 65"
      OLD (pre-2021): year-header "### 2019" then "Dec 22 : 80"
    Quantities in EMPTY_QTYS are skipped.
    """
    records = []
    text = soup.get_text(separator="\n")
    city = extract_city_from_page(soup, customer)

    def make_record(date, qty):
        if qty in EMPTY_QTYS:
            return None
        return {
            "customer_id": customer["customer_id"],
            "name": customer["name"],
            "city": city,
            "county": COUNTY_MAP.get(city, "Unknown"),
            "date": date,
            "year": date.year,
            "month": date.strftime("%Y-%m"),
            "gallons": qty,
            # True/False when scraped live; stays None if the caller did not
            # stamp the customer (never inferred from collection history).
            "is_active": customer.get("is_active"),
        }

    years_covered = set()
    seen = set()

    # NEW FORMAT: MM/DD/YYYY : number
    for match in re.finditer(r"(\d{1,2}/\d{1,2}/\d{4})\s*:\s*(\d+)", text):
        date_str, qty_str = match.group(1), match.group(2)
        qty = int(qty_str)
        try:
            date = datetime.strptime(date_str, "%m/%d/%Y")
        except ValueError:
            continue
        years_covered.add(date.year)
        key = (date_str, qty_str)
        if key in seen:
            continue
        seen.add(key)
        rec = make_record(date, qty)
        if rec:
            records.append(rec)

    # OLD FORMAT: ### YEAR header + "Dec 22 : 80" lines
    current_year = None
    for line in text.splitlines():
        line = line.strip()
        ymatch = re.match(r"^#{0,3}\s*(20\d{2}|19\d{2})\s*$", line)
        if ymatch:
            current_year = int(ymatch.group(1))
            continue
        if current_year is None or current_year in years_covered:
            continue
        omatch = re.match(
            r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"\s+(\d{1,2})\s*:\s*(\d+)$",
            line,
        )
        if omatch:
            mon_str, day_str, qty_str = omatch.groups()
            qty = int(qty_str)
            try:
                date = datetime(current_year, MONTH_MAP[mon_str], int(day_str))
            except ValueError:
                continue
            rec = make_record(date, qty)
            if rec:
                records.append(rec)

    return records


def scrape_all():
    session = requests.Session()
    session.headers.update({"User-Agent": "OilCollectionScraper/1.0"})

    print("Fetching active customer list...")
    soup = get_soup(LIST_URL, session)
    active = parse_customers(soup)
    print(f"   Found {len(active)} active customers.")

    print("Fetching inactive customer list...")
    isoup = get_soup(INACTIVE_URL, session)
    inactive = parse_customers(isoup)
    print(f"   Found {len(inactive)} inactive customers.")

    seen_ids = {c["customer_id"] for c in active}
    inactive_only = [c for c in inactive if c["customer_id"] not in seen_ids]
    print(f"   {len(inactive_only)} inactive-only customers to add.")

    customers = active + inactive_only

    # Stamp the source-site status onto each customer so it reaches the records
    # (and therefore the CSV and the JSON), instead of only the progress log.
    # A customer on both lists counts as active.
    for cust in customers:
        cust["is_active"] = cust["customer_id"] in seen_ids

    print(f"\nScraping {len(customers)} total customers...\n")

    all_records = []
    unresolved_city_ids = []

    for i, cust in enumerate(customers, 1):
        tag = "active  " if cust["is_active"] else "INACTIVE"
        url = f"{BASE_URL}/customer.php?id={cust['customer_id']}"
        try:
            csoup = get_soup(url, session)
            records = parse_collections(csoup, cust)
            all_records.extend(records)

            resolved_city = records[0]["city"] if records else extract_city_from_page(csoup, cust)
            if not resolved_city or resolved_city not in COUNTY_MAP:
                unresolved_city_ids.append((cust["customer_id"], cust["name"], resolved_city))

            status = f"{len(records)} records"
            if resolved_city:
                status += f", city={resolved_city}"
            else:
                status += ", city=UNRESOLVED"

            print(
                f"  [{i:>3}/{len(customers)}] [{tag}] "
                f"{cust['name'][:38]:<38} -> {status}"
            )
        except Exception as e:
            print(f"  [{i:>3}/{len(customers)}] ERROR for {cust['name']}: {e}")
        time.sleep(DELAY_SEC)

    if unresolved_city_ids:
        print("\nWARNING: could not confidently resolve town for these customers:")
        for cid, name, city in unresolved_city_ids:
            print(f"  - {cid}: {name} -> {city or 'UNRESOLVED'}")

    return pd.DataFrame(all_records)

# ─────────────────────────────────────────────
# REPORTING HELPERS
# ─────────────────────────────────────────────

def make_pivot(df, row_col, col_col, columns=None):
    """Cross-tab pivot with row and column totals."""
    pivot = (
        df.groupby([row_col, col_col])["gallons"]
        .sum().unstack(fill_value=0).sort_index()
    )
    if columns is not None:
        pivot = pivot.reindex(columns=columns, fill_value=0)
    pivot.loc["TOTAL"] = pivot.sum()
    pivot["ROW_TOTAL"] = pivot.sum(axis=1)
    return pivot


def assign_region_labels(df):
    """
    Add a 'region' column to df using CUSTOM_REGIONS matchers.
    A row may match multiple regions — it gets ALL matching labels.
    Rows matching no region are labeled 'Other'.
    """
    rows = []
    for _, row in df.iterrows():
        matched = [name for name, fn in CUSTOM_REGIONS if fn(row)]
        if not matched:
            matched = ["Other"]
        for label in matched:
            r = row.to_dict()
            r["region"] = label
            rows.append(r)
    return pd.DataFrame(rows)


def make_subregion_tables(sub_df):
    monthly = (
        sub_df.groupby("month")["gallons"].sum()
        .reset_index().rename(columns={"month": "Month", "gallons": "Gallons"})
        .sort_values("Month").reset_index(drop=True)
    )
    monthly.loc[len(monthly)] = ["TOTAL", monthly["Gallons"].sum()]

    yearly = (
        sub_df.groupby("year")["gallons"].sum()
        .reset_index().rename(columns={"year": "Year", "gallons": "Gallons"})
        .sort_values("Year").reset_index(drop=True)
    )
    yearly.loc[len(yearly)] = ["TOTAL", yearly["Gallons"].sum()]

    return monthly, yearly

# ─────────────────────────────────────────────
# MAIN REPORT BUILDER
# ─────────────────────────────────────────────

def build_reports(df):
    if df.empty:
        print("No data collected.")
        return

    df.to_csv("oil_collections_raw.csv", index=False)
    print(f"\nRaw data: {len(df)} records -> oil_collections_raw.csv")

    df_region = assign_region_labels(df)
    df_region_named = df_region[df_region["region"].isin(REGION_NAMES)].copy()

    # Same order the website uses: current-year gallons, highest first.
    ordered_regions = region_display_order(df_region)

    with pd.ExcelWriter("oil_collection_report.xlsx", engine="openpyxl") as writer:
        make_pivot(df, "month", "city").to_excel(writer, sheet_name="Monthly by Town")
        make_pivot(df, "month", "county").to_excel(writer, sheet_name="Monthly by County")
        make_pivot(df_region, "month", "region").to_excel(writer, sheet_name="Monthly by Region")

        make_pivot(df, "year", "city").to_excel(writer, sheet_name="Yearly by Town")
        make_pivot(df, "year", "county").to_excel(writer, sheet_name="Yearly by County")
        make_pivot(df_region, "year", "region").to_excel(writer, sheet_name="Yearly by Region")

        # Per-calendar-year sheets: monthly x REGION (not county)
        for year in sorted(df["year"].unique()):
            ydf = df_region_named[df_region_named["year"] == year]
            make_pivot(ydf, "month", "region", columns=ordered_regions).to_excel(
                writer,
                sheet_name=str(year),
            )

        # Individual subregion detail sheets. Region display names may contain
        # characters Excel rejects in a sheet title, so the workbook uses the
        # sanitized names while the JSON and the website keep the real ones.
        sheet_titles = region_sheet_names()
        matchers = dict(CUSTOM_REGIONS)
        for region_name in ordered_regions:
            mask = df.apply(matchers[region_name], axis=1)
            sub = df[mask]
            if sub.empty:
                print(f"  WARNING: no data matched for '{region_name}'")
                continue

            sheet_name = sheet_titles[region_name]
            monthly, yearly = make_subregion_tables(sub)
            monthly.to_excel(writer, sheet_name=sheet_name, index=False, startrow=0)
            yearly.to_excel(writer, sheet_name=sheet_name, index=False, startrow=len(monthly) + 2)

            print(
                f"  Sheet '{sheet_name}': {len(sub):,} records, "
                f"{sub['gallons'].sum():,} total gallons"
            )

    print("\nExcel report saved: oil_collection_report.xlsx")

    print("\n" + "=" * 55)
    print("YEARLY GRAND TOTALS")
    print("=" * 55)
    print(df.groupby("year")["gallons"].sum().to_string())

    print("\n" + "=" * 55)
    print("YEARLY TOTALS BY COUNTY")
    print("=" * 55)
    yr = df.groupby(["year", "county"])["gallons"].sum().unstack(fill_value=0)
    yr["TOTAL"] = yr.sum(axis=1)
    print(yr.to_string())
    
    
def records(frame):
    """
    DataFrame -> list of dicts using plain Python types.

    json.dump below is called with default=str, so any value it cannot natively
    serialize gets stringified. A leftover numpy int64 would therefore be
    written as "26530" instead of 26530, which quietly breaks any consumer doing
    arithmetic on it. Coerce to native types here so that cannot happen.
    """
    rows = frame.to_dict(orient="records")
    for row in rows:
        for key, value in row.items():
            if value is pd.NA or value is None:
                row[key] = None
            elif hasattr(value, "item"):        # numpy scalar
                row[key] = value.item()
    return rows


def compute_projection(df):
    """
    Project the current year's counted gallons from the pace so far.

    Compares against the PRORATED previous year rather than the previous year's
    same-day total, so a partial month is scaled by how much of it has elapsed:

        previousProratedYTD  = previousThroughPriorMonth
                               + (dayOfMonth / daysInMonth) * previousSameMonthTotal
        projectedCurrentYear = previousYearFull / previousProratedYTD * currentYTD

    Anchored to the latest collection date in the data, NOT today's real date,
    so a scrape that has not run for a few days does not drag the projection
    down as though nothing were collected.

    Returns None when there is no usable prior year to compare against.
    """
    if df.empty:
        return None

    latest = pd.to_datetime(df["date"]).max()
    current_year = int(latest.year)
    previous_year = current_year - 1

    current_rows = df[df["year"] == current_year]
    previous_rows = df[df["year"] == previous_year]
    if current_rows.empty or previous_rows.empty:
        return None

    month = int(latest.month)
    day = int(latest.day)
    days_in_month = calendar.monthrange(previous_year, month)[1]

    current_ytd = int(current_rows["gallons"].sum())
    previous_year_full = int(previous_rows["gallons"].sum())

    prior_months = previous_rows[
        pd.to_datetime(previous_rows["date"]).dt.month < month
    ]
    previous_through_prior_month = int(prior_months["gallons"].sum())

    same_month = previous_rows[
        pd.to_datetime(previous_rows["date"]).dt.month == month
    ]
    previous_same_month_total = int(same_month["gallons"].sum())

    previous_prorated_ytd = (
        previous_through_prior_month
        + (day / days_in_month) * previous_same_month_total
    )

    # Guard against a prior year with nothing to compare against.
    if previous_prorated_ytd <= 0 or previous_year_full <= 0:
        return None

    projected = previous_year_full / previous_prorated_ytd * current_ytd

    return {
        "latest_data_date": latest.strftime("%Y-%m-%d"),
        "current_year": current_year,
        "previous_year": previous_year,
        "current_ytd": current_ytd,
        "previous_year_full": previous_year_full,
        "previous_through_prior_month": previous_through_prior_month,
        "previous_same_month_total": previous_same_month_total,
        "previous_prorated_ytd": round(previous_prorated_ytd, 2),
        "projected_current_year": int(round(projected)),
        "percent_vs_previous_year": round(projected / previous_year_full - 1, 6),
    }


def compute_customer_lifecycle(df):
    """
    One row per customer describing their qualifying-pickup lifecycle.

    Every row in df is already a qualifying pickup: EMPTY_QTYS strips 0/1/2/3 at
    parse time, so everything retained is an oil pickup of 4+ gallons. That
    means "first/last pickup" here is precisely "first/last QUALIFYING pickup",
    and the pages must label it that way.

    lost_year is set when the customer is either off the source-site active list
    or still on it but dormant since DORMANT_CUTOFF. It is the year of their
    LAST qualifying pickup, which may be long before they were marked inactive.
    """
    if df.empty:
        return []

    dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    work = df.assign(_date=dates)

    customers = []

    for cid, group in work.groupby("customer_id"):
        first = group["_date"].min()
        last = group["_date"].max()
        row = group.iloc[0]

        is_active = row["is_active"]
        if pd.isna(is_active):
            is_active = None
        else:
            is_active = bool(is_active)

        # Two independent ways to be "lost". Recorded separately so the year
        # page can explain WHY a customer appears in the list.
        source_inactive = is_active is False
        dormant_active = is_active is True and last < DORMANT_CUTOFF

        if source_inactive:
            lost_reason = "Source-site inactive"
        elif dormant_active:
            lost_reason = "Dormant active-list"
        else:
            lost_reason = None

        customers.append({
            "customer_id": int(cid),
            "name": row["name"],
            "city": row["city"],
            "geo_town": row["geo_town"],
            "county": row["county"],
            "is_active": is_active,
            "gallons": int(group["gallons"].sum()),
            "pickups": int(len(group)),
            "first_qualifying_pickup": first,
            "last_qualifying_pickup": last,
            "gained_year": int(first[:4]),
            "lost_year": int(last[:4]) if lost_reason else None,
            "lost_reason": lost_reason,
        })

    customers.sort(key=lambda c: -c["gallons"])
    return customers


def compute_lifecycle_by_year(df, customers):
    """
    Places serviced / gained / lost / net per calendar year.

      places_serviced — unique customers with 1+ qualifying pickup that year
      gained          — customers whose FIRST qualifying pickup was that year
      lost            — customers whose LAST qualifying pickup was that year AND
                        who are source-site inactive or dormant-active
      net             — gained - lost
    """
    if df.empty:
        return []

    served = df.groupby("year")["customer_id"].nunique()

    gained = {}
    lost = {}
    for c in customers:
        gained[c["gained_year"]] = gained.get(c["gained_year"], 0) + 1
        if c["lost_year"] is not None:
            lost[c["lost_year"]] = lost.get(c["lost_year"], 0) + 1

    rows = []
    for year in sorted(served.index):
        y = int(year)
        g = int(gained.get(y, 0))
        l = int(lost.get(y, 0))
        rows.append({
            "year": y,
            "places_serviced": int(served.loc[year]),
            "gained": g,
            "lost": l,
            "net": g - l,
        })

    return rows


def compute_region_customers(df_region):
    """
    Which customer ids belong to each region.

    Derived from the same CUSTOM_REGIONS matchers used by the Excel report, so
    the website cannot drift from it. Regions are NOT exclusive — a customer
    matching two regions appears under both.
    """
    return {
        str(region): sorted(int(c) for c in group["customer_id"].unique())
        for region, group in df_region.groupby("region")
    }


def compute_region_stats(df_region):
    """
    Customer and active-customer counts per region.

    Regions are NOT exclusive — a customer matching two regions is counted in
    both — so these counts do not sum to the company total. That matches the
    existing Excel behavior and is intentional.
    """
    stats = {}

    for region, group in df_region.groupby("region"):
        by_customer = group.drop_duplicates("customer_id")
        active = by_customer["is_active"]

        stats[str(region)] = {
            "customers": int(len(by_customer)),
            # Source-site status only. Never inferred from pickup recency.
            # None when the scrape has not captured status yet.
            "active": int((active == True).sum()) if active.notna().any() else None,
        }

    return stats


def export_json(df):
    """Export clean JSON for the website to consume."""

    # Monthly totals by town, grouped by official GeoJSON town name so the
    # heatmap can match them. Rows with no Vermont town (out-of-state, or a
    # place we could not confidently resolve) are excluded from the map data —
    # they remain in the raw CSV and in the county summary below.
    mappable = df[df["geo_town"] != ""]
    monthly_town = (
        mappable.groupby(["month", "geo_town"])["gallons"]
        .sum()
        .reset_index()
    )
    # 'city' is kept as a backward-compatible alias of 'geo_town' so an older
    # cached copy of index.html keeps working. Safe to drop once cache-busting
    # is in place (Phase 3).
    monthly_town["city"] = monthly_town["geo_town"]


    # Monthly totals by county
    monthly_county = (
        df.groupby(["month", "county"])["gallons"]
        .sum()
        .reset_index()
    )
    
    # Yearly totals
    yearly = (
        df.groupby("year")["gallons"]
        .sum()
        .reset_index()
    )
    
    # Grand total per month, across every record including out-of-state ones,
    # so sum(monthly_totals) reconciles exactly to all_time_total.
    monthly_totals = (
        df.groupby("month")["gallons"]
        .sum()
        .reset_index()
    )

    # Regions. IMPORTANT: assign_region_labels is deliberately NOT exclusive —
    # a record matching two regions is emitted once per region, and anything
    # matching none lands in "Other". Region gallons therefore SUM TO MORE than
    # all_time_total. That is the existing Excel behavior and is intentional;
    # do not "fix" it by forcing one region per record.
    df_region = assign_region_labels(df)
    monthly_region = (
        df_region.groupby(["month", "region"])["gallons"]
        .sum()
        .reset_index()
    )
    yearly_region = (
        df_region.groupby(["year", "region"])["gallons"]
        .sum()
        .reset_index()
    )

    # Per-customer lifecycle, shared by the summary below and the detail file.
    customers = compute_customer_lifecycle(df)

    # Current year month-by-month
    current_year = current_calendar_year()
    current = df[df["year"] == current_year]

    output = {
        "last_updated":    datetime.now().isoformat(),
        "monthly_by_town": records(monthly_town),
        "monthly_by_county": records(monthly_county),
        "yearly_totals":   records(yearly),
        "all_time_total":  int(df["gallons"].sum()),
        "current_year_total": int(current["gallons"].sum()),
        "customer_count":  int(df["customer_id"].nunique()),
        # Gallons excluded from the Vermont map (out-of-state / unresolved).
        # Reported so the map total can be reconciled against all_time_total.
        "unmapped_total":  int(df[df["geo_town"] == ""]["gallons"].sum()),

        # ── added in Phase 2 so future pages can be built from this file ──
        "monthly_totals":    records(monthly_totals),
        "monthly_by_region": records(monthly_region),
        "yearly_by_region":  records(yearly_region),
        # The named regions, ordered by current-year gallons, highest first.
        # Excludes the "Other" bucket, which still appears in the region
        # summaries above and is appended after these by region.html.
        "region_names":      region_display_order(df_region, current_year),
        # What each region contains, for the "Includes:" line on region.html.
        # Built from REGION_TOWNS so the page cannot drift from the matchers.
        "region_descriptions": region_descriptions(),
        # Per-record detail lives in its own file so the homepage does not have
        # to download ~4.7 MB it never reads. Fetch it only when a page needs it.
        "collections_file":  COLLECTIONS_JSON,
        "collections_count": int(len(df)),
        "out_of_state_total": int(
            df[df["county"].str.startswith("Out-of-State", na=False)]["gallons"].sum()
        ),

        # ── added in Phase 4B ──
        # Source-site active customers. None if a scrape has not captured
        # status yet; the page then says so rather than showing a wrong number.
        "active_customer_count": (
            int(df.drop_duplicates("customer_id")["is_active"].eq(True).sum())
            if df["is_active"].notna().any() else None
        ),
        # Month-prorated projection for the current year. None when there is no
        # usable prior year to compare against.
        "projection": compute_projection(df),
        # Per-region customer and active-customer counts. Not additive: regions
        # are not exclusive.
        "region_stats": compute_region_stats(df_region),

        # ── added in Phase 4C ──
        # Places serviced / gained / lost / net per year. Small enough to live
        # in the summary file; the per-customer detail behind it ships with
        # oil_collections.json so the homepage does not pay for it.
        "lifecycle_by_year": compute_lifecycle_by_year(df, customers),
        "dormant_cutoff": DORMANT_CUTOFF,
        # Region membership by customer id, from the same matchers the Excel
        # report uses. Regions are not exclusive.
        "region_customers": compute_region_customers(df_region),
    }

    with open("oil_data.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print("Exported oil_data.json")


def export_collections(df):
    """
    Export every individual collection record to its own JSON file.

    Kept separate from oil_data.json on purpose: this is roughly 4.7 MB and the
    homepage does not use it. Detail pages can fetch it on demand.

    Out-of-state rows ARE included — raw collections keep everything. Only the
    map-facing summaries in export_json filter them out.
    """
    detail = df.copy()
    # 'date' is a datetime; without this it would serialize as
    # "2026-06-13 00:00:00" instead of a plain calendar date.
    detail["date"] = pd.to_datetime(detail["date"]).dt.strftime("%Y-%m-%d")

    columns = [
        "customer_id",   # source-site id
        "name",          # customer name as shown on the source site
        "city",          # ORIGINAL raw scraped city/village, never rewritten
        "geo_town",      # normalized official VT town ("" when unmappable)
        "county",
        "date",
        "year",
        "month",
        "gallons",
        "is_active",     # source-site status; null until a scrape captures it
    ]
    detail = detail[columns]

    output = {
        "last_updated": datetime.now().isoformat(),
        "count": int(len(detail)),
        # Per-customer lifecycle travels with the detail file rather than the
        # summary, so the homepage never downloads it.
        "customers": compute_customer_lifecycle(df),
        "dormant_cutoff": DORMANT_CUTOFF,
        "records": detail.to_dict(orient="records"),
    }

    with open(COLLECTIONS_JSON, "w") as f:
        # Compact separators keep this near 4.7 MB instead of 6.3 MB.
        json.dump(output, f, separators=(",", ":"), default=str)

    size_mb = os.path.getsize(COLLECTIONS_JSON) / 1_000_000
    print(f"Exported {COLLECTIONS_JSON} ({len(detail):,} records, {size_mb:.1f} MB)")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Oil Collection Scraper")
    parser.add_argument(
        "--rescrape",
        action="store_true",
        help="Force a fresh scrape instead of loading the existing CSV."
    )
    args = parser.parse_args()

    print("Oil Collection Scraper")
    print("=" * 55)

    if os.path.exists("oil_collections_raw.csv") and not args.rescrape:
        print("Loading existing oil_collections_raw.csv.")
        print("Use --rescrape to fetch fresh website data.\n")
        df = pd.read_csv("oil_collections_raw.csv", parse_dates=["date"])
    else:
        print("Fresh scrape requested.\n")
        df = scrape_all()

    # Normalize town names and drop empty-quantity rows before anything is
    # written out, so the CSV, the Excel report and the JSON all agree.
    before = len(df)
    df = prepare_dataframe(df)
    print(f"\nPrepared {len(df):,} records ({before - len(df):,} empty-quantity rows dropped).")

    build_reports(df)
    export_json(df)
    export_collections(df)

    print("\nDone.")


    



