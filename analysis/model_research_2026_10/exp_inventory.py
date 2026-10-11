#!/usr/bin/env python3
"""
Experiment J: partial pickups and residual inventory (diagnostic first).

Champion residual e_k = actual_k - predicted_k, every scored pickup (core).

The key test separates carryover from the model's own feedback. The
champion's rate at k includes pickup k-1's gallons, so a small pickup k-1
lowers the next prediction and mechanically pushes residual k positive. The
FIXED-RATE design removes that: both pickups k-1 and k are predicted with the
one rate known before k-1 (record k-1's rate):

    u(k-1) = log(actual(k-1) / (R x days(k-1)))
    u(k)   = log(actual(k)   / (R x days(k)))

With no carryover the shared error in R makes corr(u(k-1), u(k)) POSITIVE.
A NEGATIVE correlation means a short pickup tends to be followed by a long
one beyond anything the rate explains: oil left behind and collected next
time (or recording / timing noise that behaves the same way). Consistent
with partial pickups, not proof of them.

Only if that holds in dev AND the other windows is a correction tested:
    allowance   pred + c gallons, c chosen on dev
    carryover   pred + phi x max(0, R x days(k-1) - actual(k-1)), phi on dev
Both predict COLLECTED volume; neither changes the production rate.

    PYTHONPATH=analysis python3 analysis/model_research_2026_10/exp_inventory.py
"""

import math
import random
import statistics
from collections import defaultdict

import harness
import metrics as m
import replay
from replay import bpt

ALLOWANCE_GRID = (0, 2, 5, 10)
PHI_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
ELASTICITY_GRID = (1.0, 0.8, 0.6, 0.4)
WINDOW_SETS = {"dev 2021-23": replay.DEV_YEARS, "confirm 2024-26": replay.CONFIRM_YEARS,
               "least-inspected 2018-19": replay.LEAST_INSPECTED, "all eval years": replay.EVAL_YEARS}


def pearson(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan")


def cluster_corr(pairs, draws=1000, seed=m.SEED):
    """Pearson r of (x, y) pairs with a customer-cluster 95% interval."""
    per = defaultdict(list)
    for cid, x, y in pairs:
        per[cid].append((x, y))
    groups = list(per.values())
    r = pearson([x for _, x, _ in pairs], [y for _, _, y in pairs])
    rng = random.Random(seed)
    boot = []
    for _ in range(draws):
        pts = [p for g in rng.choices(groups, k=len(groups)) for p in g]
        boot.append(pearson([p[0] for p in pts], [p[1] for p in pts]))
    boot.sort()
    return r, boot[int(0.025 * draws)], boot[int(0.975 * draws) - 1], len(groups)


def bias_ci(rows, key="base", draws=1000, seed=m.SEED):
    per = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        per[r["cid"]][0] += r[key] - r["actual"]
        per[r["cid"]][1] += r["actual"]
    sums = list(per.values())
    rng = random.Random(seed)
    b = []
    for _ in range(draws):
        e = a = 0.0
        for s in rng.choices(sums, k=len(sums)):
            e += s[0]
            a += s[1]
        b.append(100 * e / a)
    b.sort()
    tot_e = sum(s[0] for s in sums)
    tot_a = sum(s[1] for s in sums)
    return 100 * tot_e / tot_a, b[int(0.025 * draws)], b[int(0.975 * draws) - 1]


def cluster_slope(pts, draws=500, seed=m.SEED):
    """OLS slope of y on x with a customer-cluster 95% interval."""
    def slope(ps):
        mx = statistics.fmean(p[1] for p in ps)
        my = statistics.fmean(p[2] for p in ps)
        sxx = sum((p[1] - mx) ** 2 for p in ps)
        return sum((p[1] - mx) * (p[2] - my) for p in ps) / sxx if sxx else float("nan")
    per = defaultdict(list)
    for p in pts:
        per[p[0]].append(p)
    groups = list(per.values())
    rng = random.Random(seed)
    boot = sorted(slope([p for g in rng.choices(groups, k=len(groups)) for p in g])
                  for _ in range(draws))
    return slope(pts), boot[int(0.025 * draws)], boot[int(0.975 * draws) - 1]


def load_caps():
    """Today's capacities, shared barrels at the largest member value. HINDSIGHT."""
    caps = bpt.load_capacities()
    for g in bpt.SHARED_CONTAINERS:
        ids = sorted(g.members)
        vals = [caps.get(c) for c in ids if caps.get(c)]
        caps[ids[0]] = max(vals) if vals else None
    return caps


def build(records, pickups):
    by_cid = defaultdict(dict)
    for r in records:
        by_cid[r["cid"]][r["k"]] = r
    rows = []
    for r in records:
        if not r["scored"] or r["year"] not in replay.EVAL_YEARS:
            continue
        prev = by_cid[r["cid"]].get(r["k"] - 1)
        h = pickups[r["cid"]][:r["k"]]
        med = statistics.median(p["gallons"] for p in h[-7:-1]) if len(h) >= 3 else None
        row = {
            "cid": r["cid"], "year": r["year"], "actual": r["actual"], "base": r["pred"],
            "days": r["days"], "stage": r["stage"], "shared": r["shared"],
            "prev_actual": h[-1]["gallons"], "prev_median": med,
            "rate_now": r["rate"],
            "med_gap": statistics.median(g for g in ((b["date"] - a["date"]).days
                                                     for a, b in zip(h[-7:], h[-6:])) if g > 0)
                       if len(h) >= 3 and any((b["date"] - a["date"]).days > 0
                                              for a, b in zip(h[-7:], h[-6:])) else None,
        }
        if prev is not None and prev["rate"] and prev["days"] > 0 and r["days"] > 0:
            R = prev["rate"]
            row["fixed"] = (math.log(prev["actual"] / (R * prev["days"])),
                            math.log(r["actual"] / (R * r["days"])))
            row["prev_pred"] = R * prev["days"]
            # Mechanical (champion) residuals, both from their own records.
            if prev["scored"] and prev["pred"]:
                row["champ_pair"] = (math.log(prev["actual"] / prev["pred"]),
                                     math.log(r["actual"] / r["pred"]))
            # Inventory left after pickup k-1, valued at the rate known before k.
            row["left"] = max(0.0, r["rate"] * prev["days"] - prev["actual"])
        rows.append(row)
    return rows


def gap_label(d):
    for hi, lab in ((14, "1-14 d"), (21, "15-21 d"), (30, "22-30 d"), (45, "31-45 d"),
                    (60, "46-60 d"), (90, "61-90 d")):
        if d <= hi:
            return lab
    return "91-180 d"


def main():
    pickups, _, _, records = replay.load_or_build(mode="core")
    rows = build(records, pickups)
    caps = load_caps()
    L = ["# Experiment J: residual inventory diagnostics (generated by `exp_inventory.py`)", "",
         f"Champion residuals on {len(rows):,} scored pickups (core mode, eval years 2018-19, 2021-26). "
         "Residual = actual − predicted; positive bias = over-projection is NEGATIVE here, so bias "
         "is reported the usual way: (predicted − actual) ÷ actual.", ""]

    # 1. Lag correlation, fixed-rate design vs the champion's own residuals.
    L += ["## 1. Do short pickups predict long ones? (lag-1 correlation of log ratios)", "",
          "| Window | Design | Pairs | Customers | r | 95% interval (customer clusters) |",
          "|---|---|---:|---:|---:|---|"]
    corr = {}
    for wname, years in WINDOW_SETS.items():
        for design, key in (("fixed rate (no feedback)", "fixed"), ("champion residuals", "champ_pair")):
            pairs = [(r["cid"], *r[key]) for r in rows if key in r and r["year"] in years]
            if len(pairs) < 30:
                continue
            c = cluster_corr(pairs)
            corr[(wname, key)] = c
            L.append(f"| {wname} | {design} | {len(pairs):,} | {c[3]} | {c[0]:+.3f} | {c[1]:+.3f} to {c[2]:+.3f} |")
    L += ["", "Fixed rate by gap of the second pickup (all eval years):", "",
          "| Gap of pickup k | Pairs | r | 95% interval |", "|---|---:|---:|---|"]
    for lab, sub in m.by([r for r in rows if "fixed" in r], lambda r: gap_label(r["days"])).items():
        pairs = [(r["cid"], *r["fixed"]) for r in sub]
        if len(pairs) >= 30:
            c = cluster_corr(pairs, draws=500)
            L.append(f"| {lab} | {len(pairs):,} | {c[0]:+.3f} | {c[1]:+.3f} to {c[2]:+.3f} |")

    # 2. Bias by gap length.
    L += ["", "## 2. Champion bias by days on the clock", "",
          "| Days | Pickups | Gallons | WAPE | Bias | 95% interval (bias) |", "|---|---:|---:|---:|---:|---|"]
    for lab, sub in m.by(rows, lambda r: gap_label(r["days"])).items():
        s = m.summary(sub, "base")
        b, lo, hi = bias_ci(sub)
        L.append(f"| {lab} | {s['pickups']:,} | {s['actual_gal']:,} | {s['wape']:.1f}% | {b:+.1f}% | {lo:+.1f} to {hi:+.1f} |")

    # 3. After a small / large previous pickup (relative to the customer's own median).
    L += ["", "## 3. Next residual after a small or large pickup (previous ÷ its own prior median)", "",
          "| Previous pickup | Window | Pickups | Champion bias | 95% interval | Fixed-rate mean u(k) |",
          "|---|---|---:|---:|---|---:|"]

    def size_lab(r):
        q = r["prev_actual"] / r["prev_median"] if r["prev_median"] else None
        if q is None:
            return None
        return "small (< 0.6× median)" if q < 0.6 else "large (> 1.4× median)" if q > 1.4 else "typical"
    for wname, years in WINDOW_SETS.items():
        for lab, sub in m.by([r for r in rows if r["year"] in years and size_lab(r)], size_lab).items():
            b, lo, hi = bias_ci(sub, draws=500)
            us = [r["fixed"][1] for r in sub if "fixed" in r]
            L.append(f"| {lab} | {wname} | {len(sub):,} | {b:+.1f}% | {lo:+.1f} to {hi:+.1f} | "
                     f"{statistics.fmean(us):+.3f} |")

    # 4. Stage, shared, capacity (hindsight).
    L += ["", "## 4. Bias by stage, shared barrel and previous pickup vs TODAY's capacity (hindsight)", "",
          "| Group | Pickups | WAPE | Bias | 95% interval |", "|---|---:|---:|---:|---|"]

    def cap_lab(r):
        c = caps.get(r["cid"])
        if not c:
            return "capacity: none listed"
        q = r["prev_actual"] / c
        return ("capacity: previous ≥ 90% of today's capacity" if q >= 0.9
                else "capacity: previous 50-90%" if q >= 0.5 else "capacity: previous < 50%")
    for fn in (lambda r: f"stage: {r['stage']}", lambda r: "shared barrel" if r["shared"] else "single account",
               cap_lab):
        for lab, sub in m.by(rows, fn).items():
            s = m.summary(sub, "base")
            b, lo, hi = bias_ci(sub, draws=500)
            L.append(f"| {lab} | {s['pickups']:,} | {s['wape']:.1f}% | {b:+.1f}% | {lo:+.1f} to {hi:+.1f} |")

    # 5. Corrections, only if the fixed-rate correlation is negative everywhere.
    neg = all(corr[(w, "fixed")][2] < 0 for w in WINDOW_SETS if (w, "fixed") in corr)
    results = []
    L += ["", "## 5. Corrections for collected volume", ""]
    if not neg:
        L.append("Not tested: the fixed-rate lag correlation is not negative with an interval below "
                 "zero in every window, so there is no stable carryover signature to correct.")
    else:
        grid = {}
        for c in ALLOWANCE_GRID:
            grid[("allowance", c)] = [dict(r, cand=r["base"] + c) for r in rows]
        for phi in PHI_GRID:
            grid[("carryover", phi)] = [dict(r, cand=r["base"] + phi * r.get("left", 0.0)) for r in rows]
        for kind in ("allowance", "carryover"):
            g = {k: v for k, v in grid.items() if k[0] == kind}
            pick = harness.choose(g, replay.DEV_YEARS)
            res, _ = harness.evaluate("J", f"{kind} {pick[1]} (dev-chosen)", "all scored", g[pick],
                                      note=f"grid {[k[1] for k in g]}")
            results += res
            roll, picks = harness.rolling_choice(g)
            res, _ = harness.evaluate("J", f"{kind} rolling", "all scored", roll,
                                      note="picks " + ", ".join(f"{y}:{v[1]}" for y, v in picks.items()))
            results += res
            # Where it matters most: short gaps.
            short = [r for r in g[pick] if r["days"] <= 30]
            res, _ = harness.evaluate("J", f"{kind} {pick[1]} (dev-chosen)", "gaps ≤ 30 d", short)
            results += res
        L += [harness.table(results), ""]
    # 6. Gap elasticity: does collected volume scale with days on the clock?
    L += ["", "## 6. Gap elasticity of collected gallons (diagnostic)", "",
          "u = log(actual ÷ champion prediction) regressed on x = log(days ÷ the customer's as-of "
          "median gap). Under the model's assumption (gallons ∝ days) the slope is 0; a slope of "
          "β − 1 < 0 means collected gallons grow less than proportionally with the gap: early "
          "pickups find more than the rate says, late ones less. That is a statement about WHEN the "
          "truck comes (pickups timed to full barrels, capped containers), not about production.", "",
          "| Window | Pickups | Slope (β − 1) | 95% interval | implied β |", "|---|---:|---:|---|---:|"]
    for wname, years in WINDOW_SETS.items():
        pts = [(r["cid"], math.log(r["days"] / r["med_gap"]), math.log(r["actual"] / r["base"]))
               for r in rows if r["year"] in years and r["med_gap"] and r["base"] > 0]
        b, lo, hi = cluster_slope(pts)
        L.append(f"| {wname} | {len(pts):,} | {b:+.3f} | {lo:+.3f} to {hi:+.3f} | {1 + b:.2f} |")
    egrid = {}
    for beta in ELASTICITY_GRID:
        egrid[beta] = [dict(r, cand=(r["base"] if not r["med_gap"] or beta == 1.0 else
                                     r["rate_now"] * r["med_gap"] * (r["days"] / r["med_gap"]) ** beta))
                       for r in rows]
    pick = harness.choose(egrid, replay.DEV_YEARS)
    res, _ = harness.evaluate("J", f"gap elasticity β={pick} (dev-chosen)", "all scored", egrid[pick],
                              note="COLLECTED-volume model: rate x median gap x (days/median gap)^beta; "
                                   f"grid {list(ELASTICITY_GRID)}; not a production or inventory estimate")
    results += res
    roll, picks = harness.rolling_choice(egrid)
    res, _ = harness.evaluate("J", "gap elasticity rolling", "all scored", roll,
                              note="picks " + ", ".join(f"{y}:{v}" for y, v in picks.items()))
    results += res
    for lab, sub in m.by(egrid[pick], lambda r: gap_label(r["days"])).items():
        res, _ = harness.evaluate("J", f"gap elasticity β={pick} (dev-chosen)", f"days {lab}", sub)
        results += [x for x in res if x["window"] == "all eval years"]
    L += ["", f"Candidate (dev-chosen β = {pick}):", "", harness.table(results), ""]
    harness.write_results(harness.HERE / "results_J.csv", results)
    (harness.HERE / "results_J.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
