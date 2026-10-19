# Comeback gaps, comeback cutoff, production-level shifts (October 2026)

Research only. Three questions about the October 15 ladder, the champion on `main` at 90075bf:

1. Can predictions improve for customers back after a 181–365-day gap?
2. Is the 365-day comeback-hold cutoff well placed?
3. Can persistent production-level shifts be told from pickup noise?

The study itself changed nothing in production. **Afterwards Sarge's return rule was backtested here (`return_last_year.md`), guarded after two bad live rows (`return_guards.md`), and adopted on 2026-10-18 as the *return* stage, variant V2** (branch `return-last-year`).

**Start with `recommendations.md`.**

## Method

- **Replay.** `champion.py` replays the Oct-15 ladder at every historical pickup, from what was known that morning:
  - earlier pickups and earlier empty checks;
  - seasons and indices from earlier complete years only.

  It extends `model_research_2026_10/replay.py` (which replays f204e6b) with:
  - the **comeback hold**;
  - the **closer off-season** open-day exposure.

  `HISTORY_STARTS` and shared barrels come from today's config, as before.
- **Fidelity.** At the live edge the replay matches `build_row` on stage, section, rate and projected gallons for **784 of 784** customers. It holds the same 7 rows.
- **Not replayed: the capacity cap.** Capacity history starts 2026-06. The lead candidates are re-scored capped at today's capacities, labelled hindsight.
- **Not replayed: `MODEL_OVERRIDES`.** It is hindsight, so the core mode uses detected closers only.
- **Windows and scoring.**
  - Folds: dev 2021–23 (where parameters were chosen), confirm 2024–26, least-inspected 2018–19. 2020 is replayed but not pooled.
  - 2021–26 were inspected by earlier studies, so they are retrospective evidence, not untouched data.
  - Each comparison is candidate vs champion on identical pickups.
  - Intervals resample whole customers (2,000 draws, seed 42).
  - Every tuned grid is also reported with an expanding-window (rolling) choice and an odd→even choice.
- **Grids** were declared in each script's header before its first run.
  - One scope was added after Phase 1: A2/A3 restricted to the established/new stage, "(b)".
  - It was chosen with Sarge after seeing diagnostics that included 2024–26, and is labelled as such. The unrestricted version "(a)" is reported alongside.
- **Definitions.**
  - *Since* is the number of post-return pickups already in the history. Since 1 predicts R2.
  - The first pickup after a gap over 180 d (R1) is never projected (the row is stale), so "the first two pickups after a return" means R2 and R3.
- **Holdouts.** They are scored as coverage: the pickups withheld and the error their projection would have had. They are never treated as zero error.

## Files

| File | What |
|---|---|
| `recommendations.md` | Answers to the three questions, the summary table, ranked candidates, repo status |
| `comeback_181_365.md` | Experiments A and B1: gap distribution, return diagnostics, A1–A4 |
| `comeback_cutoff.md` | Experiment B2/B3: cutoff × hold-length sweep, route snapshots |
| `level_shift.md` | Experiment C1–C4 and the noise-vs-shift check |
| `experiment_results.csv` | Every result row: A, B (coverage) and C |
| `affected_customers.csv` | Per-customer champion vs candidate WAPE (A and C, dev and confirm) |
| `events_A.csv` | One row per 181–365 d return event (2017+), from R1 to R4: rates, routes, predictions |
| `results_{A,B,C}.csv`, `impacts_{A,C}.csv` | Per-experiment outputs that `run.py` merges |
| `return_last_year.md` | Sarge's return rule (same time last year for the 2nd pickup back, half recent for the 3rd): candidates, then as built |
| `exp_return_ly.py` | That backtest; its "as first built" section is V1, the ungated rule |
| `return_guards.md` | V0–V5b: reopenings only, open-day accrual, pre-gap rates for the rest, false-urgency snapshots. V2 is what the builder runs |
| `exp_return_guards.py` | That backtest. It asserts that `bpt.return_rate` reproduces V2 on every pickup and snapshot |
| `champion.py` | Oct-15 replay and `--check` fidelity. With the return stage in the builder, `--check` differs on exactly the return rows, by design |
| `common.py` | Return events, operating calendars, open-day rates, reporting helpers |
| `exp_comeback.py` | A + B1 |
| `exp_cutoff.py` | B2/B3 |
| `exp_level_shift.py` | C |
| `run.py` | Runs everything and merges the CSVs |

## Run

```
cd analysis/comeback_level_shift_research
RESEARCH_CACHE=/path/outside/repo/cache.pkl python3 run.py --rebuild --check   # ~1 min
RESEARCH_CACHE=/path/outside/repo/cache.pkl python3 run.py                     # reuses the cache
```

- The output is deterministic: two runs produce byte-identical files.
- The cache is a pickle kept outside the repo.
