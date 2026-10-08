"""
Seasonal classification, scored from prior complete years only: the
seasonal-open test, and the seasonal-closed detector.

Shared by backtest_phase2_rates.py (where it was chosen, 2026-10-07) and
build_projection_table.py (which routes the customers it finds to the
level x month index rate). It lives in its own module because the backtest
imports the builder, so the builder cannot import the backtest.

Seasonal-open = never below OPEN_TROUGH of an average month, amplitude >=
SEASONAL_AMPLITUDE, repeatability >= SEASONAL_REPEAT over the
PROFILE_YEARS_BACK complete years before the test year (SKIP_YEARS left out),
at least MIN_GAL_PER_YEAR, AND a repeatability that beats the month-shuffled
versions of the same customer (the noise test).

Seasonal-closed (backtest_seasonal_closed.py, 2026-10-08) = over the
CLOSED_YEARS_BACK complete years before the year, a cyclic block of at least
CLOSED_MIN_MONTHS months below CLOSED_INDEX of an average month, for a
customer with at least MIN_GAL_PER_YEAR and a median gap of at most the
60-day spread cap (longer gaps leave artificial empty months).

THE SHAPE KEEPS ZEROS. cf.factor_from_years leaves a month with no production
out of its average and sets an all-zero month to 1.0, which hid every closure:
closers passed the seasonal-open test until 2026-10-08.
"""

import statistics

from datetime import date

import customer_factors as cf
import seasonality_score as ss

PROFILE_YEARS_BACK = 3          # repeatability needs 3 years
SKIP_YEARS = {2020}             # the pandemic year distorts a shape
SEASONAL_AMPLITUDE = 0.25       # the house thresholds (seasonality_score)
SEASONAL_REPEAT = 0.60
OPEN_TROUGH = 0.10              # below this a month is "closed" (bsm.CLOSED_INDEX)
MIN_GAL_PER_YEAR = 300
PERMUTATIONS = 1000
PERMUTATION_P = 0.05

SEASONAL_OPEN = "seasonal-open"

CLOSED_YEARS_BACK = 2           # 1 year held out 15% of gallons; 3 added nothing
CLOSED_INDEX = 0.10
CLOSED_MIN_MONTHS = 2
CLOSED_MAX_MEDIAN_GAP = ss.SPREAD_CAP_DAYS
CLOSED_MIN_PICKUPS = 4


def profile_years_back(test_year, k):
    years, y = [], test_year - 1
    while len(years) < k and y >= 2016:
        if y not in SKIP_YEARS:
            years.append(y)
        y -= 1
    return sorted(years)


def zero_keeping_shape(profile, years):
    """Mean index per month across `years`, zeros included, renormalised to 1."""
    used = [y for y in years if y in profile]
    if not used:
        return None
    raw = [statistics.fmean(profile[y][m] for y in used) for m in range(12)]
    mean = statistics.fmean(raw)
    return [r / mean for r in raw] if mean > 0 else None


def closed_block(shape, threshold=CLOSED_INDEX, min_months=CLOSED_MIN_MONTHS):
    """The longest cyclic run of months below `threshold`, if long enough."""
    flags = [v < threshold for v in shape]
    if all(flags) or not any(flags):
        return set()
    best = set()
    for start in range(12):
        if flags[start] and not flags[(start - 1) % 12]:
            run, i = set(), start
            while flags[i % 12] and len(run) < 12:
                run.add(i % 12)
                i += 1
            if len(run) > len(best):
                best = run
    return best if len(best) >= min_months else set()


def closer_eligible(pickups, years, profile):
    span = [p for p in pickups if p["date"].year in years]
    if len(span) < CLOSED_MIN_PICKUPS or any(y not in profile for y in years):
        return False
    if sum(p["gallons"] for p in span) / len(years) < MIN_GAL_PER_YEAR:
        return False
    gaps = [g for g in ((b["date"] - a["date"]).days for a, b in zip(span, span[1:])) if g > 0]
    # A closer's off-season gap is one a year; the median sees through it.
    return bool(gaps) and statistics.median(gaps) <= CLOSED_MAX_MEDIAN_GAP


def closed_months(pickups, test_year, k=CLOSED_YEARS_BACK,
                  threshold=CLOSED_INDEX, min_months=CLOSED_MIN_MONTHS):
    """
    Detected closed months (0 = Jan) for `test_year`, from the k complete
    years before it; an empty set when open all year; None when undecidable.
    """
    years = profile_years_back(test_year, k)
    if len(years) < k:
        return None
    prior = [p for p in pickups if p["date"] < date(test_year, 1, 1)]
    if len(prior) < 2:
        return None
    daily = ss.daily_production(prior)
    profile = {y: idx for y in years if (idx := cf.year_index(daily, y)) is not None}
    if not closer_eligible(prior, years, profile):
        return None
    shape = zero_keeping_shape(profile, years)
    return closed_block(shape, threshold, min_months) if shape else None


def profile_years(test_year):
    return profile_years_back(test_year, PROFILE_YEARS_BACK)


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
    used = [y for y in years if y in profile]
    if len(used) < cf.MIN_YEARS_FOR_FACTOR:
        return None
    factor = zero_keeping_shape(profile, used)
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
    """(class, permutation p or None). `rng` drives the shuffles."""
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
        return (SEASONAL_OPEN if p <= PERMUTATION_P else "seasonal, fails noise test"), p
    return "steady/other", None
