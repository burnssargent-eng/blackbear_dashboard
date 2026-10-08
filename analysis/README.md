# Oil-rate modelling and pickup projections

**Start here for any projection, capacity or seasonality work.** This file is
the methodology and the decision history; [`ROADMAP.md`](ROADMAP.md) is what
comes next and what is in flight. The site reads exactly one thing produced from
this folder — `oil_projections.json` at the repo root — and nothing reads the
folder itself.

## The pipeline as it runs today

The nightly (`.github/workflows/nightly-update.yml`, 07:00 UTC) runs steps 1–2
and commits the results to `main`. Everything else is run by hand.

| # | Step | Code | Output |
|---|---|---|---|
| 1 | Scrape pickups, drop `EMPTY_QTYS` {0,1,2,3} from totals but keep them as events; read each page's **Capacity** and **Periodicity** | `oil_scraper.py --rescrape` | `oil_collections_raw.csv`, `oil_collections.json`, `oil_data.json`, `capacity_cache.csv`, `oil_non_pickups.csv` |
| 2 | Build the projection table and the website file | `analysis/build_projection_table.py` | `oil_projections.json` (committed nightly), `analysis/oil_projection_table.md` (tracked, but only committed by hand, so it lags), `.csv` + `_detail.csv` (gitignored) |
| 3 | Page | `projections.html` | displays `oil_projections.json`; never recomputes |
| — | Review tool for seasonal classification (manual) | `analysis/build_fringe_seasonal_candidates.py` | `fringe_seasonal_candidates.md` (+ gitignored csv) |
| — | Rate-model research (manual, rarely rerun) | the backtest scripts, [below](#rate-study-2026-09-11-to-09-15) | the `*_report.md`, `seasonal_models.md`, etc. |

**What step 2 computes, per active customer (`is_active` from the source site):**

```
rate       = by stage (the ladder, since 2026-10-08):
             new          pooled gallons ÷ days over ≤ the last 6 gaps (3+ pickups)
             established  0.5 × previous-year rate + 0.5 × pooled (= last 6 once 7+ pickups)
             seasonal     season-free level × month index (seasonal_open.py passes)
clock      = the last pickup, or a later empty check (qty 0 or 1), whichever is later
projected  = rate × days since the clock started                         (gallons)
target     = 0.75 × listed capacity                                      (the pickup point)
% full     = projected ÷ capacity, capped at 100 for display (raw kept in _detail.csv)
band       = ± the customer's own backtested WAPE (analysis/customer_wape.json) once past
             "new", else projection × RANGE_FACTORS (20th–80th pct, replay_newcomers.md)
```

Before routing, accounts in `SHARED_CONTAINERS` are folded into one row per
barrel (gallons summed by date, the members' common capacity), and accounts
in `HISTORY_STARTS` lose the pickups before their new owner's start.

Then every row is **routed** to one section, in this order:

1. `MODEL_OVERRIDES` (in the builder, keyed by customer id, name asserted) sends
   **on-demand** (Perrigo), **event-driven** (Champlain Valley Expo, Tunbridge
   Fair) and **lump-sum** accounts to their own sections, with no fill estimate.
2. **Will-call** (detected, never for an override, not once stale) — median
   gap over 120 days in the last 3 years, or gap sd/mean over 0.8 once gaps
   over 3× the median are dropped as closures. Listed, no fill estimate.
3. **Insufficient data** — fewer than 3 pickups.
4. **True closers** are held out whenever the month is outside their
   month-level season; **semi-closers** only when off-season *and* silent past
   max(60, 2 × median gap) days. Either is held out as *season open — awaiting
   first pickup* until it gets a pickup inside the current season.
5. **Stale** — no pickup in 180 days.
6. Everything else is **ranked** on its stage's rate, including *seasonal open-ish* overrides,
   most urgent first (days past capacity, days past 75%, % full).

The automatic seasonality heuristic survives only as a `review_hint` column.
Customers are added to `MODEL_OVERRIDES` by a human, informed by
`fringe_seasonal_candidates.md` — never read from it.

**Dependencies between scripts.** The builder imports `backtest_steady_rate`
(both rates), `seasonality_score` (monthly shape), `backtest_seasonal_models`
(`CLOSED_INDEX`, `rate_span`), `backtest_customer_factors` / `customer_factors`
(the seasonal level and index), `seasonal_open` (the seasonal-open test, shared
with `backtest_phase2_rates`) and `build_fringe_seasonal_candidates` (season helpers and
the silence rule). The fringe script in turn reads `oil_projection_table.csv`
for its `projection_status` column, so run the builder first.

**Capacity** is read from the source site, never edited in the repo. A wrong
capacity is fixed on the site and arrives with the next nightly. See
`CLAUDE.md` and [`ROADMAP.md`](ROADMAP.md) for the capacity cleanup in progress.

## Decision log

| Date | Decision | Where |
|---|---|---|
| 2026-09-11 | Last 6 pickups as the first rate; windows 3–7 tie, averaging interval rates rejected | [below](#rate-study-2026-09-11-to-09-15), `model_comparison.md` |
| 2026-09-11 | **50/50 last 6 + previous year** as the default rate; seasonal blend unproven out of sample; no rate survives a closure | `seasonal_models.md`, `routing_rule_test.md` |
| 2026-09-15 | Customer month/quarter factors **rejected** as a blanket adjustment — the baseline already carries the season, a factor double-counts it | `customer_factor_model_test.md` |
| 2026-09-17 | Fringe/closer candidate table built as a **review tool**, not a classifier; elapsed silence in a seasonal account is evidence against accumulation, not for it | `fringe_seasonal_candidates.md` |
| 2026-09-29 | **`MODEL_OVERRIDES`** (18 customers) routes closers, call-driven, event and on-demand accounts around the 50/50 ranking; operator % full capped at 100; 50/50 arithmetic unchanged for everyone else | `build_projection_table.py`, PR #24 |
| 2026-09-29 | Beta `projections.html`, hidden from the nav, built nightly; bands frozen in committed `customer_wape.json` so CI and local agree byte for byte | PR #24 |
| 2026-10-01 | Capacities refreshed nightly from the source site (the April snapshot was 47 customers stale); corrections are made on the site | `oil_scraper.py`, PR #25 |
| 2026-10-05 | Jim's capacity review received; 103 to enter on the site, 9 to confirm with him | [`ROADMAP.md`](ROADMAP.md) |
| 2026-10-06 | **Shared barrels shown as one stop** (`SHARED_CONTAINERS`, 15 groups). Each account records only its share of the gallons, so the barrel is the members' gallons summed by date; combined rate = sum of member rates. Capacity convention with Jim: the full barrel on every member. `HISTORY_STARTS` for new owners on old accounts (JJ's, 2026-09-22) | builder |
| 2026-10-05 | **Empty checks (qty 0 / 1) restart the oil clock.** On 651 intervals with a 1 between two pickups, counting from the pickup over-projected the next pickup by +115% (WAPE 134%); counting from the 1, +11% (WAPE 81%). Rate unchanged. 3 (barrel delivery) tested as only a partial reset (+213% → −34%) and left out | `oil_scraper.py` (`RESET_QTYS`, `oil_non_pickups.csv`), builder `load_empty_checks` |
| 2026-10-07 | **Phase 2 rate tests** (research only, builder unchanged). Seasonal-open customers (29, scored from prior years only, with a 1000-shuffle noise test): season-free **level × month index** −14.5 WAPE points vs 50/50 on the 2025–26 holdout (−20.3 to −9.6), bias −6%; last 3 ÷ index × coming index −13.1, bias +0%; Sarge's last-year ±3 wk + last 2 at 70/30 −7.6, 50/50 −5.3. **Recency gate rejected** (last 2–3 vs the 3 before, >30/50/75%): −0.6 in choose, **+0.6 worse** confirmed, +1.9 on the pickups it fired on. EWMA −0.4: real, too small to adopt. Adoption of the index model for seasonal-open customers pending | `backtest_phase2_rates.py`, `phase2_rate_models.md` |
| 2026-10-08 | Newcomer design agreed with Sarge: **lumpy accounts are will-call** (last pickup X days ago, never projected); a customer's **first pickup is a starting point only**, never a rate; ≥ 3 pickups → "new" projection with a wide band; lean toward the high side via a band, never by inflating the estimate; shared-barrel members modelled separately in research. Partial pickups (truck nearly full) are absorbed by the pooled last-6 rate — not modelled. Code 2 is the sign-up call, not a fullness call | [`ROADMAP.md`](ROADMAP.md) |
| 2026-10-08 | **Newcomer replay** (research; 354 customers starting 2021+, pickups > `STALE_DAYS` apart left out). Pooled-rate error falls from ~53% at 2 measured gaps (the 3-pickup rule) to ~42% at 3–4 and ~37% by 7+; **3 pickups kept**, with a wider band. Promotion to 50/50 at one year −1.1 points (−1.7 to −0.6). Calibrated high-side factors (80th pct of actual ÷ projected, chosen 2021–23): new ×1.64 / ×1.49 / ×1.35 at 2 / 3 / 4–6 gaps, established ×1.45; holdout coverage 75–82%. Today's ±20% default band is far too narrow for newcomers. **Will-call rule: median gap > 120 d or gap sd/mean > 0.8** — 81% of Jim's labels caught; flagged pickups ~70% WAPE and −20% bias vs 29% for the rest. It also flags seasonal closers (off-season gap); a robust IQR variant did worse. Seasonal stage reached by 4 newcomers only, so learned shapes (Phase 3b) are not needed now. **Revised same day:** the sd/mean rule pulled 62 ranked customers, mostly summer businesses; the **season-aware** rule (gaps > 3× the median count as closures and are dropped first; median gap > 120 d or remaining sd/mean > 0.8) was chosen on 2021–23 gaps (F1 0.52, confirm 0.47) and moves 25. Likely range for rows without their own WAPE: projection × 20th–80th pct factors, new ×0.60–1.64 / ×0.71–1.49 / ×0.70–1.35, established ×0.75–1.45 |
| 2026-10-08 | **Ladder in the builder.** Stages new / established / seasonal placed automatically; will-call detector (overrides win; stale wins); calibrated ranges replace ±20% for rows without their own WAPE. Established rates unchanged; seasonal-open customers (19 ranked) move to level × month index. The seasonal-open test moved to `seasonal_open.py` (phase 2 report regenerates identically) | builder, `projections.html` |
| 2026-10-08 | **Seasonal-closed detector** (research). A closed block (2+ months under 10% of an average month) in the 2 complete years before Y predicts Y: 89% of flagged customer-years really close, 84% of predicted months right, 5 real pickups in predicted-closed months (1.9% of gallons) on 2024–25. One year of history is unsafe (70 pickups, 15%); three adds nothing. Finds 9 of 11 registry closers plus 7 new (Thunder Road, White Cottage, TCs, Cravens, Gondola's Morrisville, Grand Summit, Mad Taco Middlebury). **In-season closer rate**: pooled over open-season gaps only beats 50/50 by 20.9 points (−24.8 to −16.8), bias −7%; range ×0.62–×1.64. **Bug found:** `cf.factor_from_years` sets all-zero months to 1.0, so closers passed the seasonal-open test (Thunder Road, TCs ranked on the seasonal rate) — the shape must keep zeros | `backtest_seasonal_closed.py`, `seasonal_closed.md` | `backtest_replay_newcomers.py`, `replay_newcomers.md` |

## Known weaknesses of the current model

- **An in-season closer is ranked on a rate that includes its closed months**,
  so it likely understates the in-season rate. A seasonal rate needs its own
  backtest before it replaces 50/50 for them.
- **Seasons are month-level.** A season opens on the 1st of its first month;
  no reopening date is implied.
- **Two Okemo overrides rest on two years of history.**
- **Seasonality scores use the same years they describe** (2023–2025). A
  production classifier would score from history before each prediction.
- **Projected gallons are oil produced, not what the truck will collect.**
- **A dated `--as-of` run leaks later pickups**; it never writes the site file.

---

# Rate study (2026-09-11 to 09-15)

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

**Customer month and quarter factors were tested on top of this and rejected**
for steady and erratic customers — the baseline already carries their season, so
a factor double-counts it. They remain worth having only inside the seasonal
branch. See [below](#do-customer-month-factors-add-anything-2026-09-15).

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

## Do customer month factors add anything? (2026-09-15)

`customer_factors.py` → `customer_cyclicality.md`,
`backtest_customer_factors.py` → `customer_factor_model_test.md`.

**No, not as a blanket adjustment — and the reason is double-counting.** last6
spans a median of 131 days against the 21-day gap it predicts, so it already
carries a third of a year of season; the previous-year rate carries the same
season a year earlier. Multiplying that baseline by a month index applies the
swing twice.

Measured directly: the **residual** factor (actual ÷ baseline prediction, by
customer and month, built only from earlier pickups) has a median of **exactly
1.000** for steady, seasonal and erratic customers. There is no leftover
monthly bias for a factor to correct.

WAPE on 5,492 held-out pickups from 104 open customers, everything
no-lookahead:

| Model | All open | Steady (73) | Seasonal (12) | Erratic (19) | Closers (10) |
|---|---:|---:|---:|---:|---:|
| 50/50 baseline | **37.3%** | **25.1%** | 45.9% | 75.6% | 138.3% |
| season-free level × month index | 40.2% | 32.1% | **31.4%** | 75.4% | **62.7%** |
| baseline × month index (naive) | 40.4% | 30.5% | 38.4% | 77.3% | 112.7% |
| baseline × residual month factor, w=0.5 | 39.4% | 26.1% | 41.4% | 86.3% | 136.5% |
| baseline × residual quarter factor, w=0.25 | 37.2% | 25.1% | 42.6% | 77.0% | 221.3% |

- **Steady and erratic customers:** nothing beats the plain baseline. The best
  model chosen on 2023–24 came back at +0.1 points on 2025–26 — a null.
- **Seasonal customers:** the explicit model wins hugely, −16.9 points on the
  confirm period (interval −22.3 to −12.0). This is the same routing answer as
  the seasonal blend, reached a different way.
- **Closers:** −82.6 points, but still 54% WAPE. Better, not usable.
- **Raw factors overfit as expected:** every shrinkage step from 0.25 to 1.00
  makes the residual models worse (37.2% → 39.5% quarterly, 38.1% → 43.3%
  monthly), and caps only limit the damage rather than producing a win.
- **Quarterly is the safer of the two for open customers** — roughly neutral
  where monthly actively hurts — but neutral is not a reason to add a term, and
  on closers it is the worse of the two by a wide margin (221% against the
  baseline's 138%).

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
| `customer_factors.py` → `customer_cyclicality.md` | Per-customer monthly/quarterly factors and the screen of who has a repeating pattern |
| `backtest_customer_factors.py` → `customer_factor_model_test.md` | Whether those factors beat the 50/50 baseline — explicit, naive and residual forms |
| `build_fringe_seasonal_candidates.py` → `fringe_seasonal_candidates.md` | Review table of seasonal / closer / call-driven / event / on-demand candidates. Labels are prompts, not classifications |
| `backtest_phase2_rates.py` → `phase2_rate_models.md` | Phase 2: seasonal-open detection from prior years (with a shuffle noise test), Sarge's last-year ±3 wk formula, month-index models, recency gates and EWMA; choose 2023–24, confirm 2025–26 |
| `backtest_replay_newcomers.py` → `replay_newcomers.md` | Phase 3: every newcomer replayed from its first pickup through the insufficient / new / established / seasonal ladder; learning curve, calibrated high-side bands, will-call rule |
| `backtest_seasonal_closed.py` → `seasonal_closed.md` | Seasonal-closed detection from prior years (1/2/3 years, thresholds), its safety (pickups that would be held out), and the in-season closer rate |
| `build_projection_table.py` → `oil_projection_table.md`, `../oil_projections.json` | The production projection: 50/50 rate, `MODEL_OVERRIDES`, routing, capped % full. Run nightly |
| `customer_wape.json` | Committed snapshot of per-customer 50/50 WAPE for the confidence bands. Rebuild with `--refresh-wape` after rerunning `test_routing_rule.py` / `backtest_seasonal_models.py` |

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
python3 analysis/customer_factors.py                                    # per-customer factors
python3 analysis/backtest_customer_factors.py                           # do the factors help?
python3 analysis/backtest_phase2_rates.py                               # phase 2 rate tests (a few minutes)
python3 analysis/backtest_replay_newcomers.py                           # newcomer replay (seconds)
python3 analysis/backtest_seasonal_closed.py                            # closer detector (seconds)
python3 analysis/build_projection_table.py                              # projections (the nightly runs this)
python3 analysis/build_projection_table.py --as-of 2026-06-30           # what-if date; never writes the site file
python3 analysis/build_projection_table.py --refresh-wape               # rebuild customer_wape.json
python3 analysis/build_fringe_seasonal_candidates.py                    # seasonal review table (after the builder)
```

The nightly scrape adds pickups, so figures will drift slightly from those
above on a later rerun.

**Known quirk, benchmark only:** the ±45-day window skips a pickup made the same
day as the one before it, gallons included. One instance in scope (Doc Ponds,
2024-04-19). Left as is, since that window is not a candidate model.
