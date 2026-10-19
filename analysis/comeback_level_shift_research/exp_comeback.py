#!/usr/bin/env python3
"""
Experiment A (returns after 181-365 days) and B1 (the long-gap distribution),
on the October 15 champion replay (champion.py). Research only.

Phase 1 -- diagnostics:
  B1  every return gap > 120 d, binned; champion accuracy on the first two
      PROJECTED post-return pickups (since 1 and 2 = R2, R3), routing shares,
      seasonal composition. In bins over 365 d production holds those
      pickups; their row shows what the unheld projection would have scored.
  A1  per-event table for 181-365 d returns, and a decomposition of the miss:
      champion rate vs pre-gap rate vs pre-gap rate on open days, R1's
      gallons against the pre-gap rate over the gap's calendar and open days.

Phase 2 -- candidates (declared here before any run; see GRIDS):
  A1  open-day exposure: the champion's stage formula with every gap's days
      replaced by its open days, and the clock likewise. Calendar: the
      detector's closed months, or the loose calendar (common.Calendars).
  A2  pre-gap pooled rate (>= 3 pre-gap gaps), a re-test on this replay.
  A3  w x pre-gap + (1 - w) x champion, w in A3_WEIGHTS, chosen on dev.
  A4  carryover sensitivity: R1's gallons reduced by an allowance before they
      enter the A0 / A1 rate. Never an addition to a production rate.

    RESEARCH_CACHE=... python3 exp_comeback.py
"""

import csv
import statistics
from collections import Counter, defaultdict

import champion as ch
import common as c
import harness
import metrics as m
import replay
from replay import bpt

HERE = c.HERE

# Declared before the first run (2026-10-15). Never extended after looking.
A3_WEIGHTS = (0.25, 0.5, 0.75)
A4_ALLOWANCES = ("0", "5 gal", "25% of median pickup", "10% of capacity (hindsight)")
CALENDARS = ("detector", "loose")
SCOPE_GAP = (181, 365)
SINCE_SCORED = (1, 2, 3)
SCOPE_B = " [b: established/new only]"

SECTION_SHORT = {
    bpt.SECTION_RANKED: "ranked", bpt.SECTION_STALE: "stale", bpt.SECTION_WILL_CALL: "will-call",
    bpt.SECTION_INSUFFICIENT: "insufficient", bpt.SECTION_SEASONAL: "seasonal holdout",
}


def routing(r):
    if r["scored"]:
        return "scored"
    if r["scored_unheld"] and r["held"]:
        return "held (comeback)"
    s = SECTION_SHORT.get(r["section"], "no-rate account")
    return "ranked, interval > 180 d" if s == "ranked" else s


def composition(r):
    if r["season_spec"]:
        return "closer"
    if r["stage"] == bpt.STAGE_SEASONAL:
        return "seasonal-open"
    return "neither"


def season_type(ps, i):
    """By the gap's midpoint month: closed in winter -> summer business."""
    a, b = ps[i - 1]["date"], ps[i]["date"]
    mid = a + (b - a) / 2
    return "summer business (closed Oct-Mar)" if mid.month in (10, 11, 12, 1, 2, 3) \
        else "winter business (closed Apr-Sep)"


def return_class(ps, i):
    """
    reopening   an earlier gap > 120 d, >= 300 d before, had its midpoint
                within one month of this gap's midpoint (the same closure)
    ordinary    >= 1.5 years of history before the gap and no such gap
    first year  less history than that: undecidable
    """
    a, b = ps[i - 1]["date"], ps[i]["date"]
    mid = a + (b - a) / 2
    for j in range(1, i - 1):
        g = (ps[j]["date"] - ps[j - 1]["date"]).days
        if g <= c.MIN_EVENT_GAP:
            continue
        mj = ps[j - 1]["date"] + (ps[j]["date"] - ps[j - 1]["date"]) / 2
        if (mid - mj).days >= 300 and min((mid.month - mj.month) % 12, (mj.month - mid.month) % 12) <= 1:
            return "reopening"
    return "ordinary" if (a - ps[0]["date"]).days >= 548 else "first year"


def pre_gap(ps, i):
    """Pre-gap pooled rate (h[:i], <= 6 gaps), its gap count, gallons in the 365 d before."""
    h = ps[:i]
    gaps = min(len(h) - 1, bpt.WINDOW)
    rate = bpt.rate_pooled(h) if gaps >= 1 else None
    start = h[-1]["date"]
    year_gal = sum(p["gallons"] for p in h[1:] if (start - p["date"]).days < bpt.YEAR_DAYS)
    return rate, gaps, year_gal


# ─────────────────────────────────────────────
# Phase 1
# ─────────────────────────────────────────────

def attach_events(pickups, records):
    """Add event fields to every record that predicts R1..R4 of a return."""
    out = []
    for r in records:
        ps = pickups[r["cid"]]
        e = c.event_of(ps[:r["k"]]) or c.event_since0(ps, r["k"])
        if not e:
            continue
        i, gap, since = e
        r = dict(r, ev_i=i, ev_gap=gap, since=since, bin=c.bin_label(gap))
        out.append(r)
    return out


def b1_table(ev_records):
    rows = [r for r in ev_records if r["year"] in replay.EVAL_YEARS and r["since"] in (1, 2)]
    lines = ["| Gap | Customers | Events | Pickups (since 1–2) | Scored | Held | Stale | Will-call | Insufficient | Other | Closer / seasonal-open / neither | Projection exists: pickups (cust.) | Actual gal | WAPE | Bias | Median APE | Median actual ÷ proj. |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|"]
    out = []
    for lo, hi in c.GAP_BINS:
        label = c.bin_label(lo)
        sub = [r for r in rows if r["bin"] == label]
        if not sub:
            continue
        rt = Counter(routing(r) for r in sub)
        comp = Counter(composition(r) for r in sub)
        n = len(sub)
        proj = [dict(r, base=r["pred"]) for r in sub if r["scored_unheld"]]
        s = m.summary(proj, "base") if proj else None
        ratio = statistics.median(r["actual"] / r["pred"] for r in proj if r["pred"] > 0) if proj else None
        other = n - sum(rt[k] for k in ("scored", "held (comeback)", "stale", "will-call", "insufficient"))
        pct = lambda k: f"{100 * rt[k] / n:.0f}%"
        lines.append(
            f"| {label} | {len({r['cid'] for r in sub})} | {len({(r['cid'], r['ev_i']) for r in sub})} | {n} "
            f"| {pct('scored')} | {pct('held (comeback)')} | {pct('stale')} | {pct('will-call')} "
            f"| {pct('insufficient')} | {100 * other / n:.0f}% "
            f"| {100 * comp['closer'] / n:.0f}% / {100 * comp['seasonal-open'] / n:.0f}% / {100 * comp['neither'] / n:.0f}% "
            + (f"| {s['pickups']} ({s['customers']}) | {s['actual_gal']:,.0f} | {s['wape']:.1f}% | {s['bias']:+.1f}% "
               f"| {s['median_ape']:.1f}% | {ratio:.2f} |" if s else "| 0 | – | – | – | – | – |"))
        out.append({"bin": label, "pickups": n, **{f"route_{k}": v for k, v in rt.items()},
                    **({"proj_pickups": s["pickups"], "actual_gal": s["actual_gal"], "wape": s["wape"],
                        "bias": s["bias"], "median_ape": s["median_ape"], "median_ratio": ratio} if s else {})})
    return "\n".join(lines), out


def by_since_table(ev_records, gap_range):
    lo, hi = gap_range
    lines = ["| Since (predicts) | Pickups (cust.) | Actual gal | Champion WAPE | Bias | Median APE |",
             "|---|---|---:|---:|---:|---:|"]
    for since in (0, 1, 2, 3):
        sub = [dict(r, base=r["pred"]) for r in ev_records
               if r["year"] in replay.EVAL_YEARS and lo <= r["ev_gap"] <= hi and r["since"] == since
               and (r["scored"] if since else (r["rate"] is not None and r["exposure"] > 0))]
        label = f"{since} (R{since + 1})" + (" — never projected; rate × days shown" if since == 0 else "")
        lines.append(f"| {label} | {c.stat_line(sub)} |")
    return "\n".join(lines)


def decomposition(pickups, ev_records, cals):
    """For 181-365 d returns, since 1-2, scored by the champion."""
    rows = []
    for r in ev_records:
        if not (r["year"] in replay.EVAL_YEARS and r["scored"] and r["since"] in (1, 2)
                and SCOPE_GAP[0] <= r["ev_gap"] <= SCOPE_GAP[1]):
            continue
        ps = pickups[r["cid"]]
        i = r["ev_i"]
        pre_rate, pre_gaps, _ = pre_gap(ps, i)
        if pre_rate is None or pre_gaps < 3:
            continue
        closed = cals.get(r["cid"], ps, ps[i]["date"].year, "loose")
        a, b = ps[i - 1]["date"], ps[i]["date"]
        pre_open = c.pooled_open(ps[:i], closed)
        days = r["exposure"]
        rows.append({
            "cid": r["cid"], "since": r["since"], "actual": r["actual"], "champ": r["pred"],
            "old": pre_rate * days, "old_open": (pre_open or pre_rate) * c.open_days(r["clock_start"], r["date"], closed),
            "r1": ps[i]["gallons"], "r1_old": pre_rate * (b - a).days,
            "r1_old_open": (pre_open or pre_rate) * c.open_days(a, b, closed),
            "gap_days": (b - a).days, "gap_open": c.open_days(a, b, closed), "has_cal": bool(closed),
            "post_rate": r["actual"] / days if days else None, "pre_rate": pre_rate,
            "champ_rate": r["rate"], "stage": r["stage"],
        })
    return rows


def decomposition_md(rows):
    def ratio(sub, num, den):
        d = sum(x[den] for x in sub)
        return sum(x[num] for x in sub) / d if d else float("nan")
    out = ["| Subset | Pickups (cust.) | Actual ÷ champion | Actual ÷ pre-gap rate × days | Actual ÷ pre-gap open-day rate × open days | R1 ÷ pre-gap rate × gap days | R1 ÷ pre-gap open rate × gap open days | Gap days closed (calendar) | Median post ÷ pre rate | Median champion ÷ pre rate |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    groups = {"all": rows, "with a loose calendar": [x for x in rows if x["has_cal"]],
              "no calendar": [x for x in rows if not x["has_cal"]],
              "since 1 (R2)": [x for x in rows if x["since"] == 1],
              "since 2 (R3)": [x for x in rows if x["since"] == 2],
              "champion stage closer": [x for x in rows if x["stage"] == bpt.STAGE_CLOSER],
              "champion stage established": [x for x in rows if x["stage"] == bpt.STAGE_ESTABLISHED]}
    for name, sub in groups.items():
        if not sub:
            continue
        closed_share = 1 - sum(x["gap_open"] for x in sub) / sum(x["gap_days"] for x in sub)
        out.append(
            f"| {name} | {len(sub)} ({len({x['cid'] for x in sub})}) | {ratio(sub, 'actual', 'champ'):.2f} "
            f"| {ratio(sub, 'actual', 'old'):.2f} | {ratio(sub, 'actual', 'old_open'):.2f} "
            f"| {ratio(sub, 'r1', 'r1_old'):.2f} | {ratio(sub, 'r1', 'r1_old_open'):.2f} | {100 * closed_share:.0f}% "
            f"| {statistics.median(x['post_rate'] / x['pre_rate'] for x in sub if x['pre_rate']):.2f} "
            f"| {statistics.median(x['champ_rate'] / x['pre_rate'] for x in sub if x['pre_rate']):.2f} |")
    return "\n".join(out)


def event_table(pickups, checks, ev_records, cals, capacities):
    """One row per 181-365 d return event with R1 in 2017+ (affected_customers.csv)."""
    by_ev = defaultdict(dict)
    for r in ev_records:
        if SCOPE_GAP[0] <= r["ev_gap"] <= SCOPE_GAP[1]:
            by_ev[(r["cid"], r["ev_i"])][r["since"]] = r
    rows = []
    for (cid, i), recs in sorted(by_ev.items()):
        ps = pickups[cid]
        pre_rate, pre_gaps, year_gal = pre_gap(ps, i)
        r1y = ps[i]["date"].year
        row = {
            "study": "A: 181-365 d return", "customer_id": cid, "customer": ps[i]["name"],
            "gap_days": recs[min(recs)]["ev_gap"], "gap_from": ps[i - 1]["date"].isoformat(),
            "return_date": ps[i]["date"].isoformat(), "return_class": return_class(ps, i),
            "season_type": season_type(ps, i),
            "detector_closed": "".join(bpt.MONTHS[x][0] for x in sorted(cals.get(cid, ps, r1y, "detector"))),
            "loose_closed": ",".join(bpt.MONTHS[x] for x in sorted(cals.get(cid, ps, r1y, "loose"))),
            "pre_gap_rate": round(pre_rate, 3) if pre_rate is not None else "",
            "pre_gap_gaps": pre_gaps, "pre_gap_year_gal": year_gal,
            "capacity_today_hindsight": capacities.get(cid) or "",
            "history_start": bpt.HISTORY_STARTS[cid][1].isoformat() if cid in bpt.HISTORY_STARTS else "",
        }
        for since in (0, 1, 2, 3):
            r = recs.get(since)
            if not r:
                continue
            tag = f"R{since + 1}"
            row[f"{tag}_date"] = r["date"].isoformat()
            row[f"{tag}_actual"] = r["actual"]
            row[f"{tag}_stage"] = r["stage"] or ""
            row[f"{tag}_route"] = routing(r)
            row[f"{tag}_champion_rate"] = round(r["rate"], 3) if r["rate"] is not None else ""
            row[f"{tag}_champion_pred"] = round(r["pred"], 1) if r["pred"] is not None else ""
            row[f"{tag}_clock_reset_by_check"] = r["clock_start"] != ps[r["k"] - 1]["date"]
        rows.append(row)
    return rows


# ─────────────────────────────────────────────
# Phase 2: candidates
# ─────────────────────────────────────────────

def a_rate(r, h, closed, gallons=None):
    """
    The champion's stage formula on open days; None where the stage is not one
    the open-day version changes (restart, seasonal, closer: those already use
    their own windows or exposure).
    """
    if r["restart"] or r["stage"] not in (bpt.STAGE_ESTABLISHED, bpt.STAGE_NEW):
        return None
    p = c.pooled_open(h, closed, gallons)
    if p is None:
        return None
    if r["stage"] == bpt.STAGE_NEW:
        return p
    y = c.prev_year_open(h, r["date"], closed, gallons)
    return None if y is None else (p + y) / 2


def champion_rate_with(r, h, gallons):
    """The champion's established / new rate with modified gallons (A4 on A0)."""
    if r["restart"] or r["stage"] not in (bpt.STAGE_ESTABLISHED, bpt.STAGE_NEW):
        return None
    p = c.pooled_open(h, frozenset(), gallons)
    if r["stage"] == bpt.STAGE_NEW:
        return p
    y = c.prev_year_open(h, r["date"], frozenset(), gallons)
    return None if p is None or y is None else (p + y) / 2


def allowance(kind, h, cid, capacities):
    if kind == "0":
        return 0.0
    if kind == "5 gal":
        return 5.0
    if kind == "25% of median pickup":
        return 0.25 * (c.median_pickup(h) or 0)
    cap = capacities.get(cid)
    return 0.1 * cap if cap else None


def build_candidates(pickups, ev_records, cals, capacities):
    """Rows {cid, year, actual, base, cand_<name>, splits...} for the A scope."""
    rows = []
    for r in ev_records:
        if not (r["scored"] and r["since"] in SINCE_SCORED
                and SCOPE_GAP[0] <= r["ev_gap"] <= SCOPE_GAP[1]):
            continue
        ps = pickups[r["cid"]]
        h = ps[:r["k"]]
        i = r["ev_i"]
        pre_rate, pre_gaps, year_gal = pre_gap(ps, i)
        row = {"cid": r["cid"], "name": r["name"], "year": r["year"], "actual": r["actual"],
               "base": r["pred"], "since": r["since"], "stage": r["stage"],
               "season_type": season_type(ps, i), "return_class": return_class(ps, i),
               "pre_gaps": pre_gaps, "pre_year_gal": year_gal, "cand": {}}
        days = (r["date"] - r["clock_start"]).days
        for kind in CALENDARS:
            closed = cals.get(r["cid"], ps, r["year"], kind)
            rate = a_rate(r, h, closed) if closed else None
            row["cand"][f"A1 open days ({kind})"] = (
                rate * c.open_days(r["clock_start"], r["date"], closed) if rate is not None else None)
        if pre_rate is not None and pre_gaps >= 3:
            row["cand"]["A2 pre-gap rate"] = pre_rate * days
            for w in A3_WEIGHTS:
                row["cand"][f"A3 blend w={w}"] = (w * pre_rate + (1 - w) * r["rate"]) * r["exposure"]
        # Scope (b), decided after Phase 1 (Sarge, 2026-10-16): A2/A3 on the
        # established/new stage only; closers already use open-season gaps.
        # Informed by diagnostics that included 2024-26: labelled, (a) kept.
        if r["stage"] in (bpt.STAGE_ESTABLISHED, bpt.STAGE_NEW):
            for k in [k for k in row["cand"] if k.startswith(("A2", "A3"))]:
                row["cand"][k + SCOPE_B] = row["cand"][k]
        closed = cals.get(r["cid"], ps, r["year"], "loose")
        for kind in A4_ALLOWANCES:
            al = allowance(kind, ps[:i], r["cid"], capacities)
            if al is None:
                continue
            g = lambda j, al=al: max(0.0, h[j]["gallons"] - al) if j == i else h[j]["gallons"]
            r0 = champion_rate_with(r, h, g)
            if r0 is not None:
                row["cand"][f"A4 on A0, allowance {kind}"] = r0 * r["exposure"]
            if closed:
                r1 = a_rate(r, h, closed, g)
                if r1 is not None:
                    row["cand"][f"A4 on A1 loose, allowance {kind}"] = r1 * c.open_days(r["clock_start"], r["date"], closed)
        rows.append(row)
    return rows


def head_to_head(rows, name, fallback=True):
    """Identical pickups: the candidate where it exists, else the champion (fallback) or dropped."""
    out = []
    for r in rows:
        v = r["cand"].get(name)
        if v is None and not fallback:
            continue
        out.append({**{k: r[k] for k in r if k != "cand"}, "cand": r["base"] if v is None else v})
    return out


def split_md(rows, keyfn, title):
    lines = [f"| {title} | Pickups (cust.) | Champion WAPE | Candidate WAPE | Δ | 95% interval | Bias: champ → cand | Better/worse |",
             "|---|---|---:|---:|---:|---|---|---|"]
    for k, sub in m.by(rows, keyfn).items():
        cmp = m.compare(sub, draws=1000)
        lines.append(f"| {k} | {cmp['pickups']} ({cmp['customers']}) | {cmp['base_wape']:.1f}% | {cmp['cand_wape']:.1f}% "
                     f"| {cmp['d_wape']:+.2f} | {m.ci(cmp)} | {cmp['base_bias']:+.1f}% → {cmp['cand_bias']:+.1f}% "
                     f"| {cmp['cust_better']}/{cmp['cust_worse']} |")
    return "\n".join(lines)


def run_candidates(pickups, records, ev_records, cals, capacities, results, impacts):
    rows = build_candidates(pickups, ev_records, cals, capacities)
    eval_rows = [r for r in rows if r["year"] in replay.EVAL_YEARS]
    names = sorted({n for r in rows for n in r["cand"]})
    md = []
    yearly = {}
    # The fired subset (scored only where the candidate exists) and the full A scope.
    for name in names:
        fired = head_to_head(eval_rows, name, fallback=False)
        yearly[name] = c.evaluate(results, "A", name, "181-365 d return, since 1-3, where the candidate exists", fired)
    # Hindsight sensitivity: the capacity cap with today's capacities.
    for name in ("A1 open days (loose)", "A2 pre-gap rate" + SCOPE_B):
        c.evaluate(results, "A", name + " (capped, today's capacity: hindsight)",
                   "181-365 d return, since 1-3, where the candidate exists",
                   c.capped(head_to_head(eval_rows, name, fallback=False), capacities))
    # A3: the weight chosen on dev, rolling and odd->even, for scope (a) and (b).
    dev_pick = {}
    for suffix in ("", SCOPE_B):
        grid = {w: head_to_head(eval_rows, f"A3 blend w={w}{suffix}", fallback=False) for w in A3_WEIGHTS}
        dev_pick[suffix] = harness.choose(grid, replay.DEV_YEARS)
        rolled, picks = harness.rolling_choice(grid)
        c.evaluate(results, "A", f"A3 blend, rolling choice{suffix}", "181-365 d return, since 1-3, where the candidate exists",
                   rolled, note="picks " + ", ".join(f"{y}:{v}" for y, v in picks.items()))
        oe, oe_picks = harness.odd_even_choice(grid)
        c.evaluate(results, "A", f"A3 blend, odd->even choice{suffix}", "181-365 d return, since 1-3, where the candidate exists",
                   oe, note="picks " + ", ".join(f"{y}:{v}" for y, v in oe_picks.items()))
        dev_pick[suffix + "rolling"] = picks

    md.append("## Phase 2: candidates (Experiment A)\n")
    md.append("Scope: every pickup the champion projects and scores whose history holds a 181–365 d return "
              "with 1–3 pickups since (R2–R4). Each candidate is scored where it forms a rate; the champion "
              "is on the same pickups. A3's weight grid and A4's allowances were declared before the run. "
              "Scope (b) — A2/A3 on established/new-stage pickups only — was chosen after the Phase 1 "
              "diagnostics, which included 2024–26, so its confirm rows are not independent of that choice.\n")
    sel = [r for r in results if r["experiment"] == "A"]
    md.append(harness.table(sel))
    md.append("\n### Per year (Δ WAPE points, candidate − champion)\n")
    md.append(harness.year_table(yearly))
    for suffix, label in (("", "scope (a), all stages"), (SCOPE_B, "scope (b), established/new")):
        md.append(f"\nA3 weight chosen on dev 2021–23, {label}: **w = {dev_pick[suffix]}**. Rolling picks: "
                  + ", ".join(f"{y}: {v}" for y, v in dev_pick[suffix + "rolling"].items()) + ".")
    md.append("")

    # Splits for the dev-chosen A3, A2 and A1 loose, by since and the requested subsets.
    for name in ("A1 open days (loose)", "A2 pre-gap rate", f"A3 blend w={dev_pick['']}",
                 "A2 pre-gap rate" + SCOPE_B, f"A3 blend w={dev_pick[SCOPE_B]}" + SCOPE_B):
        sub = head_to_head(eval_rows, name, fallback=False)
        if not sub:
            continue
        md.append(f"\n### {name}: by subset (all eval years)\n")
        md.append(split_md(sub, lambda r: f"since {r['since']} (R{r['since'] + 1})", "Pickup"))
        md.append("")
        md.append(split_md(sub, lambda r: r["season_type"], "Season type"))
        md.append("")
        md.append(split_md(sub, lambda r: r["return_class"], "Return class"))
        md.append("")
        vol_med = statistics.median(r["pre_year_gal"] for r in eval_rows)
        md.append(split_md(sub, lambda r, v=vol_med: f"pre-gap year ≥ {v:.0f} gal" if r["pre_year_gal"] >= v
                           else f"pre-gap year < {v:.0f} gal", "Volume"))
        md.append("")
        md.append(split_md(sub, lambda r: "6 pre-gap gaps" if r["pre_gaps"] >= 6 else "3–5 pre-gap gaps", "Pre-gap history"))
        md.append("\n" + c.top_contributors(sub))
        for wname, years in (("dev 2021-23", replay.DEV_YEARS), ("confirm 2024-26", replay.CONFIRM_YEARS)):
            impacts.extend(harness.customer_impacts("A", name, wname, [r for r in sub if r["year"] in years]))

    # Effect on the WHOLE scored set if A1 applied everywhere its calendar exists.
    md.append("\n### A1 applied to every scored pickup (not only returns)\n")
    md.append("A1 is a general rule: wherever a calendar exists, closed-month days leave every rate window. "
              "Its cost elsewhere:\n")
    md.append(whole_set_a1(pickups, records, cals, results))
    return "\n".join(md), dev_pick


def whole_set_a1(pickups, records, cals, results):
    lines = []
    for kind in CALENDARS:
        rows = []
        for r in records:
            if not r["scored"] or r["year"] not in replay.EVAL_YEARS:
                continue
            ps = pickups[r["cid"]]
            closed = cals.get(r["cid"], ps, r["year"], kind)
            rate = a_rate(r, ps[:r["k"]], closed) if closed else None
            cand = rate * c.open_days(r["clock_start"], r["date"], closed) if rate is not None else r["pred"]
            rows.append({"cid": r["cid"], "name": r["name"], "year": r["year"], "actual": r["actual"],
                         "base": r["pred"], "cand": cand})
        c.evaluate(results, "A", f"A1 open days ({kind}), everywhere", "every scored pickup", rows)
    lines.append(harness.table([r for r in results if r["scope"] == "every scored pickup"]))
    return "\n".join(lines)


# ─────────────────────────────────────────────

def main():
    pickups, checks, _, records = ch.load_or_build()
    capacities = bpt.load_capacities()
    cals = c.Calendars()
    ev = attach_events(pickups, records)

    md = ["# Returns after 181–365 days (Experiment A) and the long-gap distribution (B1)",
          "",
          "Generated by `exp_comeback.py` on the October 15 champion replay (`champion.py`). "
          "Eval years 2018–19 and 2021–26 (2020 replayed, not pooled). *Since* = post-return pickups "
          "already in the history: since 1 predicts R2, the first pickup production projects after a "
          "gap over 180 d.",
          "", "## B1. Long-gap distribution (since 1–2: the first two projected pickups)", ""]
    table, b1 = b1_table(ev)
    md.append(table)
    md.append("\n*Projection exists* = ranked with the hold switched off. In the bins over 365 d "
              "production withholds these pickups; the WAPE shown is what their projection would have "
              "scored, never a zero error.\n")
    md.append("## A1 diagnostics: 181–365 d, by post-return pickup\n")
    md.append(by_since_table(ev, SCOPE_GAP))
    md.append("\nFor comparison, 120–180 d (R1 is projected there):\n")
    md.append(by_since_table(ev, (120, 180)))
    dec = decomposition(pickups, ev, cals)
    md.append("\n## Where the miss comes from (181–365 d, since 1–2, ≥ 3 pre-gap gaps)\n")
    md.append(decomposition_md(dec))
    events = event_table(pickups, checks, ev, cals, capacities)
    cls = Counter((e["return_class"], e["season_type"]) for e in events)
    md.append("\n## Event classes (every 181–365 d return, 2017+)\n")
    md.append("| Return class | Season type | Events |\n|---|---|---:|")
    for (rc, stp), n in sorted(cls.items()):
        md.append(f"| {rc} | {stp} | {n} |")

    results, impacts = [], []
    cand_md, _ = run_candidates(pickups, records, ev, cals, capacities, results, impacts)
    md.append("\n" + cand_md)

    (HERE / "comeback_181_365.md").write_text("\n".join(md) + "\n")
    harness.write_results(HERE / "results_A.csv", results)
    harness.write_impacts(HERE / "impacts_A.csv", impacts)
    fields = sorted({k for e in events for k in e}, key=lambda k: (k[0] == "R", k))
    with open(HERE / "events_A.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(events[0].keys()) + [k for k in fields if k not in events[0]])
        w.writeheader()
        w.writerows(events)
    print("wrote comeback_181_365.md, results_A.csv, impacts_A.csv, events_A.csv:", len(events), "events")


if __name__ == "__main__":
    main()
