#!/usr/bin/env python3
"""
Sparsity-weighted cycle blend (Sarge, 2026-11-02): the cycle blend's lean
toward the typical pickup, weighted by how little pickup data a customer has.

    projected = F x ((1 - s x ramp) x line + s x ramp x typical)
    ramp      = min(1, days / usual gap)   (or its square root)
    s         = sparsity, 0 = dense data (the straight line), 1 = one pickup

Production today is s = 0.5 for every ranked row, linear ramp, F = 1.04, and
no estimate at all for will-call and insufficient-data rows.

For pickup k of each customer, build_row runs on pickups[:k] as of pickup k's
date (the backtest_cycle_blend.py pattern). Rows routed ranked, will-call or
insufficient data with a clock of 1-180 days are kept; everything else
(stale, seasonal holdouts, event, on-demand) is out of scope. Line = the
builder's rate x exposure days where it has a rate; else the pooled rate
(since the return, for a comeback); a single pickup has no line. A single
pickup's typical pickup is its gallons and its "usual gap" a prior (site
periodicity, or the median first gap on dev). Scored capped at listed
capacity. Today's registry and capacities on every date (hindsight).

    python3 analysis/backtest_sparsity_blend.py

Writes analysis/sparsity_blend.md. Deterministic; ~20 s on 8 processes.
"""

import csv
import itertools
import math
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta
from multiprocessing import Pool
from pathlib import Path

import backtest_steady_rate as bt
import build_projection_table as bpt

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "sparsity_blend.md"
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
    section = row["table_section"]
    if section not in (bpt.SECTION_RANKED, bpt.SECTION_WILL_CALL, bpt.SECTION_INSUFFICIENT):
        return None
    if not 0 < row["days_accumulating"] <= bpt.STALE_DAYS:
        return None
    seen = ps[:k]
    back = bpt.comeback_hold(seen)
    if back and section == bpt.SECTION_INSUFFICIENT:
        seen = seen[len(seen) - back[1]:]
    days = row["exposure_days"] if row["exposure_days"] is not None else row["days_accumulating"]
    line = row["line_projected_gallons"]
    if line is None and len(seen) >= 2:
        line = bpt.rate_pooled(seen) * days
    year_ago, three = asof - timedelta(days=365), asof - timedelta(days=3 * 365)
    gaps_1y = sum(1 for i in range(1, len(seen)) if seen[i]["date"] >= year_ago)
    gaps_3y = sum(1 for i in range(1, len(seen)) if seen[i]["date"] >= three)
    recent = [(b["date"] - a["date"]).days for a, b in zip(seen, seen[1:]) if b["date"] >= three]
    group = ("1 pickup" if len(seen) == 1
             else "will-call" if section == bpt.SECTION_WILL_CALL
             else "2 pickups / comeback" if section == bpt.SECTION_INSUFFICIENT
             else "ranked, frequent (8+ gaps in a year)" if gaps_1y >= 8
             else "ranked, sparse (< 8 gaps in a year)")
    return {"cid": cid, "year": asof.year, "actual": ps[k]["gallons"],
            "cap": capacities.get(cid), "group": group, "section": section,
            "line": line, "days": days,
            "gap": bpt.usual_gap(seen) if len(seen) >= 2 else None,
            "typical": bpt.average_collection(seen),
            "prod": row["model_projected_gallons"] if section == bpt.SECTION_RANKED else None,
            "g1": gaps_1y, "g3": gaps_3y,
            "median_gap_3y": statistics.median(recent) if recent else None,
            "period": periodicity.get(cid), "first_gap": (asof - seen[-1]["date"]).days}


# ── sparsity definitions: row -> s in [0, 1] ──
def credibility(k, key):
    return lambda r: k / (k + r[key])


def count_ramp(n, key):
    return lambda r: max(0.0, (n - r[key]) / n)


def gap_ramp(lo, hi):
    def s(r):
        g = r["median_gap_3y"]
        return 1.0 if g is None else min(1.0, max(0.0, (g - lo) / (hi - lo)))
    return s


SPARSITY = {
    **{f"cred1y k={k}": credibility(k, "g1") for k in (1, 2, 4, 8)},
    **{f"cred3y k={k}": credibility(k, "g3") for k in (2, 4, 8, 16)},
    **{f"count1y n={n}": count_ramp(n, "g1") for n in (4, 6, 8, 12)},
    "median gap 30->120d": gap_ramp(30, 120),
    "median gap 45->180d": gap_ramp(45, 180),
}


def make(sname, floor, ramp, factor, prior):
    sfun = SPARSITY[sname]

    def project(r):
        s = 1.0 if r["line"] is None else max(floor, sfun(r))
        gap = r["gap"] or (r["period"] if prior == "site periodicity" else PRIOR_GAP)
        x = min(1.0, r["days"] / gap) if gap else 1.0
        w = s * (math.sqrt(x) if ramp == "sqrt" else x)
        v = factor * ((1 - w) * (r["line"] or 0.0) + w * r["typical"])
        return min(v, r["cap"]) if r["cap"] else v
    return project


PRIOR_GAP = None


def stats(rows, f):
    act = sum(r["actual"] for r in rows)
    err = [f(r) - r["actual"] for r in rows]
    return (100 * sum(map(abs, err)) / act, 100 * sum(err) / act,
            100 * sum(max(0, -e) for e in err) / act)


def interval(rows, fa, fb):
    per = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        s = per[r["cid"]]
        s[0] += abs(fa(r) - r["actual"])
        s[1] += abs(fb(r) - r["actual"])
        s[2] += r["actual"]
    sums, rng, diffs = list(per.values()), random.Random(SEED), []
    for _ in range(BOOT):
        draw = rng.choices(sums, k=len(sums))
        diffs.append(100 * (sum(s[1] for s in draw) - sum(s[0] for s in draw)) / sum(s[2] for s in draw))
    diffs.sort()
    return diffs[int(0.025 * BOOT)], diffs[int(0.975 * BOOT) - 1]


def main():
    global PRIOR_GAP
    jobs = [(c, k) for c in sorted(pickups) for k in range(1, len(pickups[c]))
            if pickups[c][k]["date"].year in YEARS]
    with Pool(8) as pool:
        rows = [r for r in pool.map(score, jobs, chunksize=200) if r]
    PRIOR_GAP = statistics.median(r["first_gap"] for r in rows
                                  if r["group"] == "1 pickup" and r["year"] in DEV)

    variants = {"production (s=0.5 ranked, x1.04)": make("cred1y k=1", 0.5, "lin", 1.04, "fleet")}
    for sname, floor, ramp, factor in itertools.product(
            SPARSITY, (0.0, 0.25, 0.5), ("lin", "sqrt"), (1.0, 1.04)):
        variants[f"{sname} | floor {floor} | {ramp} | x{factor}"] = make(sname, floor, ramp, factor, "fleet")
    prod_name = next(iter(variants))
    # Production itself has no estimate for held rows; on ranked rows it IS s=0.5.
    by_period = {lab: [r for r in rows if r["year"] in ys] for lab, ys in PERIODS}
    dev = by_period["dev 2021-23"]
    ranked = lambda rs: [r for r in rs if r["section"] == bpt.SECTION_RANKED]

    scored = sorted(((stats(dev, f)[0], name) for name, f in variants.items()))
    # Shortlist: best on dev overall, among those with dev bias within +-3%.
    unbiased = [(w, n) for w, n in scored if abs(stats(dev, variants[n])[1]) <= 3]
    shortlist = [n for _, n in unbiased[:8]]
    for must in (prod_name, "cred1y k=2 | floor 0.0 | sqrt | x1.0", "count1y n=8 | floor 0.0 | sqrt | x1.0",
                 "cred1y k=2 | floor 0.5 | lin | x1.04", "cred3y k=8 | floor 0.0 | sqrt | x1.04"):
        if must not in shortlist:
            shortlist.append(must)

    rep = ["# Sparsity-weighted cycle blend", "",
           "Generated by `backtest_sparsity_blend.py`. projected = F x ((1 - s x ramp) x line + s x ramp x",
           "typical); s = sparsity (0 dense, 1 one pickup), ramp = min(1, days / usual gap) or its sqrt.",
           "Rows ranked, will-call or insufficient data, clock 1-180 days; capped at capacity as shown.",
           f"A single pickup's prior gap = {PRIOR_GAP:.0f} days (median first gap, dev 2021-23).",
           "",
           "Sparsity definitions: credN k = k / (k + gaps in the last 1 or 3 years) (credibility);",
           "count1y n = (n - gaps in the last year) / n, floored at 0; median gap a->b = 0 at a days,",
           "1 at b days. A floor keeps at least that lean for everyone.", "",
           f"{len(rows):,} pickups scored. Production on held rows = the same formula at s = 0.5",
           "(it gives them nothing today).", "",
           "## Shortlist: best dev WAPE with dev bias within ±3%, plus reference rules", "",
           "| Rule | " + " | ".join(f"{lab} WAPE / bias / low-side" for lab in by_period) + " |",
           "|---|" + "---|" * len(by_period)]
    for n in shortlist:
        rep.append(f"| {n} | " + " | ".join("{:.1f}% / {:+.1f}% / {:.1f}%".format(*stats(rs, variants[n]))
                                          for rs in by_period.values()) + " |")
    rep += ["", "## Top 15 on dev WAPE (any bias)", "", "| Rule | dev WAPE | dev bias |", "|---|---:|---:|"]
    for w, n in scored[:15]:
        rep.append(f"| {n} | {w:.1f}% | {stats(dev, variants[n])[1]:+.1f}% |")

    best = shortlist[0]
    rep += ["", f"## By group: `{best}` vs production", ""]
    groups = ["ranked, frequent (8+ gaps in a year)", "ranked, sparse (< 8 gaps in a year)",
              "will-call", "2 pickups / comeback", "1 pickup"]
    rep += ["| Group | Period | Pickups | Production | Candidate | WAPE change, 95% CI |", "|---|---|---:|---|---|---|"]
    for g in groups:
        for lab, rs in by_period.items():
            sub = [r for r in rs if r["group"] == g]
            if not sub:
                continue
            a = "{:.1f}% / {:+.1f}%".format(*stats(sub, variants[prod_name])[:2])
            b = "{:.1f}% / {:+.1f}%".format(*stats(sub, variants[best])[:2])
            lo, hi = interval(sub, variants[prod_name], variants[best])
            rep.append(f"| {g} | {lab} | {len(sub):,} | {a} | {b} | {lo:+.1f} to {hi:+.1f} |")
    rep += ["", "## By year, ranked rows only (production is defined there)", "",
            "| Year | Pickups | Production | Candidate |", "|---|---:|---|---|"]
    for y in sorted(YEARS):
        sub = ranked([r for r in rows if r["year"] == y])
        rep.append(f"| {y} | {len(sub):,} | " + " | ".join("{:.1f}% / {:+.1f}%".format(*stats(sub, variants[n])[:2])
                                                       for n in (prod_name, best)) + " |")
    # Customers helped, ranked rows, 2021-26
    per = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        if r["year"] >= 2021:
            per[r["cid"]][0] += abs(variants[prod_name](r) - r["actual"])
            per[r["cid"]][1] += abs(variants[best](r) - r["actual"])
    better = sum(b < a for a, b in per.values())
    worse = sum(b > a for a, b in per.values())
    n = better + worse
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(min(better, worse) + 1)) / 2 ** n)
    rep += ["", f"Customers 2021-26 (every row): candidate closer for {better}, further for {worse} "
            f"(sign test p = {p:.2g}).", ""]
    # Weight distribution
    s_best = SPARSITY[best.split(" | ")[0]]
    floor = float(best.split(" | ")[1].split()[1])
    dist = defaultdict(int)
    for r in by_period["confirm 2024-26"]:
        s = 1.0 if r["line"] is None else max(floor, s_best(r))
        dist["s = 0-0.1" if s <= 0.1 else "0.1-0.33" if s <= 0.33 else "0.33-0.67" if s <= 0.67
             else "0.67-0.99" if s < 1 else "1 (no line)"] += 1
    rep += ["Where the candidate's s falls, confirm pickups: "
            + ", ".join(f"{k}: {v:,}" for k, v in sorted(dist.items())), ""]
    OUT_REPORT.write_text("\n".join(rep))
    import os
    if os.environ.get("SPARSITY_ROWS"):
        import pickle
        pickle.dump(rows, open(os.environ["SPARSITY_ROWS"], "wb"))
    print(f"{len(rows):,} pickups scored, {len(variants)} rules. Best: {best}. Wrote {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
