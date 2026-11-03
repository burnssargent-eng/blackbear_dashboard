#!/usr/bin/env python3
"""
Customer page edits (Sarge, 2026-11-02): what the nightly exports to
customer_edits.csv, which addresses get a new pin, and how the scraper reads a
site town. No network, no database.

    python3 tests/customer_edits_test.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import oil_scraper as scraper  # noqa: E402
import supabase_sync as sync  # noqa: E402

fails = 0


def ok(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    fails += not cond


rows = [
    {"id": 3, "site_name": None, "site_town": None, "site_capacity": None},
    {"id": 1, "site_name": " Mad Moose ", "site_town": None, "site_capacity": None},
    {"id": 2, "site_name": None, "site_town": "Barre Town", "site_capacity": 0},
    {"id": -5, "site_name": "Added here", "site_town": "Winooski", "site_capacity": 40},
    {"id": 4, "site_region": "Route 7", "site_themes": [], "site_status": "inactive", "site_status_since": "2026-11-01"},
    {"id": 6, "site_themes": ["Ski Slopes", "Summer Snack Stops"]},
]
ok(sync.edit_lines(rows) == [[1, "Mad Moose", "", "", "", "", "", ""], [2, "", "Barre Town", 0, "", "", "", ""],
                             [4, "", "", "", "Route 7", "none", "inactive", "2026-11-01"],
                             [6, "", "", "", "", "Ski Slopes;Summer Snack Stops", "", ""]],
   "export: edited source-site customers only, by id, trimmed; a 0 capacity is kept; an empty theme list is 'none'")
ok(sync.lapsed_statuses([{"id": 1, "site_status": "inactive", "site_status_since": "2026-10-01"},
                         {"id": 2, "site_status": "closed_season", "site_status_since": "2026-10-01"},
                         {"id": 3, "site_status": "open", "site_status_since": "2026-10-05"}],
                        {1: "2026-10-02", 2: "2026-10-01", 3: "2026-09-30"}) == [1],
   "a pickup after a status's day ends it; one on the day or before does not")

pins = sync.pins_wanted([
    {"id": 1, "address": "2 B St", "jamaal_address": "1 A St, Barre, VT", "loc": None},   # edited here
    {"id": 2, "address": "1 A St, Barre, VT", "jamaal_address": "1 A St, Barre, VT", "loc": None},   # never placed
    {"id": 3, "address": "2 B St", "jamaal_address": "1 A St", "loc": "census"},   # already placed
    {"id": -4, "address": "9 C Rd", "jamaal_address": None, "loc": None},   # added on the site
    {"id": 5, "address": "  ", "jamaal_address": "x", "loc": None},
])
ok([r["id"] for r in pins] == [-4, 1], "pins: an edited address and a site-added customer; never-placed and placed ones left alone")

ok(sync.pin_query({"address": "12 Main St, Barre, VT 05641", "city": "Barre"}) == "12 Main St, Barre, VT 05641",
   "geocode a full address as it is")
ok(sync.pin_query({"address": "12 Main St", "city": "Burlington", "site_town": "Winooski"}) == "12 Main St, Winooski, VT",
   "a bare street takes the site town and VT")
ok(sync.pin_query({"address": "4 Elm St", "city": "Burlington", "site_town": "Berlin, NH"}) == "4 Elm St, Berlin, NH",
   "an out-of-state town carries its own state")
ok(sync.pin_query({"address": "behind the diner", "city": "Barre"}) is None, "no house number: no street to geocode (town centre)")

saved = scraper.SITE_EDITS
try:
    scraper.SITE_EDITS = {1506: {"name": "", "town": "Barre Town", "capacity": ""},
                          119: {"name": "Thunder Road Speedbowl", "town": "Lancaster, NH", "capacity": ""}}
    ok(scraper.normalize_geo_town("Burlington", 1506) == "Barre Town" and scraper._out_of_state_place(1506) is None,
       "a site town beats OUT_OF_STATE_BY_CUSTOMER")
    ok(scraper.normalize_geo_town("Barre", 119) is None and scraper._out_of_state_place(119) == "Lancaster, NH"
       and scraper._customer_county("Barre", 119) == "Out-of-State (NH)",
       "a site out-of-state place beats CITY_TOWN_BY_CUSTOMER, county included")
    ok(scraper._customer_county("Burlington", 1506) == "Washington", "a site town's county comes from the town map")
    ok(scraper.site_name(119, "Thunder Road") == "Thunder Road Speedbowl" and scraper.site_name(1506, "NH Pen") == "NH Pen",
       "the roster takes a site name, else the scraped one")
    scraper.SITE_EDITS = {1496: {"region": "Central", "themes": "Ski Slopes"}, 901: {"region": "", "themes": "none"}}
    ok(scraper.region_labels({"customer_id": 1496, "geo_town": "New Haven", "city": "Middlebury"})
       == ["Central", "Ski Slopes", "Winter / Summer Combined"],
       "a site region replaces the town's (Route 7), a site theme is added, the composite follows")
    ok(scraper.region_labels({"customer_id": 901, "geo_town": "Dover", "city": "West Dover"})
       == ["Southern Ski Slopes", "South / Ski Combined"],
       "themes 'none' takes a ski slope out of Ski Slopes and Winter / Summer, its town region stays")
    ok(scraper.region_labels({"customer_id": 901, "geo_town": "Dover", "city": "West Dover"})
       == scraper.region_labels({"customer_id": 901, "geo_town": "Dover", "city": "West Dover"}), "stable")
    scraper.SITE_EDITS = {}
    ok("Ski Slopes" in scraper.region_labels({"customer_id": 901, "geo_town": "Dover", "city": "West Dover"}),
       "without edits the config stands")
    ok(scraper.normalize_geo_town("Barre", 119) == "Barre Town" and scraper._customer_county("Barre", 119) == "Washington",
       "without edits the registry stands, and Barre Town is no longer an Unknown county")
finally:
    scraper.SITE_EDITS = saved

# Builder: what a status marked on the page does (analysis/build_projection_table.py).
sys.path.insert(0, os.path.join(ROOT, "analysis"))
import build_projection_table as bpt  # noqa: E402
from datetime import date  # noqa: E402
p = [{"date": date(2026, 7, 1), "gallons": 50, "name": "x"}, {"date": date(2026, 9, 1), "gallons": 50, "name": "x"}]
asof = date(2026, 11, 2)
hold, reset = bpt.site_status_plan(5, p, asof, {"status": "inactive", "status_since": "2026-10-01"})
ok(hold and hold[1] == bpt.SECTION_INACTIVE and reset is None, "inactive: held in its own section")
ok(bpt.site_status_plan(5, p, asof, {"status": "inactive", "status_since": "2026-08-01"}) == (None, None),
   "a pickup after the day ends it")
ok(bpt.site_status_plan(5, p, asof, {"status": "open", "status_since": "2026-10-20"}) == (None, date(2026, 10, 20)),
   "open for the season: the oil clock starts that day")
hold, reset = bpt.site_status_plan(1059, p, asof, {"status": "closed_season", "status_since": "2026-10-20"})
ok(hold and hold[1] == bpt.SECTION_SEASONAL and "2027-06-01" in hold[2], "closed: held until its usual season start (Jun)")
ok(bpt.site_status_plan(1059, p, date(2027, 6, 10), {"status": "closed_season", "status_since": "2026-10-20"})
   == (None, date(2027, 6, 1)), "after the season start it projects again, its clock from that day")
hold, _ = bpt.site_status_plan(5, p, asof, {"status": "closed_season", "status_since": "2026-10-20"})
ok(hold and "no usual season" in hold[2], "no known season: held until marked open or a pickup")
ok(bpt.next_season_start("May-Oct", date(2026, 3, 1)) == date(2026, 5, 1)
   and bpt.next_season_start("Nov-Apr", date(2026, 11, 15)) == date(2027, 11, 1), "the next season start after a day")
print(f"\n{fails} FAILURE(S)" if fails else "\nALL CHECKS PASSED")
sys.exit(1 if fails else 0)
