#!/usr/bin/env python3
"""
The takeover dry run's report (Sarge, 2026-11-15): would the dashboard and
projections come out the same if they were built from the site's pickups
instead of the source site's? .github/workflows/takeover-dry-run.yml builds
both after each nightly, in two copies of the repo, and runs:

    python3 takeover_check.py JAMAAL_DIR SITE_DIR --since 2026-10-26

  JAMAAL_DIR  built from the scrape, as the nightly does
  SITE_DIR    the same, after supabase_sync.py --export-collections --since

It compares, for every day from --since through the scrape's latest day:
  pickups   only in Jamaal, only on the site, different gallons, and a
            pickup whose day differs by one (entered on the wrong day)
  codes     1 (empty), 2 (sign-up call) and 3 (barrel delivery) the same
            way; 0s are left out, since the source site writes them itself
            (CLAUDE.md, RESET_QTYS)
  days      entries per day on each side, so a day the drivers skipped on
            the site shows, not only days with some entries
  output    customers whose projection differs (oil_projections.json)

Days are compared through --through, by default yesterday in Vermont: the
nightly scrapes in the small hours (or hours late), so today is never complete
on either side. Site entries after it are listed as not yet in Jamaal, never
as differences. The report is markdown, printed and, under GitHub
Actions, written to the run's summary page. Customer names and gallons are
already public (oil_projections.json, oil_collections_raw.csv): nothing
private is read. Exit 0 whatever it finds; the workflow fails only when the
export or a build breaks.
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CODES = {1: "empty", 2: "sign-up call", 3: "barrel delivery"}   # 0 left out: the source site writes it


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def entries(raw_rows, code_rows, since):
    """{(customer_id, day): [gallons, ...]} for pickups, and the same for codes 1-3, from `since` on."""
    pickups, codes = {}, {}
    for r in raw_rows:
        if r["date"][:10] >= since:
            pickups.setdefault((int(r["customer_id"]), r["date"][:10]), []).append(int(r["gallons"]))
    for r in code_rows:
        if r["date"] >= since and int(r["qty"]) in CODES:
            codes.setdefault((int(r["customer_id"]), r["date"]), []).append(int(r["qty"]))
    return ({k: sorted(v) for k, v in pickups.items()}, {k: sorted(v) for k, v in codes.items()})


def compare(jamaal, site, through):
    """
    jamaal, site: {(customer_id, day): [values]}. Returns differences through
    `through`, and site entries after it (not yet in Jamaal).
    """
    def shifted(key, table, values):
        cid, day = key
        d = date.fromisoformat(day)
        for delta in (-1, 1):
            k = (cid, (d + timedelta(days=delta)).isoformat())
            if table.get(k) == values:
                return k
        return None

    out = {"only_jamaal": [], "only_site": [], "differ": [], "day_off": [], "not_yet": []}
    moved = set()
    for key in sorted(set(jamaal) | set(site), key=lambda k: (k[1], k[0])):
        j, s = jamaal.get(key), site.get(key)
        if key[1] > through:
            if s:
                out["not_yet"].append((key, s))
            continue
        if j == s or key in moved:
            continue
        if j and s:
            out["differ"].append((key, j, s))
        elif j:
            k = shifted(key, site, j)
            if k and k not in jamaal:
                out["day_off"].append((key, k[1], j))
                moved.add(k)
            else:
                out["only_jamaal"].append((key, j))
        else:
            k = shifted(key, jamaal, s)
            if k and k not in site:
                out["day_off"].append((k, key[1], s))
                moved.add(k)
            else:
                out["only_site"].append((key, s))
    return out


def per_day(jamaal, site, since, through):
    """[(day, jamaal entries, site entries)] for every day from since through `through` with any entry."""
    jc = Counter(d for (_, d), v in jamaal.items() for _ in v)
    sc = Counter(d for (_, d), v in site.items() for _ in v)
    days = sorted(d for d in set(jc) | set(sc) if since <= d <= through)
    return [(d, jc[d], sc[d]) for d in days]


def projection_changes(jamaal_rows, site_rows):
    """[(id, name, jamaal projected_gal, site projected_gal)] where they differ, biggest change first."""
    j = {r["id"]: r for r in jamaal_rows}
    s = {r["id"]: r for r in site_rows}
    out = []
    for cid in set(j) | set(s):
        a, b = (j.get(cid) or {}).get("projected_gal"), (s.get(cid) or {}).get("projected_gal")
        if a != b:
            out.append((cid, (j.get(cid) or s.get(cid))["name"], a, b))
    return sorted(out, key=lambda r: -abs((r[2] or 0) - (r[3] or 0)))


def report(jamaal_dir, site_dir, since, through):
    names = {}
    proj = {}
    for side, d in (("jamaal", jamaal_dir), ("site", site_dir)):
        with open(os.path.join(d, "oil_projections.json")) as fh:
            proj[side] = json.load(fh)["customers"]
        names.update({r["id"]: r["name"] for r in proj[side]})
    jraw = read_csv(os.path.join(jamaal_dir, "oil_collections_raw.csv"))
    jp, jc = entries(jraw, read_csv(os.path.join(jamaal_dir, "oil_non_pickups.csv")), since)
    sp, sc = entries(read_csv(os.path.join(site_dir, "oil_collections_raw.csv")),
                     read_csv(os.path.join(site_dir, "oil_non_pickups.csv")), since)
    pick, code = compare(jp, sp, through), compare(jc, sc, through)
    changes = projection_changes(proj["jamaal"], proj["site"])
    differences = sum(len(v) for k, v in list(pick.items()) + list(code.items()) if k != "not_yet")

    who = lambda cid: f"{names[cid]} ({cid})" if cid in names else f"#{cid}"
    gal = lambda v: " + ".join(str(x) for x in v)
    word = lambda v: ", ".join(f"{x} {CODES[x]}" for x in v)
    lines = [f"# Takeover dry run: {'MATCH' if not differences else f'{differences} difference(s)'}",
             "",
             f"Site pickups from **{since}** through **{through}** (yesterday: today is never complete), "
             "against the source site's.",
             ""]
    if not differences:
        lines += ["Every pickup and code matches. Built from the site, the dashboard and projections "
                  "come out the same." if not changes else
                  f"Every pickup and code matches. The {len(changes)} projection difference(s) below come from "
                  f"entries made after {through} (today is compared tomorrow)."]
    lines += ["", "| | Pickups | Codes 1-3 |", "|---|---|---|"]
    for k, label in (("only_jamaal", "Only in Jamaal (missing on the site)"), ("only_site", "Only on the site"),
                     ("differ", "Different gallons / code"), ("day_off", "Entered a day off")):
        lines.append(f"| {label} | {len(pick[k])} | {len(code[k])} |")
    lines.append(f"| Site entries after {through} (not yet in Jamaal) | {len(pick['not_yet'])} | {len(code['not_yet'])} |")

    def section(title, rows):
        if rows:
            lines.extend(["", f"## {title}", ""] + rows)

    section("Missing on the site", [f"- {d}: {who(c)}, {gal(v)} gal" for (c, d), v in pick["only_jamaal"]]
            + [f"- {d}: {who(c)}, {word(v)}" for (c, d), v in code["only_jamaal"]])
    section("Only on the site", [f"- {d}: {who(c)}, {gal(v)} gal" for (c, d), v in pick["only_site"]]
            + [f"- {d}: {who(c)}, {word(v)}" for (c, d), v in code["only_site"]])
    section("Different gallons or code", [f"- {d}: {who(c)}, Jamaal {gal(j)}, site {gal(s)}"
                                          for (c, d), j, s in pick["differ"]]
            + [f"- {d}: {who(c)}, Jamaal {word(j)}, site {word(s)}" for (c, d), j, s in code["differ"]])
    section("Entered a day off", [f"- {who(c)}: Jamaal {d}, site {s} ({gal(v) if v[0] > 3 else word(v)})"
                                  for (c, d), s, v in pick["day_off"] + code["day_off"]])
    # Pickups and codes together, per day (a code is keyed apart so it never meets a pickup).
    jall, sall = dict(jp), dict(sp)
    for k, v in jc.items():
        jall[(("code", k[0]), k[1])] = v
    for k, v in sc.items():
        sall[(("code", k[0]), k[1])] = v
    rows = per_day(jall, sall, since, through)
    lines += ["", "## Entries per day", "", "| Day | Jamaal | Site | |", "|---|---|---|---|"]
    lines += [f"| {d} | {j} | {s} | {'' if j == s else ('**none on the site**' if not s else 'differs')} |"
              for d, j, s in rows]
    lines += ["", f"## Projections: {len(changes)} customer(s) differ", ""]
    lines += [f"- {who(c)}: Jamaal {a}, site {b}" for c, _, a, b in changes[:25]]
    if len(changes) > 25:
        lines.append(f"- … and {len(changes) - 25} more")
    return "\n".join(lines) + "\n", differences


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("jamaal_dir")
    ap.add_argument("site_dir")
    ap.add_argument("--since", required=True)
    ap.add_argument("--through", help="last day compared (default: yesterday in Vermont)")
    args = ap.parse_args()
    through = args.through or (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()
    text, _ = report(args.jamaal_dir, args.site_dir, args.since, through)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
