#!/usr/bin/env python3
"""
Nightly backup of the shared database (Supabase, db/schema.sql) into a folder
that .github/workflows/backup.yml commits to the PRIVATE repo
burnssargent-eng/blackbear-backup (Sarge, 2026-11-14). The free plan has no
backups of its own, and much of the database exists nowhere else: routes,
notes and door codes, phones, address fixes, Oil Left, thefts, photos,
customers added on the site. After the switch from the source site it is the
only record of pickups too.

    python3 supabase_backup.py OUT_DIR

  OUT_DIR/tables/<table>.json   every row of every table, sorted by its key,
                                rewritten each night (the repo's history keeps
                                every night's copy)
  OUT_DIR/counts.json           rows per table and photo files held
  OUT_DIR/photos/<path>         each photo file (not the _t.jpg thumbnails,
                                which the phone remakes), copied once and never
                                removed: a photo removed on the site stays here

Everything in OUT_DIR is PRIVATE (addresses, door codes, phones). This script
runs in the PUBLIC repo's Actions, whose logs anyone can read, so it prints
row counts and nothing else.

All tables are read before any is written: a failed read leaves the folder as
it was and exits 1 (the workflow then fails and GitHub emails the owner). So
does an empty customers or collections table, which can only mean a broken
read. A photo that fails to download is skipped and retried the next night;
the run still exits 1 so the failure is seen.

Not covered: sign-in accounts (Supabase's auth.users is not readable here).
`people` holds who is on the team; after a restore each person is added again
under Authentication > Users. Needs SUPABASE_SERVICE_KEY.
"""

import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote

import supabase_sync as ss

# Every table in db/schema.sql and the key it is read and sorted by.
# tests/supabase_backup_test.py fails when the schema gains a table not listed here.
TABLES = {
    "people": "user_id",
    "customers": "id",
    "routes": "id",
    "route_stops": "id",
    "route_trucks": "route_id,day",
    "collections": "id",
    "collection_history": "id",
    "oil_left": "id",
    "daily_notes": "customer_id,day",
    "photos": "id",
    "outgoing": "id",
    "sync_reports": "id",
    "customer_contacts": "id",
    "office_phones": "name",
}
MUST_HAVE_ROWS = ("customers", "collections")   # empty = a broken read, never real


class BackupError(Exception):
    pass


def read_tables(db):
    """Every table, read in full before anything is written."""
    out = {}
    for table, key in TABLES.items():
        order = ",".join(f"{k}.asc" for k in key.split(","))
        out[table] = db.select(table, f"select=*&order={order}")
    empty = [t for t in MUST_HAVE_ROWS if not out[t]]
    if empty:
        raise BackupError(f"{', '.join(empty)} came back empty: a broken read, nothing written")
    return out


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def backup_photos(rows, out_dir, fetch):
    """
    Copy each photo file not yet in out_dir/photos. `fetch(path)` returns the
    file's bytes or raises. Returns (copied, failed, held).
    """
    copied = failed = 0
    for r in rows:
        path = r.get("storage_path") or ""
        if not path or ".." in path.split("/") or path.startswith("/"):
            continue
        dest = os.path.join(out_dir, "photos", path)
        if os.path.exists(dest):
            continue
        try:
            data = fetch(path)
        except Exception:   # noqa: BLE001  any failure: skip it, retry tomorrow
            failed += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(dest + ".tmp", dest)
        copied += 1
    held = sum(len(files) for _, _, files in os.walk(os.path.join(out_dir, "photos")))
    return copied, failed, held


def run(db, out_dir, fetch):
    tables = read_tables(db)
    for table, rows in tables.items():
        write_json(os.path.join(out_dir, "tables", f"{table}.json"), rows)
    copied, failed, held = backup_photos(tables["photos"], out_dir, fetch)
    counts = {"backed_up_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "rows": {t: len(r) for t, r in tables.items()}, "photo_files": held}
    write_json(os.path.join(out_dir, "counts.json"), counts)
    # Counts only: the logs of the public repo's Actions are public.
    for t, n in counts["rows"].items():
        print(f"  {t:<20} {n:>7} rows")
    print(f"  photo files: {held} held, {copied} new, {failed} failed")
    return failed


def main():
    if len(sys.argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    out_dir = sys.argv[1]
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        print("SUPABASE_SERVICE_KEY is not set: nothing backed up", file=sys.stderr)
        return 1
    db = ss.Db(ss.SUPABASE_URL, key)
    storage = ss.SUPABASE_URL.rstrip("/") + "/storage/v1/object/photos/"

    def fetch(path):
        resp = db.s.get(storage + quote(path), timeout=60)
        resp.raise_for_status()
        return resp.content

    try:
        failed = run(db, out_dir, fetch)
    except (BackupError, ss.SyncError, ss.requests.RequestException) as exc:
        # The message names a table and an HTTP status, never row contents.
        print(f"Backup failed: {str(exc)[:200]}", file=sys.stderr)
        return 1
    if failed:
        print(f"{failed} photo file(s) failed; the next night retries them", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
