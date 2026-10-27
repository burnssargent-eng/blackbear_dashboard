#!/usr/bin/env python3
"""
Season-end taper for in-season closers (Sarge, 2026-10-26): does oil slow in
the last open month of a closer's season, enough that projecting those days
at a reduced rate beats today's flat open-season rate?

Prompted by Toziers (803) and Quechee Gorge Snack Bar (1056) on 2026-10-26:
projected 180 and 149 gal, collected 40 and 60, both semi-closers in the last
weeks of an Apr/Mar-Oct season.

The replay is production's: every pickup of a closer (registry, or detected
from the complete years before its year) whose whole gap, from the clock
(the previous pickup or a later empty check), lies in season. Rate =
build_projection_table.rate_open_season over the pickups before it, exactly
as the builder ranks an in-season closer. Projected = rate x days, uncapped
(the builder ranks on the uncapped figure).

Variants weight the days that fall in the season's LAST month (all other
days stay at 1):

  A  every day of the last month x f
  B  only days 16-end of the last month x f
  C  a ramp: day d of an n-day last month x (1 - (1 - f) * d / n)

f is chosen on dev (2019, 2021-23; 2020 skipped) by WAPE over all in-season
closer pickups, then confirmed once on 2024-26 with a customer bootstrap.

SPILLOVER (Sarge, same day): a fixed shape, not tuned, so it is scored on
both periods as it stands. Days 16-end of the last month x 0.7, then the
first 7 days of the next month ramp 0.7 -> 0 (day d x 0.7 * (7 - d) / 6),
later days 0. For it the test set also takes pickups whose gap starts in
season and ends in the month after it ("spillover" pickups). Today's figure
for those is the builder's off-season rule: in-season days x the rate,
closed days 0 (a true closer is held out off-season instead, so for it this
is what it would show if ranked).

    python3 analysis/backtest_season_end_taper.py

Writes analysis/season_end_taper.md. Standard library + the builder; deterministic.
"""

import calendar
import random
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import backtest_seasonal_closed as bsc
import build_projection_table as bpt

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "season_end_taper.md"

DEV = (2019, 2021, 2022, 2023)
CONFIRM = (2024, 2025, 2026)
FACTORS = [round(0.3 + 0.1 * i, 1) for i in range(8)]      # 0.3 .. 1.0
SPILL_LATE, SPILL_DAYS = 0.7, 7
VARIANTS = ("A", "B", "C")
SEED = 42
BOOT = 2000


def weight(day, last_month, variant, f):
    after = day.month - 1 == (last_month + 1) % 12
    if variant == "spillover":
        if after:
            return SPILL_LATE * max(0, SPILL_DAYS - day.day) / (SPILL_DAYS - 1)
        return SPILL_LATE if day.month - 1 == last_month and day.day > 15 else 1.0
    if after:
        return 0.0                         # off-season: the builder accrues nothing
    if day.month - 1 != last_month:
        return 1.0
    if variant == "A":
        return f
    if variant == "B":
        return f if day.day > 15 else 1.0
    n = calendar.monthrange(day.year, day.month)[1]
    return 1 - (1 - f) * day.day / n


def tests():
    customers, checks = bsc.load()
    out = []
    for cid, ps in customers.items():
        registry = bpt.MODEL_OVERRIDES.get(cid)
        if registry and registry.status not in bpt.SEASONAL_STATUSES:
            continue
        for i in range(1, len(ps)):
            x = ps[i]
            y = x["date"].year
            if y not in DEV + CONFIRM or x["gallons"] <= 0:
                continue
            before = ps[:i]
            override = registry or bpt.detected_closer(cid, before, x["date"])
            if not override or override.status not in bpt.SEASONAL_STATUSES:
                continue
            months = bpt.season_months(override.season)
            start = bsc.p2.clock_start(ps, i, checks.get(cid, []))
            days = (x["date"] - start).days
            if days <= 0:
                continue
            span = [start + timedelta(days=d) for d in range(1, days + 1)]
            last = bpt.MONTHS.index(override.season.split("-")[1])
            # In season throughout, or a spillover: starts in season, the rest
            # in the month after the season, and ends there.
            closed = [d for d in span if d.month - 1 not in months]
            spill = bool(closed) and start.month - 1 in months and len(span) > len(closed) and all(
                d.month - 1 == (last + 1) % 12 and (x["date"] - d).days < 31 for d in closed)
            if closed and not spill:
                continue
            rate = bpt.rate_open_season(before, override.season)
            if rate is None:
                continue
            out.append({"cid": cid, "name": x["name"], "date": x["date"], "actual": x["gallons"],
                        "rate": rate, "span": span, "last": last,
                        "touches": any(d.month - 1 == last for d in span) and not spill,
                        "spill": spill,
                        "kind": override.status,
                        "period": "dev" if y in DEV else "confirm"})
    return out


def projected(t, variant, f):
    return t["rate"] * sum(weight(d, t["last"], variant, f) for d in t["span"])


def wape(rows, variant, f):
    act = sum(t["actual"] for t in rows)
    err = sum(abs(projected(t, variant, f) - t["actual"]) for t in rows)
    bias = sum(projected(t, variant, f) - t["actual"] for t in rows)
    return 100 * err / act, 100 * bias / act


def boot_diff(rows, variant, f, rng):
    """95% interval of WAPE(variant f) - WAPE(today), resampling customers."""
    by = defaultdict(list)
    for t in rows:
        by[t["cid"]].append(t)
    ids = sorted(by)
    diffs = []
    for _ in range(BOOT):
        pick = [t for c in (rng.choice(ids) for _ in ids) for t in by[c]]
        if not pick:
            continue
        diffs.append(wape(pick, variant, f)[0] - wape(pick, "A", 1.0)[0])
    diffs.sort()
    return diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]


def main():
    rows = tests()
    dev = [t for t in rows if t["period"] == "dev"]
    conf = [t for t in rows if t["period"] == "confirm"]
    dev_t = [t for t in dev if t["touches"]]
    conf_t = [t for t in conf if t["touches"]]
    lines = ["# Season-end taper for in-season closers", "",
             "Generated by `backtest_season_end_taper.py` (see its docstring for the method).", "",
             f"In-season closer pickups: dev {len(dev)} ({len(dev_t)} touch the last month, "
             f"{len({t['cid'] for t in dev_t})} customers); confirm {len(conf)} ({len(conf_t)} touch it, "
             f"{len({t['cid'] for t in conf_t})} customers).", "",
             "WAPE and bias in % of actual gallons. *All* = every in-season closer pickup; "
             "*last month* = only those whose gap touches the season's last month "
             "(the only ones a taper changes).", "",
             "## Dev (choose)", "",
             "| Variant | f | All WAPE | All bias | Last-month WAPE | Last-month bias |",
             "|---|---:|---:|---:|---:|---:|"]
    best = {}
    for v in VARIANTS:
        for f in FACTORS:
            a, ab = wape(dev, v, f)
            l, lb = wape(dev_t, v, f)
            lines.append(f"| {v} | {f:.1f} | {a:.1f} | {ab:+.1f} | {l:.1f} | {lb:+.1f} |")
            if v not in best or a < best[v][1]:
                best[v] = (f, a)
    base_dev = wape(dev, "A", 1.0)[0]
    lines += ["", "Chosen on dev (lowest all-pickup WAPE; f = 1.0 is today): "
              + ", ".join(f"{v} f={best[v][0]:.1f} ({best[v][1] - base_dev:+.1f})" for v in VARIANTS), "",
              "## Confirm (2024-26), chosen f only", "",
              "| Variant | f | All WAPE | Δ vs today (95% CI, customer bootstrap) | All bias | "
              "Last-month WAPE | Last-month bias |",
              "|---|---:|---:|---|---:|---:|---:|"]
    rng = random.Random(SEED)
    a0, b0 = wape(conf, "A", 1.0)
    l0, lb0 = wape(conf_t, "A", 1.0)
    lines.append(f"| today | 1.0 | {a0:.1f} | – | {b0:+.1f} | {l0:.1f} | {lb0:+.1f} |")
    for v in VARIANTS:
        f = best[v][0]
        a, ab = wape(conf, v, f)
        l, lb = wape(conf_t, v, f)
        lo, hi = boot_diff(conf, v, f, rng) if f != 1.0 else (0.0, 0.0)
        lines.append(f"| {v} | {f:.1f} | {a:.1f} | {a - a0:+.1f} ({lo:+.1f} to {hi:+.1f}) | {ab:+.1f} | "
                     f"{l:.1f} | {lb:+.1f} |")
    fa = best["A"][0]
    lines += ["", "## Spillover shape (Sarge) against today and A, both periods", "",
              f"*Spillover* = gaps that start in season and end in the month after it "
              f"(dev {sum(t['spill'] for t in dev)}, confirm {sum(t['spill'] for t in conf)}). "
              "*All* includes them. Δ is the all-pickup WAPE against today, 95% CI by customer bootstrap.", "",
              "| Period | Model | All WAPE | Δ vs today (95% CI) | All bias | Last-month WAPE | Last-month bias | "
              "Spillover WAPE | Spillover bias |",
              "|---|---|---:|---|---:|---:|---:|---:|---:|"]
    for label, rs in (("dev", dev), ("confirm", conf)):
        last_rs = [t for t in rs if t["touches"]]
        spill_rs = [t for t in rs if t["spill"]]
        base = wape(rs, "A", 1.0)[0]
        for name, v, f in (("today", "A", 1.0), (f"A ×{fa:.1f}", "A", fa), ("spillover", "spillover", None)):
            al, ab = wape(rs, v, f)
            ll, lb = wape(last_rs, v, f)
            sl, sb = wape(spill_rs, v, f) if spill_rs else (float("nan"), float("nan"))
            ci = "–" if name == "today" else "{:+.1f} ({:+.1f} to {:+.1f})".format(al - base, *boot_diff(rs, v, f, rng))
            lines.append(f"| {label} | {name} | {al:.1f} | {ci} | {ab:+.1f} | {ll:.1f} | {lb:+.1f} | {sl:.1f} | {sb:+.1f} |")
    lines += ["", "## Spillover pickups by customer (both periods)", "",
              "| Customer | Kind | Pickups | Actual | Today | Spillover |", "|---|---|---:|---:|---:|---:|"]
    by = defaultdict(list)
    for t in rows:
        if t["spill"]:
            by[(t["cid"], t["name"], t["kind"])].append(t)
    for (cid, name, kind), ts in sorted(by.items(), key=lambda kv: -sum(t["actual"] for t in kv[1])):
        lines.append(f"| {name} ({cid}) | {kind} | {len(ts)} | {sum(t['actual'] for t in ts):.0f} | "
                     f"{sum(projected(t, 'A', 1.0) for t in ts):.0f} | {sum(projected(t, 'spillover', None) for t in ts):.0f} |")
    lines += ["", "## Last-month pickups by customer (confirm, today's projection)", "",
              "| Customer | Kind | Pickups | Actual | Projected today | Bias |", "|---|---|---:|---:|---:|---:|"]
    by = defaultdict(list)
    for t in conf_t:
        by[(t["cid"], t["name"], t["kind"])].append(t)
    for (cid, name, kind), ts in sorted(by.items(), key=lambda kv: -sum(t["actual"] for t in kv[1])):
        act = sum(t["actual"] for t in ts)
        prj = sum(projected(t, "A", 1.0) for t in ts)
        lines.append(f"| {name} ({cid}) | {kind} | {len(ts)} | {act:.0f} | {prj:.0f} | {100 * (prj - act) / act:+.0f}% |")
    OUT_REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
