#!/usr/bin/env python3
"""
Experiment G: closer open-day exposure.

The champion (G0) is production's rate_open_season: pooled over the last <= 6
gaps lying WHOLLY in open months, so it already divides by open days; G1
("exclude gaps crossing closed seasons") IS G0 and is not re-run.

  G2  last <= 6 gaps INCLUDING those that cross the closure, each counted at
      gallons / its open days only (days in open months). Gaps with no open
      day are skipped. >= 3 gaps.
  G3  the same gaps, exposure = sum of the customer's prior-year index over
      the gap's days (closed months ~0), projection = level x the index over
      the forecast days. >= 3 gaps.
  G4  this season only: wholly-open gaps starting on/after the season's 1st;
      >= 2 gaps, else G0.
  G5  1/2 G0 + 1/2 G4 (G0 where G4 does not exist). Fixed weight, not tuned.

No candidate has a free parameter, so nothing is tuned; dev/confirm are
reported for consistency.

Season = the detector (core mode, no registry): months below 10% of an
average month in the 2 complete years before the year.

Also: a weekly snapshot replay of every detected closer for the false-
urgency proxy (projected >= the customer's as-of median pickup, no pickup in
the next 30 days). Partially observed: no pickup does not prove no oil.

    PYTHONPATH=analysis python3 analysis/model_research_2026_10/exp_closer.py
"""

import statistics
from datetime import date, timedelta

import harness
import metrics as m
import replay
from replay import bpt, seasonal_open

cf = seasonal_open.cf


def open_days(a, b, months):
    return sum(1 for d in range(1, (b - a).days + 1) if (a + timedelta(days=d)).month - 1 in months)


def index_days(a, b, factor):
    return sum(factor[(a + timedelta(days=d)).month - 1] for d in range(1, (b - a).days + 1))


def gaps_back(h, keep, limit=bpt.WINDOW):
    """The most recent <= limit gaps (i-1, i) passing keep(a, b)."""
    out = []
    for i in range(len(h) - 1, 0, -1):
        a, b = h[i - 1]["date"], h[i]["date"]
        if (b - a).days > 0 and keep(a, b):
            out.append(i)
            if len(out) == limit:
                break
    return out


def g2(h, months):
    idx = gaps_back(h, lambda a, b: open_days(a, b, months) > 0)
    if len(idx) < bpt.CLOSER_MIN_OPEN_GAPS:
        return None
    gal = sum(h[i]["gallons"] for i in idx)
    days = sum(open_days(h[i - 1]["date"], h[i]["date"], months) for i in idx)
    return gal / days if days else None


def g3(h, factor, clock, asof):
    if not factor:
        return None
    idx = gaps_back(h, lambda a, b: index_days(a, b, factor) >= 1.0)
    if len(idx) < bpt.CLOSER_MIN_OPEN_GAPS:
        return None
    gal = sum(h[i]["gallons"] for i in idx)
    expo = sum(index_days(h[i - 1]["date"], h[i]["date"], factor) for i in idx)
    if expo <= 0:
        return None
    return gal / expo * cf.gap_weighted(factor, clock, asof)


def g4(h, months, opened):
    def keep(a, b):
        return a >= opened and all((a + timedelta(days=d)).month - 1 in months
                                   for d in range(1, (b - a).days + 1))
    idx = gaps_back(h, keep)
    if len(idx) < 2:
        return None
    return sum(h[i]["gallons"] for i in idx) / sum((h[i]["date"] - h[i - 1]["date"]).days for i in idx)


def closer_rows(records, pickups):
    seasons = {}
    rows, fallback = [], []
    for r in records:
        if not r["season_spec"] or r["year"] not in replay.EVAL_YEARS:
            continue
        if not (r["section"] == bpt.SECTION_RANKED and 0 < r["gap"] <= bpt.STALE_DAYS and r["days"] > 0):
            continue
        if r["registry_status"] in replay.NO_RATE:
            continue
        opened = bpt.season_opened(r["season_spec"], r["date"])
        if opened is None:
            continue                               # off-season call: G changes nothing
        h = pickups[r["cid"]][:r["k"]]
        months = bpt.season_months(r["season_spec"])
        key = (r["cid"], r["year"])
        if key not in seasons:
            s = seasonal_open.season_for_year(pickups[r["cid"]], r["year"])
            seasons[key] = s["factor"] if s else None
        days, clock = r["days"], r["clock_start"]
        r0 = r["rate"] if r["stage"] == bpt.STAGE_CLOSER else None
        cand = {"G2": g2(h, months), "G3": g3(h, seasons[key], clock, r["date"])}
        r4 = g4(h, months, opened)
        cand["G4"] = r4 if r4 is not None else r0
        cand["G5"] = (r0 + r4) / 2 if r0 is not None and r4 is not None else r0
        prior_years = len({p["date"].year for p in h if p["date"].year < r["year"]})
        season_pickups = sum(1 for p in h if p["date"] >= opened)
        row = {
            "cid": r["cid"], "year": r["year"], "actual": r["actual"], "days": days,
            "name": h[-1]["name"],
            "kind": "summer closer" if 6 in months else "winter closer",
            "seasons": "2 prior years" if prior_years <= 2 else "3+ prior years",
            "position": ("2nd pickup of season" if season_pickups == 1 else
                         "3rd-4th" if season_pickups <= 3 else "5th+"),
            "crosses": "interval crosses a month" if r["days"] and (
                clock.month != r["date"].month or clock.year != r["date"].year) else "same month",
            "stage_rate": r["rate"] * days,
            "rates": {k: (v * days if v is not None else None) for k, v in cand.items()},
        }
        if r0 is not None:
            row["base"] = r0 * days
            rows.append(row)
        else:
            fallback.append(row)
    return rows, fallback


# ─────────────────────────────────────────────
# Weekly snapshots: urgency, partially observed
# ─────────────────────────────────────────────

def snapshots(pickups, checks):
    """
    Every Monday 2018-2026, every customer the detector calls a closer for
    that year: the champion's section and projection, the candidates' in-
    season projections, and what happened next.
    """
    seasons = replay.SeasonCache()
    factors = {}
    out = []
    first = date(2018, 1, 1)
    first += timedelta(days=(7 - first.weekday()) % 7)
    last_day = max(p["date"] for ps in pickups.values() for p in ps)
    for cid, ps in pickups.items():
        dates = [p["date"] for p in ps]
        t = first
        while t <= last_day:
            if t.year == 2020:
                t += timedelta(days=7)
                continue
            k = sum(1 for d in dates if d < t)
            if k < 3:
                t += timedelta(days=7)
                continue
            h = ps[:k]
            closed = seasons.closed_months(cid, ps, t.year)
            if not closed or (bpt.MODEL_OVERRIDES.get(cid) and
                              bpt.MODEL_OVERRIDES[cid].status in replay.NO_RATE):
                t += timedelta(days=7)
                continue
            st = replay.champion_state(cid, h, t, checks.get(cid, ()), seasons, "core")
            nxt = next((p for p in ps[k:]), None)
            days_to_next = (nxt["date"] - t).days if nxt else None
            median_pick = statistics.median(p["gallons"] for p in h[-6:])
            spec = st["season_spec"]
            in_season = spec is not None and bpt.season_opened(spec, t) is not None
            rec = {
                "cid": cid, "date": t, "year": t.year, "section": st["section"],
                "in_season": in_season, "median_pick": median_pick,
                "days_to_next": days_to_next, "next_gal": nxt["gallons"] if nxt else None,
                "proj": {"G0": None},
            }
            dd = (t - st["clock_start"]).days
            if st["section"] == bpt.SECTION_RANKED and st["rate"] is not None:
                rec["proj"]["G0"] = st["rate"] * dd
                if in_season and st["stage"] == bpt.STAGE_CLOSER:
                    months = bpt.season_months(spec)
                    key = (cid, t.year)
                    if key not in factors:
                        s = seasonal_open.season_for_year(ps, t.year)
                        factors[key] = s["factor"] if s else None
                    opened = bpt.season_opened(spec, t)
                    r0 = st["rate"]
                    r4 = g4(h, months, opened)
                    for name, v in (("G2", g2(h, months)),
                                    ("G3", g3(h, factors[key], st["clock_start"], t)),
                                    ("G4", r4 if r4 is not None else r0),
                                    ("G5", (r0 + r4) / 2 if r4 is not None else r0)):
                        rec["proj"][name] = (v if v is not None else r0) * dd
            out.append(rec)
            t += timedelta(days=7)
    return out


def urgency_table(snaps):
    lines = ["| Snapshot set | Model | Customer-weeks ranked | Flagged (proj ≥ median pickup) | of which no pickup within 30 d | Pickup within 30 d but NOT flagged |",
             "|---|---|---:|---:|---:|---:|"]
    for label, keep in (("off-season, ranked (semi/detected closer called recently)",
                         lambda s: not s["in_season"]),
                        ("in season", lambda s: s["in_season"])):
        sub = [s for s in snaps if keep(s) and s["proj"]["G0"] is not None]
        for model in ("G0", "G2", "G3", "G4", "G5"):
            if model != "G0" and label.startswith("off"):
                continue
            vals = [(s, s["proj"].get(model, s["proj"]["G0"]) if s["proj"].get(model) is not None
                     else s["proj"]["G0"]) for s in sub]
            flagged = [s for s, p in vals if p >= s["median_pick"]]
            false = [s for s in flagged if s["days_to_next"] is None or s["days_to_next"] > 30]
            missed = [s for s, p in vals if p < s["median_pick"]
                      and s["days_to_next"] is not None and s["days_to_next"] <= 30]
            lines.append(f"| {label} | {model} | {len(vals):,} | {len(flagged):,} | "
                         f"{len(false):,} ({100 * len(false) / max(len(flagged), 1):.0f}%) | {len(missed):,} |")
    held = [s for s in snaps if not s["in_season"]]
    lines += ["", f"Off-season customer-weeks: {len(held):,}; held out by the champion: "
              f"{sum(1 for s in held if s['section'] != bpt.SECTION_RANKED):,}; ranked: "
              f"{sum(1 for s in held if s['section'] == bpt.SECTION_RANKED):,} "
              f"(of which with a pickup within 30 d: "
              f"{sum(1 for s in held if s['section'] == bpt.SECTION_RANKED and s['days_to_next'] is not None and s['days_to_next'] <= 30):,})."]
    return "\n".join(lines)


def main():
    pickups, checks, _, records = replay.load_or_build(mode="core")
    rows, fallback = closer_rows(records, pickups)
    results, yearly = [], {}
    for name in ("G2", "G3", "G4", "G5"):
        sub = [dict(r, cand=r["rates"][name] if r["rates"][name] is not None else r["base"]) for r in rows]
        res, yrs = harness.evaluate("G", name, "in-season closer (champion closer stage)", sub,
                                    note=f"no rate (fell back to G0) on {sum(1 for r in rows if r['rates'][name] is None)}")
        results += res
        yearly[name] = yrs
    harness.write_results(harness.HERE / "results_G.csv", results)

    lines = ["# Experiment G: closer open-day exposure (generated by `exp_closer.py`)", "",
             f"In-season closer pickups scored by the champion's open-season rate: {len(rows):,} "
             f"({len({r['cid'] for r in rows})} customers). In-season but ranked on the stage rate "
             f"because fewer than 3 wholly-open gaps exist (coverage fallback): {len(fallback):,}.", "",
             "## Pooled comparisons", "", harness.table(results), "",
             "## Δ WAPE by year", "", harness.year_table(yearly), ""]
    for name in ("G2", "G3", "G4", "G5"):
        sub = [dict(r, cand=r["rates"][name] if r["rates"][name] is not None else r["base"])
               for r in rows if r["year"] in replay.EVAL_YEARS]
        lines += [f"## Splits for {name}, all eval years", "",
                  harness.split_table(sub, lambda r: r["kind"], "Closer type"), "",
                  harness.split_table(sub, lambda r: r["position"], "Position in season"), "",
                  harness.split_table(sub, lambda r: r["seasons"], "History"), "",
                  harness.split_table(sub, lambda r: r["crosses"], "Forecast interval"), ""]
    # Coverage: rows where G0 has no rate, the champion's stage rate vs candidates.
    lines += ["## Coverage fallback: in season, fewer than 3 wholly-open gaps", "",
              "The champion ranks these on the stage rate (50/50 or new), which includes closed months. "
              "Candidates that can form a rate are compared to that stage rate on the rows where they can.", "",
              "| Candidate | Rows with a rate | Stage-rate WAPE | Candidate WAPE | Δ | 95% interval | Bias: stage → cand |",
              "|---|---:|---:|---:|---:|---|---|"]
    if not fallback:
        lines.append("| none | 0 | – | – | – | – | every in-season ranked closer row had ≥ 3 wholly-open gaps |")
    for name in ("G2", "G3"):
        sub = [dict(r, base=r["stage_rate"], cand=r["rates"][name]) for r in fallback
               if r["rates"][name] is not None]
        if sub:
            c = m.compare(sub, draws=1000)
            lines.append(f"| {name} | {c['pickups']:,} ({c['customers']} cust.) | {c['base_wape']:.1f}% | {c['cand_wape']:.1f}% "
                         f"| {c['d_wape']:+.2f} | {m.ci(c)} | {c['base_bias']:+.1f}% → {c['cand_bias']:+.1f}% |")
    snaps = snapshots(pickups, checks)
    lines += ["", "## False urgency proxy (weekly snapshots, detected closers, 2018-19 and 2021-26)", "",
              "Flagged = projected gallons at least the customer's median pickup over its last 6 "
              "(capacity history does not exist). A flagged week with no pickup in the next 30 days is "
              "*possible* false urgency: the truck may simply not have come. G0-G5 differ only in "
              "season; off-season rows are the champion's own.", "", urgency_table(snaps), ""]
    (harness.HERE / "results_G.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
