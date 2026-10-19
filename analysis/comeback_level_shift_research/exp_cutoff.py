#!/usr/bin/env python3
"""
Experiment B2/B3: is 365 days the right comeback-hold cutoff? Research only.

The hold never changes a projection; it only removes rows. So accuracy on the
pickups two cutoffs BOTH score is identical by construction, and the whole
trade is coverage: which pickups a cell withholds, how wrong the projection
it suppresses would have been, and which route candidates disappear.

Grid, declared before the run: thresholds 270, 300, 365 (production), 450,
540 days x hold lengths 1, 2, 3 (production) post-return pickups. A hold of
1 never holds anything (the first post-return pickup is always in the
history), so it is the pre-2026-10-15 ladder. On release the row takes the
ladder's own rate; the 180-day dormant restart is untouched.

B3 uses weekly snapshots (Mondays, eval years) of every customer whose
history could be held by any cell: was the row ranked, would the projection
have flagged it (>= the as-of median pickup, the October study's rule), and
did a pickup follow within 30 days. A week without a pickup does not prove
the barrel was not full: the outcome is partially observed.

    RESEARCH_CACHE=... python3 exp_cutoff.py
"""

import statistics
from collections import defaultdict
from datetime import date, timedelta

import champion as ch
import common as c
import metrics as m
import replay
from exp_comeback import return_class
from replay import bpt

HERE = c.HERE
THRESHOLDS = (270, 300, 365, 450, 540)
HOLDS = (1, 2, 3)
PRODUCTION = (365, 3)
FLAG_HORIZON = 30


def volume_cut(records):
    """75th percentile of gallons in the 365 d before the as-of, over scored records."""
    return statistics.quantiles([r["year_gal"] for r in records if r["scored_unheld"]], n=4)[2]


def add_context(pickups, records):
    for r in records:
        h = pickups[r["cid"]][:r["k"]]
        r["year_gal"] = sum(p["gallons"] for p in h[1:] if (r["date"] - p["date"]).days <= bpt.YEAR_DAYS)
        e = c.event_of(h)
        # Volume before the gap: after a long gap the trailing year is mostly the gap itself.
        start = h[e[0] - 1]["date"] if e else r["date"]
        r["pre_gal"] = sum(p["gallons"] for p in h[1:(e[0] if e else len(h))]
                           if (start - p["date"]).days < bpt.YEAR_DAYS)
        r["ret_class"] = return_class(pickups[r["cid"]], e[0]) if e and e[1] > 180 else ""
        r["seasonal"] = bool(r["season_spec"]) or r["ret_class"] == "reopening"


def cell_rows(records, T, n):
    sc = [r for r in records if r["year"] in replay.EVAL_YEARS and r["scored_unheld"]]
    withheld = [r for r in sc if ch.held(r, T, n)]
    kept = [r for r in sc if not ch.held(r, T, n)]
    return sc, withheld, kept


def b2_table(records, vcut):
    lines = ["| Cutoff | Hold (pickups) | Withheld pickups (cust.) | Withheld gal | WAPE they would have scored | Bias | Seasonal share | High-volume share | vs production: newly held / released | Error on the newly held (WAPE, bias) | Error on the released (WAPE, bias) | Scored pickups | Scored WAPE (coverage-driven) |",
             "|---|---|---|---:|---:|---:|---:|---:|---|---|---|---:|---:|"]
    prod_held = {(r["cid"], r["k"]) for r in records
                 if r["year"] in replay.EVAL_YEARS and r["scored_unheld"] and ch.held(r, *PRODUCTION)}
    out = []
    for T in THRESHOLDS:
        for n in HOLDS:
            sc, wh, kept = cell_rows(records, T, n)
            keys = {(r["cid"], r["k"]) for r in wh}
            new, rel = len(keys - prod_held), len(prod_held - keys)
            def err(sel):
                x = m.summary([dict(r, base=r["pred"]) for r in sc if sel((r["cid"], r["k"]))], "base")
                return f"{x['wape']:.0f}%, {x['bias']:+.0f}% ({x['customers']} cust.)" if x else "–"
            e_new = err(lambda key: key in keys and key not in prod_held)
            e_rel = err(lambda key: key in prod_held and key not in keys)
            s = m.summary([dict(r, base=r["pred"]) for r in wh], "base") if wh else None
            ks = m.summary([dict(r, base=r["pred"]) for r in kept], "base")
            seas = sum(r["seasonal"] for r in wh) / len(wh) if wh else 0
            hv = sum(r["pre_gal"] >= vcut for r in wh) / len(wh) if wh else 0
            mark = " **(production)**" if (T, n) == PRODUCTION else ""
            lines.append(
                f"| {T} d{mark} | {n} | " + (f"{s['pickups']} ({s['customers']}) | {s['actual_gal']:,.0f} | {s['wape']:.1f}% | {s['bias']:+.1f}%"
                                              if s else "0 | 0 | – | –")
                + f" | {100 * seas:.0f}% | {100 * hv:.0f}% | +{new} / −{rel} | {e_new} | {e_rel} | {ks['pickups']:,} | {ks['wape']:.2f}% |")
            out.append({"experiment": "B", "candidate": f"hold {T} d x {n}", "scope": "comeback hold",
                        "window": "all eval years", "customers": s["customers"] if s else 0,
                        "pickups": s["pickups"] if s else 0, "actual_gal": s["actual_gal"] if s else 0,
                        "base_wape": s["wape"] if s else "", "base_bias": s["bias"] if s else "",
                        "note": f"withheld pickups; their unheld projection error. newly held {new}, released {rel}; "
                                f"scored {ks['pickups']} at {ks['wape']:.2f}% WAPE (coverage-driven)"})
    return "\n".join(lines), out


def fine_gap_table(records):
    """Unheld champion on since 1-2 by fine gap bins around one year: a break, or a slope?"""
    bins = [(181, 240), (241, 300), (301, 335), (336, 365), (366, 400), (401, 450), (451, 540), (541, 730), (731, 10 ** 6)]
    lines = ["| Gap | Pickups (cust.) | Actual gal | WAPE | Bias | Median APE | Median actual ÷ proj. |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for lo, hi in bins:
        sub = []
        for r in records:
            if r["year"] not in replay.EVAL_YEARS or not r["scored_unheld"]:
                continue
            gaps = [g for i, g in r["comeback_gaps"] if i > r["k"] - 3]
            if gaps and lo <= gaps[0] <= hi:
                sub.append(dict(r, base=r["pred"]))
        if not sub:
            continue
        ratio = statistics.median(x["actual"] / x["pred"] for x in sub if x["pred"] > 0)
        label = f"{lo}–{hi} d" if hi < 10 ** 6 else f"> {lo - 1} d"
        lines.append(f"| {label} | {c.stat_line(sub)} | {ratio:.2f} |")
    return "\n".join(lines)


def snapshots(pickups, checks):
    """Weekly states for customers a cell could hold: (cid, date, ranked, flagged, event, cells_held)."""
    seasons = replay.SeasonCache()
    out = []
    first = date(2018, 1, 1)
    first += timedelta(days=(7 - first.weekday()) % 7)
    last_day = max(p["date"] for ps in pickups.values() for p in ps)
    min_t = min(THRESHOLDS)
    for cid in sorted(pickups):
        ps = pickups[cid]
        reg = bpt.MODEL_OVERRIDES.get(cid)
        if reg and reg.status in replay.NO_RATE:
            continue
        dates = [p["date"] for p in ps]
        t = first
        k = 0
        while t <= last_day - timedelta(days=FLAG_HORIZON):
            while k < len(dates) and dates[k] < t:
                k += 1
            if t.year in replay.EVAL_YEARS and k >= 2:
                h = ps[:k]
                cg = [(i, (h[i]["date"] - h[i - 1]["date"]).days)
                      for i in range(k - 1, max(k - ch.MAX_HOLD_PICKUPS, 0), -1)]
                cg = [x for x in cg if x[1] > min_t]
                if cg:
                    st = ch.state(cid, h, t, checks.get(cid, ()), seasons, "core")
                    if st["section"] == bpt.SECTION_RANKED and st["rate"] is not None:
                        rec = {"holdable": ch.holdable(st), "k": k, "comeback_gaps": cg}
                        proj = st["rate"] * st["exposure"]
                        out.append({
                            "cid": cid, "date": t, "year": t.year,
                            "flagged": proj >= (c.median_pickup(h) or float("inf")),
                            "event": k < len(dates) and (dates[k] - t).days <= FLAG_HORIZON,
                            "held": {(T, n): ch.held(rec, T, n) for T in THRESHOLDS for n in HOLDS},
                        })
            t += timedelta(days=7)
    return out


def b3_table(snaps):
    lines = ["| Cutoff | Hold | Ranked weeks suppressed (cust.) | Of which flagged | Flagged, pickup within 30 d | Flagged, no pickup within 30 d | vs production: weeks newly suppressed / restored |",
             "|---|---|---|---:|---:|---:|---|"]
    prod = {(s["cid"], s["date"]) for s in snaps if s["held"][PRODUCTION]}
    for T in THRESHOLDS:
        for n in HOLDS:
            sup = [s for s in snaps if s["held"][(T, n)]]
            fl = [s for s in sup if s["flagged"]]
            hit = sum(s["event"] for s in fl)
            keys = {(s["cid"], s["date"]) for s in sup}
            mark = " **(production)**" if (T, n) == PRODUCTION else ""
            lines.append(f"| {T} d{mark} | {n} | {len(sup)} ({len({s['cid'] for s in sup})}) | {len(fl)} "
                         f"| {hit} | {len(fl) - hit} | +{len(keys - prod)} / −{len(prod - keys)} |")
    lines.append("\nReference (the October study, `model_research_2026_10/reopening.md`): of in-season ranked "
                 "closer weeks flagged by the same rule, 66% saw a pickup within 30 days.")
    return "\n".join(lines)


def main():
    pickups, checks, _, records = ch.load_or_build()
    add_context(pickups, records)
    vcut = volume_cut(records)
    md = ["# Comeback-hold cutoff (Experiment B2/B3)", "",
          "Generated by `exp_cutoff.py` on the October 15 champion replay. The hold removes rows and never "
          "changes a projection, so on pickups two cutoffs both score their accuracy is identical by "
          "construction. What differs is coverage. The table reports the pickups each cell withholds and the "
          "error their projection would have had, never a zero error. *Scored WAPE* moves only because the "
          "scored set changes. It is not a model improvement.", "",
          f"High-volume = at least {vcut:.0f} gal in the 365 days before the GAP began (the top quartile of "
          "trailing-year gallons over all scored pickups). Seasonal = a closer, or a return classified as a reopening.", "",
          "## Fine gap bins: the first projected pickups (since 1–2), unheld champion", "",
          fine_gap_table(records), "",
          "## B2. Cutoff × hold length (all eval years)", ""]
    t, results = b2_table(records, vcut)
    md.append(t)
    md.append("\n## B3. Route snapshots (weekly, eval years)\n")
    snaps = snapshots(pickups, checks)
    md.append(b3_table(snaps))
    (HERE / "comeback_cutoff.md").write_text("\n".join(md) + "\n")
    import harness
    harness.write_results(HERE / "results_B.csv", results)
    print("wrote comeback_cutoff.md, results_B.csv;", len(snaps), "snapshots")


if __name__ == "__main__":
    main()
