"""
Shared pieces for the three experiments: return events, operating calendars
built from earlier years only, open-day rates, and report helpers.

A RETURN EVENT is a gap longer than 120 days between pickups i-1 and i.
Pickup i is the first after the return (R1). A replay record with history
h = pickups[:k] has `since` = k - i post-return pickups in its history, so it
predicts pickup R(since + 1):

    since 0   predicts R1 -- never projected after a gap > 180 d (the row is
              stale, or a closer awaiting its first pickup); diagnostic only
    since 1   predicts R2 -- the first projected pickup after the return
    since 2   predicts R3
    since 3   predicts R4 -- the dormant restart (gap > 180 d) takes over here

dormant_restart.md's "1 since / 2 since" are since 1 and 2, i.e. R2 and R3.
"""

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "model_research_2026_10"))

import customer_factors as cf                    # noqa: E402
import harness                                   # noqa: E402
import metrics as m                              # noqa: E402
import replay                                    # noqa: E402
import seasonal_open                             # noqa: E402
import seasonality_score as ss                   # noqa: E402
from replay import bpt                           # noqa: E402

MIN_EVENT_GAP = 120
GAP_BINS = ((120, 180), (181, 270), (271, 365), (366, 450), (451, 540), (541, 730), (731, 10 ** 6))


def bin_label(gap):
    for lo, hi in GAP_BINS:
        if lo <= gap <= hi:
            return f"{lo}-{hi} d" if hi < 10 ** 6 else f"> {lo - 1} d"
    return None


def event_of(h, max_since=3):
    """(index i of R1, gap, since) for the latest return gap in h's last post-return window."""
    n = len(h)
    for i in range(n - 1, max(n - 1 - max_since, 0), -1):
        gap = (h[i]["date"] - h[i - 1]["date"]).days
        if gap > MIN_EVENT_GAP:
            return i, gap, n - i
    return None


def event_since0(ps, k):
    """The record predicting pickup k when pickup k itself is R1 (gap into k > 120)."""
    gap = (ps[k]["date"] - ps[k - 1]["date"]).days
    return (k, gap, 0) if gap > MIN_EVENT_GAP else None


# ─────────────────────────────────────────────
# Operating calendars (months 0-11 closed), as of Jan 1 of a year
# ─────────────────────────────────────────────

class Calendars:
    """
    detector  seasonal_open.closed_months: 2 complete prior years, volume and
              median-gap gates. Production's closer detector.
    loose     the same zero-keeping shape and closed block (< 0.10 of an
              average month for >= 2 consecutive months), WITHOUT the
              eligibility gates, from the 1-2 complete years before the year
              (2020 skipped). Fires on sparse customers too.
    Both read only pickups dated before Jan 1 of the year.
    """

    def __init__(self):
        self.cache = {}

    def get(self, cid, ps, year, kind):
        key = (cid, year, kind)
        if key not in self.cache:
            self.cache[key] = self._build(ps, year, kind)
        return self.cache[key]

    @staticmethod
    def _build(ps, year, kind):
        if kind == "detector":
            return frozenset(seasonal_open.closed_months(ps, year) or ())
        prior = [p for p in ps if p["date"] < date(year, 1, 1)]
        if len(prior) < 3:
            return frozenset()
        years = [y for y in seasonal_open.profile_years_back(year, 2)
                 if prior[0]["date"] < date(y, 1, 1)]
        if not years:
            return frozenset()
        daily = ss.daily_production(prior)
        profile = {y: idx for y in years if (idx := cf.year_index(daily, y)) is not None}
        shape = seasonal_open.zero_keeping_shape(profile, years)
        return frozenset(seasonal_open.closed_block(shape) if shape else ())


def open_days(a, b, closed):
    """Days in (a, b] outside the closed months."""
    if not closed:
        return (b - a).days
    return sum(1 for d in range(1, (b - a).days + 1)
               if (a + timedelta(days=d)).month - 1 not in closed)


def span_open(h, lo, hi, closed, gallons=None):
    """Gallons at lo+1..hi over the OPEN days from lo to hi (bsm.rate_span on open days)."""
    if lo is None or lo < 0 or hi <= lo:
        return None
    days = sum(open_days(h[i - 1]["date"], h[i]["date"], closed) for i in range(lo + 1, hi + 1))
    if days <= 0:
        return None
    g = gallons or (lambda i: h[i]["gallons"])
    return sum(g(i) for i in range(lo + 1, hi + 1)) / days


def pooled_open(h, closed, gallons=None):
    n = len(h)
    return span_open(h, max(0, n - 1 - bpt.WINDOW), n - 1, closed, gallons) if n >= 2 else None


def prev_year_open(h, asof, closed, gallons=None):
    """rate_prev_year_asof with open days in the denominator."""
    if len(h) < 2:
        return None
    cutoff = asof - timedelta(days=bpt.YEAR_DAYS)
    i = next((i for i in range(len(h) - 1, -1, -1) if h[i]["date"] <= cutoff), None)
    if i is None or i == len(h) - 1:
        return None
    return span_open(h, i, len(h) - 1, closed, gallons)


def median_pickup(h):
    return statistics.median(p["gallons"] for p in h[1:]) if len(h) > 1 else None


# ─────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────

def evaluate(results, experiment, candidate, scope, rows, note=""):
    """harness.evaluate, appended to `results`; returns the per-year dict."""
    out, yrs = harness.evaluate(experiment, candidate, scope, rows, note)
    results.extend(out)
    return yrs


def stat_line(rows, key="base"):
    s = m.summary(rows, key)
    if not s:
        return "| – | – | – | – | – |"
    return (f"{s['pickups']:,} ({s['customers']}) | {s['actual_gal']:,.0f} | {s['wape']:.1f}% "
            f"| {s['bias']:+.1f}% | {s['median_ape']:.1f}%")


def top_contributors(rows, n=5):
    """Customers contributing most to Δ absolute error (cand - base), as text."""
    per = {}
    for r in rows:
        d = abs(r["cand"] - r["actual"]) - abs(r["base"] - r["actual"])
        per.setdefault(r["cid"], [r["name"], 0.0])[1] += d
    act = sum(r["actual"] for r in rows) or 1
    worst = sorted(per.items(), key=lambda kv: kv[1][1])
    pick = worst[:n] + worst[-n:] if len(worst) > 2 * n else worst
    total = sum(v[1] for v in per.values())
    lines = [f"Δ total {100 * total / act:+.2f} pts; the {n} customers helped most and hurt most:"]
    for cid, (name, d) in pick:
        lines.append(f"- {name} ({cid}): {100 * d / act:+.2f} pts")
    return "\n".join(lines)


def capped(rows, capacities):
    """Hindsight sensitivity: base and cand both capped at TODAY's listed capacity."""
    out = []
    for r in rows:
        cap = capacities.get(r["cid"])
        out.append(dict(r, base=min(r["base"], cap) if cap else r["base"],
                        cand=min(r["cand"], cap) if cap else r["cand"]))
    return out
