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
    {"id": 7, "site_model": "seasonal", "site_season": "May-Oct"},
    {"id": 8, "site_model": "will_call", "site_season": None},
]
ok(sync.edit_lines(rows) == [[1, "Mad Moose", "", "", "", "", "", "", "", ""],
                             [2, "", "Barre Town", 0, "", "", "", "", "", ""],
                             [4, "", "", "", "Route 7", "none", "inactive", "2026-11-01", "", ""],
                             [6, "", "", "", "", "Ski Slopes;Summer Snack Stops", "", "", "", ""],
                             [7, "", "", "", "", "", "", "", "seasonal", "May-Oct"],
                             [8, "", "", "", "", "", "", "", "will_call", ""]],
   "export: edited source-site customers only, by id, trimmed; a 0 capacity is kept; an empty theme list is 'none'; "
   "a projection type and its season")
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
# Projection type set on the customer page (Sarge, 2026-11-12): it replaces the
# model's choice and any MODEL_OVERRIDES entry; a shared member's lands on its group.
types = bpt.site_overrides({1503: {"model": "will_call", "season": ""},
                            836: {"model": "seasonal", "season": "Jun-Sep"},
                            131: {"model": "event", "season": ""},
                            7: {"model": "seasonal", "season": ""},
                            8: {"model": "", "season": ""}})
ok(sorted(types) == [130, 836, 1503], "types: a shared member's on its group (131 -> 130); seasonal without months, or none, ignored")
ok(types[1503].status == bpt.STATUS_SITE_WILL_CALL and types[836] == bpt.Override(
    bpt.STATUS_SEMI_CLOSER, None, "Jun-Sep", "marked seasonal on the site (Jun-Sep)")
   and types[130].status == bpt.STATUS_EVENT, "will-call, seasonal as a semi-closer with its months, event-driven")
saved_types = dict(bpt.SITE_OVERRIDES)
try:
    bpt.SITE_OVERRIDES.clear()
    bpt.SITE_OVERRIDES.update(types)
    ok(bpt.override_for(836).status == bpt.STATUS_SEMI_CLOSER and bpt.override_for(413).status == bpt.STATUS_EVENT
       and bpt.override_for(2) is None, "the page's type beats the registry's; the registry stands where none is set")
    few = [{"date": date(2026, 5, 27), "gallons": 60, "name": "Way Out Wax", "town": "Burlington"}]
    ok(bpt.route(types[1503], False, 30, date(2026, 5, 27), few, date(2026, 6, 26))
       == (bpt.STATUS_SITE_WILL_CALL, bpt.SECTION_WILL_CALL, "marked will-call on the site"),
       "marked will-call: listed as will-call even with one pickup")
    ok(bpt.route(types[1503], True, 200, date(2026, 5, 27), few, date(2026, 12, 13))[1] == bpt.SECTION_STALE,
       "marked will-call but silent past STALE_DAYS: stale, as a detected one")
    # Seasonal set on the page, off-season: held unless a pickup came after the season closed.
    arl = bpt.Override(bpt.STATUS_SEMI_CLOSER, None, "May-Oct", "marked seasonal on the site (May-Oct)")
    summer = [{"date": date(2026, 7, 15), "gallons": 60, "name": "x", "town": "x"},
              {"date": date(2026, 9, 10), "gallons": 60, "name": "x", "town": "x"}]
    ok(bpt.season_closed_on("May-Oct", date(2026, 11, 12)) == date(2026, 11, 1)
       and bpt.season_closed_on("May-Oct", date(2027, 2, 1)) == date(2026, 11, 1)
       and bpt.season_closed_on("Nov-Apr", date(2026, 7, 1)) == date(2026, 5, 1)
       and bpt.season_closed_on("Jun-Dec", date(2027, 3, 1)) == date(2027, 1, 1)
       and bpt.season_closed_on("May-Oct", date(2026, 7, 1)) is None, "the day a season last closed")
    ok(bpt.route(arl, True, 63, date(2026, 9, 10), summer, date(2026, 11, 12))[1] == bpt.SECTION_SEASONAL,
       "page seasonal, off-season, last pickup in season: held (Arlington Dairy Bar)")
    called = summer + [{"date": date(2026, 11, 5), "gallons": 30, "name": "x", "town": "x"}]
    ok(bpt.route(arl, True, 7, date(2026, 11, 5), called, date(2026, 11, 12))[1] == bpt.SECTION_RANKED,
       "page seasonal, off-season, a pickup after it closed: ranked")
    coded = bpt.MODEL_OVERRIDES[803]
    ok(bpt.route(coded, True, 20, date(2026, 10, 23), summer[:1] + [{"date": date(2026, 10, 23), "gallons": 60,
       "name": "x", "town": "x"}], date(2026, 11, 12))[1] == bpt.SECTION_RANKED,
       "a coded semi-closer keeps the recent-call threshold")
finally:
    bpt.SITE_OVERRIDES.clear()
    bpt.SITE_OVERRIDES.update(saved_types)

# Thefts marked on the site (Sarge, 2026-11-12): exported without notes, read by the builder as THEFTS.
ok(sync.theft_lines([{"customer_id": 1188, "picked_on": "2026-11-06", "gallons": 1, "theft_note": "x"},
                     {"customer_id": 325, "picked_on": "2026-10-21", "gallons": 0}])
   == [(325, "2026-10-21", 0), (1188, "2026-11-06", 1)], "theft export: id, day, gallons; never the note")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
saved_file = bpt.SITE_THEFTS
try:
    with tempfile.TemporaryDirectory() as tmp:
        bpt.SITE_THEFTS = Path(tmp) / "thefts.csv"
        ok(bpt.theft_registry() == bpt.THEFTS, "no thefts.csv: the registry alone")
        bpt.SITE_THEFTS.write_text("customer_id,date,collected\n1188,2026-11-06,1\n325,2026-10-21,1\n")
        reg = bpt.theft_registry()
        ok(reg[(1188, date(2026, 11, 6))].name is None and reg[(325, date(2026, 10, 21))] == bpt.THEFTS[(325, date(2026, 10, 21))],
           "a site theft joins the registry; the registry's entry wins for the same day")
        hist = {1188: [{"date": date(2026, 10, 22), "gallons": 90, "name": "Windjammer", "town": "x"},
                       {"date": date(2026, 11, 20), "gallons": 40, "name": "Windjammer", "town": "x"}]}
        found, flags = bpt.apply_thefts(hist, date(2026, 11, 30), reg)
        ok([d for d, _ in found[1188]] == [date(2026, 11, 6)] and hist[1188][1].get("theft_gap") == date(2026, 11, 6)
           and not flags.get(1188), "a site theft marks the gap it spans, with no name flag")
finally:
    bpt.SITE_THEFTS = saved_file

print(f"\n{fails} FAILURE(S)" if fails else "\nALL CHECKS PASSED")
sys.exit(1 if fails else 0)
