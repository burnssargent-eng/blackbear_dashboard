#!/usr/bin/env python3
"""
Estimates for the rows the builder holds out with no projection: detected
will-calls and insufficient data (Sarge, 2026-11-01: "many will-calls are not
will-calls"; no estimate under-projects the total).

For pickup k of each customer, build_row runs on pickups[:k] as of pickup k's
date (the backtest_cycle_blend.py pattern). Rows it routes to will-call or
insufficient data are kept and every candidate estimate is scored against
pickup k's gallons, capped at listed capacity as the page would show it.
Today's registry and capacities are used for every date (hindsight, same for
every candidate).

Candidates, for rows with at least one measured gap (first pickup = start only):
    ladder      the builder's own stage rate + cycle blend, had it not held the row
    line3       rate over the last 3 years (pooled gaps) x days
    blend3/2    cycle blend (model B) on the 3- / 2-year rate and the typical pickup
    avg         the typical pickup (mean of the last 6), flat
    half3       0.5 x the 3-year line + 0.5 x typical pickup, from day 0
    fullB       w = min(1, days / mean gap over 3 years); (1 - w) x line3 + w x typical
    fullB_max   the same, never below line3 (keeps growing past the gap)
    halfB_mean  w = 0.5 x min(1, days / mean gap): model B on the mean gap
For 1-pickup rows (no gap yet): site periodicity, first pickup, a fleet rate
per gallon of capacity (chosen on 2021-23), half first pickup + half either
rate, nothing.

    python3 analysis/backtest_sparse_estimates.py

Writes analysis/sparse_estimates.md. Deterministic; ~20 s on 8 processes.
"""

import csv
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta
from multiprocessing import Pool
from pathlib import Path

import backtest_steady_rate as bt
import build_projection_table as bpt

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "sparse_estimates.md"
PERIODS = [("dev 2021-23", (2021, 2022, 2023)), ("confirm 2024-26", (2024, 2025, 2026)),
           ("2018-19", (2018, 2019))]
YEARS = {y for _, ys in PERIODS for y in ys}
DEV = PERIODS[0][1]
SEED, BOOT = 42, 2000

pickups = bt.load_pickups(ids=None)
capacities, regions = bpt.load_capacities(), bpt.load_regions()
checks = bpt.load_empty_checks(date.max)
bpt._check_registries()
bpt.apply_history_starts(pickups, checks)
bpt.combine_shared(pickups, checks, capacities, regions, set(pickups))
thefts, _ = bpt.apply_thefts(pickups, date.max)
oil_left = bpt.apply_oil_left(pickups, bpt.load_oil_left(date.max))
periodicity = {}
with open(bpt.CAPACITY_CACHE, newline="") as f:
    for r in csv.DictReader(f):
        try:
            periodicity[int(r["customer_id"])] = float(r["periodicity_days"]) or None
        except (TypeError, ValueError):
            pass


def years_rate(ps, asof, years):
    """Pooled rate over the pickups in the last `years` years (the earliest
    in the window marks the start); at least the last gap."""
    cutoff = asof - timedelta(days=round(years * 365))
    lo = next((i for i, p in enumerate(ps) if p["date"] >= cutoff), len(ps) - 1)
    lo = min(lo, len(ps) - 2)
    return bpt.span_rate(ps, lo, len(ps) - 1)


def mean_gap(ps, asof, years):
    """Mean days between the pickups in the last `years` years (at least the last gap)."""
    cutoff = asof - timedelta(days=round(years * 365))
    lo = next((i for i, p in enumerate(ps) if p["date"] >= cutoff), len(ps) - 1)
    lo = min(lo, len(ps) - 2)
    return (ps[-1]["date"] - ps[lo]["date"]).days / (len(ps) - 1 - lo)


def median_gap(ps, asof, years):
    """Median days between the pickups in the last `years` years (at least the last gap)."""
    cutoff = asof - timedelta(days=round(years * 365))
    lo = next((i for i, p in enumerate(ps) if p["date"] >= cutoff), len(ps) - 1)
    lo = min(lo, len(ps) - 2)
    gaps = [(b["date"] - a["date"]).days for a, b in zip(ps[lo:], ps[lo + 1:])]
    return statistics.median(gaps)


def lean(line, days, gap, typical, weight=1.0):
    """Model B with a full lean: w = weight x min(1, days / gap)."""
    w = weight * min(1.0, days / gap) if gap else 0.0
    return (1 - w) * line + w * typical


def group(row, ps):
    if row["table_section"] == bpt.SECTION_WILL_CALL:
        return "will-call"
    reason = row["routing_reason"]
    if reason.startswith("back after"):
        return "comeback / held return"
    return f"{len(ps)} pickup{'s' if len(ps) > 1 else ''}"


def score(job):
    cid, k = job
    ps = pickups[cid]
    asof = ps[k]["date"]
    try:
        row = bpt.build_row(cid, ps[:k], asof, capacities.get(cid), regions, {},
                            [d for d in checks.get(cid, ()) if d < asof],
                            [t for t in thefts.get(cid, ()) if t[0] < asof],
                            [o for o in oil_left.get(cid, ()) if o[0] < asof])
    except SystemExit:
        return None
    if row["table_section"] not in (bpt.SECTION_WILL_CALL, bpt.SECTION_INSUFFICIENT):
        return None
    days = row["days_accumulating"]
    if not 0 < days <= bpt.STALE_DAYS:
        return None
    seen = ps[:k]
    # A comeback measures from the return, like the dormant restart.
    back = bpt.comeback_hold(seen)
    if back:
        seen = seen[len(seen) - back[1]:]
    cap = capacities.get(cid)
    shown = lambda v: None if v is None else (min(v, cap) if cap else v)
    out = {"cid": cid, "year": asof.year, "actual": ps[k]["gallons"], "days": days,
           "cap": cap, "group": group(row, ps[:k]), "n": len(seen)}
    if len(seen) == 1:
        per = periodicity.get(cid)
        out["period"] = shown(0.75 * cap / per * days) if cap and per else None
        out["period_full"] = shown(cap / per * days) if cap and per else None
        out["first"] = shown(seen[0]["gallons"])
        return out
    typical = bpt.average_collection(seen)
    gap = bpt.usual_gap(seen)
    r3, r2 = years_rate(seen, asof, 3), years_rate(seen, asof, 2)
    out["ladder"] = shown(row["model_projected_gallons"])
    out["line3"] = shown(r3 * days) if r3 is not None else None
    out["blend3"] = shown(bpt.cycle_projection(r3 * days, days, gap, typical)) if r3 is not None else None
    out["blend2"] = shown(bpt.cycle_projection(r2 * days, days, gap, typical)) if r2 is not None else None
    out["avg"] = shown(typical)
    out["half3"] = shown(0.5 * r3 * days + 0.5 * typical) if r3 is not None else None
    out["half2"] = shown(0.5 * r2 * days + 0.5 * typical) if r2 is not None else None
    mg = mean_gap(seen, asof, 3)
    if r3 is not None:
        out["fullB"] = shown(lean(r3 * days, days, mg, typical))
        out["fullB_max"] = shown(max(r3 * days, lean(r3 * days, days, mg, typical)))
        out["halfB_mean"] = shown(lean(r3 * days, days, mg, typical, 0.5))
    out["u"] = days / mg if mg else None
    out.update(r3=r3, typical=typical, mean_gap=mg, median_gap=median_gap(seen, asof, 3))
    return out


def stats(rows, key):
    act = sum(r["actual"] for r in rows)
    err = [r[key] - r["actual"] for r in rows]
    return {"wape": 100 * sum(map(abs, err)) / act, "bias": 100 * sum(err) / act,
            "low": 100 * sum(max(0, -e) for e in err) / act,
            "half": 100 * sum(r[key] < 0.5 * r["actual"] for r in rows) / len(rows)}


def interval(rows, a, b):
    """95% interval of WAPE(b) - WAPE(a), resampling whole customers."""
    per = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        s = per[r["cid"]]
        s[0] += abs(r[a] - r["actual"])
        s[1] += abs(r[b] - r["actual"])
        s[2] += r["actual"]
    sums, rng, diffs = list(per.values()), random.Random(SEED), []
    for _ in range(BOOT):
        draw = rng.choices(sums, k=len(sums))
        diffs.append(100 * (sum(s[1] for s in draw) - sum(s[0] for s in draw)) / sum(s[2] for s in draw))
    diffs.sort()
    return diffs[int(0.025 * BOOT)], diffs[int(0.975 * BOOT) - 1]


def table(rows, keys, base=None):
    """One line per candidate, all scored on the rows where every key exists."""
    rows = [r for r in rows if all(r.get(k) is not None for k in keys)]
    if not rows:
        return "(no pickups)"
    cust = len({r["cid"] for r in rows})
    head = (f"{len(rows):,} pickups, {cust} customers, {sum(r['actual'] for r in rows):,.0f} gal collected.\n\n"
            "| Estimate | WAPE | Bias | Low-side gal missed | Under half of actual |"
            + (f" WAPE vs {base}, 95% CI |" if base else "") + "\n|---|---:|---:|---:|---:|"
            + ("---|" if base else ""))
    lines = [head]
    for k in keys:
        s = stats(rows, k)
        ci = ""
        if base:
            ci = " — |" if k == base else " {:+.1f} to {:+.1f} |".format(*interval(rows, base, k))
        lines.append(f"| {k} | {s['wape']:.1f}% | {s['bias']:+.1f}% | {s['low']:.1f}% | {s['half']:.0f}% |{ci}")
    return "\n".join(lines)


def main():
    jobs = [(c, k) for c in sorted(pickups) for k in range(1, len(pickups[c]))
            if pickups[c][k]["date"].year in YEARS]
    with Pool(8) as pool:
        rows = [r for r in pool.map(score, jobs, chunksize=200) if r]
    multi = [r for r in rows if r["n"] >= 2]
    single = [r for r in rows if r["n"] == 1]

    # Fleet rate per gallon of capacity for a customer's first gap, chosen on dev.
    dev1 = [r for r in single if r["year"] in DEV and r["cap"]]
    c = statistics.median(r["actual"] / r["days"] / r["cap"] for r in dev1)
    for r in single:
        r["fleet"] = min(c * r["cap"] * r["days"], r["cap"]) if r["cap"] else None
        r["nothing"] = 0.0
        r["first+fleet"] = 0.5 * r["first"] + 0.5 * r["fleet"] if r["fleet"] is not None else None
        r["first+period"] = (0.5 * r["first"] + 0.5 * r["period"]
                             if r.get("period") is not None else None)

    keys = ["ladder", "line3", "blend3", "avg", "half3", "fullB", "fullB_max", "halfB_mean"]
    one = ["nothing", "first", "fleet", "period", "period_full", "first+fleet", "first+period"]
    rep = ["# Estimates for will-call and insufficient-data rows", "",
           "Generated by `backtest_sparse_estimates.py`. `build_row` at every pickup 2018-26 as of",
           "that morning; kept where it routed the row to will-call or insufficient data and the",
           f"clock had run 1-{bpt.STALE_DAYS} days. Scored capped at listed capacity. Today's",
           "registry and capacities on every date. 'Low-side gal missed' = gallons collected above",
           "the estimate, as % of all collected (the costly direction).", ""]
    for gname in ("will-call", "2 pickups", "comeback / held return"):
        g = [r for r in multi if r["group"] == gname]
        ks = keys if gname == "will-call" else keys[1:]   # the ladder has no rate for the rest
        rep += [f"## {gname}", ""]
        for label, ys in PERIODS:
            rep += [f"### {label}", "", table([r for r in g if r["year"] in ys], ks, "fullB"), ""]
    by_year = ("fullB", "fullB_max", "halfB_mean", "half3", "line3")
    rep += ["## Every row with a gap, by year", "",
            "| Year | Pickups | " + " | ".join(f"{k} WAPE | bias" for k in by_year) + " |",
            "|---|---:|" + "---:|---:|" * len(by_year)]
    for y in sorted(YEARS):
        g = [r for r in multi if r["year"] == y and all(r.get(k) is not None for k in keys[1:])]
        if g:
            cells = " | ".join(f"{stats(g, k)['wape']:.0f}% | {stats(g, k)['bias']:+.0f}%"
                               for k in by_year)
            rep.append(f"| {y} | {len(g)} | {cells} |")
    rep += ["", f"## 1 pickup (no gap): predicting the 2nd pickup", "",
            f"Fleet rate = {c:.5f} gal/day per gallon of capacity (median first gap, dev 2021-23).",
            "Site periodicity is today's value on the page (hindsight; Jim's sign-up guess,",
            "rarely updated). period = 0.75 x capacity / periodicity per day; period_full = capacity /",
            "periodicity.", ""]
    for label, ys in PERIODS:
        rep += [f"### {label}", "", table([r for r in single if r["year"] in ys], one, "first"), ""]
    OUT_REPORT.write_text("\n".join(rep))
    import os
    if os.environ.get("SPARSE_ROWS"):           # per-pickup rows for ad-hoc study
        import pickle
        pickle.dump(rows, open(os.environ["SPARSE_ROWS"], "wb"))
    print(f"{len(rows):,} held-out pickups scored ({len(multi):,} with a gap). Wrote {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
