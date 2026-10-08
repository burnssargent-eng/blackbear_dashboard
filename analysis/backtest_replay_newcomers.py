#!/usr/bin/env python3
"""
Phase 3: replay every newcomer from its first pickup.

The question: for a customer we have never seen, how good is each stage of
the projection ladder as pickups accumulate, when should it be promoted, and
what band makes the high side trustworthy?

The ladder (agreed with Sarge 2026-10-08, set before this was run):

  insufficient  1-2 pickups                 no projection
  new           3+ pickups                  pooled rate since the first pickup
                                            (at most the last 6 gaps)
  established   a previous-year rate exists 0.5 x pooled + 0.5 x previous year
                                            (today's 50/50 once 6 gaps exist)
  seasonal      passes the seasonal-open    season-free level x month index
                test (phase 2), prior years
  will-call     lumpy / infrequent          never projected

Rules:
  * the FIRST pickup is a starting point only -- its gallons never enter a
    rate (oil may predate the barrel; deliveries are logged only sometimes);
  * nothing on or after a pickup's date is used to predict it;
  * shared-barrel members are modelled on their own (not folded), new-owner
    history starts are applied, inactive customers stay in (no survivorship);
  * band multipliers and the will-call rule are chosen on pickups dated
    2021-23 (CHOOSE) and reported on 2024-26 (CONFIRM).

The high side is a band, never an inflated estimate: the central projection
stays unbiased, and "as full as" = central x the 80th percentile of
actual / projected in the choose period, per stage.

    python3 analysis/backtest_replay_newcomers.py

Writes analysis/replay_newcomers.md and backtest_replay_detail.csv
(gitignored). Standard library only; deterministic (fixed seed).
"""

import csv
import random
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import backtest_customer_factors as bcf
import backtest_phase2_rates as p2
import backtest_seasonal_models as bsm
import backtest_steady_rate as bt
import build_projection_table as bpt
import customer_factors as cf

HERE = Path(__file__).resolve().parent
OUT_REPORT = HERE / "replay_newcomers.md"
OUT_DETAIL = HERE / "backtest_replay_detail.csv"

YEAR = 365
FIRST_FROM = date(2021, 1, 1)       # a newcomer's first pickup is on or after this
CHOOSE = (2021, 2022, 2023)
CONFIRM = (2024, 2025, 2026)
NEW_MIN_PICKUPS = 3                 # Sarge's rule: 3 pickups = 2 measured gaps
POOLED_GAPS = 6
HIGH_Q = 0.80                       # the "could be as full as" percentile
TARGET_FILL = 0.75
SEED = 42

# Will-call candidates: a customer is flagged once it has 3+ pickups and its
# gaps so far are long or irregular. The grid is scored, one rule is chosen.
WILL_CALL_MEDIAN_GAP = (90, 120, 180)   # days
WILL_CALL_GAP_CV = (0.8, 1.0, 1.2)      # sd / mean of the gaps (needs 3+ gaps)
# A closer's one off-season gap a year makes sd / mean look irregular, so the
# robust version uses the middle half of the gaps: (Q3 - Q1) / median, 4+ gaps.
WILL_CALL_GAP_IQR = (0.6, 0.8, 1.0)
WILL_CALL_PERIODICITY = 180             # Jim's sign-up guess: 180/365 ~ will-call
WILL_CALL_STATUSES = {bpt.STATUS_SEMI_CLOSER, bpt.STATUS_EVENT,
                      bpt.STATUS_ON_DEMAND, bpt.STATUS_LUMP_SUM}

GAP_BUCKETS = [(1, 1, "1"), (2, 2, "2"), (3, 3, "3"), (4, 4, "4"), (5, 6, "5–6"),
               (7, 9, "7–9"), (10, 12, "10–12"), (13, 24, "13–24"), (25, 10**6, "25+")]


# ─────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────

def load():
    pickups = bt.load_pickups(ids=None)
    checks = bpt.load_empty_checks(date.max)
    bpt.apply_history_starts(pickups, checks)
    capacities = bpt.load_capacities()
    active = bpt.load_active_ids()
    periodicity = {}
    with open(bpt.CAPACITY_CACHE, newline="") as f:
        for row in csv.DictReader(f):
            try:
                periodicity[int(row["customer_id"])] = float(row["periodicity_days"])
            except (TypeError, ValueError):
                pass
    return pickups, checks, capacities, active, periodicity


def gap_features(ps):
    """Gap timing from pickups seen so far: (median, sd/mean, IQR/median).
    Gap lengths use the first pickup's DATE, never its gallons."""
    gaps = [(b["date"] - a["date"]).days for a, b in zip(ps, ps[1:])]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None, None, None
    med = statistics.median(gaps)
    cv = (statistics.pstdev(gaps) / statistics.fmean(gaps)) if len(gaps) >= 3 else None
    iqr = None
    if len(gaps) >= 4:
        q = statistics.quantiles(gaps, n=4)
        iqr = (q[2] - q[0]) / med
    return med, cv, iqr


RULES = ([("sd/mean", g, c) for g in WILL_CALL_MEDIAN_GAP for c in WILL_CALL_GAP_CV]
         + [("IQR/median", g, c) for g in WILL_CALL_MEDIAN_GAP for c in WILL_CALL_GAP_IQR])


def will_call(features, rule):
    med, cv, iqr = features
    kind, g_max, c_max = rule
    if med is None:
        return False
    spread = cv if kind == "sd/mean" else iqr
    return med > g_max or (spread is not None and spread > c_max)


def rule_label(rule):
    kind, g_max, c_max = rule
    return f"gap > {g_max} d or {kind} > {c_max}"


def bucket(gaps):
    return next(label for lo, hi, label in GAP_BUCKETS if lo <= gaps <= hi)


# ─────────────────────────────────────────────
# Replay
# ─────────────────────────────────────────────

def run():
    pickups, checks, capacities, active, periodicity = load()
    rng = random.Random(SEED)
    newcomers = {cid: ps for cid, ps in pickups.items()
                 if ps and ps[0]["date"] >= FIRST_FROM}
    seasons, groups = {}, {}
    tests, stale = [], []

    for cid in sorted(newcomers):
        ps = newcomers[cid]
        for k in range(1, len(ps)):
            x = ps[k]
            if x["gallons"] <= 0:
                continue
            start = p2.clock_start(ps, k, checks.get(cid, []))
            days = (x["date"] - start).days
            if days <= 0:
                continue
            if days > bpt.STALE_DAYS:       # production holds these out of the ranking
                stale.append(cid)
                continue
            gaps = k - 1                                  # measured gaps before x
            pooled = bsm.rate_span(ps, max(0, k - 1 - POOLED_GAPS), k - 1) if gaps else None
            prev_year, _ = bt.rate_prev_year(ps, k, YEAR)
            est = p2.blend(pooled, 0.5, prev_year)

            year = x["date"].year
            seasonal = None
            if prev_year is not None:
                if (cid, year) not in groups:
                    season = p2.season_for_year(ps, year)
                    groups[(cid, year)], _ = p2.classify(season, rng)
                    seasons[(cid, year)] = season
                if groups[(cid, year)] == "seasonal-open":
                    _, _, level, _ = bcf.base_rates(ps, k)
                    if level is not None:
                        seasonal = level * cf.gap_weighted(
                            seasons[(cid, year)]["factor"], start, x["date"])

            if k < NEW_MIN_PICKUPS:
                stage, ladder = "insufficient", None
            elif seasonal is not None:
                stage, ladder = "seasonal", seasonal
            elif est is not None:
                stage, ladder = "established", est
            else:
                stage, ladder = "new", pooled

            features = gap_features(ps[:k])
            tests.append({
                "customer_id": cid, "name": x["name"], "date": x["date"],
                "period": "choose" if year in CHOOSE else "confirm",
                "first": ps[0]["date"], "k": k, "gaps": gaps, "days": days,
                "actual": x["gallons"], "stage": stage,
                "capacity": capacities.get(cid), "active": cid in active,
                "periodicity": periodicity.get(cid),
                "status": bpt.MODEL_OVERRIDES[cid].status if cid in bpt.MODEL_OVERRIDES else None,
                "features": features,
                "rates": {"ladder": ladder, "pooled": pooled, "established": est,
                          "seasonal": seasonal},
            })
    return tests, newcomers, pickups, active, periodicity, stale


# ─────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────

def score(rows, model):
    """WAPE, bias and median |days to 75%| error for rows where `model` exists."""
    rows = [t for t in rows if t["rates"].get(model)]
    if not rows:
        return None
    vol = sum(t["actual"] for t in rows)
    err = [t["rates"][model] * t["days"] - t["actual"] for t in rows]
    day_err = []
    for t in rows:
        cap = t["capacity"]
        if cap:
            actual_rate = t["actual"] / t["days"]
            day_err.append(abs(TARGET_FILL * cap / t["rates"][model]
                               - TARGET_FILL * cap / actual_rate))
    return {"n": len(rows), "customers": len({t["customer_id"] for t in rows}),
            "wape": sum(abs(e) for e in err) / vol * 100,
            "bias": sum(err) / vol * 100,
            "days": statistics.median(day_err) if day_err else None}


def high_multiplier(rows, model):
    ratios = sorted(t["actual"] / (t["rates"][model] * t["days"])
                    for t in rows if t["rates"].get(model))
    if len(ratios) < 20:
        return None
    return ratios[min(len(ratios) - 1, int(HIGH_Q * len(ratios)))]


def coverage(rows, model, mult):
    rows = [t for t in rows if t["rates"].get(model)]
    if not rows or mult is None:
        return None
    return sum(t["actual"] <= t["rates"][model] * t["days"] * mult for t in rows) / len(rows) * 100


def band_key(t):
    if t["stage"] == "new":
        return "new, " + ("2 gaps" if t["gaps"] == 2 else "3 gaps" if t["gaps"] == 3
                          else "4–6 gaps")
    return t["stage"]


def pct(v, signed=False):
    if v is None:
        return "–"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def num(v):
    return "–" if v is None else f"{v:.0f}"


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def main():
    tests, newcomers, pickups, active, periodicity, stale = run()
    choose = [t for t in tests if t["period"] == "choose"]
    confirm = [t for t in tests if t["period"] == "confirm"]
    out = []
    w = out.append

    w("# Phase 3: replaying newcomers from their first pickup\n")
    w("Generated by `analysis/backtest_replay_newcomers.py`. Every customer whose "
      f"first pickup is on or after {FIRST_FROM} is replayed pickup by pickup, using "
      "only what was known before each pickup. The **first pickup is a starting "
      "point only** — its gallons never enter a rate. Shared-barrel members are "
      "modelled on their own; customers who later left are included. **Choose** = "
      "pickups dated 2021–23 (band widths and the will-call rule are set here); "
      "**confirm** = 2024–26.\n")
    w("Error measures: **WAPE** on gallons at pickup (\"how full is it now\"); "
      "**bias** (negative = under-projects, the costly direction); **days** = "
      "median absolute error in the date the barrel reaches 75% of capacity "
      "(\"when will it hit 75%\"), where the capacity is known.\n")

    n_new = len(newcomers)
    n_scored = len({t["customer_id"] for t in tests})
    w(f"{n_new} newcomers; {n_scored} have at least one scorable pickup after the "
      f"first; {sum(cid in active for cid in newcomers)} are active today. "
      f"{len(stale)} pickups that came more than {bpt.STALE_DAYS} days after the "
      "previous one are left out, as production holds such customers out of the "
      "ranking (a closer's off-season, a sporadic bulk account).\n")

    # 1. Learning curve -------------------------------------------------
    w("## 1. Learning curve: the pooled rate by measured gaps\n")
    w("The **new** stage's rate (pooled gallons ÷ days over at most the last 6 "
      "gaps, first pickup excluded), scored at every point in a newcomer's life. "
      "\"Gaps\" counts measured intervals before the predicted pickup: 2 gaps = "
      "the 3-pickup rule.\n")
    w("| Gaps | Choose: pickups | WAPE | Bias | Days | Confirm: pickups | WAPE | Bias | Days |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for lo, hi, label in GAP_BUCKETS:
        a = score([t for t in choose if lo <= t["gaps"] <= hi], "pooled")
        b = score([t for t in confirm if lo <= t["gaps"] <= hi], "pooled")
        cells = []
        for r in (a, b):
            cells += (["–"] * 4 if r is None else
                      [f"{r['n']:,}", pct(r["wape"]), pct(r["bias"], True), num(r["days"])])
        w(f"| {label} | " + " | ".join(cells) + " |")
    w("")

    # 2. The ladder ----------------------------------------------------
    w("## 2. The ladder, stage by stage\n")
    w("Each pickup is scored with the rate its stage would show. Will-call is not "
      "applied here (see section 4).\n")
    w("| Stage | Choose: pickups / customers | WAPE | Bias | Days | Confirm: pickups / customers | WAPE | Bias | Days |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for stage in ("new", "established", "seasonal"):
        cells = []
        for rows in (choose, confirm):
            r = score([t for t in rows if t["stage"] == stage], "ladder")
            cells += (["–"] * 4 if r is None else
                      [f"{r['n']:,} / {r['customers']}", pct(r["wape"]),
                       pct(r["bias"], True), num(r["days"])])
        w(f"| {stage} | " + " | ".join(cells) + " |")
    w("")

    w("**Promotion to 50/50 at one year — does the previous-year half help a "
      "newcomer?** Same pickups (established stage), pooled-only vs the 50/50:\n")
    w("| Period | Pickups | Pooled only | 50/50 | Difference | 95% interval |")
    w("|---|---:|---:|---:|---:|---|")
    rng = random.Random(SEED)
    for label, rows in (("choose", choose), ("confirm", confirm)):
        est_rows = [dict(t, rates={"a": t["rates"]["established"], "b": t["rates"]["pooled"]})
                    for t in rows if t["stage"] == "established" and t["rates"]["pooled"]]
        r = p2.compare([dict(t, actual=t["actual"]) for t in est_rows], "a", base="b", rng=rng)
        if r:
            w(f"| {label} | {r['n']:,} | {r['base']:.1f}% | {r['wape']:.1f}% | "
              f"**{r['diff']:+.1f}** | {r['lo']:+.1f} to {r['hi']:+.1f} |")
    w("")

    # 3. Bands ---------------------------------------------------------
    w(f"## 3. The high side: an {HIGH_Q:.0%} band per stage\n")
    w(f"Multiplier = the {HIGH_Q:.0%} point of actual ÷ projected in the choose "
      "period. \"As full as\" = projection × multiplier; \"hits 75% as early as\" "
      "= the date that inflated rate reaches 75%. Coverage = share of pickups "
      f"that came in at or under the high side (target {HIGH_Q:.0%}).\n")
    w("| Stage | Multiplier | Choose coverage | Confirm coverage | Confirm pickups |")
    w("|---|---:|---:|---:|---:|")
    keys = ["new, 2 gaps", "new, 3 gaps", "new, 4–6 gaps", "established", "seasonal"]
    for key in keys:
        c_rows = [t for t in choose if band_key(t) == key]
        f_rows = [t for t in confirm if band_key(t) == key]
        mult = high_multiplier(c_rows, "ladder")
        w(f"| {key} | {'–' if mult is None else f'× {mult:.2f}'} | "
          f"{pct(coverage(c_rows, 'ladder', mult))} | "
          f"{pct(coverage(f_rows, 'ladder', mult))} | {len(f_rows):,} |")
    w("")

    # 4. Will-call -----------------------------------------------------
    w("## 4. Will-call detector\n")
    w("A customer is flagged once it has 3+ pickups and its gaps so far are long "
      "(median gap over G days) or irregular (gap variability over C, needing 3+ "
      "gaps). Checked against two labels the detector never reads: Jim's sign-up "
      f"periodicity ≥ {WILL_CALL_PERIODICITY} days, and the call-driven / event / "
      "on-demand / lump-sum overrides. Scored on **every** customer with 3+ "
      "pickups in the last 3 years (their gaps since 2023-10), not just newcomers.\n")
    recent_from = date(2023, 10, 1)
    profiles = {}
    for cid, ps in pickups.items():
        recent = [p for p in ps if p["date"] >= recent_from]
        if len(recent) >= 3:
            profiles[cid] = gap_features(recent)
    labels = {cid for cid in profiles
              if (periodicity.get(cid) or 0) >= WILL_CALL_PERIODICITY
              or (cid in bpt.MODEL_OVERRIDES
                  and bpt.MODEL_OVERRIDES[cid].status in WILL_CALL_STATUSES)}
    w(f"{len(profiles)} customers scored; {len(labels)} carry a will-call label.\n")
    w("| Rule | Flagged | Agree with a label | Labels caught | Choose: flagged pickups | WAPE flagged | WAPE kept |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    best = None
    for rule in RULES:
        flagged = {cid for cid, f in profiles.items() if will_call(f, rule)}
        hit = len(flagged & labels)
        precision = hit / len(flagged) if flagged else 0
        recall = hit / len(labels) if labels else 0
        f1 = 2 * precision * recall / (precision + recall) if hit else 0
        fl = [t for t in choose if t["stage"] != "insufficient"
              and will_call(t["features"], rule)]
        fl_ids = {id(t) for t in fl}
        kept = [t for t in choose if t["stage"] != "insufficient" and id(t) not in fl_ids]
        a, b = score(fl, "ladder"), score(kept, "ladder")
        w(f"| {rule_label(rule)} | {len(flagged)} | {precision:.0%} | {recall:.0%} | "
          f"{len(fl):,} | {pct(a and a['wape'])} | {pct(b and b['wape'])} |")
        if best is None or f1 > best[0]:
            best = (f1, rule, flagged)
    w("")
    f1, chosen, flagged = best
    w(f"**Best agreement with the labels (F1 {f1:.2f}): {rule_label(chosen)}.** "
      "Disagreements, for review:\n")
    names = {cid: ps[-1]["name"] for cid, ps in pickups.items() if ps}

    def listing(ids):
        return ", ".join(f"{names.get(c, c)} ({c})" for c in sorted(ids)) or "none"
    w(f"- Flagged, no label ({len(flagged - labels)}): {listing(flagged - labels)}")
    w(f"- Labelled, not flagged ({len(labels - flagged)}): {listing(labels - flagged)}\n")

    # 5. Timing --------------------------------------------------------
    w("## 5. How long newcomers take to move up\n")
    reach = defaultdict(list)
    never = Counter()
    for cid, ps in newcomers.items():
        rows = [t for t in tests if t["customer_id"] == cid]
        for stage in ("new", "established", "seasonal"):
            hit = next((t for t in rows if t["stage"] == stage), None)
            if hit:
                reach[stage].append((hit["date"] - ps[0]["date"]).days)
            else:
                never[stage] += 1
    w("| Stage | Newcomers reaching it | Median days from first pickup | 75th percentile |")
    w("|---|---:|---:|---:|")
    for stage in ("new", "established", "seasonal"):
        d = sorted(reach[stage])
        if d:
            w(f"| {stage} | {len(d)} of {len(newcomers)} | {statistics.median(d):.0f} | "
              f"{d[int(0.75 * (len(d) - 1))]:.0f} |")
        else:
            w(f"| {stage} | 0 of {len(newcomers)} | – | – |")
    w("")

    w("## 6. The ladder with will-call applied\n")
    w(f"Pickups the chosen rule ({rule_label(chosen)}) would have flagged at the "
      "time get no projection, as on the page. Everything else as section 2.\n")
    w("| Stage | Choose: pickups / customers | WAPE | Bias | Days | Confirm: pickups / customers | WAPE | Bias | Days |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for stage in ("new", "established", "seasonal", "will-call (not shown)"):
        cells = []
        for rows in (choose, confirm):
            if stage.startswith("will-call"):
                sel = [t for t in rows if t["stage"] != "insufficient"
                       and will_call(t["features"], chosen)]
            else:
                sel = [t for t in rows if t["stage"] == stage
                       and not will_call(t["features"], chosen)]
            r = score(sel, "ladder")
            cells += (["–"] * 4 if r is None else
                      [f"{r['n']:,} / {r['customers']}", pct(r["wape"]),
                       pct(r["bias"], True), num(r["days"])])
        w(f"| {stage} | " + " | ".join(cells) + " |")
    w("")

    OUT_REPORT.write_text("\n".join(out) + "\n")
    with open(OUT_DETAIL, "w", newline="") as f:
        cols = ["customer_id", "name", "date", "period", "k", "gaps", "days", "actual",
                "stage", "capacity", "med_gap", "gap_cv", "gap_iqr", "ladder", "pooled",
                "established", "seasonal"]
        wr = csv.writer(f)
        wr.writerow(cols)
        for t in tests:
            wr.writerow([t["customer_id"], t["name"], t["date"], t["period"], t["k"],
                         t["gaps"], t["days"], t["actual"], t["stage"], t["capacity"],
                         *t["features"]] +
                        [None if t["rates"][m] is None else round(t["rates"][m], 4)
                         for m in ("ladder", "pooled", "established", "seasonal")])
    print(f"wrote {OUT_REPORT.name} ({len(tests):,} pickups)")


if __name__ == "__main__":
    main()
