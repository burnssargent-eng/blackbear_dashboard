"""
Fold reporting shared by the experiment scripts.

Every candidate is reported the same way: pooled dev (2021-23), pooled
confirm (2024-26), pooled over every evaluation year (2018-19, 2021-26), and
each year on its own, all against the champion on identical pickups, with
customer-cluster intervals. Rows carry `base` (champion gallons) and `cand`.
"""

import csv
from pathlib import Path

import metrics as m
import replay

HERE = Path(__file__).resolve().parent

RESULT_FIELDS = [
    "experiment", "candidate", "scope", "window", "customers", "pickups", "actual_gal",
    "base_wape", "cand_wape", "d_wape", "ci_lo", "ci_hi", "base_bias", "cand_bias", "d_bias",
    "base_median_ape", "cand_median_ape", "base_mae", "cand_mae",
    "cust_better", "cust_worse", "fired_pickups", "years_better", "years_scored", "note",
]

WINDOWS = {
    "dev 2021-23": replay.DEV_YEARS,
    "confirm 2024-26": replay.CONFIRM_YEARS,
    "least-inspected 2018-19": replay.LEAST_INSPECTED,
    "all eval years": replay.EVAL_YEARS,
}


def fired(rows, tol=1e-6):
    return [r for r in rows if abs(r["cand"] - r["base"]) > tol]


def evaluate(experiment, candidate, scope, rows, note=""):
    """Result rows (one per window) for one candidate on one scope."""
    out = []
    yrs, better = m.year_signs(rows, replay.EVAL_YEARS)
    for wname, years in WINDOWS.items():
        sub = [r for r in rows if r["year"] in years]
        if not sub:
            continue
        c = m.compare(sub)
        out.append({
            "experiment": experiment, "candidate": candidate, "scope": scope, "window": wname,
            **{k: c[k] for k in c if k in RESULT_FIELDS},
            "fired_pickups": len(fired(sub)),
            "years_better": better if wname == "all eval years" else "",
            "years_scored": len(yrs) if wname == "all eval years" else "",
            "note": note,
        })
    return out, yrs


def write_results(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})


def table(results, windows=("dev 2021-23", "confirm 2024-26", "least-inspected 2018-19", "all eval years")):
    """Markdown comparison table, one line per candidate x window."""
    lines = ["| Candidate | Scope | Window | Pickups (cust.) | Champion WAPE | Candidate WAPE | Δ | 95% interval | Bias: champ → cand | Fired | Cust. better/worse | Years better |",
             "|---|---|---|---:|---:|---:|---:|---|---|---:|---|---|"]
    for r in results:
        if r["window"] not in windows:
            continue
        ci = "–" if r["ci_lo"] is None else f"{r['ci_lo']:+.2f} to {r['ci_hi']:+.2f}"
        yb = f"{r['years_better']}/{r['years_scored']}" if r["years_scored"] != "" else ""
        lines.append(
            f"| {r['candidate']} | {r['scope']} | {r['window']} | {r['pickups']:,} ({r['customers']}) "
            f"| {r['base_wape']:.1f}% | {r['cand_wape']:.1f}% | **{r['d_wape']:+.2f}** | {ci} "
            f"| {r['base_bias']:+.1f}% → {r['cand_bias']:+.1f}% | {r['fired_pickups']:,} "
            f"| {r['cust_better']}/{r['cust_worse']} | {yb} |")
    return "\n".join(lines)


def year_table(named_years):
    """Per-year Δ WAPE for several candidates: {name: {year: d}}."""
    years = replay.EVAL_YEARS
    lines = ["| Candidate | " + " | ".join(str(y) for y in years) + " |",
             "|---|" + "---:|" * len(years)]
    for name, yrs in named_years.items():
        lines.append(f"| {name} | " + " | ".join(
            f"{yrs[y]:+.2f}" if y in yrs else "–" for y in years) + " |")
    return "\n".join(lines)


def split_table(rows, keyfn, title):
    lines = [f"| {title} | Pickups | Champion WAPE | Candidate WAPE | Δ | 95% interval | Bias: champ → cand |",
             "|---|---:|---:|---:|---:|---|---|"]
    for k, sub in m.by(rows, keyfn).items():
        c = m.compare(sub, draws=1000)
        lines.append(f"| {k} | {c['pickups']:,} | {c['base_wape']:.1f}% | {c['cand_wape']:.1f}% "
                     f"| {c['d_wape']:+.2f} | {m.ci(c)} | {c['base_bias']:+.1f}% → {c['cand_bias']:+.1f}% |")
    return "\n".join(lines)


def choose(grid_rows, years):
    """The grid value with the lowest pooled WAPE on `years`: {value: rows}."""
    best = None
    for v, rows in grid_rows.items():
        sub = [r for r in rows if r["year"] in years]
        if not sub:
            continue
        w = m.summary(sub, "cand")["wape"]
        if best is None or w < best[1]:
            best = (v, w)
    return best[0] if best else None


def rolling_choice(grid_rows, base_rows_key="cand"):
    """
    Expanding-window selection: for each eval year Y, the grid value chosen on
    eval years before Y (2018 has none, so it takes the first grid value);
    returns (rows assembled from each year's choice, {year: choice}).
    Odd/even: `parity` restricts the tuning years.
    """
    out, picks = [], {}
    values = list(grid_rows)
    for y in replay.EVAL_YEARS:
        prior = [t for t in replay.EVAL_YEARS if t < y]
        v = choose(grid_rows, prior) if prior else None
        v = values[0] if v is None else v        # no tuning rows yet: the first grid value
        picks[y] = v
        out += [r for r in grid_rows[v] if r["year"] == y]
    return out, picks


def odd_even_choice(grid_rows):
    """For each EVEN eval year, choose on ODD eval years before it."""
    out, picks = [], {}
    values = list(grid_rows)
    for y in replay.EVAL_YEARS:
        if y % 2:
            continue
        prior = [t for t in replay.EVAL_YEARS if t < y and t % 2]
        v = choose(grid_rows, prior) if prior else None
        v = values[0] if v is None else v        # no tuning rows yet: the first grid value
        picks[y] = v
        out += [r for r in grid_rows[v] if r["year"] == y]
    return out, picks


def customer_impacts(experiment, candidate, window, rows):
    """Per-customer champion vs candidate on `rows` (each needs cid, name, actual, base, cand)."""
    out = []
    for cid, rs in m.by(rows, lambda r: r["cid"]).items():
        act = sum(r["actual"] for r in rs)
        changes = [(r["cand"] - r["base"]) / r["base"] for r in rs if r["base"] > 0]
        out.append({
            "experiment": experiment, "candidate": candidate, "window": window,
            "customer_id": cid, "customer": rs[-1]["name"], "pickups": len(rs), "actual_gal": act,
            "champion_wape": round(100 * sum(abs(r["base"] - r["actual"]) for r in rs) / act, 1),
            "candidate_wape": round(100 * sum(abs(r["cand"] - r["actual"]) for r in rs) / act, 1),
            "mean_pred_change_pct": round(100 * sum(changes) / len(changes), 1) if changes else "",
        })
    return out


def write_impacts(path, impacts):
    if not impacts:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(impacts[0]))
        w.writeheader()
        w.writerows(impacts)
