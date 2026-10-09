#!/usr/bin/env python3
"""
Live test of the pickup projections: what the page projected vs what the truck
collected, pickup by pickup.

PREDICTIONS ARE FROZEN. Each pickup is scored against the projection the site
was serving before that pickup appeared in the data -- never a recompute -- so a
later model change cannot rewrite the record. Actual gallons are re-read from
the scrape on every run, so a correction on the source site flows through.

How one served oil_projections.json scores a pickup:
  * For each customer row, the FIRST pickup dated after the row's last_pickup
    is the one that projection was predicting. A shared barrel (a row with
    `members`) is its members' gallons summed by date, as the builder does.
  * A ranked row's projection is moved from the file's as_of to the pickup
    date at its own rate (projected + rate x days); the range is scaled by the
    same factor, since the bands are multiplicative on the projection. A pickup
    entered late, dated before as_of, is wound back the same way.
  * A held-out row (will-call, stale, seasonal holdout, ...) is logged as not
    projected, so nothing disappears from the record.
  * A customer missing from the file (a first-ever pickup, an inactive
    account) had no projection to test and is not logged.

Run modes:
  python3 analysis/live_test.py             nightly: score the oil_projections.json
                                            on disk. Run AFTER the scrape and
                                            BEFORE the projection build, so the
                                            file is still the one that was served.
  python3 analysis/live_test.py --backfill  one-off: walk every served version in
                                            git (first-parent main), newest first,
                                            so each pickup is scored by the latest
                                            version that did not yet contain it.

Writes analysis/live_test_log.csv (the record) and oil_live_test.json (what
projections.html displays). No wall-clock timestamps: a rerun on unchanged data
writes identical files.
"""

import argparse
import csv
import json
import statistics
import subprocess
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import backtest_steady_rate as bt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECTIONS = ROOT / "oil_projections.json"
OIL_DATA = ROOT / "oil_data.json"
LOG_CSV = HERE / "live_test_log.csv"
OUT_JSON = ROOT / "oil_live_test.json"

SECTION_RANKED = "ranked"

# The record, in column order. The prediction columns are frozen once written;
# actual / error / in_range are refreshed from the scrape on every run.
LOG_FIELDS = [
    "pickup_date", "customer_id", "member_ids", "customer", "town", "stage",
    "status", "section", "previous_pickup", "predicted_as_of", "prediction_data",
    "projected", "range_low", "range_high", "actual", "error", "in_range",
]
FROZEN = LOG_FIELDS[:LOG_FIELDS.index("actual")]

# Summary windows, counted back from the latest scored pickup date (not the
# wall clock, so a rerun is identical).
WINDOWS = [("Last 7 days", 7), ("Last 30 days", 30), ("All", None)]


# ─────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────

def gallons_by_date():
    """customer_id -> {date: gallons that day}, same-day pickups summed."""
    out = defaultdict(lambda: defaultdict(int))
    for cid, pickups in bt.load_pickups(ids=None).items():
        for p in pickups:
            out[cid][p["date"]] += p["gallons"]
    return out


def member_ids(row):
    """The account ids whose gallons make up this row: a shared barrel's members."""
    if row.get("members"):
        return sorted(m["id"] for m in row["members"])
    return [row["id"]]


def barrel_gallons(gallons, ids):
    """{date: gallons} summed across the accounts in `ids`."""
    total = defaultdict(int)
    for cid in ids:
        for day, gal in gallons.get(cid, {}).items():
            total[day] += gal
    return total


def served_versions():
    """Every oil_projections.json the site served, newest first, as parsed dicts."""
    commits = subprocess.run(
        ["git", "log", "--first-parent", "--format=%H", "main", "--",
         PROJECTIONS.name],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for sha in commits:
        text = subprocess.run(["git", "show", f"{sha}:{PROJECTIONS.name}"],
                              cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout
        yield json.loads(text)


# ─────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────

def score_version(payload, gallons, log):
    """
    Add to `log` (key -> record) every pickup this served version predicted
    that is not logged yet. Returns how many were added.
    """
    as_of = date.fromisoformat(payload["as_of"])
    added = 0
    for row in payload["customers"]:
        ids = member_ids(row)
        days = barrel_gallons(gallons, ids)
        if row.get("last_pickup"):
            last = date.fromisoformat(row["last_pickup"])
            later = sorted(d for d in days if d > last)
        elif row.get("history_start"):
            # A new owner with no pickup yet: their first one, from the start.
            start = date.fromisoformat(row["history_start"])
            later = sorted(d for d in days if d >= start)
        else:
            continue
        if not later:
            continue
        day = later[0]
        key = (day.isoformat(), row["id"])
        if key in log:
            continue

        projected = low = high = None
        if row["section"] == SECTION_RANKED and row.get("projected_gal") is not None:
            base = row["projected_gal"]
            projected = max(0.0, base + (row.get("rate_gpd") or 0) * (day - as_of).days)
            if base > 0 and row.get("range_low") is not None:
                factor = projected / base
                low, high = row["range_low"] * factor, row["range_high"] * factor

        log[key] = {
            "pickup_date": day.isoformat(),
            "customer_id": row["id"],
            "member_ids": ";".join(str(i) for i in ids),
            "customer": row["name"],
            "town": row.get("town") or "",
            # Versions before the ladder (2026-10-08) carry a status, no stage.
            "stage": row.get("stage") or "",
            "status": row.get("status") or "",
            "section": row["section"],
            "previous_pickup": row["last_pickup"],
            "predicted_as_of": payload["as_of"],
            "prediction_data": (payload.get("data_last_updated") or "")[:10],
            "projected": _round(projected),
            "range_low": _round(low),
            "range_high": _round(high),
        }
        added += 1
    return added


def refresh_actuals(log, gallons):
    """Re-read each logged pickup's gallons from the scrape; recompute the error."""
    for rec in log.values():
        ids = [int(i) for i in str(rec["member_ids"]).split(";")]
        actual = barrel_gallons(gallons, ids).get(date.fromisoformat(rec["pickup_date"]))
        rec["actual"] = actual
        projected = rec["projected"]
        if actual is None or projected in (None, ""):
            rec["error"] = rec["in_range"] = None
            continue
        rec["error"] = _round(float(projected) - actual)
        low, high = rec["range_low"], rec["range_high"]
        rec["in_range"] = (None if low in (None, "") else
                           "yes" if float(low) <= actual <= float(high) else "no")


def _round(value):
    return None if value is None else round(value, 1)


# ─────────────────────────────────────────────
# The record
# ─────────────────────────────────────────────

def load_log():
    """key -> record, keeping only the frozen prediction columns."""
    log = {}
    if not LOG_CSV.exists():
        return log
    with open(LOG_CSV, newline="") as f:
        for row in csv.DictReader(f):
            rec = {k: row[k] for k in FROZEN}
            rec["customer_id"] = int(rec["customer_id"])
            for k in ("projected", "range_low", "range_high"):
                rec[k] = float(rec[k]) if rec[k] != "" else None
            log[(rec["pickup_date"], rec["customer_id"])] = rec
    return log


def write_log(log):
    with open(LOG_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        writer.writeheader()
        for key in sorted(log):
            rec = log[key]
            writer.writerow({k: "" if rec.get(k) is None else rec[k] for k in LOG_FIELDS})


# ─────────────────────────────────────────────
# What the page shows
# ─────────────────────────────────────────────

def metrics(records):
    """WAPE, bias, share in range and median miss over scored records."""
    scored = [r for r in records if r["error"] is not None]
    actual = sum(r["actual"] for r in scored)
    ranged = [r for r in scored if r["in_range"] is not None]
    return {
        "scored": len(scored),
        "not_projected": sum(1 for r in records if r["projected"] is None
                             and r["actual"] is not None),
        "wape": round(100 * sum(abs(r["error"]) for r in scored) / actual, 1) if actual else None,
        "bias": round(100 * sum(r["error"] for r in scored) / actual, 1) if actual else None,
        "in_range": round(100 * sum(r["in_range"] == "yes" for r in ranged) / len(ranged), 1)
                    if ranged else None,
        "median_miss": round(statistics.median(abs(r["error"]) for r in scored), 1)
                       if scored else None,
    }


def write_json(log):
    records = [log[k] for k in sorted(log)]
    present = [r for r in records if r["actual"] is not None]
    latest = max((date.fromisoformat(r["pickup_date"]) for r in present), default=None)

    summary = []
    for label, days in WINDOWS:
        start = latest - timedelta(days=days - 1) if (latest and days) else None
        rows = [r for r in present
                if start is None or date.fromisoformat(r["pickup_date"]) >= start]
        summary.append({"window": label, "from": start.isoformat() if start else None,
                        **metrics(rows)})

    # By stage only where the served version had stages (since the ladder).
    by_stage = defaultdict(list)
    for r in present:
        if r["projected"] is not None and r["stage"]:
            by_stage[r["stage"]].append(r)
    stages = [{"stage": s, **metrics(rs)} for s, rs in sorted(by_stage.items())]

    not_projected = defaultdict(int)
    for r in present:
        if r["projected"] is None:
            not_projected[r["section"]] += 1

    days = defaultdict(list)
    for r in present:
        days[r["pickup_date"]].append({
            "id": r["customer_id"], "name": r["customer"], "town": r["town"],
            "stage": r["stage"] or r["status"], "section": r["section"],
            "previous_pickup": r["previous_pickup"],
            "predicted_as_of": r["predicted_as_of"],
            "projected": r["projected"], "range_low": r["range_low"],
            "range_high": r["range_high"], "actual": r["actual"],
            "error": r["error"], "in_range": r["in_range"],
        })
    # Within a day: projected rows first, then by name.
    by_day = defaultdict(list)
    for r in present:
        by_day[r["pickup_date"]].append(r)
    day_list = [{"date": d, **metrics(by_day[d]),
                 "rows": sorted(rows, key=lambda x: (x["projected"] is None,
                                                     x["name"].lower()))}
                for d, rows in sorted(days.items(), reverse=True)]

    oil_data = json.loads(OIL_DATA.read_text())
    payload = {
        "data_last_updated": oil_data.get("last_updated"),
        "latest_pickup": latest.isoformat() if latest else None,
        "summary": summary,
        "by_stage": stages,
        "not_projected_by_section": dict(sorted(not_projected.items())),
        "days": day_list,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backfill", action="store_true",
                        help="score every served version in git, newest first")
    args = parser.parse_args()

    gallons = gallons_by_date()
    log = load_log()
    before = len(log)

    if args.backfill:
        for payload in served_versions():
            score_version(payload, gallons, log)
    else:
        score_version(json.loads(PROJECTIONS.read_text()), gallons, log)

    refresh_actuals(log, gallons)
    write_log(log)
    write_json(log)
    print(f"Live test: {len(log) - before} new pickup(s) scored, {len(log)} in the log. "
          f"Wrote {LOG_CSV.name} and {OUT_JSON.name}")


if __name__ == "__main__":
    main()
