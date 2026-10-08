"""
Seasonal-open classification, scored from prior complete years only.

Shared by backtest_phase2_rates.py (where it was chosen, 2026-10-07) and
build_projection_table.py (which routes the customers it finds to the
level x month index rate). It lives in its own module because the backtest
imports the builder, so the builder cannot import the backtest.

Seasonal-open = never below OPEN_TROUGH of an average month, amplitude >=
SEASONAL_AMPLITUDE, repeatability >= SEASONAL_REPEAT over the
PROFILE_YEARS_BACK complete years before the test year (SKIP_YEARS left out),
at least MIN_GAL_PER_YEAR, AND a repeatability that beats the month-shuffled
versions of the same customer (the noise test).
"""

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
