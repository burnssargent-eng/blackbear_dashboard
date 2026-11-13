#!/usr/bin/env python3
"""
--export-collections (supabase_sync.py), on made-up rows, no network. Run from
the repo root:

    python3 tests/export_collections_test.py

The site's pickups and 0-3 codes replace the scrape's from --since on; rows
before it are kept as they were; a pickup goes to the raw file and a code to
the non-pickups file, as the scraper splits them; each customer keeps its
block, newest first; and the export refuses, writing nothing, when the
database returns under half the window's pickups.
"""

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

import oil_scraper  # noqa: E402
import supabase_sync as ss  # noqa: E402

fails = 0


def ok(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


def raw(cid, day, gal, name="Diner", city="Stowe", active="True"):
    return {"customer_id": str(cid), "name": name, "city": city, "county": "Lamoille", "date": day,
            "year": day[:4], "month": day[:7], "gallons": str(gal), "is_active": active, "geo_town": city}


def site(cid, day, gal):
    return {"customer_id": cid, "picked_on": day, "gallons": gal}


SINCE = "2026-10-26"
RAW = [raw(1, "2026-11-02", 50), raw(1, "2026-10-10", 40), raw(1, "2026-09-01", 30),
       raw(2, "2026-10-27", 80, name="Pub", city="Barre City"), raw(2, "2026-08-01", 60, name="Pub", city="Barre City"),
       raw(3, "2026-10-30", 20, name="Cafe")]          # 3: only window rows
CODES = [{"customer_id": "1", "date": "2026-09-15", "qty": "1"},
         {"customer_id": "9", "date": "2026-10-28", "qty": "0"}]   # a source-site auto 0 in the window
SITE = [site(1, "2026-11-02", 50), site(1, "2026-11-10", 1), site(2, "2026-10-27", 85),
        site(3, "2026-10-30", 20), site(4, "2026-11-05", 70), site(2, "2026-11-11", 3)]
CUSTOMERS = [{"id": 1, "name": "Diner", "city": "Burlington", "active": True},
             {"id": 2, "name": "Pub (renamed)", "city": "Barre", "active": False},
             {"id": 4, "name": "New Bakery", "city": "Montpelier", "active": True}]

print("The scraper's rules")
ok(ss.EMPTY_QTYS == set(oil_scraper.EMPTY_QTYS), "EMPTY_QTYS matches oil_scraper.py")
ok(ss.RAW_FIELDS == ["customer_id", "name", "city", "county", "date", "year", "month", "gallons",
                     "is_active", "geo_town"], "raw columns as the scraper writes them")
ok(ss.NON_PICKUP_FIELDS == oil_scraper.NON_PICKUP_FIELDS, "non-pickup columns match oil_scraper.py")

out, codes, window = ss.site_collection_files(SITE, CUSTOMERS, RAW, CODES, SINCE)
key = lambda r: (r["customer_id"], r["date"], r["gallons"])

print("Rows")
ok([key(r) for r in out] == [("1", "2026-11-02", "50"), ("1", "2026-10-10", "40"), ("1", "2026-09-01", "30"),
                             ("2", "2026-10-27", "85"), ("2", "2026-08-01", "60"),
                             ("3", "2026-10-30", "20"), ("4", "2026-11-05", "70")],
   "window replaced by the site's, history kept, blocks in file order, new customer last")
ok(all(r["gallons"] not in ("0", "1", "2", "3") for r in out), "no 0-3 code in the raw file")
ok([(r["customer_id"], r["date"], r["qty"]) for r in codes]
   == [("1", "2026-09-15", "1"), ("1", "2026-11-10", "1"), ("2", "2026-11-11", "3")],
   "codes: history kept, the window's auto 0 gone, the site's codes in, sorted")
ok(window == {"file_pickups": 3, "file_codes": 1, "site_pickups": 4, "site_codes": 2}, f"window counts {window}")

print("Customer fields")
pub = [r for r in out if r["customer_id"] == "2"]
ok(all(r["name"] == "Pub (renamed)" and r["is_active"] == "False" for r in pub),
   "name and is_active follow the database on every row, old ones too")
ok(all(r["city"] == "Barre City" for r in pub), "a new row takes the customer's newest city, not the database's")
cafe = [r for r in out if r["customer_id"] == "3"][0]
ok(cafe["name"] == "Cafe" and cafe["is_active"] == "True", "a customer the database lacks keeps the file's")
new = [r for r in out if r["customer_id"] == "4"][0]
ok(new["name"] == "New Bakery" and new["city"] == "Montpelier" and new["is_active"] == "True",
   "a customer new to the file takes the database's name, city and status")
ok([r["date"] for r in out if r["customer_id"] == "1"] == ["2026-11-02", "2026-10-10", "2026-09-01"],
   "newest first within a customer")

print("Idempotent")
out2, codes2, _ = ss.site_collection_files(SITE, CUSTOMERS, out, codes, SINCE)
ok(out2 == out and codes2 == codes, "a second export over its own output changes nothing")


class FakeDb:
    def __init__(self, collections):
        self.collections = collections

    def select(self, table, query="select=*"):
        return self.collections if table == "collections" else CUSTOMERS


def write(path, fields, rows, term):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator=term)
        w.writeheader()
        w.writerows(rows)


print("Files and the guard")
with tempfile.TemporaryDirectory() as tmp:
    rf, cf = os.path.join(tmp, "raw.csv"), os.path.join(tmp, "codes.csv")
    write(rf, ss.RAW_FIELDS, RAW, "\n")
    write(cf, ss.NON_PICKUP_FIELDS, CODES, "\r\n")
    before = (open(rf, "rb").read(), open(cf, "rb").read())
    for label, rows in (("an empty read", []), ("a partial read", SITE[:1])):
        try:
            ss.export_collections(FakeDb(rows), SINCE, rf, cf)
            ok(False, f"{label} refused")
        except ss.SyncError:
            ok((open(rf, "rb").read(), open(cf, "rb").read()) == before, f"{label} refused, files untouched")
    for bad in (None, "26/10/2026"):
        try:
            ss.export_collections(FakeDb(SITE), bad, rf, cf)
            ok(False, f"--since {bad!r} refused")
        except ss.SyncError:
            ok(True, f"--since {bad!r} refused")
    ss.export_collections(FakeDb(SITE), SINCE, rf, cf)
    ok(ss.read_csv_rows(rf) == out, "written raw file reads back as the rows")
    ok(open(cf, "rb").read().startswith(b"customer_id,date,qty\r\n"), "codes file keeps the scraper's \\r\\n")
    ok(b"\r" not in open(rf, "rb").read(), "raw file keeps pandas' \\n")

print(f"\n{'All passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
