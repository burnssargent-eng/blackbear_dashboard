#!/usr/bin/env python3
"""
Per-customer monthly and quarterly cyclicality factors, and a screen of which
customers have a pattern worth using.

This is the factor-building half of the customer-cyclicality work. The models
that use these factors, and the verdict on whether they are worth using at all,
live in backtest_customer_factors.py.

WHAT A FACTOR IS

    Production by month comes from seasonality_score.py's method, so these
    factors agree with seasonality_scores.md: each pickup's gallons are spread
    back over the days since the previous pickup (60-day cap), and a month's
    rate is its gallons per day. A month's INDEX is that rate divided by the
    customer-year's average month, so 1.00 is an average month and 1.30 is 30%
    above it. A quarter's index is the mean of its three months.

NO LOOKAHEAD

    A factor for a pickup in year Y averages the indexes of complete years
    before Y only, never 2020, and never Y itself. prior_years() is the single
    place that rule lives, and the backtest asserts it.

    A month with no production in a year is dropped from that month's average
    rather than averaged in as a zero: with two prior years, one empty month
    would halve the index and the multiplier would never recover. For customers
    that close seasonally a zero IS the signal, which is exactly why they are
    held out as an unsolved group rather than handed a multiplier.

HOW A FACTOR IS APPLIED

    A pickup's gallons accumulate over the days since the previous pickup, and
    2 of every 3 of those windows cross a month boundary, so the factor is
    averaged across the window's days (gap_weighted) rather than read off the
    pickup's own month.

    python3 analysis/customer_factors.py

Writes customer_cyclicality_tests.csv, customer_monthly_factors.csv and
customer_cyclicality.md. Analysis only; writes nothing outside analysis/.
"""

import csv
import json
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import backtest_steady_rate as bt
import seasonality_score as ss

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Sample: still producing, with enough history that a 2023 test pickup has two
# complete prior years to build a factor from.
POOL_YEARS = tuple(range(2021, 2027))     # a pickup in every one of these
PROFILE_YEARS = tuple(range(2021, 2026))  # complete calendar years; 2020 excluded
SCREEN_YEARS = (2023, 2024, 2025)         # descriptive screen, matches seasonality_scores.md
MIN_AVG_GALLONS = 500                     # averaged over SCREEN_YEARS

MONTHS = ss.MONTHS
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]

# A month is "materially" off normal outside these bounds.
HIGH, LOW = 1.15, 0.85

MIN_YEARS_FOR_FACTOR = 2
MIN_AMPLITUDE = 0.15            # below this the swing is not worth correcting
MIN_REPEATABILITY = 0.6         # the routing rule's existing threshold
REGION_MIN_CUSTOMERS = 8        # a thinner region falls through to the class factor
SHRINK_WEIGHTS = (0.25, 0.50, 0.75)


# ─────────────────────────────────────────────
# Sample
# ─────────────────────────────────────────────

def sample_customers(pickups):
    """customer_ids with a pickup in every POOL_YEARS year and enough volume."""
    out = []
    for cid, ps in pickups.items():
        gallons = defaultdict(int)
        for p in ps:
            gallons[p["date"].year] += p["gallons"]
        if (all(gallons[y] > 0 for y in POOL_YEARS)
                and statistics.fmean(gallons[y] for y in SCREEN_YEARS) >= MIN_AVG_GALLONS):
            out.append(cid)
    return sorted(out)


# ─────────────────────────────────────────────
# Profiles
# ─────────────────────────────────────────────

def year_index(daily, year):
    """A year's 12 monthly indexes, or None when the year has no production."""
    rates = ss.monthly_rates(daily, year)
    mean = statistics.fmean(rates)
    return [r / mean for r in rates] if mean else None


def customer_profiles(pickups):
    """customer_id -> {year: [12 indexes]} for every year with production."""
    profiles = {}
    for cid, ps in pickups.items():
        daily = ss.daily_production(ps)
        years = {}
        for y in PROFILE_YEARS:
            idx = year_index(daily, y)
            if idx is not None:
                years[y] = idx
        profiles[cid] = years
    return profiles


def prior_years(test_year):
    """The complete years a factor for `test_year` may use. The no-lookahead rule."""
    return tuple(y for y in PROFILE_YEARS if y < test_year)


def factor_from_years(profile, years):
    """
    A 12-month factor averaged over `years`, renormalised to average 1.00.

    Returns (factor, years_used). A month with no production in a year is left
    out of that month's average rather than counted as a zero.
    """
    used = [y for y in years if y in profile]
    if len(used) < MIN_YEARS_FOR_FACTOR:
        return None, used

    factor = []
    for m in range(12):
        seen = [profile[y][m] for y in used if profile[y][m] > 0]
        factor.append(statistics.fmean(seen) if seen else 1.0)

    mean = statistics.fmean(factor)
    if mean <= 0:
        return None, used
    return [f / mean for f in factor], used


def quarterly_factor(factor):
    """The 4 quarter indexes of a 12-month factor, renormalised to average 1.00."""
    quarters = [statistics.fmean(factor[q * 3:q * 3 + 3]) for q in range(4)]
    mean = statistics.fmean(quarters)
    return [q / mean for q in quarters]


def monthly_from_quarterly(factor):
    """A 12-month vector holding each month's quarter index — the quarterly model."""
    quarters = quarterly_factor(factor)
    return [quarters[(m // 3)] for m in range(12)]


# ─────────────────────────────────────────────
# Applying a factor
# ─────────────────────────────────────────────

def gap_weighted(factor, start, end):
    """
    The factor averaged over the days (start, end] — the days whose production
    the pickup at `end` collected. Month-by-month rather than day-by-day.
    """
    if end <= start:
        return 1.0
    total = days = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        month_end = date(cur.year + cur.month // 12, cur.month % 12 + 1, 1) - timedelta(days=1)
        seg_end = min(month_end, end)
        n = (seg_end - cur).days + 1
        total += factor[cur.month - 1] * n
        days += n
        cur = seg_end + timedelta(days=1)
    return total / days if days else 1.0


def shrink(factor, weight, target=1.0):
    """Pull a factor toward `target` (1.00, or a region/class factor)."""
    if isinstance(target, (int, float)):
        return [1 * target + weight * (f - target) for f in factor]
    return [t + weight * (f - t) for f, t in zip(factor, target)]


def cap(value, low, high):
    return min(high, max(low, value))


# ─────────────────────────────────────────────
# Region and class factors
# ─────────────────────────────────────────────

def regions_by_customer():
    """customer_id -> region name, from the site's own region_customers mapping."""
    data = json.loads((ROOT / "oil_data.json").read_text())

    # Theme regions (ski slopes, summer snack stops) are overlays on the map,
    # not places on it: a Killington customer's seasonality belongs to its
    # geography, not to a statewide theme group. The key is absent from older
    # exports, which simply means there is nothing to skip.
    themes = set(data.get("theme_regions", ()))

    regions = defaultdict(list)
    for region, ids in data["region_customers"].items():
        for cid in ids:
            regions[int(cid)].append(region)

    out = {}
    for cid, names in regions.items():
        named = [n for n in names if n != "Other" and n not in themes]
        # A customer in two geographic regions — Newport City sits in both
        # Northwest and North-Northeast — resolves to the more specific name
        # deterministically rather than averaging two regions together.
        out[cid] = min(named, key=lambda n: (len(n), n)) if named else "Other"
    return out


def group_factor(members, factors, exclude):
    """
    The average factor of a group, leaving the customer out so nobody ever
    falls back onto their own history.
    """
    vectors = [factors[c] for c in members if c != exclude and factors.get(c)]
    if not vectors:
        return None
    factor = [statistics.fmean(v[m] for v in vectors) for m in range(12)]
    mean = statistics.fmean(factor)
    return [f / mean for f in factor] if mean > 0 else None


# ─────────────────────────────────────────────
# Screen
# ─────────────────────────────────────────────

def amplitude(factor):
    return statistics.pstdev(factor)


def repeatability(profile, years):
    """Mean leave-one-out correlation between years' shapes, as seasonality_score does."""
    used = [y for y in years if y in profile]
    if len(used) < 3:
        return None
    scores = []
    for y in used:
        others = [statistics.fmean(profile[o][m] for o in used if o != y) for m in range(12)]
        try:
            scores.append(statistics.correlation(profile[y], others))
        except statistics.StatisticsError:
            return None
    return statistics.fmean(scores)


def friedman(profile, years, quarterly=False):
    """
    Friedman across months (or quarters) using complete years as blocks.

    Reported because the brief asks for it, but it cannot decide anything here:
    with 3 years and 114 customers the smallest attainable p-value does not
    survive correction for multiple comparisons even for a flawless pattern.
    """
    try:
        from scipy.stats import friedmanchisquare
    except ImportError:
        return None

    rows = []
    for y in years:
        if y not in profile or any(v <= 0 for v in profile[y]):
            continue          # Friedman needs complete blocks
        rows.append(quarterly_factor(profile[y]) if quarterly else profile[y])
    if len(rows) < 3:
        return None
    columns = list(zip(*rows))
    try:
        return friedmanchisquare(*columns).pvalue
    except ValueError:
        return None


def benjamini_hochberg(pvalues):
    """q-values for a dict of {key: p}, keys with p=None left as None."""
    pairs = sorted(((k, p) for k, p in pvalues.items() if p is not None), key=lambda kv: kv[1])
    n = len(pairs)
    q, previous = {}, 1.0
    for rank, (key, p) in enumerate(reversed(pairs), start=1):
        value = min(previous, p * n / (n - rank + 1))
        q[key] = previous = value
    return {k: q.get(k) for k in pvalues}


def recommend(row, region_size):
    """
    Which factor level this customer should get, and why.

    A screen, not the verdict: the backtest decides whether any of it is used.
    """
    if row["closes"]:
        return "none", "closes seasonally — unsolved group, no multiplier"
    if row["years_used"] < MIN_YEARS_FOR_FACTOR:
        return "none", f"only {row['years_used']} usable prior year(s)"
    if row["amplitude"] < MIN_AMPLITUDE:
        return "none", f"swing {row['amplitude']:.2f} is inside the noise floor"

    repeat = row["repeatability"]
    if repeat is not None and repeat >= MIN_REPEATABILITY:
        return ("customer_quarterly",
                f"swing {row['amplitude']:.2f} repeating at {repeat:.2f} — "
                "quarterly preferred as the steadier of the two")
    if region_size >= REGION_MIN_CUSTOMERS:
        return ("region_monthly",
                f"swing {row['amplitude']:.2f} but repeatability "
                f"{'n/a' if repeat is None else format(repeat, '.2f')} — own history unreliable")
    return ("none",
            f"swing {row['amplitude']:.2f} does not repeat and region is too small to borrow from")


# ─────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────

def build():
    """Everything the backtest needs: sample, profiles, factors by vintage, screen rows."""
    pickups = bt.load_pickups(ids=None)
    sample = sample_customers(pickups)
    profiles = customer_profiles(pickups)
    regions = regions_by_customer()

    scores = {cid: ss.score(pickups[cid]) for cid in sample}
    classes = {cid: ss.classify(scores[cid]) for cid in sample}
    closers = {cid for cid in sample if scores[cid]["trough_index"] < 0.1}

    # Factor vintages: a pure function of (customer, complete years before Y),
    # so there are at most four per customer across test years 2023-2026.
    vintages = {}
    for cid in sample:
        for test_year in range(2023, 2027):
            factor, used = factor_from_years(profiles[cid], prior_years(test_year))
            vintages[(cid, test_year)] = (factor, used)

    members = defaultdict(list)
    for cid in sample:
        members[("region", regions.get(cid, "Other"))].append(cid)
        members[("class", classes[cid])].append(cid)

    return {
        "pickups": pickups, "sample": sample, "profiles": profiles, "regions": regions,
        "scores": scores, "classes": classes, "closers": closers,
        "vintages": vintages, "members": members,
    }


def fallback_factors(data, test_year):
    """(region_factors, class_factors) for one vintage, each leaving self out."""
    latest = {cid: data["vintages"][(cid, test_year)][0] for cid in data["sample"]}
    open_customers = {cid: f for cid, f in latest.items() if cid not in data["closers"]}
    region, klass = {}, {}
    for cid in data["sample"]:
        region_name = data["regions"].get(cid, "Other")
        peers = [c for c in data["members"][("region", region_name)] if c not in data["closers"]]
        region[cid] = (group_factor(peers, open_customers, cid)
                       if len(peers) >= REGION_MIN_CUSTOMERS else None)
        class_peers = [c for c in data["members"][("class", data["classes"][cid])]
                       if c not in data["closers"]]
        klass[cid] = group_factor(class_peers, open_customers, cid)
    return region, klass


# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────

def screen_rows(data):
    rows = []
    for cid in data["sample"]:
        profile = data["profiles"][cid]
        factor, used = factor_from_years(profile, PROFILE_YEARS)
        if factor is None:
            factor, used = [1.0] * 12, used
        quarters = quarterly_factor(factor)
        score = data["scores"][cid]
        rows.append({
            "customer_id": cid,
            "name": data["pickups"][cid][-1]["name"],
            "town": data["pickups"][cid][-1]["town"],
            "class": data["classes"][cid],
            "closes": cid in data["closers"],
            "avg_gallons_per_year": score["avg_gallons_per_year"],
            "years_used": len(used),
            "amplitude": amplitude(factor),
            "repeatability": repeatability(profile, PROFILE_YEARS),
            "peak_month": MONTHS[max(range(12), key=factor.__getitem__)],
            "trough_month": MONTHS[min(range(12), key=factor.__getitem__)],
            "peak_quarter": QUARTERS[max(range(4), key=quarters.__getitem__)],
            "trough_quarter": QUARTERS[min(range(4), key=quarters.__getitem__)],
            "months_above_1_15": sum(1 for f in factor if f > HIGH),
            "months_below_0_85": sum(1 for f in factor if f < LOW),
            "quarters_above_1_15": sum(1 for q in quarters if q > HIGH),
            "quarters_below_0_85": sum(1 for q in quarters if q < LOW),
            "friedman_p_monthly": friedman(profile, PROFILE_YEARS),
            "friedman_p_quarterly": friedman(profile, PROFILE_YEARS, quarterly=True),
            "factor": factor, "quarters": quarters,
        })

    for key, quarterly in (("friedman_p_monthly", False), ("friedman_p_quarterly", True)):
        qs = benjamini_hochberg({r["customer_id"]: r[key] for r in rows})
        for r in rows:
            r[key.replace("_p_", "_q_")] = qs[r["customer_id"]]

    for r in rows:
        region_name = data["regions"].get(r["customer_id"], "Other")
        size = len([c for c in data["members"][("region", region_name)]
                    if c not in data["closers"]])
        r["recommended_factor_level"], r["reason"] = recommend(r, size)
    return rows


def write_screen(rows):
    fields = (["customer_id", "name", "town", "class", "avg_gallons_per_year", "years_used",
               "amplitude", "repeatability", "peak_month", "trough_month", "peak_quarter",
               "trough_quarter", "months_above_1_15", "months_below_0_85",
               "quarters_above_1_15", "quarters_below_0_85", "friedman_p_monthly",
               "friedman_p_quarterly", "friedman_q_monthly", "friedman_q_quarterly",
               "recommended_factor_level", "reason"] + MONTHS + QUARTERS)
    out = []
    for r in rows:
        row = {k: r[k] for k in fields if k in r}
        row["avg_gallons_per_year"] = round(r["avg_gallons_per_year"])
        row["amplitude"] = round(r["amplitude"], 3)
        row["repeatability"] = None if r["repeatability"] is None else round(r["repeatability"], 3)
        for key in ("friedman_p_monthly", "friedman_p_quarterly",
                    "friedman_q_monthly", "friedman_q_quarterly"):
            row[key] = None if r[key] is None else round(r[key], 5)
        row.update({m: round(v, 3) for m, v in zip(MONTHS, r["factor"])})
        row.update({q: round(v, 3) for q, v in zip(QUARTERS, r["quarters"])})
        out.append(row)
    bt.write_csv(HERE / "customer_cyclicality_tests.csv", fields, out)


def write_factors(data, rows):
    """One row per customer-month, on the newest vintage (prior years through 2025)."""
    test_year = 2026
    region, klass = fallback_factors(data, test_year)
    by_id = {r["customer_id"]: r for r in rows}
    fields = ["customer_id", "name", "month", "factor_vintage", "raw_customer_factor",
              "shrink_25", "shrink_50", "shrink_75", "region_factor_if_available",
              "class_factor_if_available", "recommended_factor", "recommended_factor_level"]
    out = []
    for cid in data["sample"]:
        factor, used = data["vintages"][(cid, test_year)]
        level = by_id[cid]["recommended_factor_level"]
        quarters = monthly_from_quarterly(factor) if factor else None
        for m, month in enumerate(MONTHS):
            raw = factor[m] if factor else None
            region_value = region[cid][m] if region[cid] else None
            class_value = klass[cid][m] if klass[cid] else None
            if level == "customer_quarterly" and quarters:
                recommended = quarters[m]
            elif level == "customer_monthly" and raw is not None:
                recommended = raw
            elif level == "region_monthly" and region_value is not None:
                recommended = region_value
            elif level == "class_monthly" and class_value is not None:
                recommended = class_value
            else:
                recommended = 1.0
            out.append({
                "customer_id": cid, "name": data["pickups"][cid][-1]["name"], "month": month,
                "factor_vintage": "-".join(str(y) for y in (used[0], used[-1])) if used else "",
                "raw_customer_factor": None if raw is None else round(raw, 3),
                **{f"shrink_{int(w * 100)}": None if raw is None else round(1 + w * (raw - 1), 3)
                   for w in SHRINK_WEIGHTS},
                "region_factor_if_available": None if region_value is None else round(region_value, 3),
                "class_factor_if_available": None if class_value is None else round(class_value, 3),
                "recommended_factor": round(recommended, 3),
                "recommended_factor_level": level,
            })
    bt.write_csv(HERE / "customer_monthly_factors.csv", fields, out)


def write_report(data, rows):
    by_class = defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)
    levels = defaultdict(int)
    for r in rows:
        levels[r["recommended_factor_level"]] += 1

    strong = sorted((r for r in rows if not r["closes"]
                     and r["repeatability"] is not None
                     and r["repeatability"] >= MIN_REPEATABILITY
                     and r["amplitude"] >= MIN_AMPLITUDE),
                    key=lambda r: -r["amplitude"])

    lines = [
        "# Customer cyclicality screen\n",
        "Generated by `analysis/customer_factors.py`. Factors and the screen only — "
        "whether any of this is worth using is decided in "
        "[`customer_factor_model_test.md`](customer_factor_model_test.md).\n",
        f"**Sample:** {len(rows)} customers with a pickup in every year "
        f"{POOL_YEARS[0]}–{POOL_YEARS[-1]} averaging at least {MIN_AVG_GALLONS:,} gallons a "
        f"year over {SCREEN_YEARS[0]}–{SCREEN_YEARS[-1]}.\n",
        "A month's **index** is its gallons per day divided by the customer-year's average "
        "month, so 1.00 is an average month. Production by month uses "
        "`seasonality_score.py`'s spreading method, so these agree with "
        "`seasonality_scores.md`.\n",
        "## What the screen recommends\n",
        "| Factor level | Customers |", "|---|---:|",
    ]
    for level, n in sorted(levels.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {level} | {n} |")

    lines += [
        "\n## By class\n",
        "| Class | Customers | Median amplitude | Median repeatability | Months above 1.15 (median) |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, group in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        repeats = [r["repeatability"] for r in group if r["repeatability"] is not None]
        lines.append(
            f"| {name} | {len(group)} | "
            f"{statistics.median(r['amplitude'] for r in group):.2f} | "
            f"{statistics.median(repeats):.2f} | "
            f"{statistics.median(r['months_above_1_15'] for r in group):.0f} |")

    lines += [
        f"\n## Customers with a real, repeating pattern — {len(strong)} of {len(rows)}\n",
        "Amplitude ≥ 0.15 and repeatability ≥ 0.6, closers excluded.\n",
        "| Customer | Town | Class | Amplitude | Repeat | Peak | Trough | Peak qtr | Trough qtr |",
        "|---|---|---|---:|---:|---|---|---|---|",
    ]
    for r in strong:
        lines.append(
            f"| {r['name']} | {r['town']} | {r['class']} | {r['amplitude']:.2f} | "
            f"{r['repeatability']:.2f} | {r['peak_month']} {max(r['factor']):.2f} | "
            f"{r['trough_month']} {min(r['factor']):.2f} | "
            f"{r['peak_quarter']} {max(r['quarters']):.2f} | "
            f"{r['trough_quarter']} {min(r['quarters']):.2f} |")

    monthly_p = [r["friedman_p_monthly"] for r in rows if r["friedman_p_monthly"] is not None]
    monthly_q = [r["friedman_q_monthly"] for r in rows if r["friedman_q_monthly"] is not None]
    lines += [
        "\n## On the Friedman tests\n",
        f"Computed where a customer has at least 3 complete years: "
        f"{len(monthly_p)} of {len(rows)} customers monthly. "
        f"{sum(1 for p in monthly_p if p < 0.05)} come in under an uncorrected 0.05, "
        f"{sum(1 for q in monthly_q if q < 0.05)} under a Benjamini-Hochberg q of 0.05.\n",
        "**They cannot decide anything here.** With 3 years and 12 months the smallest "
        "p-value the test can produce — a flawless, identical pattern every year — is "
        "5.3e-4, against a Bonferroni threshold of 4.4e-4 across these customers. "
        "Quarterly is worse: its floor is 2.9e-2 at 3 years, so no quarterly pattern can "
        "ever reach corrected significance at the number of years available. They are "
        "reported as description; held-out prediction decides.\n",
        "## Caveats\n",
        "- **Survivorship.** Requiring a pickup in every year through 2026 selects "
        "customers who stayed. The factors are not representative of customers who left.",
        "- **The screen is descriptive and uses all years 2021–2025.** The backtest's "
        "factors are strictly no-lookahead; this table is not, and is for reading, not "
        "for gating.",
        "- **Closers get no multiplier.** A closed month is a real zero, not a low season; "
        "they stay an unsolved group pending a reopening rule.",
    ]
    (HERE / "customer_cyclicality.md").write_text("\n".join(lines) + "\n")


def main():
    scores_csv = HERE / "seasonality_scores.csv"
    if scores_csv.exists():
        existing = list(csv.DictReader(scores_csv.open()))
        classes = defaultdict(int)
        for r in existing:
            classes[r["class"]] += 1
        factors = [statistics.fmean(float(r[m]) for m in MONTHS) for r in existing]
        print(f"seasonality_scores.csv: {len(existing)} rows — "
              + ", ".join(f"{n} {k}" for k, n in sorted(classes.items(), key=lambda kv: -kv[1])))
        print(f"  avg_gallons_per_year >= 500: "
              f"{sum(1 for r in existing if float(r['avg_gallons_per_year']) >= 500)}, "
              f">= 1000: {sum(1 for r in existing if float(r['avg_gallons_per_year']) >= 1000)}")
        print(f"  monthly factors normalised around 1.0: each row averages "
              f"{min(factors):.3f}-{max(factors):.3f}")
    else:
        print("seasonality_scores.csv not present — run seasonality_score.py first")

    data = build()
    rows = screen_rows(data)
    write_screen(rows)
    write_factors(data, rows)
    write_report(data, rows)

    levels = defaultdict(int)
    for r in rows:
        levels[r["recommended_factor_level"]] += 1
    print(f"\n{len(rows)} customers in the sample; factor levels recommended by the screen:")
    for level, n in sorted(levels.items(), key=lambda kv: -kv[1]):
        print(f"  {level:20} {n}")
    print("\nWrote analysis/customer_cyclicality_tests.csv, customer_monthly_factors.csv "
          "and customer_cyclicality.md")


if __name__ == "__main__":
    main()
