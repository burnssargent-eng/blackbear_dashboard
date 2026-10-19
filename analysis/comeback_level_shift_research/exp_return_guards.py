#!/usr/bin/env python3
"""
Guards on the return stage (Sarge, 2026-10-18), research only.

Prompted by two live rows: What Ales You (fading account, 2023's busy rate
propping up "same time last year" -> 133 gal) and Sugarbush Resort Golf
Course (closes every October, under the detector's volume floor, so the
projection would keep growing all winter).

Variants, declared before the first run:
    V0  champion: the 50/50 diluted by the gap (main before the return stage)
    V1  the return stage as built (bpt.return_rate)
    V2  V1 only where the gap repeats an earlier year's closure (an earlier
        gap > 120 d, >= 300 d before, midpoint within a month: the
        "reopening" class of exp_comeback); any other return is held
    V3  V1, accruing on open days only: months closed in the loose calendar
        (common.Calendars, from complete years before the as-of year) add no oil
    V4  V2 + V3
Added 2026-10-18 after the held rows were reviewed (declared before running):
    V5  V4 for reopenings; any other return with >= PRE_GAP_MIN_PICKUPS
        pickups before the gap: the pre-gap pooled rate (<= 6 gaps) x days
        since the clock; held otherwise
    V5b V5, but the 3rd pickup back on 1/2 pre-gap rate + 1/2 the rate from
        the 1st to the 2nd pickup back

Two tests:
  accuracy  on pickups ranked by V0 and the variant (identical pickups);
            pickups the variant holds are reported as coverage, with V0's error
  snapshots every Monday of the eval years where the customer is in the
            return window (181-365 d gap, 1-2 pickups since, would be
            ranked): flagged = projection >= the as-of median pickup (the
            October study's rule); outcome = a pickup within 30 days. A week
            without a pickup does not prove the barrel was not full.

    RESEARCH_CACHE=... python3 exp_return_guards.py
"""

from datetime import date, timedelta

import champion as ch
import common as c
import exp_comeback as ec
import harness
import metrics as m
import replay
from replay import bpt

HERE = c.HERE
VARIANTS = ("V1 as built", "V2 reopenings only", "V3 open days only", "V4 reopenings + open days",
            "V5 V4 + pre-gap rate otherwise", "V5b V4 + pre-gap blend otherwise")
PRE_GAP_MIN_PICKUPS = 6
HORIZON = 30


def eligible(st):
    return (st["stage"] in (bpt.STAGE_ESTABLISHED, bpt.STAGE_NEW) and not st["restart"]
            and not st["season_spec"] and st["days_since"] <= bpt.STALE_DAYS and not st["will_call"])


def v1_rate(h, asof):
    """(rate or None, gap, since): the return stage as first built (2026-10-17), no reopening gate."""
    back = bpt.return_gap(h)
    if not back:
        return None
    i, gap, since = back
    ly = bpt.rate_same_time_last_year(h, asof)
    if ly is None:
        return None, gap, since
    if since == 1:
        return ly, gap, since
    fb = bpt.bsm.rate_span(h, i, i + 1)
    return (None if fb is None else bpt.RETURN_RECENT_WEIGHT * fb + (1 - bpt.RETURN_RECENT_WEIGHT) * ly), gap, since


def variants(cid, ps, h, asof, st, cals):
    """{variant: projected gallons or None (held)}, or None when not a return."""
    ret = v1_rate(h, asof)
    if not ret:
        return None
    rate = ret[0]
    i = len(h) - ret[2]
    reopening = ec.return_class(ps, i) == "reopening"
    closed = cals.get(cid, ps, asof.year, "loose")
    calendar_days = st["exposure"]
    open_d = c.open_days(st["clock_start"], asof, closed)   # V3/V4: common.Calendars "loose"
    out = {}
    out["V1 as built"] = rate * calendar_days if rate is not None else None
    out["V2 reopenings only"] = out["V1 as built"] if reopening else None
    out["V3 open days only"] = rate * open_d if rate is not None else None
    out["V4 reopenings + open days"] = out["V3 open days only"] if reopening else None
    out["V5 V4 + pre-gap rate otherwise"] = out["V5b V4 + pre-gap blend otherwise"] = out["V4 reopenings + open days"]
    if not reopening and i >= PRE_GAP_MIN_PICKUPS:
        pre = bpt.rate_pooled(h[:i])
        if pre is not None:
            out["V5 V4 + pre-gap rate otherwise"] = pre * calendar_days
            fb = bpt.bsm.rate_span(h, i, i + 1) if ret[2] == 2 else None
            blend = 0.5 * pre + 0.5 * fb if fb is not None else pre
            out["V5b V4 + pre-gap blend otherwise"] = blend * calendar_days
    # The builder's return_rate is V2 (2026-10-18): it must agree exactly.
    built = bpt.return_rate(h, asof)
    if built is not None:
        proj = None if built[0] is None else built[0] * calendar_days
        v2 = out["V2 reopenings only"]
        assert (proj is None) == (v2 is None) and (proj is None or abs(proj - v2) < 1e-9), (cid, asof, proj, v2)
    return out, reopening, bool(closed)


def accuracy(pickups, records, cals):
    rows = []
    for r in records:
        if not (r["scored"] and r["year"] in replay.EVAL_YEARS and eligible(r)):
            continue
        ps = pickups[r["cid"]]
        v = variants(r["cid"], ps, ps[:r["k"]], r["date"], r, cals)
        if v:
            rows.append({"cid": r["cid"], "name": r["name"], "year": r["year"], "actual": r["actual"],
                         "base": r["pred"], "v": v[0], "reopening": v[1]})
    return rows


def snapshots(pickups, checks, cals):
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
        t, k = first, 0
        while t <= last_day - timedelta(days=HORIZON):
            while k < len(dates) and dates[k] < t:
                k += 1
            if t.year in replay.EVAL_YEARS and k >= bpt.NEW_MIN_PICKUPS and bpt.return_gap(ps[:k]):
                h = ps[:k]
                st = ch.state(cid, h, t, checks.get(cid, ()), seasons, "core")
                if st["section"] == bpt.SECTION_RANKED and eligible(st):
                    v = variants(cid, ps, h, t, st, cals)
                    if v:
                        projs = {"V0 champion": st["rate"] * st["exposure"], **v[0]}
                        closed = cals.get(cid, ps, t.year, "loose")
                        out.append({
                            "cid": cid, "date": t, "year": t.year, "proj": projs, "reopening": v[1],
                            "median": c.median_pickup(h),
                            "closed_month": t.month - 1 in closed,
                            "event": k < len(dates) and (dates[k] - t).days <= HORIZON,
                        })
            t += timedelta(days=7)
    return out


def snapshot_table(snaps, subset=None):
    lines = ["| Model | Weeks listed | Held weeks | Flagged | Flagged, pickup ≤ 30 d | Flagged, no pickup ≤ 30 d | Flagged in a closed month | Pickup weeks flagged |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    snaps = [s for s in snaps if subset is None or subset(s)]
    events = sum(s["event"] for s in snaps)
    for name in ("V0 champion",) + VARIANTS:
        listed = [s for s in snaps if s["proj"][name] is not None]
        flagged = [s for s in listed if s["median"] and s["proj"][name] >= s["median"]]
        hit = sum(s["event"] for s in flagged)
        cm = sum(s["closed_month"] for s in flagged)
        nf = len(flagged) - hit
        lines.append(f"| {name} | {len(listed):,} | {len(snaps) - len(listed):,} | {len(flagged):,} | {hit:,} "
                     f"| {nf:,} ({100 * nf / len(flagged):.0f}%) | {cm:,} | {hit} of {events} |"
                     if flagged else f"| {name} | {len(listed):,} | {len(snaps) - len(listed):,} | 0 | 0 | – | 0 | 0 of {events} |")
    return "\n".join(lines)


def main():
    pickups, checks, _, records = ch.load_or_build()
    cals = c.Calendars()
    rows = accuracy(pickups, records, cals)
    results, yearly = [], {}
    md = ["# Guards on the return stage (V0–V4)", "",
          "Generated by `exp_return_guards.py`. Variants declared in the script header before the first run. "
          "181–365 d returns, established/new stage, 2nd and 3rd pickups back, eval years.", "",
          "## Accuracy on identical pickups (both ranked)", ""]
    cov = ["| Variant | Pickups it holds (cust.) | Actual gal | V0's WAPE / bias on them |", "|---|---|---:|---|"]
    for name in VARIANTS:
        both = [dict({k: v for k, v in r.items() if k != "v"}, cand=r["v"][name]) for r in rows if r["v"][name] is not None]
        yearly[name] = c.evaluate(results, "V", name, "returns ranked under V0 and the variant", both)
        held = [r for r in rows if r["v"][name] is None]
        s = m.summary(held, "base") if held else None
        cov.append(f"| {name} | {len(held)} ({len({r['cid'] for r in held})}) | "
                   + (f"{s['actual_gal']:,.0f} | {s['wape']:.1f}% / {s['bias']:+.1f}% |" if s else "0 | – |"))
    md += [harness.table(results), "", harness.year_table(yearly), "", "### Coverage", "", "\n".join(cov), ""]
    nonreo = [r for r in rows if not r["reopening"] and r["v"]["V5 V4 + pre-gap rate otherwise"] is not None]
    md += ["### Non-reopenings that V5 projects (V4 holds them)", ""]
    sub_results = []
    for name in ("V1 as built", "V5 V4 + pre-gap rate otherwise", "V5b V4 + pre-gap blend otherwise"):
        both = [dict({k: v for k, v in r.items() if k != "v"}, cand=r["v"][name]) for r in nonreo if r["v"][name] is not None]
        c.evaluate(sub_results, "V", name, "non-reopenings with >= 6 pre-gap pickups", both)
    md += [harness.table(sub_results), ""]
    results.extend(sub_results)
    reo = [r for r in rows if r["v"]["V1 as built"] is not None]
    md += ["### V1 by return class (where to draw V2's line)", "",
           harness.split_table([dict({k: v for k, v in r.items() if k != "v"}, cand=r["v"]["V1 as built"]) for r in reo],
                               lambda r: "reopening" if r["reopening"] else "other return", "Class"), ""]
    snaps = snapshots(pickups, checks, cals)
    md += ["## False urgency: weekly snapshots in the return window", "",
           f"{len(snaps):,} customer-weeks ({len({s['cid'] for s in snaps})} customers). Flagged = projection ≥ the "
           "as-of median pickup. *Pickup weeks flagged* = of the weeks followed by a pickup within 30 days, "
           "how many the model flagged. Outcomes are partially observed.", "",
           "### All", "", snapshot_table(snaps), "",
           "### Reopenings", "", snapshot_table(snaps, lambda s: s["reopening"]), "",
           "### Other returns", "", snapshot_table(snaps, lambda s: not s["reopening"]), ""]
    (HERE / "return_guards.md").write_text("\n".join(md) + "\n")
    harness.write_results(HERE / "results_V.csv", results)
    print("wrote return_guards.md, results_V.csv;", len(rows), "pickups,", len(snaps), "snapshots")


if __name__ == "__main__":
    main()
