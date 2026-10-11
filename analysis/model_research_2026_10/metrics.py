"""
Scoring shared by every experiment.

A comparison takes rows {cid, year, actual, base, cand, ...} on IDENTICAL
pickups: the candidate falls back to the champion wherever it cannot form a
rate, so coverage never differs inside a head-to-head. Coverage changes are
reported separately by the experiment.

Intervals resample whole CUSTOMERS (a folded shared barrel is one customer),
keeping every pickup of a drawn customer, across all years at once when
years are pooled. Pickups are never treated as independent.
"""

import random
import statistics
from collections import defaultdict

SEED = 42
DRAWS = 2000


def summary(rows, key):
    """Accuracy of predictions rows[i][key] against rows[i]['actual']."""
    n = len(rows)
    if not n:
        return None
    act = sum(r["actual"] for r in rows)
    err = [r[key] - r["actual"] for r in rows]
    ape = [abs(e) / r["actual"] for e, r in zip(err, rows) if r["actual"] > 0]
    return {
        "customers": len({r["cid"] for r in rows}),
        "pickups": n,
        "actual_gal": act,
        "wape": 100 * sum(abs(e) for e in err) / act,
        "median_ape": 100 * statistics.median(ape),
        "mape": 100 * statistics.fmean(ape),
        "mae": statistics.fmean(abs(e) for e in err),
        "bias": 100 * sum(err) / act,
    }


def cluster_interval(rows, a="base", b="cand", draws=DRAWS, seed=SEED):
    """95% interval of WAPE(b) - WAPE(a), resampling customers."""
    per = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        s = per[r["cid"]]
        s[0] += abs(r[a] - r["actual"])
        s[1] += abs(r[b] - r["actual"])
        s[2] += r["actual"]
    sums = list(per.values())
    rng = random.Random(seed)
    diffs = []
    for _ in range(draws):
        ea = eb = act = 0.0
        for s in rng.choices(sums, k=len(sums)):
            ea += s[0]
            eb += s[1]
            act += s[2]
        if act:
            diffs.append(100 * (eb - ea) / act)
    diffs.sort()
    return diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]


def customers_better(rows, a="base", b="cand", tol=1e-9):
    """(better, worse, same) customers by their own absolute error total."""
    per = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        per[r["cid"]][0] += abs(r[a] - r["actual"])
        per[r["cid"]][1] += abs(r[b] - r["actual"])
    better = sum(1 for x, y in per.values() if y < x - tol)
    worse = sum(1 for x, y in per.values() if y > x + tol)
    return better, worse, len(per) - better - worse


def compare(rows, a="base", b="cand", draws=DRAWS):
    """Head-to-head on identical rows."""
    if not rows:
        return None
    sa, sb = summary(rows, a), summary(rows, b)
    lo, hi = cluster_interval(rows, a, b, draws) if len({r["cid"] for r in rows}) > 1 else (None, None)
    better, worse, same = customers_better(rows, a, b)
    return {
        "customers": sa["customers"], "pickups": sa["pickups"], "actual_gal": sa["actual_gal"],
        "base_wape": sa["wape"], "cand_wape": sb["wape"], "d_wape": sb["wape"] - sa["wape"],
        "ci_lo": lo, "ci_hi": hi,
        "base_bias": sa["bias"], "cand_bias": sb["bias"], "d_bias": sb["bias"] - sa["bias"],
        "base_median_ape": sa["median_ape"], "cand_median_ape": sb["median_ape"],
        "base_mape": sa["mape"], "cand_mape": sb["mape"],
        "base_mae": sa["mae"], "cand_mae": sb["mae"],
        "cust_better": better, "cust_worse": worse, "cust_same": same,
    }


def by(rows, keyfn):
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    return dict(sorted(groups.items(), key=lambda kv: str(kv[0])))


def gap_bucket(r):
    g = r["days"]
    return "<30 d" if g < 30 else "30-90 d" if g <= 90 else ">90 d"


def year_signs(rows, years, a="base", b="cand"):
    """Per-year d_wape, and how many years the candidate is better."""
    out = {}
    for y in years:
        sub = [r for r in rows if r["year"] == y]
        if sub:
            out[y] = summary(sub, b)["wape"] - summary(sub, a)["wape"]
    better = sum(1 for v in out.values() if v < 0)
    return out, better


def fmt(x, d=1, signed=False):
    if x is None:
        return "–"
    return f"{x:+.{d}f}" if signed else f"{x:.{d}f}"


def ci(c):
    return "–" if c["ci_lo"] is None else f"{c['ci_lo']:+.2f} to {c['ci_hi']:+.2f}"
