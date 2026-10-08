#!/usr/bin/env python3
"""
Recent-rate override for frequently collected customers, and a growth factor
that does not double count.

Sarge, 2026-10-08: a customer picked up every couple of weeks has all the
information needed in its recent pickups -- short gaps, the same season --
so its current rate should come from them. Unlike the rejected recency gate
(which fired when recent pickups DISAGREED with older ones, mostly noise),
this triggers on pickup FREQUENCY.

  production   what the builder does today: the seasonal formula for strict
               seasonal-open customers, the 50/50 for everyone else
  override     when the median of the last 4 gaps is <= G days, the pooled
               rate over the last n pickups (fully, or 75/25 with production)

Also: the seasonal formula's growth (last 12 months / the 12 before) contains
the year-ago window it scales, so one unusual autumn counts twice (Skinny
Pancake Quechee, 2026-10-08). Non-overlapping version: the last 6 months /
the same 6 months a year earlier.

Choose = test pickups 2021-23, confirm = 2024-26. Every open customer
(closers left out). Projected gallons = rate x days since the clock started.

    python3 analysis/backtest_recent_override.py

Writes analysis/recent_override.md. Standard library only; deterministic.
"""

import random
import statistics
from datetime import timedelta
from pathlib import Path

import backtest_customer_factors as bcf
import backtest_phase2_rates as p2
import backtest_seasonal_formulas as sf
import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import build_projection_table as bpt
import seasonal_open as so

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "recent_override.md"

YEAR = 365
SEED = 42
HIGH = 1.45                       # the range's high end for established rows
TRIGGERS = (14, 21, 30)           # median of the last 4 gaps, days
RECENT = (("last 3", 3, 1.0), ("last 4", 4, 1.0), ("75/25 last 4", 4, 0.75))

PROD = "Production (today)"
SEASONAL_TODAY = "Avg: 70/30 LY × growth + last 3 ÷ index"
SEASONAL_NOOVERLAP = "Seasonal, growth = last 6 mo ÷ same 6 mo a year earlier"
SEASONAL_NOGROWTH = "Avg: 70/30 + Last 3 ÷ index × coming"


def label(g, name):
    return f"Gaps ≤ {g} d → {name}"


OVERRIDES = [label(g, name) for g in TRIGGERS for name, _, _ in RECENT]


def growth_6mo(ps, k):
    end = ps[k - 1]["date"]
    now = bcf.rate_between(ps, k, end - timedelta(days=182), end)
    then = bcf.rate_between(ps, k, end - timedelta(days=YEAR + 182), end - timedelta(days=YEAR))
    if not now or not then:
        return None
    return min(2.0, max(0.5, now / then))


def run():
    pickups, checks = sf.load()
    rng = random.Random(SEED)
    groups, tests = {}, []
    for cid in sorted(pickups):
        ps = pickups[cid]
        running = p2.RunningDaily()
        for p in ps[:sf.MIN_PRIOR]:
            running.add(p)
        for k in range(sf.MIN_PRIOR, len(ps)):
            if k > sf.MIN_PRIOR:
                running.add(ps[k - 1])
            x = ps[k]
            if x["date"] < sf.TEST_FROM or x["gallons"] <= 0:
                continue
            y = x["date"].year
            if (cid, y) not in groups:
                season = so.season_for_year(ps, y)
                strict, _ = so.classify(season, rng)
                closer = season is not None and season["trough"] < so.OPEN_TROUGH
                groups[(cid, y)] = (None if closer else (season or {}).get("factor"),
                                    strict == so.SEASONAL_OPEN, closer)
            factor, strict, closer = groups[(cid, y)]
            if closer:
                continue
            start = p2.clock_start(ps, k, checks.get(cid, []))
            days = (x["date"] - start).days
            if days <= 0 or days > bpt.STALE_DAYS:
                continue
            last6 = p2.span(ps, k - 7, k - 1)
            prev_year, _ = bt.rate_prev_year(ps, k, YEAR)
            base = p2.blend(last6, 0.5, prev_year)
            if base is None:
                continue

            r = {"50/50": base}
            prod = base
            if strict and factor:
                s = sf.rates_for(ps, k, start, factor, running.daily)
                if s.get(SEASONAL_TODAY) is not None:
                    prod = s[SEASONAL_TODAY]
                    r[SEASONAL_NOGROWTH] = s[SEASONAL_NOGROWTH]
                    ly = p2.last_year_rate(running.daily, ps[0]["date"], x["date"], 21)
                    g6 = growth_6mo(ps, k)
                    last2 = p2.span(ps, k - 3, k - 1)
                    b = s["Last 3 ÷ index × coming"]
                    if ly is not None and g6 is not None and last2 is not None:
                        r[SEASONAL_NOOVERLAP] = (0.7 * ly * g6 + 0.3 * last2 + b) / 2
            r[PROD] = prod

            gaps = [(ps[i]["date"] - ps[i - 1]["date"]).days for i in range(k - 4, k)]
            med = statistics.median(gaps)
            for g in TRIGGERS:
                for name, n, w in RECENT:
                    recent = bsm.rate_span(ps, k - 1 - n, k - 1)
                    use = med <= g and recent is not None
                    r[label(g, name)] = (w * recent + (1 - w) * prod) if use else prod
                r[f"_fires {g}"] = med <= g
            tests.append({"customer_id": cid, "period": "choose" if y in sf.CHOOSE else "confirm",
                          "strict": strict and r.get(SEASONAL_NOGROWTH) is not None,
                          "actual": x["gallons"], "fires": {g: r.pop(f"_fires {g}") for g in TRIGGERS},
                          "rates": {m: v * days for m, v in r.items()}})
    return tests


def metrics(rows, m):
    rows = [t for t in rows if m in t["rates"]]
    vol = sum(t["actual"] for t in rows)
    err = [t["rates"][m] - t["actual"] for t in rows]
    pr = [(t["rates"][m], t["actual"]) for t in rows]
    return {"n": len(rows), "wape": sum(map(abs, err)) / vol * 100, "bias": sum(err) / vol * 100,
            "low": sum(p < a / 1.33 for p, a in pr) / len(pr) * 100,
            "above": sum(a > p * HIGH for p, a in pr) / len(pr) * 100,
            "high": sum(p > a * 1.33 for p, a in pr) / len(pr) * 100}


def table(rows, models, rng, base=PROD):
    out = ["| Model | Pickups | WAPE | vs production | 95% interval | Bias | Badly low | Above range top | Badly high |",
           "|---|---:|---:|---:|---|---:|---:|---:|---:|"]
    for m in models:
        a = metrics(rows, m)
        c = p2.compare([dict(t, days=1) for t in rows if m in t["rates"]], m, base=base,
                       rng=rng if m != base else None)
        ci = f"{c['lo']:+.1f} to {c['hi']:+.1f}" if c and c["lo"] is not None else "–"
        out.append(f"| {m} | {a['n']:,} | {a['wape']:.1f}% | **{c['diff']:+.1f}** | {ci} | "
                   f"{a['bias']:+.1f}% | {a['low']:.1f}% | {a['above']:.1f}% | {a['high']:.1f}% |")
    return "\n".join(out)


def main():
    tests = run()
    rng = random.Random(SEED)
    w = ["# Recent-rate override and non-overlapping growth\n",
         "Generated by `analysis/backtest_recent_override.py`. **Production** = what "
         "the builder does today: the seasonal formula for strict seasonal-open "
         "customers, the 50/50 for everyone else. **Gaps ≤ G d → rate** = when the "
         "median of the last 4 gaps is at most G days, use the pooled rate of the last "
         "n pickups (or 75% of it + 25% production). **Badly low** = projection under "
         "75% of actual; **above range top** = actual over projection × 1.45; **badly "
         "high** = projection over 133% of actual. Choose = 2021–23, confirm = 2024–26.\n"]
    for period in ("choose", "confirm"):
        rows = [t for t in tests if t["period"] == period]
        w.append(f"## Every open customer — {period} ({len(rows):,} pickups, "
                 f"{len({t['customer_id'] for t in rows})} customers)\n")
        w.append(table(rows, [PROD] + OVERRIDES, rng) + "\n")
        for g in TRIGGERS:
            fired = [t for t in rows if t["fires"][g]]
            w.append(f"### Pickups where gaps ≤ {g} d fired ({len(fired):,}, "
                     f"{len(fired) / len(rows):.0%} of pickups, "
                     f"{len({t['customer_id'] for t in fired})} customers)\n")
            w.append(table(fired, [PROD] + [label(g, n) for n, _, _ in RECENT], rng) + "\n")
    w.append("## Seasonal growth without the double count (strict seasonal-open)\n")
    for period in ("choose", "confirm"):
        rows = [t for t in tests if t["period"] == period and t["strict"]
                and SEASONAL_NOOVERLAP in t["rates"]]
        w.append(f"### {period} ({len(rows):,} pickups, {len({t['customer_id'] for t in rows})} customers)\n")
        w.append(table(rows, [PROD, SEASONAL_NOOVERLAP, SEASONAL_NOGROWTH, "50/50"], rng) + "\n")
    OUT_REPORT.write_text("\n".join(w) + "\n")
    print(f"wrote {OUT_REPORT.name}: {len(tests):,} pickups")


if __name__ == "__main__":
    main()
