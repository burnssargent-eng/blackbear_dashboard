#!/usr/bin/env python3
"""
Seasonal formulas, round 2: narratively simple options on a wider sample.

Phase 2 left three seasonal formulas within ~2 WAPE points of each other on
too small a selection sample (7 customers). This widens the test and adds
three options, each with a plain-language story:

  YoY growth      last year's rate around today x (last 12 months' gallons /
                  the 12 before): "they did X last July and run 10% busier"
  Season-corr.    today's 50/50 with each half rescaled from the season it
    50/50         was measured in to the season ahead
  Same days,      average rate over these same calendar days in each of up
    past years    to 3 prior years: "how fast does it fill this time of year"

each alone and paired 50/50 with Sarge's 70/30 ("two witnesses").

Wider sample:
  * choose = test pickups 2021-23, confirm = 2024-26 (phase 2: 2023-24 / 25-26);
  * selection on the WIDE seasonal group: open (no month below 0.10) and
    amplitude >= 0.25 from prior years, no repeatability or noise filter;
    the STRICT seasonal-open group and EVERY open customer are reported too;
  * inactive customers included (their pickups are real).
Every model is compared on the same pickups (all models present).

    python3 analysis/backtest_seasonal_formulas.py

Writes analysis/seasonal_formulas.md. Standard library only; deterministic.
"""

import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import backtest_customer_factors as bcf
import backtest_phase2_rates as p2
import backtest_steady_rate as bt
import build_projection_table as bpt
import customer_factors as cf
import seasonal_open as so

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "seasonal_formulas.md"

YEAR = 365
TEST_FROM = date(2021, 1, 1)
CHOOSE = (2021, 2022, 2023)
MIN_PRIOR = 8
WIDE_AMPLITUDE = 0.25
GROWTH_CLAMP = (0.5, 2.0)
SEED = 42

BASE = "50/50 (today)"
SARGE = "Sarge 70/30"
SINGLES = [SARGE, "Last 3 ÷ index × coming", "Level × month index",
           "YoY growth", "Season-corrected 50/50", "Same days, past years"]
PAIRS = [f"Avg: 70/30 + {m}" for m in SINGLES[1:]]
MODELS = SINGLES + PAIRS + ["Avg of 70/30, last 3 ÷ index, YoY growth"]

# Weight sweep (Sarge, 2026-10-08): last-year share of the LY / last-2 blend,
# alone and averaged with last 3 ÷ index.
WEIGHTS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
SWEEP_ALONE = [f"LY/last 2 {round(w * 100)}/{round(100 - w * 100)}" for w in WEIGHTS]
SWEEP_PAIRED = [f"Avg: {m} + last 3 ÷ index" for m in SWEEP_ALONE]
SWEEP = SWEEP_ALONE + SWEEP_PAIRED

# Growth-adjusted last year inside the 70/30 (Sarge, 2026-10-08). Last year's
# rate runs ~13% below what arrives on the strict group, consistent with
# growth; scale it by the last 12 months / the 12 before.
GROWN = ["70/30, LY × growth", "70/30, LY × growth per year",
         "Avg: 70/30 LY × growth + last 3 ÷ index",
         "Avg: 70/30 LY × growth per year + last 3 ÷ index"]
GROWTH_REF = [SARGE, "Avg: 70/30 + Last 3 ÷ index × coming"]


def load():
    pickups = bt.load_pickups(ids=None)
    checks = bpt.load_empty_checks(date.max)
    active = bpt.load_active_ids()
    bpt.apply_history_starts(pickups, checks)
    bpt.combine_shared(pickups, checks, {}, defaultdict(list), active)
    excluded = {cid for cid, o in bpt.MODEL_OVERRIDES.items() if o.status in p2.NOT_OPEN}
    return {cid: ps for cid, ps in pickups.items()
            if cid not in excluded and len(ps) > MIN_PRIOR}, checks


def growth(pickups, k):
    """Last 12 months' rate / the 12 before, ending at the last known pickup."""
    end = pickups[k - 1]["date"]
    now = bcf.rate_between(pickups, k, end - timedelta(days=YEAR), end)
    before = bcf.rate_between(pickups, k, end - timedelta(days=2 * YEAR),
                              end - timedelta(days=YEAR))
    if not now or not before:
        return None
    return min(GROWTH_CLAMP[1], max(GROWTH_CLAMP[0], now / before))


def growth_between(pickups, k, years_back):
    """Last 12 months' rate / the 12 months `years_back` years earlier."""
    end = pickups[k - 1]["date"]
    now = bcf.rate_between(pickups, k, end - timedelta(days=YEAR), end)
    then = bcf.rate_between(pickups, k, end - timedelta(days=YEAR * (years_back + 1)),
                            end - timedelta(days=YEAR * years_back))
    if not now or not then:
        return None
    return min(GROWTH_CLAMP[1], max(GROWTH_CLAMP[0], now / then))


def same_days_past(daily, first, start, end, years=3):
    """Mean rate over (start, end] shifted back 1..years years, where covered."""
    rates = []
    n = (end - start).days
    for back in range(1, years + 1):
        a = start - timedelta(days=YEAR * back)
        if a <= first + timedelta(days=60):
            break
        rates.append(sum(daily.get(a + timedelta(days=i + 1), 0.0) for i in range(n)) / n)
    return sum(rates) / len(rates) if rates else None


def rates_for(ps, k, start, factor, daily):
    x = ps[k]["date"]
    first = ps[0]["date"]
    last6 = p2.span(ps, k - 7, k - 1)
    prev_year, info = bt.rate_prev_year(ps, k, YEAR)
    base = p2.blend(last6, 0.5, prev_year)
    last2 = p2.span(ps, k - 3, k - 1)
    last3 = p2.span(ps, k - 4, k - 1)
    coming = cf.gap_weighted(factor, start, x)
    r = {BASE: base}

    ly = p2.last_year_rate(daily, first, x, 21)
    r[SARGE] = p2.blend(ly, 0.7, last2)

    covered = cf.gap_weighted(factor, ps[k - 4]["date"], ps[k - 1]["date"])
    r["Last 3 ÷ index × coming"] = (last3 / covered * coming
                                    if last3 is not None and covered > 0 else None)

    _, _, level, _ = bcf.base_rates(ps, k)
    r["Level × month index"] = level * coming if level is not None else None

    ly1 = p2.window_rate(daily, first, x - timedelta(days=YEAR), 21)
    g = growth(ps, k)
    r["YoY growth"] = ly1 * g if ly1 is not None and g is not None else None

    corr = None
    if last6 is not None and prev_year is not None:
        w6 = cf.gap_weighted(factor, ps[k - 7]["date"], ps[k - 1]["date"])
        wp = cf.gap_weighted(factor, info["training_start"], ps[k - 1]["date"])
        if w6 > 0 and wp > 0:
            corr = 0.5 * last6 / w6 * coming + 0.5 * prev_year / wp * coming
    r["Season-corrected 50/50"] = corr

    r["Same days, past years"] = same_days_past(daily, first, start, x)

    for m in SINGLES[1:]:
        r[f"Avg: 70/30 + {m}"] = (None if r[SARGE] is None or r[m] is None
                                  else (r[SARGE] + r[m]) / 2)
    b = r["Last 3 ÷ index × coming"]
    g1 = growth_between(ps, k, 1)
    ly_grown = ly * g1 if ly is not None and g1 is not None else None
    per_year = []
    for back in (1, 2):
        wr = p2.window_rate(daily, first, x - timedelta(days=YEAR * back), 21)
        gb = growth_between(ps, k, back)
        if wr is not None and gb is not None:
            per_year.append(wr * gb)
    ly_grown_py = sum(per_year) / len(per_year) if per_year else None
    r["70/30, LY × growth"] = p2.blend(ly_grown, 0.7, last2)
    r["70/30, LY × growth per year"] = p2.blend(ly_grown_py, 0.7, last2)
    for src, dst in (("70/30, LY × growth", "Avg: 70/30 LY × growth + last 3 ÷ index"),
                     ("70/30, LY × growth per year",
                      "Avg: 70/30 LY × growth per year + last 3 ÷ index")):
        r[dst] = None if r[src] is None or b is None else (r[src] + b) / 2
    for w, alone, paired in zip(WEIGHTS, SWEEP_ALONE, SWEEP_PAIRED):
        r[alone] = p2.blend(ly, w, last2)
        r[paired] = None if r[alone] is None or b is None else (r[alone] + b) / 2
    trio = [r[SARGE], r["Last 3 ÷ index × coming"], r["YoY growth"]]
    r["Avg of 70/30, last 3 ÷ index, YoY growth"] = (None if None in trio
                                                     else sum(trio) / 3)
    return r


def run():
    pickups, checks = load()
    rng = random.Random(SEED)
    groups, tests = {}, []
    for cid in sorted(pickups):
        ps = pickups[cid]
        running = p2.RunningDaily()
        for p in ps[:MIN_PRIOR]:
            running.add(p)
        for k in range(MIN_PRIOR, len(ps)):
            if k > MIN_PRIOR:
                running.add(ps[k - 1])
            x = ps[k]
            if x["date"] < TEST_FROM or x["gallons"] <= 0:
                continue
            y = x["date"].year
            if (cid, y) not in groups:
                season = so.season_for_year(ps, y)
                strict, _ = so.classify(season, rng)
                if season is None or season["trough"] < so.OPEN_TROUGH:
                    groups[(cid, y)] = (None, None)          # closer / unscored
                else:
                    groups[(cid, y)] = (season["factor"], {
                        "strict": strict == so.SEASONAL_OPEN,
                        "wide": season["amplitude"] >= WIDE_AMPLITUDE})
            factor, member = groups[(cid, y)]
            if factor is None:
                continue
            start = p2.clock_start(ps, k, checks.get(cid, []))
            days = (x["date"] - start).days
            if days <= 0 or days > bpt.STALE_DAYS:
                continue
            rates = rates_for(ps, k, start, factor, running.daily)
            if any(rates[m] is None for m in [BASE] + MODELS + SWEEP):
                continue
            # Growth models need two years of history; scored only where present.
            rates = {m: v for m, v in rates.items() if v is not None}
            tests.append({"customer_id": cid, "date": x["date"],
                          "period": "choose" if y in CHOOSE else "confirm",
                          "strict": member["strict"], "wide": member["wide"],
                          "actual": x["gallons"],
                          "grown": all(m in rates for m in GROWN),
                          "rates": {m: v * days for m, v in rates.items()}})
    return tests


def as_rates(rows):
    """p2.compare wants rate x days == projected gallons; days = 1 here."""
    return [dict(t, days=1) for t in rows]


def table(rows, rng, base=BASE, models=MODELS):
    out = [p2.HEADER]
    for m in models:
        out.append(p2.fmt_row(m, p2.compare(as_rates(rows), m, base=base, rng=rng)))
    return "\n".join(out)


def main():
    tests = run()
    rng = random.Random(SEED)
    w = []
    w.append("# Seasonal formulas, round 2: wider sample\n")
    w.append("Generated by `analysis/backtest_seasonal_formulas.py`. Every model is "
             "scored on the same pickups (all models available), rate × days since "
             "the clock started (empty checks restart it), nothing on or after the "
             "pickup used. Seasonality is scored from complete years before each "
             "pickup's year with the zero-keeping shape. **Choose** = pickups "
             "2021–23; **confirm** = 2024–26. Closers and gaps over "
             f"{bpt.STALE_DAYS} days are left out. Difference = model minus 50/50 "
             "in WAPE points (negative is better); interval = paired bootstrap "
             "within customers.\n")
    w.append("New models: **YoY growth** = last year's rate over today ±3 weeks × "
             "(last 12 months ÷ the 12 before, clamped 0.5–2). **Season-corrected "
             "50/50** = each half of today's 50/50 divided by the month index of the "
             "days it was measured over, times the index of the days ahead. **Same "
             "days, past years** = mean rate over these same calendar days in up to "
             "3 prior years. **Avg: 70/30 + X** = equal average with Sarge's 70/30.\n")
    sel = None
    for label, key in (("Wide seasonal (selection group)", "wide"),
                       ("Strict seasonal-open (the production stage)", "strict"),
                       ("Every open customer", None)):
        for period in ("choose", "confirm"):
            rows = [t for t in tests if t["period"] == period and (key is None or t[key])]
            n_c = len({t["customer_id"] for t in rows})
            w.append(f"## {label} — {period} ({len(rows):,} pickups, {n_c} customers)\n")
            # Intervals only where a decision rests on them; the all-open
            # view is context, and its bootstrap would take far longer.
            w.append(table(rows, rng if key else None) + "\n")
            if key == "wide" and period == "choose":
                scored = [(p2.compare(as_rates(rows), m)["wape"], m) for m in MODELS]
                sel = min(scored)[1]
    w.append(f"**Chosen on the wide group, 2021–23: {sel}.**\n")
    for period in ("confirm",):
        for key, label in (("wide", "wide"), ("strict", "strict")):
            rows = [t for t in tests if t["period"] == period and t[key]]
            r = p2.compare(as_rates(rows), sel, base=SARGE, rng=rng)
            w.append(f"- vs Sarge 70/30 alone, {label} group, {period}: "
                     f"{r['diff']:+.1f} points ({r['lo']:+.1f} to {r['hi']:+.1f})")
    w.append("")
    w.append("## Weight sweep: last year vs last 2 pickups\n")
    w.append("`LY/last 2 a/b` = a% last year's rate over today ±3 weeks (1–2 years "
             "back) + b% the last 2 pickups' rate; 70/30 is Sarge's formula. Each "
             "alone and averaged 50/50 with last 3 ÷ index. Same pickups as above.\n")
    for label, key in (("Strict seasonal-open", "strict"), ("Wide seasonal", "wide")):
        for period in ("choose", "confirm"):
            rows = [t for t in tests if t["period"] == period and t[key]]
            n_c = len({t["customer_id"] for t in rows})
            w.append(f"### {label} — {period} ({len(rows):,} pickups, {n_c} customers)\n")
            w.append(table(rows, rng, models=SWEEP) + "\n")
    w.append("## Last year scaled by growth, inside the 70/30\n")
    w.append("**LY × growth** = last year's rate (±3 weeks, 1–2 years back) × (last "
             "12 months' gallons per day ÷ the 12 months before), clamped 0.5–2. "
             "**Per year** = each year-back window × its own growth (last 12 months "
             "÷ the 12 months that many years earlier), then averaged. Scored on the "
             "pickups where every model exists, against the unadjusted versions.\n")
    for label, key in (("Strict seasonal-open", "strict"), ("Wide seasonal", "wide")):
        for period in ("choose", "confirm"):
            rows = [t for t in tests if t["period"] == period and t[key] and t["grown"]]
            n_c = len({t["customer_id"] for t in rows})
            w.append(f"### {label} — {period} ({len(rows):,} pickups, {n_c} customers)\n")
            w.append(table(rows, rng, models=GROWTH_REF + GROWN) + "\n")
    OUT_REPORT.write_text("\n".join(w) + "\n")
    print(f"wrote {OUT_REPORT.name}: {len(tests):,} pickups; chosen {sel}")


if __name__ == "__main__":
    main()
