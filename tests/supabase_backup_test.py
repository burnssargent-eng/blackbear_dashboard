#!/usr/bin/env python3
"""
The nightly database backup (supabase_backup.py), against a stand-in
database, no network. Run from the repo root:

    python3 tests/supabase_backup_test.py

Every table in db/schema.sql is backed up, sorted by its key; a failed or
empty read writes nothing; photos are copied once and never removed; and
nothing but counts is printed (the public repo's Actions logs are public).
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

import supabase_backup as sb  # noqa: E402
import supabase_sync as ss  # noqa: E402

fails = 0


def ok(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


SECRET = "4471 Secret Hollow Rd, door code 9931"


class FakeDb:
    def __init__(self, broken=None, empty=()):
        self.queries, self.broken, self.empty = [], broken, empty

    def select(self, table, query="select=*"):
        self.queries.append((table, query))
        if table == self.broken:
            raise ss.SyncError(f"read {table}: HTTP 500")
        if table in self.empty:
            return []
        if table == "customers":
            return [{"id": 1, "name": "Diner", "address": SECRET}]
        if table == "collections":
            return [{"id": 7, "customer_id": 1, "gallons": 40}]
        if table == "photos":
            return [{"id": 1, "storage_path": "1/a.jpg"}, {"id": 2, "storage_path": "1/b.jpg"},
                    {"id": 3, "storage_path": "../escape.jpg"}]
        return []


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*a)
    return result, buf.getvalue()


print("Tables")
schema = open("db/schema.sql").read()
in_schema = set(re.findall(r"create table if not exists public\.(\w+)", schema))
ok(in_schema == set(sb.TABLES), f"every table in db/schema.sql is backed up (missing: {sorted(in_schema - set(sb.TABLES))})")

with tempfile.TemporaryDirectory() as out:
    db = FakeDb()
    fetched = []
    fetch = lambda p: (fetched.append(p), b"jpeg:" + p.encode())[1]
    failed, printed = quiet(sb.run, db, out, fetch)
    ok(all(re.search(r"order=[a-z_]+\.asc", q) for _, q in db.queries)
       and ("route_trucks", "select=*&order=route_id.asc,day.asc") in db.queries, "each table is read in its key's order")
    ok(json.load(open(os.path.join(out, "tables", "customers.json")))[0]["address"] == SECRET,
       "rows are written in full")
    ok(sorted(os.listdir(os.path.join(out, "tables"))) == sorted(f"{t}.json" for t in sb.TABLES), "one file per table")
    counts = json.load(open(os.path.join(out, "counts.json")))
    ok(counts["rows"]["customers"] == 1 and counts["photo_files"] == 2, "counts.json holds rows per table and photos held")
    ok(SECRET not in printed and "Diner" not in printed and "rows" in printed, "only counts are printed")

    print("Photos")
    ok(failed == 0 and fetched == ["1/a.jpg", "1/b.jpg"], "each photo is fetched; a path leaving the folder is not")
    ok(open(os.path.join(out, "photos", "1", "a.jpg"), "rb").read() == b"jpeg:1/a.jpg", "the file is stored at its path")
    fetched.clear()
    quiet(sb.run, FakeDb(), out, fetch)
    ok(fetched == [], "a photo already held is not fetched again")
    os.makedirs(os.path.join(out, "photos", "9"))
    open(os.path.join(out, "photos", "9", "gone.jpg"), "wb").write(b"x")
    quiet(sb.run, FakeDb(), out, fetch)
    ok(os.path.exists(os.path.join(out, "photos", "9", "gone.jpg")), "a photo no longer in the table is kept")

    def flaky(p):
        raise OSError("timeout")
    os.remove(os.path.join(out, "photos", "1", "b.jpg"))
    failed, _ = quiet(sb.run, FakeDb(), out, flaky)
    ok(failed == 1 and json.load(open(os.path.join(out, "counts.json")))["photo_files"] == 2,
       "a photo that fails is counted and left for the next night")

    print("Refusals")
    before = open(os.path.join(out, "tables", "customers.json")).read()
    for label, db in (("a failed read", FakeDb(broken="oil_left")), ("an empty customers table", FakeDb(empty=("customers",))),
                      ("an empty collections table", FakeDb(empty=("collections",)))):
        try:
            quiet(sb.run, db, out, fetch)
            ok(False, f"{label} is refused")
        except (sb.BackupError, ss.SyncError):
            ok(open(os.path.join(out, "tables", "customers.json")).read() == before, f"{label} is refused, nothing written")

print("The workflow")
wf = open(".github/workflows/backup.yml").read()
ok("burnssargent-eng/blackbear-backup" in wf and "secrets.BACKUP_REPO_TOKEN" in wf, "it pushes to the private repo with its own token")
ok("upload-artifact" not in wf and "cat " not in wf, "it never puts the backup in an artifact or the log")
ok("|| echo" not in wf.split("Export the database")[1].split("- name:")[0], "a failed export fails the run (GitHub emails)")

print(f"\n{'All passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
