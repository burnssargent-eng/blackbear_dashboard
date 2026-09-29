#!/usr/bin/env python3
"""
Does a customer-specific monthly or quarterly factor improve on the 50/50 rate?

Every model predicts  gallons = rate × (days since the previous pickup)  and
differs only in the rate. The benchmark is

    C = 0.5 · last6_rate + 0.5 · previous_year_rate

THE PROBLEM THIS IS BUILT AROUND

    last6 already reflects whatever season it spans — its window runs a median
    of 131 days against a 21-day gap — and the previous-year rate reflects the
    same season a year earlier. So multiplying C by a raw month index applies a
    whole season's swing to a quantity that already carries a third of a year of
    it. Two model families separate the question:

      · the EXPLICIT family (D, E) replaces the base with a deliberately
        season-free level — half the trailing 365 days, half the 365 before
        that — so the factor is the only seasonal input;

      · the RESIDUAL family (F, G) asks the direct question: after C has had its
        say, is there a leftover per-customer, per-month bias? A residual factor
        near 1.00 means C already has the season and a factor adds nothing.

    Both are also run in the naive "multiply by the pickup's month index" form,
    reported as a straw man, because that is the form that double-counts.

NO LOOKAHEAD

    Shape factors use complete years before the test pickup's year only
    (customer_factors.prior_years). Residual factors use only pickups strictly
    before the test pickup's date: residual history opens at 2022-01-01, the
    earliest a baseline can be formed from 2021 history, and pickups are walked
    in date order so a residual is recorded only after it has been predicted.
    Both rules are asserted, not assumed.

    python3 analysis/backtest_customer_factors.py

Writes customer_residual_factors.csv, customer_factor_model_test.md and
customer_factor_model_test_detail.csv. Analysis only.
"""

import csv
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import customer_factors as cf

HERE = Path(__file__).resolve().parent

YEAR = 365
RESIDUAL_FROM = date(2022, 1, 1)     # earliest pickup that can carry a residual
LEVEL_MIN_DAYS = 180                 # a 365-day level window needs this much cover
MIN_OBS_MONTH = 2                    # prior same-month residuals needed for a factor
MIN_OBS_QUARTER = 3

SHRINKS = (0.25, 0.50, 0.75, 1.00)
CAPS = {"uncapped": None, "0.67-1.33": (0.67, 1.33), "0.50-1.50": (0.50, 1.50)}
RATIO_CLAMP = (0.5, 2.0)             # on the explicit family's multiplier

BOOTSTRAP_DRAWS = 2000
SEED = 42

BASE = "C 50/50"
MONTHS = cf.MONTHS


# ─────────────────────────────────────────────
# Rates
# ─────────────────────────────────────────────

def rate_between(pickups, k, start, end):
    """
    Gallons collected in (start, end] over the days covered, using only pickups
    before index k. The window is trimmed to the customer's first pickup, so a
    short history understates coverage rather than the rate.
    """
    first = pickups[0]["date"]
    if end <= start or end <= first:
        return None
    begin = max(start, first)
    days = (end - begin).days
    if days < LEVEL_MIN_DAYS:
        return None
    gallons = sum(p["gallons"] for p in pickups[:k] if begin < p["date"] <= end)
    return gallons / days


def base_rates(pickups, k):
    """last6, previous-year and the season-free level rate, plus their windows."""
    last6 = bsm.rate_span(pickups, k - 7, k - 1)
    prev_year, info = bt.rate_prev_year(pickups, k, YEAR)
    end = pickups[k - 1]["date"]
    trailing = rate_between(pickups, k, end - timedelta(days=YEAR), end)
    prior = rate_between(pickups, k, end - timedelta(days=2 * YEAR), end - timedelta(days=YEAR))

    level = None
    if trailing is not None and prior is not None:
        level = 0.5 * trailing + 0.5 * prior
    elif trailing is not None:
        level = trailing

    windows = {
        "last6": (pickups[k - 7]["date"], end),
        "prev_year": (info["training_start"], end) if prev_year is not None else None,
        "target": (end, pickups[k]["date"]),
    }
    return last6, prev_year, level, windows


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

def clamp(value, bounds):
    return min(bounds[1], max(bounds[0], value))


def predictions(pickups, k, factor, quarter_factor, residuals):
    """
    Every model's predicted gallons for test pickup k, or None where a model
    cannot be formed. `residuals` holds only pickups already walked past.
    """
    x = pickups[k]
    gap = (x["date"] - pickups[k - 1]["date"]).days
    if gap <= 0:
        return None, None

    last6, prev_year, level, windows = base_rates(pickups, k)
    if last6 is None or prev_year is None or level is None or factor is None:
        return None, None

    base = 0.5 * last6 + 0.5 * prev_year
    month = x["date"].month - 1

    def gw(vector, window):
        return cf.gap_weighted(vector, *window) if window else 1.0

    out = {
        "A last6": last6 * gap,
        "B prev year": prev_year * gap,
        BASE: base * gap,
    }

    for label, vector in (("month", factor), ("quarter", quarter_factor)):
        target = gw(vector, windows["target"])
        # Explicit: a season-free level carries the factor on its own.
        out[f"D level x {label}"] = level * gap * target
        # Straw man: the factor read off the pickup's own month.
        out[f"D naive {label}"] = level * gap * vector[month]
        out[f"C naive {label}"] = base * gap * vector[month]
        # Ratio form: take the season out of each half of the base over the days
        # it came from, then put back the season of the days being predicted.
        deseasonalised = 0.5 * (last6 / gw(vector, windows["last6"])) \
            + 0.5 * (prev_year / gw(vector, windows["prev_year"]))
        out[f"C ratio {label}"] = deseasonalised * gap * clamp(target, RATIO_CLAMP)

    for weight in (0.25, 0.50, 0.75):
        out[f"E {int(weight * 100)}/{int((1 - weight) * 100)} C+D"] = (
            weight * out[BASE] + (1 - weight) * out["D level x month"])

    # Residual family: correct C by its own leftover bias in this period.
    for period, history, minimum in (("month", residuals["month"], MIN_OBS_MONTH),
                                     ("quarter", residuals["quarter"], MIN_OBS_QUARTER)):
        key = x["date"].month if period == "month" else (x["date"].month - 1) // 3
        prior = history.get(key, [])
        raw = statistics.median(r for r, _, _ in prior) if len(prior) >= minimum else 1.0
        for weight in SHRINKS:
            shrunk = 1 + weight * (raw - 1)
            for cap_name, bounds in CAPS.items():
                value = clamp(shrunk, bounds) if bounds else shrunk
                out[f"{'F' if period == 'month' else 'G'} resid {period} "
                    f"w{weight:.2f} {cap_name}"] = out[BASE] * value

    detail = {"gap": gap, "base_rate": base, "level_rate": level,
              "target_factor": gw(factor, windows["target"]),
              "last6_factor": gw(factor, windows["last6"]),
              "resid_month": statistics.median(
                  [r for r, _, _ in residuals["month"].get(x["date"].month, [])] or [1.0]),
              "resid_quarter": statistics.median(
                  [r for r, _, _ in residuals["quarter"].get((x["date"].month - 1) // 3, [])]
                  or [1.0])}
    return out, detail


# ─────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────

def score_customer(cid, pickups, vintages):
    """
    Walk one customer's pickups in date order, recording residuals only after
    the pickup they came from has been predicted.
    """
    residuals = {"month": defaultdict(list), "quarter": defaultdict(list)}
    tests, dropped = [], 0

    for k in range(bt.MIN_PRIOR, len(pickups)):
        x = pickups[k]
        if x["date"] < RESIDUAL_FROM or x["gallons"] <= 0:
            continue

        factor, used = vintages.get((cid, x["date"].year), (None, []))
        assert all(y < x["date"].year for y in used), \
            "factor vintage must predate the test pickup's year"
        quarter_factor = cf.monthly_from_quarterly(factor) if factor else None

        preds, detail = predictions(pickups, k, factor, quarter_factor, residuals)

        if preds is not None and x["date"] >= bt.TEST_FROM:
            for model, predicted in preds.items():
                tests.append({
                    "customer_id": cid, "name": x["name"], "model": model,
                    "test_pickup_date": x["date"].isoformat(), "index": k,
                    "month": x["date"].month, "year": x["date"].year,
                    "actual_gallons": x["gallons"], "days_since_previous": detail["gap"],
                    "predicted_gallons": round(predicted, 1),
                    "_signed": predicted - x["gallons"],
                    **{key: round(value, 3) for key, value in detail.items() if key != "gap"},
                })
        elif preds is None and x["date"] >= bt.TEST_FROM:
            dropped += 1

        # Record this pickup's residual for LATER pickups only.
        if preds is not None:
            baseline = preds[BASE]
            if baseline > 0:
                residual = x["gallons"] / baseline
                for period, key in (("month", x["date"].month),
                                    ("quarter", (x["date"].month - 1) // 3)):
                    residuals[period][key].append((residual, x["gallons"], baseline))

    return tests, dropped, residuals


def wape(tests):
    return sum(abs(t["_signed"]) for t in tests) / sum(t["actual_gallons"] for t in tests) * 100


def interval(pairs_by_customer, rng):
    """Paired bootstrap of WAPE(model) − WAPE(base), resampling within customers."""
    diffs = []
    for _ in range(BOOTSTRAP_DRAWS):
        ea = eb = vol = 0
        for pairs in pairs_by_customer:
            for _ in range(len(pairs)):
                a, b = pairs[rng.randrange(len(pairs))]
                ea += abs(a["_signed"])
                eb += abs(b["_signed"])
                vol += a["actual_gallons"]
        diffs.append((eb - ea) / vol * 100)
    diffs.sort()
    return diffs[int(0.025 * BOOTSTRAP_DRAWS)], diffs[int(0.975 * BOOTSTRAP_DRAWS) - 1]


def wilcoxon(a, b):
    """Two-sided Wilcoxon on paired absolute errors, or None without scipy."""
    try:
        from scipy.stats import wilcoxon as _w
    except ImportError:
        return None
    x = [abs(p["_signed"]) for p in a]
    y = [abs(p["_signed"]) for p in b]
    try:
        return _w(x, y).pvalue
    except ValueError:
        return None


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def headline_models(models):
    """The models the main table shows: every family, residuals uncapped only."""
    return [m for m in models
            if not m.startswith(("F resid", "G resid")) or m.endswith("uncapped")]


def main():
    data = cf.build()
    pickups, sample = data["pickups"], data["sample"]
    classes, closers = data["classes"], data["closers"]

    by_customer, dropped_total, residual_state = {}, 0, {}
    for cid in sample:
        tests, dropped, residuals = score_customer(cid, pickups[cid], data["vintages"])
        dropped_total += dropped
        residual_state[cid] = residuals
        grouped = defaultdict(list)
        for t in tests:
            grouped[t["model"]].append(t)
        if grouped:
            by_customer[cid] = grouped

    models = list(next(iter(by_customer.values())).keys())
    open_customers = [c for c in by_customer if c not in closers]
    groups = {
        "all open customers": open_customers,
        "steady": [c for c in open_customers if classes[c] == "steady"],
        "seasonal": [c for c in open_customers if classes[c] == "seasonal"],
        "erratic": [c for c in open_customers if classes[c] == "erratic"],
        "closes seasonally": [c for c in by_customer if c in closers],
    }

    def pool(cids, model, years=None):
        return [t for c in cids for t in by_customer[c][model]
                if years is None or t["year"] in years]

    rng = random.Random(SEED)
    lines = []
    out = lines.append

    n_open = len(pool(open_customers, BASE))
    out("# Customer cyclicality factors vs the 50/50 baseline\n")
    out("Generated by `analysis/backtest_customer_factors.py`. Model definitions in the "
        "script's docstring; factors in [`customer_cyclicality.md`](customer_cyclicality.md).\n")
    out(f"**Sample:** {len(by_customer)} customers, "
        f"{len(pool(list(by_customer), BASE)):,} test pickups from "
        f"{bt.TEST_FROM.isoformat()} on, each scored by every model. "
        f"{len(open_customers)} customers and {n_open:,} pickups after removing the "
        f"{len(groups['closes seasonally'])} that close seasonally, which lead every table "
        "separately and never drive the recommendation.\n")
    out("**Everything is no-lookahead:** shape factors use complete years before the test "
        "pickup's year, residual factors only pickups before its date.\n")

    # ── 1. Headline ──
    out("## Model comparison — open customers\n")
    out(f"WAPE against the {BASE} benchmark. Negative difference is better. "
        f"95% interval is a paired bootstrap ({BOOTSTRAP_DRAWS} draws, within customers).\n")
    out("| Model | WAPE | MAPE | Median APE | MAE | RMSE | Bias | vs 50/50 | 95% interval |")
    out("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    base_tests = pool(open_customers, BASE)
    base_wape = wape(base_tests)
    for model in headline_models(models):
        tests = pool(open_customers, model)
        m = bt.metrics(tests)
        if model == BASE:
            diff, ci = "—", "—"
        else:
            lo, hi = interval([list(zip(by_customer[c][BASE], by_customer[c][model]))
                               for c in open_customers], rng)
            diff, ci = f"{m['wape'] - base_wape:+.1f}", f"{lo:+.1f} to {hi:+.1f}"
        out(f"| {model} | {m['wape']:.1f}% | {m['mape']:.1f}% | {m['median_ape']:.1f}% | "
            f"{m['mae']:.1f} | {m['rmse']:.1f} | {m['bias_pct']:+.1f}% | {diff} | {ci} |")

    # ── 2. By class ──
    out("\n## By class\n")
    out("WAPE. The pooled row above is 4 to 1 steady, so a factor that helps seasonal "
        "customers and hurts steady ones can still look like a wash.\n")
    shown = [BASE, "D level x month", "D level x quarter", "C ratio month", "C naive month",
             "F resid month w0.50 uncapped", "G resid quarter w0.50 uncapped"]
    out("| Group | Customers | Pickups | " + " | ".join(shown) + " |")
    out("|---|---:|---:|" + "---:|" * len(shown))
    for name, cids in groups.items():
        if not cids:
            continue
        values = []
        for model in shown:
            w = wape(pool(cids, model))
            values.append(w)
        best = min(values)
        cells = [f"**{v:.1f}%**" if v == best else f"{v:.1f}%" for v in values]
        out(f"| {name} | {len(cids)} | {len(pool(cids, BASE)):,} | " + " | ".join(cells) + " |")

    # ── 3. Shrinkage and caps ──
    for family, period in (("F", "month"), ("G", "quarter")):
        out(f"\n## {family}: residual {period} factor — shrinkage × cap, open customers\n")
        out("| Shrinkage | " + " | ".join(CAPS) + " |")
        out("|---|" + "---:|" * len(CAPS))
        for weight in SHRINKS:
            cells = []
            for cap_name in CAPS:
                model = f"{family} resid {period} w{weight:.2f} {cap_name}"
                cells.append(f"{wape(pool(open_customers, model)):.1f}%")
            out(f"| w={weight:.2f} | " + " | ".join(cells) + " |")
        out(f"\nBenchmark: {base_wape:.1f}%.")

    # ── 4. Choose on 2023-24, confirm on 2025-26 ──
    out("\n## Chosen on 2023–24, confirmed on 2025–26\n")
    out("A weight picked on the same pickups that report the win is not evidence. "
        "The choice period picks the best shrinkage per group; the confirm period is "
        "the honest number.\n")
    out("| Group | Best on 2023–24 | Its WAPE there | vs 50/50 | Its WAPE on 2025–26 | "
        "vs 50/50 | 95% interval (confirm) |")
    out("|---|---|---:|---:|---:|---:|---|")
    choose, confirm = (2023, 2024), (2025, 2026)
    for name, cids in groups.items():
        if not cids:
            continue
        candidates = [m for m in models if m != BASE]
        best = min(candidates, key=lambda m: wape(pool(cids, m, choose)))
        cw, bw = wape(pool(cids, best, choose)), wape(pool(cids, BASE, choose))
        cf_w, bf_w = wape(pool(cids, best, confirm)), wape(pool(cids, BASE, confirm))
        pairs = [[(a, b) for a, b in zip(by_customer[c][BASE], by_customer[c][best])
                  if a["year"] in confirm] for c in cids]
        lo, hi = interval([p for p in pairs if p], rng)
        out(f"| {name} | {best} | {cw:.1f}% | {cw - bw:+.1f} | {cf_w:.1f}% | "
            f"{cf_w - bf_w:+.1f} | {lo:+.1f} to {hi:+.1f} |")

    # ── 5. Helped vs hurt ──
    out("\n## Customers helped vs hurt\n")
    out("| Model | Helped | Hurt | Unchanged | Median change (points) |")
    out("|---|---:|---:|---:|---:|")
    for model in headline_models(models):
        if model == BASE:
            continue
        deltas = [wape(by_customer[c][model]) - wape(by_customer[c][BASE])
                  for c in open_customers]
        out(f"| {model} | {sum(1 for d in deltas if d < -0.05)} | "
            f"{sum(1 for d in deltas if d > 0.05)} | "
            f"{sum(1 for d in deltas if abs(d) <= 0.05)} | {statistics.median(deltas):+.2f} |")

    # ── 6. Who it helps and hurts ──
    pick = "F resid month w0.50 uncapped"
    deltas = sorted(((wape(by_customer[c][pick]) - wape(by_customer[c][BASE]), c)
                     for c in by_customer), key=lambda t: t[0])
    for title, rows in (("Most helped", deltas[:20]), ("Most hurt", list(reversed(deltas[-20:])))):
        out(f"\n### {title} by `{pick}`\n")
        out("| Customer | Class | Pickups | 50/50 WAPE | With factor | Change |")
        out("|---|---|---:|---:|---:|---:|")
        for delta, c in rows:
            out(f"| {pickups[c][-1]['name']} | "
                f"{classes[c]}{' (closes)' if c in closers else ''} | "
                f"{len(by_customer[c][BASE])} | {wape(by_customer[c][BASE]):.1f}% | "
                f"{wape(by_customer[c][pick]):.1f}% | {delta:+.1f} |")

    # ── 7. Are residuals near 1.00? ──
    out("\n## The double-counting check\n")
    out("If the 50/50 baseline already carries a customer's season, its residual factors "
        "sit at 1.00 and there is nothing left to correct.\n")
    out("| Group | Median residual | 10th–90th percentile | Share outside 0.85–1.15 |")
    out("|---|---:|---|---:|")
    for name, cids in groups.items():
        if not cids:
            continue
        values = sorted(t["resid_month"] for c in cids for t in by_customer[c][BASE])
        outside = sum(1 for v in values if v < 0.85 or v > 1.15) / len(values)
        out(f"| {name} | {statistics.median(values):.3f} | "
            f"{values[len(values) // 10]:.2f}–{values[-len(values) // 10 - 1]:.2f} | "
            f"{outside:.0%} |")

    # ── 8. By month ──
    out("\n## Bias by month — open customers\n")
    out("| Month | Pickups | " + " | ".join(shown[:5]) + " |")
    out("|---|---:|" + "---:|" * 5)
    for month in range(1, 13):
        cells = []
        for model in shown[:5]:
            tests = [t for c in open_customers for t in by_customer[c][model]
                     if t["month"] == month]
            cells.append(f"{bt.metrics(tests)['bias_pct']:+.1f}%")
        n = len([t for c in open_customers for t in by_customer[c][BASE] if t["month"] == month])
        out(f"| {MONTHS[month - 1]} | {n} | " + " | ".join(cells) + " |")

    # ── 9. Wilcoxon, secondary ──
    p = wilcoxon(pool(open_customers, BASE), pool(open_customers, pick))
    out(f"\nWilcoxon on paired absolute errors, {BASE} vs `{pick}`: "
        + (f"p = {p:.2g}" if p is not None else "scipy unavailable")
        + ". Secondary only — it tests the typical pickup, while WAPE weighs the gallons.\n")

    out("## Caveats\n")
    out(f"- **Pickups not scored:** {dropped_total} (a model could not be formed — usually "
        "too little history for the two-year level rate).")
    out("- **Survivorship.** The sample requires a pickup in every year 2021–2026.")
    out("- **The bootstrap treats pickups as independent,** so true intervals are wider.")
    out("- **Closers are unsolved.** A closed month is a real zero; a multiplier cannot fix "
        "a rate × gap model across a closure.")

    (HERE / "customer_factor_model_test.md").write_text("\n".join(lines) + "\n")

    # ── residual factors CSV ──
    fields = ["customer_id", "name", "period_type", "period", "n_observations",
              "median_residual", "weighted_residual", "raw_factor", "shrink_25", "shrink_50",
              "shrink_75", "capped_factor_50_150", "capped_factor_67_133"]
    rows = []
    for cid in sample:
        for period_type, history in residual_state[cid].items():
            for key, entries in sorted(history.items()):
                values = [r for r, _, _ in entries]
                raw = statistics.median(values)
                weighted = sum(a for _, a, _ in entries) / sum(p for _, _, p in entries)
                label = MONTHS[key - 1] if period_type == "month" else f"Q{key + 1}"
                rows.append({
                    "customer_id": cid, "name": pickups[cid][-1]["name"],
                    "period_type": period_type, "period": label, "n_observations": len(entries),
                    "median_residual": round(raw, 3), "weighted_residual": round(weighted, 3),
                    "raw_factor": round(raw, 3),
                    **{f"shrink_{int(w * 100)}": round(1 + w * (raw - 1), 3)
                       for w in (0.25, 0.50, 0.75)},
                    "capped_factor_50_150": round(clamp(raw, (0.5, 1.5)), 3),
                    "capped_factor_67_133": round(clamp(raw, (0.67, 1.33)), 3),
                })
    bt.write_csv(HERE / "customer_residual_factors.csv", fields, rows)

    detail_fields = ["customer_id", "name", "model", "test_pickup_date", "index", "year",
                     "month", "actual_gallons", "days_since_previous", "predicted_gallons",
                     "base_rate", "level_rate", "target_factor", "last6_factor",
                     "resid_month", "resid_quarter"]
    # Headline models only: every shrinkage × cap combination would be 220k rows.
    keep = headline_models(models)
    bt.write_csv(HERE / "customer_factor_model_test_detail.csv", detail_fields,
                 [t for c in by_customer for m in keep for t in by_customer[c][m]])

    print("\n".join(lines[:60]))
    print(f"\nWrote analysis/customer_factor_model_test.md, customer_residual_factors.csv "
          f"and customer_factor_model_test_detail.csv")


if __name__ == "__main__":
    main()
