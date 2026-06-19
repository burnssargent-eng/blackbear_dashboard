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
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
import json

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BASE_URL     = "https://jammal28.dreamhosters.com/tracking"
LIST_URL     = f"{BASE_URL}/assign_routes.php"
INACTIVE_URL = f"{BASE_URL}/inactive.php"
DELAY_SEC    = 0.3

# Quantities meaning "we collected nothing" — excluded from all totals
EMPTY_QTYS = {0, 1, 3}

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
    "Albany":            "Out-of-State (NY)",
    "Plattsburgh":       "Out-of-State (NY)",
    "Claremont":         "Out-of-State (NH)",
    "Hanover":           "Out-of-State (NH)",
    "West Lebanon":      "Out-of-State (NH)",
    "Pawtucket":         "Out-of-State (RI)",
    "Dalton":            "Out-of-State (NH)",
    "Belmont":           "Out-of-State (NH)",
}

# Longest-first helps avoid partial matches like Essex before Essex Junction.
KNOWN_CITIES = sorted(COUNTY_MAP.keys(), key=len, reverse=True)

# ─────────────────────────────────────────────
# CUSTOM SUBREGION DEFINITIONS
# ─────────────────────────────────────────────
UVM_IDS = {353, 354, 355, 356, 358}

CUSTOM_REGIONS = [
    ("Stowe", lambda r: r["city"] == "Stowe"),
    ("Waterbury", lambda r: r["city"] == "Waterbury"),
    ("Warren+Waitsfield", lambda r: r["city"] in ("Warren", "Waitsfield")),
    ("Middlebury+Vergennes", lambda r: r["city"] in ("Middlebury", "Vergennes")),
    ("Jay+Montgomery+Troy", lambda r: r["city"] in ("Jay", "Montgomery", "Montgomery Center", "North Troy")),
    ("Winooski", lambda r: r["city"] == "Winooski"),
    ("UVM", lambda r: r["customer_id"] in UVM_IDS),
    ("Woodstock+Quechee", lambda r: r["city"] in ("Woodstock", "Quechee")),
    ("Mt. Snow", lambda r: any(x in str(r["name"]).lower() for x in ("mt. snow", "mt snow", "mount snow"))),
    ("Manchester", lambda r: r["city"] in ("Manchester", "Manchester Center")),
    ("Okemo", lambda r: "okemo" in str(r["name"]).lower()),
    ("Central Vermont", lambda r: r["city"] in ("Montpelier", "Barre", "Berlin", "Northfield")),
    ("Burlington", lambda r: r["city"] == "Burlington" and r["customer_id"] not in UVM_IDS),
]

REGION_NAMES = [name for name, _ in CUSTOM_REGIONS]

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
        }

    years_covered = set()
    seen = set()

    # NEW FORMAT: MM/DD/YYYY : number
    for match in re.finditer(r"(\d{2}/\d{2}/\d{4})\s*:\s*(\d+)", text):
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
    print(f"\nScraping {len(customers)} total customers...\n")

    all_records = []
    unresolved_city_ids = []

    for i, cust in enumerate(customers, 1):
        tag = "INACTIVE" if cust["customer_id"] not in seen_ids else "active  "
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
            make_pivot(ydf, "month", "region", columns=REGION_NAMES).to_excel(
                writer,
                sheet_name=str(year),
            )

        # Individual subregion detail sheets
        for sheet_name, matcher in CUSTOM_REGIONS:
            mask = df.apply(matcher, axis=1)
            sub = df[mask]
            if sub.empty:
                print(f"  WARNING: no data matched for '{sheet_name}'")
                continue

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
    
    
def export_json(df):
    """Export clean JSON for the website to consume."""
    
    # Monthly totals by town
    monthly_town = (
        df.groupby(["month", "city"])["gallons"]
        .sum()
        .reset_index()
    )
    
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
    
    # Current year month-by-month
    current_year = datetime.today().year
    current = df[df["year"] == current_year]
    
    output = {
        "last_updated":    datetime.now().isoformat(),
        "monthly_by_town": monthly_town.to_dict(orient="records"),
        "monthly_by_county": monthly_county.to_dict(orient="records"),
        "yearly_totals":   yearly.to_dict(orient="records"),
        "all_time_total":  int(df["gallons"].sum()),
        "current_year_total": int(current["gallons"].sum()),
        "customer_count":  df["customer_id"].nunique(),
    }
    
    with open("oil_data.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    
    print("Exported oil_data.json")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Oil Collection Scraper")
    print("=" * 55)

    if os.path.exists("oil_collections_raw.csv"):
        print("Loading existing oil_collections_raw.csv (delete to re-scrape).\n")
        df = pd.read_csv("oil_collections_raw.csv", parse_dates=["date"])
    else:
        df = scrape_all()

    build_reports(df)
    export_json(df)
    print("\nDone.")


    



