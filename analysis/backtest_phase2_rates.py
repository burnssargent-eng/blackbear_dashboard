#!/usr/bin/env python3
"""
Phase 2 holdout tests: seasonal and recency-weighted oil-rate formulas.

Two questions, both against today's production rate (0.5 x previous year +
0.5 x last 6 pickups):

  A. For customers that stay open but swing with the seasons, does a formula
     built on last year's rate around the same date beat it?
  B. For every open customer, does shifting weight toward the most recent
     pickups -- only when they disagree with the ones before -- beat it?

Every model predicts a pickup's gallons as rate x days, the way every
backtest in this folder does, with ONE change: the clock starts at the later
of the previous pickup and any empty check (qty 0/1) in between, the
production rule since 2026-10-05. Applied to every model, so comparisons stay
fair.

No lookahead anywhere:
  * a prediction for pickup k sees only pickups before k;
  * a customer's seasonality for test year Y is scored from complete years
    before Y (not the fixed 2023-25 the earlier scripts use);
  * weights and thresholds are chosen on 2023-24 test pickups only and
    reported on 2025-26 (CHOOSE / CONFIRM).

Shared barrels are folded and new-owner histories cut exactly as the
projection builder does, so the customers here are the customers on the page.

    python3 analysis/backtest_phase2_rates.py

Writes analysis/phase2_rate_models.md and backtest_phase2_detail.csv
(gitignored). Standard library only; deterministic (fixed seed).
"""

import csv
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import backtest_customer_factors as bcf
import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import build_projection_table as bpt
import customer_factors as cf
import seasonality_score as ss

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "phase2_rate_models.md"
OUT_DETAIL = HERE / "backtest_phase2_detail.csv"

YEAR = 365
TEST_FROM = date(2023, 1, 1)
CHOOSE = (2023, 2024)
CONFIRM = (2025, 2026)
MIN_PRIOR = 8

# Seasonal-open, scored from prior complete years only.
PROFILE_YEARS_BACK = 3          # repeatability needs 3 years
SKIP_YEARS = {2020}             # the pandemic year distorts a shape
SEASONAL_AMPLITUDE = 0.25       # the house thresholds (seasonality_score)
SEASONAL_REPEAT = 0.60
OPEN_TROUGH = 0.10              # below this a month is "closed" (bsm.CLOSED_INDEX)
MIN_GAL_PER_YEAR = 300
PERMUTATIONS = 1000
PERMUTATION_P = 0.05

SEED = 42
CHECK_DAILY = True              # spot-check RunningDaily against ss.daily_production
BOOTSTRAP_DRAWS = 2000

# Statuses that never take a daily rate, or are closers: out of both tests.
NOT_OPEN = {bpt.STATUS_TRUE_CLOSER, bpt.STATUS_SEMI_CLOSER, bpt.STATUS_EVENT,
            bpt.STATUS_ON_DEMAND, bpt.STATUS_LUMP_SUM}


# ─────────────────────────────────────────────
# Data, folded the way the builder folds it
# ─────────────────────────────────────────────

def load():
    pickups = bt.load_pickups(ids=None)
    checks = bpt.load_empty_checks(date.max)
    active = bpt.load_active_ids()
    bpt._check_registries()
    bpt.apply_history_starts(pickups, checks)
    bpt.combine_shared(pickups, checks, {}, defaultdict(list), active)
    excluded = {cid for cid, o in bpt.MODEL_OVERRIDES.items() if o.status in NOT_OPEN}
    pickups = {cid: ps for cid, ps in pickups.items()
               if cid in active and cid not in excluded and len(ps) > MIN_PRIOR}
    return pickups, checks


def clock_start(pickups, k, checks):
    """The later of pickup k-1 and an empty check before pickup k."""
    prev, cur = pickups[k - 1]["date"], pickups[k]["date"]
    inside = [d for d in checks if prev < d < cur]
    return max(inside) if inside else prev


# ─────────────────────────────────────────────
# Seasonality from prior years only
# ─────────────────────────────────────────────

def profile_years(test_year):
    years, y = [], test_year - 1
    while len(years) < PROFILE_YEARS_BACK and y >= 2016:
        if y not in SKIP_YEARS:
            years.append(y)
        y -= 1
    return sorted(years)


def season_for_year(pickups, test_year):
    """
    Seasonality for `test_year`, from pickups before Jan 1 of that year only.

    The spreading pushes a January pickup's gallons back into December, so
    leaving January out under-counts the last December slightly. Accepted: the
    alternative is lookahead.
    """
    cutoff = date(test_year, 1, 1)
    prior = [p for p in pickups if p["date"] < cutoff]
    if len(prior) < 2:
        return None
    daily = ss.daily_production(prior)
    years = profile_years(test_year)
    profile = {}
    for y in years:
        idx = cf.year_index(daily, y)
        if idx is not None:
            profile[y] = idx
    volume = sum(p["gallons"] for p in prior if p["date"].year in years) / len(years)
    factor, used = cf.factor_from_years(profile, years)
    if factor is None:
        return None
    return {
        "profile": profile, "years": used, "factor": factor,
        "amplitude": cf.amplitude(factor),
        "repeatability": cf.repeatability(profile, years),
        "trough": min(factor), "gal_per_year": volume,
    }


def permutation_p(profile, years, observed, rng):
    """Share of month-shuffled profiles whose repeatability reaches the real one."""
    hits = 0
    for _ in range(PERMUTATIONS):
        shuffled = {y: rng.sample(profile[y], 12) for y in years if y in profile}
        r = cf.repeatability(shuffled, years)
        if r is not None and r >= observed:
            hits += 1
    return (hits + 1) / (PERMUTATIONS + 1)


def classify(season, rng):
    if season is None or season["repeatability"] is None:
        return "unscored", None
    if season["gal_per_year"] < MIN_GAL_PER_YEAR:
        return "low volume", None
    if season["trough"] < OPEN_TROUGH:
        return "closer", None
    if (season["amplitude"] >= SEASONAL_AMPLITUDE
            and season["repeatability"] >= SEASONAL_REPEAT):
        p = permutation_p(season["profile"], season["years"],
                          season["repeatability"], rng)
        return ("seasonal-open" if p <= PERMUTATION_P else "seasonal, fails noise test"), p
    return "steady/other", None


# ─────────────────────────────────────────────
# Rates
# ─────────────────────────────────────────────

class RunningDaily:
    """
    ss.daily_production for pickups[:k], grown one pickup at a time.

    Rebuilding it for every test pickup is quadratic. Each new pickup only adds
    its own gallons spread back over its gap (capped as ss does), and a pickup
    on an existing date adds to that date's spread over the same span, so the
    result matches ss.daily_production(pickups[:k]) exactly -- asserted below.
    """

    def __init__(self):
        self.daily = defaultdict(float)
        self.dates = []          # distinct dates, ascending
        self.span = {}           # date -> days its gallons are spread over

    def add(self, p):
        d = p["date"]
        if self.dates and d == self.dates[-1]:
            span = self.span.get(d)
        else:
            span = (min((d - self.dates[-1]).days, ss.SPREAD_CAP_DAYS)
                    if self.dates else None)
            self.dates.append(d)
            self.span[d] = span
        if span:
            for i in range(span):
                self.daily[d - timedelta(days=i)] += p["gallons"] / span

def window_rate(daily, first_date, center, half_width):
    """Gallons per day over center +/- half_width, from spread production."""
    start, end = center - timedelta(days=half_width), center + timedelta(days=half_width)
    if start <= first_date + timedelta(days=60):
        return None
    days = (end - start).days + 1
    return sum(daily.get(start + timedelta(days=i), 0.0) for i in range(days)) / days


def last_year_rate(daily, first_date, target, half_width):
    """Average over last year and the year before, whichever exist."""
    rates = [window_rate(daily, first_date, target - timedelta(days=YEAR * n), half_width)
             for n in (1, 2)]
    rates = [r for r in rates if r is not None]
    return statistics.fmean(rates) if rates else None


def span(pickups, lo, hi):
    return bsm.rate_span(pickups, lo, hi) if lo >= 0 else None


def ewma_rate(pickups, k, half_life, intervals=12):
    """Pooled rate over recent intervals, each weighted by 0.5^(age/half_life)."""
    gal = days = 0.0
    for age, i in enumerate(range(k - 1, max(0, k - 1 - intervals), -1)):
        gap = (pickups[i]["date"] - pickups[i - 1]["date"]).days
        if gap <= 0:
            continue
        w = 0.5 ** (age / half_life)
        gal += w * pickups[i]["gallons"]
        days += w * gap
    return gal / days if days else None


def blend(a, wa, b):
    if a is None or b is None:
        return None
    return wa * a + (1 - wa) * b


def model_rates(pickups, k, start, season, daily):
    """
    Every model's rate for pickup k. `start` is the clock start; the models
    that use the month index need `season` (prior-years factor) to exist.
    """
    x = pickups[k]["date"]
    first = pickups[0]["date"]

    last6 = span(pickups, k - 7, k - 1)
    prev_year, _ = bt.rate_prev_year(pickups, k, YEAR)
    base = blend(last6, 0.5, prev_year)
    last2 = span(pickups, k - 3, k - 1)
    last3 = span(pickups, k - 4, k - 1)

    rates = {"50/50 (today)": base}

    # A. Seasonal formulas.
    ly21 = last_year_rate(daily, first, x, 21)
    ly42 = last_year_rate(daily, first, x, 42)
    rates["Sarge 50/50: last-yr ±3wk + last 2"] = blend(ly21, 0.5, last2)
    rates["Sarge 70/30"] = blend(ly21, 0.7, last2)
    rates["Sarge 30/70"] = blend(ly21, 0.3, last2)
    rates["Sarge ±6wk"] = blend(ly42, 0.5, last2)

    adj = None
    ly1 = window_rate(daily, first, x - timedelta(days=YEAR), 21)
    if ly1 is not None and last3 is not None:
        a, b = pickups[k - 4]["date"] - timedelta(days=YEAR), pickups[k - 1]["date"] - timedelta(days=YEAR)
        if a > first + timedelta(days=60):
            n = (b - a).days
            base_ly = sum(daily.get(a + timedelta(days=i + 1), 0.0) for i in range(n)) / n if n else 0
            if base_ly > 0:
                adj = ly1 * min(2.0, max(0.5, last3 / base_ly))
    rates["Level-adjusted last year"] = adj

    level_x_month = recent_x_index = None
    if season is not None:
        factor = season["factor"]
        _, _, level, _ = bcf.base_rates(pickups, k)
        coming = cf.gap_weighted(factor, start, x)
        if level is not None:
            level_x_month = level * coming
        if last3 is not None:
            covered = cf.gap_weighted(factor, pickups[k - 4]["date"], pickups[k - 1]["date"])
            if covered > 0:
                recent_x_index = last3 / covered * coming
    rates["Level × month index"] = level_x_month
    rates["Last 3 ÷ its index × coming index"] = recent_x_index

    seasonal_blend, _ = bsm.model_rates(pickups, k)
    rates["Seasonal blend (2026-09)"] = seasonal_blend["seasonal blend"]

    # B. Recency adaptation.
    for n in (2, 3):
        recent = span(pickups, k - 1 - n, k - 1)
        before = span(pickups, k - 1 - n - 3, k - 1 - n)
        change = (abs(recent / before - 1) if recent is not None and before
                  else None)
        for t in (0.30, 0.50, 0.75):
            fired = change is not None and change > t
            for label, w in (("mild", 0.5), ("strong", 0.25)):
                rate = blend(prev_year, w, last3) if fired else base
                rates[f"Gate last {n} vs 3 before >{t:.0%}, {label}"] = rate
                rates[f"_fired last {n} >{t:.0%}"] = fired
    for h in (2, 3, 4):
        rates[f"EWMA half-life {h} + prev year"] = blend(ewma_rate(pickups, k, h), 0.5, prev_year)

    return rates


# ─────────────────────────────────────────────
# Walk every test pickup
# ─────────────────────────────────────────────

def run():
    pickups, checks = load()
    rng = random.Random(SEED)
    groups = {}             # (cid, year) -> class
    pvalues = {}
    seasons = {}
    tests = []

    for cid in sorted(pickups):
        ps = pickups[cid]
        cust_checks = checks.get(cid, [])
        running = RunningDaily()
        for p in ps[:MIN_PRIOR]:
            running.add(p)
        for k in range(MIN_PRIOR, len(ps)):
            if k > MIN_PRIOR:
                running.add(ps[k - 1])
            x = ps[k]
            if x["date"] < TEST_FROM or x["gallons"] <= 0:
                continue
            year = x["date"].year
            if (cid, year) not in groups:
                season = season_for_year(ps, year)
                groups[(cid, year)], pvalues[(cid, year)] = classify(season, rng)
                seasons[(cid, year)] = season
            start = clock_start(ps, k, cust_checks)
            days = (x["date"] - start).days
            if days <= 0:
                continue
            if CHECK_DAILY and k % 25 == 0:
                ref = ss.daily_production(ps[:k])
                assert all(abs(ref[d] - running.daily.get(d, 0.0)) < 1e-6 for d in ref), cid
            rates = model_rates(ps, k, start, seasons[(cid, year)], running.daily)
            if rates["50/50 (today)"] is None:
                continue
            tests.append({
                "customer_id": cid, "name": x["name"], "date": x["date"],
                "period": "choose" if year in CHOOSE else "confirm",
                "group": groups[(cid, year)], "days": days,
                "actual": x["gallons"], "rates": rates,
            })
    return tests, groups, pvalues, seasons


# ─────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────

def as_test(t, model):
    rate = t["rates"].get(model)
    if rate is None:
        return None
    return {"_signed": rate * t["days"] - t["actual"], "actual_gallons": t["actual"],
            "customer_id": t["customer_id"]}


def compare(tests, model, base="50/50 (today)", rng=None):
    """Paired WAPE of `model` vs `base` on pickups where both exist."""
    pairs = defaultdict(list)
    for t in tests:
        a, b = as_test(t, model), as_test(t, base)
        if a and b:
            pairs[t["customer_id"]].append((a, b))
    flat = [p for v in pairs.values() for p in v]
    if not flat:
        return None
    vol = sum(a["actual_gallons"] for a, _ in flat)
    wm = sum(abs(a["_signed"]) for a, _ in flat) / vol * 100
    wb = sum(abs(b["_signed"]) for _, b in flat) / vol * 100
    bias = sum(a["_signed"] for a, _ in flat) / vol * 100
    lo = hi = None
    if rng is not None and model != base:
        diffs = []
        groups = list(pairs.values())
        for _ in range(BOOTSTRAP_DRAWS):
            ea = eb = v = 0.0
            for g in groups:
                for _ in range(len(g)):
                    a, b = g[rng.randrange(len(g))]
                    ea += abs(a["_signed"]); eb += abs(b["_signed"]); v += a["actual_gallons"]
            diffs.append((ea - eb) / v * 100)
        diffs.sort()
        lo, hi = diffs[int(0.025 * BOOTSTRAP_DRAWS)], diffs[int(0.975 * BOOTSTRAP_DRAWS) - 1]
    return {"n": len(flat), "customers": len(pairs), "wape": wm, "base": wb,
            "diff": wm - wb, "lo": lo, "hi": hi, "bias": bias}


def fmt_row(label, r):
    if r is None:
        return f"| {label} | – | – | – | – | – | – |"
    ci = f"{r['lo']:+.1f} to {r['hi']:+.1f}" if r["lo"] is not None else "–"
    return (f"| {label} | {r['n']:,} / {r['customers']} | {r['base']:.1f}% | "
            f"{r['wape']:.1f}% | **{r['diff']:+.1f}** | {ci} | {r['bias']:+.1f}% |")


HEADER = ("| Model | Pickups / customers | 50/50 WAPE | Model WAPE | Difference | "
          "95% interval | Model bias |\n|---|---:|---:|---:|---:|---|---:|")


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

SEASONAL_MODELS = ["Sarge 50/50: last-yr ±3wk + last 2", "Sarge 70/30", "Sarge 30/70",
                   "Sarge ±6wk", "Level-adjusted last year", "Level × month index",
                   "Last 3 ÷ its index × coming index", "Seasonal blend (2026-09)"]


def main():
    tests, groups, pvalues, seasons = run()
    rng = random.Random(SEED)
    lines = [
        "# Phase 2: seasonal and recency-weighted rate formulas",
        "",
        "Generated by `analysis/backtest_phase2_rates.py`. Every model predicts a "
        "pickup's gallons as rate × days since the clock started (the later of the "
        "previous pickup and any empty check), using only data before that pickup. "
        "**Choose** = test pickups in 2023–24; **confirm** = 2025–26, where nothing "
        "was tuned. Differences are model minus 50/50 in WAPE points: **negative is "
        "better**. Intervals are a paired bootstrap resampling within customers.",
        "",
    ]

    # Step 1 — groups.
    by_class = defaultdict(set)
    for (cid, year), cls in groups.items():
        by_class[cls].add(cid)
    lines += ["## 1. Who is open but seasonal (scored from prior years only)", "",
              f"Seasonal-open = never below {OPEN_TROUGH} of an average month, amplitude ≥ "
              f"{SEASONAL_AMPLITUDE}, repeatability ≥ {SEASONAL_REPEAT} over the "
              f"{PROFILE_YEARS_BACK} complete years before the test year (2020 skipped), "
              f"≥ {MIN_GAL_PER_YEAR} gal/yr, **and** a repeatability that beats "
              f"{100 - PERMUTATION_P * 100:.0f}% of {PERMUTATIONS} month-shuffled versions "
              "of the same customer (the noise test).", "",
              "| Class (any test year) | Customers |", "|---|---:|"]
    for cls in sorted(by_class, key=lambda c: -len(by_class[c])):
        lines.append(f"| {cls} | {len(by_class[cls])} |")
    seasonal_ids = sorted(by_class["seasonal-open"])
    names = {t["customer_id"]: t["name"] for t in tests}
    lines += ["", "Seasonal-open customers: " + (", ".join(
        f"{names.get(c, c)}" for c in seasonal_ids) or "none"), ""]
    failed = sorted(by_class["seasonal, fails noise test"])
    if failed:
        lines += ["Looked seasonal but failed the noise test: "
                  + ", ".join(names.get(c, str(c)) for c in failed), ""]

    # Step 2 — seasonal models.
    seasonal = [t for t in tests if t["group"] == "seasonal-open"]
    lines += ["## 2. Seasonal-open customers", ""]
    for period in ("choose", "confirm"):
        sub = [t for t in seasonal if t["period"] == period]
        lines += [f"### {period.title()} ({'2023–24' if period == 'choose' else '2025–26'})",
                  "", HEADER]
        for m in SEASONAL_MODELS:
            lines.append(fmt_row(m, compare(sub, m, rng=rng)))
        lines.append("")

    # Step 3 — recency gates on every open, scored-or-not customer.
    open_tests = [t for t in tests if t["group"] not in ("closer",)]
    gate_models = [m for m in tests[0]["rates"] if (m.startswith("Gate") or m.startswith("EWMA"))]
    choose = [t for t in open_tests if t["period"] == "choose"]
    confirm = [t for t in open_tests if t["period"] == "confirm"]
    scored = {m: compare(choose, m) for m in gate_models}
    ranked = sorted((m for m in gate_models if scored[m]), key=lambda m: scored[m]["diff"])
    lines += ["## 3. Recency adaptation, every open customer", "",
              "A gate compares the rate over the last 2 (or 3) pickups with the 3 before. "
              "When they differ by more than the threshold the rate shifts toward the last "
              "3 pickups: **mild** = ½ prev-year + ½ last 3; **strong** = ¼ prev-year + "
              "¾ last 3. Otherwise it is the 50/50. EWMA = recency-weighted rate "
              "(half-life in pickups) blended ½/½ with prev-year.", "",
              "### Choose (2023–24): every setting", "", HEADER]
    for m in ranked:
        lines.append(fmt_row(m, scored[m]))
    best = ranked[:3]
    lines += ["", "### Confirm (2025–26): the 3 best settings from choose", "", HEADER]
    for m in best:
        lines.append(fmt_row(m, compare(confirm, m, rng=rng)))
    for m in best:
        if m.startswith("Gate"):
            n, t = m.split("last ")[1].split(" vs")[0], m.split(">")[1].split(",")[0]
            key = f"_fired last {n} >{t}"
            fired = [x for x in confirm if x["rates"].get(key)]
            share = len(fired) / len(confirm) * 100 if confirm else 0
            r = compare(fired, m, rng=rng)
            lines += ["", f"`{m}` fired on {len(fired):,} of {len(confirm):,} confirm "
                      f"pickups ({share:.0f}%)."]
            if r:
                lines += ["", HEADER, fmt_row(m + " — fired pickups only", r)]
    lines.append("")

    OUT_REPORT.write_text("\n".join(lines) + "\n")
    with open(OUT_DETAIL, "w", newline="") as f:
        cols = ["customer_id", "name", "date", "period", "group", "days", "actual"]
        models = [m for m in tests[0]["rates"] if not m.startswith("_")]
        w = csv.writer(f)
        w.writerow(cols + models)
        for t in tests:
            w.writerow([t[c] for c in cols] + [
                "" if t["rates"].get(m) is None else round(t["rates"][m] * t["days"], 1)
                for m in models])
    print(f"{len(tests):,} test pickups; seasonal-open customers: {len(seasonal_ids)}")
    print(f"Wrote analysis/{OUT_REPORT.name} and {OUT_DETAIL.name}")


if __name__ == "__main__":
    main()
