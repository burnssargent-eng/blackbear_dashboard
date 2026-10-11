#!/usr/bin/env python3
"""
Experiments A (reliability-weighted 50/50), B (fixed horizons) and K (an
exposure-aware decaying rate) on the champion's ESTABLISHED rows, where
rate = 1/2 P + 1/2 Y with P = pooled last 6 gaps and Y = rate_prev_year_asof.
B and K are also run on the NEW stage (rate = P).

A   w x P + (1 - w) x Y, by frequency bucket (median of the last 6 gaps):
    A1  span weight: w = days(P window) / (days(P) + days(Y))
    A2  inverse error variance: w = (1/sP^2) / (1/sP^2 + 1/sY^2)
    A3  covariance-aware: w = (sY^2 - sPY) / (sP^2 + sY^2 - 2 sPY), clipped [0, 1]
    A4  A3 shrunk to 1/2: w = lam x w3 + (1 - lam) x 1/2, lam in {0.25, 0.5, 0.75}
    The variances are of each component's own log forecast error
    log(actual / (component x days)). Two fits: on dev 2021-23, frozen
    (dev rows are then in-sample, flagged), and rolling (for year Y, fitted on
    replay years before Y, 2020 left out -- 2017 is the warm-up).
B   recent rate = the fewest most recent gaps spanning >= H days (>= 2 gaps),
    H in {60, 90, 120, 180}: alone (B-solo) and as P inside the 50/50 (B-half).
K   rate = sum w_i g_i / sum w_i d_i over every gap of the last 3 years,
    w_i = exp(-age_i / tau), age = days from the gap's end to the last pickup.
    tau in {45, 90, 180, 365}. One parameter, non-negative by construction.

A candidate with no rate falls back to the champion on that pickup; the
counts are in the note column.

    python3 exp_established.py      (from this folder)
"""

import math
import statistics
from datetime import timedelta

import harness
import metrics as m
import replay
from replay import bpt

A4_LAMBDAS = (0.25, 0.5, 0.75)
HORIZONS = (60, 90, 120, 180)
TAUS = (45, 90, 180, 365)
K_LOOKBACK = 3 * bpt.YEAR_DAYS
LEARN_YEARS = tuple(y for y in replay.REPLAY_YEARS if y != 2020)


def freq_bucket(h):
    gaps = [(b["date"] - a["date"]).days for a, b in zip(h[-7:], h[-6:])]
    g = statistics.median(gaps)
    return "weekly-ish (<21 d)" if g < 21 else "21-45 d" if g <= 45 else "slow (>45 d)"


def horizon_rate(h, H):
    n = len(h)
    for j in range(2, n):
        span = (h[-1]["date"] - h[n - 1 - j]["date"]).days
        if span >= H:
            return bpt.bsm.rate_span(h, n - 1 - j, n - 1)
    return None


def decay_rate(h, tau):
    last = h[-1]["date"]
    num = den = 0.0
    for a, b in zip(h, h[1:]):
        d = (b["date"] - a["date"]).days
        if d <= 0 or (last - b["date"]).days > K_LOOKBACK:
            continue
        w = math.exp(-(last - b["date"]).days / tau)
        num += w * b["gallons"]
        den += w * d
    return num / den if den else None


def build(records, pickups):
    rows = []
    for r in records:
        if not r["scored"] or r["stage"] not in (bpt.STAGE_ESTABLISHED, bpt.STAGE_NEW):
            continue
        if r["year"] not in replay.REPLAY_YEARS:
            continue
        h = pickups[r["cid"]][:r["k"]]
        n = len(h)
        P, Y, days = r["pooled"], r["prev_year"], r["days"]
        row = {"cid": r["cid"], "year": r["year"], "actual": r["actual"], "days": days, "name": h[-1]["name"],
               "stage": r["stage"], "base": r["pred"], "restart": bool(r["restart"]), "bucket": freq_bucket(h),
               "gap_bucket": m.gap_bucket(r), "P": P, "Y": Y}
        if r["stage"] == bpt.STAGE_ESTABLISHED:
            i = next(i for i in range(n - 1, -1, -1)
                     if h[i]["date"] <= r["date"] - timedelta(days=bpt.YEAR_DAYS))
            row["span_P"] = (h[-1]["date"] - h[max(0, n - 7)]["date"]).days
            row["span_Y"] = (h[-1]["date"] - h[i]["date"]).days
            row["eP"] = math.log(r["actual"] / (P * days)) if P > 0 else None
            row["eY"] = math.log(r["actual"] / (Y * days)) if Y > 0 else None
        row["B"] = {H: horizon_rate(h, H) for H in HORIZONS}
        row["K"] = {t: decay_rate(h, t) for t in TAUS}
        rows.append(row)
    return rows


# ─────────────────────────────────────────────
# A: weights
# ─────────────────────────────────────────────

def learn(rows, years):
    """{bucket: (w2, w3, n)} from the components' log errors on `years`."""
    out = {}
    for b, sub in m.by([r for r in rows if r["year"] in years and r["stage"] == bpt.STAGE_ESTABLISHED
                        and r["eP"] is not None and r["eY"] is not None], lambda r: r["bucket"]).items():
        if len(sub) < 30:
            continue
        eP = [r["eP"] for r in sub]
        eY = [r["eY"] for r in sub]
        vP, vY = statistics.pvariance(eP), statistics.pvariance(eY)
        mP, mY = statistics.fmean(eP), statistics.fmean(eY)
        cPY = statistics.fmean((a - mP) * (b - mY) for a, b in zip(eP, eY))
        w2 = (1 / vP) / (1 / vP + 1 / vY)
        den = vP + vY - 2 * cPY
        w3 = min(1.0, max(0.0, (vY - cPY) / den)) if den > 0 else 0.5
        out[b] = (w2, w3, len(sub))
    return out


def blend(r, w):
    return (w * r["P"] + (1 - w) * r["Y"]) * r["days"]


def a_candidates(est, weights_for_year):
    """{name: rows} using weights_for_year(year) -> {bucket: (w2, w3, n)}."""
    out = {"A1 span weight": [], "A2 inverse variance": [], "A3 covariance-aware": []}
    out.update({f"A4 shrunk, λ={l}": [] for l in A4_LAMBDAS})
    for r in est:
        wb = weights_for_year(r["year"]).get(r["bucket"])
        w1 = r["span_P"] / (r["span_P"] + r["span_Y"]) if r["span_P"] + r["span_Y"] else 0.5
        out["A1 span weight"].append(dict(r, cand=blend(r, w1), w=w1))
        w2, w3 = (wb[0], wb[1]) if wb else (0.5, 0.5)
        out["A2 inverse variance"].append(dict(r, cand=blend(r, w2), w=w2))
        out["A3 covariance-aware"].append(dict(r, cand=blend(r, w3), w=w3))
        for l in A4_LAMBDAS:
            w4 = l * w3 + (1 - l) * 0.5
            out[f"A4 shrunk, λ={l}"].append(dict(r, cand=blend(r, w4), w=w4))
    return out


# ─────────────────────────────────────────────

def evaluate_grid(exp, label, scope, grid, results, yearly, notes):
    """Each grid value, the dev choice, and the rolling and odd/even choices."""
    for v, rows in grid.items():
        res, yrs = harness.evaluate(exp, f"{label}={v}", scope, rows, notes.get(v, ""))
        results += res
        yearly[f"{label}={v} ({scope})"] = yrs
    dev = harness.choose(grid, replay.DEV_YEARS)
    roll, picks = harness.rolling_choice(grid)
    res, yrs = harness.evaluate(exp, f"{label} rolling choice", scope, roll,
                                "picks " + ", ".join(f"{y}:{v}" for y, v in picks.items()))
    results += res
    yearly[f"{label} rolling ({scope})"] = yrs
    oe, oe_picks = harness.odd_even_choice(grid)
    res, _ = harness.evaluate(exp, f"{label} odd→even choice", scope, oe,
                              "picks " + ", ".join(f"{y}:{v}" for y, v in oe_picks.items()))
    results += res
    return dev, picks, oe_picks


def main():
    pickups, _, _, records = replay.load_or_build(mode="core")
    rows = build(records, pickups)
    eval_rows = [r for r in rows if r["year"] in replay.EVAL_YEARS]
    est = [r for r in eval_rows if r["stage"] == bpt.STAGE_ESTABLISHED]
    new = [r for r in eval_rows if r["stage"] == bpt.STAGE_NEW]
    results, yearly, lines = [], {}, []

    # A
    dev_w = learn(rows, replay.DEV_YEARS)
    roll_w = {y: learn(rows, tuple(t for t in LEARN_YEARS if t < y)) for y in replay.EVAL_YEARS}
    a_dev = a_candidates(est, lambda y: dev_w)
    a_roll = a_candidates(est, lambda y: roll_w[y])
    for name, sub in a_dev.items():
        res, yrs = harness.evaluate("A", f"{name} (dev-fit)", "established", sub,
                                    "weights fitted on dev: dev rows are in-sample" if "span" not in name else "")
        results += res
        yearly[f"{name} (dev-fit)"] = yrs
    for name, sub in a_roll.items():
        if "span" in name:
            continue
        res, yrs = harness.evaluate("A", f"{name} (rolling fit)", "established", sub,
                                    "weights refitted each year on replay years before it")
        results += res
        yearly[f"{name} (rolling fit)"] = yrs
    lam_dev = harness.choose({l: a_dev[f"A4 shrunk, λ={l}"] for l in A4_LAMBDAS}, replay.DEV_YEARS)

    lines += ["# Experiments A, B, K: the established 50/50 and its windows (generated by `exp_established.py`)", "",
              f"Established-stage pickups, eval years: {len(est):,} ({len({r['cid'] for r in est})} customers). "
              f"New-stage: {len(new):,} ({len({r['cid'] for r in new})}).", "",
              "## A. Learned weights on the recent half P (the champion is 0.5)", "",
              "| Bucket (median gap) | A2 dev | A3 dev | Rows (dev) | A3 rolling, 2018 → 2026 |",
              "|---|---:|---:|---:|---|"]
    for b in sorted({r["bucket"] for r in est}):
        w = dev_w.get(b)
        rw = ", ".join(f"{roll_w[y][b][1]:.2f}" if b in roll_w[y] else "–" for y in replay.EVAL_YEARS)
        lines.append(f"| {b} | {w[0]:.2f} | {w[1]:.2f} | {w[2]:,} | {rw} |" if w else f"| {b} | – | – | – | {rw} |")
    spans = [r["span_P"] / (r["span_P"] + r["span_Y"]) for r in est]
    q = statistics.quantiles(spans, n=4)
    lines += ["", f"A1 span weights: median {statistics.median(spans):.2f}, quartiles {q[0]:.2f}–{q[2]:.2f}. "
              f"A4 λ chosen on dev: {lam_dev}.", ""]

    # B
    b_notes = {}
    for scope, sub_rows, mk in (("established", est, "half"), ("established", est, "solo"), ("new", new, "solo")):
        grid = {}
        for H in HORIZONS:
            out, miss = [], 0
            for r in sub_rows:
                b = r["B"][H]
                if b is None:
                    miss += 1
                    out.append(dict(r, cand=r["base"]))
                    continue
                rate = (b + r["Y"]) / 2 if mk == "half" else b
                out.append(dict(r, cand=rate * r["days"]))
            grid[H] = out
            b_notes[H] = f"no horizon rate (fell back) on {miss}"
        evaluate_grid("B", f"B-{mk} H", scope, grid, results, yearly, b_notes)

    # K
    k_dev = {}
    for scope, sub_rows in (("established", est), ("new", new)):
        grid, notes = {}, {}
        for t in TAUS:
            miss = sum(1 for r in sub_rows if r["K"][t] is None)
            grid[t] = [dict(r, cand=(r["K"][t] * r["days"]) if r["K"][t] is not None else r["base"])
                       for r in sub_rows]
            notes[t] = f"fell back on {miss}"
        k_dev[scope] = evaluate_grid("K", "K τ", scope, grid, results, yearly, notes)[0]

    harness.write_results(harness.HERE / "results_ABK.csv", results)
    impacts = []
    for scope, sub_rows in (("established", est), ("new", new)):
        t = k_dev[scope]
        rows_t = [dict(r, cand=r["K"][t] * r["days"] if r["K"][t] is not None else r["base"])
                  for r in sub_rows if r["year"] in replay.CONFIRM_YEARS]
        impacts += harness.customer_impacts("K", f"K τ={t} ({scope})", "confirm 2024-26", rows_t)
    harness.write_impacts(harness.HERE / "impacts_ABK.csv", impacts)

    def section(exp, title):
        sub = [r for r in results if r["experiment"] == exp]
        return [f"## {title}", "", harness.table(sub), ""]

    lines += section("A", "A. Comparisons (Δ vs the 50/50; identical pickups)")
    lines += section("B", "B. Fixed horizons")
    lines += section("K", "K. Exposure-aware decaying rate")
    lines += [f"K τ chosen on dev: established {k_dev['established']}, new {k_dev['new']}.", ""]
    restart = [r for r in new if r["restart"]]
    for label, sub_rows in (("new, after a dormant restart", restart),
                            ("new, genuinely new", [r for r in new if not r["restart"]])):
        t = k_dev["new"]
        sub = [dict(r, cand=r["K"][t] * r["days"] if r["K"][t] is not None else r["base"]) for r in sub_rows]
        if sub:
            c = m.compare(sub, draws=1000)
            lines.append(f"- K τ={t}, {label}: {c['pickups']:,} pickups ({c['customers']}), "
                         f"Δ {c['d_wape']:+.2f} ({m.ci(c)}), bias {c['base_bias']:+.1f}% → {c['cand_bias']:+.1f}%")
    lines.append("")
    lines += ["## Δ WAPE by year", "", harness.year_table(yearly), ""]
    best = {"A3 covariance-aware (rolling fit)": a_roll["A3 covariance-aware"],
            f"A4 λ={lam_dev} (rolling fit)": a_roll[f"A4 shrunk, λ={lam_dev}"]}
    for name, sub in best.items():
        lines += [f"## Splits for {name}, all eval years", "",
                  harness.split_table(sub, lambda r: r["bucket"], "Frequency"), "",
                  harness.split_table(sub, lambda r: r["gap_bucket"], "Days on the clock"), ""]
    (harness.HERE / "results_ABK.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
