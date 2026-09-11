#!/usr/bin/env python3
"""
Seasonality score for the higher-producing customers.

Production by month
    Each pickup's gallons are spread evenly over the days since the previous
    pickup, so a pickup on 2 June counts mostly toward May. The spread is capped
    at SPREAD_CAP_DAYS: after a long gap the oil is assumed to have accumulated
    in the final stretch, not smeared across a closure. Same-day pickups share
    one interval. Gallons per day are then totalled by calendar month for each
    full past year in SCORE_YEARS.

    Spreading rather than counting by pickup month matters: on a two-week cycle
    a month holds two pickups or three, which alone swings a month by 50%.

Shape
    Each year's 12 monthly rates divided by that year's average month, so 1.0 is
    an average month and 1.3 is 30% above it. Dividing by the year's own average
    removes growth and decline between years, leaving only the pattern.

Amplitude — how big the swing is
    The standard deviation of the average shape across the 12 months. 0.10
    means a typical month sits about 10% off the annual average.

Repeatability — whether the swing is the same every year
    Each year's shape correlated with the average of the other years' shapes,
    averaged over the years. Near 1: the same months are high every year. Near
    0: the swings land in different months each year, which is noise, not a
    season.

A customer is seasonal when the swing is both large and repeatable. A large
swing that does not repeat is an erratic customer, and last year's calendar
will not help predict it.

    python3 analysis/seasonality_score.py

Writes analysis/seasonality_scores.md and seasonality_scores.csv. Analysis only.
"""

import calendar
import csv
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import backtest_steady_rate as bt

HERE = Path(__file__).resolve().parent

# The pool: customers still producing, with enough history for a year-ago
# window on every 2023+ test pickup, and enough volume to be worth modelling.
POOL_YEARS = (2022, 2023, 2024, 2025, 2026)   # at least one pickup in each
SCORE_YEARS = (2023, 2024, 2025)               # full past calendar years
MIN_AVG_GALLONS = 1000                         # per year, over SCORE_YEARS

SPREAD_CAP_DAYS = 60

# Classes, set by eye from the scores rather than fitted. The hand-picked steady
# customers mostly sit at 0.05-0.15 amplitude with repeatability anywhere from
# -0.3 to +0.6, so a swing under 0.25, or one repeating at under 0.6, is not
# distinguishable from noise.
SEASONAL_MIN_AMPLITUDE = 0.25
SEASONAL_MIN_REPEAT = 0.6

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def classify(row):
    if row["amplitude"] < SEASONAL_MIN_AMPLITUDE:
        return "steady"
    return "seasonal" if row["repeatability"] >= SEASONAL_MIN_REPEAT else "erratic"


def high_producers(pickups, min_avg=MIN_AVG_GALLONS):
    """customer_ids with a pickup in every POOL_YEARS year and at least `min_avg`
    gallons a year on average over SCORE_YEARS."""
    pool = []
    for cid, ps in pickups.items():
        gallons = defaultdict(int)
        for p in ps:
            gallons[p["date"].year] += p["gallons"]
        if (all(gallons[y] > 0 for y in POOL_YEARS)
                and statistics.fmean(gallons[y] for y in SCORE_YEARS) >= min_avg):
            pool.append(cid)
    return pool


def daily_production(pickups):
    """date -> gallons produced that day, spreading each pickup over its interval."""
    by_date = defaultdict(int)
    for p in pickups:
        by_date[p["date"]] += p["gallons"]
    dates = sorted(by_date)

    daily = defaultdict(float)
    for prev, cur in zip(dates, dates[1:]):
        span = min((cur - prev).days, SPREAD_CAP_DAYS)
        for i in range(span):
            daily[cur - timedelta(days=i)] += by_date[cur] / span
    return daily


def monthly_rates(daily, year):
    rates = []
    for m in range(1, 13):
        days = calendar.monthrange(year, m)[1]
        rates.append(sum(daily.get(date(year, m, d), 0.0) for d in range(1, days + 1)) / days)
    return rates


def score(pickups):
    daily = daily_production(pickups)
    shapes = {}
    for y in SCORE_YEARS:
        rates = monthly_rates(daily, y)
        mean = statistics.fmean(rates)
        shapes[y] = [r / mean for r in rates]

    shape = [statistics.fmean(shapes[y][m] for y in SCORE_YEARS) for m in range(12)]

    def others(y):
        return [statistics.fmean(shapes[o][m] for o in SCORE_YEARS if o != y) for m in range(12)]

    repeat = statistics.fmean(statistics.correlation(shapes[y], others(y)) for y in SCORE_YEARS)

    gaps = [(b["date"] - a["date"]).days for a, b in zip(pickups, pickups[1:])
            if b["date"].year in SCORE_YEARS and b["date"] > a["date"]]
    peak, trough = max(range(12), key=shape.__getitem__), min(range(12), key=shape.__getitem__)
    return {
        "amplitude": statistics.pstdev(shape),
        "repeatability": repeat,
        "shape": shape,
        "peak_month": MONTHS[peak],
        "peak_index": shape[peak],
        "trough_month": MONTHS[trough],
        "trough_index": shape[trough],
        "median_gap_days": statistics.median(gaps),
        "avg_gallons_per_year": sum(p["gallons"] for p in pickups
                                    if p["date"].year in SCORE_YEARS) / len(SCORE_YEARS),
    }


def scores():
    """Every pool customer's score, most seasonal (largest swing) first."""
    pickups = bt.load_pickups(ids=None)
    rows = []
    for cid in high_producers(pickups):
        ps = pickups[cid]
        rows.append({"customer_id": cid, "name": ps[-1]["name"], "town": ps[-1]["town"],
                     "in_steady_sample": cid in bt.SAMPLE, **score(ps)})
    for r in rows:
        r["class"] = classify(r)
    return sorted(rows, key=lambda r: -r["amplitude"]), pickups


def write_report(rows):
    counts = defaultdict(int)
    for r in rows:
        counts[r["class"]] += 1
    lines = [
        "# Seasonality scores\n",
        "Generated by `analysis/seasonality_score.py`. Method in the script's docstring.\n",
        f"**Pool:** {len(rows)} customers with a pickup in every year "
        f"{POOL_YEARS[0]}–{POOL_YEARS[-1]} and at least {MIN_AVG_GALLONS:,} gallons a year "
        f"on average over {SCORE_YEARS[0]}–{SCORE_YEARS[-1]}.\n",
        "- **Amplitude** — how far a typical month sits from the annual average "
        "(0.10 = about 10%).",
        "- **Repeatability** — whether the same months are high every year "
        "(1 = identical pattern, 0 = no pattern).",
        "- **Peak / trough** — the highest and lowest month of the average shape, "
        "as a multiple of an average month.",
        f"- **Class** — seasonal: amplitude ≥ {SEASONAL_MIN_AMPLITUDE} and repeatability "
        f"≥ {SEASONAL_MIN_REPEAT}; erratic: a large swing that does not repeat; "
        "steady: everything else.\n",
        f"{counts['seasonal']} seasonal, {counts['erratic']} erratic, {counts['steady']} steady. "
        "✓ marks the 11 hand-picked steady customers.\n",
        "| # | Customer | ID | Town | Gal/yr | Typical gap (days) | Amplitude | Repeatability "
        "| Peak | Trough | Class | Steady 11 |",
        "|---:|---|---:|---|---:|---:|---:|---:|---|---|---|:---:|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['name']} | {r['customer_id']} | {r['town']} | "
            f"{r['avg_gallons_per_year']:,.0f} | {r['median_gap_days']:.0f} | "
            f"{r['amplitude']:.2f} | {r['repeatability']:.2f} | "
            f"{r['peak_month']} {r['peak_index']:.2f} | {r['trough_month']} {r['trough_index']:.2f} | "
            f"{r['class']} | {'✓' if r['in_steady_sample'] else ''} |")
    (HERE / "seasonality_scores.md").write_text("\n".join(lines) + "\n")


def main():
    rows, _ = scores()
    write_report(rows)

    fields = ["customer_id", "name", "town", "in_steady_sample", "class", "avg_gallons_per_year",
              "median_gap_days", "amplitude", "repeatability", "peak_month", "peak_index",
              "trough_month", "trough_index"] + MONTHS
    with open(HERE / "seasonality_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, **{m: round(v, 3) for m, v in zip(MONTHS, r["shape"])},
                        "amplitude": round(r["amplitude"], 3),
                        "repeatability": round(r["repeatability"], 3),
                        "peak_index": round(r["peak_index"], 2),
                        "trough_index": round(r["trough_index"], 2),
                        "avg_gallons_per_year": round(r["avg_gallons_per_year"])})

    print(f"{len(rows)} customers in the pool\n")
    print(f"{'#':>3} {'CUSTOMER':32}{'ID':>5} {'TOWN':16}{'GAL/YR':>7}{'GAP':>5}"
          f"{'AMPL':>6}{'REPEAT':>7}  PEAK      TROUGH")
    for i, r in enumerate(rows, 1):
        print(f"{i:>3} {r['name'][:31]:32}{r['customer_id']:>5} {r['town'][:15]:16}"
              f"{r['avg_gallons_per_year']:>7.0f}{r['median_gap_days']:>5.0f}"
              f"{r['amplitude']:>6.2f}{r['repeatability']:>7.2f}  "
              f"{r['peak_month']} {r['peak_index']:.2f}  {r['trough_month']} {r['trough_index']:.2f}"
              f"  {r['class']:9}{'[steady 11]' if r['in_steady_sample'] else ''}")
    print("\nWrote analysis/seasonality_scores.md and seasonality_scores.csv")


if __name__ == "__main__":
    main()
