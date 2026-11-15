#!/usr/bin/env python3
"""
Failure alerts in the nightly (.github/workflows/nightly-update.yml), no
network: each step's own shell commands are run with `python` failing or
succeeding. Run from the repo root:

    python3 tests/nightly_alerts_test.py

A step that matters after the cutover still carries on when it fails, but
records the failure; the last step, after the commit, fails the run if any
did (GitHub emails); a step reading the source site only warns; and a night
with no failures passes.
"""

import os
import re
import subprocess
import sys
import tempfile

os.chdir(os.path.join(os.path.dirname(__file__), ".."))

fails = 0


def ok(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


ALERTS = ["Export customer page edits", "Export site collections", "Score live test", "Export Oil Left",
          "Export thefts", "Build projections", "Place pins for edited addresses",
          "Clear site statuses ended by a pickup"]
SOURCE_SITE = []   # since the cutover no step reads the source site (CUTOVER.md)


def steps(path=".github/workflows/nightly-update.yml"):
    """[(name, run script)] in order, from the workflow's own text."""
    out = []
    for block in re.split(r"\n      - name: ", open(path).read())[1:]:
        name = block.split("\n", 1)[0].strip()
        m = re.search(r"\n        run: \|\n((?:          .*\n?|\s*\n)+)", block)
        script = "\n".join(l[10:] for l in m.group(1).splitlines()) if m else ""
        out.append((name, script, "if: always()" in block))
    return out


def run(script, tmp, python_ok):
    """The step's commands under bash, `python` replaced by a stand-in that succeeds or fails."""
    stub = os.path.join(tmp, "bin")
    os.makedirs(stub, exist_ok=True)
    with open(os.path.join(stub, "python"), "w") as fh:
        fh.write("#!/bin/sh\nexit %d\n" % (0 if python_ok else 1))
    os.chmod(os.path.join(stub, "python"), 0o755)
    env = dict(os.environ, RUNNER_TEMP=tmp, PATH=stub + os.pathsep + os.environ["PATH"])
    return subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)


all_steps = steps()
by_name = {n: (s, a) for n, s, a in all_steps}
names = [n for n, _, _ in all_steps]

print("The workflow")
ok(set(ALERTS) | set(SOURCE_SITE) <= set(by_name), "every step named here is in the nightly")
ok(not re.search(r"jamaal_|--rescrape|--routes|--sweep|supabase_sync\.py \|\|", open(".github/workflows/nightly-update.yml").read()),
   "since the cutover nothing in the nightly reads the source site or mirrors it")
ok("--export-collections --since \"$CUTOVER_SINCE\"" in by_name["Export site collections"][0]
   and names.index("Export site collections") < names.index("Rebuild dashboard data") < names.index("Score live test")
   < names.index("Build projections"),
   "the site's pickups are exported, then the dashboard rebuilt, then the live test, then the projections")
ok(names[-1] == "Report failed steps" and names[-2] == "Commit updated data files" and by_name["Report failed steps"][1],
   "the report is the last step, after the commit, and always runs")
warning_steps = {n for n, s, _ in all_steps if "::warning::" in s}
ok(warning_steps == set(ALERTS) | set(SOURCE_SITE),
   f"every step that carries on is either alerting or reads the source site ({sorted(warning_steps - set(ALERTS) - set(SOURCE_SITE))})")

print("A step fails")
for name in ALERTS:
    with tempfile.TemporaryDirectory() as tmp:
        r = run(by_name[name][0], tmp, python_ok=False)
        log = os.path.join(tmp, "nightly-failures.txt")
        recorded = open(log).read() if os.path.exists(log) else ""
        ok(r.returncode == 0 and recorded.startswith(f"{name}: ") and "::warning::" in r.stdout,
           f"{name}: carries on, warns, and records it")
for name in SOURCE_SITE:
    with tempfile.TemporaryDirectory() as tmp:
        script = by_name[name][0].replace('"$(TZ=America/New_York date +%u)" = 7', "7 = 7")   # as on a Sunday
        r = run(script, tmp, python_ok=False)
        ok(r.returncode == 0 and not os.path.exists(os.path.join(tmp, "nightly-failures.txt")),
           f"{name}: warns only (switched off at the cutover)")

print("The report")
report = by_name["Report failed steps"][0]
with tempfile.TemporaryDirectory() as tmp:
    for name in ("Export Oil Left", "Build projections"):
        run(by_name[name][0], tmp, python_ok=False)
    r = run(report, tmp, python_ok=True)
    ok(r.returncode == 1 and r.stdout.count("::error::") == 2 and "::error::Export Oil Left: Oil Left export failed" in r.stdout,
       "two failures: the run fails (GitHub emails) and names both")
with tempfile.TemporaryDirectory() as tmp:
    for name in ALERTS:
        run(by_name[name][0], tmp, python_ok=True)
    r = run(report, tmp, python_ok=True)
    ok(r.returncode == 0 and "::error::" not in r.stdout, "a night with no failures passes, no email")

print(f"\n{'All passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
