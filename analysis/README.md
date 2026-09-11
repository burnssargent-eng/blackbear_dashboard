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

## Decision (2026-09-11)

**Last 6 pickups** is the preceding-rate estimate for now:

```
rate = gallons at x-6 … x-1  ÷  days from x-7 to x-1
```

It is a safe default from a flat field, not a clear winner:

- windows of 3 to 7 pickups all score 22.9–23.0% WAPE
- the previous-year rate ties it too: 22.8% against 22.9%, and the bootstrap
  interval on the difference (−1.1 to +1.0 points) straddles zero
- 2 pickups is slightly worse, and 1 pickup is clearly worse

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

The CSVs (per-pickup detail and summaries) are gitignored. Both scripts are
deterministic, so rerunning regenerates them byte for byte.

## Rerun

Standard library only; run from the repo root.

```
python3 analysis/compare_models.py                                      # the comparison
python3 analysis/backtest_steady_rate.py --window 6 --sample all        # last 6, every pickup
python3 analysis/backtest_steady_rate.py --prev-year --sample all       # previous year
python3 analysis/backtest_steady_rate.py --window 3 --sample 50 --full  # any window, sampled
python3 analysis/backtest_steady_rate.py --around 45 --rate pooled --sample 50   # benchmark
```

The nightly scrape adds pickups, so figures will drift slightly from those
above on a later rerun.

**Known quirk, benchmark only:** the ±45-day window skips a pickup made the same
day as the one before it, gallons included. One instance in scope (Doc Ponds,
2024-04-19). Left as is, since that window is not a candidate model.
