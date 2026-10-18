#!/usr/bin/env python3
"""
Read-only snapshot of the source site's routing pages -> jamaal_routes.json:
  routes        the routes currently assigned ("Today's Routes", routes_assigned.php)
  route_names   every route in the Assign dropdown, by id (assign_routes.php)
  assign_list   the Assign Routes list: active customers and task rows, in page order

The route planner beta (planner.html) and the shadow site (ops-*.html) read it.
The source site stays the system of record: this script only ever GETs two
pages. Never POST, and never request any page whose name suggests an action
(delete_routes.php, update_order.php, ...): those forms appear to work without
a login, so a stray request here would change the real routes.

Assignments are not per-day. A stop stays on its route until someone removes
it, so "today's routes" means "currently assigned", and a stop can sit there
for months (days_over in the hundreds).

    python3 jamaal_routes.py                 # fetch and write the JSON
    python3 jamaal_routes.py --html FILE --assign-html FILE   # parse saved copies

On a fetch or parse failure nothing is written and the exit code is non-zero,
so the previous snapshot stays in place.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://jammal28.dreamhosters.com/tracking"
ROUTES_URL = f"{BASE_URL}/routes_assigned.php"
ASSIGN_URL = f"{BASE_URL}/assign_routes.php"
OUTPUT = "jamaal_routes.json"

# Same identity the main scraper uses (oil_scraper.py).
USER_AGENT = "OilCollectionScraper/1.0"

CAPACITY_RE = re.compile(r"\((\d+)\)")
DAYS_OVER_RE = re.compile(r"\[(-?\d+)\]")
ASSIGNED_RE = re.compile(r"Assigned Capacity:\s*(-?\d+)")


class RoutesParseError(Exception):
    """The page did not look like Today's Routes; the layout probably changed."""


def parse_routes(html):
    """Routes in page order, each with its stops in the site's stop order.

    Page shape (2026-10-17): per route a <div> holding <h4>name</h4>,
    <ol id="ROUTE_ID" class="sortable"> of <li data="CUSTOMER_ID"> stops, and
    <p>Assigned Capacity: N</p>. In each stop the checkbox value is the
    assignment id, an <a class="hotlist"> marks Must Get, then "(capacity)" and
    "[days over periodicity]".
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", action=re.compile(r"delete_routes\.php"))
    if form is None:
        raise RoutesParseError("no route form found: not the Today's Routes page")

    routes = []
    for ol in form.find_all("ol"):
        holder = ol.parent
        heading = holder.find("h4") if holder else None
        if heading is None:
            raise RoutesParseError(f"route list id={ol.get('id')} has no <h4> name")

        stops = []
        for li in ol.find_all("li", recursive=False):
            link = li.find("a", href=re.compile(r"customer\.php\?id=\d+"))
            box = li.find("input", attrs={"type": "checkbox"})
            if link is None or box is None:
                raise RoutesParseError(f"unreadable stop on route {heading.get_text(strip=True)!r}")
            cust = re.search(r"id=(\d+)", link["href"])
            text = li.get_text(" ", strip=True)
            cap = CAPACITY_RE.search(text)
            over = DAYS_OVER_RE.search(text)
            stops.append({
                "assignment_id": int(box["value"]) if str(box.get("value", "")).isdigit() else None,
                "customer_id": int(cust.group(1)),
                "name": link.get_text(" ", strip=True),
                "capacity": int(cap.group(1)) if cap else None,
                "days_over": int(over.group(1)) if over else None,
                "must_get": "hotlist" in (link.get("class") or []),
            })

        assigned = holder.find(string=ASSIGNED_RE)
        route_id = ol.get("id")
        routes.append({
            "route_id": int(route_id) if route_id and route_id.isdigit() else None,
            "name": heading.get_text(" ", strip=True),
            "assigned_capacity": int(ASSIGNED_RE.search(assigned).group(1)) if assigned else None,
            "stops": stops,
        })
    return routes


def parse_assign_page(html):
    """(route_names, assign_list) from the Assign Routes page.

    route_names: [{route_id, name}] from the "route_number" dropdown, in its
    order. Ids are the join key: names differ between pages (route 4 is
    "MamaBear" here and "Stowe/Wtbry/MRV" on Today's Routes, 2026-10-18), and
    one id can appear twice ("Drop Tote" and "Drop tote" are both 15).

    assign_list: [{customer_id, name, city, days_over}] in page order (by city).
    Task rows ("BBB-Load a Barrel", "PUT EXTENDO ON TRUCK") are listed too.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", action=re.compile(r"add_to_route\.php"))
    select = soup.find("select", attrs={"name": "route_number"})
    if form is None or select is None:
        raise RoutesParseError("no assign form or route dropdown: not the Assign Routes page")

    route_names = []
    for opt in select.find_all("option"):
        value = str(opt.get("value", "")).strip()
        if value.isdigit():
            route_names.append({"route_id": int(value), "name": opt.get_text(" ", strip=True)})

    assign_list = []
    for box in form.find_all("input", attrs={"name": "customer[]"}):
        row = box.find_parent("tr")
        link = row.find("a", href=re.compile(r"customer\.php\?id=\d+")) if row else None
        if link is None or not str(box.get("value", "")).isdigit():
            raise RoutesParseError("unreadable row on the Assign Routes page")
        cells = row.find_all("td")
        over = DAYS_OVER_RE.search(row.get_text(" ", strip=True))
        assign_list.append({
            "customer_id": int(box["value"]),
            "name": link.get_text(" ", strip=True),
            "city": cells[-1].get_text(" ", strip=True) if len(cells) >= 3 else "",
            "days_over": int(over.group(1)) if over else None,
        })
    if not assign_list:
        raise RoutesParseError("the Assign Routes page lists no customers")
    return route_names, assign_list


def write_atomic(path, payload):
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--html", help="parse this saved Today's Routes page instead of fetching")
    ap.add_argument("--assign-html", help="parse this saved Assign Routes page instead of fetching")
    ap.add_argument("--out", default=OUTPUT)
    args = ap.parse_args()

    def page(saved, url):
        if saved:
            with open(saved, encoding="utf-8") as fh:
                return fh.read()
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        return resp.text

    try:
        routes = parse_routes(page(args.html, ROUTES_URL))
        route_names, assign_list = parse_assign_page(page(args.assign_html, ASSIGN_URL))
    except (requests.RequestException, RoutesParseError, OSError) as exc:
        print(f"Routes snapshot NOT written: {exc}", file=sys.stderr)
        return 1

    # The site's own "Assigned Capacity" is the sum of its stops' listed
    # capacities; a mismatch means a stop was misread. Warn, don't fail.
    for r in routes:
        listed = sum(s["capacity"] or 0 for s in r["stops"])
        if r["assigned_capacity"] is not None and listed != r["assigned_capacity"]:
            print(f"warning: {r['name']}: stops sum to {listed}, page says "
                  f"{r['assigned_capacity']}", file=sys.stderr)

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "routes_assigned.php, assign_routes.php",
        "routes": routes,
        "route_names": route_names,
        "assign_list": assign_list,
    }
    write_atomic(args.out, payload)
    n_stops = sum(len(r["stops"]) for r in routes)
    print(f"Wrote {args.out}: {len(routes)} routes, {n_stops} stops, "
          f"{len(route_names)} route names, {len(assign_list)} assign rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
