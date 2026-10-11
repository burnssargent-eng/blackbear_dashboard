# Oil-rate model research, October 2026: recommendations

The study itself was research only: the champion is `main` at f204e6b (the 2026-10-08 ladder plus the dormant restart), and every number below is against it. **Recommendation 1 was adopted afterwards (2026-10-11, branch `offseason-open-days`)**; the other two remain open.

## 1. Audit (full version: `model_audit.md`)

- **Replay.** A faithful replay of the champion predicts every historical pickup from what was known the morning of the pickup: pickups before it, empty checks before it, and seasons and indices from prior complete years only.
  - At the live edge it matches `build_row` on 782 of 782 customers.
  - It reconciles with `dormant_restart.md`: confirm 29.4% vs 29.5% WAPE.
- **Champion WAPE by year.**

  | Years | Champion WAPE |
  |---|---|
  | 2018–19, the least-inspected years | 37.5% and 38.0% |
  | Dev, 2021–23 | 42.6%, 34.0% and 30.6% |
  | Confirm, 2024–26 | 30.3%, 31.4% and 32.7% |

  Bias is between −5% and +3% in every year.
- **What was already in production.** Exposure is already day-weighted in seasonal half B (C1/C5). Closers are already rated on wholly-open gaps (G1). Several "new" ideas were therefore narrower than they looked.
- **Hindsight inputs, not used in core scoring:**
  - `MODEL_OVERRIDES`, written 2026-09-29;
  - capacity history, which starts 2026-06;
  - `is_active` history, which starts 2026-07.

  Empty checks exist only from 2021, so 2018–19 run without clock resets, for champion and candidates alike.
- **Inspection status.** 2021–26 had all been inspected by earlier work, so they are retrospective replay, not untouched confirmation. 2018–19 were never scoring years before, so they are the closest thing here to fresh data.
  - **Multiple comparisons.** 104 candidate-vs-champion tests have a pooled interval. At 95%, about 5 would clear zero by chance in either direction.
  - **The bar for "supported":** the interval excludes zero, the same sign in at least 6 of 8 years, and it survives the rolling (expanding-window) choice.

## 2. Results

Δ is WAPE points vs the champion on identical pickups, all eval years pooled (2018–19, 2021–26). Intervals are 95%, from resampling whole customers (2,000 draws, seed 42).

| Exp | Candidate (scope) | Δ WAPE (95% interval) | Years better | Bias: champ → cand | Verdict |
|---|---|---|---:|---|---|
| **H** | **H2: off-season "called recently" projected on open days only** (detected closers, weekly snapshots) | Off-season flags 214 → 59; flagged with no pickup in 30 d 88% → 75% (confirm 87% → 62%). On the 17 off-season pickups: −97 (−183 to −51) | every window | +171% → +64% | **Supported** (partially observed) |
| H | R1: rank closers during "awaiting first pickup" | 63–66% of flags not followed by a pickup (in-season reference 34%) | – | – | Rejected: keep the holdout |
| I | First pickup of a season, lag 30 d (held out today; vs 0 lag) | −58 (−85 to −32) | 8/8 | +94% → +21% | Still not forecastable: keep the holdout |
| I | Last season's tail counted (carryover) | +59 (+26 to +107) | 1/8 | – | Rejected. Carryover b = 0.13 (−0.01 to 0.90): not identifiable |
| C | C-cap 180: index spread over the whole gap (wide) | −0.77 (−1.37 to −0.35) | 8/8 | +1.1% → +0.6% | Supported, too small to matter. Strict confirm −0.07 |
| C | C2: half B over 6 gaps (strict) | −1.17 (−2.60 to +0.01) | 6/6 | – | Inconclusive |
| C | C-idx (exposure-fitted index); C-A (half A exposure-adjusted) | −0.84 (−3.02 to +0.95); +3.04 | 5/8; 0/8 | – | Inconclusive; rejected |
| G | G2–G5 closer alternatives | +0.40 to +10.45 | ≤ 4/8 | – | Rejected. G0 (production) stands |
| J | Carryover signature (fixed-rate lag-1 correlation) | r = +0.29 to +0.41, every window | – | – | **None.** Persistence, not carryover |
| J | Gap elasticity β = 0.6 (collected volume) | −2.51 (−3.16 to −1.92) | 8/8 | −2.0% → −5.0% | Not for projections (see §3) |
| A | A2 inverse variance, rolling | −0.00 (−0.02 to +0.02) | 6/8 | – | Null: the 50/50 stands |
| A | A3 covariance-aware, rolling; A1 span weight | +0.29 (+0.09 to +0.50); +0.21 | 2/8; 1/8 | – | Rejected. A3 weights swing 0.13 → 1.00 between years |
| B | B-half, 60 d horizon as the recent half | −0.33 (−0.61 to −0.09) | 5/8 | – | Not supported (fails the year test) |
| B | B-solo, any horizon | +0.96 to +1.55 | 0/8 | – | Rejected |
| K | Decaying rate, τ 90 d (established) | −0.16 (−0.43 to +0.12) | 6/8 | – | Null |
| K | **Decaying rate, τ 45 d (new stage)** | −1.64 (−3.13 to −0.13); rolling −1.32 (−2.88 to +0.22) | 7/8 | **+9.3% → +1.1%** | Promising, inconclusive |
| D | Level × shape, any recent weight (strict / wide) | +1.97 to +2.71 / +5.71 to +8.68 | ≤ 2/8 | – | Rejected |
| E | Group-shrunk index, k = 4 (wide) | −3.61 (−5.03 to −2.45) | 8/8 | – | Real on wide, but no better than the plain 50/50 there (−3.38). Strict (production): k = 1 −0.20, k = 4 +0.43. No production case |
| F | Smoothed (interpolated) index (wide / strict) | −0.45 (−0.56 to −0.34) / −0.30 (−0.65 to +0.24) | 8/8 / 4/6 | – | Supported, too small to matter |

Every row, every window and every year is in `experiment_results.csv` (419 rows), and per-customer effects are in `customer_impacts.csv`. Notes per experiment are in `seasonal_exposure.md`, `closer_exposure.md`, `inventory_diagnostics.md` and `reopening.md`. The tables are in `results_*.md`.

## 3. What it means operationally

- **The ladder is close to what these data can support.** Every rate-formula idea (A, B, C, D, E, F, G, K-established) moves WAPE by less than a point, or makes it worse. The physically motivated versions were usually worse: more parameters to be wrong. Simple pooled rates over recent gaps remain hard to beat.
- **The biggest errors are about timing, not rate:**
  - when a closer actually opens;
  - when a closer has really stopped producing;
  - how pickup timing interacts with the barrel.

  A rate formula cannot fix those. Each one shows up as a large bias in a small, identifiable group:
  - off-season calls, +171%;
  - first pickup of a season, +94%;
  - long gaps, +61%;
  - restarted accounts, +22%.
- **Off-season closers produce the worst false urgency on the list.** After an off-season call the projection keeps running through closed months, so about 25 customers show as due when they are not.
- **Partial pickups and carryover are not what the data show.**
  - Consecutive pickups move together: production shifts persist.
  - Collected gallons grow with roughly the square root of the gap. That is evidence about how pickups are timed, not about leftover oil.
  - Nothing here supports adding an allowance or carryover term.
- **Short-gap under-projection (−15% at 1–14 days) is real but endogenous.** The truck comes early where there is more oil. A model that "corrected" it would demote long-unvisited stops, which are the ones most likely to be full.
- **New and restarted accounts run hot.** The new stage over-projects by +9% (+22% after a dormant restart). Weighting recent gaps more heavily (K, τ 45 d) removes most of that bias. The gain is concentrated, though: more customers get slightly worse (226) than better (200), and the tuning is unstable across folds.

## 4. Recommendations (3; none implemented)

1. **Project off-season "called recently" closers on open days only (H2).**
   - **The change.** When a detected or semi-closer is ranked off-season, project closer rate × days in open months since the clock, instead of stage rate × all days.
   - **Scope.** It touches only rows already routed by the "called recently" rule, about 25 customers. It reuses values the row already has (`rate_open_season`, `season_months`).
   - **Evidence.**
     - It removes about 72% of off-season flags. The no-pickup share falls in every window: confirm 87% → 62%.
     - On the off-season pickups that did happen, WAPE goes 182% → 85% (13 customers; 12 better).
   - **Caveats.**
     - The outcome is partially observed.
     - 7 of 32 confirm pickup-weeks lose their flag. These are call-driven, so the call still reaches Jim.
   - **Adopted 2026-10-11** as stage *closer off-season* in `build_row`, with a projections test. `replay.py` here still replays the f204e6b champion, so its `--check` now differs on off-season ranked closers by design. Watch the live-test log through the off-season.
2. **Keep the reopening holdout, and ask Jim for operating dates instead of modelling them (H, I).**
   - Month-level seasons put the opening a median of 70 days before the first pickup.
   - A season yields about a third of what its calendar days imply before then.
   - Ranking those weeks would mostly raise false alarms (65%).
   - **Proposed.** One question to Jim: when do the closers actually open and close, and how is the first visit of a season timed? Store the answer as day-level dates on the registry's season field.
   - That one data item would address G's shortfall, H's false urgency and I's +94% bias together. No formula change until the dates exist.
3. **Re-test a faster-decaying rate for the new stage (K, τ 45 d) in six months; do not adopt now.**
   - It is the only rate change with a large, consistent bias fix: +9.3% → +1.1%, better in 7 of 8 years.
   - It fails the stricter bar:
     - the rolling interval spans 0;
     - the odd→even choice is unstable;
     - more customers get worse than better.
   - Re-run `exp_established.py` on 2027 data, with τ frozen at 45. If it holds on those genuinely new pickups, it is a one-function change to the new stage.

**Not recommended, with reasons in §2:**
- the collected-volume elasticity, for projections (it is a candidate for asymmetric range bands, as a separate study);
- every A, B, C, D, E, F and G variant;
- any carryover or allowance term.

## 5. Repo status

- **Branch.** The study ran on `research/model-2026-10`, local only, cut from `main` at f204e6b, with nothing committed. Its scripts and reports were committed later with recommendation 1.
- **New files.** `analysis/model_research_2026_10/` only. Scripts and `.md` reports are committed; the CSVs regenerate from `run_all.py` and are not committed; `__pycache__` is gitignored.
  - **Code:**
    - `replay.py`, `metrics.py`, `harness.py`, `run_all.py`
    - `exp_seasonal.py` (C), `exp_closer.py` (G), `exp_inventory.py` (J), `exp_reopen.py` (H, I), `exp_established.py` (A, B, K), `exp_shape.py` (D, E, F)
  - **Outputs:**
    - `experiment_results.csv`, `customer_impacts.csv`
    - `results_{C,G,J,H,I,ABK,DEF}.{csv,md}`, `impacts_{C,HI,ABK,DEF}.csv`
  - **Notes:**
    - `README.md`, `model_audit.md`, `seasonal_exposure.md`, `closer_exposure.md`, `inventory_diagnostics.md`, `reopening.md`
    - this file
- **Production untouched during the study.** `git diff --stat` on tracked files was empty; the builder, registry, overrides and generated JSON were unchanged until recommendation 1 was adopted.
- **Checks.**
  - `python3 validate_data.py`: 116 pass, 2 warnings (the standing two), 0 fail.
  - `node tests/run_all.js`: 6 of 7 suites pass. `seasonality_test` fails as it already did before this work.
- **Determinism.** `run_all.py` reproduces every CSV and md byte for byte (checked by re-running).
- **Replay cache.** It lives outside the repo (`RESEARCH_CACHE`, default `/tmp`).
