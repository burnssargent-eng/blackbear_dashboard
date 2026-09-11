# Oil-rate backtest

Analysis only. Nothing on the site reads this folder.

**The question:** before building any accumulation or fullness projection, how
well does a plain gallons-per-day rate predict the size of the next pickup, for
customers we already believe are steady?

Every model predicts the same way —

```
predicted gallons at x = rate × (date(x) − date(x-1))
```

— and they differ only in how the rate is estimated.

## Where it stands (2026-09-11)

**Last 6 pickups** was the first pick —

```
rate = gallons at x-6 … x-1  ÷  days from x-7 to x-1
```

— chosen from a flat field: windows of 3 to 7 pickups all score 22.9–23.0%
WAPE, the previous-year rate ties at 22.8%, and 1 or 2 pickups are worse.

Later rounds, [below](#seasonal-customers-and-the-routing-rule), point to
routing each customer to a rate by its seasonality:

| Customer | Rate | Evidence |
|---|---|---|
| Default | **50/50 last 6 + previous year** | −0.8 points WAPE vs last 6 on the steady 11 (interval −1.3 to −0.3). Beats the seasonal blend on 90 of 103 held-out customers. |
| Repeatable seasonal pattern (repeatability ≥ 0.6) | **seasonal blend** | −13.8 points vs last 6 on 6 seasonal customers. On 7 held-out ones, −1.6 vs 50/50 with an interval of −5.7 to +1.8: inconclusive. |
| Closes for part of the year | **unsolved** | No rate works across a closure (79–185% WAPE). Needs a reopening rule. |

Not yet adopted: the seasonal branch is unproven out of sample, and its
threshold is not settled.

## Results

Every eligible pickup from 2023 on, scored by every model — 962 pickups, no
sampling. Full breakdown in [`model_comparison.md`](model_comparison.md).

| Model | Median APE | WAPE | MAPE | MAE (gal) | Bias |
|---|---:|---:|---:|---:|---:|
| last 1 | 20.6% | 29.3% | 37.4% | 32.8 | +3.8% |
| last 2 | 18.0% | 23.5% | 29.9% | 26.3 | +0.9% |
| last 3 | 18.5% | 22.9% | 29.3% | 25.6 | +0.4% |
| last 4 | 18.2% | 22.9% | 29.2% | 25.6 | +0.3% |
| last 5 | 18.0% | 22.9% | 29.1% | 25.6 | +0.2% |
| **last 6** | 17.7% | 22.9% | 29.3% | 25.6 | −0.0% |
| last 7 | 18.0% | 23.0% | 29.4% | 25.7 | −0.1% |
| prev year | 16.7% | 22.8% | 30.1% | 25.5 | −0.1% |

- **WAPE** is total error ÷ total gallons — the steadiest single number here.
- **Median APE** is the typical miss on one pickup.
- **MAPE** is dragged up by a few small pickups, where a 20-gallon miss on a
  15-gallon pickup reads as 133%.

**The noise floor.** A benchmark that is allowed to see pickups *after* the
one being predicted — every pickup within ±45 days, total gallons ÷ total days —
reaches only 16.0% median APE and 21.7% WAPE (on 959 pickups). Even with the
true local rate the error barely moves, so most of what remains comes from the
pickup itself — how full the container happened to be when the truck arrived
— not from how the rate is estimated. A cleverer window will not fix it.

**Averaging rates is the wrong way to combine them.** The same ±45-day window
as a plain mean of each interval's rate scores 24.6% WAPE with +6.0% bias:
back-to-back pickups produce extreme one-interval rates that pull a plain mean
up. Rates here are always total gallons ÷ total days.

## What last 6 gets wrong

- **Seasonal turns.** It trails the season by a few weeks: pooled bias runs
  +13% in May, +14% in November, +15% in December and −10% in July (by-month
  table in `model_comparison.md`). The Stowe customers drive most of it.
- **Long gaps.** When a pickup comes much later than usual the prediction
  scales with the gap but the container does not. The largest misses in every
  report are long-gap pickups.
- **New customers.** It needs 7 prior pickups; a customer with fewer needs a
  fallback.

## Where the previous-year rate differs

- **Better** for Mulligans-Barre (18.4% vs 21.0% WAPE) and Piecasso (22.5% vs
  24.9%), with intervals that exclude zero — Piecasso only just.
- **Leans worse** for Three Penny and Leunigs (about +4 points), with intervals
  that only just include zero.
- **Misses in different months.** September–October bias is about −8% for the
  previous-year rate against +1% for last 6. Because the two go wrong at
  different times, a blend of the two may beat either. Untested.

## Seasonal customers and the routing rule

**Seasonality score** (`seasonality_score.py` → `seasonality_scores.md`). Each
customer's production by month over 2023–25, spreading each pickup's gallons
over the days since the previous pickup:

- **amplitude** — how far a typical month sits from the annual average
- **repeatability** — whether the same months are high every year

Seasonal means amplitude ≥ 0.25 and repeatability ≥ 0.6. Of the 59 customers
averaging 1,000+ gallons a year, 10 are seasonal, 4 erratic (big swings that do
not repeat) and 45 steady. Most of the hand-picked steady 11 score at the very
bottom.

**Four models** (`backtest_seasonal_models.py` → `seasonal_models.md`), WAPE on
every pickup, all four scored on the same pickups:

| Group | last 6 | 50/50 | seasonal blend | ratio |
|---|---:|---:|---:|---:|
| Steady (11 customers) | 22.9% | **22.1%** | 22.2% | 25.7% |
| Seasonal, open all year (6) | 49.7% | 46.5% | **36.0%** | 37.5% |
| Closes seasonally (3) | 185% | 166% | **79%** | 166% |

- **50/50** — the average of last 6 and the previous-year rate.
- **Seasonal blend** — equal weight on the recent rate (the last *n* pickups,
  *n* = pickups in the last 60 days, at most 6, at least 3) and last year's
  rate around the same date (the 3 pickups either side of date − 365). With
  fewer than 3 recent pickups it uses last year's rate alone, which is the
  usual case for customers collected every 3–6 weeks.
- **Ratio** — last 6 scaled by last year's seasonal change. Dropped: dividing
  one noisy rate by another amplifies the noise.

**Out-of-sample test** (`test_routing_rule.py` → `routing_rule_test.md`). The
rule was fixed first, then scored on 117 customers averaging 500+ gallons a
year that played no part in finding it:

| Approach — 110 customers, 4,925 pickups | WAPE | Rule minus this (95% interval) |
|---|---:|---|
| routing rule | 38.1% | — |
| last 6 for everyone | 38.7% | −0.6 (−1.3 to +0.1) |
| 50/50 for everyone | 38.2% | −0.1 (−0.3 to +0.1) |
| seasonal blend for everyone | 44.6% | −6.5 (−10.5 to −3.9) |

- **The 50/50 branch is right.** 90 of 103 customers do better on 50/50 than on
  the blend.
- **The blend branch is unproven.** 7 customers, 4 better on the blend. The
  three most repeatable (0.78–0.88) gain 6–12 points over 50/50; the two just
  over 0.6 lose 6–8. Raising the threshold on this evidence would fit it to the
  holdout, so it stays at 0.6 until more seasonal customers can be tested.
- **So far the rule is roughly 50/50 for everyone.** Only 5% of held-out
  pickups route to the blend.
- **Closers** (7 held out) sit at 66–138% WAPE on every model.
- Holdout WAPE runs higher across the board because these are smaller
  customers.

**Caveat:** a customer's score is computed from the same years its pickups are
scored on. A production version would score from history before each
prediction.

## Scope and data rules

- **11 hand-picked customers**, pinned by `customer_id` in `SAMPLE` at the top of
  `backtest_steady_rate.py`. Chosen because they look steady, so these results
  are a best case, not an estimate for the whole customer base.
- **Test pickups** are 2023-01-01 onward, each with at least 8 prior pickups.
- **Input** is `oil_collections_raw.csv`, which holds only qualifying pickups.
  The script *asserts* that `EMPTY_QTYS` {0,1,2,3} are absent rather than
  re-filtering, and 4-gallon records count as 4.

## Files

| File | What it is |
|---|---|
| `backtest_steady_rate.py` | One model per run. Writes a report and CSVs named for its settings |
| `compare_models.py` | Every model on the same pickups, with a paired bootstrap for last 6 vs previous year. Writes `model_comparison.md` |
| `model_comparison.md` | The side-by-side tables the decision rests on |
| `backtest_steady_rate_*_report.md` | One report per run: method, per-customer table, largest misses |
| `seasonality_score.py` → `seasonality_scores.md` | Seasonality score and class for the 59 customers averaging 1,000+ gallons a year |
| `backtest_seasonal_models.py` → `seasonal_models.md` | Last 6, 50/50, seasonal blend and ratio on the steady, seasonal and closing groups |
| `test_routing_rule.py` → `routing_rule_test.md` | The routing rule scored on 117 held-out customers |

The CSVs (per-pickup detail and summaries) are gitignored. Every script is
deterministic, so rerunning regenerates them byte for byte.

## Rerun

Standard library only; run from the repo root.

```
python3 analysis/compare_models.py                                      # the comparison
python3 analysis/backtest_steady_rate.py --window 6 --sample all        # last 6, every pickup
python3 analysis/backtest_steady_rate.py --prev-year --sample all       # previous year
python3 analysis/backtest_steady_rate.py --window 3 --sample 50 --full  # any window, sampled
python3 analysis/backtest_steady_rate.py --around 45 --rate pooled --sample 50   # benchmark
python3 analysis/seasonality_score.py                                   # seasonality scores
python3 analysis/backtest_seasonal_models.py                            # four models by group
python3 analysis/test_routing_rule.py                                   # held-out rule test
```

The nightly scrape adds pickups, so figures will drift slightly from those
above on a later rerun.

**Known quirk, benchmark only:** the ±45-day window skips a pickup made the same
day as the one before it, gallons included. One instance in scope (Doc Ponds,
2024-04-19). Left as is, since that window is not a candidate model.
