# Oil-rate model research, October 2026

A research-only evaluation of eleven alternatives to the projection ladder (Experiments A–K) against the champion on `main` at f204e6b. The study changed nothing in production; its recommendation 1 (off-season calls on open days) was adopted afterwards.

**Start with `model_recommendations.md`.** It is the five-part report: audit, results, operational meaning, the 3 recommendations and repo status.

## Method in one paragraph

`replay.py` predicts every historical pickup the way the builder would have on that morning:
- it uses only earlier pickups, earlier empty checks, and seasons from prior complete years;
- it calls the builder's own functions, in `build_row`'s order;
- at the live edge it matches `build_row` on 782 of 782 customers.

Each experiment compares the champion and a candidate on identical pickups. Results are pooled and by year:
- 2018–19: least-inspected;
- 2021–23: dev, where parameters are tuned;
- 2024–26: confirm.

2020 is replayed but not pooled. The statistics:
- **WAPE** = Σ|error| ÷ Σ actual.
- **Intervals** resample whole customers (2,000 draws, seed 42).
- **Grids** are declared before any run.
- **Every tuned candidate** is also reported with a rolling (expanding-window) choice and an odd→even choice.

## Files

| File | What |
|---|---|
| `model_recommendations.md` | The report |
| `model_audit.md` | The champion's exact formulas, the replay, hindsight inputs, inspection status |
| `seasonal_exposure.md`, `closer_exposure.md`, `inventory_diagnostics.md`, `reopening.md` | Interpretive notes for C, G, J, H+I |
| `results_*.md` / `results_*.csv` | Generated tables per experiment group (ABK, C, DEF, G, H, I, J) |
| `experiment_results.csv`, `customer_impacts.csv` | All results and per-customer effects, merged |
| `replay.py`, `metrics.py`, `harness.py` | Champion replay, scoring, fold reporting |
| `exp_seasonal.py` (C), `exp_closer.py` (G), `exp_inventory.py` (J), `exp_reopen.py` (H, I), `exp_established.py` (A, B, K), `exp_shape.py` (D, E, F) | Experiments |
| `run_all.py` | Runs everything and merges the CSVs |

## Run

```
cd analysis/model_research_2026_10
RESEARCH_CACHE=/tmp/model_research_cache.pkl python3 run_all.py            # ~1.5 min with a cache
RESEARCH_CACHE=/tmp/model_research_cache.pkl python3 run_all.py --rebuild  # re-walk the replay first
python3 replay.py --check                                                  # fidelity vs build_row
```

The output is deterministic. The cache is a pickle outside the repo.
