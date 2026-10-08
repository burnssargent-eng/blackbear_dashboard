#!/usr/bin/env python3
"""
Operator-facing oil projection table for active customers.

The question this answers, per customer: how much oil is probably sitting in
the container right now, and how close is that to the point we want to collect?

    rate      = 0.5 x previous-year rate  +  0.5 x last-6-pickups rate
    projected = rate x days since the last pickup
    target    = 0.75 x listed capacity          (the intended pickup point,
                                                 leaving a spillover buffer)

Both component rates come from analysis/backtest_steady_rate.py, where the
50/50 blend was chosen (see analysis/README.md). Nothing here is re-derived:
the last-6 rate calls bt.rate_rolling directly, and the previous-year rate is
the same formula keyed to a date instead of a pickup index -- see
rate_prev_year_asof below.

WHAT THIS IS NOT. The projection estimates oil PRODUCED since the last pickup.
It is not a promise of how many gallons the truck will collect: that depends on
route timing, container count, partial pickups and data quality. Treat it as a
queue ordering, not a forecast of volume.

THE LADDER (since 2026-10-08; replay_newcomers.md). Every customer is placed
automatically, so a new account needs no manual survey:

    insufficient  fewer than 3 pickups         no projection
    new           3+ pickups, no prev-year     pooled rate over at most the last
                                               6 gaps (never the FIRST pickup's
                                               gallons: oil may predate the barrel)
    established   a previous-year rate exists  0.5 x pooled + 0.5 x previous year
                                               (= the 50/50 above once 7+ pickups)
    seasonal      seasonal_open.classify       average of Sarge's 70/30 (last year
                  passes (prior years only)    around the date / last 2 pickups) and
                                               last 3 pickups re-timed by the month
                                               index (seasonal_formulas.md)
    closer in     a registry closer, or one    pooled rate over open-season gaps
      season      seasonal_open.closed_months  (seasonal_closed.md); detected
                  detects, in its season       closers route like semi-closers
    will-call     long or irregular gaps       no projection, ever

The will-call detector (median gap > 120 days, or gap sd/mean > 0.8 once gaps
over 3x the median -- a closer's off-season -- are dropped) runs only for
customers NOT in MODEL_OVERRIDES: a human decision always wins. Customers with
no pickup in STALE_DAYS still get every column but are held out of the urgency
ranking, because rate x days grows without limit and would otherwise fill the
top of the table.

Monthly indices enter the projection ONLY for seasonal-stage customers, and
only on a rate with the season divided out first. Multiplying the 50/50 by an
index double-counts the season -- measured and rejected in
customer_factor_model_test.md. Seasonal formulas HURT customers whose swing is
not strong and repeatable (seasonal_formulas.md), so the stage stays strict.

Capacity flags are review prompts. A listed capacity is NEVER overwritten.

Some customers are routed AROUND the 50/50 ranking by MODEL_OVERRIDES, an
explicit, human-reviewed registry keyed by customer id: ski closers held out of
the ranking while closed, call-driven seasonal accounts held out while silent,
event venues and bulk on-demand accounts separated entirely. The overrides only
decide which section a row lands in and which columns are shown; the 50/50
arithmetic itself is the same for every row. build_fringe_seasonal_candidates
informs the registry but is never read as input -- its labels are a sorting
tool, not a verdict.

Reads oil_collections_raw.csv, which already holds only qualifying pickups:
EMPTY_QTYS {0,1,2,3} were removed upstream by oil_scraper.py and 4-gallon
records are retained as exactly 4. bt.load_pickups ASSERTS that rather than
re-filtering, so this script can never quietly diverge from the cleaning rule.

All percentage fields are written in percent units: 23.4 means 23.4%.

    python3 analysis/build_projection_table.py
    python3 analysis/build_projection_table.py --as-of 2026-06-30

    python3 analysis/build_projection_table.py --refresh-wape

Writes analysis/oil_projection_table.csv, .md and _detail.csv, plus ONE file
outside analysis/: oil_projections.json at the repo root, which projections.html
reads. The nightly workflow runs this after the scraper. The confidence bands
come from the committed analysis/customer_wape.json, never the gitignored
backtest CSVs, so the nightly and a local run agree.
"""

import argparse
import csv
import json
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import build_fringe_seasonal_candidates as fringe
import customer_factors as cf
import seasonal_open
import seasonality_score as ss

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

COLLECTIONS = ROOT / "oil_collections.json"
OIL_DATA = ROOT / "oil_data.json"
CAPACITY_CACHE = ROOT / "capacity_cache.csv"
NON_PICKUPS = ROOT / "oil_non_pickups.csv"

# Quantities that mean "checked, container empty": the oil clock restarts at
# the latest one after the last pickup. MUST MATCH oil_scraper.RESET_QTYS;
# validate_data.py asserts it. Measured 2026-10-05: on 651 intervals with a
# check between two pickups, counting from the pickup over-projected the next
# pickup by +115%, counting from the check by +11%.
RESET_QTYS = {0, 1}
ROUTING_WAPE = HERE / "routing_rule_test_customers.csv"
SEASONAL_DETAIL = HERE / "backtest_seasonal_models_detail.csv"
# Committed snapshot of the two backtests above, so the nightly runner -- which
# has neither CSV -- builds the same confidence bands as a local run.
WAPE_SNAPSHOT = HERE / "customer_wape.json"

# The one file this script writes outside analysis/: the website's input.
OUT_JSON = ROOT / "oil_projections.json"

OUT_MAIN = HERE / "oil_projection_table.csv"
OUT_REPORT = HERE / "oil_projection_table.md"
OUT_DETAIL = HERE / "oil_projection_table_detail.csv"

# The pickup point: 75% of listed capacity, leaving a spillover buffer.
TARGET_FRACTION = 0.75

WINDOW = 6                      # pickups in the rolling rate
YEAR_DAYS = bt.YEAR_DAYS        # 365, matching the backtests

# No pickup in this many days: every column is still computed, but the row is
# held out of the urgency ranking. is_active only means "not dormant since
# 2025-01-01", so it admits customers years past their last pickup.
STALE_DAYS = 180

# The ladder (replay_newcomers.md, 2026-10-08).
NEW_MIN_PICKUPS = 3             # the first is a starting point: 3 pickups = 2 gaps
STAGE_NEW = "new"
STAGE_ESTABLISHED = "established"
STAGE_SEASONAL = "seasonal"
STAGE_CLOSER = "closer in season"

# Will-call: chosen on 2021-23 gaps, confirmed on 2024-26.
WILL_CALL_WINDOW_DAYS = 3 * 365
WILL_CALL_MEDIAN_GAP = 120      # days
WILL_CALL_GAP_CV = 0.8          # sd / mean of the gaps that are not closures
WILL_CALL_CLOSURE_MULTIPLE = 3  # a gap over 3x the median is a closure

# Likely range for rows WITHOUT a backtested WAPE of their own: projection x
# the 20th and 80th percentiles of actual / projected in the replay's choose
# period, by stage and measured gaps. Replaces a flat +/-20%, which real
# newcomer error (40-55% WAPE) made far too narrow. Seasonal rows use the
# established factors (too few replay pickups for their own).
RANGE_FACTORS = {
    "new, 2 gaps": (0.60, 1.64),
    "new, 3 gaps": (0.71, 1.49),
    "new, 4+ gaps": (0.70, 1.35),
    STAGE_ESTABLISHED: (0.75, 1.45),
    STAGE_CLOSER: (0.62, 1.64),     # seasonal_closed.md, open-season rate
}

# A closer's in-season rate needs at least this many open-season gaps;
# with fewer it keeps its stage's rate.
CLOSER_MIN_OPEN_GAPS = 3

RECENT_PICKUPS = 10             # "recent" for the strong capacity flag
SOFT_FLAG_YEARS = 2             # "recent" for the soft capacity flag

# Complete past calendar years for collections_per_year. 2020 is excluded on
# purpose: 1,449 pickups against 1,863 in 2019 and 1,895 in 2021, a real dip
# that would drag a customer's cadence down. The current year is excluded as
# incomplete.
COMPLETE_YEARS = (2023, 2024, 2025)

MONTHS = ss.MONTHS

FLAG_OVER_CAPACITY = "Collection exceeds listed capacity - check capacity"
FLAG_CAPACITY_STALE = "Capacity likely stale/wrong"
FLAG_CAPACITY_MISSING = "Capacity missing"
FLAG_CAPACITY_LOW = "Capacity below typical pickup"
FLAG_ABOVE_CAPACITY = "Projected above listed capacity — review capacity/model."

SECTION_RANKED = "ranked"
SECTION_SEASONAL = "seasonal holdout"
SECTION_EVENT = "event-driven"
SECTION_ON_DEMAND = "on-demand / unlimited"
SECTION_LUMP_SUM = "lump-sum / interval-based"
SECTION_WILL_CALL = "will-call"
SECTION_STALE = "stale"
SECTION_INSUFFICIENT = "insufficient data"

SECTION_ORDER = [SECTION_RANKED, SECTION_SEASONAL, SECTION_EVENT,
                 SECTION_ON_DEMAND, SECTION_LUMP_SUM, SECTION_WILL_CALL, SECTION_STALE,
                 SECTION_INSUFFICIENT]

# Final model statuses. model_status on every row is one of these.
STATUS_DEFAULT = "50/50 default"
STATUS_TRUE_CLOSER = "true closer"
STATUS_SEMI_CLOSER = "semi-closer / call-driven"
STATUS_OPEN_ISH = "seasonal open-ish"
STATUS_EVENT = "event-driven"
STATUS_ON_DEMAND = "on-demand / unlimited"
STATUS_LUMP_SUM = "lump-sum / interval-based"
STATUS_WILL_CALL = "will-call (detected)"
STATUS_DETECTED_CLOSER = "closer (detected)"
STATUS_INSUFFICIENT = "insufficient data"
STATUS_STALE = "stale - projection not meaningful"

# Statuses that never take a daily-rate urgency, whatever the date.
NO_RATE_SECTIONS = {
    STATUS_EVENT: SECTION_EVENT,
    STATUS_ON_DEMAND: SECTION_ON_DEMAND,
    STATUS_LUMP_SUM: SECTION_LUMP_SUM,
}
SEASONAL_STATUSES = {STATUS_TRUE_CLOSER, STATUS_SEMI_CLOSER, STATUS_DETECTED_CLOSER}

# The automatic heuristic, kept as a REVIEW HINT for customers not in
# MODEL_OVERRIDES. It never moves a row out of the ranking on its own.
HINT_CLOSER = "likely closer / reopening rule needed"
HINT_SEASONAL = "likely seasonal / needs separate rule"


# ─────────────────────────────────────────────
# Model-status overrides
# ─────────────────────────────────────────────

class Override(NamedTuple):
    status: str
    name: str                   # asserted against the pickup record
    season: str | None          # month-level active season, "Nov-Apr"
    reason: str


# Human-reviewed, keyed by customer id -- never by name, which the region code
# learned the hard way ("Winooski" contains "ski"). `name` is checked against
# the latest pickup record at build time so a mistyped or reused id fails loudly.
#
# Seasons are month-level only. They are the active block inferred by
# build_fringe_seasonal_candidates.active_block, except where noted; the build
# prints a warning if the live inference drifts from what is written here.
#
# STATUS_LUMP_SUM is supported but deliberately unused: nothing in the data
# justifies it for any account yet.
MODEL_OVERRIDES = {
    # ── on-demand / unlimited ──
    423: Override(STATUS_ON_DEMAND, "Perrigo Nutritionals", None,
                  "Bulk account: ~8,200 gal/yr against a listed 500, a single "
                  "13,400-gal pickup in Jun 2026. Not a barrel."),

    # ── true closers: held out whenever the month is outside the season ──
    1388: Override(STATUS_TRUE_CLOSER, "Okemo- Mountain Resort", "Dec-Apr",
                   "Pickups only Dec-Apr in both seasons it has; May-Sep index "
                   "0.00. Two years only."),
    225: Override(STATUS_TRUE_CLOSER, "Mount Ellen", "Nov-Apr",
                  "Quiet May-Oct, repeatability 0.97 over 3 years."),
    817: Override(STATUS_TRUE_CLOSER, "Suicide 6= Saskadena", "Nov-Apr",
                  "Quiet May-Oct, repeatability 0.84 over 3 years."),
    901: Override(STATUS_TRUE_CLOSER, "Main Lodge (Mt. Snow)", "Oct-Apr",
                  "Quiet Jun-Aug, repeatability 0.90; May pickups are "
                  "season-end cleanouts."),
    902: Override(STATUS_TRUE_CLOSER, "Carinthia Lodge (Mt. Snow)", "Oct-Apr",
                  "3-month quiet block repeats, repeatability 0.80. Close call: "
                  "one July pickup (2023) in three summers."),
    529: Override(STATUS_TRUE_CLOSER, "Jay Peak-Stateside (loading dock)", "Nov-Jun",
                  "Quiet Jul, low Aug-Oct, repeatability 0.85. Close call: one "
                  "October pickup (2024)."),

    # ── semi-closer / call-driven: held out while off-season AND silent ──
    934: Override(STATUS_SEMI_CLOSER, "Okemo-Jackson Gore Inn", "Dec-Apr",
                  "Dec-Apr pickups plus summer calls (Jul, Aug 2025), none in "
                  "summer 2026. Two years only. Season set from the pickup "
                  "months: the index shape (inferred Oct-Mar) spreads the first "
                  "December pickup back into autumn."),
    903: Override(STATUS_SEMI_CLOSER, "Cousins (Mt. Snow)", "Oct-Apr",
                  "Quiet May-Aug with 2 isolated off-season months of production."),
    527: Override(STATUS_SEMI_CLOSER, "Jay Peak- Hotel Jay -OutsideNow", "Nov-Jun",
                  "Quiet Jul-Oct with 5 isolated off-season months of production."),
    803: Override(STATUS_SEMI_CLOSER, "Toziers-", "Apr-Oct",
                  "2,165 gal/yr; quiet Dec-Feb with November pickups in 2 of 3 years."),
    1056: Override(STATUS_SEMI_CLOSER, "Quechee Gorge Snack Bar", "Mar-Oct",
                   "Quiet Dec-Feb with Nov/Dec off-season pickups."),

    # ── seasonal open-ish: strong season, never shuts; stays on 50/50 ──
    270: Override(STATUS_OPEN_ISH, "Stowe Mountain-Spruce Peak", None,
                  "Trough index 0.49, repeatability 0.96. Open year-round."),
    526: Override(STATUS_OPEN_ISH, "Jay Peak-waterslide", None,
                  "Trough index 0.55, repeatability 0.87. Open year-round."),
    224: Override(STATUS_OPEN_ISH, "Sugarbush Resort", None,
                  "Trough index 0.30, repeatability 0.76. Open year-round."),
    1219: Override(STATUS_OPEN_ISH, "Mad River Glen", None,
                   "Trough index 0.45, repeatability 0.61. Open year-round."),

    # ── event-driven: production follows an event calendar, not a daily rate ──
    413: Override(STATUS_EVENT, "Champlain Valley Expo", None,
                  "September fair peak at 5.9x an average month; Nov-Mar zero "
                  "every year."),
    836: Override(STATUS_EVENT, "Tunbridge Fair", None,
                  "One pickup a year, each September."),
}


# ─────────────────────────────────────────────
# Shared barrels and new owners
# ─────────────────────────────────────────────

class SharedContainer(NamedTuple):
    members: dict               # customer_id -> name expected on the account
    label: str                  # how the one barrel is shown
    note: str                   # split and evidence


# One physical barrel, several accounts. Each account records only ITS SHARE
# of the gallons (Henry's/Pascolo 35/35, Three Penny/Namaste 67/33 on every
# joint pickup), so the barrel's history is the members' gallons summed by
# date, and both rates work on that unchanged. CONVENTION (Sarge and Jim,
# 2026-10-06): every member lists the FULL barrel capacity on the site; the
# split lives in the names, for people. Nothing here reads a % from a name.
#
# Shown as one row, under the lowest member id. A group whose members list
# different capacities is flagged until the site is converted. A member's
# name is checked against the account; a rename is FLAGGED, not fatal, so
# renaming an account to show its split never stops the nightly.
#
# NOT a shared barrel: Pitchers Inn (219) and Warren Store (288) are two 65-gal
# barrels in one shed, pumped together with the total split evenly. Kept as
# two stops for now (Sarge, 2026-10-06; asking Jim).
SHARED_CONTAINERS = [
    # ── barrel size confirmed by Sarge / Jim ──
    SharedContainer({143: "Julio's-67%", 169: "Oakes and Evelyn-33%"},
                    "Julio's + Oakes and Evelyn", "67/33; 150 gal barrel"),
    SharedContainer({544: "Johnsons Chinese Kitchen 90%",
                     543: "Marsala Salsa/ Pizzeria -Johnson-10%"},
                    "Johnsons Chinese Kitchen + Marsala Salsa", "90/10; 150 gal barrel"),
    SharedContainer({327: "RiRa's 40%", 328: "Sweetwaters 2.0!!!- 50%",
                     1273: "BKK-inTheAlley 10%"},
                    "RiRa's + Sweetwaters + BKK-inTheAlley",
                    "40/50/10; 300 gal barrel for the three (Jim)"),
    SharedContainer({322: "Henrys Diner-50%", 323: "Pascolo 50% with Henrys"},
                    "Henrys Diner + Pascolo", "50/50; 150 gal barrel; shared since Jan 2026"),
    SharedContainer({130: "Three Penny Taproom 67%", 131: "Namaste ~ 33%"},
                    "Three Penny Taproom + Namaste", "67/33; 300 gal barrel"),
    SharedContainer({1054: "Duo Restaurant 50%-LOCK", 1055: "Tulip Bar and Cafe 50%-LOCK"},
                    "Duo Restaurant + Tulip Bar and Cafe",
                    "50/50; 300 gal (the site's own notes)"),
    # Barrel sizes below confirmed by Sarge, 2026-10-06, and set on the site.
    SharedContainer({304: "Hotel Vermont - 50%", 1037: "Hen of the Wood - 50%"},
                    "Hotel Vermont + Hen of the Wood", "50/50; 200 gal barrel"),
    SharedContainer({248: "Ranch Camp 50%", 287: "Backyard Tavern 50%"},
                    "Ranch Camp + Backyard Tavern", "50/50; 200 gal barrel"),
    SharedContainer({1214: "Pioneer Lakeshore Cafe 50%", 1215: "NY Oven Pizza 50%"},
                    "Pioneer Lakeshore Cafe + NY Oven Pizza", "50/50; 150 gal barrel"),
    SharedContainer({1286: "The PizzeriaVeritas50%", 1287: "Trattoria Delia 50%"},
                    "Pizzeria Veritas + Trattoria Delia", "50/50; 200 gal barrel"),
    SharedContainer({308: "Ruben James (RJs) (Ali Babas)- 0421",
                     311: "Ahli Baba's (split with RJs)"},
                    "Ruben James + Ahli Baba's", "50/50; 200 gal barrel"),
    SharedContainer({501: "Positive Pie 3 ~25%", 502: "Village Restaurant-Hardwick~ 50%",
                     504: "Cork & Fork/Scale House ~25%"},
                    "Positive Pie + Village Restaurant + Cork & Fork (Hardwick)", "25/50/25; 200 gal barrel"),
    SharedContainer({1053: "Namaste Garden 50%-Essex Jct", 1402: "Pick Thai -50% EssexJCT"},
                    "Namaste Garden + Pick Thai", "50/50; 200 gal barrel"),
    SharedContainer({159: "Northfield Pizza/ Depot Square 50%", 162: "O'Maddis 50%"},
                    "Northfield Pizza + O'Maddis", "50/50; 100 gal barrel"),
    SharedContainer({1246: "Junction Restaurant-50%-North Troy",
                     1436: "JNETME CONCESSIONS 50%-North Troy"},
                    "Junction Restaurant + JNETME Concessions", "50/50; 150 gal barrel"),
]


class HistoryStart(NamedTuple):
    name: str
    start: date
    note: str


# An existing account taken over by a new business: pickups and empty checks
# before `start` belong to the old one and are ignored. Name checked, flagged
# on mismatch (as for shared barrels).
HISTORY_STARTS = {
    133: HistoryStart("JJ’s- Langdon Street Tavern", date(2026, 9, 22),
                      "New owner; the account's earlier pickups were Langdon Street Tavern"),
}


def _check_registries():
    seen = {}
    for group in SHARED_CONTAINERS:
        for cid in group.members:
            if cid in seen:
                raise SystemExit(f"customer {cid} is in two SHARED_CONTAINERS groups")
            if cid in MODEL_OVERRIDES:
                raise SystemExit(f"customer {cid} is in both SHARED_CONTAINERS and "
                                 "MODEL_OVERRIDES; routing would be ambiguous")
            seen[cid] = group.label


def _renamed(cid, expected, pickups):
    """A flag when the account's current name is not the one configured."""
    current = pickups[-1]["name"] if pickups else None
    if current is None or current == expected:
        return None
    return (f"Account {cid} is now named {current!r} on the site (configured "
            f"{expected!r}); check the registry")


def apply_history_starts(pickups, empty_checks):
    """
    Drop pickups and empty checks before each HISTORY_STARTS date, in place.
    Returns customer_id -> extras for the row (start, name, town, flags).
    """
    extras = {}
    for cid, hs in HISTORY_STARTS.items():
        before = pickups.get(cid, [])
        flag = _renamed(cid, hs.name, before)
        extras[cid] = {
            "history_start": hs.start,
            "name": before[-1]["name"] if before else hs.name,
            "town": before[-1]["town"] if before else "",
            "flags": [flag] if flag else [],
            "note": hs.note,
        }
        pickups[cid] = [p for p in before if p["date"] >= hs.start]
        if cid in empty_checks:
            empty_checks[cid] = [d for d in empty_checks[cid] if d >= hs.start]
    return extras


def combine_shared(pickups, empty_checks, capacities, regions, active):
    """
    Fold each SHARED_CONTAINERS group into one customer under its lowest id,
    in place. Returns that id -> extras for the row (label, members, flags).

    Gallons are summed by date: each account records only its share, so the
    sum is the barrel. The combined capacity is the members' common listed
    capacity, or the largest with a flag while the site disagrees.
    """
    extras = {}
    for group in SHARED_CONTAINERS:
        ids = sorted(group.members)
        key = ids[0]
        flags = [f for f in (_renamed(cid, group.members[cid], pickups.get(cid, []))
                             for cid in ids) if f]

        gallons = defaultdict(int)
        for cid in ids:
            for p in pickups.get(cid, []):
                gallons[p["date"]] += p["gallons"]
        first_town = next((pickups[cid][-1]["town"] for cid in ids if pickups.get(cid)), "")
        merged = [{"date": d, "gallons": gallons[d], "name": group.label, "town": first_town}
                  for d in sorted(gallons)]

        caps = {cid: capacities.get(cid) for cid in ids}
        listed = sorted({c for c in caps.values() if c is not None})
        if len(listed) > 1:
            flags.append(
                "Members list different capacities ("
                + ", ".join(f"{caps[c]:g}" if caps[c] is not None else "none" for c in ids)
                + "): the convention is the full barrel on each")

        member_regions = []
        for cid in ids:
            for name in regions.get(cid, []):
                if name not in member_regions:
                    member_regions.append(name)

        checks = sorted({d for cid in ids for d in empty_checks.get(cid, ())})
        any_active = any(cid in active for cid in ids)

        for cid in ids:
            pickups.pop(cid, None)
            empty_checks.pop(cid, None)
            active.discard(cid)
        if merged:
            pickups[key] = merged
        if checks:
            empty_checks[key] = checks
        capacities[key] = listed[-1] if listed else None
        regions[key] = member_regions
        if any_active:
            active.add(key)

        extras[key] = {
            "shared_label": group.label,
            "members": [{"id": cid, "name": group.members[cid],
                         "capacity": caps[cid]} for cid in ids],
            "flags": flags,
            "note": group.note,
        }
    return extras


def new_owner_row(cid, extra, capacity, regions, asof):
    """The row for a HISTORY_STARTS account with no pickup since its start."""
    start = extra["history_start"]
    flags = list(extra["flags"])
    return {
        "table_section": SECTION_INSUFFICIENT, "customer_id": cid,
        "customer": extra["name"], "town": extra["town"],
        "region_names": "; ".join(regions.get(cid, [])),
        "model_status": STATUS_INSUFFICIENT, "stage": None,
        "routing_reason": f"new owner since {start.isoformat()}, no pickup yet",
        "active_season": None, "review_hint": None, "flags": "; ".join(flags),
        "last_pickup_date": None, "days_since_last_pickup": (asof - start).days,
        "last_empty_check": None, "days_accumulating": (asof - start).days,
        "avg_collection": None, "collections_per_year": None,
        "oil_rate_gpd_projected": None, "current_projected_gallons": None,
        "projected_range_low": None, "projected_range_high": None,
        "capacity": capacity, "collections_over_capacity_all_time": 0,
        "display_pct_full": None, "days_until_75pct": None, "days_past_75pct": None,
        "days_past_capacity": None, "implied_periodicity_days": None,
        "model_projected_gallons": None, "raw_pct_of_listed_capacity": None,
        "model_days_until_75pct": None, "model_days_past_75pct": None,
        "model_days_past_capacity": None, "inferred_active_season": None,
        "override_reason": extra["note"], "current_month": MONTHS[asof.month - 1],
        "current_month_index": None, "top_month": None, "top_month_index": None,
        "bottom_month": None, "bottom_month_index": None,
        "oil_rate_gpd_prev_year": None, "oil_rate_gpd_prev_6_pickups": None,
        "oil_rate_gpd_pooled": None, "seasonal_last_year_gpd": None,
        "seasonal_recent_indexed_gpd": None,
        "model_wape_if_available": None, "confidence_band_used": "",
        "history_start": start, "registry_note": extra["note"],
        "_pickups": 0, "_first_pickup": "", "_season": None,
        "_findings": {"flags": flags, "over_all_time": 0, "over_recent_years": 0,
                      "over_last_n": 0, "median_recent": None, "mean_recent": None},
        "_recent_gallons": [],
    }


# ─────────────────────────────────────────────
# Rates
# ─────────────────────────────────────────────

def rate_last_six(pickups):
    """The last-6 rate at the live edge, or None."""
    if len(pickups) < WINDOW + 1:
        return None
    # k = len(pickups) treats "the next pickup" as the thing being predicted,
    # so this is bt.rate_rolling's own window: gallons at x-6..x-1 over the
    # days from x-7 to x-1.
    rate, _ = bt.rate_rolling(pickups, len(pickups), WINDOW)
    return rate


def rate_prev_year_asof(pickups, asof, lookback=YEAR_DAYS):
    """
    The previous-year rate at a live as-of date, or None.

    THE SAME FORMULA AS bt.rate_prev_year in backtest_steady_rate.py, keyed to
    a calendar date instead of a test-pickup index: that function reads
    pickups[k]["date"], which does not exist at the live edge where there is no
    next pickup yet. IF ONE CHANGES, CHANGE BOTH.

        x-j   = the latest pickup at least `lookback` days before `asof`
        rate  = gallons at x-j+1 .. last  over  date(last) - date(x-j)
    """
    if len(pickups) < 2:
        return None

    cutoff = asof - timedelta(days=lookback)
    i = next((i for i in range(len(pickups) - 1, -1, -1)
              if pickups[i]["date"] <= cutoff), None)
    if i is None or i == len(pickups) - 1:
        return None

    days = (pickups[-1]["date"] - pickups[i]["date"]).days
    if days <= 0:
        return None
    return sum(p["gallons"] for p in pickups[i + 1:]) / days


def rate_pooled(pickups):
    """
    Gallons over days across at most the last WINDOW gaps, or None.

    The window's first pickup only marks where the days start, so the FIRST
    pickup a customer ever had never contributes gallons. With 7+ pickups this
    is exactly rate_last_six.
    """
    n = len(pickups)
    if n < 2:
        return None
    return bsm.rate_span(pickups, max(0, n - 1 - WINDOW), n - 1)


SEASONAL_LY_WEIGHT = 0.7         # Sarge's 70/30: last year vs the last 2 pickups
SEASONAL_LY_HALF_WIDTH = 21      # days either side of the date, a year back
# No growth factor on last year's rate: tried 2026-10-08 and removed the same
# day. It amplified any customer whose volume had shifted, at its peak season
# (recent_override.md; Skinny Pancake Quechee projected 151 gal vs ~100).


def window_rate(daily, first_date, center, half_width):
    """
    Gallons per day over center +/- half_width, from spread production.
    THE SAME FORMULA AS backtest_phase2_rates.window_rate, which imports this
    module and so cannot be imported here. IF ONE CHANGES, CHANGE BOTH.
    """
    start, end = center - timedelta(days=half_width), center + timedelta(days=half_width)
    if start <= first_date + timedelta(days=60):
        return None
    days = (end - start).days + 1
    return sum(daily.get(start + timedelta(days=i), 0.0) for i in range(days)) / days


def seasonal_rate(pickups, factor, clock_start, asof):
    """
    The seasonal-open rate (seasonal_formulas.md, chosen 2026-10-08):

        A = 0.7 x last year's rate over asof +/- 3 weeks (1-2 years back,
            averaged) + 0.3 x the last 2 pickups' rate        -- Sarge's 70/30
        B = last 3 pickups' rate / the index of the days they cover
                                 x the index of the days being projected
        rate = (A + B) / 2, or whichever exists

    Returns (rate, A, B); rate is None when neither half can be formed.
    """
    n = len(pickups)
    daily = ss.daily_production(pickups)
    first = pickups[0]["date"]
    years = [r for r in (window_rate(daily, first, asof - timedelta(days=YEAR_DAYS * back),
                                     SEASONAL_LY_HALF_WIDTH) for back in (1, 2))
             if r is not None]
    last_year = statistics.fmean(years) if years else None
    last2 = bsm.rate_span(pickups, n - 3, n - 1) if n >= 3 else None
    a = (SEASONAL_LY_WEIGHT * last_year + (1 - SEASONAL_LY_WEIGHT) * last2
         if last_year is not None and last2 is not None else None)

    b = None
    if n >= 4:
        last3 = bsm.rate_span(pickups, n - 4, n - 1)
        covered = cf.gap_weighted(factor, pickups[n - 4]["date"], pickups[n - 1]["date"])
        if last3 is not None and covered > 0:
            b = last3 / covered * cf.gap_weighted(factor, clock_start, asof)

    halves = [h for h in (a, b) if h is not None]
    return (statistics.fmean(halves) if halves else None), a, b


def seasonal_factor(cid, pickups, asof):
    """
    The 12-month index when the customer is seasonal-open for asof's year,
    scored from complete years before it; otherwise None. The shuffles are
    seeded per customer and year so the result never depends on build order.
    """
    season = seasonal_open.season_for_year(pickups, asof.year)
    rng = random.Random(f"{cid}-{asof.year}")
    label, _ = seasonal_open.classify(season, rng)
    return season["factor"] if label == seasonal_open.SEASONAL_OPEN else None


def open_season_spec(closed):
    """Detected closed months -> the registry's season format, e.g. 'Apr-Oct'."""
    first = next(m for m in range(12) if m not in closed and (m - 1) % 12 in closed)
    last = (first + 12 - len(closed) - 1) % 12
    return f"{MONTHS[first]}-{MONTHS[last]}"


def detected_closer(cid, pickups, asof):
    """
    A pseudo-override for a customer the detector finds closing each year
    (seasonal_open.closed_months, from the complete years before asof's
    year), or None. Routed like a semi-closer: held out off-season only once
    silent, so a real off-season call still ranks it -- the detector put
    2% of gallons in predicted-closed months on the 2024-25 holdout.
    """
    closed = seasonal_open.closed_months(pickups, asof.year)
    if not closed:
        return None
    spec = open_season_spec(closed)
    return Override(STATUS_DETECTED_CLOSER, pickups[-1]["name"], spec,
                    f"detected: closed {MONTHS[(MONTHS.index(spec.split('-')[1]) + 1) % 12]}"
                    f"-{MONTHS[(MONTHS.index(spec.split('-')[0]) - 1) % 12]} over the "
                    f"{seasonal_open.CLOSED_YEARS_BACK} complete years before {asof.year}")


def rate_open_season(pickups, spec):
    """
    Gallons over days across at most the last WINDOW gaps that lie wholly in
    season, or None with fewer than CLOSER_MIN_OPEN_GAPS. The 50/50 averages
    in the closed months and under-projects an open closer by ~21 WAPE points
    (seasonal_closed.md). The first pickup ever never contributes gallons.
    """
    open_months = season_months(spec)
    gallons = days = used = 0
    for i in range(len(pickups) - 1, 0, -1):
        a, b = pickups[i - 1]["date"], pickups[i]["date"]
        span = (b - a).days
        if span <= 0 or any((a + timedelta(days=d)).month - 1 not in open_months
                            for d in range(1, span + 1)):
            continue
        gallons += pickups[i]["gallons"]
        days += span
        used += 1
        if used == WINDOW:
            break
    return gallons / days if used >= CLOSER_MIN_OPEN_GAPS and days else None


def will_call_signal(pickups, asof):
    """
    The routing reason when the gaps over the last WILL_CALL_WINDOW_DAYS say
    will-call, else None. Gaps longer than WILL_CALL_CLOSURE_MULTIPLE x the
    median are a closer's off-season and are left out of the irregularity
    measure, so a summer business is not called irregular for closing.
    """
    recent = [p for p in pickups
              if p["date"] >= asof - timedelta(days=WILL_CALL_WINDOW_DAYS)]
    if len(recent) < NEW_MIN_PICKUPS:
        return None
    gaps = [g for g in ((b["date"] - a["date"]).days for a, b in zip(recent, recent[1:]))
            if g > 0]
    if not gaps:
        return None
    median = statistics.median(gaps)
    if median > WILL_CALL_MEDIAN_GAP:
        return f"median gap {median:.0f} days (over {WILL_CALL_MEDIAN_GAP})"
    open_gaps = [g for g in gaps if g <= WILL_CALL_CLOSURE_MULTIPLE * median]
    if len(open_gaps) >= 3:
        spread = statistics.pstdev(open_gaps) / statistics.fmean(open_gaps)
        if spread > WILL_CALL_GAP_CV:
            return f"irregular gaps (sd/mean {spread:.2f}, over {WILL_CALL_GAP_CV})"
    return None


# ─────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────

def load_active_ids():
    """customer_ids flagged active in the exported roster."""
    data = json.loads(COLLECTIONS.read_text())
    return {c["customer_id"] for c in data["customers"] if c.get("is_active")}


def load_empty_checks(asof):
    """customer_id -> sorted dates of RESET_QTYS entries on or before `asof`."""
    checks = defaultdict(list)
    if not NON_PICKUPS.exists():
        print(f"  note: {NON_PICKUPS.name} missing; no empty-check resets applied")
        return checks
    with open(NON_PICKUPS, newline="") as f:
        for row in csv.DictReader(f):
            day = date.fromisoformat(row["date"])
            if int(row["qty"]) in RESET_QTYS and day <= asof:
                checks[int(row["customer_id"])].append(day)
    for days in checks.values():
        days.sort()
    return checks


def load_capacities():
    """customer_id -> capacity, or None when missing or not positive."""
    capacities = {}
    with open(CAPACITY_CACHE, newline="") as f:
        for row in csv.DictReader(f):
            try:
                value = float(row["capacity"])
            except (TypeError, ValueError):
                value = 0.0
            capacities[int(row["customer_id"])] = value if value > 0 else None
    return capacities


def load_regions():
    """customer_id -> region display names, in the exported region order."""
    data = json.loads(OIL_DATA.read_text())
    order = list(data.get("region_names", [])) + ["Other"]
    rank = {name: i for i, name in enumerate(order)}

    regions = defaultdict(list)
    for name, ids in data.get("region_customers", {}).items():
        for cid in ids:
            regions[cid].append(name)
    for cid in regions:
        regions[cid].sort(key=lambda n: (rank.get(n, len(rank)), n))
    return regions


def load_customer_wape():
    """
    customer_id -> WAPE of the 50/50 model, in percent units.

    Read from the committed WAPE_SNAPSHOT only, never from the backtest CSVs:
    those are gitignored, so the nightly runner does not have them, and reading
    them when present would make local and nightly bands disagree.
    """
    if not WAPE_SNAPSHOT.exists():
        print(f"  note: {WAPE_SNAPSHOT.name} missing; every band comes from "
              "RANGE_FACTORS")
        return {}
    return {int(cid): value
            for cid, value in json.loads(WAPE_SNAPSHOT.read_text()).items()}


def refresh_wape_snapshot():
    """
    Rebuild WAPE_SNAPSHOT from the backtest CSVs (--refresh-wape).

    Two sources, no overlap: the held-out routing-rule test, and the seasonal
    backtest detail for the customers that shaped the rule. Run this after
    re-running those backtests, then commit the snapshot.
    """
    for source in (ROUTING_WAPE, SEASONAL_DETAIL):
        if not source.exists():
            raise SystemExit(f"{source.name} not found; re-run its backtest first")
    wape = {}

    with open(ROUTING_WAPE, newline="") as f:
        for row in csv.DictReader(f):
            try:
                wape[int(row["customer_id"])] = float(row["50/50"])
            except (KeyError, TypeError, ValueError):
                continue

    totals = defaultdict(lambda: [0.0, 0.0])
    with open(SEASONAL_DETAIL, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("model") != "50/50 last 6 + prev year":
                continue
            cid = int(row["customer_id"])
            totals[cid][0] += abs(float(row["signed_error"]))
            totals[cid][1] += float(row["actual_gallons"])
    for cid, (error, actual) in totals.items():
        if actual > 0:
            wape.setdefault(cid, error / actual * 100)

    # Full float precision: rounding here would move the rounded bands.
    WAPE_SNAPSHOT.write_text(json.dumps(
        {str(cid): wape[cid] for cid in sorted(wape)}, indent=1) + "\n")
    print(f"Wrote analysis/{WAPE_SNAPSHOT.name}: {len(wape)} customers")


# ─────────────────────────────────────────────
# Per-customer measures
# ─────────────────────────────────────────────

def average_collection(pickups):
    """Mean gallons of the last WINDOW pickups: the window the rate uses."""
    recent = pickups[-WINDOW:]
    return statistics.fmean(p["gallons"] for p in recent) if recent else None


def collections_per_year(pickups):
    """
    Qualifying pickups per complete calendar year.

    Averaged over COMPLETE_YEARS from the customer's first pickup year on, so a
    customer who joined in 2025 is not divided by three. Years inside that span
    with no pickups count as real zeros -- a missing year is a zero, not
    missing data.
    """
    first_year = pickups[0]["date"].year
    years = [y for y in COMPLETE_YEARS if y >= first_year]
    if not years:
        return None

    counts = defaultdict(int)
    for p in pickups:
        counts[p["date"].year] += 1
    return sum(counts[y] for y in years) / len(years)


def seasonality(pickups):
    """
    ss.score for this customer, or None when it cannot be formed.

    It needs production in every one of ss.SCORE_YEARS, so newer customers have
    no score. Diagnostic only -- never applied to the projection.
    """
    try:
        return ss.score(pickups)
    except Exception:
        return None


def capacity_findings(pickups, capacity, avg_collection, asof):
    """Capacity flags and the counts behind them. Never changes the capacity."""
    findings = {
        "flags": [],
        "over_all_time": 0,
        "over_recent_years": 0,
        "over_last_n": 0,
        "median_recent": None,
        "mean_recent": None,
    }

    if capacity is None:
        findings["flags"].append(FLAG_CAPACITY_MISSING)
        return findings

    findings["over_all_time"] = sum(1 for p in pickups if p["gallons"] > capacity)

    soft_cutoff = asof - timedelta(days=365 * SOFT_FLAG_YEARS)
    findings["over_recent_years"] = sum(
        1 for p in pickups if p["date"] >= soft_cutoff and p["gallons"] > capacity)

    recent = [p["gallons"] for p in pickups[-RECENT_PICKUPS:]]
    if recent:
        findings["over_last_n"] = sum(1 for g in recent if g > capacity)
        findings["median_recent"] = statistics.median(recent)
        findings["mean_recent"] = statistics.fmean(recent)

    # Soft: something overflowed recently enough to be about today's container.
    if findings["over_recent_years"]:
        findings["flags"].append(FLAG_OVER_CAPACITY)

    # Strong: it is not a one-off.
    strong = (findings["over_last_n"] > 1
              or (findings["median_recent"] is not None
                  and findings["median_recent"] > capacity)
              or (findings["mean_recent"] is not None
                  and findings["mean_recent"] > capacity))
    if strong:
        findings["flags"].append(FLAG_CAPACITY_STALE)

    if avg_collection is not None and avg_collection > capacity:
        findings["flags"].append(FLAG_CAPACITY_LOW)

    return findings


def review_hint(season):
    """
    The automatic seasonality heuristic, as a review prompt only.

    It used to be the model status itself, which implied these customers were
    classified. They are not: they are ranked on the 50/50 default like anyone
    else until a human adds them to MODEL_OVERRIDES.
    """
    if season:
        if season["trough_index"] < bsm.CLOSED_INDEX:
            return HINT_CLOSER
        if (season["repeatability"] >= ss.SEASONAL_MIN_REPEAT
                and season["amplitude"] >= ss.SEASONAL_MIN_AMPLITUDE):
            return HINT_SEASONAL
    return None


def season_months(spec):
    """'Nov-Apr' -> {10, 11, 0, 1, 2, 3}, wrapping the year end."""
    start, end = (MONTHS.index(m) for m in spec.split("-"))
    return {(start + i) % 12 for i in range((end - start) % 12 + 1)}


def season_opened(spec, asof):
    """
    First day of the season `asof` falls in, or None when `asof` is off-season.

    Month-level only: the season is taken to open on the 1st of its first
    month. That is the resolution the data supports, not a reopening date.
    """
    if asof.month - 1 not in season_months(spec):
        return None
    first = MONTHS.index(spec.split("-")[0]) + 1
    year = asof.year if asof.month >= first else asof.year - 1
    return date(year, first, 1)


def inferred_season(pickups):
    """The active block build_fringe_seasonal_candidates would infer today."""
    years = fringe.profile(pickups)
    if len(years) < fringe.MIN_YEARS:
        return None
    shape = [statistics.fmean(years[y][m] for y in years) for m in range(12)]
    start, end, span, _ = fringe.active_block(shape)
    return f"{start}-{end}" if start and span < 12 else None


def silence_threshold(pickups):
    """Days of silence that count as 'no call signal': the fringe table's rule."""
    gap = fringe.median_gap(pickups)
    return (max(fringe.SILENCE_FLOOR_DAYS, fringe.SILENCE_GAP_MULTIPLE * gap)
            if gap else fringe.SILENCE_FLOOR_DAYS)


def route(override, has_rate, days_since, last_date, pickups, asof, will_call=None):
    """
    (model_status, table_section, routing_reason) for one row.

    Precedence: no-rate overrides -> will-call (detected, never for an
    override, and not once stale: a long-silent account is reported as
    stale, which says more) -> insufficient data -> seasonal holdouts ->
    stale -> ranked.
    The seasonal rules run before the stale rule, so a closer that has been
    shut since spring is reported as off-season, not as lost.
    """
    status = override.status if override else STATUS_DEFAULT

    if status in NO_RATE_SECTIONS:
        return status, NO_RATE_SECTIONS[status], "not a daily-rate account"

    if will_call and not override and days_since <= STALE_DAYS:
        return STATUS_WILL_CALL, SECTION_WILL_CALL, will_call

    if not has_rate:
        reason = (f"{len(pickups)} pickup{'s' if len(pickups) != 1 else ''} so far; "
                  f"projections start at {NEW_MIN_PICKUPS}"
                  if len(pickups) < NEW_MIN_PICKUPS else "")
        return (status if override else STATUS_INSUFFICIENT,
                SECTION_INSUFFICIENT, reason)

    if status in SEASONAL_STATUSES:
        opened = season_opened(override.season, asof)
        if opened is None:
            if status == STATUS_TRUE_CLOSER:
                return (status, SECTION_SEASONAL,
                        f"off-season (closed; season {override.season})")
            threshold = silence_threshold(pickups)
            if days_since > threshold:
                return (status, SECTION_SEASONAL,
                        f"seasonal / no recent call signal ({days_since} days "
                        f"silent, threshold {threshold:.0f}; season "
                        f"{override.season})")
            return (status, SECTION_RANKED,
                    f"off-season but called {days_since} days ago; "
                    f"season {override.season}")
        if last_date < opened:
            return (status, SECTION_SEASONAL,
                    f"season open — awaiting first pickup (season "
                    f"{override.season}; last pickup before it opened)")
        return status, SECTION_RANKED, f"in season ({override.season})"

    if days_since > STALE_DAYS:
        return status if override else STATUS_STALE, SECTION_STALE, ""

    return status, SECTION_RANKED, ""


# ─────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────

def build_row(cid, pickups, asof, capacity, regions, wape, empty_checks=()):
    last = pickups[-1]
    days_since = (asof - last["date"]).days

    # The oil clock starts at the last pickup, or at a later empty check: the
    # truck looked and found nothing worth pumping, so nothing had built up.
    # The RATE is untouched -- gallons over days between pickups is still the
    # right average production -- only where the counting starts moves.
    later = [d for d in empty_checks if d > last["date"]]
    last_check = later[-1] if later else None
    clock_start = last_check or last["date"]
    days_accumulating = (asof - clock_start).days

    # The ladder. last6 is kept for the detail column; pooled equals it once
    # there are 7+ pickups, so established rows keep today's 50/50 exactly.
    last6 = rate_last_six(pickups)
    pooled = rate_pooled(pickups)
    prev_year = rate_prev_year_asof(pickups, asof)
    gaps = len(pickups) - 1
    stage = rate = factor = season_a = season_b = None
    if len(pickups) >= NEW_MIN_PICKUPS and pooled is not None:
        if prev_year is not None:
            stage, rate = STAGE_ESTABLISHED, (pooled + prev_year) / 2
            factor = seasonal_factor(cid, pickups, asof)
            if factor:
                seasonal, season_a, season_b = seasonal_rate(
                    pickups, factor, clock_start, asof)
                if seasonal is not None:
                    stage, rate = STAGE_SEASONAL, seasonal
        else:
            stage, rate = STAGE_NEW, pooled

    avg_collection = average_collection(pickups)
    season = seasonality(pickups)
    findings = capacity_findings(pickups, capacity, avg_collection, asof)

    registry = MODEL_OVERRIDES.get(cid)
    if registry and registry.name != last["name"]:
        raise SystemExit(
            f"MODEL_OVERRIDES[{cid}] expects {registry.name!r} but the pickup "
            f"record says {last['name']!r}. Check the id before trusting the "
            "override.")
    # The registry wins; otherwise the closer detector may supply a season.
    override = registry or detected_closer(cid, pickups, asof)

    # An in-season closer is ranked on its open-season gaps, not the 50/50.
    closer_rate = None
    if (override and override.status in SEASONAL_STATUSES and rate is not None
            and season_opened(override.season, asof) is not None):
        closer_rate = rate_open_season(pickups, override.season)
        if closer_rate is not None:
            stage, rate = STAGE_CLOSER, closer_rate
    # Stale and the off-season silence rule stay on the last PICKUP (a check is
    # not oil); "awaiting first pickup" uses the clock, so an in-season empty
    # check counts as a fresh start.
    status, section, routing_reason = route(
        override, rate is not None, days_since, clock_start, pickups, asof,
        will_call_signal(pickups, asof))
    if section == SECTION_RANKED and routing_reason.startswith("in season"):
        routing_reason += ("; rate from its open-season pickups" if closer_rate is not None
                           else "; too few open-season gaps, so the stage rate, which "
                                "includes closed months and likely understates")
    hint = None if override else review_hint(season)

    target = capacity * TARGET_FRACTION if capacity is not None else None
    projected = rate * days_accumulating if rate is not None else None

    # Likely range: the customer's own backtested WAPE where it exists (an
    # established customer), otherwise the replay's calibrated factors for the
    # stage. Six customers score over 100% WAPE, so the low end is clamped at
    # zero rather than allowed to go negative.
    low = high = None
    band_used = ""
    if projected is not None:
        if stage not in (STAGE_NEW, STAGE_CLOSER) and cid in wape:
            band_pct = wape[cid]
            band_used = f"customer WAPE {band_pct:.1f}%"
            low = max(0.0, projected * (1 - band_pct / 100))
            high = projected * (1 + band_pct / 100)
        else:
            key = (STAGE_CLOSER if stage == STAGE_CLOSER
                   else STAGE_ESTABLISHED if stage != STAGE_NEW
                   else f"new, {gaps} gaps" if gaps <= 3 else "new, 4+ gaps")
            lo_f, hi_f = RANGE_FACTORS[key]
            band_used = f"calibrated {key}: x{lo_f:.2f} to x{hi_f:.2f}"
            low, high = projected * lo_f, projected * hi_f

    raw_pct = days_until = days_past_75 = days_past_cap = periodicity = None
    if projected is not None and capacity:
        raw_pct = projected / capacity * 100
        if rate > 0:
            days_until = (target - projected) / rate
            days_past_75 = abs(days_until) if projected > target else 0.0
            days_past_cap = ((projected - capacity) / rate
                             if projected > capacity else 0.0)
            periodicity = target / rate

    # Only a ranked row's capacity arithmetic is a route signal. Everywhere
    # else the operator-facing columns are blanked, and the 50/50 arithmetic
    # survives in the model_* detail columns for audit.
    ranked = section == SECTION_RANKED
    shown = (lambda value: value) if ranked else (lambda value: None)

    if ranked and raw_pct is not None and raw_pct > 100:
        findings["flags"].append(FLAG_ABOVE_CAPACITY)
    if days_since > STALE_DAYS:
        findings["flags"].append(f"No pickup in {days_since} days")

    month_index = season["shape"][asof.month - 1] if season else None
    configured_season = override.season if override else None
    inferred = inferred_season(pickups) if registry and registry.season else None

    return {
        "table_section": section,
        "customer_id": cid,
        "customer": last["name"],
        "town": last["town"],
        "region_names": "; ".join(regions.get(cid, [])),
        "model_status": status,
        "stage": stage,
        "routing_reason": routing_reason,
        "active_season": configured_season,
        "review_hint": hint,
        "flags": "; ".join(findings["flags"]),
        "last_pickup_date": last["date"].isoformat(),
        "days_since_last_pickup": days_since,
        "last_empty_check": last_check.isoformat() if last_check else None,
        "days_accumulating": days_accumulating,
        "avg_collection": avg_collection,
        "collections_per_year": collections_per_year(pickups),
        "oil_rate_gpd_projected": rate,
        "current_projected_gallons": shown(projected),
        "projected_range_low": shown(low),
        "projected_range_high": shown(high),
        "capacity": capacity,
        "collections_over_capacity_all_time": findings["over_all_time"],
        "display_pct_full": shown(min(raw_pct, 100.0) if raw_pct is not None else None),
        "days_until_75pct": shown(days_until),
        "days_past_75pct": shown(days_past_75),
        "days_past_capacity": shown(days_past_cap),
        "implied_periodicity_days": shown(periodicity),
        # The 50/50 arithmetic on every row, blanked or not. Detail only.
        "model_projected_gallons": projected,
        "raw_pct_of_listed_capacity": raw_pct,
        "model_days_until_75pct": days_until,
        "model_days_past_75pct": days_past_75,
        "model_days_past_capacity": days_past_cap,
        "inferred_active_season": inferred,
        "override_reason": override.reason if override else None,
        "current_month": MONTHS[asof.month - 1],
        "current_month_index": month_index,
        "top_month": season["peak_month"] if season else None,
        "top_month_index": season["peak_index"] if season else None,
        "bottom_month": season["trough_month"] if season else None,
        "bottom_month_index": season["trough_index"] if season else None,
        "oil_rate_gpd_prev_year": prev_year,
        "oil_rate_gpd_prev_6_pickups": last6,
        "oil_rate_gpd_pooled": pooled,
        "seasonal_last_year_gpd": season_a,
        "seasonal_recent_indexed_gpd": season_b,
        "model_wape_if_available": wape.get(cid),
        "confidence_band_used": band_used,
        # Detail-only, and for sorting.
        "_pickups": len(pickups),
        "_first_pickup": pickups[0]["date"].isoformat(),
        "_season": season,
        "_findings": findings,
        "_recent_gallons": [p["gallons"] for p in pickups[-RECENT_PICKUPS:]],
    }


def sort_key(row):
    """
    Operational urgency. Missing values sort last within each measure, and
    customer_id breaks every remaining tie so the output is deterministic.
    """
    def desc(value):
        return -value if value is not None else 1.0

    # Will-call rows have no fill estimate: longest since the last pickup first.
    if row["table_section"] == SECTION_WILL_CALL:
        return (-row["days_since_last_pickup"], 0, 0, 0, row["customer_id"])

    # The raw model values, not the display ones: identical for ranked rows,
    # and they keep a sensible order inside the held-out sections too.
    return (
        desc(row["model_days_past_capacity"]),
        desc(row["model_days_past_75pct"]),
        desc(row["raw_pct_of_listed_capacity"]),
        desc(row["model_projected_gallons"]),
        row["customer_id"],
    )


# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────

MAIN_FIELDS = [
    "table_section", "customer_id", "customer", "town", "region_names",
    "model_status", "stage", "routing_reason", "active_season", "review_hint",
    "flags", "shared_members_text", "history_start",
    "last_pickup_date", "days_since_last_pickup",
    "last_empty_check", "days_accumulating",
    "avg_collection", "collections_per_year", "oil_rate_gpd_projected",
    "current_projected_gallons", "projected_range_low", "projected_range_high",
    "capacity", "collections_over_capacity_all_time", "display_pct_full",
    "days_until_75pct", "days_past_75pct", "days_past_capacity",
    "implied_periodicity_days", "current_month", "current_month_index",
    "top_month", "top_month_index", "bottom_month", "bottom_month_index",
    "oil_rate_gpd_prev_year", "oil_rate_gpd_prev_6_pickups",
    "model_wape_if_available", "confidence_band_used",
]

ROUNDING = {
    "avg_collection": 1, "collections_per_year": 1,
    "oil_rate_gpd_projected": 3, "current_projected_gallons": 1,
    "projected_range_low": 1, "projected_range_high": 1,
    "capacity": 0, "display_pct_full": 1, "days_until_75pct": 1, "days_past_75pct": 1,
    "days_past_capacity": 1, "implied_periodicity_days": 1,
    "model_projected_gallons": 1, "raw_pct_of_listed_capacity": 1,
    "model_days_until_75pct": 1, "model_days_past_75pct": 1,
    "model_days_past_capacity": 1,
    "current_month_index": 2, "top_month_index": 2, "bottom_month_index": 2,
    "oil_rate_gpd_prev_year": 3, "oil_rate_gpd_prev_6_pickups": 3,
    "oil_rate_gpd_pooled": 3, "seasonal_last_year_gpd": 3,
    "seasonal_recent_indexed_gpd": 3,
    "model_wape_if_available": 2,
}


def presented(row, fields):
    out = {}
    for key in fields:
        value = row.get(key)
        if isinstance(value, float) and key in ROUNDING:
            digits = ROUNDING[key]
            value = round(value, digits) if digits else round(value)
        out[key] = "" if value is None else value
    return out


def write_main(rows):
    with open(OUT_MAIN, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MAIN_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(presented(row, MAIN_FIELDS))


DETAIL_FIELDS = [
    "customer_id", "customer", "town", "table_section", "model_status", "stage",
    "routing_reason", "active_season", "inferred_active_season",
    "override_reason", "review_hint", "flags",
    "shared_members_text", "history_start", "registry_note",
    "pickup_count", "first_pickup", "last_pickup_date",
    "days_since_last_pickup", "last_empty_check", "days_accumulating",
    "oil_rate_gpd_prev_6_pickups", "oil_rate_gpd_pooled",
    "seasonal_last_year_gpd", "seasonal_recent_indexed_gpd",
    "oil_rate_gpd_prev_year", "oil_rate_gpd_projected",
    "model_projected_gallons", "raw_pct_of_listed_capacity",
    "model_days_until_75pct", "model_days_past_75pct",
    "model_days_past_capacity", "capacity",
    "collections_over_capacity_all_time", "collections_over_capacity_2y",
    "collections_over_capacity_last_10", "median_last_10", "mean_last_10",
    "recent_gallons", "amplitude", "repeatability", "model_wape_if_available",
    "confidence_band_used",
] + MONTHS


def write_detail(rows):
    with open(OUT_DETAIL, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            season, findings = row["_season"], row["_findings"]
            record = dict(row)
            record.update({
                "pickup_count": row["_pickups"],
                "first_pickup": row["_first_pickup"],
                "collections_over_capacity_2y": findings["over_recent_years"],
                "collections_over_capacity_last_10": findings["over_last_n"],
                "median_last_10": findings["median_recent"],
                "mean_last_10": findings["mean_recent"],
                "recent_gallons": " ".join(str(g) for g in row["_recent_gallons"]),
                "amplitude": round(season["amplitude"], 3) if season else None,
                "repeatability": round(season["repeatability"], 3) if season else None,
            })
            for i, month in enumerate(MONTHS):
                record[month] = round(season["shape"][i], 3) if season else None
            writer.writerow(presented(record, DETAIL_FIELDS))


def write_json(rows, asof):
    """
    oil_projections.json for projections.html.

    Operator fields only, taken from the SAME presented values as the main CSV,
    so the page shows exactly what the table shows: % full is the capped
    display value and held-out rows carry nulls. Rows keep the build's order;
    the page never re-sorts or recomputes a projection.
    """
    def value(row, key):
        return presented(row, [key])[key] if row.get(key) is not None else None

    customers = [{
        "id": r["customer_id"],
        "name": r["customer"],
        "town": r["town"],
        "regions": r["region_names"].split("; ") if r["region_names"] else [],
        "section": r["table_section"],
        "status": r["model_status"],
        "stage": r["stage"],
        "reason": r["routing_reason"] or None,
        "season": r["active_season"],
        "last_pickup": r["last_pickup_date"],
        "days_since": r["days_since_last_pickup"],
        "last_empty_check": r["last_empty_check"],
        "days_accumulating": r["days_accumulating"],
        "shared_label": r.get("shared_label"),
        "members": [{"id": m["id"], "name": m["name"]} for m in r["shared_members"]]
                   if r.get("shared_members") else None,
        "history_start": r.get("history_start"),
        "rate_gpd": value(r, "oil_rate_gpd_projected"),
        "avg_collection": value(r, "avg_collection"),
        "capacity": value(r, "capacity"),
        "projected_gal": value(r, "current_projected_gallons"),
        "range_low": value(r, "projected_range_low"),
        "range_high": value(r, "projected_range_high"),
        "pct_full": value(r, "display_pct_full"),
        "days_until_75": value(r, "days_until_75pct"),
        "days_past_75": value(r, "days_past_75pct"),
        "days_past_capacity": value(r, "days_past_capacity"),
        "flags": r["flags"].split("; ") if r["flags"] else [],
    } for r in rows]

    # The scrape this was built from. The page compares it with the live
    # oil_data.json and warns when they differ, i.e. the nightly scraped but the
    # projection build failed. No wall-clock timestamp, so a rerun on unchanged
    # data writes an identical file.
    oil_data = json.loads(OIL_DATA.read_text())

    payload = {
        "as_of": asof.isoformat(),
        "data_last_updated": oil_data.get("last_updated"),
        "beta": True,
        "target_fraction": TARGET_FRACTION,
        "stale_days": STALE_DAYS,
        "section_order": SECTION_ORDER,
        "region_order": list(oil_data.get("region_names", [])) + ["Other"],
        "customers": customers,
    }
    # One customer per line: compact, and a nightly diff reads row by row.
    head = json.dumps({k: v for k, v in payload.items() if k != "customers"},
                      ensure_ascii=False, indent=1)
    body = ",\n".join(json.dumps(c, ensure_ascii=False) for c in customers)
    OUT_JSON.write_text(f'{head[:-2]},\n "customers": [\n{body}\n ]\n}}\n')


def fmt(value, digits=1, dash="-"):
    if value is None or value == "":
        return dash
    return f"{value:,.{digits}f}"


def urgent_table(rows, limit):
    lines = [
        "| # | Customer | Town | Status | Projected gal | Range | Capacity | % full "
        "| Days past 75% | Days past full | Rate (gpd) | Last pickup | Days |",
        "|---:|---|---|---|---:|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for i, r in enumerate(rows[:limit], 1):
        lines.append(
            f"| {i} | {r['customer']} | {r['town']} | {r['model_status']} | "
            f"{fmt(r['current_projected_gallons'])} | "
            f"{fmt(r['projected_range_low'], 0)}-{fmt(r['projected_range_high'], 0)} | "
            f"{fmt(r['capacity'], 0)} | {fmt(r['display_pct_full'])}% | "
            f"{fmt(r['days_past_75pct'])} | {fmt(r['days_past_capacity'])} | "
            f"{fmt(r['oil_rate_gpd_projected'], 2)} | {r['last_pickup_date']} | "
            f"{r['days_since_last_pickup']} |")
    return "\n".join(lines)


def capacity_table(rows, limit):
    lines = [
        "| Customer | Town | Capacity | Avg collection | Median last 10 | Max ever "
        "| Over cap (2y) | Over cap (all) | Flags |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows[:limit]:
        f = r["_findings"]
        lines.append(
            f"| {r['customer']} | {r['town']} | {fmt(r['capacity'], 0)} | "
            f"{fmt(r['avg_collection'])} | {fmt(f['median_recent'])} | "
            f"{fmt(max(r['_recent_gallons']) if r['_recent_gallons'] else None, 0)} | "
            f"{f['over_recent_years']} | {f['over_all_time']} | {r['flags']} |")
    return "\n".join(lines)


def held_out_table(rows):
    """Held-out rows: history and the reason, no capacity urgency."""
    if not rows:
        return "- none"
    lines = [
        "| Customer | Town | Status | Reason | Last pickup | Days | Rate (gpd) "
        "| Avg collection | Capacity | Flags |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['customer']} | {r['town']} | {r['model_status']} | "
            f"{r['routing_reason'] or '-'} | {r['last_pickup_date']} | "
            f"{r['days_since_last_pickup']} | "
            f"{fmt(r['oil_rate_gpd_projected'], 2)} | {fmt(r['avg_collection'])} | "
            f"{fmt(r['capacity'], 0)} | {r['flags'] or '-'} |")
    return "\n".join(lines)


def overrides_table():
    lines = ["| Id | Customer | Status | Season | Why |", "|---:|---|---|---|---|"]
    for cid, o in sorted(MODEL_OVERRIDES.items(),
                         key=lambda item: (item[1].status, item[1].name)):
        lines.append(f"| {cid} | {o.name} | {o.status} | {o.season or '-'} "
                     f"| {o.reason} |")
    return "\n".join(lines)


def status_counts_table(rows):
    counts = defaultdict(int)
    for r in rows:
        counts[(r["table_section"], r["model_status"])] += 1
    lines = ["| Section | Model status | Customers |", "|---|---|---:|"]
    for (section, status), n in sorted(
            counts.items(), key=lambda kv: (SECTION_ORDER.index(kv[0][0]), kv[0][1])):
        lines.append(f"| {section} | {status} | {n} |")
    return "\n".join(lines)


def write_report(rows, asof, counts):
    ranked = [r for r in rows if r["table_section"] == SECTION_RANKED]
    stale = [r for r in rows if r["table_section"] == SECTION_STALE]
    insufficient = [r for r in rows if r["table_section"] == SECTION_INSUFFICIENT]
    seasonal = [r for r in rows if r["table_section"] == SECTION_SEASONAL]
    on_demand = [r for r in rows if r["table_section"] == SECTION_ON_DEMAND]
    event = [r for r in rows if r["table_section"] == SECTION_EVENT]
    lump_sum = [r for r in rows if r["table_section"] == SECTION_LUMP_SUM]
    will_call = [r for r in rows if r["table_section"] == SECTION_WILL_CALL]

    capacity_problems = sorted(
        (r for r in ranked if FLAG_CAPACITY_STALE in r["flags"]),
        key=lambda r: -(r["_findings"]["over_all_time"]))
    no_capacity = [r for r in rows if r["capacity"] is None]
    low_confidence = [r for r in ranked if r["review_hint"]]
    flagged_overrides = [r for r in ranked if r["model_status"] != STATUS_DEFAULT]

    text = f"""# Oil projection table

Generated by `analysis/build_projection_table.py`. Analysis only -- nothing on
the site reads this folder. Percentages are in percent units.

**As of {asof.isoformat()}**, the latest pickup date in `oil_collections_raw.csv`.

## Methodology

For each active customer, at the as-of date:

- **pooled rate** = gallons over days across at most the last {WINDOW} gaps;
  with {WINDOW + 1}+ pickups this is the previous-6-pickups rate (*x-6* … *x-1*
  over *x-7* to *x-1*, `bt.rate_rolling`). The first pickup a customer ever
  had only marks where the days start: its gallons never enter a rate
- **previous year rate** = gallons since the latest pickup at least
  {YEAR_DAYS} days before the as-of date, over that same span
- **projected rate**, by stage (the ladder, `replay_newcomers.md`):
  *new* (3+ pickups, no previous-year rate) = pooled; *established* = 0.5 x
  previous year + 0.5 x pooled; *seasonal* (passes the seasonal-open test,
  `seasonal_open.py`, scored from complete years before this one) = half
  Sarge's 70/30 (0.7 x last year's rate over the date ±3 weeks + 0.3 x the
  last 2 pickups) + half the last 3 pickups' rate ÷ the month index of the
  days they cover × the index of the days being projected
- **projected gallons** = projected rate x days since the last pickup, or
  since a later empty check (quantity 0 or 1 in `oil_non_pickups.csv`),
  whichever is later: the truck looked and found nothing worth pumping
- **target** = {TARGET_FRACTION:.0%} of listed capacity -- the intended pickup
  point, leaving a spillover buffer
- **implied periodicity** = target ÷ projected rate: the cadence that would
  collect at roughly {TARGET_FRACTION:.0%} full

The 50/50 blend is the default chosen in `analysis/README.md`. Both component
formulas are reused from `backtest_steady_rate.py` rather than restated.

**The ladder.** Fewer than {NEW_MIN_PICKUPS} pickups is *insufficient data*.
A customer not in `MODEL_OVERRIDES` whose gaps over the last
{WILL_CALL_WINDOW_DAYS // 365} years have a median over {WILL_CALL_MEDIAN_GAP}
days, or an sd/mean over {WILL_CALL_GAP_CV} once gaps over
{WILL_CALL_CLOSURE_MULTIPLE}x the median (a closer's off-season) are dropped,
is *will-call*: listed, never projected.

**Supporting definitions.**

- *avg_collection* -- mean gallons of the last {WINDOW} pickups, matching the
  window the rate uses.
- *collections_per_year* -- pickups averaged over complete calendar years
  {COMPLETE_YEARS[0]}-{COMPLETE_YEARS[-1]}, counted from the customer's first
  pickup year so a recent joiner is not divided by three. **2020 is excluded**
  (1,449 pickups against 1,863 in 2019 and 1,895 in 2021 -- a real dip); the
  current year is excluded as incomplete. A year inside the customer's own span
  with no pickups counts as a real zero.
- *confidence range* -- the customer's own backtested 50/50 WAPE where one
  exists and the customer is past the *new* stage ({counts['with_wape']} of the
  {len(ranked)} ranked rows), otherwise the projection x the replay's 20th
  and 80th percentile factors for its stage: {"; ".join(
      f"{k} x{lo:.2f}-x{hi:.2f}" for k, (lo, hi) in RANGE_FACTORS.items())}. The
  low end is clamped at zero, since a few customers score over 100% WAPE.
- *stale* -- no pickup in {STALE_DAYS} days. Every column is still computed,
  but the row is held out of the ranking: `is_active` only means "not dormant
  since 2025-01-01", so it admits customers years past their last pickup and
  rate x days would otherwise put them at the top.

**% full.** The main table shows *display_pct_full*, capped at 100%. The
uncapped *raw_pct_of_listed_capacity* is in the detail CSV, and a ranked row
above 100% carries the flag *{FLAG_ABOVE_CAPACITY}* Projected gallons and
listed capacity are never capped or changed; the ranking sorts on the raw
values, so the cap changes no order.

## Model status and routing

`model_status` is the FINAL status of the rate model for a row. It is
*{STATUS_DEFAULT}* unless the customer is in `MODEL_OVERRIDES`, a
human-reviewed registry keyed by customer id (with the name asserted), or the
data cannot support the model (*insufficient data*, *stale*). Routing, in
precedence order:

1. **{STATUS_ON_DEMAND}**, **{STATUS_EVENT}** and **{STATUS_LUMP_SUM}** accounts
   never take a daily-rate urgency. Each has its own section. No account is
   assigned *{STATUS_LUMP_SUM}*: nothing in the data justifies one yet.
2. **{STATUS_WILL_CALL}** -- the detector above, for customers NOT in the
   registry and not stale. A human override always wins over it. Listed
   longest-since-pickup first.
3. **insufficient data** -- fewer than {NEW_MIN_PICKUPS} pickups.
4. **{STATUS_TRUE_CLOSER}** -- outside its configured month-level season it is
   held out as *off-season*, however long the silence. In season, if the last
   pickup came before the season opened, it is held out as *season open --
   awaiting first pickup*, so the closed months are never counted as
   production. With a pickup this season it is ranked on the 50/50 default.
5. **{STATUS_SEMI_CLOSER}** -- the same, except off-season it is held out only
   once silent longer than max({fringe.SILENCE_FLOOR_DAYS},
   {fringe.SILENCE_GAP_MULTIPLE} x median gap) days -- the fringe table's
   *no recent call signal* rule. A recent off-season call keeps it ranked.
   **{STATUS_DETECTED_CLOSER}** -- a customer NOT in the registry whose
   last {seasonal_open.CLOSED_YEARS_BACK} complete years show a closed block
   (`seasonal_open.closed_months`, `seasonal_closed.md`) is routed exactly like
   a semi-closer, with the detected season. In season, every closer (registry
   or detected) is ranked on a pooled rate over its open-season gaps when it
   has {CLOSER_MIN_OPEN_GAPS}+, not the 50/50, which averages in the closed
   months; range x{RANGE_FACTORS[STAGE_CLOSER][0]:.2f}-x{RANGE_FACTORS[STAGE_CLOSER][1]:.2f}.
6. **stale** -- no pickup in {STALE_DAYS} days.
7. Everything else is ranked, on its stage's rate. **{STATUS_OPEN_ISH}**
   customers are ranked like anyone else; those that pass the seasonal-open
   test take the seasonal rate.

Held-out rows keep their rates, averages, history and capacity flags, but the
main CSV leaves projected gallons, range, % full and the day counts blank --
they are not route signals there. The 50/50 arithmetic is still in the detail
CSV (`model_*`, `raw_pct_of_listed_capacity`) for audit.

*review_hint* is the old automatic heuristic -- likely closer (trough index <
{bsm.CLOSED_INDEX}) or likely seasonal (repeatability >=
{ss.SEASONAL_MIN_REPEAT} and amplitude >= {ss.SEASONAL_MIN_AMPLITUDE}) -- for
customers NOT in the registry. It is a prompt to review, and moves nothing.
Capacity problems are reported in *flags*, separately from the model status.

## Counts

| | |
|---|---:|
| Active customers considered | {counts['active']} |
| Model-ready (a stage rate) | {counts['model_ready']} |
| — stage new / established / seasonal / closer in season | {counts['stages']} |
| Detected closers (not in the registry) | {counts['detected_closers']} |
| — ranked in the main table | {len(ranked)} |
| — seasonal holdouts | {len(seasonal)} |
| — held out as stale | {len(stale)} |
| Event-driven | {len(event)} |
| On-demand / unlimited | {len(on_demand)} |
| Lump-sum / interval-based | {len(lump_sum)} |
| Will-call (detected) | {len(will_call)} |
| Insufficient data | {len(insufficient)} |
| Missing capacity | {len(no_capacity)} |
| With a capacity warning | {counts['capacity_warning']} |
| Ranked and projected above listed capacity | {counts['above_capacity']} |
| Past the {TARGET_FRACTION:.0%} target | {counts['past_target']} |
| Past full capacity | {counts['past_capacity']} |

{status_counts_table(rows)}

## Top 25 urgent stops

Ranked rows only. Sorted by days past capacity, then days past the
{TARGET_FRACTION:.0%} target, then percent full, then projected gallons.

{urgent_table(ranked, 25)}

## Seasonal holdouts ({len(seasonal)})

Closers and call-driven seasonal accounts held out of the ranking, so months
of closure do not read as oil accumulating. Seasons are month-level.

{held_out_table(seasonal)}

## On-demand / unlimited ({len(on_demand)})

Bulk accounts collected on request. Capacity-based timing does not apply;
capacity flags stay as review information.

{held_out_table(on_demand)}

## Will-call ({len(will_call)})

Detected from long or irregular gaps; collected on call, so no fill estimate.

{held_out_table(will_call)}

## Event-driven ({len(event)})

Production follows an event calendar, not a daily rate. No event model is
built here.

{held_out_table(event)}

## Model-status overrides ({len(MODEL_OVERRIDES)})

{overrides_table()}

Overridden customers still ranked: {", ".join(
    f"{r['customer']} ({r['model_status']})" for r in flagged_overrides) or "none"}.

## Top capacity problems

Customers whose recent collections do not fit the listed capacity. These are
**review prompts** -- no capacity is changed by this script.

{capacity_table(capacity_problems, 25) if capacity_problems
 else "None flagged."}

## Customers excluded or low confidence

**Insufficient data ({len(insufficient)}).** Fewer than {NEW_MIN_PICKUPS}
pickups: the first only starts the clock, so a rate needs two more.

**Stale ({len(stale)}).** Model-ready but no pickup in {STALE_DAYS}+ days. All
columns are in the CSV under `table_section = {SECTION_STALE}`.

{chr(10).join(f"- {r['customer']} ({r['town']}) -- last pickup "
              f"{r['last_pickup_date']}, {r['days_since_last_pickup']} days"
              for r in sorted(stale, key=lambda r: -r['days_since_last_pickup'])[:25])
 if stale else "- none"}

**Review hints ({len(low_confidence)}).** Ranked on the 50/50 default and not
in the registry, but the heuristic says the default may not suit them.

{chr(10).join(f"- {r['customer']} ({r['town']}) -- {r['review_hint']}"
              for r in sorted(low_confidence, key=lambda r: r['customer'])[:25])
 if low_confidence else "- none"}

## Caveats

- **The projection estimates oil produced since the last pickup, not the
  volume a truck will collect.** Pickup volume depends on route timing,
  capacity, partial pickups, multiple containers and data quality.
- **{TARGET_FRACTION:.0%} of capacity is the target pickup threshold**, not a
  limit. It leaves a spillover buffer; past it is not an overflow.
- **Capacity flags are review prompts, not automatic corrections.** No listed
  capacity is overwritten by this script.
- **Monthly indices apply only to seasonal-stage customers**, on a rate with
  the season divided out first. Multiplying the 50/50 by an index double-counts the
  season -- measured and rejected in `customer_factor_model_test.md`. The
  month columns shown for everyone else are diagnostic.
- **The diagnostic seasonality score uses the same years it describes.** The
  seasonal stage does not: it is scored from complete years before this one.
- **Confidence ranges come from a backtest of pickup size**, so they describe
  how wrong the rate has been, not how wrong today's container reading is.
- **Seasons are month-level.** A season is taken to open on the 1st of its
  first month; the data supports nothing finer, and no reopening date is
  implied.
- **An in-season closer is ranked on its open-season gaps** when it has
  {CLOSER_MIN_OPEN_GAPS}+; with fewer it falls back to its stage rate, which
  averages in the closed months and likely understates.
- **A new closer is found only after {seasonal_open.CLOSED_YEARS_BACK}
  complete years.** One year of history put 15% of gallons in wrongly closed
  months. Until then its first closures are ranked, then go stale.
- **Overrides are human decisions, not measurements.** The registry is
  informed by `fringe_seasonal_candidates.md` but does not read it. The two
  Okemo accounts rest on two years of history.
"""
    OUT_REPORT.write_text(text)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--as-of", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="projection date (default: latest pickup in the CSV); "
                             "a dated run does not touch oil_projections.json")
    parser.add_argument("--refresh-wape", action="store_true",
                        help=f"rebuild analysis/{WAPE_SNAPSHOT.name} from the "
                             "backtest CSVs, then exit")
    args = parser.parse_args()

    if args.refresh_wape:
        refresh_wape_snapshot()
        return

    pickups = bt.load_pickups(ids=None)
    active = load_active_ids()
    capacities = load_capacities()
    regions = load_regions()
    wape = load_customer_wape()

    latest = max(p["date"] for ps in pickups.values() for p in ps)
    asof = args.as_of or latest
    if asof < latest:
        print(f"  note: --as-of {asof} is before the latest pickup {latest}; "
              "later pickups are still in the history and will leak")

    empty_checks = load_empty_checks(asof)

    _check_registries()
    owner_extras = apply_history_starts(pickups, empty_checks)
    shared_extras = combine_shared(pickups, empty_checks, capacities, regions, active)

    rows = []
    for cid in sorted(active):
        history = pickups.get(cid)
        if not history:
            if cid in owner_extras:
                rows.append(new_owner_row(cid, owner_extras[cid], capacities.get(cid),
                                          regions, asof))
            continue
        row = build_row(cid, history, asof, capacities.get(cid), regions, wape,
                        empty_checks.get(cid, ()))
        extra = shared_extras.get(cid) or owner_extras.get(cid)
        if extra:
            if extra["flags"]:
                row["flags"] = "; ".join(f for f in [row["flags"], *extra["flags"]] if f)
            row["shared_label"] = extra.get("shared_label")
            row["shared_members"] = extra.get("members")
            row["history_start"] = extra.get("history_start")
            row["registry_note"] = extra["note"]
        rows.append(row)

    for r in rows:
        members = r.get("shared_members")
        r["shared_members_text"] = ("; ".join(f"{m['id']} {m['name']}" for m in members)
                                    if members else None)
        if r.get("history_start"):
            r["history_start"] = r["history_start"].isoformat()

    missing = sorted(set(MODEL_OVERRIDES) - {r["customer_id"] for r in rows})
    for cid in missing:
        print(f"  note: MODEL_OVERRIDES[{cid}] ({MODEL_OVERRIDES[cid].name}) is not "
              "an active customer with pickups; override unused")
    for r in rows:
        if (r["customer_id"] in MODEL_OVERRIDES and r["active_season"]
                and r["inferred_active_season"] != r["active_season"]):
            print(f"  note: {r['customer']} is configured {r['active_season']} but "
                  f"the fringe inference now reads {r['inferred_active_season']}")

    rows.sort(key=sort_key)
    rows.sort(key=lambda r: SECTION_ORDER.index(r["table_section"]))

    ranked = [r for r in rows if r["table_section"] == SECTION_RANKED]
    counts = {
        "active": len(rows),
        "model_ready": sum(1 for r in rows
                           if r["oil_rate_gpd_projected"] is not None),
        "stages": " / ".join(str(sum(1 for r in rows if r["stage"] == st))
                             for st in (STAGE_NEW, STAGE_ESTABLISHED, STAGE_SEASONAL,
                                        STAGE_CLOSER)),
        "detected_closers": sum(1 for r in rows if r["model_status"] == STATUS_DETECTED_CLOSER),
        "with_wape": sum(1 for r in ranked if r["model_wape_if_available"] is not None),
        "capacity_warning": sum(1 for r in rows if FLAG_OVER_CAPACITY in r["flags"]
                                or FLAG_CAPACITY_STALE in r["flags"]
                                or FLAG_CAPACITY_LOW in r["flags"]),
        "above_capacity": sum(1 for r in ranked if FLAG_ABOVE_CAPACITY in r["flags"]),
        "past_target": sum(1 for r in ranked if (r["days_past_75pct"] or 0) > 0),
        "past_capacity": sum(1 for r in ranked if (r["days_past_capacity"] or 0) > 0),
    }

    write_main(rows)
    write_detail(rows)
    write_report(rows, asof, counts)
    # The website shows the live edge only. A dated run is an analysis
    # question and must never replace what the site is serving.
    if args.as_of is None:
        write_json(rows, asof)

    def in_section(section):
        return sum(1 for r in rows if r["table_section"] == section)

    print(f"As of {asof.isoformat()}\n")
    print(f"  active customers considered   {counts['active']:>4}")
    print(f"  model-ready (a stage rate)    {counts['model_ready']:>4}")
    print(f"    new / established / seasonal / closer in season  {counts['stages']}")
    print(f"  detected closers              {counts['detected_closers']:>4}")
    for section in SECTION_ORDER:
        print(f"    {section:28}{in_section(section):>4}")
    print(f"  missing capacity              {sum(1 for r in rows if r['capacity'] is None):>4}")
    print(f"  capacity warnings             {counts['capacity_warning']:>4}")
    print(f"  ranked above listed capacity  {counts['above_capacity']:>4}")
    print(f"  past the {TARGET_FRACTION:.0%} target           {counts['past_target']:>4}")
    print(f"  past full capacity            {counts['past_capacity']:>4}")

    print(f"\n  TOP 10 URGENT")
    head = f"{'CUSTOMER':32}{'TOWN':16}{'PROJ':>7}{'CAP':>6}{'%FULL':>7}{'PAST75':>8}{'PASTCAP':>9}"
    print(f"  {head}")
    print("  " + "-" * len(head))
    for r in ranked[:10]:
        print(f"  {r['customer'][:31]:32}{r['town'][:15]:16}"
              f"{r['current_projected_gallons']:>7.0f}{r['capacity'] or 0:>6.0f}"
              f"{r['display_pct_full'] or 0:>6.0f}%{r['days_past_75pct'] or 0:>8.1f}"
              f"{r['days_past_capacity'] or 0:>9.1f}")

    print(f"\nWrote analysis/{OUT_MAIN.name}, {OUT_REPORT.name} and {OUT_DETAIL.name}"
          + (f", and {OUT_JSON.name}" if args.as_of is None else ""))


if __name__ == "__main__":
    main()
