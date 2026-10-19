#!/usr/bin/env python3
"""
Run the whole study and merge its outputs. Research only: writes nothing
outside this folder except the replay cache (RESEARCH_CACHE).

    cd analysis/comeback_level_shift_research
    RESEARCH_CACHE=/path/to/cache.pkl python3 run.py [--rebuild] [--check]

--rebuild re-walks the champion replay (~15 s); --check also compares it with
build_row at the live edge. Fixed seeds: a re-run is byte-identical.
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = ["exp_comeback.py", "exp_cutoff.py", "exp_level_shift.py", "exp_return_ly.py", "exp_return_guards.py"]
RESULTS = ["results_A.csv", "results_B.csv", "results_C.csv", "results_U.csv", "results_V.csv"]
IMPACTS = ["impacts_A.csv", "impacts_C.csv"]


def merge(names, out):
    rows, fields = [], []
    for n in names:
        with open(HERE / n, newline="") as f:
            r = csv.DictReader(f)
            fields += [x for x in r.fieldnames if x not in fields]
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
    flags = [a for a, on in (("--rebuild", args.rebuild), ("--check", args.check)) if on]
    for script in ["champion.py", *SCRIPTS]:
        print("running", script, flush=True)
        subprocess.run([sys.executable, script, *(flags if script == "champion.py" else [])],
                       cwd=HERE, check=True)
    print("experiment_results.csv:", merge(RESULTS, "experiment_results.csv"), "rows")
    print("affected_customers.csv:", merge(IMPACTS, "affected_customers.csv"), "rows")


if __name__ == "__main__":
    main()
