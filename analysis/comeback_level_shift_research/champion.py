#!/usr/bin/env python3
"""
The October 15 champion, replayed at every historical pickup with no lookahead.

model_research_2026_10/replay.py replays main at f204e6b. Since then build_row
gained two state changes this module adds on top of the same builder calls:

    comeback hold   a gap > COMEBACK_HOLD_DAYS among the last pickups holds a
                    would-be-ranked row as insufficient data until
                    NEW_MIN_PICKUPS pickups follow it (BPT comeback_hold)
    closer off-season   a ranked off-season closer accrues on open-season days
                    only, at its open-season rate (BPT build_row, H2)

The capacity cap is NOT replayed: capacity history starts 2026-06, so core
scoring is uncapped. `capped_pred` applies today's capacities as a labelled
hindsight sensitivity. HISTORY_STARTS (Mad Moose) is config and arrives
through replay.load_inputs.

The hold is recorded, not applied, in each record: `unheld` is the ladder with
the hold switched off, and `comeback_gaps` keeps what any (threshold, hold
length) needs, so the cutoff sweep (exp_cutoff.py) never re-walks the replay.
The champion itself is `held(record, 365, 3)`.

    PYTHONPATH=analysis python3 analysis/comeback_level_shift_research/champion.py --check
"""

import argparse
import os
import pickle
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "model_research_2026_10"))

import replay                                   # noqa: E402
from replay import bpt, bt                      # noqa: E402

DEFAULT_CACHE = Path(os.environ.get("RESEARCH_CACHE", "/tmp/comeback_level_shift_cache.pkl"))
CACHE_VERSION = 1

HOLD_DAYS = bpt.COMEBACK_HOLD_DAYS             # 365
HOLD_PICKUPS = bpt.NEW_MIN_PICKUPS             # 3
MAX_HOLD_PICKUPS = 3                           # the longest hold the sweep tests


def comeback(h, threshold=HOLD_DAYS, hold_pickups=HOLD_PICKUPS):
    """BPT comeback_hold with the hold length as a parameter (3 = production)."""
    n = len(h)
    for i in range(n - 1, max(n - hold_pickups, 0), -1):
        gap = (h[i]["date"] - h[i - 1]["date"]).days
        if gap > threshold:
            return gap, n - i
    return None


def state(cid, h, asof, checks, seasons, mode="core"):
    """
    build_row's stage, rate, section and exposure for history h, with the
    comeback hold switched OFF. Mirrors BPT build_row step for step otherwise.
    """
    last = h[-1]
    days_since = (asof - last["date"]).days
    later = [d for d in checks if last["date"] < d < asof]
    clock_start = later[-1] if later else last["date"]

    pooled = bpt.rate_pooled(h)
    prev_year = bpt.rate_prev_year_asof(h, asof)
    stage = rate = factor = sa = sb = None
    restart = bpt.dormant_restart(h, asof)
    will_call = bpt.will_call_signal(h, asof)
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
    override = registry or replay.detected_override(
        cid, seasons.closed_months(cid, h, asof.year), last["name"])
    seasonal_override = override and override.status in bpt.SEASONAL_STATUSES

    closer_rate = None
    if seasonal_override and rate is not None and bpt.season_opened(override.season, asof) is not None:
        closer_rate = bpt.rate_open_season(h, override.season)
        if closer_rate is not None:
            stage, rate = bpt.STAGE_CLOSER, closer_rate
            restart = None
    status, section, reason = bpt.route(
        override, rate is not None, days_since, clock_start, h, asof, will_call)

    open_months = None
    if (section == bpt.SECTION_RANKED and seasonal_override
            and bpt.season_opened(override.season, asof) is None):
        open_months = bpt.season_months(override.season)
        open_rate = bpt.rate_open_season(h, override.season)
        if open_rate is not None:
            stage, rate = bpt.STAGE_CLOSER_OFF, open_rate
            restart = None
    exposure = (bpt.open_days(clock_start, asof, open_months) if open_months
                else (asof - clock_start).days)
    return {
        "stage": stage, "rate": rate, "section": section, "status": status, "reason": reason,
        "clock_start": clock_start, "exposure": exposure, "restart": restart,
        "pooled": pooled, "prev_year": prev_year, "factor": factor,
        "season_a": sa, "season_b": sb, "will_call": bool(will_call), "days_since": days_since,
        "season_spec": override.season if seasonal_override else None,
        "override_status": override.status if override else None,
    }


def holdable(st):
    """build_row applies the hold only inside STALE_DAYS and when not will-call."""
    return (st["days_since"] <= bpt.STALE_DAYS and not st["will_call"]
            and st["status"] not in replay.NO_RATE)


def held(rec, threshold=HOLD_DAYS, hold_pickups=HOLD_PICKUPS):
    """True when a (threshold, hold length) comeback hold would hold this record."""
    if not rec["holdable"]:
        return False
    n = rec["k"]
    for i, gap in rec["comeback_gaps"]:
        if gap > threshold and i > max(n - hold_pickups, 0):
            return True
    return False


def scoreable_unheld(rec, mode="core"):
    """Ranked with the hold off, a real interval inside STALE_DAYS (replay.scoreable)."""
    if mode == "core" and rec["registry_status"] in replay.NO_RATE:
        return False
    return (rec["section"] == bpt.SECTION_RANKED and rec["rate"] is not None
            and 0 < rec["gap"] <= bpt.STALE_DAYS and rec["exposure"] > 0)


def replay_all(pickups, checks, shared_ids, mode="core", years=replay.REPLAY_YEARS):
    seasons = replay.SeasonCache()
    out = []
    for cid in sorted(pickups):
        ps = pickups[cid]
        cks = checks.get(cid, ())
        for k in range(1, len(ps)):
            asof = ps[k]["date"]
            if asof.year not in years:
                continue
            h = ps[:k]
            st = state(cid, h, asof, cks, seasons, mode)
            registry = bpt.MODEL_OVERRIDES.get(cid)
            # (index, gap) of the gaps the longest hold could look at
            cg = [(i, (h[i]["date"] - h[i - 1]["date"]).days)
                  for i in range(k - 1, max(k - MAX_HOLD_PICKUPS, 0), -1)]
            st.update({
                "cid": cid, "k": k, "date": asof, "year": asof.year, "name": h[-1]["name"],
                "actual": ps[k]["gallons"], "gap": (asof - h[-1]["date"]).days,
                "shared": cid in shared_ids,
                "registry_status": registry.status if registry else None,
                "pred": st["rate"] * st["exposure"] if st["rate"] is not None else None,
                "comeback_gaps": [x for x in cg if x[1] > 180],
            })
            st["holdable"] = holdable(st)
            st["scored_unheld"] = scoreable_unheld(st, mode)
            st["held"] = held(st)
            st["scored"] = st["scored_unheld"] and not st["held"]
            out.append(st)
    return out


def load_or_build(cache=DEFAULT_CACHE, mode="core", rebuild=False):
    key = (CACHE_VERSION, mode)
    data = {}
    if cache.exists():
        with open(cache, "rb") as f:
            data = pickle.load(f)
        if key in data and not rebuild:
            return data[key]
    pickups, checks, shared_ids = replay.load_inputs()
    records = replay_all(pickups, checks, shared_ids, mode)
    data[key] = (pickups, checks, shared_ids, records)
    with open(cache, "wb") as f:
        pickle.dump(data, f)
    return data[key]


def capped_pred(rec, capacities):
    """Hindsight sensitivity: the shown estimate capped at TODAY's listed capacity."""
    cap = capacities.get(rec["cid"])
    return min(rec["pred"], cap) if cap else rec["pred"]


# ─────────────────────────────────────────────
# Fidelity at the live edge
# ─────────────────────────────────────────────

def fidelity_check():
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
    seasons = replay.SeasonCache()
    agree, diffs, skipped, held_n = 0, [], 0, 0
    for cid in sorted(pickups):
        ps = pickups[cid]
        if not ps:
            continue
        try:
            row = bpt.build_row(cid, ps, asof, capacities.get(cid), regions, {}, checks.get(cid, ()))
        except SystemExit:
            skipped += 1
            continue
        mine = state(cid, ps, asof, checks.get(cid, ()), seasons, "registry")
        rec = {"holdable": holdable(mine), "k": len(ps),
               "comeback_gaps": [(i, (ps[i]["date"] - ps[i - 1]["date"]).days)
                                 for i in range(len(ps) - 1, max(len(ps) - MAX_HOLD_PICKUPS, 0), -1)]}
        if rec["holdable"]:
            assert (bpt.comeback_hold(ps) is not None) == held(rec), cid
        if held(rec):
            held_n += 1
            mine = dict(mine, stage=None, rate=None, section=bpt.SECTION_INSUFFICIENT, exposure=None)
        same = (row["stage"] == mine["stage"] and row["table_section"] == mine["section"]
                and replay._close(row["oil_rate_gpd_projected"], mine["rate"]))
        if same and mine["rate"] is not None and row["model_projected_gallons"] is not None:
            same = abs(row["model_projected_gallons"] - mine["rate"] * mine["exposure"]) < 0.06
        if same:
            agree += 1
        else:
            diffs.append((cid, row["stage"], mine["stage"], row["table_section"], mine["section"],
                          row["oil_rate_gpd_projected"], mine["rate"]))
    return asof, agree, diffs, skipped, held_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        asof, agree, diffs, skipped, held_n = fidelity_check()
        print(f"fidelity at {asof}: {agree} agree, {len(diffs)} differ, {skipped} skipped, "
              f"{held_n} held by the comeback hold")
        for d in diffs[:20]:
            print("  ", d)
    for mode in ("core", "registry"):
        _, _, _, recs = load_or_build(args.cache, mode, args.rebuild)
        print(mode, len(recs), "records,", sum(r["scored"] for r in recs), "scored,",
              sum(r["scored_unheld"] and r["held"] for r in recs), "held by the hold")


if __name__ == "__main__":
    main()
