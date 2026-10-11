#!/usr/bin/env python3
"""
Run the whole study and merge its outputs. Research only: writes nothing
outside this folder except the replay cache (RESEARCH_CACHE, default /tmp).

    cd analysis/model_research_2026_10
    RESEARCH_CACHE=/path/to/cache.pkl python3 run_all.py [--rebuild] [--check]

--rebuild re-walks the champion replay (a few minutes); --check also compares
the replay with build_row at the live edge. Every output is deterministic
(fixed seeds), so a re-run is byte-identical.
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = ["exp_seasonal.py", "exp_closer.py", "exp_inventory.py", "exp_reopen.py",
           "exp_established.py", "exp_shape.py"]
RESULTS = ["results_C.csv", "results_G.csv", "results_J.csv", "results_H.csv", "results_I.csv",
           "results_ABK.csv", "results_DEF.csv"]
IMPACTS = ["impacts_C.csv", "impacts_HI.csv", "impacts_ABK.csv", "impacts_DEF.csv"]


def merge(names, out):
    rows, fields = [], None
    for n in names:
        with open(HERE / n, newline="") as f:
            r = csv.DictReader(f)
            fields = fields or r.fieldnames
            rows += list(r)
    with open(HERE / out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    replay_args = [a for a, on in (("--rebuild", args.rebuild), ("--check", args.check)) if on]
    for script in ["replay.py", *SCRIPTS]:
        print("running", script, flush=True)
        subprocess.run([sys.executable, script, *(replay_args if script == "replay.py" else [])],
                       cwd=HERE, check=True)
    print("experiment_results.csv:", merge(RESULTS, "experiment_results.csv"), "rows")
    print("customer_impacts.csv:", merge(IMPACTS, "customer_impacts.csv"), "rows")


if __name__ == "__main__":
    main()
