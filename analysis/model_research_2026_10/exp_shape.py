#!/usr/bin/env python3
"""
Experiments D (level x shape), E (group shrinkage of the index) and F (a
smoothed index) on the two seasonal scopes defined in exp_seasonal.py:
strict (the champion's seasonal stage) and wide (a component test: the
production seasonal formula on every established/seasonal pickup of a
customer whose prior-year shape swings).

D   rate = w x recent level + (1 - w) x historical level, each re-timed by the
    production index (exp_seasonal.levels). w in {0, 0.3, 0.5, 0.7, 1}:
    0 is C3 and 0.5 is C4 (references, already rejected). Chosen on dev.
E   the production index shrunk toward its group's: lam x own + (1 - lam) x
    group, lam = n_years / (n_years + k), k in {0, 1, 2, 4} (0 = champion),
    renormalised to mean 1. Group = the mean index of every customer with a
    profile that year (>= 300 gal/yr) whose peak falls in the same half
    (Apr-Sep / Oct-Mar), the customer itself left out. Uses pickups before
    Jan 1 only. Only half B reads the index, so only half B changes.
F   half B's day weighting from a linearly interpolated daily index (month
    values at mid-month) instead of the step; a closed (zero) month stays a
    step. No parameter.

    python3 exp_shape.py      (from this folder)
"""

import statistics
from collections import defaultdict
from datetime import date, timedelta

import exp_seasonal as es
import harness
import metrics as m
import replay
from replay import bpt, seasonal_open

D_GRID = (0.0, 0.3, 0.5, 0.7, 1.0)
E_GRID = (0, 1, 2, 4)
MID = 15


def peak_half(f):
    return "summer" if 3 <= max(range(12), key=lambda i: f[i]) <= 8 else "winter"


def group_factors(pickups, years):
    """{(year, half): (sum vector, members{cid})} from every customer's prior-year profile."""
    out = {}
    for y in years:
        acc = defaultdict(lambda: ([0.0] * 12, {}))
        for cid, ps in pickups.items():
            s = seasonal_open.season_for_year(ps, y)
            if not s or s["gal_per_year"] < seasonal_open.MIN_GAL_PER_YEAR:
                continue
            vec, members = acc[peak_half(s["factor"])]
            for i in range(12):
                vec[i] += s["factor"][i]
            members[cid] = s["factor"]
        for half, (vec, members) in acc.items():
            out[(y, half)] = (vec, members)
    return out


def group_without(group, cid):
    """The group's mean index with `cid` left out, or None."""
    vec, members = group
    others = len(members) - (cid in members)
    if others < 1:
        return None
    own = members.get(cid, [0.0] * 12)
    return [(vec[i] - own[i]) / others for i in range(12)]


def shrunk(own, n_years, k, g):
    lam = n_years / (n_years + k)
    f = [lam * own[i] + (1 - lam) * g[i] for i in range(12)]
    mean = statistics.fmean(f)
    return [v / mean for v in f]


def daily_index(factor, t):
    """Linear between mid-month points; a zero month or a zero neighbour stays a step."""
    i = t.month - 1
    j = (i + 1) % 12 if t.day >= MID else (i - 1) % 12
    if factor[i] == 0 or factor[j] == 0:
        return factor[i]
    centre = date(t.year, t.month, MID)
    if t.day >= MID:
        nxt = date(t.year + t.month // 12, t.month % 12 + 1, MID)
        frac = (t - centre).days / (nxt - centre).days
    else:
        prv = date(t.year - (t.month == 1), (t.month - 2) % 12 + 1, MID)
        frac = (centre - t).days / (centre - prv).days
    return (1 - frac) * factor[i] + frac * factor[j]


def smooth_gw(factor, a, b):
    if b <= a:
        return 1.0
    n = (b - a).days
    return sum(daily_index(factor, a + timedelta(days=d)) for d in range(1, n + 1)) / n


def half_b_smooth(h, factor, clock, asof):
    n = len(h)
    if n < 4:
        return None
    r = bpt.bsm.rate_span(h, n - 4, n - 1)
    cov = smooth_gw(factor, h[n - 4]["date"], h[n - 1]["date"])
    if r is None or cov <= 0:
        return None
    return r / cov * smooth_gw(factor, clock, asof)


def build(records, pickups):
    groups = group_factors(pickups, replay.EVAL_YEARS)
    seasons = {}
    rows = []
    for r in records:
        if not r["scored"] or r["year"] not in replay.EVAL_YEARS:
            continue
        strict = r["stage"] == bpt.STAGE_SEASONAL
        wide = False
        s = None
        if r["stage"] in (bpt.STAGE_ESTABLISHED, bpt.STAGE_SEASONAL):
            key = (r["cid"], r["year"])
            if key not in seasons:
                seasons[key] = seasonal_open.season_for_year(pickups[r["cid"]], r["year"])
            s = seasons[key]
            wide = bool(s and s["trough"] >= seasonal_open.OPEN_TROUGH
                        and s["amplitude"] >= es.WIDE_AMPLITUDE
                        and s["gal_per_year"] >= seasonal_open.MIN_GAL_PER_YEAR)
        if not (strict or wide):
            continue
        h = pickups[r["cid"]][:r["k"]]
        prod = s["factor"]
        clock, asof, days = r["clock_start"], r["date"], r["days"]
        base, a_prod, b_prod = bpt.seasonal_rate(h, prod, clock, asof)
        if strict:
            base = r["rate"]
        if base is None:
            continue
        rates = {}
        rec, hist = es.levels(h, prod, clock, asof)
        for w in D_GRID:
            parts = [(w, rec), (1 - w, hist)]
            have = [(x, v) for x, v in parts if v is not None and x > 0]
            tot = sum(x for x, _ in have)
            rates[f"D w={w}"] = sum(x * v for x, v in have) / tot if have and tot else (rec or hist)
        group = groups.get((r["year"], peak_half(prod)))
        g = group_without(group, r["cid"]) if group else None
        for k in E_GRID[1:]:
            f = shrunk(prod, len(s["years"]), k, g) if g else None
            rates[f"E k={k}"] = bpt.seasonal_rate(h, f, clock, asof)[0] if f else None
        rates["E k=0"] = base
        rates["F smoothed index"] = es.mean_of(a_prod, half_b_smooth(h, prod, clock, asof) or b_prod)
        n = len(h)
        i = es.prev_year_start(h, asof)
        rows.append({
            "cid": r["cid"], "year": r["year"], "actual": r["actual"], "days": days,
            "name": h[-1]["name"], "base": base * days, "strict": strict, "wide": wide,
            "months": es.months_crossed(clock, asof), "n_years": len(s["years"]),
            "overlap": ((h[-1]["date"] - h[max(0, n - 7)]["date"]).days
                        / (h[-1]["date"] - h[i]["date"]).days) if i is not None and i < n - 1 else None,
            "boundary": "within 7 d of a month boundary" if min(asof.day, 32 - asof.day) <= 7 else "mid-month",
            "rates": {k: (v * days if v is not None else None) for k, v in rates.items()},
        })
    return rows


def main():
    pickups, _, _, records = replay.load_or_build(mode="core")
    rows = build(records, pickups)
    results, yearly, picks = [], {}, {}
    for scope in ("strict", "wide"):
        for exp, names in (("D", [f"D w={w}" for w in D_GRID]),
                           ("E", [f"E k={k}" for k in E_GRID]),
                           ("F", ["F smoothed index"])):
            grid = {}
            for name in names:
                sub = es.with_cand(rows, name, scope)
                miss = sum(1 for r in rows if r[scope] and r["rates"][name] is None)
                res, yrs = harness.evaluate(exp, name, scope, sub, f"fell back on {miss}")
                results += res
                yearly[f"{name} ({scope})"] = yrs
                grid[name] = sub
            if len(names) > 1:
                dev = harness.choose(grid, replay.DEV_YEARS)
                roll, p = harness.rolling_choice(grid)
                res, yrs = harness.evaluate(exp, f"{exp} rolling choice", scope, roll,
                                            "picks " + ", ".join(f"{y}:{v}" for y, v in p.items()))
                results += res
                yearly[f"{exp} rolling ({scope})"] = yrs
                picks[(exp, scope)] = (dev, p)
    harness.write_results(harness.HERE / "results_DEF.csv", results)
    impacts = []
    for name in ("E k=4", "F smoothed index"):
        for scope in ("strict", "wide"):
            sub = [r for r in es.with_cand(rows, name, scope) if r["year"] in replay.CONFIRM_YEARS]
            impacts += harness.customer_impacts(name[0], f"{name} ({scope})", "confirm 2024-26", sub)
    harness.write_impacts(harness.HERE / "impacts_DEF.csv", impacts)

    ov = [r["overlap"] for r in rows if r["overlap"] is not None]
    q = statistics.quantiles(ov, n=4)
    lines = ["# Experiments D, E, F: level × shape, group shrinkage, smoothing (generated by `exp_shape.py`)", "",
             f"Strict pickups: {sum(r['strict'] for r in rows):,}; wide: {sum(r['wide'] for r in rows):,}.", "",
             "Dev choices and rolling picks:", ""]
    for (exp, scope), (dev, p) in picks.items():
        lines.append(f"- {exp}, {scope}: dev **{dev}**; rolling " + ", ".join(f"{y}: {v}" for y, v in p.items()))
    lines += ["", f"D training windows: the recent window (last 6 gaps) always lies inside the historical one "
              f"(the year to the last pickup); it covers a median {100 * statistics.median(ov):.0f}% of it "
              f"(quartiles {100 * q[0]:.0f}–{100 * q[2]:.0f}%). The two levels are not independent.", ""]
    for exp, title in (("D", "D. Level × shape"), ("E", "E. Group shrinkage"), ("F", "F. Smoothed index")):
        lines += [f"## {title}", "", harness.table([r for r in results if r["experiment"] == exp]), ""]
    lines += ["## Δ WAPE by year", "", harness.year_table(yearly), ""]
    for scope in ("strict", "wide"):
        sub = es.with_cand(rows, "F smoothed index", scope)
        lines += [f"## F splits ({scope})", "",
                  harness.split_table(sub, lambda r: r["boundary"], "As-of date"), "",
                  harness.split_table(sub, lambda r: "same month" if r["months"] == 1 else
                                      "2 months" if r["months"] == 2 else "3+ months", "Forecast interval"), ""]
    (harness.HERE / "results_DEF.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
