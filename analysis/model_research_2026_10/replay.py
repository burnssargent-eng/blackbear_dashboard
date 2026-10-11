#!/usr/bin/env python3
"""
Champion replay: the production ladder (build_projection_table.py on main,
f204e6b) evaluated at every historical pickup, no lookahead.

For pickup k of customer c the state is built from pickups[:k], the empty
checks dated strictly between pickup k-1 and pickup k, and as-of = date(k):

    prediction(k) = rate(state) x days(clock_start -> date(k))

which is exactly what the builder would have shown on the morning of the
pickup. Every rate comes from the builder's own functions, called in
build_row's order; nothing is re-implemented except the loop.

Two registry modes:

    core       MODEL_OVERRIDES is hindsight (written 2026-09-29), so closers
               come from the detector only. Registry accounts with no daily
               rate (event, on-demand, lump-sum) are dropped from scoring --
               production never projects them; dropping them is the one
               hindsight left, and is reported.
    registry   the builder exactly as it runs today, registry included. A
               sensitivity run, labelled as hindsight.

Shared barrels (SHARED_CONTAINERS) and new-owner starts (HISTORY_STARTS) use
today's config: there is no history of either.

    PYTHONPATH=analysis python3 analysis/model_research_2026_10/replay.py --check

caches the states to the session scratchpad (or --cache PATH) and, with
--check, compares the replay at the latest date with build_row itself.
"""

import argparse
import copy
import os
import pickle
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import build_projection_table as bpt          # noqa: E402
import backtest_steady_rate as bt             # noqa: E402
import seasonal_open                          # noqa: E402

DEFAULT_CACHE = Path(os.environ.get("RESEARCH_CACHE", "/tmp/model_research_2026_10_cache.pkl"))
CACHE_VERSION = 3

# Evaluation folds. 2020 is replayed but left out of pooled results (pandemic).
EVAL_YEARS = (2018, 2019, 2021, 2022, 2023, 2024, 2025, 2026)
REPLAY_YEARS = (2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
DEV_YEARS = (2021, 2022, 2023)
CONFIRM_YEARS = (2024, 2025, 2026)
LEAST_INSPECTED = (2018, 2019)       # never scoring years in any earlier backtest

NO_RATE = set(bpt.NO_RATE_SECTIONS)


# ─────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────

def load_inputs():
    """(pickups, empty_checks, extras) after history starts and shared folding."""
    pickups = bt.load_pickups(ids=None)
    checks = bpt.load_empty_checks(date.max)
    capacities, regions, active = {}, defaultdict(list), set(pickups)
    bpt._check_registries()
    bpt.apply_history_starts(pickups, checks)
    shared = bpt.combine_shared(pickups, checks, capacities, regions, active)
    pickups = {c: ps for c, ps in pickups.items() if ps}
    return pickups, checks, set(shared)


class SeasonCache:
    """Per (customer, year) classification: it only reads pickups before Jan 1."""

    def __init__(self):
        self.factor = {}
        self.closed = {}

    def seasonal_factor(self, cid, ps, year):
        key = (cid, year)
        if key not in self.factor:
            self.factor[key] = bpt.seasonal_factor(cid, ps, date(year, 6, 30))
        return self.factor[key]

    def closed_months(self, cid, ps, year):
        key = (cid, year)
        if key not in self.closed:
            self.closed[key] = seasonal_open.closed_months(ps, year)
        return self.closed[key]


def detected_override(cid, closed, name):
    if not closed:
        return None
    spec = bpt.open_season_spec(closed)
    return bpt.Override(bpt.STATUS_DETECTED_CLOSER, name, spec, "detected")


# ─────────────────────────────────────────────
# The ladder, as build_row runs it
# ─────────────────────────────────────────────

def champion_state(cid, h, asof, checks, seasons, mode="core"):
    """
    The builder's stage, rate and section for history h (pickups before asof).
    Mirrors build_row lines 1070-1133 step for step.
    """
    last = h[-1]
    days_since = (asof - last["date"]).days
    later = [d for d in checks if last["date"] < d < asof]
    clock_start = later[-1] if later else last["date"]

    pooled = bpt.rate_pooled(h)
    prev_year = bpt.rate_prev_year_asof(h, asof)
    stage = rate = factor = sa = sb = None
    restart = bpt.dormant_restart(h, asof)
    if restart:
        stage, rate = bpt.STAGE_NEW, bpt.rate_pooled(h[restart[0]:])
    elif len(h) >= bpt.NEW_MIN_PICKUPS and pooled is not None:
        if prev_year is not None:
            stage, rate = bpt.STAGE_ESTABLISHED, (pooled + prev_year) / 2
            factor = seasons.seasonal_factor(cid, h, asof.year)
            if factor:
                seasonal, sa, sb = bpt.seasonal_rate(h, factor, clock_start, asof)
                if seasonal is not None:
                    stage, rate = bpt.STAGE_SEASONAL, seasonal
        else:
            stage, rate = bpt.STAGE_NEW, pooled

    registry = bpt.MODEL_OVERRIDES.get(cid) if mode == "registry" else None
    override = registry or detected_override(
        cid, seasons.closed_months(cid, h, asof.year), last["name"])

    closer_rate = None
    if (override and override.status in bpt.SEASONAL_STATUSES and rate is not None
            and bpt.season_opened(override.season, asof) is not None):
        closer_rate = bpt.rate_open_season(h, override.season)
        if closer_rate is not None:
            stage, rate = bpt.STAGE_CLOSER, closer_rate
            restart = None
    status, section, reason = bpt.route(
        override, rate is not None, days_since, clock_start, h, asof,
        bpt.will_call_signal(h, asof))
    return {
        "stage": stage, "rate": rate, "section": section, "status": status,
        "reason": reason, "clock_start": clock_start, "restart": restart,
        "pooled": pooled, "prev_year": prev_year, "factor": factor,
        "season_a": sa, "season_b": sb,
        "season_spec": override.season if override and override.status in bpt.SEASONAL_STATUSES else None,
        "override_status": override.status if override else None,
    }


# ─────────────────────────────────────────────
# Replay
# ─────────────────────────────────────────────

def replay(pickups, checks, shared_ids, mode="core", years=REPLAY_YEARS):
    """
    One record per pickup k >= 1 dated in `years`. Records keep the index k
    so a candidate can rebuild h = pickups[cid][:k] without storing it.
    """
    seasons = SeasonCache()
    out = []
    for cid in sorted(pickups):
        ps = pickups[cid]
        cks = checks.get(cid, ())
        for k in range(1, len(ps)):
            asof = ps[k]["date"]
            if asof.year not in years:
                continue
            h = ps[:k]
            gap = (asof - h[-1]["date"]).days
            st = champion_state(cid, h, asof, cks, seasons, mode)
            days = (asof - st["clock_start"]).days
            registry = bpt.MODEL_OVERRIDES.get(cid)
            st.update({
                "cid": cid, "k": k, "date": asof, "year": asof.year,
                "actual": ps[k]["gallons"], "gap": gap, "days": days,
                "n_prior": k, "shared": cid in shared_ids,
                "registry_status": registry.status if registry else None,
                "pred": st["rate"] * days if st["rate"] is not None else None,
            })
            st["scored"] = scoreable(st, mode)
            out.append(st)
    return out


def scoreable(st, mode):
    """Ranked by the champion, a real interval, inside STALE_DAYS (every backtest's rule)."""
    if mode == "core" and st["registry_status"] in NO_RATE:
        return False
    return (st["section"] == bpt.SECTION_RANKED and st["rate"] is not None
            and 0 < st["gap"] <= bpt.STALE_DAYS and st["days"] > 0)


def load_or_build(cache=DEFAULT_CACHE, mode="core", rebuild=False):
    key = (CACHE_VERSION, mode)
    if cache.exists() and not rebuild:
        with open(cache, "rb") as f:
            data = pickle.load(f)
        if key in data:
            return data[key]
    else:
        data = {}
    if cache.exists():
        with open(cache, "rb") as f:
            data = pickle.load(f)
    pickups, checks, shared_ids = load_inputs()
    records = replay(pickups, checks, shared_ids, mode)
    data[key] = (pickups, checks, shared_ids, records)
    with open(cache, "wb") as f:
        pickle.dump(data, f)
    return data[key]


# ─────────────────────────────────────────────
# Fidelity: the replay at the live edge vs build_row itself
# ─────────────────────────────────────────────

def fidelity_check():
    """
    build_row (the production function) and champion_state on the same full
    history at the same as-of date, registry included. Every stage, rate and
    section must agree. Nothing is written.
    """
    pickups = bt.load_pickups(ids=None)
    latest = max(p["date"] for ps in pickups.values() for p in ps)
    asof = latest + timedelta(days=1)
    checks = bpt.load_empty_checks(asof)
    capacities = bpt.load_capacities()
    regions = bpt.load_regions()
    active = set(pickups)
    bpt._check_registries()
    bpt.apply_history_starts(pickups, checks)
    bpt.combine_shared(pickups, checks, capacities, regions, active)
    seasons = SeasonCache()
    agree = differ = skipped = 0
    diffs = []
    for cid in sorted(pickups):
        ps = pickups[cid]
        if not ps:
            continue
        try:
            row = bpt.build_row(cid, ps, asof, capacities.get(cid), regions, {}, checks.get(cid, ()))
        except SystemExit:
            skipped += 1
            continue
        mine = champion_state(cid, ps, asof, checks.get(cid, ()), seasons, "registry")
        same = (row["stage"] == mine["stage"] and row["table_section"] == mine["section"]
                and _close(row["oil_rate_gpd_projected"], mine["rate"]))
        if same:
            agree += 1
        else:
            differ += 1
            diffs.append((cid, row["stage"], mine["stage"], row["table_section"],
                          mine["section"], row["oil_rate_gpd_projected"], mine["rate"]))
    return asof, agree, differ, skipped, diffs


def _close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - round(b, 3)) < 0.0015 or abs(a - b) < 1e-9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        asof, agree, differ, skipped, diffs = fidelity_check()
        print(f"fidelity at {asof}: {agree} agree, {differ} differ, {skipped} skipped (registry name)")
        for d in diffs[:20]:
            print("  ", d)
    for mode in ("core", "registry"):
        _, _, _, recs = load_or_build(args.cache, mode, args.rebuild)
        print(mode, len(recs), "records,", sum(r["scored"] for r in recs), "scored")


if __name__ == "__main__":
    main()
