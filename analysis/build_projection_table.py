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

Eligibility is strict. A customer is modelled only when BOTH rates can be
formed; with one missing it is reported as insufficient data rather than
projected from half the model. Customers with no pickup in STALE_DAYS still get
every column but are held out of the urgency ranking, because rate x days grows
without limit and would otherwise fill the top of the table.

Monthly seasonality indices are DIAGNOSTIC ONLY here. They are not multiplied
into the projection: last-6 spans a median of ~131 days and the previous-year
rate carries the same season a year earlier, so both already contain the
seasonal signal, and applying an index on top double-counts it. That was
measured and rejected in analysis/customer_factor_model_test.md.

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

Writes analysis/oil_projection_table.csv, .md and _detail.csv. Analysis only;
writes nothing outside analysis/ and nothing on the site reads this folder.
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import build_fringe_seasonal_candidates as fringe
import seasonality_score as ss

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

COLLECTIONS = ROOT / "oil_collections.json"
OIL_DATA = ROOT / "oil_data.json"
CAPACITY_CACHE = ROOT / "capacity_cache.csv"
ROUTING_WAPE = HERE / "routing_rule_test_customers.csv"
SEASONAL_DETAIL = HERE / "backtest_seasonal_models_detail.csv"

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

# Band when the customer has no backtested WAPE of its own.
DEFAULT_BAND_PCT = 20.0

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
SECTION_STALE = "stale"
SECTION_INSUFFICIENT = "insufficient data"

SECTION_ORDER = [SECTION_RANKED, SECTION_SEASONAL, SECTION_EVENT,
                 SECTION_ON_DEMAND, SECTION_LUMP_SUM, SECTION_STALE,
                 SECTION_INSUFFICIENT]

# Final model statuses. model_status on every row is one of these.
STATUS_DEFAULT = "50/50 default"
STATUS_TRUE_CLOSER = "true closer"
STATUS_SEMI_CLOSER = "semi-closer / call-driven"
STATUS_OPEN_ISH = "seasonal open-ish"
STATUS_EVENT = "event-driven"
STATUS_ON_DEMAND = "on-demand / unlimited"
STATUS_LUMP_SUM = "lump-sum / interval-based"
STATUS_INSUFFICIENT = "insufficient data"
STATUS_STALE = "stale - projection not meaningful"

# Statuses that never take a daily-rate urgency, whatever the date.
NO_RATE_SECTIONS = {
    STATUS_EVENT: SECTION_EVENT,
    STATUS_ON_DEMAND: SECTION_ON_DEMAND,
    STATUS_LUMP_SUM: SECTION_LUMP_SUM,
}
SEASONAL_STATUSES = {STATUS_TRUE_CLOSER, STATUS_SEMI_CLOSER}

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


# ─────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────

def load_active_ids():
    """customer_ids flagged active in the exported roster."""
    data = json.loads(COLLECTIONS.read_text())
    return {c["customer_id"] for c in data["customers"] if c.get("is_active")}


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

    Two sources, no overlap: the held-out routing-rule test, and the seasonal
    backtest detail for the customers that shaped the rule.
    """
    wape = {}

    if ROUTING_WAPE.exists():
        with open(ROUTING_WAPE, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    wape[int(row["customer_id"])] = float(row["50/50"])
                except (KeyError, TypeError, ValueError):
                    continue

    if SEASONAL_DETAIL.exists():
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

    return wape


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


def route(override, has_both_rates, days_since, last_date, pickups, asof):
    """
    (model_status, table_section, routing_reason) for one row.

    Precedence: no-rate overrides -> insufficient data -> seasonal holdouts ->
    stale -> ranked. The seasonal rules run before the stale rule, so a closer
    that has been shut since spring is reported as off-season, not as lost.
    """
    status = override.status if override else STATUS_DEFAULT

    if status in NO_RATE_SECTIONS:
        return status, NO_RATE_SECTIONS[status], "not a daily-rate account"

    if not has_both_rates:
        return (status if override else STATUS_INSUFFICIENT,
                SECTION_INSUFFICIENT, "")

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
        return (status, SECTION_RANKED,
                f"in season ({override.season}); 50/50 rate includes closed "
                "months, so it likely understates the in-season rate")

    if days_since > STALE_DAYS:
        return status if override else STATUS_STALE, SECTION_STALE, ""

    return status, SECTION_RANKED, ""


# ─────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────

def build_row(cid, pickups, asof, capacity, regions, wape):
    last = pickups[-1]
    days_since = (asof - last["date"]).days

    last6 = rate_last_six(pickups)
    prev_year = rate_prev_year_asof(pickups, asof)
    has_both = last6 is not None and prev_year is not None
    rate = (last6 + prev_year) / 2 if has_both else None

    avg_collection = average_collection(pickups)
    season = seasonality(pickups)
    findings = capacity_findings(pickups, capacity, avg_collection, asof)

    override = MODEL_OVERRIDES.get(cid)
    if override and override.name != last["name"]:
        raise SystemExit(
            f"MODEL_OVERRIDES[{cid}] expects {override.name!r} but the pickup "
            f"record says {last['name']!r}. Check the id before trusting the "
            "override.")
    status, section, routing_reason = route(
        override, has_both, days_since, last["date"], pickups, asof)
    hint = None if override else review_hint(season)

    projected = rate * days_since if rate is not None else None
    target = capacity * TARGET_FRACTION if capacity is not None else None

    # Confidence band: the customer's own backtested WAPE where it exists,
    # otherwise a flat default. Six customers score over 100% WAPE, so the low
    # end is clamped at zero rather than allowed to go negative.
    band_pct = wape.get(cid, DEFAULT_BAND_PCT)
    band_used = (f"customer WAPE {wape[cid]:.1f}%" if cid in wape
                 else f"default +/-{DEFAULT_BAND_PCT:.0f}%")
    low = high = None
    if projected is not None:
        low = max(0.0, projected * (1 - band_pct / 100))
        high = projected * (1 + band_pct / 100)

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
    inferred = inferred_season(pickups) if configured_season else None

    return {
        "table_section": section,
        "customer_id": cid,
        "customer": last["name"],
        "town": last["town"],
        "region_names": "; ".join(regions.get(cid, [])),
        "model_status": status,
        "routing_reason": routing_reason,
        "active_season": configured_season,
        "review_hint": hint,
        "flags": "; ".join(findings["flags"]),
        "last_pickup_date": last["date"].isoformat(),
        "days_since_last_pickup": days_since,
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
    "model_status", "routing_reason", "active_season", "review_hint",
    "flags", "last_pickup_date", "days_since_last_pickup",
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
    "customer_id", "customer", "town", "table_section", "model_status",
    "routing_reason", "active_season", "inferred_active_season",
    "override_reason", "review_hint", "flags",
    "pickup_count", "first_pickup", "last_pickup_date",
    "days_since_last_pickup", "oil_rate_gpd_prev_6_pickups",
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

- **previous 6 pickups rate** = gallons at *x-6* … *x-1* over the days from
  *x-7* to *x-1* (`bt.rate_rolling`, the backtest's own window)
- **previous year rate** = gallons since the latest pickup at least
  {YEAR_DAYS} days before the as-of date, over that same span
- **projected rate** = 0.5 x previous year + 0.5 x previous 6 pickups
- **projected gallons** = projected rate x days since the last pickup
- **target** = {TARGET_FRACTION:.0%} of listed capacity -- the intended pickup
  point, leaving a spillover buffer
- **implied periodicity** = target ÷ projected rate: the cadence that would
  collect at roughly {TARGET_FRACTION:.0%} full

The 50/50 blend is the default chosen in `analysis/README.md`. Both component
formulas are reused from `backtest_steady_rate.py` rather than restated.

**Eligibility is strict.** A customer is projected only when BOTH rates can be
formed. With one missing it is reported as *insufficient data* and no
projection is emitted, rather than projecting from half the model.

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
  exists ({counts['with_wape']} of the {len(ranked)} ranked rows), otherwise a
  flat +/-{DEFAULT_BAND_PCT:.0f}%. The low end is clamped at zero, since a few
  customers score over 100% WAPE.
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
2. **insufficient data** -- one of the two rates cannot be formed.
3. **{STATUS_TRUE_CLOSER}** -- outside its configured month-level season it is
   held out as *off-season*, however long the silence. In season, if the last
   pickup came before the season opened, it is held out as *season open --
   awaiting first pickup*, so the closed months are never counted as
   production. With a pickup this season it is ranked on the 50/50 default.
4. **{STATUS_SEMI_CLOSER}** -- the same, except off-season it is held out only
   once silent longer than max({fringe.SILENCE_FLOOR_DAYS},
   {fringe.SILENCE_GAP_MULTIPLE} x median gap) days -- the fringe table's
   *no recent call signal* rule. A recent off-season call keeps it ranked.
5. **stale** -- no pickup in {STALE_DAYS} days.
6. Everything else is ranked. **{STATUS_OPEN_ISH}** customers stay here on the
   50/50 default: strongly seasonal, but they never shut.

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
| Model-ready (both rates) | {counts['model_ready']} |
| — ranked in the main table | {len(ranked)} |
| — seasonal holdouts | {len(seasonal)} |
| — held out as stale | {len(stale)} |
| Event-driven | {len(event)} |
| On-demand / unlimited | {len(on_demand)} |
| Lump-sum / interval-based | {len(lump_sum)} |
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

**Insufficient data ({len(insufficient)}).** One of the two rates could not be
formed -- typically fewer than {WINDOW + 1} pickups, or no pickup old enough to
anchor a previous-year window.

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
- **Monthly indices are diagnostic only in this version.** They are not applied
  to the projection: last-6 and the previous-year rate already carry the
  season, and multiplying by an index double-counts it -- measured and rejected
  in `customer_factor_model_test.md`.
- **A customer's seasonality score uses the same years it would be scored on.**
  A production version would score from history before each prediction.
- **Confidence ranges come from a backtest of pickup size**, so they describe
  how wrong the rate has been, not how wrong today's container reading is.
- **Seasons are month-level.** A season is taken to open on the 1st of its
  first month; the data supports nothing finer, and no reopening date is
  implied.
- **An in-season closer is ranked on a rate that includes its closed months.**
  The previous-year rate spans the whole year, so it likely understates the
  in-season rate. No seasonal rate model is applied here; that needs its own
  backtest.
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
                        help="projection date (default: latest pickup in the CSV)")
    args = parser.parse_args()

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

    rows = []
    for cid in sorted(active):
        history = pickups.get(cid)
        if not history:
            continue
        rows.append(build_row(cid, history, asof, capacities.get(cid), regions, wape))

    missing = sorted(set(MODEL_OVERRIDES) - {r["customer_id"] for r in rows})
    for cid in missing:
        print(f"  note: MODEL_OVERRIDES[{cid}] ({MODEL_OVERRIDES[cid].name}) is not "
              "an active customer with pickups; override unused")
    for r in rows:
        if r["active_season"] and r["inferred_active_season"] != r["active_season"]:
            print(f"  note: {r['customer']} is configured {r['active_season']} but "
                  f"the fringe inference now reads {r['inferred_active_season']}")

    rows.sort(key=sort_key)
    rows.sort(key=lambda r: SECTION_ORDER.index(r["table_section"]))

    ranked = [r for r in rows if r["table_section"] == SECTION_RANKED]
    counts = {
        "active": len(rows),
        "model_ready": sum(1 for r in rows
                           if r["oil_rate_gpd_projected"] is not None),
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

    def in_section(section):
        return sum(1 for r in rows if r["table_section"] == section)

    print(f"As of {asof.isoformat()}\n")
    print(f"  active customers considered   {counts['active']:>4}")
    print(f"  model-ready (both rates)      {counts['model_ready']:>4}")
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

    print(f"\nWrote analysis/{OUT_MAIN.name}, {OUT_REPORT.name} and {OUT_DETAIL.name}")


if __name__ == "__main__":
    main()
