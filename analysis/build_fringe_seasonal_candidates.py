#!/usr/bin/env python3
"""
Fringe / closer-status candidate review table.

Which customers are seasonal, and in what way? This surfaces candidates for a
human to classify. IT MAKES NO FINAL CLASSIFICATION -- every label here is a
sorting tool, and `candidate_reason` always says which test fired.

WHY NOT A SINGLE ACTIVITY-PROBABILITY THRESHOLD

    Many customers have only 2-5 usable years. One statistic computed from
    three observations is not stable enough to decide closure on: a single
    quiet month, or a startup year only partly covered, moves it a long way.
    So this computes SEVERAL INDEPENDENT SIGNALS -- the averaged monthly shape,
    the per-year shapes, repeated zero blocks, theme-region membership and the
    current projection status -- and shows them side by side. Where one signal
    is unavailable (repeatability needs 3 years) the others still stand.

ZERO-BLOCK LOGIC

    A closed season is a RUN of quiet months, not a count of them. Runs here are
    CYCLIC, so Nov-Dec-Jan-Feb-Mar is one five-month winter block rather than a
    run of two and a run of three. A block is only evidence if it REPEATS: the
    per-year signals ask whether the same months were quiet in at least two
    separate years, and whether that happened in consecutive years.

    A month with no production is a real zero, not missing data -- the house
    rule from CLAUDE.md.

SKI / SNACK / GOLF ARE FOUND BY CUSTOMER ID, NEVER BY NAME

    CLAUDE.md: membership is by customer id, never a name match -- "Winooski"
    contains "ski" and "Blodgett" contains "lodge". The ski and snack groupings
    come from the site's own THEME REGIONS. A word-boundary `name_clue` column
    exists as a supplementary review hint and to widen candidate selection, but
    no label is ever assigned from a name alone.

METHOD

    Monthly production uses seasonality_score.py's spreading, unchanged: each
    pickup's gallons are spread back over the days since the previous pickup,
    capped at 60 days. A month's INDEX is its gallons per day over the
    customer-year's average month, so 1.00 is an average month and 0.00 is a
    month with no production.

    Scored over complete years 2023-2025, matching seasonality_scores.csv --
    recomputing reproduces all 59 of its published rows exactly, which this
    script checks on every run. A customer needs 2 of those years to be scored
    at all; repeatability needs 3 and is left blank below that, rather than
    reporting a correlation drawn from two observations.

Reads oil_collections_raw.csv, which already holds only qualifying pickups:
EMPTY_QTYS {0,1,2,3} were removed upstream and 4-gallon records are retained as
exactly 4. bt.load_pickups ASSERTS that rather than re-filtering.

    python3 analysis/build_fringe_seasonal_candidates.py
    python3 analysis/build_fringe_seasonal_candidates.py --as-of 2026-06-30

Writes analysis/fringe_seasonal_candidates.csv and .md. Analysis only; writes
nothing outside analysis/ and nothing on the site reads this folder.
"""

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

import backtest_steady_rate as bt
import customer_factors as cf
import seasonality_score as ss

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

COLLECTIONS = ROOT / "oil_collections.json"
OIL_DATA = ROOT / "oil_data.json"
CAPACITY_CACHE = ROOT / "capacity_cache.csv"
SCORES_CSV = HERE / "seasonality_scores.csv"
PROJECTION_CSV = HERE / "oil_projection_table.csv"

OUT_CSV = HERE / "fringe_seasonal_candidates.csv"
OUT_REPORT = HERE / "fringe_seasonal_candidates.md"

MONTHS = ss.MONTHS
SCORE_YEARS = ss.SCORE_YEARS          # (2023, 2024, 2025) -- matches the published CSV

MIN_YEARS = 2                         # to be scored at all
MIN_YEARS_REPEATABILITY = 3           # cf.repeatability's own floor

ZERO = 1e-9                           # an index of exactly zero, in floating point
NEAR_ZERO = 0.10
LOW = 0.25
ACTIVE = 0.25                         # a month counts as in-season at or above this

# Silence is judged against the customer's own cadence: a snack bar on a 14-day
# summer cycle and a ski lodge on a 45-day one both get a fair test.
SILENCE_GAP_MULTIPLE = 2
SILENCE_FLOOR_DAYS = 60

DORMANT_FROM_YEAR = 2024              # inactive, but recent enough to still be a closer

# Label thresholds.
TRUE_CLOSER_RUN = 3
POSSIBLE_CLOSER_RUN = 2
TRUE_CLOSER_TROUGH = 0.10
POSSIBLE_CLOSER_TROUGH = 0.20
SEASONAL_AMPLITUDE = 0.25
SEASONAL_REPEATABILITY = 0.60
SEMI_CLOSER_OFFSEASON = 2             # isolated off-season year-months
EVENT_AMPLITUDE = 0.50
EVENT_PEAK_INDEX = 2.0
EVENT_PEAK_MAX_MONTHS = 2
ONDEMAND_GALLONS = 4000
ONDEMAND_OVER_CAP = 5

# Supplementary only. Word boundaries, so "Winooski" is not a ski slope.
NAME_CLUE = re.compile(
    r"\b(ski|lodge|fair|fairground|fairgrounds|golf|snack|expo|event|festival|"
    r"creemee|orchard|gorge|resort|mountain|peak|slope|drive[- ]?in|campground|"
    r"beach|seasonal|summer|winter|country club|ice cream|food truck)\b", re.I)
EVENT_CLUE = re.compile(r"\b(expo|fair|fairground|fairgrounds|event|festival)\b", re.I)

LABEL_ORDER = [
    "likely true closer",
    "possible closer",
    "semi-closer / call-driven seasonal",
    "event-driven seasonal",
    "seasonal but open-ish",
    "erratic / needs review",
    "on-demand / unlimited review",
    "too little history / cannot score yet",
    "steady enough / not closer candidate",
]

NEXT_REVIEW = {
    "likely true closer": "confirm closure dates with the customer; needs a reopening rule",
    "possible closer": "check whether the quiet months are a closure or just a low season",
    "semi-closer / call-driven seasonal": "confirm whether off-season pickups are on call",
    "event-driven seasonal": "confirm the event calendar; model per event, not per day",
    "seasonal but open-ish": "keep on the 50/50 default; revisit if monthly bias is large",
    "erratic / needs review": "inspect the pickup history by hand before modelling",
    "on-demand / unlimited review": "confirm container type and capacity; not a barrel",
    "too little history / cannot score yet": "revisit after a full season of data",
    "steady enough / not closer candidate": "no action",
}


# ─────────────────────────────────────────────
# Shape helpers
# ─────────────────────────────────────────────

def cyclic_run(shape, predicate):
    """Longest run of months satisfying `predicate`, wrapping December to January."""
    flags = [1 if predicate(v) else 0 for v in shape]
    if all(flags):
        return 12
    best = current = 0
    for flag in flags * 2:                 # doubled so a run can wrap the year end
        current = current + 1 if flag else 0
        best = max(best, current)
    return min(best, 12)


def cyclic_blocks(month_indexes):
    """Maximal cyclic runs of a set of month numbers, as 'Nov-Mar' strings."""
    months = set(month_indexes)
    if not months:
        return []
    if len(months) == 12:
        return ["Jan-Dec (every month)"]

    blocks = []
    for start in range(12):
        if start in months and (start - 1) % 12 not in months:
            length = 0
            while (start + length) % 12 in months:
                length += 1
            end = (start + length - 1) % 12
            blocks.append(MONTHS[start] if length == 1
                          else f"{MONTHS[start]}-{MONTHS[end]}")
    return blocks


def active_block(shape):
    """
    The main in-season block: the longest cyclic run of months at or above
    ACTIVE. Returns (start_month, end_month, span, set_of_month_indexes).
    """
    flags = [1 if v >= ACTIVE else 0 for v in shape]
    if not any(flags):
        return None, None, 0, set()
    if all(flags):
        return MONTHS[0], MONTHS[11], 12, set(range(12))

    best_length, best_start = 0, 0
    for start in range(12):
        if flags[start] and not flags[(start - 1) % 12]:
            length = 0
            while flags[(start + length) % 12] and length < 12:
                length += 1
            if length > best_length:
                best_length, best_start = length, start

    members = {(best_start + i) % 12 for i in range(best_length)}
    end = (best_start + best_length - 1) % 12
    return MONTHS[best_start], MONTHS[end], best_length, members


# ─────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────

def load_scope(pickups):
    """
    Active customers, plus inactive ones whose last pickup is recent enough
    that they may be a closer rather than a lost customer.

    is_active only means "not dormant since 2025-01-01", so a customer whose
    season ended in autumn 2024 is marked inactive despite being a live
    seasonal account. Those are exactly the fringe cases this table exists for.
    """
    data = json.loads(COLLECTIONS.read_text())
    active, dormant = set(), set()
    for customer in data["customers"]:
        cid = customer["customer_id"]
        if cid not in pickups:
            continue
        if customer.get("is_active"):
            active.add(cid)
        elif pickups[cid][-1]["date"].year >= DORMANT_FROM_YEAR:
            dormant.add(cid)
    return active, dormant


def load_theme_regions():
    """theme region name -> set of customer ids, and id -> [theme names]."""
    data = json.loads(OIL_DATA.read_text())
    names = list(data.get("theme_regions", ()))
    by_region = {n: set(data["region_customers"].get(n, ())) for n in names}
    by_customer = defaultdict(list)
    for name in names:
        for cid in by_region[name]:
            by_customer[cid].append(name)
    return by_region, by_customer


def load_capacities():
    capacities = {}
    with open(CAPACITY_CACHE, newline="") as f:
        for row in csv.DictReader(f):
            try:
                value = float(row["capacity"])
            except (TypeError, ValueError):
                value = 0.0
            capacities[int(row["customer_id"])] = value if value > 0 else None
    return capacities


def load_published_scores():
    if not SCORES_CSV.exists():
        return {}
    return {int(r["customer_id"]): r for r in csv.DictReader(SCORES_CSV.open())}


def load_projection_status():
    if not PROJECTION_CSV.exists():
        return {}
    return {int(r["customer_id"]): r["model_status"]
            for r in csv.DictReader(PROJECTION_CSV.open())}


# ─────────────────────────────────────────────
# Per-customer measures
# ─────────────────────────────────────────────

def profile(pickups):
    """{year: [12 monthly indexes]} for the score years with production."""
    daily = ss.daily_production(pickups)
    years = {}
    for year in SCORE_YEARS:
        index = cf.year_index(daily, year)
        if index is not None:
            years[year] = index
    return years


def average_gallons(pickups):
    """Matches ss.score: always divided by the number of score years."""
    total = sum(p["gallons"] for p in pickups if p["date"].year in SCORE_YEARS)
    return total / len(SCORE_YEARS)


def median_gap(pickups):
    gaps = [(b["date"] - a["date"]).days for a, b in zip(pickups, pickups[1:])
            if b["date"].year in SCORE_YEARS and b["date"] > a["date"]]
    return statistics.median(gaps) if gaps else None


def per_year_signals(years, shape, active_members):
    """Evidence that a quiet block REPEATS, rather than happening once."""
    quiet = {y: [v <= NEAR_ZERO for v in index] for y, index in years.items()}
    ordered = sorted(years)

    repeated = {m for m in range(12) if sum(quiet[y][m] for y in ordered) >= 2}

    def block_repeats(length):
        """A run of `length` quiet months shared by at least 2 years."""
        for start in range(12):
            hits = sum(1 for y in ordered
                       if all(quiet[y][(start + i) % 12] for i in range(length)))
            if hits >= 2:
                return True
        return False

    def same_block_consecutive():
        for earlier, later in zip(ordered, ordered[1:]):
            if later - earlier != 1:
                continue
            shared = {m for m in range(12) if quiet[earlier][m] and quiet[later][m]}
            if shared and max(_run_length(shared, s) for s in range(12)) >= 2:
                return True
        return False

    def _run_length(members, start):
        if start not in members:
            return 0
        length = 0
        while (start + length) % 12 in members and length < 12:
            length += 1
        return length

    def months_quiet_in_years(month_indexes):
        """How many years have ALL of these months quiet."""
        return sum(1 for y in ordered
                   if all(quiet[y][m] for m in month_indexes))

    # Isolated off-season production: a month the averaged shape calls quiet,
    # but which had real production in some individual year.
    off_season = sum(1 for y in ordered for m in range(12)
                     if shape[m] <= NEAR_ZERO and years[y][m] > 0)

    shoulders = []
    if active_members and len(active_members) < 12:
        # The block can wrap the year end, so its edges are the months whose
        # outside neighbour is missing — not min() and max(), which would read
        # a Nov-Mar season as starting in January.
        start = next(m for m in range(12)
                     if m in active_members and (m - 1) % 12 not in active_members)
        end = next(m for m in range(12)
                   if m in active_members and (m + 1) % 12 not in active_members)
        for m in ((start - 1) % 12, (end + 1) % 12):
            if 0 < shape[m] < ACTIVE:
                shoulders.append(f"{MONTHS[m]} {shape[m]:.2f}")

    return {
        "repeated_zero_months": sorted(repeated),
        "repeated_zero_month_blocks": cyclic_blocks(repeated),
        "has_2": block_repeats(2),
        "has_3": block_repeats(3),
        "same_block_consecutive": same_block_consecutive(),
        "winter": months_quiet_in_years((11, 0, 1)) >= 2 or months_quiet_in_years((0, 1, 2)) >= 2,
        "summer": months_quiet_in_years((5, 6, 7)) >= 2,
        "one_off_offseason": off_season,
        "shoulders": ", ".join(shoulders),
    }


def capacity_signals(pickups, capacity, asof):
    """Evidence a customer is not a normal barrel."""
    if capacity is None:
        return {"over_recent": 0, "mean_recent": None}
    cutoff = date(asof.year - 2, asof.month, 1)
    recent = [p["gallons"] for p in pickups[-10:]]
    return {
        "over_recent": sum(1 for p in pickups
                           if p["date"] >= cutoff and p["gallons"] > capacity),
        "mean_recent": statistics.fmean(recent) if recent else None,
    }


# ─────────────────────────────────────────────
# Labelling
# ─────────────────────────────────────────────

def classify(row):
    """
    A sorting tool, not a verdict. Returns (label, reason).

    The closer tests are evaluated as a group and then split on whether the
    quiet months are clean: a customer that meets a closer test but also shows
    repeated isolated off-season production is call-driven, not closed.
    """
    if row["years_used"] < MIN_YEARS:
        return ("too little history / cannot score yet",
                f"only {row['years_used']} usable year(s) in "
                f"{SCORE_YEARS[0]}-{SCORE_YEARS[-1]}")

    amplitude, repeat = row["amplitude"], row["repeatability"]
    trough, run = row["trough_index"], row["longest_near_zero_run"]
    peak_months = sum(1 for v in row["shape"] if v >= EVENT_PEAK_INDEX)

    if (row["event_name_clue"] and amplitude >= EVENT_AMPLITUDE
            and 1 <= peak_months <= EVENT_PEAK_MAX_MONTHS):
        return ("event-driven seasonal",
                f"event name, swing {amplitude:.2f}, production concentrated in "
                f"{peak_months} month(s) at {EVENT_PEAK_INDEX}x or more")

    if row["avg_gallons_per_year"] >= ONDEMAND_GALLONS:
        return ("on-demand / unlimited review",
                f"{row['avg_gallons_per_year']:,.0f} gallons a year — too large to "
                "model as a barrel")
    if (row["capacity"] and row["over_capacity_recent"] >= ONDEMAND_OVER_CAP
            and row["mean_recent_collection"]
            and row["mean_recent_collection"] > row["capacity"]):
        return ("on-demand / unlimited review",
                f"{row['over_capacity_recent']} collections over listed capacity in 2 "
                "years and the average pickup exceeds it")

    true_closer = (run >= TRUE_CLOSER_RUN or row["has_3_month_zero_block_repeated"]
                   or (trough <= TRUE_CLOSER_TROUGH and repeat is not None
                       and repeat >= SEASONAL_REPEATABILITY
                       and amplitude >= SEASONAL_AMPLITUDE))
    possible = (run >= POSSIBLE_CLOSER_RUN or row["has_2_month_zero_block_repeated"]
                or (trough <= POSSIBLE_CLOSER_TROUGH and amplitude >= SEASONAL_AMPLITUDE))

    if true_closer or possible:
        if row["one_off_offseason_pickups"] >= SEMI_CLOSER_OFFSEASON:
            return ("semi-closer / call-driven seasonal",
                    f"quiet block present but {row['one_off_offseason_pickups']} "
                    "isolated off-season month(s) of production")
        if true_closer:
            reason = (f"longest near-zero run {run} months" if run >= TRUE_CLOSER_RUN
                      else "a 3-month quiet block repeats across years"
                      if row["has_3_month_zero_block_repeated"]
                      else f"trough {trough:.2f} repeating at {repeat:.2f}")
            return "likely true closer", reason
        reason = (f"longest near-zero run {run} months" if run >= POSSIBLE_CLOSER_RUN
                  else "a 2-month quiet block repeats across years"
                  if row["has_2_month_zero_block_repeated"]
                  else f"trough {trough:.2f} with swing {amplitude:.2f}")
        return "possible closer", reason

    if (repeat is not None and repeat >= SEASONAL_REPEATABILITY
            and amplitude >= SEASONAL_AMPLITUDE):
        return ("seasonal but open-ish",
                f"swing {amplitude:.2f} repeating at {repeat:.2f}, but the trough is "
                f"{trough:.2f} — never actually shuts")
    if amplitude >= SEASONAL_AMPLITUDE:
        return ("erratic / needs review",
                f"swing {amplitude:.2f} but repeatability "
                f"{'unavailable' if repeat is None else format(repeat, '.2f')}")
    return ("steady enough / not closer candidate",
            f"swing {amplitude:.2f} is inside the noise floor")


# ─────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────

def build_row(cid, pickups, asof, context):
    years = profile(pickups)
    name = pickups[-1]["name"]
    last = pickups[-1]["date"]
    days_since = (asof - last).days
    gap = median_gap(pickups)

    row = {
        "customer_id": cid,
        "name": name,
        "town": pickups[-1]["town"],
        "current_class": context["published"].get(cid, {}).get("class", "(not scored)"),
        "avg_gallons_per_year": average_gallons(pickups),
        "median_gap_days": gap,
        "years_used": len(years),
        "last_pickup_date": last.isoformat(),
        "days_since_last_pickup": days_since,
        "current_month": MONTHS[asof.month - 1],
        "theme_regions": "; ".join(context["themes_by_customer"].get(cid, [])),
        "name_clue": bool(NAME_CLUE.search(name)),
        "event_name_clue": bool(EVENT_CLUE.search(name)),
        "projection_status": context["projection"].get(cid, ""),
        "is_dormant": cid in context["dormant"],
        "capacity": context["capacities"].get(cid),
        "shape": None,
    }

    caps = capacity_signals(pickups, row["capacity"], asof)
    row["over_capacity_recent"] = caps["over_recent"]
    row["mean_recent_collection"] = caps["mean_recent"]

    if len(years) < MIN_YEARS:
        row.update({
            "amplitude": None, "repeatability": None, "peak_month": None,
            "peak_index": None, "trough_month": None, "trough_index": None,
            "zero_month_count": None, "near_zero_month_count": None,
            "low_month_count": None, "longest_zero_run": None,
            "longest_near_zero_run": None, "zero_months": "", "near_zero_months": "",
            "active_months": "", "active_season_start": None, "active_season_end": None,
            "active_span_months": None, "peak_to_trough_ratio": None,
            "trough_is_zero": None, "repeated_zero_months": "",
            "repeated_zero_month_blocks": "",
            "has_2_month_zero_block_repeated": None,
            "has_3_month_zero_block_repeated": None,
            "has_same_zero_block_consecutive_years": None,
            "winter_zero_pattern": None, "summer_zero_pattern": None,
            "shoulder_month_activity": "", "one_off_offseason_pickups": None,
            "current_month_index": None, "currently_in_active_season": None,
            "seasonal_silence_flag": "",
        })
        row["candidate_label"], row["candidate_reason"] = classify(row)
        row["recommended_next_review"] = NEXT_REVIEW[row["candidate_label"]]
        return row

    ordered = sorted(years)
    shape = [statistics.fmean(years[y][m] for y in ordered) for m in range(12)]
    peak = max(range(12), key=shape.__getitem__)
    trough = min(range(12), key=shape.__getitem__)
    start, end, span, members = active_block(shape)
    signals = per_year_signals(years, shape, members)

    in_season = (asof.month - 1) in members
    silence = ""
    threshold = max(SILENCE_FLOOR_DAYS, SILENCE_GAP_MULTIPLE * gap) if gap else SILENCE_FLOOR_DAYS
    if not in_season and days_since > threshold:
        silence = "off-season / no recent call signal"

    row.update({
        "shape": shape,
        "amplitude": statistics.pstdev(shape),
        "repeatability": cf.repeatability(years, SCORE_YEARS),
        "peak_month": MONTHS[peak], "peak_index": shape[peak],
        "trough_month": MONTHS[trough], "trough_index": shape[trough],
        "zero_month_count": sum(1 for v in shape if v <= ZERO),
        "near_zero_month_count": sum(1 for v in shape if v <= NEAR_ZERO),
        "low_month_count": sum(1 for v in shape if v <= LOW),
        "longest_zero_run": cyclic_run(shape, lambda v: v <= ZERO),
        "longest_near_zero_run": cyclic_run(shape, lambda v: v <= NEAR_ZERO),
        "zero_months": " ".join(MONTHS[m] for m in range(12) if shape[m] <= ZERO),
        "near_zero_months": " ".join(MONTHS[m] for m in range(12) if shape[m] <= NEAR_ZERO),
        "active_months": " ".join(MONTHS[m] for m in range(12) if shape[m] >= ACTIVE),
        "active_season_start": start, "active_season_end": end,
        "active_span_months": span,
        "peak_to_trough_ratio": (shape[peak] / shape[trough]
                                 if shape[trough] > ZERO else None),
        "trough_is_zero": shape[trough] <= ZERO,
        "repeated_zero_months": " ".join(MONTHS[m] for m in signals["repeated_zero_months"]),
        "repeated_zero_month_blocks": " ".join(signals["repeated_zero_month_blocks"]),
        "has_2_month_zero_block_repeated": signals["has_2"],
        "has_3_month_zero_block_repeated": signals["has_3"],
        "has_same_zero_block_consecutive_years": signals["same_block_consecutive"],
        "winter_zero_pattern": signals["winter"],
        "summer_zero_pattern": signals["summer"],
        "shoulder_month_activity": signals["shoulders"],
        "one_off_offseason_pickups": signals["one_off_offseason"],
        "current_month_index": shape[asof.month - 1],
        "currently_in_active_season": in_season,
        "seasonal_silence_flag": silence,
    })
    row["candidate_label"], row["candidate_reason"] = classify(row)
    row["recommended_next_review"] = NEXT_REVIEW[row["candidate_label"]]
    return row


def is_candidate(row, context):
    """Any one signal is enough to earn a review row."""
    published = context["published"].get(row["customer_id"], {})
    reasons = []
    if published.get("class") == "seasonal":
        reasons.append("class seasonal")
    if published.get("class") == "erratic" and (row["amplitude"] or 0) >= SEASONAL_AMPLITUDE:
        reasons.append("class erratic with a real swing")
    if row["shape"]:
        if row["trough_index"] <= LOW:
            reasons.append(f"trough {row['trough_index']:.2f}")
        if row["longest_near_zero_run"] >= POSSIBLE_CLOSER_RUN:
            reasons.append(f"{row['longest_near_zero_run']}-month quiet run")
        if row["avg_gallons_per_year"] >= 500 and row["amplitude"] >= 0.20:
            reasons.append("500+ gallons with a swing")
    if row["name_clue"]:
        reasons.append("name clue")
    if row["theme_regions"]:
        reasons.append("theme region")
    if row["projection_status"].startswith(("likely closer", "likely seasonal")):
        reasons.append("projection flag")
    return reasons


def sort_key(row):
    def desc(value):
        return -value if value is not None else 1.0
    return (
        LABEL_ORDER.index(row["candidate_label"]),
        desc(row["longest_near_zero_run"]),
        desc(row["repeatability"]),
        desc(row["amplitude"]),
        desc(row["avg_gallons_per_year"]),
        row["customer_id"],
    )


# ─────────────────────────────────────────────
# Regression check
# ─────────────────────────────────────────────

def check_published(rows, published):
    """
    The 2023-25 window must reproduce seasonality_scores.csv exactly.

    A shared change to the spreading method would show up here first.
    """
    matched, mismatched = 0, []
    by_id = {r["customer_id"]: r for r in rows}
    for cid, want in published.items():
        got = by_id.get(cid)
        if not got or not got["shape"]:
            mismatched.append((cid, want["name"], "not scored"))
            continue
        same = abs(got["amplitude"] - float(want["amplitude"])) < 0.0015
        if got["repeatability"] is not None:
            same = same and abs(got["repeatability"] - float(want["repeatability"])) < 0.0015
        same = same and all(abs(got["shape"][i] - float(want[MONTHS[i]])) < 0.0015
                            for i in range(12))
        if same:
            matched += 1
        else:
            mismatched.append((cid, want["name"], f"amplitude {got['amplitude']:.3f} "
                                                  f"vs {want['amplitude']}"))
    return matched, mismatched


# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────

FIELDS = [
    "customer_id", "name", "town", "current_class", "avg_gallons_per_year",
    "median_gap_days", "amplitude", "repeatability", "peak_month", "peak_index",
    "trough_month", "trough_index", "zero_month_count", "near_zero_month_count",
    "low_month_count", "longest_zero_run", "longest_near_zero_run", "zero_months",
    "near_zero_months", "active_months", "active_season_start", "active_season_end",
    "active_span_months", "peak_to_trough_ratio", "years_used",
    "repeated_zero_months", "repeated_zero_month_blocks",
    "has_2_month_zero_block_repeated", "has_3_month_zero_block_repeated",
    "has_same_zero_block_consecutive_years", "winter_zero_pattern",
    "summer_zero_pattern", "one_off_offseason_pickups", "last_pickup_date",
    "days_since_last_pickup", "current_month", "current_month_index",
    "currently_in_active_season", "seasonal_silence_flag", "candidate_label",
    "candidate_reason", "recommended_next_review",
    # Supplementary context, beyond the requested columns.
    "shoulder_month_activity", "trough_is_zero", "theme_regions", "name_clue",
    "projection_status", "is_dormant", "capacity", "over_capacity_recent",
    "selected_because",
] + MONTHS

ROUNDING = {
    "avg_gallons_per_year": 0, "median_gap_days": 1, "amplitude": 3,
    "repeatability": 3, "peak_index": 2, "trough_index": 2,
    "peak_to_trough_ratio": 1, "current_month_index": 2, "capacity": 0,
    "mean_recent_collection": 1,
}


def presented(row):
    out = {}
    for key in FIELDS:
        if key in MONTHS:
            index = MONTHS.index(key)
            out[key] = round(row["shape"][index], 3) if row["shape"] else ""
            continue
        value = row.get(key)
        if isinstance(value, float) and key in ROUNDING:
            digits = ROUNDING[key]
            value = round(value, digits) if digits else round(value)
        out[key] = "" if value is None else value
    return out


def fmt(value, digits=2, dash="-"):
    if value is None or value == "":
        return dash
    return f"{value:,.{digits}f}"


def table(rows, limit=25, extra=None):
    head = ("| Customer | Town | Gal/yr | Yrs | Ampl | Repeat | Quiet run | "
            "Quiet months | Season | Reason |")
    lines = [head, "|---|---|---:|---:|---:|---:|---:|---|---|---|"]
    for r in rows[:limit]:
        lines.append(
            f"| {r['name']} | {r['town']} | {fmt(r['avg_gallons_per_year'], 0)} | "
            f"{r['years_used']} | {fmt(r['amplitude'])} | {fmt(r['repeatability'])} | "
            f"{r['longest_near_zero_run'] if r['longest_near_zero_run'] is not None else '-'} | "
            f"{r['near_zero_months'] or '-'} | "
            f"{(r['active_season_start'] or '?')}-{(r['active_season_end'] or '?')} | "
            f"{r['candidate_reason']} |")
    return "\n".join(lines) if len(lines) > 2 else "_None._"


def write_report(rows, asof, counts, themes_by_region, published_check):
    by_label = defaultdict(list)
    for r in rows:
        by_label[r["candidate_label"]].append(r)

    def theme_rows(region):
        ids = themes_by_region.get(region, set())
        return [r for r in rows if r["customer_id"] in ids]

    # Shape says seasonal, but the year-by-year evidence does not back it up.
    weak = [r for r in rows if r["shape"]
            and (r["trough_index"] <= LOW or r["amplitude"] >= SEASONAL_AMPLITUDE)
            and (r["years_used"] < MIN_YEARS_REPEATABILITY
                 or not r["has_2_month_zero_block_repeated"])]

    examples = [r for r in rows if r["customer_id"] in (413, 803, 1056, 709)]
    matched, mismatched = published_check

    lines = [
        "# Fringe / closer-status candidates\n",
        "Generated by `analysis/build_fringe_seasonal_candidates.py`. Analysis only — "
        "nothing on the site reads this folder.\n",
        f"**As of {asof.isoformat()}.** "
        f"{counts['scope']} customers considered ({counts['active']} active, "
        f"{counts['dormant_scope']} recently dormant); **{len(rows)} candidates** "
        "surfaced for review.\n",
        "> **This table makes no final classification.** Every label is a sorting "
        "tool, and `candidate_reason` records which test fired.\n",
        "## Why not a single activity-probability threshold\n",
        "Many customers here have only 2–5 usable years. A single statistic drawn "
        "from three observations moves a long way on one quiet month or one "
        "partly-covered startup year, so it cannot carry a closure decision on its "
        "own. This table instead reports **several independent signals** — the "
        "averaged monthly shape, the per-year shapes, whether a quiet block "
        "*repeats*, theme-region membership, and the current projection status — "
        "side by side. Where one signal is unavailable the others still stand: "
        f"repeatability needs {MIN_YEARS_REPEATABILITY} years and is left blank "
        f"below that ({counts['no_repeatability']} rows), which is why those rows "
        "are labelled from zero-block evidence instead.\n",
        "## How the zero-block logic works\n",
        "A closed season is a **run** of quiet months, not a count of them. Runs "
        "are **cyclic**, so Nov-Dec-Jan-Feb-Mar reads as one five-month winter "
        "block rather than a run of three and a run of two.\n",
        f"- **zero** — index at or below 0 · **near-zero** — at or below "
        f"{NEAR_ZERO:.2f} · **low** — at or below {LOW:.2f}\n"
        f"- **active season** — the longest cyclic run of months at or above "
        f"{ACTIVE:.2f}\n"
        "- A block is only evidence if it **repeats**: the per-year signals ask "
        "whether the same months were quiet in at least two separate years, and "
        "whether that happened in consecutive years.\n"
        "- A month with no production is a **real zero**, not missing data.\n",
        f"**Regression check:** recomputing the 2023–2025 window reproduces "
        f"{matched} of {matched + len(mismatched)} "
        f"published rows in `seasonality_scores.csv`"
        + (" — exact match." if not mismatched else
           f", with {len(mismatched)} mismatch(es): "
           + ", ".join(f"{n} ({w})" for _, n, w in mismatched[:5]) + ".") + "\n",
        "## Candidates by label\n",
        "| Label | Customers | What to do next |", "|---|---:|---|",
    ]
    for label in LABEL_ORDER:
        if by_label[label]:
            lines.append(f"| {label} | {len(by_label[label])} | {NEXT_REVIEW[label]} |")

    for label in ("likely true closer", "possible closer",
                  "semi-closer / call-driven seasonal", "event-driven seasonal"):
        lines += [f"\n## {label.capitalize()} — {len(by_label[label])}\n",
                  table(by_label[label])]

    lines += [
        "\n## Summer snack, golf and fairground candidates\n",
        "From the **`Summer Snack Stops` theme region**, by customer id — not by "
        "name. `CLAUDE.md` forbids name matching here, and the trap is real: "
        "\"Winooski\" contains \"ski\" and \"Blodgett\" contains \"lodge\".\n",
        table(theme_rows("Summer Snack Stops"), limit=30),
        "\n## Ski and winter candidates\n",
        "From the **`Ski Slopes` theme region**, by customer id.\n",
        table(theme_rows("Ski Slopes"), limit=30),
        "\n## Shape suggests seasonality, yearly evidence is weak\n",
        "The averaged shape looks seasonal, but either there are fewer than "
        f"{MIN_YEARS_REPEATABILITY} usable years or no quiet block repeats across "
        "years. These need a human eye most.\n",
        table(weak, limit=25),
        "\n## Worked examples\n",
        table(examples, limit=10),
        "\nChamplain Valley Expo is the clearest case in the data: Jan, Feb, Mar, "
        "Nov and Dec are all exactly zero in every scored year, which the cyclic "
        "logic reads as **one five-month block (Nov–Mar)**, against a September "
        "peak near six times an average month.\n",
        "## Caveats\n",
        "- **No final classifications.** Labels are sorting tools for human review.\n"
        "- **Thin history.** Rows with 2 usable years carry no repeatability; "
        "`years_used` is on every row and should be read before trusting a label.\n"
        "- **Scores use the same years they describe.** A production classifier "
        "would score from history before each prediction.\n"
        "- **Dormant rows may simply be lost customers**, not closers. They are "
        "included because the 2025-01-01 dormancy cutoff cannot tell the "
        "difference; a human can.\n"
        "- **Name clues are supplementary only** and never assign a label.\n",
        "## The rule this changes for the next modelling pass\n",
        "For seasonal and semi-closed customers, **elapsed time since the last "
        "pickup is not positive evidence of oil accumulation.** In many cases — "
        "ski slopes, snack stops, fairgrounds and other call-driven accounts — a "
        "long period without collection is *negative* evidence: if urgent oil were "
        "accumulating, the customer would likely have called or appeared on the "
        "route. Seasonal projections should therefore **not** blindly sum daily "
        "indexed production across every month since the last pickup. Off-season "
        "or long-silent seasonal customers should be suppressed, capped, or held "
        "out of the ordinary urgency ranking until their active season resumes or "
        "a new pickup/call signal appears.\n",
        f"{counts['silence']} candidates already carry the "
        "`off-season / no recent call signal` flag.\n",
        "## Next recommended classification pass\n",
        "1. Confirm closure windows for the `likely true closer` rows — they need a "
        "reopening rule, not a rate.\n"
        "2. Decide whether `semi-closer / call-driven` accounts are scheduled or "
        "on call; that decides whether they get a rate at all.\n"
        "3. Treat `event-driven seasonal` per event, not per day.\n"
        "4. Confirm container type for `on-demand / unlimited review` rows before "
        "any barrel model is applied.\n"
        "5. Re-run once the 2026 season completes, which moves several "
        "`too little history` rows into scope.\n",
    ]
    OUT_REPORT.write_text("\n".join(lines) + "\n")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--as-of", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="review date (default: latest pickup in the CSV)")
    args = parser.parse_args()

    pickups = bt.load_pickups(ids=None)
    active, dormant = load_scope(pickups)
    themes_by_region, themes_by_customer = load_theme_regions()

    context = {
        "published": load_published_scores(),
        "projection": load_projection_status(),
        "capacities": load_capacities(),
        "themes_by_customer": themes_by_customer,
        "dormant": dormant,
    }

    latest = max(p["date"] for ps in pickups.values() for p in ps)
    asof = args.as_of or latest

    scope = sorted(active | dormant)
    everything = [build_row(cid, pickups[cid], asof, context) for cid in scope]

    matched, mismatched = check_published(everything, context["published"])

    rows = []
    for row in everything:
        reasons = is_candidate(row, context)
        if reasons:
            row["selected_because"] = "; ".join(reasons)
            rows.append(row)
    rows.sort(key=sort_key)

    counts = {
        "scope": len(scope),
        "active": len(active),
        "dormant_scope": len(dormant),
        "no_repeatability": sum(1 for r in rows if r["repeatability"] is None),
        "silence": sum(1 for r in rows if r["seasonal_silence_flag"]),
    }

    bt.write_csv(OUT_CSV, FIELDS, [presented(r) for r in rows])
    write_report(rows, asof, counts, themes_by_region, (matched, mismatched))

    by_label = defaultdict(int)
    for r in rows:
        by_label[r["candidate_label"]] += 1

    print(f"As of {asof.isoformat()}\n")
    print(f"  considered                    {len(scope):>4}  "
          f"({len(active)} active + {len(dormant)} recently dormant)")
    print(f"  candidates surfaced           {len(rows):>4}")
    print(f"    unscorable (<{MIN_YEARS} years)        "
          f"{sum(1 for r in rows if not r['shape']):>4}")
    print(f"    dormant                     {sum(1 for r in rows if r['is_dormant']):>4}")
    print(f"    no repeatability            {counts['no_repeatability']:>4}")
    print(f"    off-season silence flag     {counts['silence']:>4}")
    print(f"\n  seasonality_scores.csv reproduction: {matched} matched, "
          f"{len(mismatched)} mismatched")
    for cid, name, why in mismatched[:5]:
        print(f"    MISMATCH {cid} {name}: {why}")

    print("\n  BY LABEL")
    for label in LABEL_ORDER:
        if by_label[label]:
            print(f"    {by_label[label]:>4}  {label}")

    print(f"\nWrote analysis/{OUT_CSV.name} and {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
