#!/usr/bin/env python3
"""
Read-only copy of the source site's Outgoing Report (dumps.php) -> outgoing.json,
for the shadow site's Outgoing page (ops-outgoing.html).

The report is a month calendar. Each day lists:
  "MB (2)"  two stillage runs by Mamma Bear (MD = Mack Daddy, BB = Baby Bird);
            a run has no volume
  "800"     800 gallons of Schmutz, Black Bear Shop -> Gebbie Farm. This is the
            same material as the Schmutz page's Shop -> Gebbie block; Barr Hill
            -> Gebbie is NOT logged here (Erin keeps it in a separate spreadsheet)

Like jamaal_routes.py this only ever GETs. Never request add_outgoing.php or
outgoing_edit.php: they change the real log.

    python3 jamaal_outgoing.py            # refresh the last 2 months, keep the rest
    python3 jamaal_outgoing.py --all      # rebuild from the first month (Jul 2021)

On a fetch or parse failure nothing is written and the exit code is non-zero.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone

import requests
from bs4 import BeautifulSoup

from jamaal_routes import USER_AGENT, BASE_URL, write_atomic

REPORT_URL = f"{BASE_URL}/dumps.php"
OUTPUT = "outgoing.json"
FIRST_MONTH = (2021, 7)       # the earliest month with any entry (2026-10-18)
DELAY_SEC = 0.3

# The report's truck abbreviations -> the source site's truck keys.
TRUCK_ABBR = {"MB": "mammabear", "MD": "mackdaddy", "BB": "babybird"}

RUNS_RE = re.compile(r"^([A-Z]{2})\s*\((\d+)\)$")
VOLUME_RE = re.compile(r"^\d+(?:\.\d+)?$")
DAY_LINK_RE = re.compile(r"outgoing_detail\.php\?date=(\d{4}-\d{2}-\d{2})")


class OutgoingParseError(Exception):
    """The page did not look like the Outgoing Report; the layout probably changed."""


def parse_month(html):
    """{date: {"stillage": {truck: runs}, "schmutz": [gallons, ...]}} for one report page."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("div", class_="calendar") is None:
        raise OutgoingParseError("no calendar on the page: not the Outgoing Report")
    days = {}
    for link in soup.find_all("a", href=DAY_LINK_RE):
        day = DAY_LINK_RE.search(link["href"]).group(1)
        text = link.get_text(" ", strip=True)
        entry = days.setdefault(day, {"stillage": {}, "schmutz": []})
        runs = RUNS_RE.match(text)
        if runs:
            abbr, n = runs.group(1), int(runs.group(2))
            if abbr not in TRUCK_ABBR:
                raise OutgoingParseError(f"unknown truck abbreviation {abbr!r} on {day}")
            truck = TRUCK_ABBR[abbr]
            entry["stillage"][truck] = entry["stillage"].get(truck, 0) + n
        elif VOLUME_RE.match(text):
            gallons = float(text)
            entry["schmutz"].append(int(gallons) if gallons.is_integer() else gallons)
        else:
            raise OutgoingParseError(f"unreadable entry {text!r} on {day}")
    return days


def months(first, last):
    y, m = first
    while (y, m) <= last:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_month(session, y, m):
    # The report shows the month containing ?startdate; any day in it will do.
    resp = session.get(REPORT_URL, params={"startdate": f"{y:04d}-{m:02d}-15"}, timeout=15)
    resp.raise_for_status()
    return parse_month(resp.text)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--all", action="store_true", help="rebuild every month from Jul 2021")
    ap.add_argument("--recent", type=int, default=2, help="months to refresh (default 2)")
    ap.add_argument("--out", default=OUTPUT)
    args = ap.parse_args()

    today = date.today()
    existing = {}
    if not args.all and os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as fh:
            existing = json.load(fh).get("days", {})
    if args.all or not existing:
        todo = list(months(FIRST_MONTH, (today.year, today.month)))
    else:
        todo = list(months(FIRST_MONTH, (today.year, today.month)))[-args.recent:]

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    days = dict(existing)
    try:
        for i, (y, m) in enumerate(todo):
            prefix = f"{y:04d}-{m:02d}-"
            fresh = fetch_month(session, y, m)
            # A refreshed month replaces what was kept for it, so deletions arrive too.
            days = {d: v for d, v in days.items() if not d.startswith(prefix)}
            days.update({d: v for d, v in fresh.items() if d.startswith(prefix)})
            if i < len(todo) - 1:
                time.sleep(DELAY_SEC)
    except (requests.RequestException, OutgoingParseError) as exc:
        print(f"Outgoing NOT written: {exc}", file=sys.stderr)
        return 1

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "dumps.php",
        "trucks": TRUCK_ABBR,
        "days": dict(sorted(days.items())),
    }
    write_atomic(args.out, payload)
    runs = sum(sum(v["stillage"].values()) for v in days.values())
    gallons = sum(sum(v["schmutz"]) for v in days.values())
    print(f"Wrote {args.out}: {len(todo)} month(s) read, {len(days)} days, "
          f"{runs} stillage runs, {gallons:,.0f} gal Schmutz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
