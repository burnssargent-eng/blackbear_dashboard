#!/usr/bin/env python3
"""
Seasonal-closed detector: find customers that shut for part of the year,
from prior complete years only, and test what to do with them.

Three questions:

  A. DETECTION. From the k complete years before test year Y (k = 1, 2, 3;
     2020 skipped), does a closed block predict the months that are actually
     closed in Y? A detector that fires after ONE year finds a new closer a
     year in; three years is the registry's standard.
  B. SAFETY. A detected closer is held out of the ranking in its closed
     months. How often does a real pickup land in a month we would have held
     out? That is the costly error.
  C. IN-SEASON RATE. For detected closers, pickups whose whole gap lies in
     open months: today's 50/50 (which averages in the closed months) vs
     level x month index vs a pooled rate over open-month gaps only.

THE SHAPE KEEPS ZEROS. cf.factor_from_years leaves a month with no
production out of its average and sets an all-zero month to 1.0, which hides
a closure (Mount Ellen's shut Jun-Oct reads 0.69, not 0). Here a month's index
is the plain mean across years, zeros included, renormalised to average 1.

Choose = test years 2022-23, confirm = 2024-25 (complete years, so the
truth is a whole year); the in-season rate also scores 2026 pickups.

    python3 analysis/backtest_seasonal_closed.py

Writes analysis/seasonal_closed.md and backtest_seasonal_closed_detail.csv
(gitignored). Standard library only; deterministic.
"""

import csv
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import backtest_customer_factors as bcf
import backtest_phase2_rates as p2
import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import build_projection_table as bpt
import customer_factors as cf
import seasonal_open as so
import seasonality_score as ss

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "seasonal_closed.md"
OUT_DETAIL = HERE / "backtest_seasonal_closed_detail.csv"

YEAR = 365
SKIP_YEARS = {2020}
CHOOSE = (2022, 2023)
CONFIRM = (2024, 2025)
RATE_CONFIRM = (2024, 2025, 2026)
SEED = 42

# The grid, chosen on CHOOSE.
YEARS_BACK = (1, 2, 3)
CLOSED_INDEX = (0.10, 0.20)        # a month below this share of an average month is closed
MIN_CLOSED_MONTHS = (2, 3)         # the closed block must be at least this long

# Fixed guards, not tuned.
MIN_GAL_PER_YEAR = 300
MAX_MEDIAN_GAP = ss.SPREAD_CAP_DAYS  # gaps longer than the spread cap leave
                                     # artificial empty months behind them
MIN_PRIOR_PICKUPS = 8

NOT_DAILY = {bpt.STATUS_EVENT, bpt.STATUS_ON_DEMAND, bpt.STATUS_LUMP_SUM}
REGISTRY_CLOSERS = {bpt.STATUS_TRUE_CLOSER, bpt.STATUS_SEMI_CLOSER}


# ─────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────

def load():
    pickups = bt.load_pickups(ids=None)
    checks = bpt.load_empty_checks(date.max)
    active = bpt.load_active_ids()
    bpt.apply_history_starts(pickups, checks)
    bpt.combine_shared(pickups, checks, {}, defaultdict(list), active)
    excluded = {cid for cid, o in bpt.MODEL_OVERRIDES.items() if o.status in NOT_DAILY}
    return {cid: ps for cid, ps in pickups.items()
            if cid not in excluded and len(ps) >= 2}, checks


prior_years = so.profile_years_back
shape = so.zero_keeping_shape
closed_block = so.closed_block


def months_label(months):
    if not months:
        return "–"
    ms = sorted(months)
    # Start at the month after a non-member, so a wrapping block reads Nov-Mar.
    start = next(m for m in ms if (m - 1) % 12 not in months)
    end = (start + len(ms) - 1) % 12
    return f"{ss.MONTHS[start]}–{ss.MONTHS[end]}"


class Customer:
    """Daily production and yearly profiles, built once."""

    def __init__(self, cid, ps):
        self.cid, self.ps = cid, ps
        self.daily = ss.daily_production(ps)
        self._detected = {}
        self.profile = {}
        for y in range(ps[0]["date"].year, 2026):
            idx = cf.year_index(self.daily, y)
            if idx is not None:
                self.profile[y] = idx

    def eligible(self, years):
        """Guards: enough volume and pickups often enough for the spread."""
        span = [p for p in self.ps if p["date"].year in years]
        if len(span) < MIN_PRIOR_PICKUPS / 2 or any(y not in self.profile for y in years):
            return False
        if sum(p["gallons"] for p in span) / len(years) < MIN_GAL_PER_YEAR:
            return False
        gaps = [(b["date"] - a["date"]).days for a, b in zip(span, span[1:])]
        gaps = [g for g in gaps if g > 0]
        # A closer's off-season gap is one per year; the median sees through it.
        return bool(gaps) and statistics.median(gaps) <= MAX_MEDIAN_GAP

    def detect(self, test_year, k, threshold, min_months):
        """seasonal_open.closed_months: pickups before Jan 1 of the year only."""
        key = (test_year, k, threshold, min_months)
        if key not in self._detected:
            self._detected[key] = so.closed_months(self.ps, test_year, k,
                                                   threshold, min_months)
        return self._detected[key]

    def truth(self, year, threshold, min_months):
        """Closed months actually seen in `year` (needs the year's production)."""
        if year not in self.profile or not self.eligible([year]):
            return None
        return closed_block(self.profile[year], threshold, min_months)


# ─────────────────────────────────────────────
# A + B: detection and safety
# ─────────────────────────────────────────────

def score_detector(customers, years, k, threshold, min_months):
    tp = fp = fn = tn = 0
    held_pickups = held_gal = total_gal = 0
    month_tp = month_pred = 0
    flagged = set()
    for c in customers.values():
        for y in years:
            pred = c.detect(y, k, threshold, min_months)
            truth = c.truth(y, threshold, min_months)
            if pred is None or truth is None:
                continue
            if pred and truth:
                tp += 1
            elif pred:
                fp += 1
            elif truth:
                fn += 1
            else:
                tn += 1
            if pred:
                flagged.add(c.cid)
                month_pred += len(pred)
                month_tp += len(pred & truth)
                for p in c.ps:
                    if p["date"].year == y:
                        total_gal += p["gallons"]
                        if p["date"].month - 1 in pred:
                            held_pickups += 1
                            held_gal += p["gallons"]
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if tp else 0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision,
            "recall": recall, "f1": f1, "flagged": flagged,
            "month_precision": month_tp / month_pred if month_pred else None,
            "held_pickups": held_pickups,
            "held_share": held_gal / total_gal * 100 if total_gal else None}


# ─────────────────────────────────────────────
# C: in-season rate
# ─────────────────────────────────────────────

def in_season_rates(c, k, closed, sh, start):
    """50/50, level x index, and pooled over open-month gaps, for pickup k."""
    ps = c.ps
    last6 = bsm.rate_span(ps, k - 7, k - 1) if k >= 7 else None
    prev_year, _ = bt.rate_prev_year(ps, k, YEAR)
    base = p2.blend(last6, 0.5, prev_year)

    _, _, level, _ = bcf.base_rates(ps, k)
    level_index = level * cf.gap_weighted(sh, start, ps[k]["date"]) if level else None

    gal = days = 0.0
    used = 0
    for i in range(k - 1, 0, -1):
        a, b = ps[i - 1]["date"], ps[i]["date"]
        if spans_closed(a, b, closed):
            continue
        gal += ps[i]["gallons"]
        days += (b - a).days
        used += 1
        if used == 6:
            break
    open_pooled = gal / days if days > 0 and used >= 3 else None
    return {"50/50 (today)": base, "Level × month index": level_index,
            "Pooled, open-season gaps": open_pooled}


def spans_closed(a, b, closed):
    d = a + timedelta(days=1)
    while d <= b:
        if d.month - 1 in closed:
            return True
        d += timedelta(days=1)
    return False


def rate_tests(customers, checks, k, threshold, min_months):
    tests = []
    for c in customers.values():
        for i in range(MIN_PRIOR_PICKUPS, len(c.ps)):
            x = c.ps[i]
            y = x["date"].year
            if y not in CHOOSE + RATE_CONFIRM or x["gallons"] <= 0:
                continue
            closed = c.detect(y, k, threshold, min_months)
            if not closed:
                continue
            start = p2.clock_start(c.ps, i, checks.get(c.cid, []))
            if spans_closed(start, x["date"], closed):
                continue                    # production holds these out
            days = (x["date"] - start).days
            if days <= 0:
                continue
            sh = shape(c.profile, prior_years(y, k))
            rates = in_season_rates(c, i, closed, sh, start)
            if rates["50/50 (today)"] is None:
                continue
            tests.append({"customer_id": c.cid, "name": x["name"], "date": x["date"],
                          "period": "choose" if y in CHOOSE else "confirm",
                          "days": days, "actual": x["gallons"], "rates": rates})
    return tests


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def main():
    pickups, checks = load()
    customers = {cid: Customer(cid, ps) for cid, ps in pickups.items()}
    out = []
    w = out.append

    w("# Seasonal-closed detector\n")
    w("Generated by `analysis/backtest_seasonal_closed.py`. A customer is a "
      "**detected closer** for test year Y when the k complete years before Y "
      "(2020 skipped) average to a shape with a cyclic block of at least M months "
      "below T of an average month. **The shape keeps zero months as zero**: "
      "`cf.factor_from_years` sets a month with no production in any year to 1.0, "
      "which hides a closure. Guards, not tuned: at least "
      f"{MIN_GAL_PER_YEAR} gal/yr and a median gap of at most {MAX_MEDIAN_GAP} days "
      "(longer gaps leave artificial empty months behind the 60-day spread). "
      "**Truth** = the same block test on year Y itself. Choose = 2022–23, "
      "confirm = 2024–25.\n")

    # A + B ------------------------------------------------------------
    w("## 1. Detection and safety\n")
    w("Customer-years: a hit is a detected closer that is closed in Y. **Months "
      "right** = share of predicted-closed months that were closed in Y. **Pickups "
      "held out** = real pickups in Y that fell in a predicted-closed month — the "
      "costly error — and their share of those customers' gallons.\n")
    w("| k years | T | M | Choose F1 | Confirm: precision | recall | F1 | months right | pickups held out | gallons held out |")
    w("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    best = None
    grid = [(k, t, m) for k in YEARS_BACK for t in CLOSED_INDEX for m in MIN_CLOSED_MONTHS]
    results = {}
    for k, t, m in grid:
        a = score_detector(customers, CHOOSE, k, t, m)
        b = score_detector(customers, CONFIRM, k, t, m)
        results[(k, t, m)] = (a, b)
        mp = f"{b['month_precision'] * 100:.0f}%" if b["month_precision"] is not None else "–"
        hs = f"{b['held_share']:.1f}%" if b["held_share"] is not None else "–"
        w(f"| {k} | {t} | {m} | {a['f1']:.2f} | {b['precision']:.0%} | {b['recall']:.0%} | "
          f"{b['f1']:.2f} | {mp} | {b['held_pickups']} | {hs} |")
        if best is None or a["f1"] > results[best][0]["f1"]:
            best = (k, t, m)
    w("")
    k, t, m = best
    w(f"**Chosen on 2022–23: k = {k}, T = {t}, M = {m}.**\n")

    # Speed: the best variant at each k, for newcomers.
    w("Best setting at each number of years, so the speed/accuracy trade is visible:\n")
    w("| k years | Setting | Choose F1 | Confirm F1 | Confirm pickups held out |")
    w("|---:|---|---:|---:|---:|")
    for kk in YEARS_BACK:
        cand = max((g for g in grid if g[0] == kk), key=lambda g: results[g][0]["f1"])
        a, b = results[cand]
        w(f"| {kk} | T {cand[1]}, M {cand[2]} | {a['f1']:.2f} | {b['f1']:.2f} | {b['held_pickups']} |")
    w("")

    # Registry comparison, as of the current year.
    names = {cid: ps[-1]["name"] for cid, ps in pickups.items()}
    now = {cid: customers[cid].detect(2026, k, t, m) for cid in customers}
    detected = {cid for cid, v in now.items() if v}
    registry = {cid for cid, o in bpt.MODEL_OVERRIDES.items() if o.status in REGISTRY_CLOSERS}
    active = bpt.load_active_ids()
    w("## 2. Against the registry, scored for 2026\n")
    w(f"The registry holds {len(registry)} closers (true + semi). The chosen detector, "
      "scored from the years before 2026:\n")
    w("| Customer | In registry | Detected closed months | Registry season | Active |")
    w("|---|---|---|---|---|")
    for cid in sorted(registry | detected, key=lambda c: names.get(c, "")):
        o = bpt.MODEL_OVERRIDES.get(cid)
        verdict = months_label(now.get(cid)) if now.get(cid) else (
            "not decidable" if now.get(cid) is None else "not detected")
        w(f"| {names.get(cid, cid)} ({cid}) | {'yes, ' + o.status if cid in registry else 'no'} | "
          f"{verdict} | {o.season if o and o.season else '–'} | {'yes' if cid in active else 'no'} |")
    w("")

    # C ---------------------------------------------------------------
    tests = rate_tests(customers, checks, k, t, m)
    w("## 3. In-season rate for detected closers\n")
    w("Pickups whose whole gap lies in predicted-open months (a gap across the "
      "closure is held out in production). Difference = model minus today's 50/50 "
      "in WAPE points; negative is better.\n")
    w(p2.HEADER)
    rng = random.Random(SEED)
    for period in ("choose", "confirm"):
        rows = [r for r in tests if r["period"] == period]
        w(f"| **{period}** | | | | | | |")
        for model in ("Level × month index", "Pooled, open-season gaps"):
            w(p2.fmt_row(model, p2.compare(rows, model, rng=rng)))
    w("")

    model = "Pooled, open-season gaps"
    w(f"**Likely range for the {model.lower()} rate**: the 20th and 80th percentiles "
      "of actual ÷ projected on choose pickups, coverage on confirm.\n")

    def ratios(rows):
        return sorted(r["actual"] / (r["rates"][model] * r["days"])
                      for r in rows if r["rates"][model])
    rc = ratios([r for r in tests if r["period"] == "choose"])
    rf = ratios([r for r in tests if r["period"] == "confirm"])
    lo, hi = rc[int(0.2 * len(rc))], rc[int(0.8 * len(rc))]
    inside = sum(lo <= v <= hi for v in rf) / len(rf) * 100
    under = sum(v <= hi for v in rf) / len(rf) * 100
    w(f"Factors × {lo:.2f} – × {hi:.2f}; confirm: {inside:.0f}% inside (target 60%), "
      f"{under:.0f}% at or under the high end (target 80%), {len(rf)} pickups.\n")

    OUT_REPORT.write_text("\n".join(out) + "\n")
    with open(OUT_DETAIL, "w", newline="") as f:
        wr = csv.writer(f)
        models = ["50/50 (today)", "Level × month index", "Pooled, open-season gaps"]
        wr.writerow(["customer_id", "name", "date", "period", "days", "actual"] + models)
        for r in tests:
            wr.writerow([r["customer_id"], r["name"], r["date"], r["period"], r["days"],
                         r["actual"]] + [None if r["rates"][mm] is None
                                         else round(r["rates"][mm], 4) for mm in models])
    print(f"wrote {OUT_REPORT.name}: chosen k={k} T={t} M={m}; {len(tests)} in-season pickups")


if __name__ == "__main__":
    main()
