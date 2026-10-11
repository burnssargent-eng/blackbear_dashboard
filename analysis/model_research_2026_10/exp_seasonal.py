#!/usr/bin/env python3
"""
Experiment C: seasonal exposure over actual pickup intervals.

Production's seasonal half B already divides by the day-weighted index over
the history span and multiplies by the day-weighted index over the forecast
span (cf.gap_weighted), so C1/C5 are production and are not re-run. Still
open, and tested here:

  C-idx   the INDEX itself. Production builds it by spreading each pickup's
          gallons flat over min(gap, 60) days: it assumes no seasonality inside
          a gap. C-idx fits gallons_i = L_year x sum over the interval's days of
          I(month) by Poisson EM (multiplicative updates), mean-1 normalised,
          zeros kept, the same prior complete years. Only the factor changes.
  C-cap   production's spread with the 60-day cap changed (grid 90/120/180).
  C-A     half A on an exposure basis: last year's +/-3-week window and the
          last-2 rate each rescaled from their own days' index to the forecast
          days' index. Half B unchanged.
  C2      half B over the last 6 gaps instead of the last 3.
  C3, C4  reference rows already tested elsewhere: historical level x
          exposure; and 1/2 recent + 1/2 historical level x exposure
          (= "season-corrected 50/50", seasonal_formulas.md).

Two scopes:
  strict   pickups the champion ranked on the seasonal stage: the production
           impact. Small in the dev years (~55 pickups).
  wide     every scored established/seasonal pickup of a customer whose
           prior-year shape has amplitude >= 0.25, trough >= 0.10 and
           >= 300 gal/yr (no repeatability or shuffle test). There the
           champion is the PRODUCTION SEASONAL FORMULA with the production
           index, so the test isolates the ingredient, not the routing.

    PYTHONPATH=analysis python3 analysis/model_research_2026_10/exp_seasonal.py
"""

import csv
import statistics
from collections import defaultdict
from datetime import date, timedelta

import harness
import metrics as m
import replay
from replay import bpt, seasonal_open

cf = seasonal_open.cf
ss = seasonal_open.ss

CAP_GRID = (60, 90, 120, 180)        # 60 = production
EM_ITERS = 200
# EM drives a poorly identified month to 0 (a month that only ever sits inside
# longer gaps); a forecast span inside it then divides by ~0. Every customer in
# scope has a production trough >= OPEN_TROUGH, so the fitted index is floored
# there. Added after the unfloored version blew up on 2018-19 rows: a numerical
# guard, not a tuned value, disclosed in seasonal_exposure.md.
EM_FLOOR = seasonal_open.OPEN_TROUGH
WIDE_AMPLITUDE = 0.25


# ─────────────────────────────────────────────
# Index estimators (all from pickups before Jan 1 of the year)
# ─────────────────────────────────────────────

def merged(prior):
    by = defaultdict(int)
    for p in prior:
        by[p["date"]] += p["gallons"]
    return sorted(by.items())


def capped_daily(prior, cap):
    pts = merged(prior)
    daily = defaultdict(float)
    for (d0, _), (d1, g) in zip(pts, pts[1:]):
        span = min((d1 - d0).days, cap)
        for i in range(span):
            daily[d1 - timedelta(days=i)] += g / span
    return daily


def spread_factor(ps, year, cap):
    """seasonal_open.season_for_year's factor with a different spread cap."""
    prior = [p for p in ps if p["date"] < date(year, 1, 1)]
    if len(prior) < 2:
        return None
    daily = capped_daily(prior, cap)
    years = seasonal_open.profile_years(year)
    profile = {y: idx for y in years if (idx := cf.year_index(daily, y)) is not None}
    used = [y for y in years if y in profile]
    if len(used) < cf.MIN_YEARS_FOR_FACTOR:
        return None
    return seasonal_open.zero_keeping_shape(profile, used)


def em_factor(ps, year, fallback):
    """
    Exposure-consistent monthly index: Poisson EM for
        gallons_i ~ sum_{t in (a_i, b_i]} L[year(t)] * I[month(t)]
    over intervals of 1-180 days lying wholly inside the profile years.
    Months with no exposure keep the production value; covered months are
    floored at EM_FLOOR. Mean 1.
    """
    years = set(seasonal_open.profile_years(year))
    pts = merged([p for p in ps if p["date"] < date(year, 1, 1)])
    ivs = []
    for (d0, _), (d1, g) in zip(pts, pts[1:]):
        gap = (d1 - d0).days
        if not 0 < gap <= bpt.STALE_DAYS:
            continue
        cells = defaultdict(int)
        ok = True
        for i in range(1, gap + 1):
            t = d0 + timedelta(days=i)
            if t.year not in years:
                ok = False
                break
            cells[(t.year, t.month - 1)] += 1
        if ok:
            ivs.append((g, dict(cells)))
    have = {y for _, c in ivs for (y, _) in c}
    if len(have) < cf.MIN_YEARS_FOR_FACTOR:
        return None
    N = defaultdict(int)
    for _, c in ivs:
        for k, n in c.items():
            N[k] += n
    covered = {mm for (_, mm) in N}
    I = [1.0] * 12
    L = {y: 1.0 for y in have}
    for _ in range(EM_ITERS):
        A = defaultdict(float)
        for g, c in ivs:
            e = sum(n * L[y] * I[mm] for (y, mm), n in c.items())
            if e <= 0:
                continue
            for (y, mm), n in c.items():
                A[(y, mm)] += g * n * L[y] * I[mm] / e
        for mm in covered:
            den = sum(L[y] * N[(y, mm)] for y in have if (y, mm) in N)
            I[mm] = sum(A[(y, mm)] for y in have if (y, mm) in A) / den if den else 0.0
        for y in have:
            den = sum(I[mm] * N[(y, mm)] for mm in range(12) if (y, mm) in N)
            L[y] = sum(A[(y, mm)] for mm in range(12) if (y, mm) in A) / den if den else 0.0
        mean = statistics.fmean(I[mm] for mm in covered) if covered else 0
        if mean <= 0:
            return None
        I = [I[mm] / mean if mm in covered else None for mm in range(12)]
        L = {y: L[y] * mean for y in have}
        I = [max(v, EM_FLOOR) if v is not None else 1.0 for v in I]
    out = [I[mm] if mm in covered else fallback[mm] for mm in range(12)]
    mean = statistics.fmean(out)
    return [v / mean for v in out] if mean > 0 else None


# ─────────────────────────────────────────────
# Rates
# ─────────────────────────────────────────────

def gw(factor, a, b):
    return cf.gap_weighted(factor, a, b)


def prev_year_start(h, asof):
    cutoff = asof - timedelta(days=bpt.YEAR_DAYS)
    return next((i for i in range(len(h) - 1, -1, -1) if h[i]["date"] <= cutoff), None)


def half_a_exposure(h, factor, clock, asof):
    n = len(h)
    daily = ss.daily_production(h)
    first = h[0]["date"]
    fut = gw(factor, clock, asof)
    vals = []
    for back in (1, 2):
        centre = asof - timedelta(days=bpt.YEAR_DAYS * back)
        r = bpt.window_rate(daily, first, centre, bpt.SEASONAL_LY_HALF_WIDTH)
        if r is None:
            continue
        w = gw(factor, centre - timedelta(days=bpt.SEASONAL_LY_HALF_WIDTH + 1),
               centre + timedelta(days=bpt.SEASONAL_LY_HALF_WIDTH))
        if w > 0:
            vals.append(r / w * fut)
    ly = statistics.fmean(vals) if vals else None
    last2 = bpt.bsm.rate_span(h, n - 3, n - 1) if n >= 3 else None
    if ly is None or last2 is None:
        return None
    c2 = gw(factor, h[n - 3]["date"], h[n - 1]["date"])
    if c2 <= 0:
        return None
    return bpt.SEASONAL_LY_WEIGHT * ly + (1 - bpt.SEASONAL_LY_WEIGHT) * last2 / c2 * fut


def half_b(h, factor, clock, asof, gaps):
    n = len(h)
    if n < gaps + 1:
        return None
    r = bpt.bsm.rate_span(h, n - 1 - gaps, n - 1)
    cov = gw(factor, h[n - 1 - gaps]["date"], h[n - 1]["date"])
    if r is None or cov <= 0:
        return None
    return r / cov * gw(factor, clock, asof)


def mean_of(*xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def levels(h, factor, clock, asof):
    """(recent level, historical level) x forecast exposure, or Nones."""
    n = len(h)
    fut = gw(factor, clock, asof)
    rec = hist = None
    lo = max(0, n - 7)
    r = bpt.bsm.rate_span(h, lo, n - 1)
    c = gw(factor, h[lo]["date"], h[n - 1]["date"])
    if r is not None and c > 0:
        rec = r / c * fut
    i = prev_year_start(h, asof)
    if i is not None and i < n - 1:
        r = bpt.bsm.rate_span(h, i, n - 1)
        c = gw(factor, h[i]["date"], h[n - 1]["date"])
        if r is not None and c > 0:
            hist = r / c * fut
    return rec, hist


# ─────────────────────────────────────────────
# Scopes
# ─────────────────────────────────────────────

def months_crossed(a, b):
    ms = set()
    t = a + timedelta(days=1)
    while t <= b:
        ms.add((t.year, t.month))
        t += timedelta(days=1)
    return len(ms)


def build(records, pickups):
    seasons = {}

    def season(cid, year):
        if (cid, year) not in seasons:
            seasons[(cid, year)] = seasonal_open.season_for_year(pickups[cid], year)
        return seasons[(cid, year)]

    factors = {}

    def fac(kind, cid, year, prod):
        key = (kind, cid, year)
        if key not in factors:
            if kind == "em":
                factors[key] = em_factor(pickups[cid], year, prod)
            else:
                factors[key] = spread_factor(pickups[cid], year, kind)
        return factors[key]

    rows = []
    for r in records:
        if not r["scored"] or r["year"] not in replay.EVAL_YEARS:
            continue
        strict = r["stage"] == bpt.STAGE_SEASONAL
        wide_ok = False
        s = None
        if r["stage"] in (bpt.STAGE_ESTABLISHED, bpt.STAGE_SEASONAL):
            s = season(r["cid"], r["year"])
            wide_ok = bool(s and s["trough"] >= seasonal_open.OPEN_TROUGH
                           and s["amplitude"] >= WIDE_AMPLITUDE
                           and s["gal_per_year"] >= seasonal_open.MIN_GAL_PER_YEAR)
        if not (strict or wide_ok):
            continue
        h = pickups[r["cid"]][:r["k"]]
        prod = s["factor"]
        clock, asof, days = r["clock_start"], r["date"], r["days"]
        base_rate = r["rate"] if strict else bpt.seasonal_rate(h, prod, clock, asof)[0]
        if base_rate is None:
            continue
        _, a_prod, b_prod = bpt.seasonal_rate(h, prod, clock, asof)
        rates = {}
        f_em = fac("em", r["cid"], r["year"], prod)
        rates["C-idx"] = bpt.seasonal_rate(h, f_em, clock, asof)[0] if f_em else None
        for cap in CAP_GRID[1:]:
            f = fac(cap, r["cid"], r["year"], prod)
            rates[f"C-cap {cap}"] = bpt.seasonal_rate(h, f, clock, asof)[0] if f else None
        rates["C-A"] = mean_of(half_a_exposure(h, prod, clock, asof), b_prod)
        rates["C2"] = mean_of(a_prod, half_b(h, prod, clock, asof, 6) or b_prod)
        rec, hist = levels(h, prod, clock, asof)
        rates["C3 (ref)"] = hist
        rates["C4 (ref)"] = mean_of(rec, hist)
        rates["50/50 (ref)"] = (r["pooled"] + r["prev_year"]) / 2 if r["prev_year"] else None
        peak = max(range(12), key=lambda i: prod[i])
        rows.append({
            "cid": r["cid"], "year": r["year"], "actual": r["actual"], "days": days,
            "base": base_rate * days, "strict": strict, "wide": wide_ok,
            "amplitude": s["amplitude"], "peak": "summer peak (Apr-Sep)" if 3 <= peak <= 8 else "winter peak (Oct-Mar)",
            "months": months_crossed(clock, asof),
            "rates": {k: (v * days if v is not None else None) for k, v in rates.items()},
            "name": h[-1]["name"],
        })
    return rows


def with_cand(rows, name, scope):
    out = []
    for r in rows:
        if scope == "strict" and not r["strict"]:
            continue
        if scope == "wide" and not r["wide"]:
            continue
        c = r["rates"].get(name)
        out.append(dict(r, cand=c if c is not None else r["base"]))
    return out


def main():
    pickups, _, _, records = replay.load_or_build(mode="core")
    rows = build(records, pickups)
    names = ["C-idx", *[f"C-cap {c}" for c in CAP_GRID[1:]], "C-A", "C2", "C3 (ref)", "C4 (ref)", "50/50 (ref)"]
    results, yearly, impacts = [], {}, []
    cover = {}
    for scope in ("strict", "wide"):
        for name in names:
            sub = with_cand(rows, name, scope)
            if not sub:
                continue
            cover[(scope, name)] = sum(1 for r in sub if r["rates"].get(name) is None)
            res, yrs = harness.evaluate("C", name, scope, sub,
                                        note=f"no rate (fell back to champion) on {cover[(scope, name)]}")
            results += res
            yearly[f"{name} [{scope}]"] = yrs

    # C-cap: the grid chosen on dev, by rolling origin, and odd/even (wide scope).
    grid = {60: [dict(r, cand=r["base"]) for r in rows if r["wide"]]}
    for cap in CAP_GRID[1:]:
        grid[cap] = with_cand(rows, f"C-cap {cap}", "wide")
    dev_pick = harness.choose(grid, replay.DEV_YEARS)
    roll_rows, roll_picks = harness.rolling_choice(grid)
    oe_rows, oe_picks = harness.odd_even_choice(grid)
    res, yrs = harness.evaluate("C", "C-cap rolling choice", "wide", roll_rows,
                                note="cap chosen on eval years before each year: "
                                     + ", ".join(f"{y}:{v}" for y, v in roll_picks.items()))
    results += res
    yearly["C-cap rolling [wide]"] = yrs
    res, yrs = harness.evaluate("C", "C-cap odd->even choice", "wide", oe_rows,
                                note="even years only, cap chosen on odd years before: "
                                     + ", ".join(f"{y}:{v}" for y, v in oe_picks.items()))
    results += res

    # Customer impacts on confirm, wide scope, the main new candidates.
    for name in ("C-idx", "C-A", "C2"):
        sub = [r for r in with_cand(rows, name, "wide") if r["year"] in replay.CONFIRM_YEARS]
        for cid, rs in m.by(sub, lambda r: r["cid"]).items():
            act = sum(r["actual"] for r in rs)
            impacts.append({
                "experiment": "C", "candidate": name, "window": "confirm 2024-26",
                "customer_id": cid, "customer": rs[-1]["name"], "pickups": len(rs),
                "actual_gal": act,
                "champion_wape": round(100 * sum(abs(r["base"] - r["actual"]) for r in rs) / act, 1),
                "candidate_wape": round(100 * sum(abs(r["cand"] - r["actual"]) for r in rs) / act, 1),
                "mean_pred_change_pct": round(100 * statistics.fmean(
                    (r["cand"] - r["base"]) / r["base"] for r in rs if r["base"] > 0), 1),
            })

    harness.write_results(harness.HERE / "results_C.csv", results)
    write_impacts(harness.HERE / "impacts_C.csv", impacts)
    write_report(rows, results, yearly, dev_pick, roll_picks, oe_picks)


def write_impacts(path, impacts):
    if not impacts:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(impacts[0]))
        w.writeheader()
        w.writerows(impacts)


def write_report(rows, results, yearly, dev_pick, roll_picks, oe_picks):
    strict = [r for r in rows if r["strict"]]
    wide = [r for r in rows if r["wide"]]
    amp_cut = statistics.median(r["amplitude"] for r in wide)
    lines = ["# Experiment C: seasonal exposure (generated by `exp_seasonal.py`)", "",
             f"Scopes: strict = {len(strict):,} pickups / {len({r['cid'] for r in strict})} customers "
             f"(champion seasonal stage); wide = {len(wide):,} / {len({r['cid'] for r in wide})} "
             "(component test, champion = production seasonal formula).", "",
             f"C-cap chosen on dev: **{dev_pick}** days (60 = production). Rolling picks: "
             + ", ".join(f"{y} {v}" for y, v in roll_picks.items())
             + ". Odd→even picks: " + ", ".join(f"{y} {v}" for y, v in oe_picks.items()) + ".", "",
             "## Pooled comparisons", "", harness.table(results), "",
             "## Δ WAPE by year (negative = candidate better)", "", harness.year_table(yearly), ""]
    for name in ("C-idx", "C-A", "C2", "C4 (ref)"):
        sub = [r for r in with_cand(rows, name, "wide") if r["year"] in replay.EVAL_YEARS]
        lines += [f"## Splits for {name}, wide scope, all eval years", "",
                  harness.split_table(sub, lambda r: {1: "1 month", 2: "2 months"}.get(r["months"], "3+ months"),
                                      "Forecast interval spans"), "",
                  harness.split_table(sub, lambda r: "high amplitude" if r["amplitude"] >= amp_cut else "low amplitude",
                                      f"Amplitude (cut {amp_cut:.2f})"), "",
                  harness.split_table(sub, lambda r: r["peak"], "Peak"), ""]
    (harness.HERE / "results_C.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
