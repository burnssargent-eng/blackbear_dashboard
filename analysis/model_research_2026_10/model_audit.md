# Model audit: the champion as it runs (main f204e6b, 2026-10-10)

Research only. Nothing here changes the builder. Line numbers refer to
`analysis/build_projection_table.py` ("BPT") on `main`.

## 1. Exact ladder formulas

Notation: pickups p0..p(n-1) before the as-of date. `span(lo, hi)` is the
gallons of p(lo+1)..p(hi) divided by days(p(lo) → p(hi)) (`bsm.rate_span`).
Pickup lo marks where the days start; its own gallons never count. **Every rate
window ends at the last pickup, never at the as-of date.**

| Stage | When (build_row order, BPT:1084-1127) | Rate |
|---|---|---|
| dormant restart → *new* | Checked first. The last gap over 180 d sits inside the last-7 or previous-year window, with ≥ 3 pickups from the reopening on | `span` over ≤ 6 gaps of the post-gap pickups |
| *new* | ≥ 3 pickups and no previous-year rate | `span(max(0,n-7), n-1)` |
| *established* | A previous-year rate exists | ½ `span(n-7, n-1)` + ½ **prev-year**. Prev-year is gallons after the last pickup on or before as-of − 365, divided by the days from that pickup to the last pickup. It is a trailing window that **contains** the last-6 window: the two halves overlap |
| *seasonal* | Established, and `seasonal_open.classify` = seasonal-open for the as-of year | ½ A + ½ B (or whichever exists), as below |
| *closer in season* | Registry true/semi-closer, or detected closer (`closed_months`), in a season month, with ≥ 3 qualifying gaps. Overrides every stage above | pooled over the last ≤ 6 gaps lying **wholly** in open months (BPT:707). Every counted day is an open day |

The seasonal stage's two halves:
- **A** = 0.7 × LY + 0.3 × `span(n-3, n-1)`.
  - LY is the mean, over 1 and 2 years back, of the spread daily production in the ±21 days around as-of − 365·back (BPT:623-657).
  - LY is only available when both inputs exist.
  - It has no index alignment: last year's window is centred on the as-of date, not on the interval being predicted.
- **B** = `span(n-4, n-1)` ÷ Ī(p(n-4) → p(n-1)) × Ī(clock_start → as-of). Ī is the **day-weighted** mean of the monthly index over the interval (`cf.gap_weighted`). This is already an exposure calculation across month boundaries.
- **Monthly index** (`seasonal_open.season_for_year`):
  - Built from the 3 complete years before the as-of year, skipping 2020 and never using a year before 2016.
  - Each pickup's gallons are spread evenly over the `min(gap, 60)` days ending on its date. Days before a 60-day cap get zero.
  - Each year's 12 monthly means are divided by that year's mean month. Years are averaged with zeros kept, then renormalised to mean 1.
- **Seasonal-open test**, in order:
  - unscored without 3 years;
  - ≥ 300 gal/yr;
  - trough ≥ 0.10;
  - amplitude ≥ 0.25 and leave-one-out repeatability ≥ 0.60;
  - 1,000 month shuffles seeded per customer-year, passing at p ≤ 0.05.

Routing (`route`, BPT:1012) runs in this order:
1. No-rate overrides.
2. Will-call: median gap > 120 d over the last 3 years, or sd/mean > 0.8 after dropping gaps over 3× the median. Never applied to an override, and not once stale.
3. Insufficient: fewer than 3 pickups.
4. Seasonal holdouts:
   - true closer off-season;
   - semi or detected closer off-season and silent past max(60, 2 × median gap);
   - "awaiting first pickup" when the clock predates the season's 1st.
5. Stale: more than 180 d since the last pickup.
6. Ranked.

Clock = the last pickup, or a later empty check (qty 0/1). It moves the start of the days only, never a rate window.

Projection = rate × days(clock → as-of). Target = 0.75 × listed capacity.

Shared barrels (`SHARED_CONTAINERS`, 15 groups) are folded into one stop before all of this.

## 2. The replay used as champion here

`replay.py` calls these BPT functions in `build_row`'s order at every
historical pickup k:
- `rate_pooled`, `rate_prev_year_asof`, `dormant_restart`, `seasonal_rate`, `rate_open_season`, `will_call_signal`, `route`;
- the per-year classification `seasonal_factor` / `closed_months`, cached by (customer, year) because it reads only pickups before Jan 1.

Inputs at pickup k: pickups[:k], empty checks strictly between pickup k-1 and pickup k, and as-of = date(k).

**Fidelity check:** at today's date with full history, the replay (with the registry on) and `build_row` agree on stage, section and rate for **782 of 782** customers.

**Reconciliation:** on `dormant_restart.md`'s subset (established or seasonal, more than 8 prior pickups) the replay matches it closely:

| Window | Replay | `dormant_restart.md` |
|---|---|---|
| Confirm 2024–26 | 29.4% WAPE, bias −2.8%, 7,578 pickups | 29.5%, −2.3%, 7,518 |
| Choose 2021–23 | 32.3%, 4,891 pickups | 32.7% |

The differences are expected: that report kept closers out, and its "production" still carried the growth factor removed on 10-08.

**Scored** = ranked by the champion, gap 1–180 d, days on the clock > 0. In core mode, registry no-rate accounts are dropped too.

### Champion by year (core mode: detected closers only, no registry)

| Year | Role | Customers | Pickups scored / all | Gallons | WAPE | Median APE | Bias | new / estab. / seasonal / closer |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 2017 | warm-up only | 154 | 1,427 / 1,572 | 88,835 | 32.2% | 23.1% | −0.4% | 222 / 1,205 / 0 / 0 |
| 2018 | least-inspected | 198 | 1,658 / 1,871 | 127,415 | 37.5% | 25.0% | +0.0% | 376 / 1,271 / 0 / 11 |
| 2019 | least-inspected | 224 | 1,557 / 1,776 | 141,568 | 38.0% | 27.0% | +3.0% | 272 / 1,250 / 21 / 14 |
| 2020 | pandemic, not pooled | 198 | 1,184 / 1,370 | 116,813 | 47.0% | 33.1% | +8.6% | 146 / 1,012 / 3 / 23 |
| 2021 | dev | 229 | 1,531 / 1,778 | 132,589 | 42.6% | 30.6% | −1.3% | 262 / 1,251 / 0 / 18 |
| 2022 | dev | 274 | 2,160 / 2,470 | 172,294 | 34.0% | 26.4% | −4.9% | 450 / 1,638 / 24 / 48 |
| 2023 | dev | 317 | 2,642 / 2,911 | 203,192 | 30.6% | 23.5% | −3.5% | 460 / 2,116 / 31 / 35 |
| 2024 | confirm | 334 | 3,093 / 3,339 | 231,490 | 30.3% | 23.3% | −4.2% | 279 / 2,665 / 86 / 63 |
| 2025 | confirm | 348 | 3,150 / 3,412 | 235,978 | 31.4% | 24.6% | −2.5% | 315 / 2,646 / 122 / 67 |
| 2026 YTD | confirm | 355 | 2,533 / 2,752 | 189,709 | 32.7% | 23.4% | −0.1% | 215 / 2,053 / 171 / 94 |

The registry run (hindsight) differs by at most 1.4 WAPE points in any year, mostly on closers.

**Sample sizes constrain the seasonal experiments.**
- Seasonal stage: no pickups before 2019, because the seasonal-open test needs 3 complete years from 2016 on. It has 21 / 0 / 24 / 31 pickups in 2019 and 2021–23, and 86 / 122 / 171 in 2024–26. Dev-period tuning for C–F therefore rests on about 55 pickups. Those experiments are also run as a component test on a **wider seasonal set**, the formula with old vs new ingredients (see `seasonal_exposure.md`).
- Closers in season: 11–94 pickups a year.

## 3. Assumptions: reasonable and questionable

| Assumption | Verdict |
|---|---|
| Rate = total gallons ÷ total days | Sound, and measured: a mean of interval rates biases +6% (README) |
| The first pickup is a starting point, never a rate | Sound: oil may predate the barrel |
| Windows end at the last pickup; projection = rate × days on the clock | Sound for production. It ignores inventory left in the barrel |
| Prev-year window **contains** the last-6 window | Questionable as a "50/50 of two estimates". The halves are positively correlated, so the effective weight on recent gaps is above ½ (Experiment A) |
| Monthly index from production **spread flat inside each gap**, capped at 60 d | Questionable. It assumes no seasonality within a gap, while the index exists to describe seasonality within gaps. The 60-day cap piles a long gap's gallons into its last 60 days (Experiment C-idx) |
| Seasonal half A ±3 weeks around as-of − 1 y | Mildly questionable. The window is centred on today, but the oil being predicted was produced since the clock started (Experiment C-A) |
| Closer rate from wholly-open gaps only | Sound, but it discards every gap that straddles opening or closing, including the whole first interval of each season |
| Season opens on the 1st of its first month | Unverifiable: no reopening date exists in the data (Experiment H) |
| Projected gallons = oil produced = what the truck collects | Questionable: partial pickups and carryover (Experiment J) |
| Quantities 0/1 reset the clock; 2/3 are non-events; 4 = 4 gal | Adopted from measurement (README 2026-10-05). Not revisited |

## 4. Already implemented or already tested

| Proposed | Status before this study |
|---|---|
| C1 / C5: exposure-weighted index over the interval | **Already production** (seasonal half B uses `gap_weighted` over both the history span and the forecast span). Not re-run as a candidate |
| C3: level × exposure only | Tested twice (`phase2_rate_models.md`, `seasonal_formulas.md`): −8.9 / −10.3 vs 50/50 on strict seasonal, worse than today's formula. Kept only as a reference row |
| G1: exclude gaps crossing closed months | **Already production** (`rate_open_season`) |
| A (weights): last-6 only, prev-year only | +0.9 / +0.6 vs 50/50 (`customer_factor_model_test.md`). Reliability weighting was never tried |
| B (horizons): frequency override (≤ 14/21/30 d → last 3/4) | Rejected, +1.4 to +5.3 where fired (`recent_override.md`). The consistency gate (60–120 d spans) was also rejected |
| E (shrinkage): fleet index^w on all customers | Rejected, +0.01 / +0.13 (`dormant_restart.md`) |
| F (smoothing): quarter vs month factors | Quarter no better (`customer_factor_model_test.md`) |
| K: EWMA half-life 2–4 blended with prev-year | −0.5 on choose, "too small to adopt" (`phase2_rate_models.md`) |
| H / I / J | Never tested. "Partial pickups absorbed by last 6" was asserted on 10-08, not measured |

## 5. Historical metadata: what can be known as of a past date

| Input | As-of reconstructible? | Treatment |
|---|---|---|
| Pickup history | Yes | `pickups[:k]` |
| Empty checks (0/1) | Yes, but **recorded only from 2021** (≤ 2 a year before) | Clock resets cannot happen in 2017–20. This applies equally to champion and candidates |
| Monthly indices, seasonal-open, closer detection | Yes: from complete years before Jan 1 | As the builder does |
| Will-call, dormant restart | Yes: from `pickups[:k]` | — |
| Capacity | **No.** The cache's history starts 2026-06-19 | Never used. Size and urgency use the as-of median pickup instead |
| `MODEL_OVERRIDES` | **No.** Written 2026-09-29 from all history | Core mode ignores it. The registry run is labelled hindsight |
| Shared barrels, history starts | No history | Today's config (15 groups, 1 start). Documented |
| Active status | No history before 2026-07 | Not used: every customer is replayed |
| Pickup totals of 2/3 (sign-up, barrel delivery) | Yes | Not used, as in production |

## 6. Leakage and "untouched" status

- 2021–26 have all been scored by earlier backtests (choose and confirm), so here they are **retrospective replays**, not independent confirmation.
- 2018 and 2019 were never scoring years in any earlier backtest (no script references them). They are the closest thing to untouched history.
  - They still are not untouched in the strict sense: the ladder was designed by people who knew the business through those years.
- Prospective validation is the live test (`live_test.py`) going forward.
