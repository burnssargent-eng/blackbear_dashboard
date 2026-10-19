#!/usr/bin/env python3
"""
Experiment C: can persistent production-level shifts be told from pickup
noise? Established-stage pickups of the October 15 champion replay (rate =
1/2 P + 1/2 Y, P = pooled last 6 gaps, Y = rate_prev_year_asof; no restart,
no seasonal or closer stage). Research only.

Declared before the first run (2026-10-17); never extended after looking:

C1  fixed recent weight: w x P + (1 - w) x Y, w in C1_WEIGHTS. Not tuned:
    every weight is reported.
C2  span reliability: w = 0.40 + 0.20 x clip((days_P - 60) / 180, 0, 1),
    bounded [0.4, 0.6]. One spec.
C3  persistent shift. Reference Y' = pooled rate of the gaps ending in the
    year BEFORE the last 365 days (no overlap with the blocks). The last 365
    days are cut into k equal time blocks; a gap belongs to the block its
    end falls in; each block needs >= 2 gaps and >= 45 days. A shift fires
    when every block's pooled rate is beyond Y' by more than T on the same
    side AND P - Y points the same way; then w = 0.65, else the 50/50.
    Grid k in {2, 3} x T in {0.15, 0.25}, chosen on dev.
C4  local level. L starts at Y' (or the first gap's rate) and steps through
    the gaps of the last 365 days: L <- L + K (g/d - L), K = d / (d + D):
    a long interval is a more precise observation. D in {90, 180, 365},
    chosen on dev; used alone and 1/2 / 1/2 with the champion.

    RESEARCH_CACHE=... python3 exp_level_shift.py
"""

import math
import random
import statistics
from datetime import timedelta

import champion as ch
import common as c
import harness
import metrics as m
import replay
from replay import bpt

HERE = c.HERE
C1_WEIGHTS = (0.55, 0.60, 0.65)
C3_GRID = [(k, T) for k in (2, 3) for T in (0.15, 0.25)]
C3_WEIGHT = 0.65
C4_D = (90, 180, 365)
BLOCK_MIN_GAPS = 2
BLOCK_MIN_DAYS = 45
DISAGREE = 0.25
SMALL_PICKUP = 0.6


def gaps_ending(h, lo, hi):
    """(gallons, days) of the gaps whose END date is in (lo, hi]."""
    out = []
    for a, b in zip(h, h[1:]):
        d = (b["date"] - a["date"]).days
        if d > 0 and lo < b["date"] <= hi:
            out.append((b["gallons"], d))
    return out


def pooled(gs):
    d = sum(x[1] for x in gs)
    return sum(x[0] for x in gs) / d if d else None


def reference_rate(h):
    last = h[-1]["date"]
    return pooled(gaps_ending(h, last - timedelta(days=730), last - timedelta(days=365)))


def shift(h, P, Y, k, T):
    """+1 / -1 when a persistent shift fires, else 0."""
    ref = reference_rate(h)
    if not ref:
        return 0
    last = h[-1]["date"]
    width = 365 / k
    sides = []
    for b in range(k):
        hi = last - timedelta(days=round(b * width))
        lo = last - timedelta(days=round((b + 1) * width))
        gs = gaps_ending(h, lo, hi)
        if len(gs) < BLOCK_MIN_GAPS or sum(x[1] for x in gs) < BLOCK_MIN_DAYS:
            return 0
        r = pooled(gs)
        sides.append(1 if r > ref * (1 + T) else -1 if r < ref / (1 + T) else 0)
    if len(set(sides)) == 1 and sides[0] != 0 and (P - Y) * sides[0] > 0:
        return sides[0]
    return 0


def local_level(h, D):
    last = h[-1]["date"]
    L = reference_rate(h)
    for g, d in gaps_ending(h, last - timedelta(days=365), last):
        if L is None:
            L = g / d
            continue
        L += d / (d + D) * (g / d - L)
    return L


def build(pickups, records):
    rows = []
    for r in records:
        if not (r["scored"] and r["stage"] == bpt.STAGE_ESTABLISHED and r["year"] in replay.REPLAY_YEARS):
            continue
        h = pickups[r["cid"]][:r["k"]]
        P, Y = r["pooled"], r["prev_year"]
        n = len(h)
        days_p = (h[-1]["date"] - h[max(0, n - 1 - bpt.WINDOW)]["date"]).days
        e = r["exposure"]
        cand = {f"C1 w={w}": (w * P + (1 - w) * Y) * e for w in C1_WEIGHTS}
        w2 = 0.40 + 0.20 * min(1.0, max(0.0, (days_p - 60) / 180))
        cand["C2 span reliability"] = (w2 * P + (1 - w2) * Y) * e
        fires = {}
        for k, T in C3_GRID:
            s = shift(h, P, Y, k, T)
            fires[(k, T)] = s
            w = C3_WEIGHT if s else 0.5
            cand[f"C3 k={k} T={T}"] = (w * P + (1 - w) * Y) * e
        for D in C4_D:
            L = local_level(h, D)
            if L is not None:
                cand[f"C4 D={D}"] = L * e
                cand[f"C4 D={D} half"] = 0.5 * (L * e + r["pred"])
        year_gal = sum(p["gallons"] for p in h[1:] if (r["date"] - p["date"]).days <= bpt.YEAR_DAYS)
        rows.append({"cid": r["cid"], "name": r["name"], "year": r["year"], "k": r["k"],
                     "actual": r["actual"], "base": r["pred"], "cand": cand, "fires": fires,
                     "P": P, "Y": Y, "days": e, "year_gal": year_gal})
    return rows


def h2h(rows, name):
    return [{**{k: v for k, v in r.items() if k != "cand"},
             "cand": r["cand"].get(name, r["base"])} for r in rows]


def slices_md(rows, vcut, fire_key):
    def sl(r):
        return [("disagreement", f"|P/Y − 1| > {DISAGREE}" if abs(r["P"] / r["Y"] - 1) > DISAGREE else "stable"),
                ("clock", "< 30 d" if r["days"] < 30 else "30–90 d" if r["days"] <= 90 else "> 90 d"),
                ("volume", "top quartile" if r["year_gal"] >= vcut else "rest"),
                ("C3 fired", f"fired ({fire_key})" if r["fires"].get(fire_key) else "not fired")]
    out = []
    for i, title in enumerate(("Recent vs year", "Clock", "Volume", "Shift detector")):
        out.append(harness.split_table(rows, lambda r, i=i: sl(r)[i][1], title))
        out.append("")
    return "\n".join(out)


def false_fires(pickups, rows, key):
    """
    For each C3 fire (diagnostic, uses the FUTURE): the realised pooled rate of
    the next 180 days against Y'. Persisted = still beyond Y' by more than T
    on the fired side; reverted = within T of Y' or past it the other way.
    """
    k, T = key
    persisted = reverted = unknown = 0
    for r in rows:
        s = r["fires"].get(key)
        if not s or r["year"] not in replay.EVAL_YEARS:
            continue
        ps = pickups[r["cid"]]
        h = ps[:r["k"]]
        ref = reference_rate(h)
        last = h[-1]["date"]
        future = pooled(gaps_ending(ps, last, last + timedelta(days=180)))
        if future is None or not ref:
            unknown += 1
        elif (future > ref * (1 + T)) if s > 0 else (future < ref / (1 + T)):
            persisted += 1
        else:
            reverted += 1
    return persisted, reverted, unknown


def noise_md(records, rows):
    """C step 3: do errors alternate (partial pickups) or persist (level shifts)?"""
    by = {(r["cid"], r["k"]): r for r in rows}
    pairs, cond, uncond = [], [], []
    for r in rows:
        prev = by.get((r["cid"], r["k"] - 1))
        if not prev or r["year"] not in replay.EVAL_YEARS or r["base"] <= 0 or prev["base"] <= 0:
            continue
        if r["actual"] <= 0 or prev["actual"] <= 0:
            continue
        e0, e1 = math.log(prev["actual"] / prev["base"]), math.log(r["actual"] / r["base"])
        pairs.append((r["cid"], e0, e1))
        uncond.append(e1 > 0)
        if prev["actual"] < SMALL_PICKUP * prev["base"]:
            cond.append(e1 > 0)
    rng = random.Random(m.SEED)
    clusters = {}
    for cid, a, b in pairs:
        clusters.setdefault(cid, []).append((a, b))
    keys = list(clusters)

    def corr(ps):
        xa = [a for a, _ in ps]
        xb = [b for _, b in ps]
        return statistics.correlation(xa, xb)
    est = corr([p for v in clusters.values() for p in v])
    boots = sorted(corr([p for cid in rng.choices(keys, k=len(keys)) for p in clusters[cid]]) for _ in range(500))
    # P(next positive | previous small), clustered interval
    lines = [f"- Consecutive established pickups, log(actual ÷ projected): lag-1 correlation **{est:+.2f}** "
             f"(95% customer-cluster interval {boots[12]:+.2f} to {boots[487]:+.2f}; {len(pairs):,} pairs, "
             f"{len(keys)} customers). Positive = errors persist; partial collections would make it negative.",
             f"- After a pickup that came in under {SMALL_PICKUP:.0%} of its projection, the next pickup beat its "
             f"projection {100 * sum(cond) / len(cond):.0f}% of the time ({len(cond):,} cases), against "
             f"{100 * sum(uncond) / len(uncond):.0f}% unconditionally."]
    return "\n".join(lines)


def main():
    pickups, _, _, records = ch.load_or_build()
    rows = build(pickups, records)
    ev = [r for r in rows if r["year"] in replay.EVAL_YEARS]
    vcut = statistics.quantiles([r["year_gal"] for r in ev], n=4)[2]
    results, yearly, impacts = [], {}, []
    names = sorted({n for r in rows for n in r["cand"]})
    for name in names:
        yearly[name] = c.evaluate(results, "C", name, "established", h2h(ev, name))

    picks = {}
    for fam, grid_names in (("C3", [f"C3 k={k} T={T}" for k, T in C3_GRID]),
                            ("C4 alone", [f"C4 D={D}" for D in C4_D]),
                            ("C4 half", [f"C4 D={D} half" for D in C4_D])):
        grid = {g: h2h(ev, g) for g in grid_names}
        picks[fam] = harness.choose(grid, replay.DEV_YEARS)
        rolled, rp = harness.rolling_choice(grid)
        c.evaluate(results, "C", f"{fam}, rolling choice", "established", rolled,
                   note="picks " + ", ".join(f"{y}:{v}" for y, v in rp.items()))
        oe, op = harness.odd_even_choice(grid)
        c.evaluate(results, "C", f"{fam}, odd->even choice", "established", oe,
                   note="picks " + ", ".join(f"{y}:{v}" for y, v in op.items()))

    capacities = bpt.load_capacities()
    for name in ("C1 w=0.6", picks["C4 alone"], picks["C4 half"]):
        c.evaluate(results, "C", name + " (capped, today's capacity: hindsight)", "established",
                   c.capped(h2h(ev, name), capacities))

    fire_name = picks["C3"]
    fire_key = next((k, T) for k, T in C3_GRID if f"C3 k={k} T={T}" == fire_name)
    fired = [r for r in ev if r["fires"][fire_key]]
    pers, rev, unk = false_fires(pickups, rows, fire_key)

    md = ["# Persistent production-level shifts (Experiment C)", "",
          "Generated by `exp_level_shift.py` on the October 15 champion replay: established-stage pickups only "
          "(the 50/50; no restart, seasonal or closer stage). Every candidate is scored on identical pickups. "
          "Grids were declared in the script header before the first run.", "",
          "**Earlier results this overlaps** (f204e6b replay, `model_research_2026_10/results_ABK.md`): A1 span weight "
          "+0.21 (worse), A2 inverse variance +0.01, A3 covariance-aware weights (pushed toward 1.0) +0.62, "
          "K decay τ 90 −0.16 (null). The two 50/50 halves overlap, because the previous-year window CONTAINS the last six "
          "gaps, so the effective recent weight is already above ½.", "",
          "## All candidates", "", harness.table(results), "",
          "## Per year (Δ WAPE points)", "", harness.year_table(yearly), "",
          "Dev choices: " + "; ".join(f"{k} → {v}" for k, v in picks.items()) + ".", "",
          f"## C3 ({fire_name}, dev-chosen): what it fires on", "",
          f"Fires on {len(fired):,} of {len(ev):,} established eval pickups "
          f"({len({r['cid'] for r in fired})} customers). Diagnostic only, it uses the future: over the next 180 days the shift "
          f"**persisted** in {pers}, **reverted** in {rev}, and could not be checked in {unk} (no gap ending inside 180 days).", "",
          harness.split_table(h2h(fired, fire_name), lambda r: "up" if r["fires"][fire_key] > 0 else "down",
                              "Direction"), "",
          c.top_contributors(h2h(fired, fire_name)), "",
          f"## Slices for C1 w=0.6, C3 ({fire_name}) and C4 ({picks['C4 half']})", ""]
    for name in ("C1 w=0.6", fire_name, picks["C4 half"]):
        md.append(f"### {name}\n")
        md.append(slices_md(h2h(ev, name), vcut, fire_key))
        for wname, years in (("dev 2021-23", replay.DEV_YEARS), ("confirm 2024-26", replay.CONFIRM_YEARS)):
            impacts.extend(harness.customer_impacts("C", name, wname, [r for r in h2h(ev, name) if r["year"] in years]))
    md.append("## Shift or inventory noise? (C step 3)\n")
    md.append(noise_md(records, rows))
    md.append("\nPickup data alone cannot show partial collections. These figures only say which way the errors "
              "move on average.")
    (HERE / "level_shift.md").write_text("\n".join(md) + "\n")
    harness.write_results(HERE / "results_C.csv", results)
    harness.write_impacts(HERE / "impacts_C.csv", impacts)
    print("wrote level_shift.md, results_C.csv, impacts_C.csv;", len(ev), "established eval pickups")


if __name__ == "__main__":
    main()
