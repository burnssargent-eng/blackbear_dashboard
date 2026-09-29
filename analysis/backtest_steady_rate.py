#!/usr/bin/env python3
"""
Backtest simple oil-rate models on a hand-picked set of steady, high-volume
producers.

The question is narrow: before building accumulation logic, how well does a
naive gallons-per-day rate predict pickup size for customers we already believe
are consistent? Every model here predicts the same way —

    predicted = rate_gpd * (date(x) - date(x-1))

— and they differ only in how rate_gpd is estimated.

Rolling window of the last N pickups (--window N):

    training_gallons = gallons at pickups x-N .. x-1
    training_days    = date(x-1) - date(x-N-1)      (x-N-1 only sets the start)
    rate_gpd         = training_gallons / training_days

    Pickups x-N..x-1 are the oil that accumulated between x-N-1 and x-1, so the
    rate is gallons collected over exactly the span that produced them.

Calendar window around the pickup (--around DAYS):

    Every OTHER pickup dated within DAYS either side of x contributes its own
    interval rate, gallons / days since its previous pickup.
        --rate mean     plain average of those rates
        --rate pooled   their total gallons / total days (time-weighted)

    THIS USES PICKUPS AFTER x. It is not a forecast — at prediction time those
    pickups do not exist. It is a benchmark: how well rate * gap can do given
    the true local production rate. x's own interval is excluded, because its
    rate is gallons(x) / gap, which would hand the model the answer.

Previous year (--prev-year):

    x-j              = the latest pickup at least 365 days before date(x)
    training_gallons = gallons at pickups x-j+1 .. x-1
    training_days    = date(x-1) - date(x-j)
    rate_gpd         = training_gallons / training_days

    The same structure as the rolling window, but the window is set by the
    calendar instead of a pickup count, so it always spans a full year and
    averages every season together.

Test pickups are drawn from 2023-01-01 onward so results reflect current
practice; earlier pickups may serve as history.

Reads oil_collections_raw.csv, which already holds only qualifying pickups:
EMPTY_QTYS {0,1,2,3} were removed upstream by oil_scraper.py and 4-gallon
records are retained as 4. This script ASSERTS that rather than re-filtering,
so it can never quietly diverge from the production cleaning rule.

All percentage fields are written in percent units: 23.4 means 23.4%.

    python3 analysis/backtest_steady_rate.py                              # N=6, 30 samples
    python3 analysis/backtest_steady_rate.py --window 3 --sample 50
    python3 analysis/backtest_steady_rate.py --around 45 --rate mean --sample 50
    python3 analysis/backtest_steady_rate.py --prev-year --sample all     # every eligible test
    python3 analysis/backtest_steady_rate.py ... --full                   # also every eligible test

Outputs are named for their settings, e.g. backtest_steady_rate_w3_n50_summary.csv,
backtest_steady_rate_around45_mean_n50_summary.csv or
backtest_steady_rate_prevyear_all_summary.csv, so runs never overwrite each
other. Analysis only; writes nothing outside analysis/.
"""

import argparse
import csv
import math
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "oil_collections_raw.csv"

# Hand-picked steady producers, pinned by customer_id rather than name: names in
# this dataset are inconsistent and ids cannot silently match two records. Each
# was matched to exactly one id. The name here is only a cross-check.
SAMPLE = {
    325: "Farmhouse",
    261: "Piecasso",
    247: "Doc Ponds",
    262: "Idletyme",
    229: "Prohibition Pig",
    112: "Mulligans-Barre",
    714: "Two Brothers Tavern",
    1070: "Burlington Beer Co",
    267: "Von Trapp Bierhall & Taproom",
    130: "Three Penny Taproom 67%",
    326: "Leunigs Bistro",
}

TEST_FROM = date(2023, 1, 1)
SEED = 42

# Every test pickup must have at least this many prior pickups, whatever the
# model. For the sampled customers every 2023+ pickup already has 31 or more,
# so all models are scored on the same pickups and are directly comparable.
MIN_PRIOR = 8
MAX_WINDOW = MIN_PRIOR - 1

# --prev-year looks back at least this many days, a fixed 365 regardless of
# leap years.
YEAR_DAYS = 365

# Mirrors oil_scraper.py. Asserted against the input, never applied here.
EMPTY_QTYS = {0, 1, 2, 3}


# ─────────────────────────────────────────────
# Load
# ─────────────────────────────────────────────

def load_pickups(ids=SAMPLE):
    """customer_id -> pickups in date order, for `ids` (every customer if None)."""
    by_customer = defaultdict(list)

    with open(SOURCE, newline="") as f:
        for row in csv.DictReader(f):
            cid = int(row["customer_id"])
            if ids is not None and cid not in ids:
                continue

            gallons = int(row["gallons"])
            if gallons in EMPTY_QTYS:
                raise SystemExit(
                    f"customer {cid} has a {gallons}-gallon record on {row['date']}: "
                    "EMPTY_QTYS should already be stripped from the CSV. Refusing to "
                    "run rather than guess at the cleaning rule."
                )

            by_customer[cid].append({
                "date": date.fromisoformat(row["date"][:10]),
                "gallons": gallons,
                "name": row["name"],
                "town": row["geo_town"] or row["city"],
            })

    # Stable sort, so same-day pickups keep their file order.
    for pickups in by_customer.values():
        pickups.sort(key=lambda p: p["date"])

    missing = sorted(set(ids or ()) - set(by_customer))
    if missing:
        raise SystemExit(f"No pickups found for customer_id(s): {missing}")

    return by_customer


# ─────────────────────────────────────────────
# Rate estimators
#
# Each returns (rate, detail) for test pickup index k, or (None, reason) when no
# rate can be formed. `detail` fills the training columns of the output.
# ─────────────────────────────────────────────

def rate_rolling(pickups, k, window):
    start = pickups[k - window - 1]      # x-N-1: defines the window start
    prior = pickups[k - window:k]        # x-N .. x-1: the gallons
    end = pickups[k - 1]                 # x-1

    gallons = sum(p["gallons"] for p in prior)
    days = (end["date"] - start["date"]).days
    if days <= 0:
        return None, "zero-day training span"

    return gallons / days, {
        "training_start": start["date"],
        "training_end": end["date"],
        "training_gallons": gallons,
        "training_days": days,
        "window_pickups": window,
        "window_dates": [p["date"] for p in pickups[k - window - 1:k]],
    }


def rate_around(pickups, k, half_width, how):
    x = pickups[k]
    lo = x["date"] - timedelta(days=half_width)
    hi = x["date"] + timedelta(days=half_width)

    rates, gallons, days, dates = [], 0, 0, []
    for j in range(1, len(pickups)):
        if j == k:
            continue                     # x's own rate would leak the answer
        p = pickups[j]
        if not lo <= p["date"] <= hi:
            continue
        gap = (p["date"] - pickups[j - 1]["date"]).days
        if gap <= 0:
            continue                     # a same-day pickup has no rate of its own
        rates.append(p["gallons"] / gap)
        gallons += p["gallons"]
        days += gap
        dates.append(p["date"])

    if not rates:
        return None, "no other pickups in window"

    rate = statistics.fmean(rates) if how == "mean" else gallons / days
    return rate, {
        "training_start": lo,
        "training_end": hi,
        "training_gallons": gallons,
        "training_days": days,
        "window_pickups": len(rates),
        "window_dates": dates,
    }


def rate_prev_year(pickups, k, lookback):
    cutoff = pickups[k]["date"] - timedelta(days=lookback)

    # x-j: the latest pickup on or before the cutoff — closest to a year before
    # x without falling inside that year.
    i = next((i for i in range(k - 1, -1, -1) if pickups[i]["date"] <= cutoff), None)
    if i is None:
        return None, f"no pickup {lookback}+ days before"
    if i == k - 1:
        return None, f"x-1 is itself {lookback}+ days before x"

    start = pickups[i]                   # x-j: defines the window start
    prior = pickups[i + 1:k]             # x-j+1 .. x-1: the gallons
    end = pickups[k - 1]                 # x-1

    # end falls after the cutoff and start on or before it, so days > 0.
    gallons = sum(p["gallons"] for p in prior)
    days = (end["date"] - start["date"]).days

    return gallons / days, {
        "training_start": start["date"],
        "training_end": end["date"],
        "training_gallons": gallons,
        "training_days": days,
        "window_pickups": len(prior),
        "window_dates": [p["date"] for p in pickups[i:k]],
    }


# ─────────────────────────────────────────────
# Model
# ─────────────────────────────────────────────

def eligible_tests(cid, pickups, model):
    """
    Every eligible test pickup for one customer, in date order, plus a tally of
    pickups dropped because no rate could be formed.
    """
    tests, dropped = [], defaultdict(int)

    for k in range(MIN_PRIOR, len(pickups)):
        x = pickups[k]
        if x["date"] < TEST_FROM or x["gallons"] <= 0:
            continue

        if model["kind"] == "window":
            rate, info = rate_rolling(pickups, k, model["n"])
        elif model["kind"] == "year":
            rate, info = rate_prev_year(pickups, k, model["days"])
        else:
            rate, info = rate_around(pickups, k, model["days"], model["rate"])
        if rate is None:
            dropped[info] += 1
            continue

        end = pickups[k - 1]
        days_since_previous = (x["date"] - end["date"]).days
        predicted = rate * days_since_previous
        actual = x["gallons"]
        signed = predicted - actual

        tests.append({
            "customer_id": cid,
            "name": x["name"],
            "town": x["town"],
            "test_pickup_date": x["date"].isoformat(),
            "actual_gallons": actual,
            "predicted_gallons": round(predicted, 1),
            "absolute_error": round(abs(signed), 1),
            "signed_error": round(signed, 1),
            "absolute_percentage_error": round(abs(signed) / actual * 100, 2),
            "days_since_previous": days_since_previous,
            "training_start_pickup_date": info["training_start"].isoformat(),
            "training_end_pickup_date": info["training_end"].isoformat(),
            "training_gallons": info["training_gallons"],
            "training_days": info["training_days"],
            "preceding_rate_gpd": round(rate, 3),
            "window_pickups": info["window_pickups"],
            "prior_pickup_count": k,
            "uses_future_pickups": model["kind"] == "around",
            "window_touches_2020": any(d.year == 2020 for d in info["window_dates"]),
            # Unrounded, for metrics only; not written out.
            "_signed": signed,
        })

    return tests, dropped


def sample_tests(tests, size):
    """
    Up to `size` tests, drawn with a fresh RNG seeded 42 per customer. A size of
    None keeps every test.

    A fresh generator per customer keeps each customer's sample independent of
    list order, so adding or reordering customers never changes another's draw.
    """
    if size is None or len(tests) <= size:
        return list(tests)
    picked = random.Random(SEED).sample(tests, size)
    return sorted(picked, key=lambda t: t["test_pickup_date"])


# ─────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────

def metrics(tests):
    """Error metrics over a set of tests. Percentages are in percent units."""
    if not tests:
        return None

    actual = [t["actual_gallons"] for t in tests]
    signed = [t["_signed"] for t in tests]
    abs_err = [abs(s) for s in signed]
    ape = [abs(s) / a * 100 for s, a in zip(signed, actual)]
    mean_actual = statistics.fmean(actual)

    return {
        "n": len(tests),
        "mean_actual": mean_actual,
        "mean_predicted": statistics.fmean(a + s for a, s in zip(actual, signed)),
        "mae": statistics.fmean(abs_err),
        "median_ae": statistics.median(abs_err),
        "mape": statistics.fmean(ape),
        "median_ape": statistics.median(ape),
        "bias": statistics.fmean(signed),
        "bias_pct": statistics.fmean(signed) / mean_actual * 100,
        "rmse": math.sqrt(statistics.fmean(s * s for s in signed)),
        # Weighted APE: total error over total volume. Unlike MAPE it is not
        # dragged up by a handful of unusually small pickups.
        "wape": sum(abs_err) / sum(actual) * 100,
    }


# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────

DETAIL_FIELDS = [
    "customer_id", "name", "town", "test_pickup_date", "actual_gallons",
    "predicted_gallons", "absolute_error", "signed_error",
    "absolute_percentage_error", "days_since_previous",
    "training_start_pickup_date", "training_end_pickup_date",
    "training_gallons", "training_days", "preceding_rate_gpd",
    "window_pickups", "prior_pickup_count", "uses_future_pickups",
    "window_touches_2020",
]

SUMMARY_FIELDS = [
    "customer_id", "name", "town", "total_qualifying_pickups",
    "eligible_test_pickups_from_2023", "sampled_tests",
    "mean_actual_gallons", "mean_predicted_gallons", "MAE_gallons",
    "median_AE_gallons", "MAPE", "median_APE", "bias_gallons", "bias_percent",
    "RMSE_gallons", "WAPE",
    # The same model over every eligible test, not just the sample, so a
    # reader can see whether the random draw is representative.
    "full_MAPE", "full_median_APE", "full_WAPE", "full_MAE_gallons",
    "full_bias_percent",
]


def write_csv(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def summary_row(cid, pickups, eligible, sampled):
    m, full = metrics(sampled), metrics(eligible)
    r1 = lambda v: round(v, 1)
    r2 = lambda v: round(v, 2)
    return {
        "customer_id": cid,
        "name": pickups[-1]["name"],
        "town": pickups[-1]["town"],
        "total_qualifying_pickups": len(pickups),
        "eligible_test_pickups_from_2023": len(eligible),
        "sampled_tests": len(sampled),
        "mean_actual_gallons": r1(m["mean_actual"]),
        "mean_predicted_gallons": r1(m["mean_predicted"]),
        "MAE_gallons": r1(m["mae"]),
        "median_AE_gallons": r1(m["median_ae"]),
        "MAPE": r2(m["mape"]),
        "median_APE": r2(m["median_ape"]),
        "bias_gallons": r1(m["bias"]),
        "bias_percent": r2(m["bias_pct"]),
        "RMSE_gallons": r1(m["rmse"]),
        "WAPE": r2(m["wape"]),
        "full_MAPE": r2(full["mape"]),
        "full_median_APE": r2(full["median_ape"]),
        "full_WAPE": r2(full["wape"]),
        "full_MAE_gallons": r1(full["mae"]),
        "full_bias_percent": r2(full["bias_pct"]),
    }


def describe(model):
    if model["kind"] == "around":
        how = ("plain mean of their rates" if model["rate"] == "mean"
               else "their total gallons ÷ total days")
        return (f"±{model['days']} days around the pickup — every other pickup in "
                f"that window, {how}. USES FUTURE PICKUPS: a benchmark, not a forecast")
    if model["kind"] == "year":
        return (f"previous year — gallons at x-j+1..x-1 over days from x-j to x-1, "
                f"where x-j is the latest pickup at least {model['days']} days before x")
    n = model["n"]
    if n == 1:
        return "last interval only — gallons at x-1 over days from x-2 to x-1"
    return (f"last {n} pickups — gallons at x-{n}..x-1 over days "
            f"from x-{n + 1} to x-1")


def size_tag(size):
    return "all" if size is None else f"n{size}"


def size_text(size):
    return "every eligible test per customer" if size is None else f"up to {size} samples per customer"


def stem_for(model, size):
    if model["kind"] == "around":
        return f"backtest_steady_rate_around{model['days']}_{model['rate']}_{size_tag(size)}"
    if model["kind"] == "year":
        return f"backtest_steady_rate_prevyear_{size_tag(size)}"
    return f"backtest_steady_rate_w{model['n']}_{size_tag(size)}"


def print_table(summary, pooled, pooled_full, model, size):
    rows = sorted(summary, key=lambda r: r["MAPE"])
    print(f"\nModel: {describe(model)}.  {size_text(size).capitalize()}.")
    head = (f"{'CUSTOMER':30}{'n':>4}{'ACTUAL':>8}{'MAE':>7}{'MedAE':>7}"
            f"{'MAPE':>8}{'MedAPE':>8}{'WAPE':>7}{'BIAS%':>8}{'fullMAPE':>10}")
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['name'][:29]:30}{r['sampled_tests']:>4}{r['mean_actual_gallons']:>8.1f}"
              f"{r['MAE_gallons']:>7.1f}{r['median_AE_gallons']:>7.1f}{r['MAPE']:>7.1f}%"
              f"{r['median_APE']:>7.1f}%{r['WAPE']:>6.1f}%{r['bias_percent']:>+7.1f}%"
              f"{r['full_MAPE']:>9.1f}%")
    print("-" * len(head))
    print(f"{'POOLED (all sampled tests)':30}{pooled['n']:>4}{pooled['mean_actual']:>8.1f}"
          f"{pooled['mae']:>7.1f}{pooled['median_ae']:>7.1f}{pooled['mape']:>7.1f}%"
          f"{pooled['median_ape']:>7.1f}%{pooled['wape']:>6.1f}%{pooled['bias_pct']:>+7.1f}%"
          f"{pooled_full['mape']:>9.1f}%")


def fmt_table(summary):
    lines = [
        "| Customer | Town | Eligible | n | Mean actual | MAE | Median AE | MAPE | Median APE | WAPE | Bias | Full MAPE |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(summary, key=lambda r: r["MAPE"]):
        lines.append(
            f"| {r['name']} | {r['town']} | {r['eligible_test_pickups_from_2023']} | "
            f"{r['sampled_tests']} | {r['mean_actual_gallons']:.1f} | {r['MAE_gallons']:.1f} | "
            f"{r['median_AE_gallons']:.1f} | {r['MAPE']:.1f}% | {r['median_APE']:.1f}% | "
            f"{r['WAPE']:.1f}% | {r['bias_percent']:+.1f}% | {r['full_MAPE']:.1f}% |"
        )
    return "\n".join(lines)


def method_text(model):
    if model["kind"] == "around":
        rate_line = (
            "- **rate** = plain mean of those rates" if model["rate"] == "mean"
            else "- **rate** = their total gallons ÷ total days (time-weighted)"
        )
        return (
            f"- every **other** pickup dated within {model['days']} days either side of "
            "*x* contributes its own rate: gallons ÷ days since its previous pickup\n"
            f"{rate_line}\n"
            "- *x*'s own interval is excluded — its rate is gallons(*x*) ÷ gap, "
            "which would hand the model the answer\n"
            "- **prediction** = rate × (date(*x*) − date(*x-1*))"
        )
    if model["kind"] == "year":
        return (
            f"- ***x-j*** = the latest pickup at least {model['days']} days before date(*x*)\n"
            "- **training gallons** = gallons at *x-j+1* … *x-1*\n"
            "- **training days** = date(*x-1*) − date(*x-j*)\n"
            "- **rate** = training gallons ÷ training days\n"
            "- **prediction** = rate × (date(*x*) − date(*x-1*))"
        )
    n = model["n"]
    if n == 1:
        return (
            "- **rate** = gallons at *x-1* ÷ (date(*x-1*) − date(*x-2*))\n"
            "- **prediction** = rate × (date(*x*) − date(*x-1*))"
        )
    return (
        f"- **training gallons** = gallons at *x-{n}* … *x-1*\n"
        f"- **training days** = date(*x-1*) − date(*x-{n + 1}*)\n"
        "- **rate** = training gallons ÷ training days\n"
        "- **prediction** = rate × (date(*x*) − date(*x-1*))"
    )


def model_caveat(model):
    if model["kind"] == "around":
        return (
            "- **Not a forecast.** This window reaches forward in time, so the rate "
            "uses pickups that have not happened when *x* is being predicted. Read "
            "it as the error that remains even with the true local production rate "
            "— the noise floor any real model is measured against."
        )
    if model["kind"] == "year":
        return (
            "- **Seasonally neutral.** The window spans a full year, so it averages "
            "every season together: expect it to under-predict peak months, "
            "over-predict slow ones, and trail any year-over-year growth or decline."
        )
    if model["n"] == 1:
        return (
            "- **No smoothing.** A one-interval rate reacts to change immediately — "
            "no seasonal lag — but inherits every quirk of a single pickup."
        )
    return (
        f"- **Seasonal lag.** A {model['n']}-pickup window reaches back weeks to "
        "months, so it trails any seasonal swing until the window catches up."
    )


def write_report(path, summary, pooled, pooled_full, all_sampled, model, size, dropped):
    customer_mapes = [r["MAPE"] for r in summary]
    customer_median_apes = [r["median_APE"] for r in summary]
    by_mape = sorted(summary, key=lambda r: r["MAPE"])
    thin = [] if size is None else [r for r in summary if r["sampled_tests"] < size]
    thin_line = (
        "- **Tests per customer:** " + ", ".join(
            f"{r['name']} {r['sampled_tests']}" for r in sorted(summary, key=lambda r: r["name"]))
        if size is None else
        f"- **Customers with fewer than {size} eligible tests:** "
        + (", ".join(f"{r['name']} ({r['sampled_tests']})" for r in thin) if thin else "none")
    ) + "."
    touches_2020 = sum(1 for t in all_sampled if t["window_touches_2020"])
    zero_gap = sum(1 for t in all_sampled if t["days_since_previous"] == 0)
    over = sum(1 for t in all_sampled if t["_signed"] > 0)
    worst = sorted(all_sampled, key=lambda t: -t["absolute_percentage_error"])[:5]
    dropped_text = ", ".join(f"{n} ({why})" for why, n in dropped.items()) or "none"
    if model["kind"] == "around":
        flags = f"--around {model['days']} --rate {model['rate']}"
    elif model["kind"] == "year":
        flags = "--prev-year"
    else:
        flags = f"--window {model['n']}"
    sampling = (
        "Every eligible test is scored — no sampling — so the sampled and \"Full\"\n"
        "columns are the same."
        if size is None else
        f"Up to {size} tests per customer are drawn at random (seed {SEED}, a fresh\n"
        "generator per customer). \"Full\" columns rerun the model over every eligible test."
    )

    text = f"""# Steady-rate backtest — {describe(model)}

Generated by `analysis/backtest_steady_rate.py {flags} --sample {'all' if size is None else size}`.
Percentages are in percent units.

## Method

For each test pickup *x*:

{method_text(model)}

Test pickups are from {TEST_FROM.isoformat()} onward. Every test pickup has at least
{MIN_PRIOR} prior pickups, so all models are scored on the same pickups.
{sampling}

Input is `oil_collections_raw.csv`, which holds only qualifying pickups:
`EMPTY_QTYS` {{0,1,2,3}} are removed upstream and 4-gallon records count as 4.

## Results by customer

Sorted by MAPE, best first.

{fmt_table(summary)}

## Aggregate

| Measure | Sampled | Full eligible |
|---|---:|---:|
| Tests | {pooled['n']} | {pooled_full['n']} |
| Pooled MAPE | {pooled['mape']:.1f}% | {pooled_full['mape']:.1f}% |
| Pooled median APE | {pooled['median_ape']:.1f}% | {pooled_full['median_ape']:.1f}% |
| WAPE (total error ÷ total volume) | {pooled['wape']:.1f}% | {pooled_full['wape']:.1f}% |
| MAE | {pooled['mae']:.1f} gal | {pooled_full['mae']:.1f} gal |
| Bias | {pooled['bias_pct']:+.1f}% | {pooled_full['bias_pct']:+.1f}% |
| Mean of customer MAPEs (unweighted) | {statistics.fmean(customer_mapes):.1f}% | — |
| Median of customer median APEs | {statistics.median(customer_median_apes):.1f}% | — |

The model over-predicted on {over} of {pooled['n']} sampled tests.

## Notes

- **Best:** {by_mape[0]['name']} at {by_mape[0]['MAPE']:.1f}% MAPE.
  **Worst:** {by_mape[-1]['name']} at {by_mape[-1]['MAPE']:.1f}% MAPE.
{thin_line}
- **Pickups dropped because no rate could be formed:** {dropped_text}.
- **Windows touching 2020:** {touches_2020} of {pooled['n']} sampled tests.
- **Same-day pickups (zero-day interval, predicts 0):** {zero_gap} of {pooled['n']} sampled tests.
- **Largest percentage misses in the sample:**
{chr(10).join(f"  - {t['name']} {t['test_pickup_date']}: predicted {t['predicted_gallons']:.0f}, actual {t['actual_gallons']} ({t['absolute_percentage_error']:.0f}% APE, {t['days_since_previous']}-day gap)" for t in worst)}

## Caveats

{model_caveat(model)}
- **MAPE punishes small pickups.** WAPE and median APE are the steadier reads
  when a customer has occasional small pickups.
- **Pickup size is not production.** A pickup reflects how full the container
  was when the driver arrived, which depends on the route schedule as much as
  on how much oil the customer produced.
- **Hand-picked sample.** These customers were chosen because they look steady.
  Results are a best case, not an estimate for the customer base as a whole.
"""
    path.write_text(text)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def sample_size(value):
    """argparse type for --sample: a positive integer, or 'all' (None)."""
    if value == "all":
        return None
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer or 'all'")
    return n


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--window", type=int, default=6,
                        help=f"rolling window of the last N pickups, 1-{MAX_WINDOW} (default 6)")
    parser.add_argument("--around", type=int, metavar="DAYS",
                        help="use a calendar window of DAYS either side of the pickup "
                             "instead of a rolling window (uses future pickups)")
    parser.add_argument("--rate", choices=("mean", "pooled"), default="mean",
                        help="with --around: average the rates, or total gallons / total days")
    parser.add_argument("--prev-year", action="store_true",
                        help=f"rate over the span back to the latest pickup at least "
                             f"{YEAR_DAYS} days before the test pickup")
    parser.add_argument("--sample", type=sample_size, default=30,
                        help="tests sampled per customer, or 'all' (default 30)")
    parser.add_argument("--full", action="store_true",
                        help="also write every eligible test to a separate CSV")
    args = parser.parse_args()

    if args.prev_year and args.around is not None:
        raise SystemExit("--prev-year and --around are separate models; pick one")
    if args.prev_year:
        model = {"kind": "year", "days": YEAR_DAYS}
    elif args.around is not None:
        if args.around <= 0:
            raise SystemExit("--around must be a positive number of days")
        model = {"kind": "around", "days": args.around, "rate": args.rate}
    else:
        if not 1 <= args.window <= MAX_WINDOW:
            raise SystemExit(f"--window must be between 1 and {MAX_WINDOW}")
        model = {"kind": "window", "n": args.window}

    pickups = load_pickups()

    summary, all_sampled, all_eligible = [], [], []
    dropped = defaultdict(int)
    for cid in SAMPLE:
        stored = pickups[cid][-1]["name"]
        if stored != SAMPLE[cid]:
            print(f"  note: customer {cid} is stored as {stored!r}, expected {SAMPLE[cid]!r}")

        eligible, lost = eligible_tests(cid, pickups[cid], model)
        for why, n in lost.items():
            dropped[why] += n
        if not eligible:
            print(f"  WARNING: {SAMPLE[cid]} ({cid}) has no eligible tests — skipped")
            continue

        sampled = sample_tests(eligible, args.sample)
        all_eligible.extend(eligible)
        all_sampled.extend(sampled)
        summary.append(summary_row(cid, pickups[cid], eligible, sampled))

    pooled, pooled_full = metrics(all_sampled), metrics(all_eligible)

    stem = stem_for(model, args.sample)
    write_csv(HERE / f"{stem}_detail.csv", DETAIL_FIELDS, all_sampled)
    write_csv(HERE / f"{stem}_summary.csv", SUMMARY_FIELDS, summary)
    write_report(HERE / f"{stem}_report.md", summary, pooled, pooled_full,
                 all_sampled, model, args.sample, dropped)
    if args.full:
        write_csv(HERE / f"{stem}_detail_full.csv", DETAIL_FIELDS, all_eligible)

    print_table(summary, pooled, pooled_full, model, args.sample)
    for why, n in dropped.items():
        print(f"\n  {n} pickup(s) dropped: {why}")
    print(f"\nWrote analysis/{stem}_*")


if __name__ == "__main__":
    main()
