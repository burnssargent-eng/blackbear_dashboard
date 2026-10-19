# Comeback gaps, cutoff and level shifts: recommendations

Research only, on the October 15 champion (`main` 90075bf). It was replayed by `champion.py` and matches `build_row` on 784 of 784 customers at the live edge. Nothing below was adopted.

**Afterwards (2026-10-17/18):** Sarge's own rule for 181–365-day returns was backtested in `return_last_year.md`. It uses the same time last year for the 2nd pickup back, then half that and half the first rate back for the 3rd. It beat A1 where both apply, and covered 295 pickups to A1's 121.

Two live rows then showed its blind spots: a fading account, and a small seasonal account that would keep filling all winter. The rule was adopted with one guard, as **V2** in `return_guards.md`: reopenings only, with every other 181–365-day return held. On reopenings it scored −20.9 WAPE points, and flagged weeks with no pickup fell from 77% to 51%. An open-months guard (V4) was dropped for parsimony.

The figures below were written on the 90075bf data. The committed tables were regenerated one nightly later (7a2d876, 9 more established pickups), and the headline results are unchanged.

Δ = candidate WAPE − champion WAPE, in points, on identical pickups. Intervals are 95%, resampling whole customers. Unless a row says otherwise, figures pool all eval years (2018–19, 2021–26).

## Conflicts found before coding

1. **The October replay is stale.** `model_research_2026_10/replay.py` replays f204e6b, before:
   - the comeback hold;
   - the off-season open-day rule;
   - the capacity cap;
   - Mad Moose's history start.

   `champion.py` adds the first two. The cap can't be replayed honestly because capacity history starts 2026-06, so it appears only as a hindsight sensitivity. The history start is config and arrives on its own.
2. **"The first two pickups after a return" are R2 and R3.** R1 is never projected after a gap over 180 d: at that point the row is stale.
3. **The ~50% under-projection was measured on another walk** (`backtest_dormant_restart.py`, before the hold). On the Oct-15 replay it is −31% (R2) and −40% (R3).
4. **Not every band is asymmetric.** Ranges are asymmetric only for rows without their own WAPE. Established rows with a customer WAPE get a symmetric ±WAPE band. This was noted and left unchanged.
5. **Several prompt candidates had already been tested on f204e6b**, so they were re-run as re-tests:
   - A2 (`dormant_restart.md`);
   - A4 (Experiments I and J);
   - C2 (A1 span weight);
   - C4's family (K decay, phase-2 EWMA).

## Question 1: why are 181–365-day returns under-projected?

**The evidence** (`comeback_181_365.md`; 395 R2/R3 pickups, 131 customers, at least 3 pre-gap gaps):

- **The long gap produced almost no oil.** R1 holds 0.24× the pre-gap rate × gap days, and 0.29× even counting open months only. Today's 50/50 puts that interval into both of its windows: its rate is 0.79× the pre-gap rate.
- **The miss sits in the established 50/50 stage:** actual ÷ projected is 2.04. Closers already use open-season gaps and are about right (1.07).
- **Where no closed-month pattern exists ("ordinary" gaps), the pre-gap rate is unbiased (1.03).** Where one exists (reopenings), actual production runs about 2× the pre-gap rate, and 1.38× even on open days only.
- **Carryover.** Every allowance made predictions worse in every year (A4: +0.5 to +1.7). A large R1 would point to leftover oil; R1 is small.

**The main explanation is closed-day dilution:** inactive days in the rate denominator. It is not leftover inventory.

**Interpretation, not shown.** For seasonal reopenings, the extra lift above open-day dilution (1.38×) is likely the pre-gap rate coming from the slow end of last season while the return lands in peak season. A genuine production change cannot be separated from that with pickup data.

**The simple correction that helps: A1, open-day exposure on the loose calendar.** It holds in both periods, on returns only (121 pickups, 53 customers):

| Window | Pickups | Champion → A1 | Δ (95% interval) | Bias: champion → A1 |
|---|---:|---|---|---|
| Dev 2021–23 | 39 | 60.8% → 52.5% | −8.25 (−13.30 to −1.44) | −58.5% → −44.3% |
| Confirm 2024–26 | 65 | 63.1% → 50.6% | −12.53 (−17.38 to −6.90) | −58.0% → −38.3% |
| 2018–19 | 17 | 47.6% → 29.9% | −17.64 (−31.96 to +9.11) | −46.7% → −3.7% |
| All, 7/8 years better | 121 | 60.2% → 48.9% | **−11.30 (−15.25 to −6.64)** | −56.9% → −36.7% |

- **Robustness.**
  - Capped at today's capacity (hindsight): −10.67.
  - R2 −12.3 and R3 −10.4.
  - Summer businesses −15.2; winter businesses −5.8 (crosses 0).
  - No customer contributes more than 1.2 points.
- **Limits.**
  - Bias stays near −37%.
  - Only about a third of 181–365 returns have a calendar to use (the rest fall back to the champion).
  - As a rule for **everyone** it is worse: +0.14 (+0.03 to +0.27), because the loose calendar fires on about 400 non-return pickups.
  - The detector's own calendar never fires here: those customers already have open-season rates.
- **A2 (pre-gap rate) is rejected.** Scope (a): +6.55 (−1.13 to +14.17), 3/8 years. Scope (b): +1.10, 4/8 years.
- **A3 (the blend) is inconclusive.** Dev chose w = 0.5:
  - (a) +0.05;
  - (b) −2.58 (−7.75 to +2.72);
  - (b) at w = 0.25 clears zero on confirm only, with a rolling choice of −2.21 (−4.64 to +0.26).

## Question 2: is 365 days a defensible cutoff?

**Reasonable, but not uniquely optimal** (`comeback_cutoff.md`).

- **There is no break at one year.** On the unheld first projected pickups, median actual ÷ projected rises steadily:

  | Gap | 181–240 | 241–300 | 301–335 | 336–365 | 366–400 | 401–450 | > 730 |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | Median actual ÷ projected | 1.54 | 1.90 | 2.45 | 3.06 | 3.96 | 4.38 | 6.64 |

- **The trade, against production (365 d, hold of 3).** The error figures are what the projections would have scored, not zeros:
  - **300 d** holds 35 more pickups (21 customers), whose projections run 54% WAPE / −52% bias.
  - **270 d** holds 90 more, at 56% / −42%.
  - **450 d** releases 15, at 77% / −77%.
  - **540 d** releases 18, at 75% / −74%.
  - **Hold of 2** releases 39, at 76% / −76%.
  - **Hold of 1**, no hold at all, releases 86, at 82% / −80%.
- **The hold hides almost no route candidates.**
  - Production suppresses 2,334 ranked weeks (100 customers). Only 23 would have been flagged (projection ≥ median pickup), and 6 of those saw a pickup within 30 days.
  - Held projections are tiny. The hold prevents a **falsely calm** row, not false urgency.
  - A missing pickup does not prove an empty barrel (partially observed).
- **Every withheld customer is small:** none is top-quartile volume. 21% are seasonal at 365 d, rising to 41% at 270 d.
- **Keep 365.**
  - Lowering it trades fewer misleading rows for more hidden ones, without a clean breakpoint.
  - A1 addresses the 181–365 range more directly than a hold would.
  - The planned 2027 re-test of a 2-pickup hold stands.

## Question 3: can persistent level shifts be detected reliably?

**They are detectable but already absorbed** (`level_shift.md`; 14,771 established pickups, 464 customers).

- **C1, a fixed recent weight, is null.**

  | Weight | Δ | 95% interval | Years better |
  |---|---:|---|---:|
  | 0.55 | +0.01 | −0.02 to +0.04 | 3/8 |
  | 0.60 | +0.03 | −0.02 to +0.08 | 3/8 |
  | 0.65 | +0.06 | −0.01 to +0.15 | 3/8 |

- **C2, span reliability:** +0.03, null.
- **C3, the non-overlapping persistent-shift detector** (dev chose k = 2, T = 0.15):
  - It fires on 2,696 pickups (320 customers).
  - The shifts are real: 84% persisted over the next 180 days.
  - Raising the recent weight when it fires changes nothing: +0.01 (−0.02 to +0.04).
  - The 50/50 already follows these shifts: the previous-year window contains the last six gaps, so the effective recent weight is already above ½.
- **Noise vs shift.**
  - Errors persist: the lag-1 log-error correlation is +0.19 (+0.16 to +0.22).
  - After a pickup under 60% of its projection, the next one beats its projection 49% of the time, against 55% overall.
  - So there is no alternating-error sign of partial collections.
- **C4, a duration-weighted local level** (gain d/(d + 90), from the year-before reference), blended ½ / ½ with the champion:

  | Window | Δ (95% interval) |
  |---|---|
  | Dev | −0.66 (−0.87 to −0.46) |
  | Confirm | −0.36 (−0.49 to −0.24) |
  | All, 8/8 years better | **−0.44 (−0.56 to −0.34)** |

  - The rolling and odd→even choices agree.
  - With the cap applied: −0.48.
  - 301 customers better, 163 worse.
  - Alone it scores −0.52 (7/8 years).
  - It helps stable customers (−0.42) about as much as disagreeing ones (−0.56). So it is a slightly better recent estimate, not a shift detector.
  - Caveats:
    - D = 90 is at the edge of the declared grid {90, 180, 365}.
    - The gain is about 1.4% relative, the size the October study called "too small to matter".
    - It is the same family as K and the phase-2 EWMA.
- **The 50/50 stays.** No bounded recent-weight adjustment beats it.

## Summary

| Experiment | Champion WAPE | Candidate WAPE | Difference | 95% interval | Bias change | Affected pickups | Verdict |
|---|---:|---:|---:|---|---|---:|---|
| A1 open days, loose calendar (181–365 returns only) | 60.2% | 48.9% | −11.30 | −15.25 to −6.64 | −56.9% → −36.7% | 121 | Supported; prospective test |
| A1 open days, every pickup | 33.6% | 33.7% | +0.14 | +0.03 to +0.27 | −1.7% → −1.5% | 523 fired | Rejected as a general rule |
| A2 pre-gap rate (a) | 51.3% | 57.8% | +6.55 | −1.13 to +14.17 | −23.4% → −26.8% | 542 | Rejected |
| A2 pre-gap rate (b, established/new) | 58.7% | 59.8% | +1.10 | −9.03 to +11.81 | −33.2% → −16.8% | 405 | Rejected |
| A3 blend w = 0.5 (a) | 51.3% | 51.3% | +0.05 | −3.68 to +3.83 | −23.4% → −25.2% | 542 | Null |
| A3 blend w = 0.5 (b) | 58.7% | 56.1% | −2.58 | −7.75 to +2.72 | −33.2% → −25.0% | 405 | Inconclusive |
| A4 carryover, 25% of median pickup (on A0) | 58.8% | 60.5% | +1.71 | +1.29 to +2.08 | −51.0% → −53.9% | 320 | Rejected; no carryover |
| C1 w = 0.60 | 31.7% | 31.8% | +0.03 | −0.02 to +0.08 | −3.2% → −3.0% | 14,771 | Null |
| C2 span reliability | 31.7% | 31.8% | +0.03 | −0.01 to +0.07 | −3.2% → −3.3% | 14,771 | Null |
| C3 shift detector (k = 2, T = 0.15) | 31.7% | 31.7% | +0.01 | −0.02 to +0.04 | −3.2% → −3.0% | 2,696 fired | Null |
| C4 local level D = 90, ½ with champion | 31.7% | 31.3% | −0.44 | −0.56 to −0.34 | −3.2% → −2.5% | 14,771 | Supported, small; prospective test |

The cutoff is about coverage, not WAPE:

| Cutoff × hold | Pickups withheld | Their WAPE / bias | vs production | Flagged weeks hidden (pickup ≤ 30 d) | Verdict |
|---|---:|---|---|---|---|
| 270 × 3 | 176 | 69% / −61% | +90 held | 171 (30) | More conservative, hides more |
| 300 × 3 | 121 | 74% / −72% | +35 held | 105 (17) | Defensible alternative |
| **365 × 3 (production)** | **86** | **82% / −80%** | – | **23 (6)** | **Keep** |
| 450 × 3 | 71 | 82% / −81% | −15 released | 18 (6) | Releases bad projections |
| 365 × 1 (no hold) | 0 | – | −86 released | 0 | The pre-10-15 ladder |

## Recommendations (ranked; none implemented)

1. **A1 for 181–365-day returns only: prospective test.**
   - **The rule.** For R2 and R3 after a 181–365-day gap, where earlier years show a closed block, compute the stage rate on open days only.
   - **Why it ranks first.** It is the strongest out-of-sample result, with a clear physical meaning (don't count days the business was shut) and a narrow scope.
   - **Risks.**
     - It still under-projects by about a third.
     - It is weaker for winter businesses.
     - The loose calendar is inferred, and Jim's operating dates (the open question in the roadmap) would be better.
     - Its scope (A1 only after a return) was declared before the run. The decision to keep it out of non-returns rests on the +0.14 whole-set cost.
   - **Next.** Shadow-score it in the live test through the 2027 spring reopenings before any adoption.
2. **C4 local level (D = 90, ½ with the 50/50): prospective test.**
   - **Why.** It is small but consistent (8/8 years), simple, and has bounded harm (163 customers worse).
   - **Risks.**
     - D sits at the grid edge.
     - The gain may not justify an extra rate in the ladder.
   - **Next.** Freeze D = 90 and score it on 2027 pickups.
3. **Keep the 365-day comeback cutoff and the 50/50 weights.** C1–C3 confirm that the 50/50 already tracks persistent shifts.

Not recommended:
- A2;
- A3;
- any carryover allowance;
- a fixed recent weight above ½;
- the shift detector;
- moving the cutoff.

## Repository status

- **Branch.** `fall-index-not-adopted` (= `main` 90075bf plus one uncommitted line in `analysis/README.md`, which predates this study). Nothing was committed, pushed or merged.
- **New files, all under `analysis/comeback_level_shift_research/`:**
  - code: `champion.py`, `common.py`, `exp_comeback.py`, `exp_cutoff.py`, `exp_level_shift.py`, `run.py`;
  - reports: `README.md`, `recommendations.md`, `comeback_181_365.md`, `comeback_cutoff.md`, `level_shift.md`;
  - data: `experiment_results.csv` (207 rows), `affected_customers.csv` (2,571 rows), `events_A.csv` (613 events), `results_{A,B,C}.csv`, `impacts_{A,C}.csv`;
  - `__pycache__/`, which is gitignored.
- **Changed tracked files: none.** `git diff --stat` shows only the earlier `analysis/README.md | 1 +`.
- **Checks.**
  - `python3 validate_data.py`: 116 pass, 2 warnings (the standing two), 0 fail.
  - `node tests/run_all.js`: 6 of 7 suites pass. `seasonality_test` fails, as it has on `main` since 2026-10-01 (drifted fixed expectations, ROADMAP "Small fixes").
  - `champion.py --check`: 784 / 784 agree.
  - `run.py` re-run: every md and csv is byte-identical.
- **Replay cache:** in the session scratchpad, outside the repo.
