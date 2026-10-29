#!/usr/bin/env python3
"""
Cycle blend (CYCLE_BLEND / CYCLE_FACTOR, Sarge, 2026-10-29): the builder's
projection against the straight line it replaced, at every historical pickup.

For pickup k of each customer, build_row runs on pickups[:k] as of pickup k's
date, with only the empty checks, thefts and Oil Left dated before it. Its
row carries both figures -- line_projected_gallons (rate x days, the old
model) and model_projected_gallons (the blend) -- so one call scores both on
identical pickups. Scored as the page shows them: capped at listed capacity.
Ranked rows only, gap 1-180 days. Today's registry and capacities are used for
every date (the same hindsight on both sides).

    python3 analysis/backtest_cycle_blend.py

Writes analysis/cycle_blend.md. Standard library + the builder; deterministic.
Takes ~15 s on 8 processes.
"""

import json
import random
from collections import defaultdict
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import backtest_steady_rate as bt
import build_projection_table as bpt

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "cycle_blend.md"
PERIODS = [("dev 2021-23", (2021, 2022, 2023)), ("confirm 2024-26", (2024, 2025, 2026)),
           ("2018-19", (2018, 2019))]
YEARS = {y for _, ys in PERIODS for y in ys}
ROUTE_STOPS, CALL_IN = 4, 1      # other pickups that day sharing a geographic region
SEED, BOOT = 42, 2000

pickups = bt.load_pickups(ids=None)
raw = {c: list(ps) for c, ps in pickups.items()}
capacities, regions = bpt.load_capacities(), bpt.load_regions()
checks = bpt.load_empty_checks(date.max)
bpt._check_registries()
bpt.apply_history_starts(pickups, checks)
bpt.combine_shared(pickups, checks, capacities, regions, set(pickups))
thefts, _ = bpt.apply_thefts(pickups, date.max)
oil_left = bpt.apply_oil_left(pickups, bpt.load_oil_left(date.max))


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
    gap = (asof - ps[k - 1]["date"]).days
    if (row["table_section"] != bpt.SECTION_RANKED or row["model_projected_gallons"] is None
            or not 0 < gap <= bpt.STALE_DAYS or row["days_accumulating"] <= 0):
        return None
    cap = capacities.get(cid)
    shown = lambda v: min(v, cap) if cap else v
    return {"cid": cid, "date": asof, "year": asof.year, "actual": ps[k]["gallons"],
            "line": shown(row["line_projected_gallons"]), "blend": shown(row["model_projected_gallons"]),
            "u": (row["exposure_days"] / row["usual_gap_days"]) if row["usual_gap_days"] else None}


def route_context():
    """customer -> date -> most other pickups that day in one of its geographic regions."""
    data = json.loads(bpt.OIL_DATA.read_text())
    theme = set(data["theme_regions"])
    geo = defaultdict(set)
    for name, ids in data["region_customers"].items():
        if name not in theme:
            for c in ids:
                geo[c].add(name)
    group = {m: min(g.members) for g in bpt.SHARED_CONTAINERS for m in g.members}
    for m, key in group.items():
        geo[key] |= geo.get(m, set())
    by_day = defaultdict(set)
    for c, ps in raw.items():
        for p in ps:
            by_day[p["date"]].add(group.get(c, c))

    def others(cid, day):
        mine = geo.get(cid)
        if not mine:
            return None
        per = defaultdict(int)
        for c in by_day[day] - {cid}:
            for r in geo.get(c, set()) & mine:
                per[r] += 1
        return max(per.values(), default=0)
    return others


def stats(rows, key):
    act = sum(r["actual"] for r in rows)
    return (100 * sum(abs(r[key] - r["actual"]) for r in rows) / act,
            100 * sum(r[key] - r["actual"] for r in rows) / act)


def interval(rows):
    """95% interval of WAPE(blend) - WAPE(line), resampling whole customers."""
    per = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        s = per[r["cid"]]
        s[0] += abs(r["line"] - r["actual"])
        s[1] += abs(r["blend"] - r["actual"])
        s[2] += r["actual"]
    sums, rng, diffs = list(per.values()), random.Random(SEED), []
    for _ in range(BOOT):
        draw = rng.choices(sums, k=len(sums))
        diffs.append(100 * (sum(s[1] for s in draw) - sum(s[0] for s in draw)) / sum(s[2] for s in draw))
    diffs.sort()
    return diffs[int(0.025 * BOOT)], diffs[int(0.975 * BOOT) - 1]


def table(rows_by_label, ci=True):
    lines = ["| | Pickups | WAPE line | WAPE blend | " + ("95% CI of change | " if ci else "")
             + "Bias line | Bias blend |",
             "|---|---:|---:|---:|" + ("---|" if ci else "") + "---:|---:|"]
    for label, rows in rows_by_label:
        if not rows:
            continue
        (wl, bl), (wb, bb) = stats(rows, "line"), stats(rows, "blend")
        lo, hi = interval(rows) if ci else (None, None)
        mid = f"{lo:+.2f} to {hi:+.2f} | " if ci else ""
        lines.append(f"| {label} | {len(rows):,} | {wl:.1f}% | {wb:.1f}% | {mid}{bl:+.1f}% | {bb:+.1f}% |")
    return "\n".join(lines)


def main():
    jobs = [(c, k) for c in sorted(pickups) for k in range(1, len(pickups[c]))
            if pickups[c][k]["date"].year in YEARS]
    with Pool(8) as pool:
        rows = [r for r in pool.map(score, jobs, chunksize=200) if r]
    others = route_context()
    recent = [r for r in rows if r["year"] >= 2021]

    def timing(r):
        u = r["u"]
        return (None if u is None else "early (< 0.75 x usual gap)" if u < 0.75
                else "on time (0.75-1.25x)" if u <= 1.25 else "late (1.25-2x)" if u <= 2
                else "very late (> 2x)")

    def kind(r):
        o = others(r["cid"], r["date"])
        return (None if o is None else f"route stop (>= {ROUTE_STOPS} others)" if o >= ROUTE_STOPS
                else f"likely call-in (<= {CALL_IN} other)" if o <= CALL_IN else "in between")

    report = [
        "# Cycle blend vs the straight line",
        "",
        "Generated by `backtest_cycle_blend.py`. `build_row` at every historical pickup, as of",
        "that morning; both figures from the same row, capped at listed capacity as shown.",
        f"Blend: w = {bpt.CYCLE_BLEND} x min(1, days / usual gap); projected = "
        f"{bpt.CYCLE_FACTOR} x ((1 - w) x line + w x typical pickup).",
        "",
        "## By period",
        "",
        table([(label, [r for r in rows if r["year"] in ys]) for label, ys in PERIODS]),
        "",
        "## By timing against the customer's own usual gap, 2021-26",
        "",
        table([(t, [r for r in recent if timing(r) == t]) for t in
               ("early (< 0.75 x usual gap)", "on time (0.75-1.25x)", "late (1.25-2x)", "very late (> 2x)")],
              ci=False),
        "",
        "## Route stops vs likely call-ins, 2021-26",
        "",
        "Other pickups the same day in one of the customer's geographic regions. Route stops",
        "behave like the whole sample, so the gain is not only customers phoning in full.",
        "",
        table([(k, [r for r in recent if kind(r) == k]) for k in
               (f"route stop (>= {ROUTE_STOPS} others)", "in between", f"likely call-in (<= {CALL_IN} other)")]),
        "",
        "## Caveat",
        "",
        "Pickups are scored only when they happen, and early pickups are often early because",
        "the barrel filled fast. A snapshot of a customer early in its cycle that is not being",
        "picked up is not tested here: on 2026-10-29 the blend read +41% over the line for rows",
        "under half their usual gap and +5% for rows near it. The rows that change the due list",
        "sit at 0.73x their usual gap or later.",
        "",
    ]
    OUT_REPORT.write_text("\n".join(report))
    print(f"{len(rows):,} pickups scored. Wrote {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
