#!/usr/bin/env python3
"""
Four oil-rate models, scored on the 11 hand-picked steady customers and on the
customers that seasonality_score.py classes as seasonal.

Every model predicts  rate × (date(x) − date(x-1))  and differs only in the rate.
Each "rate over a span" below is total gallons ÷ total days: the gallons at
the pickups after the span's first pickup, through its last, over the days
between them.

1. last 6
       gallons at x-6 .. x-1 over date(x-7) → date(x-1)

2. 50/50 last 6 + prev year
       the average of last 6 and the previous-year rate (backtest_steady_rate.py
       --prev-year: back to the latest pickup at least 365 days before x)

3. seasonal blend — recent and last year, weighted equally
       recent    = the last n pickups, n = pickups in the 60 days before x,
                   capped at 6. With fewer than 3, recent is not used.
       year ago  = the 3 pickups on or before date(x) − 365 and the 3 after:
                   their gallons over the days from the pickup before them to
                   the last of them
       rate      = (recent + year ago) / 2, or whichever one exists

4. seasonal ratio — this year's level, last year's shape
       rate = last 6 × (year ago ÷ last year's rate over the last-6 span)
       "last year's rate over the last-6 span" moves date(x-7) → date(x-1)
       back 365 days and takes the pickups bracketing that span. For a customer
       with no seasonal pattern the ratio is near 1 and this is just last 6.
       Falls back to last 6 when either year-ago rate is missing or zero.

A pickup is scored only when every model produced a rate, so all four are
compared on the same pickups. Fixed weights, nothing fitted — so there is no
tuning to hold out, but results are split 2023–24 vs 2025–26 to show whether
they hold across periods.

    python3 analysis/backtest_seasonal_models.py

Writes analysis/seasonal_models.md and backtest_seasonal_models_detail.csv.
Analysis only.
"""

import csv
import random
import statistics
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import backtest_steady_rate as bt
import seasonality_score as ss

HERE = Path(__file__).resolve().parent
OUT = HERE / "seasonal_models.md"
DETAIL = HERE / "backtest_seasonal_models_detail.csv"

YEAR = 365
RECENT_DAYS = 60
RECENT_MAX = 6
RECENT_MIN = 3
SIDE = 3                     # pickups either side of the year-ago date

# A seasonal customer whose quietest month produces under 10% of an average
# month is effectively closed for part of the year, and is reported separately:
# rate × gap cannot work across a closure, whatever the rate.
CLOSED_INDEX = 0.1

BOOTSTRAP_DRAWS = 2000
SEED = 42

MODELS = ["last 6", "50/50 last 6 + prev year", "seasonal blend", "seasonal ratio"]
SHORT = {"last 6": "last 6", "50/50 last 6 + prev year": "50/50",
         "seasonal blend": "blend", "seasonal ratio": "ratio"}
MONTHS = ss.MONTHS


# ─────────────────────────────────────────────
# Rates
# ─────────────────────────────────────────────

def rate_span(pickups, lo, hi):
    """Gallons at pickups lo+1 .. hi over the days from lo to hi."""
    if lo is None or hi is None or lo < 0 or hi <= lo:
        return None
    days = (pickups[hi]["date"] - pickups[lo]["date"]).days
    if days <= 0:
        return None
    return sum(p["gallons"] for p in pickups[lo + 1:hi + 1]) / days


def last_on_or_before(pickups, k, day):
    return next((i for i in range(k - 1, -1, -1) if pickups[i]["date"] <= day), None)


def first_on_or_after(pickups, k, day):
    return next((i for i in range(k) if pickups[i]["date"] >= day), None)


def year_ago_around(pickups, k):
    a = last_on_or_before(pickups, k, pickups[k]["date"] - timedelta(days=YEAR))
    if a is None or a + SIDE > k - 1:
        return None
    # a-2, a-1, a on or before the date; a+1 .. a+3 after it; a-3 sets the start.
    return rate_span(pickups, a - SIDE, a + SIDE)


def year_ago_span(pickups, k, start, end):
    lo = last_on_or_before(pickups, k, start - timedelta(days=YEAR))
    hi = first_on_or_after(pickups, k, end - timedelta(days=YEAR))
    return rate_span(pickups, lo, hi)


def recent(pickups, k):
    x = pickups[k]["date"]
    cutoff = x - timedelta(days=RECENT_DAYS)
    count = sum(1 for p in pickups[:k] if cutoff <= p["date"] < x)
    n = min(RECENT_MAX, count)
    if n < RECENT_MIN:
        return None, n
    return rate_span(pickups, k - 1 - n, k - 1), n


def model_rates(pickups, k):
    """model -> rate for test pickup k, plus notes on fallbacks and the ratio."""
    last6 = rate_span(pickups, k - 7, k - 1)
    prev_year, _ = bt.rate_prev_year(pickups, k, YEAR)
    rec, n_recent = recent(pickups, k)
    around = year_ago_around(pickups, k)
    base = year_ago_span(pickups, k, pickups[k - 7]["date"], pickups[k - 1]["date"])

    if rec is not None and around is not None:
        blend, blend_note = (rec + around) / 2, "both"
    elif around is not None:
        blend, blend_note = around, "year ago only"
    elif rec is not None:
        blend, blend_note = rec, "recent only"
    else:
        blend, blend_note = None, "neither"

    ratio = None
    if last6 is not None and around is not None and base:
        ratio = around / base

    rates = {
        "last 6": last6,
        "50/50 last 6 + prev year": None if last6 is None or prev_year is None
                                    else (last6 + prev_year) / 2,
        "seasonal blend": blend,
        "seasonal ratio": last6 * ratio if ratio is not None else last6,
    }
    notes = {"blend": blend_note, "recent_n": n_recent, "ratio": ratio,
             "ratio_fallback": ratio is None}
    return rates, notes


# ─────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────

def score_customer(cid, pickups, group):
    tests, dropped = [], 0
    for k in range(bt.MIN_PRIOR, len(pickups)):
        x = pickups[k]
        if x["date"] < bt.TEST_FROM or x["gallons"] <= 0:
            continue
        rates, notes = model_rates(pickups, k)
        if any(r is None for r in rates.values()):
            dropped += 1
            continue
        gap = (x["date"] - pickups[k - 1]["date"]).days
        for model, rate in rates.items():
            signed = rate * gap - x["gallons"]
            tests.append({
                "customer_id": cid, "name": x["name"], "group": group, "model": model,
                "test_pickup_date": x["date"].isoformat(), "index": k,
                "actual_gallons": x["gallons"], "days_since_previous": gap,
                "rate_gpd": round(rate, 3), "predicted_gallons": round(rate * gap, 1),
                "signed_error": round(signed, 1),
                "blend_uses": notes["blend"], "recent_pickups": notes["recent_n"],
                "seasonal_ratio": None if notes["ratio"] is None else round(notes["ratio"], 3),
                "_signed": signed,
            })
    return tests, dropped


def wape(tests):
    return sum(abs(t["_signed"]) for t in tests) / sum(t["actual_gallons"] for t in tests) * 100


def interval_vs_last6(by_customer, model, rng):
    """Paired bootstrap of WAPE(model) − WAPE(last 6), resampling within customers."""
    pairs = [list(zip(ts["last 6"], ts[model])) for ts in by_customer.values()]
    diffs = []
    for _ in range(BOOTSTRAP_DRAWS):
        ea = eb = vol = 0
        for ps in pairs:
            for _ in range(len(ps)):
                a, b = ps[rng.randrange(len(ps))]
                ea += abs(a["_signed"])
                eb += abs(b["_signed"])
                vol += a["actual_gallons"]
        diffs.append((eb - ea) / vol * 100)
    diffs.sort()
    return diffs[int(0.025 * BOOTSTRAP_DRAWS)], diffs[int(0.975 * BOOTSTRAP_DRAWS) - 1]


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    score_rows, pickups = ss.scores()
    scores = {r["customer_id"]: r for r in score_rows}

    # A steady-sample customer below the pool's volume cut still gets a score,
    # so the by-customer table can show it.
    for cid in bt.SAMPLE:
        if cid not in scores:
            row = ss.score(pickups[cid])
            scores[cid] = {**row, "class": ss.classify(row) + " (below pool cut)"}

    seasonal = [r for r in score_rows
                if r["class"] == "seasonal" and r["customer_id"] not in bt.SAMPLE]
    groups = {
        "steady": list(bt.SAMPLE),
        "seasonal": [r["customer_id"] for r in seasonal if r["trough_index"] >= CLOSED_INDEX],
        "closes seasonally": [r["customer_id"] for r in seasonal
                              if r["trough_index"] < CLOSED_INDEX],
    }

    # tests[group][cid][model] -> list of tests, in pickup order
    tests = {g: {} for g in groups}
    dropped = {}
    all_rows = []
    for g, ids in groups.items():
        for cid in ids:
            rows, lost = score_customer(cid, pickups[cid], g)
            dropped[cid] = lost
            by_model = defaultdict(list)
            for t in rows:
                by_model[t["model"]].append(t)
            tests[g][cid] = by_model
            all_rows.extend(rows)

    names = {cid: pickups[cid][-1]["name"] for ids in groups.values() for cid in ids}
    rng = random.Random(SEED)
    lines = []
    out = lines.append

    out("# Seasonal rate models\n")
    out("Generated by `analysis/backtest_seasonal_models.py`. Every eligible pickup from "
        f"{bt.TEST_FROM.isoformat()} on, no sampling; all four models scored on the same "
        "pickups. Model definitions in the script's docstring.\n")
    out(f"- **steady** — the 11 hand-picked customers in `backtest_steady_rate.py`")
    out(f"- **seasonal** — every other customer `seasonality_score.py` classes as seasonal "
        f"(amplitude ≥ {ss.SEASONAL_MIN_AMPLITUDE}, repeatability ≥ {ss.SEASONAL_MIN_REPEAT}) "
        "that stays open all year")
    out(f"- **closes seasonally** — the same, but the quietest month produces under "
        f"{CLOSED_INDEX:.0%} of an average month\n")

    # ── 1. By group ──
    out("## By group\n")
    out("**Difference** is the model's WAPE minus last 6's, in points; negative is better. "
        f"The 95% interval is a paired bootstrap ({BOOTSTRAP_DRAWS} draws, resampling within "
        "each customer).\n")
    for g in groups:
        by_c = tests[g]
        n = sum(len(m["last 6"]) for m in by_c.values())
        out(f"### {g.capitalize()} — {len(by_c)} customers, {n} pickups\n")
        out("| Model | Median APE | WAPE | MAPE | Bias | Difference | 95% interval |")
        out("|---|---:|---:|---:|---:|---:|---|")
        base = bt.metrics([t for m in by_c.values() for t in m["last 6"]])
        for model in MODELS:
            m = bt.metrics([t for c in by_c.values() for t in c[model]])
            if model == "last 6":
                diff, interval = "—", "—"
            else:
                lo, hi = interval_vs_last6(by_c, model, rng)
                diff, interval = f"{m['wape'] - base['wape']:+.1f}", f"{lo:+.1f} to {hi:+.1f}"
            out(f"| {model} | {m['median_ape']:.1f}% | {m['wape']:.1f}% | {m['mape']:.1f}% | "
                f"{m['bias_pct']:+.1f}% | {diff} | {interval} |")
        out("")

    # ── 2. By customer ──
    out("## WAPE by customer\n")
    out("Sorted by seasonal amplitude within each group. Lowest WAPE in bold.\n")
    out("| Group | Customer | Amplitude | Repeat | Class | n | "
        + " | ".join(SHORT[m] for m in MODELS) + " |")
    out("|---|---|---:|---:|---|---:|" + "---:|" * len(MODELS))
    for g in groups:
        for cid in sorted(tests[g], key=lambda c: -scores[c]["amplitude"]):
            c = tests[g][cid]
            w = {m: wape(c[m]) for m in MODELS}
            best = min(w.values())
            cells = [f"**{v:.1f}%**" if v == best else f"{v:.1f}%" for v in w.values()]
            s = scores[cid]
            out(f"| {g} | {names[cid]} | {s['amplitude']:.2f} | {s['repeatability']:.2f} | "
                f"{s['class']} | {len(c['last 6'])} | " + " | ".join(cells) + " |")

    # ── 3. By month, seasonal group ──
    for g in groups:
        out(f"\n## Bias by month — {g}\n")
        out("Mean signed error as a % of actual gallons. Positive = over-predicted.\n")
        out("| Month | n | " + " | ".join(SHORT[m] for m in MODELS) + " |")
        out("|---|---:|" + "---:|" * len(MODELS))
        by_month = defaultdict(lambda: defaultdict(list))
        for c in tests[g].values():
            for model in MODELS:
                for t in c[model]:
                    by_month[int(t["test_pickup_date"][5:7])][model].append(t)
        for month in sorted(by_month):
            ms = by_month[month]
            cells = [f"{bt.metrics(ms[m])['bias_pct']:+.1f}%" for m in MODELS]
            out(f"| {MONTHS[month - 1]} | {len(ms['last 6'])} | " + " | ".join(cells) + " |")

    # ── 4. Period split ──
    out("\n## WAPE by period\n")
    out("Nothing is fitted, so this is a stability check rather than a holdout.\n")
    out("| Group | Period | n | " + " | ".join(SHORT[m] for m in MODELS) + " |")
    out("|---|---|---:|" + "---:|" * len(MODELS))
    for g in groups:
        for label, years in (("2023–24", (2023, 2024)), ("2025–26", (2025, 2026))):
            sel = {m: [t for c in tests[g].values() for t in c[m]
                       if int(t["test_pickup_date"][:4]) in years] for m in MODELS}
            out(f"| {g} | {label} | {len(sel['last 6'])} | "
                + " | ".join(f"{wape(sel[m]):.1f}%" for m in MODELS) + " |")

    # ── 5. Mechanics ──
    out("\n## Mechanics\n")
    for g in groups:
        rows = [t for c in tests[g].values() for t in c["seasonal ratio"]]
        ratios = sorted(t["seasonal_ratio"] for t in rows if t["seasonal_ratio"] is not None)
        uses = defaultdict(int)
        for t in rows:
            uses[t["blend_uses"]] += 1
        fallback = sum(1 for t in rows if t["seasonal_ratio"] is None)
        extreme = sum(1 for r in ratios if r > 2 or r < 0.5)
        out(f"- **{g}:** blend used both halves on {uses['both']} of {len(rows)} pickups "
            f"(year ago only {uses['year ago only']}, recent only {uses['recent only']}). "
            f"Ratio fell back to last 6 on {fallback}; median ratio "
            f"{statistics.median(ratios):.2f}, middle 90% {ratios[len(ratios) // 20]:.2f}–"
            f"{ratios[-len(ratios) // 20 - 1]:.2f}, beyond 0.5–2× on {extreme}.")
    lost = {names[c]: n for c, n in dropped.items() if n}
    out("- **Pickups not scored** (a model could not form a rate): "
        + (", ".join(f"{k} {v}" for k, v in lost.items()) if lost else "none") + ".")

    out("\n## Caveats\n")
    out("- **The seasonal group was chosen with the same years it is scored on.** The score "
        "uses 2023–25 and so do the tests. That is fine for asking whether seasonal models "
        "help seasonal customers; a production classifier would score from history before "
        "each prediction.")
    out("- **The bootstrap treats pickups as independent.** Consecutive pickups share most "
        "of their windows, so true intervals are somewhat wider.")
    out("- **Small groups.** "
        + ", ".join(f"{len(ids)} {g}" for g, ids in groups.items())
        + " customers. Check the by-customer table before trusting a pooled figure.")

    OUT.write_text("\n".join(lines) + "\n")

    fields = ["group", "customer_id", "name", "model", "test_pickup_date", "index",
              "actual_gallons", "days_since_previous", "rate_gpd", "predicted_gallons",
              "signed_error", "blend_uses", "recent_pickups", "seasonal_ratio"]
    with open(DETAIL, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print("\n".join(lines))
    print(f"\nWrote analysis/{OUT.name} and {DETAIL.name}")


if __name__ == "__main__":
    main()
