#!/usr/bin/env python3
"""
Two answers to the Schaffers miss (live test, 2026-10-05: projected 17 gal,
collected 100).

  consistency gate   Sarge, 2026-10-09: when the last n gaps (3-5) span at most
                     W days (60-120) and their interval rates agree (CV <= c),
                     use the pooled rate of those gaps instead of production.
  dormant restart    a gap longer than D days inside a production window, with
                     3+ pickups since: the pooled rate over the post-gap pickups
                     only, the ladder's new-customer rule. The production rule is
                     build_projection_table.dormant_restart, called here as is.

Schaffers had no pickups for 448 days (2025-04-04 to 2026-06-26); both halves
of the 50/50 divided by that dead stretch (0.46 gal/day against ~2 since).

Production and the test pickups are backtest_recent_override.run(), unchanged:
every open customer with 8+ prior pickups (closers left out), projected gallons
= rate x days since the clock started. Choose = 2021-23, confirm = 2024-26.
Intervals resample whole CUSTOMERS (a customer's pickups are not independent),
which is wider than the within-customer resampling of the older reports.

    python3 analysis/backtest_dormant_restart.py

Writes analysis/dormant_restart.md. Standard library only; deterministic.
"""

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

import backtest_phase2_rates as p2
import backtest_recent_override as ro
import backtest_seasonal_formulas as sf
import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import build_projection_table as bpt
import seasonal_open as so

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "dormant_restart.md"

SEED = 42
DRAWS = 1000
GATE_N = (3, 4, 5)               # gaps in the window
GATE_W = (60, 90, 120)           # days those gaps may span
GATE_CV = (0.25, 0.4)            # CV of their interval rates
DORMANT = (120, 180)             # 180 = STALE_DAYS, the production rule

PROD = "Production (today)"


def gate_label(n, w, c):
    return f"Gate: last {n} gaps ≤ {w} d, CV ≤ {c}"


def restart_label(d):
    return f"Restart after a gap > {d} d" + (" (adopted)" if d == bpt.STALE_DAYS else "")


GATES = [gate_label(n, w, c) for n in GATE_N for w in GATE_W for c in GATE_CV]
RESTARTS = [restart_label(d) for d in DORMANT]


def gate_rate(ps, k, n, window, cv):
    """Pooled rate over the last n gaps when they are recent and consistent."""
    lo = k - 1 - n
    if lo < 0:
        return None
    span = (ps[k - 1]["date"] - ps[lo]["date"]).days
    if span <= 0 or span > window:
        return None
    rates = []
    for i in range(lo + 1, k):
        gap = (ps[i]["date"] - ps[i - 1]["date"]).days
        if gap <= 0:
            return None
        rates.append(ps[i]["gallons"] / gap)
    mean = statistics.fmean(rates)
    if mean <= 0 or statistics.pstdev(rates) / mean > cv:
        return None
    return bsm.rate_span(ps, lo, k - 1)


def restart_rate(ps, k, threshold):
    """The builder's rule at the date of pickup k, from pickups before it."""
    hit = bpt.dormant_restart(ps[:k], ps[k]["date"], threshold)
    return None if hit is None else bpt.rate_pooled(ps[hit[0]:k])


def run():
    """ro.run()'s test pickups, each with the gate and restart predictions."""
    pickups, checks = sf.load()
    tests = iter(ro.run())
    rng = random.Random(SEED)              # the same draws ro.run() makes
    closer = {}
    out = []
    for cid in sorted(pickups):
        ps = pickups[cid]
        for k in range(sf.MIN_PRIOR, len(ps)):
            x = ps[k]
            if x["date"] < sf.TEST_FROM or x["gallons"] <= 0:
                continue
            y = x["date"].year
            if (cid, y) not in closer:
                season = so.season_for_year(ps, y)
                so.classify(season, rng)
                closer[(cid, y)] = season is not None and season["trough"] < so.OPEN_TROUGH
            if closer[(cid, y)]:
                continue
            start = p2.clock_start(ps, k, checks.get(cid, []))
            days = (x["date"] - start).days
            if days <= 0 or days > bpt.STALE_DAYS:
                continue
            prev_year, _ = bt.rate_prev_year(ps, k, ro.YEAR)
            if p2.blend(p2.span(ps, k - 7, k - 1), 0.5, prev_year) is None:
                continue
            t = next(tests)
            assert t["customer_id"] == cid and t["actual"] == x["gallons"], "walk drifted"
            prod = t["rates"][ro.PROD] / days
            rates = {PROD: prod}
            for n in GATE_N:
                for w in GATE_W:
                    for c in GATE_CV:
                        rates[gate_label(n, w, c)] = gate_rate(ps, k, n, w, c)
            for d in DORMANT:
                rates[restart_label(d)] = restart_rate(ps, k, d)
            out.append({
                "customer_id": cid, "name": x["name"], "date": x["date"],
                "period": t["period"], "actual": x["gallons"],
                "fired": {m: r is not None for m, r in rates.items() if m != PROD},
                "pred": {m: (prod if r is None else r) * days for m, r in rates.items()},
            })
    assert next(tests, None) is None, "walk drifted"
    return out


def wape(rows, m):
    return 100 * sum(abs(t["pred"][m] - t["actual"]) for t in rows) / sum(t["actual"] for t in rows)


def bias(rows, m):
    return 100 * sum(t["pred"][m] - t["actual"] for t in rows) / sum(t["actual"] for t in rows)


def interval(rows, m, rng):
    """95% interval of WAPE(m) - WAPE(production), resampling customers."""
    by = defaultdict(list)
    for t in rows:
        by[t["customer_id"]].append(t)
    groups = list(by.values())
    diffs = []
    for _ in range(DRAWS):
        draw = [t for _ in groups for t in groups[rng.randrange(len(groups))]]
        diffs.append(wape(draw, m) - wape(draw, PROD))
    diffs.sort()
    return diffs[int(0.025 * DRAWS)], diffs[int(0.975 * DRAWS) - 1]


def table(rows, models, rng):
    out = ["| Model | WAPE | vs production | 95% interval | Bias | Fired on | "
           "Where fired: production → model WAPE | bias |",
           "|---|---:|---:|---|---:|---:|---|---|"]
    for m in models:
        diff = wape(rows, m) - wape(rows, PROD)
        if m == PROD:
            out.append(f"| {m} | {wape(rows, m):.1f}% | **+0.0** | – | {bias(rows, m):+.1f}% "
                       f"| – | – | – |")
            continue
        lo, hi = interval(rows, m, rng)
        fired = [t for t in rows if t["fired"][m]]
        share = f"{len(fired):,} ({len(fired) / len(rows):.0%}, " \
                f"{len({t['customer_id'] for t in fired})} cust.)"
        where = (f"{wape(fired, PROD):.1f}% → {wape(fired, m):.1f}%" if fired else "–")
        wbias = (f"{bias(fired, PROD):+.1f}% → {bias(fired, m):+.1f}%" if fired else "–")
        out.append(f"| {m} | {wape(rows, m):.1f}% | **{diff:+.2f}** | {lo:+.2f} to {hi:+.2f} | "
                   f"{bias(rows, m):+.1f}% | {share} | {where} | {wbias} |")
    return "\n".join(out)


def affected_now():
    """Ranked customers whose served rate comes from a restart, before and after."""
    payload = json.loads(bpt.OUT_JSON.read_text())
    asof = bpt.date.fromisoformat(payload["as_of"])
    # The builder's own preparation: new owners' history, shared barrels folded.
    pickups = bt.load_pickups(ids=None)
    checks = bpt.load_empty_checks(asof)
    bpt.apply_history_starts(pickups, checks)
    bpt.combine_shared(pickups, checks, {}, defaultdict(list), bpt.load_active_ids())
    rows = []
    for c in payload["customers"]:
        if c["section"] != "ranked" or not c.get("restart_from"):
            continue
        ps = pickups[c["id"]]
        pooled, prev = bpt.rate_pooled(ps), bpt.rate_prev_year_asof(ps, asof)
        before = (pooled + prev) / 2 if pooled is not None and prev is not None else pooled
        rows.append((c["name"], c["restart_from"], c["restart_gap_days"], before, c["rate_gpd"]))
    rows.sort(key=lambda r: -(r[4] / r[3]) if r[3] else 0)
    lines = ["| Customer | Back since | Gap (days) | 50/50 rate | Restart rate |",
             "|---|---|---:|---:|---:|"]
    lines += [f"| {n} | {d} | {g} | {b:.2f} | {a:.2f} |" for n, d, g, b, a in rows]
    return len(rows), "\n".join(lines)


# Follow-up tests run the same day from scratch scripts (not committed), each
# against production WITH the restart, on this module's walk: choose 2021-23,
# confirm 2024-26, customer bootstrap. Recorded so they are not re-run blind.
NOT_ADOPTED = """## Also tested 2026-10-09, not adopted

Baseline for all three = production with the restart (confirm WAPE 29.5%,
bias −2.3%). Differences are WAPE points vs that baseline, 95% interval.

| Idea | Confirm WAPE vs baseline | Bias | Verdict |
|---|---|---:|---|
| Significance gate: last 4–5 gaps (≤ 120 d) differ from production by a one-sample t-test (p < 0.05) → recent pooled rate | +0.15 to +0.31 (k=4: +0.06 to +0.61) | ≈ −2.1% | Rejected. Helped on choose (−0.21), reversed on confirm: where it fired it went from +0.6% to +7.1% bias — the shift reverts |
| Same gate → 75/25 or 50/50 with production | +0.05 to +0.16 | ≈ −2.2% | Rejected: the milder the blend, the closer to zero, never better |
| Last 3 vs gaps 4–6 split: when a Welch t-test (p < 0.05) says the last 3 gaps' rates differ from gaps 4–6, use ½ last 3 + ½ previous year | −0.06 (−0.13 to +0.01) | −2.2% | Not adopted: the right direction on both periods (choose −0.15), but the confirm interval crosses zero. p < 0.10: −0.09 (−0.21 to +0.04). Worth re-testing as the live log grows |
| Fleet month index: multiply by I(m)^w, I(m) = actual ÷ projected in that calendar month over earlier years only | w=¼: +0.01 (−0.04 to +0.07); w=½: +0.13 (+0.01 to +0.23) | −1.2% / −0.0% | Not adopted: removes bias, never WAPE. The calendar bias is real but small outside spring (all years: Mar −12%, Apr −10%, Oct −2.4%, Dec +5.9%) |
"""


def main():
    rows = run()
    rng = random.Random(SEED)
    w = ["# Consistency gate vs dormant-gap restart\n",
         "Generated by `analysis/backtest_dormant_restart.py`. **Production** = the "
         "builder before this change (the seasonal formula for strict seasonal-open "
         "customers, the 50/50 for everyone else). **Gate** = Sarge's idea: when the "
         "last n gaps span at most W days and their interval rates have a coefficient "
         "of variation at most c, the pooled rate of those gaps. **Restart** = a gap "
         "longer than D days inside either production window with 3+ pickups since: "
         "the pooled rate of the post-gap pickups (at most 6 gaps) — the builder's "
         "`dormant_restart`. Every rate is total gallons ÷ total days, never a mean "
         "of interval rates. Choose = 2021–23, confirm = 2024–26; closers left out. "
         "Intervals resample whole customers.\n",
         "**Decision (2026-10-09): the restart at 180 days (= `STALE_DAYS`) is "
         "adopted; the consistency gate is rejected.** Every gate variant is worse "
         "on the confirm years; where it fires it over-projects, because a run of "
         "agreeing pickups is partly luck and the next one reverts — production is "
         "already nearly unbiased on frequent, steady customers. The restart fires "
         "on few pickups but they are among the worst misses, and it improves both "
         "periods; 120 days does better on choose but its confirm interval crosses "
         "zero, and 180 is a threshold the builder already uses.\n"]
    for period in ("choose", "confirm"):
        sub = [t for t in rows if t["period"] == period]
        w.append(f"## {period.capitalize()} ({len(sub):,} pickups, "
                 f"{len({t['customer_id'] for t in sub})} customers)\n")
        w.append(table(sub, [PROD] + RESTARTS + GATES, rng) + "\n")

    w.append("## Schaffers / Hatchet Tap & Table, 2026\n")
    w.append("| Pickup | Actual | Production | Restart > 180 d |\n|---|---:|---:|---:|")
    for t in rows:
        if t["customer_id"] == 282 and t["date"].year == 2026:
            w.append(f"| {t['date']} | {t['actual']} | {t['pred'][PROD]:.0f} | "
                     f"{t['pred'][restart_label(180)]:.0f} |")
    w.append("")

    w.append(NOT_ADOPTED)

    count, listing = affected_now()
    w.append(f"## Ranked customers on a restart rate today ({count})\n")
    w.append("**Blind spot.** A backtest scores only pickups that happened. A "
             "summer-only customer projected as filling all winter never produces a "
             "pickup to score, so that cost is invisible here. Several customers below "
             "are summer-only (food trucks, golf clubs, lake spots): the restart gives "
             "them their in-season rate, right while open, but after closing they keep "
             "accumulating until stale. Seasonal closers belong in `MODEL_OVERRIDES` "
             "(a person's call, never automated).\n")
    w.append(listing + "\n")
    OUT_REPORT.write_text("\n".join(w) + "\n")
    print(f"wrote {OUT_REPORT.name}: {len(rows):,} pickups")


if __name__ == "__main__":
    main()
