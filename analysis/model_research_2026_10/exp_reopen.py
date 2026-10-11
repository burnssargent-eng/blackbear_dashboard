#!/usr/bin/env python3
"""
Experiments H (off-season and reopening routing) and I (the first pickup after
reopening).

The champion routes a detected closer three ways outside its in-season rows:

  off-season, called recently   ranked, projected at the STAGE rate x every
                                day since the clock (closed days included)
  off-season, silent            held out (seasonal section)
  season open, no pickup yet    held out ("awaiting first pickup") until the
                                first in-season pickup, which is never projected

H, scored on weekly snapshots (every Monday, 2018-19 and 2021-26, every
detected closer), partially observed -- "no pickup within 30 days" does not
prove the barrel was not full:

  H1   hold every off-season week out (as a true closer is)
  H2   keep the champion's off-season ranking, but project closer rate x OPEN
       days since the clock (closed days count zero)
  R1L  rank awaiting weeks from the season's 1st + L days, projected closer
       rate x days since max(clock, opened + L); L from the I grid

I, scored per pickup on the first in-season pickup (which no model projects
today), reference I1(0):

  I0      closer rate x every open day since the clock, last season's tail
          included -- i.e. oil left at closing is still there at reopening
  I1(L)   closer rate x days since max(clock, opened + L): the barrel starts
          empty, the season starts L days late. L in {0, 14, 30}, dev-chosen
  I2/I3   I1(L*) + 0.25 / 0.5 x the customer's median pickup (fixed carryover)

and a regression of actual gallons on (rate x this-season days, rate x last
season's tail days) to say whether carryover is identifiable at all.

The rate is the champion's: the open-season rate where >= 3 wholly-open gaps
exist (it always is, on these rows: G's fallback set was empty), else the stage
rate. Season = the detector, month level.

    python3 exp_reopen.py      (from this folder)
"""

import random
import statistics
from datetime import date, timedelta

import harness
import metrics as m
import replay
from exp_closer import open_days
from replay import bpt, seasonal_open

LAGS = (0, 14, 30)              # declared before any run
ALLOWANCES = (0.25, 0.5)
MAX_GAP = 366                   # a first-of-season gap spans the closure; one year at most
FLAG_HORIZON = 30


def season_days(clock, t, opened, lag):
    start = max(clock, opened + timedelta(days=lag))
    return max(0, (t - start).days)


def median_pick(h):
    return statistics.median(p["gallons"] for p in h[-6:])


# ─────────────────────────────────────────────
# I: the first in-season pickup
# ─────────────────────────────────────────────

def first_pickup_rows(records, pickups):
    rows = []
    for r in records:
        if not r["season_spec"] or r["year"] not in replay.EVAL_YEARS or r["rate"] is None:
            continue
        if r["registry_status"] in replay.NO_RATE:
            continue
        opened = bpt.season_opened(r["season_spec"], r["date"])
        h = pickups[r["cid"]][:r["k"]]
        if opened is None or h[-1]["date"] >= opened or r["gap"] > MAX_GAP:
            continue
        months = bpt.season_months(r["season_spec"])
        clock, t, rate = r["clock_start"], r["date"], r["rate"]
        this = season_days(clock, t, opened, 0)
        tail = open_days(clock, min(t, opened), months)
        rows.append({
            "cid": r["cid"], "year": r["year"], "date": t, "actual": r["actual"],
            "name": h[-1]["name"], "rate": rate, "opened": opened, "this_days": this,
            "tail_days": tail, "delay": (t - opened).days, "median_pick": median_pick(h),
            "closer_rate": r["stage"] == bpt.STAGE_CLOSER,
            "kind": "summer closer" if 6 in months else "winter closer",
            "I0": rate * (this + tail),
            **{f"I1_{L}": rate * season_days(clock, t, opened, L) for L in LAGS},
        })
    return rows


def regression(rows, draws=1000):
    """actual = a * rate*this_days + b * rate*tail_days, no intercept; cluster bootstrap."""
    def fit(rs):
        x1 = [r["rate"] * r["this_days"] for r in rs]
        x2 = [r["rate"] * r["tail_days"] for r in rs]
        y = [r["actual"] for r in rs]
        s11 = sum(a * a for a in x1)
        s22 = sum(b * b for b in x2)
        s12 = sum(a * b for a, b in zip(x1, x2))
        s1y = sum(a * c for a, c in zip(x1, y))
        s2y = sum(b * c for b, c in zip(x2, y))
        det = s11 * s22 - s12 * s12
        if not det:
            return None
        return (s22 * s1y - s12 * s2y) / det, (s11 * s2y - s12 * s1y) / det

    est = fit(rows)
    per = {}
    for r in rows:
        per.setdefault(r["cid"], []).append(r)
    groups = list(per.values())
    rng = random.Random(m.SEED)
    boots = []
    for _ in range(draws):
        sample = [r for g in rng.choices(groups, k=len(groups)) for r in g]
        f = fit(sample)
        if f:
            boots.append(f)
    ci = []
    for i in range(2):
        v = sorted(b[i] for b in boots)
        ci.append((v[int(0.025 * len(v))], v[int(0.975 * len(v)) - 1]))
    return est, ci


# ─────────────────────────────────────────────
# H: weekly snapshots
# ─────────────────────────────────────────────

def snapshots(pickups, checks):
    seasons = replay.SeasonCache()
    out = []
    first = date(2018, 1, 1)
    first += timedelta(days=(7 - first.weekday()) % 7)
    last_day = max(p["date"] for ps in pickups.values() for p in ps)
    for cid in sorted(pickups):
        ps = pickups[cid]
        reg = bpt.MODEL_OVERRIDES.get(cid)
        if reg and reg.status in replay.NO_RATE:
            continue
        dates = [p["date"] for p in ps]
        t = first
        while t <= last_day - timedelta(days=FLAG_HORIZON):
            if t.year not in replay.EVAL_YEARS:
                t += timedelta(days=7)
                continue
            k = sum(1 for d in dates if d < t)
            if k < bpt.NEW_MIN_PICKUPS or not seasons.closed_months(cid, ps, t.year):
                t += timedelta(days=7)
                continue
            h = ps[:k]
            st = replay.champion_state(cid, h, t, checks.get(cid, ()), seasons, "core")
            spec = st["season_spec"]
            if not spec or st["rate"] is None:
                t += timedelta(days=7)
                continue
            opened = bpt.season_opened(spec, t)
            months = bpt.season_months(spec)
            clock = st["clock_start"]
            if opened is None:
                kind = "off-season, ranked" if st["section"] == bpt.SECTION_RANKED else "off-season, held"
            elif h[-1]["date"] < opened:
                kind = "awaiting first pickup"
            elif st["section"] == bpt.SECTION_RANKED:
                kind = "in season, ranked"
            else:
                kind = "in season, other"
            nxt = dates[k] if k < len(dates) else None
            closer = bpt.rate_open_season(h, spec) or st["rate"]
            proj = {}
            if kind in ("off-season, ranked", "in season, ranked"):
                proj["champion"] = st["rate"] * (t - clock).days
            if kind == "off-season, ranked":
                proj["H2"] = closer * open_days(clock, t, months)
            if kind == "awaiting first pickup":
                for L in LAGS:
                    proj[f"R1_{L}"] = closer * season_days(clock, t, opened, L)
            out.append({
                "cid": cid, "date": t, "year": t.year, "kind": kind,
                "median_pick": median_pick(h), "proj": proj,
                "event": nxt is not None and (nxt - t).days <= FLAG_HORIZON,
            })
            t += timedelta(days=7)
    return out


def flag_stats(snaps, model):
    """Weeks listed, flagged, flagged with / without a pickup in 30 d, pickups missed."""
    listed = [s for s in snaps if s["proj"].get(model) is not None]
    flagged = [s for s in listed if s["proj"][model] >= s["median_pick"]]
    hits = sum(s["event"] for s in flagged)
    events = sum(s["event"] for s in snaps)
    return {"weeks": len(snaps), "listed": len(listed), "flagged": len(flagged), "hits": hits,
            "false": len(flagged) - hits, "events": events, "missed": events - hits}


def flag_line(label, model, st):
    fs = st["flagged"]
    return (f"| {label} | {model} | {st['weeks']:,} | {st['listed']:,} | {fs:,} | "
            f"{st['false']:,} ({100 * st['false'] / fs:.0f}%) | " if fs else
            f"| {label} | {model} | {st['weeks']:,} | {st['listed']:,} | 0 | – | ") + (
            f"{st['events']:,} | {st['hits']:,} ({100 * st['hits'] / st['events']:.0f}%) |"
            if st["events"] else "0 | – |")


def offseason_pickups(records, pickups):
    """Off-season pickups the champion ranks and scores: stage rate x all days vs H2."""
    rows = []
    for r in records:
        if not r["scored"] or not r["season_spec"] or r["year"] not in replay.EVAL_YEARS:
            continue
        if bpt.season_opened(r["season_spec"], r["date"]) is not None:
            continue
        h = pickups[r["cid"]][:r["k"]]
        closer = bpt.rate_open_season(h, r["season_spec"]) or r["rate"]
        od = open_days(r["clock_start"], r["date"], bpt.season_months(r["season_spec"]))
        rows.append({"cid": r["cid"], "year": r["year"], "actual": r["actual"], "name": h[-1]["name"],
                     "base": r["pred"], "cand": closer * od})
    return rows


# ─────────────────────────────────────────────

def main():
    pickups, checks, _, records = replay.load_or_build(mode="core")
    rows = first_pickup_rows(records, pickups)

    # I: choose the lag on dev, then a rolling check.
    grid = {L: [dict(r, base=r["I1_0"], cand=r[f"I1_{L}"]) for r in rows] for L in LAGS}
    L_dev = harness.choose(grid, replay.DEV_YEARS)
    rolling, picks = harness.rolling_choice(grid)
    oddeven, oe_picks = harness.odd_even_choice(grid)
    results, yearly = [], {}

    def run(name, cand_key=None, fn=None, rs=rows, note=""):
        sub = [dict(r, base=r["I1_0"], cand=fn(r) if fn else r[cand_key]) for r in rs]
        res, yrs = harness.evaluate("I", name, "first in-season pickup (held out today)", sub, note)
        results.extend(res)
        yearly[name] = yrs

    run("I0 last season's tail counted (carryover)", "I0")
    for L in LAGS[1:]:
        run(f"I1 season starts {L} d late", f"I1_{L}")
    res, yrs = harness.evaluate("I", "I1 lag, rolling choice", "first in-season pickup (held out today)",
                                rolling, note="picks " + ", ".join(f"{y}:{v}" for y, v in picks.items()))
    results += res
    yearly["I1 lag, rolling choice"] = yrs
    for a in ALLOWANCES:
        run(f"I1({L_dev}) + {a} × median pickup",
            fn=lambda r, a=a: r[f"I1_{L_dev}"] + a * r["median_pick"])

    # Absolute accuracy of the reference, by year, against in-season closers.
    ref = {y: m.summary([dict(r, p=r["I1_0"]) for r in rows if r["year"] == y], "p")
           for y in replay.EVAL_YEARS if any(r["year"] == y for r in rows)}
    est, ci = regression([r for r in rows if r["year"] in replay.EVAL_YEARS])
    est_dev, ci_dev = regression([r for r in rows if r["year"] in replay.DEV_YEARS])
    est_conf, ci_conf = regression([r for r in rows if r["year"] in replay.CONFIRM_YEARS])

    harness.write_results(harness.HERE / "results_I.csv", results)

    delays = sorted(r["delay"] for r in rows if r["year"] in replay.EVAL_YEARS)
    q = statistics.quantiles(delays, n=4)
    lines = ["# Experiment I: the first pickup after reopening (generated by `exp_reopen.py`)", "",
             f"First in-season pickups of detected closers, eval years: "
             f"{sum(r['year'] in replay.EVAL_YEARS for r in rows):,} "
             f"({len({r['cid'] for r in rows if r['year'] in replay.EVAL_YEARS})} customers); "
             f"{sum(r['closer_rate'] for r in rows)} on the open-season rate, the rest on the stage rate. "
             "Production projects none of them (held out as 'awaiting first pickup').", "",
             f"Days from the month-level opening to the first pickup: median {statistics.median(delays):.0f}, "
             f"quartiles {q[0]:.0f}–{q[2]:.0f}.", "",
             "## Reference I1(0): closer rate × days since the season's 1st (or the clock, if later)", "",
             "| Year | Pickups | WAPE | Bias |", "|---|---:|---:|---:|"]
    for y, s in ref.items():
        lines.append(f"| {y} | {s['pickups']} | {s['wape']:.1f}% | {s['bias']:+.1f}% |")
    lines += ["", f"Lag chosen on dev: **{L_dev} d**. Rolling picks: "
              + ", ".join(f"{y}: {v}" for y, v in picks.items())
              + ". Odd→even picks: " + ", ".join(f"{y}: {v}" for y, v in oe_picks.items()) + ".", "",
              "## Comparisons (Δ vs I1(0); identical pickups)", "", harness.table(results), "",
              "## Δ WAPE by year", "", harness.year_table(yearly), "",
              "## Is carryover identifiable?", "",
              "actual = a × (rate × this-season days) + b × (rate × last season's open days after the last pickup); "
              "no intercept, customer-cluster bootstrap. b ≈ 0 means oil left at closing is not found at reopening; "
              "a < 1 means the season yields less than its calendar days imply.", "",
              "| Window | a (this season) | 95% interval | b (last season's tail) | 95% interval |",
              "|---|---:|---|---:|---|"]
    for label, (e, c) in (("dev 2021-23", (est_dev, ci_dev)), ("confirm 2024-26", (est_conf, ci_conf)),
                          ("all eval years", (est, ci))):
        lines.append(f"| {label} | {e[0]:.2f} | {c[0][0]:.2f}–{c[0][1]:.2f} | {e[1]:.2f} | {c[1][0]:.2f}–{c[1][1]:.2f} |")
    tail_rows = [r for r in rows if r["tail_days"] > 0 and r["year"] in replay.EVAL_YEARS]
    lines += ["", f"Rows with any tail days: {len(tail_rows)} of "
              f"{sum(r['year'] in replay.EVAL_YEARS for r in rows)}; median tail "
              f"{statistics.median(r['tail_days'] for r in tail_rows) if tail_rows else 0:.0f} d.", "",
              "## Splits for I1(0) vs the dev-chosen lag", "",
              harness.split_table([dict(r, base=r["I1_0"], cand=r[f"I1_{L_dev}"]) for r in rows
                                   if r["year"] in replay.EVAL_YEARS], lambda r: r["kind"], "Closer type"), ""]
    (harness.HERE / "results_I.md").write_text("\n".join(lines))

    # H: snapshots.
    snaps = snapshots(pickups, checks)
    lines = ["# Experiment H: off-season and reopening routing (generated by `exp_reopen.py`)", "",
             "Weekly snapshots (Mondays) of every detected closer with ≥ 3 pickups, 2018-19 and 2021-26. "
             f"Flagged = projection ≥ the customer's median of its last 6 pickups. Event = a pickup within "
             f"{FLAG_HORIZON} days. **Partially observed**: no pickup does not prove an empty barrel, and a "
             "pickup is partly the routing's own consequence.", "",
             "| Snapshot set | Model | Weeks | Listed | Flagged | Flagged, no pickup in 30 d | Weeks with a pickup in 30 d | of which flagged |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    by_kind = m.by(snaps, lambda s: s["kind"])
    plan = [("in season, ranked", ["champion"]),
            ("off-season, ranked", ["champion", "H2"]),
            ("off-season, held", []),
            ("awaiting first pickup", [f"R1_{L}" for L in LAGS])]
    for kind, models in plan:
        sub = by_kind.get(kind, [])
        if not models:
            st = flag_stats(sub, "none")
            lines.append(flag_line(kind, "held (champion)", st))
        for model in models:
            lines.append(flag_line(kind, model, flag_stats(sub, model)))
    off = by_kind.get("off-season, ranked", []) + by_kind.get("off-season, held", [])
    lines.append(flag_line("all off-season", "H1 (hold all)", flag_stats(off, "none")))
    lines += ["", "## By window", "",
              "| Window | Set | Model | Flagged | Flagged, no pickup in 30 d | Pickup weeks flagged |",
              "|---|---|---|---:|---:|---:|"]
    for wname, years in harness.WINDOWS.items():
        for kind, model in (("in season, ranked", "champion"), ("off-season, ranked", "champion"),
                            ("off-season, ranked", "H2"), ("awaiting first pickup", f"R1_{L_dev}")):
            st = flag_stats([s for s in by_kind.get(kind, []) if s["year"] in years], model)
            fs = st["flagged"]
            lines.append(f"| {wname} | {kind} | {model} | {fs:,} | "
                         + (f"{100 * st['false'] / fs:.0f}%" if fs else "–") + " | "
                         + (f"{st['hits']}/{st['events']}" if st["events"] else "–") + " |")
    off_rows = offseason_pickups(records, pickups)
    c = m.compare(off_rows)
    lines += ["", "## Off-season pickups the champion scores (gap ≤ 180 d)", "",
              "| Model | Pickups (cust.) | WAPE | Bias |", "|---|---:|---:|---:|",
              f"| champion (stage rate × all days) | {c['pickups']} ({c['customers']}) | {c['base_wape']:.1f}% | {c['base_bias']:+.1f}% |",
              f"| H2 (closer rate × open days) | {c['pickups']} ({c['customers']}) | {c['cand_wape']:.1f}% | {c['cand_bias']:+.1f}% |",
              "", f"Δ {c['d_wape']:+.1f} points ({m.ci(c)}); {c['cust_better']} customers better, {c['cust_worse']} worse. "
              "Too few pickups for years or windows."]
    lines += ["", f"Customers with an off-season ranked week: "
              f"{len({s['cid'] for s in by_kind.get('off-season, ranked', [])})}; "
              f"with an awaiting week: {len({s['cid'] for s in by_kind.get('awaiting first pickup', [])})}.", ""]
    (harness.HERE / "results_H.md").write_text("\n".join(lines))
    impacts = harness.customer_impacts("H", "H2 off-season pickups", "all eval years", off_rows)
    impacts += harness.customer_impacts(
        "I", f"I1 lag {L_dev} d", "all eval years",
        [dict(r, base=r["I1_0"], cand=r[f"I1_{L_dev}"]) for r in rows if r["year"] in replay.EVAL_YEARS])
    harness.write_impacts(harness.HERE / "impacts_HI.csv", impacts)

    h_rows = []
    for kind, models in plan:
        for model in models:
            for wname, years in harness.WINDOWS.items():
                st = flag_stats([s for s in by_kind.get(kind, []) if s["year"] in years], model)
                h_rows.append({"experiment": "H", "candidate": model, "scope": kind, "window": wname,
                               "pickups": st["events"], "fired_pickups": st["flagged"],
                               "note": f"weeks {st['weeks']}; listed {st['listed']}; flagged {st['flagged']}; "
                                       f"flagged without pickup {st['false']}; pickup weeks flagged {st['hits']}"})
    harness.write_results(harness.HERE / "results_H.csv", h_rows)


if __name__ == "__main__":
    main()
