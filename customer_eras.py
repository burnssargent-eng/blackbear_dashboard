"""
Customer eras: one business's time at one site (a source-site account).

A Jamaal account is a barrel at a place, not a business: when a restaurant
closes and the next tenant keeps the barrel, both run on the same account
(Blue Stone, then Mad Moose, on #208). Retention is counted in BUSINESSES, so
the account's pickups are split into eras by `customer_eras.csv` (committed,
reviewed by Sarge; names, dates, reasons and public links only, never an
address).

The registry, one row per era:
  customer_id, era   era 1, 2, 3... per account, in order
  business           the business's name in that era
  end                last day of the era (inclusive). Required on every era but
                     an account's last; the next era starts the day after.
                     Blank on the last era = through its last pickup.
  end_reason         why the era ended:
      lost           the business still runs and stopped using us (true churn)
      closed         the business closed and no customer of ours took the site
      changed_hands  a successor took the site and became our customer
      moved          the business moved; `next` is its new era (blank = we
                     did not follow it)
      continued      the same customer carries on in `next` (a duplicate
                     account, a partner leaving a shared barrel). Blank next =
                     this account's next era.
      non_recurring  an event or one-off: left out of every count
  next               "customer_id/era". For changed_hands, the site's successor
                     on ANOTHER account (blank = this account's next era); for
                     moved/continued, where the business went.

An account with no rows is one era under its source-site name. An era that
ended with no reason (the account is inactive on the source site, or active
but dormant since DORMANT_CUTOFF) is `unknown`: never counted as lost, shown
as unclassified until someone decides.

A business is the chain of eras linked by moved/continued (Chico's in
Middlesex then Montpelier; The Warehouse on two accounts). It is gained in the
year of its first pickup and ends in the year of its last.

Bad rows never stop the nightly: they are skipped and listed in `problems`,
which validate_data.py fails on.
"""

import csv
import os
from datetime import date, timedelta
from statistics import median

ERAS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "customer_eras.csv")

END_REASONS = ("lost", "closed", "changed_hands", "moved", "continued", "non_recurring")
# Linked to the next era of the same business (not a business ending).
BUSINESS_LINKS = ("moved", "continued")
# What a business's ending counts as, in the retention table.
ENDINGS = ("lost", "closed", "changed_hands", "moved", "unknown")


def _day(text):
    return date.fromisoformat(text) if text else None


def load_registry(path=ERAS_FILE):
    """(customer_id -> [row, ...] in era order, problems). {} without the file."""
    problems = []
    by_customer = {}
    try:
        fh = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return {}, problems
    with fh:
        for line, raw in enumerate(csv.DictReader(fh), start=2):
            r = {k: (v or "").strip() for k, v in raw.items() if k}
            where = f"{os.path.basename(path)} line {line}"
            try:
                cid, era = int(r["customer_id"]), int(r["era"])
                end = _day(r.get("end"))
            except (KeyError, ValueError) as err:
                problems.append(f"{where}: unreadable customer_id/era/end ({err})")
                continue
            reason = r.get("end_reason", "")
            if reason and reason not in END_REASONS:
                problems.append(f"{where}: end_reason {reason!r} is not one of {', '.join(END_REASONS)}")
                continue
            nxt = r.get("next", "")
            if nxt:
                try:
                    a, b = nxt.split("/")
                    nxt = (int(a), int(b))
                except ValueError:
                    problems.append(f"{where}: next {nxt!r} is not customer_id/era")
                    continue
                if reason not in ("changed_hands", "moved", "continued"):
                    problems.append(f"{where}: next is only read for changed_hands, moved or continued")
            by_customer.setdefault(cid, []).append({
                "customer_id": cid, "era": era, "business": r.get("business", ""), "end": end,
                "end_reason": reason or None, "next": nxt or None,
                "note": r.get("note", ""), "source": r.get("source", ""), "_where": where,
            })

    for cid, rows in list(by_customer.items()):
        rows.sort(key=lambda x: x["era"])
        if [x["era"] for x in rows] != list(range(1, len(rows) + 1)):
            problems.append(f"#{cid}: eras must be numbered 1..{len(rows)} (got {[x['era'] for x in rows]}); skipped")
            del by_customer[cid]
            continue
        bad = False
        for prev, cur in zip(rows, rows[1:]):
            if not prev["end"] or not prev["end_reason"]:
                problems.append(f"{prev['_where']}: #{cid} era {prev['era']} needs an end and an end_reason "
                                "(it is not the account's last era)")
                bad = True
            elif cur["end"] and cur["end"] <= prev["end"]:
                problems.append(f"{cur['_where']}: #{cid} era {cur['era']} ends before era {prev['era']}")
                bad = True
        if rows[-1]["end"] and not rows[-1]["end_reason"]:
            problems.append(f"{rows[-1]['_where']}: #{cid} last era has an end but no end_reason")
            bad = True
        if bad:
            del by_customer[cid]
    return by_customer, problems


def build_eras(customers, records, registry, problems=None):
    """
    Split each account's pickups into eras.

    customers: the lifecycle roster (customer_id, name, town, is_active,
    lost_reason). records: (customer_id, "YYYY-MM-DD", gallons) for every
    qualifying pickup. Returns era dicts in customer, era order.
    """
    problems = problems if problems is not None else []
    pickups = {}
    for cid, day, gal in records:
        pickups.setdefault(int(cid), []).append((str(day)[:10], int(gal)))
    roster = {int(c["customer_id"]): c for c in customers}

    for cid in registry:
        if cid not in roster:
            problems.append(f"#{cid} is in {os.path.basename(ERAS_FILE)} but has no qualifying pickup")

    eras = []
    for cid in sorted(roster):
        c = roster[cid]
        rows = registry.get(cid) or [{"customer_id": cid, "era": 1, "business": "", "end": None,
                                      "end_reason": None, "next": None, "note": "", "source": ""}]
        days = sorted(pickups.get(cid, []))
        start = None
        account_ended = c.get("lost_reason") is not None
        for i, row in enumerate(rows):
            last_era = i == len(rows) - 1
            end = row["end"]
            mine = [(d, g) for d, g in days
                    if (start is None or d >= start.isoformat()) and (end is None or d <= end.isoformat())]
            if last_era and end:
                after = [d for d, _ in days if d > end.isoformat()]
                if after:
                    problems.append(f"#{cid}: {len(after)} pickup(s) after its last era's end {end} "
                                    f"(first {after[0]})")
            reason = row["end_reason"]
            ended = not last_era or bool(reason) or account_ended
            eras.append({
                "key": f"{cid}/{row['era']}",
                "customer_id": cid,
                "era": row["era"],
                "eras_on_account": len(rows),
                "business": row["business"] or c.get("name") or f"#{cid}",
                "account_name": c.get("name") or "",
                "town": c.get("town") or "",
                "window_start": start.isoformat() if start else None,
                "window_end": end.isoformat() if end else None,
                "first_pickup": mine[0][0] if mine else None,
                "last_pickup": mine[-1][0] if mine else None,
                "pickups": len(mine),
                "gallons": sum(g for _, g in mine),
                "ended": ended,
                "end_reason": (reason or "unknown") if ended else None,
                "next": f"{row['next'][0]}/{row['next'][1]}" if row["next"] else None,
                "note": row["note"],
                "source": row["source"],
                "registered": cid in registry,
            })
            start = end + timedelta(days=1) if end else None

    by_key = {e["key"]: e for e in eras}
    for e in eras:
        if e["next"] and e["next"] not in by_key:
            problems.append(f"#{e['customer_id']} era {e['era']}: next {e['next']} is not an era")
            e["next"] = None
        same_next = f"{e['customer_id']}/{e['era'] + 1}" if e["era"] < e["eras_on_account"] else None
        reason = e["end_reason"]
        # Where the business went, if it carried on.
        if reason in BUSINESS_LINKS:
            e["business_next"] = e["next"] or (same_next if reason == "continued" else None)
        else:
            e["business_next"] = None
        # Who took the site after this era, if one of ours did.
        if reason == "changed_hands":
            e["site_next"] = e["next"] or same_next
            if not e["site_next"]:
                problems.append(f"#{e['customer_id']} era {e['era']}: changed_hands with no successor era")
        elif reason == "moved" or reason == "continued":
            e["site_next"] = same_next
        else:
            e["site_next"] = same_next if e["ended"] and same_next else None
    return eras


def build_businesses(eras):
    """Chains of eras joined by moved/continued, as business dicts (gallons first)."""
    parent = {e["key"]: e["key"] for e in eras}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for e in eras:
        if e["business_next"]:
            parent[find(e["key"])] = find(e["business_next"])

    groups = {}
    for e in eras:
        groups.setdefault(find(e["key"]), []).append(e)

    site_prev = {}
    for e in eras:
        if e["site_next"]:
            site_prev.setdefault(e["site_next"], []).append(e["key"])

    order = lambda e: (e["first_pickup"] or e["window_start"] or "9999", e["customer_id"], e["era"])
    era_business = {}
    out = []
    for members in groups.values():
        members.sort(key=order)
        served = [e for e in members if e["pickups"]]
        bid = members[0]["key"] if not served else served[0]["key"]
        for e in members:
            era_business[e["key"]] = bid
        if not served:
            continue
        last = max(served, key=lambda e: (e["last_pickup"], e["era"]))
        current = any(not e["ended"] for e in members)
        first = served[0]
        out.append({
            "id": bid,
            "name": max(served, key=lambda e: e["last_pickup"])["business"],
            "eras": [e["key"] for e in members],
            "customer_ids": sorted({e["customer_id"] for e in members}),
            "town": last["town"],
            "first_pickup": first["first_pickup"],
            "last_pickup": last["last_pickup"],
            "pickups": sum(e["pickups"] for e in served),
            "gallons": sum(e["gallons"] for e in served),
            "status": "current" if current else "ended",
            "end_reason": None if current else last["end_reason"],
            "gained_year": int(first["first_pickup"][:4]),
            "end_year": None if current else int(last["last_pickup"][:4]),
            # Took over a site from an earlier business of ours.
            "gain_type": "successor" if site_prev.get(first["key"]) else "new",
            "_prev_eras": [k for e in members for k in site_prev.get(e["key"], [])],
            "_next_eras": [e["site_next"] for e in members if e["ended"] and e["site_next"]],
        })

    for b in out:
        own = set(b["eras"])
        b["preceded_by"] = sorted({era_business[k] for k in b.pop("_prev_eras") if k not in own})
        b["followed_by"] = sorted({era_business[k] for k in b.pop("_next_eras") if k not in own})
    for e in eras:
        e["business_id"] = era_business[e["key"]]
    out.sort(key=lambda b: -b["gallons"])
    return out


def retention_by_year(businesses, eras, first_year, current_year):
    """
    One row per calendar year, counted in businesses (non_recurring left out).

    active_jan1: served before 1 January and not ended before it.
    churn = lost / active_jan1; churn_high also counts the unknown endings and
    moves we did not follow, so the worst case is on the table.
    """
    counted = [b for b in businesses if b["end_reason"] != "non_recurring"]
    rows = []
    for y in range(first_year, current_year + 1):
        jan1 = f"{y}-01-01"
        active = sum(1 for b in counted if b["first_pickup"] < jan1
                     and (b["status"] == "current" or b["last_pickup"] >= jan1))
        row = {"year": y, "active_jan1": active,
               "gained_new": sum(1 for b in counted if b["gained_year"] == y and b["gain_type"] == "new"),
               "gained_successor": sum(1 for b in counted if b["gained_year"] == y and b["gain_type"] == "successor")}
        for reason in ENDINGS:
            row[reason] = sum(1 for b in counted if b["end_year"] == y and b["end_reason"] == reason)
        row["moved_followed"] = sum(1 for e in eras if e["end_reason"] == "moved" and e["business_next"]
                                    and e["last_pickup"] and int(e["last_pickup"][:4]) == y)
        row["gained"] = row["gained_new"] + row["gained_successor"]
        row["ended"] = sum(row[r] for r in ENDINGS)
        row["churn"] = round(row["lost"] / active, 4) if active else None
        row["churn_high"] = (round((row["lost"] + row["unknown"] + row["moved"]) / active, 4)
                             if active else None)
        row["history_start"] = y == first_year
        row["partial"] = y == current_year
        rows.append(row)
    return rows


def retention_summary(rows, businesses):
    """Headline figures over COMPLETE years (not the first year of history, not this one)."""
    full = [r for r in rows if not r["history_start"] and not r["partial"] and r["active_jan1"]]
    counted = [b for b in businesses if b["end_reason"] != "non_recurring"]
    ended = [b for b in counted if b["status"] == "ended"]
    closures = sum(1 for b in ended if b["end_reason"] in ("closed", "changed_hands"))
    handed = sum(1 for b in ended if b["end_reason"] == "changed_hands")
    years = lambda b: (date.fromisoformat(b["last_pickup"]) - date.fromisoformat(b["first_pickup"])).days / 365.25
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
    return {
        "years": [full[0]["year"], full[-1]["year"]] if full else None,
        "avg_churn": mean([r["churn"] for r in full]),
        "avg_churn_high": mean([r["churn_high"] for r in full]),
        "lost_total": sum(1 for b in ended if b["end_reason"] == "lost"),
        "unknown_total": sum(1 for b in ended if b["end_reason"] == "unknown"),
        "ended_total": len(ended),
        "site_retention": round(handed / closures, 4) if closures else None,
        "site_handovers": handed,
        "site_closures": closures,
        "avg_gained": mean([r["gained"] for r in full]),
        "avg_gained_new": mean([r["gained_new"] for r in full]),
        "avg_gained_successor": mean([r["gained_successor"] for r in full]),
        "median_years_served": round(median(years(b) for b in ended), 1) if ended else None,
        "current_businesses": sum(1 for b in counted if b["status"] == "current"),
    }


def compute_history(customers, records, current_year, path=ERAS_FILE):
    """Everything the pages need: eras, businesses, the yearly table and its summary."""
    registry, problems = load_registry(path)
    eras = build_eras(customers, records, registry, problems)
    businesses = build_businesses(eras)
    first_year = min((int(e["first_pickup"][:4]) for e in eras if e["first_pickup"]), default=current_year)
    rows = retention_by_year(businesses, eras, first_year, current_year)
    return {
        "eras": eras,
        "businesses": businesses,
        "retention_by_year": rows,
        "retention_summary": retention_summary(rows, businesses),
        "problems": problems,
    }
