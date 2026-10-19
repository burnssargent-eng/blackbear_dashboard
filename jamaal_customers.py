#!/usr/bin/env python3
"""
Addresses and map positions for the source site's active customers ->
customer_details.json, for the shadow site's customer pages and route maps.

For each customer on the Assign Routes list (jamaal_routes.json) it reads the
customer page (GET only) and keeps:
  address  the street address behind the page's Google Maps link
  lat/lon  the site's own stored position when it has one ("site"); otherwise
           the US Census geocoder's match for the address ("census"); otherwise
           the centre of the customer's town ("town")
  notes    the page's Notes line (directions, where the barrel is)
  photos   file names of the barrel-location photos in the site's uploads/
           folder; the shadow site shows them from there, nothing is copied
Phone numbers are never kept.

PRIVACY: the repo is public, so this file publishes addresses. Sarge accepted
that on 2026-10-18 because the same addresses are public on the source site;
they move with the repo when it goes private.

    python3 jamaal_customers.py            # only customers not yet in the file
    python3 jamaal_customers.py --refresh  # re-read every customer page

A failed page keeps that customer's old entry. On a broken page layout nothing
is written and the exit code is non-zero.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import requests

from jamaal_routes import BASE_URL, USER_AGENT, write_atomic

OUTPUT = "customer_details.json"
# Full notes, codes and all, for a LOCAL preview only. Gitignored: it must never
# be committed or published. Only notes that scrub() changed are kept.
LOCAL_NOTES = "customer_notes.local.json"
ROUTES_FILE = "jamaal_routes.json"
PROJECTIONS_FILE = "oil_projections.json"
TOWNS_FILE = "vermont_towns.geojson"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
DELAY_SEC = 0.3

LATLON_RE = re.compile(r'latlon\s*=\s*"(-?[\d.]*),(-?[\d.]*)"')
PLACE_RE = re.compile(r'maps/place/([^"\']*)')
NOTES_RE = re.compile(r"Notes:(.*?)<br", re.S)
PHOTO_RE = re.compile(r"src='uploads/([^']+)'")


class CustomerPageError(Exception):
    """The page did not look like a customer page; the layout probably changed."""


# Notes are published (the repo is public), so phone numbers and door / lock
# codes are cut out of them: "Passcode 1234" -> "Passcode [on source site]".
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}\b")
CODE_RE = re.compile(r"\b((?:pass\s*code|passcode|pass|code|combo|combination|pin|key\s*pad|keypad|alarm)"
                     r"\s*(?:#|no\.?|is|:)?\s*)[\w-]*\d[\w-]*", re.I)
REDACTED = "[on source site]"


def scrub(text):
    return CODE_RE.sub(lambda m: m.group(1) + REDACTED, PHONE_RE.sub(REDACTED, text))


def raw_notes(html):
    m = NOTES_RE.search(html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def parse_extras(html):
    """(notes with phones and codes cut out, photo file names) from a customer page."""
    notes = scrub(raw_notes(html))
    photos = list(dict.fromkeys(PHOTO_RE.findall(html)))
    return notes, photos


def parse_customer_page(html):
    """(address or "", lat or None, lon or None). The site stores "lon,lat"."""
    if "initMap" not in html and "maps/place/" not in html:
        raise CustomerPageError("no map or address on the page: not a customer page")
    place = PLACE_RE.search(html)
    address = ""
    if place:
        address = re.sub(r"\s+", " ", unquote_plus(place.group(1))).strip(" ,")
        # "4278 Route 101, North Troy, VT, " and ", Burlington, VT, 05401" both occur.
        address = re.sub(r"(,\s*)+", ", ", address).strip(" ,")
    lat = lon = None
    m = LATLON_RE.search(html)
    if m and m.group(1) and m.group(2):
        lon, lat = float(m.group(1)), float(m.group(2))
    return address, lat, lon


def has_street(address):
    """True when the address starts with a house number or a road, not just a town."""
    return bool(address) and not address.startswith(",") and bool(re.match(r"\s*\w", address)) \
        and len(address.split(",")) >= 3


# Where customers can be: Vermont and the edges of NY, NH and MA. The site writes
# ", VT" on every address, so a New Hampshire street can match one in Texas.
REGION = {"lat": (42.0, 45.5), "lon": (-74.5, -70.5)}


def in_region(lat, lon):
    return REGION["lat"][0] < lat < REGION["lat"][1] and REGION["lon"][0] < lon < REGION["lon"][1]


def census_geocode(session, address):
    """(lat, lon) from the US Census geocoder, or None when it finds no match in the region."""
    resp = session.get(CENSUS_URL, params={"address": address, "benchmark": "Public_AR_Current",
                                           "format": "json"}, timeout=20)
    resp.raise_for_status()
    matches = resp.json().get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    c = matches[0]["coordinates"]
    if not in_region(c["y"], c["x"]):
        return None
    return round(c["y"], 6), round(c["x"], 6)


def town_centres():
    """Town name -> (lat, lon): the mean of each town outline's points."""
    with open(TOWNS_FILE, encoding="utf-8") as fh:
        geo = json.load(fh)
    out = {}
    for f in geo["features"]:
        name = f["properties"].get("TOWNNAMEMC") or f["properties"].get("TOWNNAME")
        geom = f["geometry"]
        rings = geom["coordinates"] if geom["type"] == "Polygon" else [r for p in geom["coordinates"] for r in p]
        pts = [pt for ring in rings for pt in ring]
        if name and pts:
            out[name.lower()] = (round(sum(p[1] for p in pts) / len(pts), 5),
                                 round(sum(p[0] for p in pts) / len(pts), 5))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true", help="re-read every customer page")
    ap.add_argument("--ids", help="comma-separated customer ids to re-read")
    ap.add_argument("--out", default=OUTPUT)
    args = ap.parse_args()

    with open(ROUTES_FILE, encoding="utf-8") as fh:
        assign = json.load(fh)["assign_list"]
    with open(PROJECTIONS_FILE, encoding="utf-8") as fh:
        towns_by_id = {}
        for c in json.load(fh)["customers"]:
            for cid in [c["id"]] + [m["id"] for m in (c.get("members") or [])]:
                towns_by_id.setdefault(cid, c.get("town") or "")
    centres = town_centres()

    existing = {}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as fh:
            existing = json.load(fh).get("customers", {})

    only = {i.strip() for i in args.ids.split(",")} if args.ids else None
    todo = [a for a in assign if (str(a["customer_id"]) in only if only is not None
                                  else args.refresh or str(a["customer_id"]) not in existing)]
    local_notes = {}
    if os.path.exists(LOCAL_NOTES):
        with open(LOCAL_NOTES, encoding="utf-8") as fh:
            local_notes = json.load(fh)
    site = requests.Session()
    site.headers["User-Agent"] = USER_AGENT
    census = requests.Session()
    customers = dict(existing)
    counts = {"site": 0, "census": 0, "town": 0, None: 0}
    failed = 0
    try:
        for i, a in enumerate(todo):
            cid = str(a["customer_id"])
            try:
                resp = site.get(f"{BASE_URL}/customer.php", params={"id": cid}, timeout=15)
                resp.raise_for_status()
            except requests.RequestException:
                failed += 1
                continue
            address, lat, lon = parse_customer_page(resp.text)
            loc = "site" if lat is not None else None
            old = existing.get(cid, {})
            if loc is None and has_street(address):
                if old.get("loc") == "census" and old.get("address") == address:
                    lat, lon, loc = old["lat"], old["lon"], "census"
                else:
                    try:
                        hit = census_geocode(census, address)
                    except requests.RequestException:
                        hit = None
                    if hit:
                        (lat, lon), loc = hit, "census"
            if loc is None:
                town = (towns_by_id.get(a["customer_id"]) or a.get("city") or "").lower()
                if town in centres:
                    (lat, lon), loc = centres[town], "town"
            counts[loc] += 1
            notes, photos = parse_extras(resp.text)
            full = raw_notes(resp.text)
            if full != notes:
                local_notes[cid] = full
            else:
                local_notes.pop(cid, None)
            customers[cid] = {"address": address, "lat": lat, "lon": lon, "loc": loc,
                              "notes": notes, "photos": photos}
            if i < len(todo) - 1:
                time.sleep(DELAY_SEC)
    except CustomerPageError as exc:
        print(f"Customer details NOT written: {exc}", file=sys.stderr)
        return 1

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "customer.php (address, stored position); US Census geocoder; town centres",
        "customers": dict(sorted(customers.items(), key=lambda kv: int(kv[0]))),
    }
    write_atomic(args.out, payload)
    write_atomic(LOCAL_NOTES, dict(sorted(local_notes.items(), key=lambda kv: int(kv[0]))))
    print(f"Wrote {args.out}: {len(customers)} customers; read {len(todo)} page(s) "
          f"({counts['site']} site position, {counts['census']} geocoded, {counts['town']} town centre, "
          f"{counts[None]} no position, {failed} failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
