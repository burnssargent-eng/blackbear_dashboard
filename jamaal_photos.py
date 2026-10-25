#!/usr/bin/env python3
"""
Copy the source site's customer photos (barrel, spot, lock) into the shared
database's private `photos` bucket, so the shadow pages show them (Sarge,
2026-10-24: blank captions, new ones copied nightly).

The source site keeps them in its uploads/ folder, named
`<customer id>__<original name>.jpeg`; the folder's own file listing is read
(GET only), with a fallback to the customer pages' <img> tags if the listing
is ever closed. Each photo is shrunk as the phone does (ops-photos.js): turned
upright, a 1600 px JPEG plus a 400 px thumbnail, stored at
`<customer>/jamaal-<hash>.jpg` and `..._t.jpg`, with one `photos` row whose
`jamaal_file` is the source file name. That name is unique, so a photo is
copied once: a re-run skips it, and one removed on the site stays removed.
A photo whose customer is not in the database yet is skipped and reported;
the nightly adds the customer, and the next run copies it.

    python3 jamaal_photos.py --dry-run      # what would be copied, sizes after shrinking; writes nothing
    python3 jamaal_photos.py                # copy every new photo
    python3 jamaal_photos.py --nightly      # the same, but only once a first copy was run by hand
    python3 jamaal_photos.py --dry-run --limit 5   # try a few

Needs SUPABASE_SERVICE_KEY, except --dry-run, which without it assumes an
empty database (every photo new, every customer known).
"""

import argparse
import hashlib
import io
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageOps

from jamaal_routes import BASE_URL, USER_AGENT
from supabase_sync import SUPABASE_URL, Db, SyncError

UPLOADS_URL = f"{BASE_URL}/uploads/"
FULL_PX, FULL_QUALITY, FULL_QUALITY_LIGHT = 1600, 80, 60
THUMB_PX, THUMB_QUALITY = 400, 75
MAX_BYTES = 1_900_000          # under the bucket's 2 MB limit (db/schema.sql)
DELAY_SEC = 0.3
VERMONT = ZoneInfo("America/New_York")

# "<a href="1037__IMG_1114.jpeg">...</a>     2026-02-24 07:17  2.9M"
LISTING_RE = re.compile(r'<a href="(\d+__[^"/?]+\.(?:jpe?g|png|heic))">[^<]*</a>\s+(\d{4}-\d\d-\d\d) \d\d:\d\d', re.I)
PAGE_PHOTO_RE = re.compile(r"src='uploads/(\d+__[^']+)'")


class PhotoError(Exception):
    pass


def parse_listing(html):
    """[(file name, customer id, YYYY-MM-DD)] from the uploads/ folder listing."""
    return [(name, int(name.split("__", 1)[0]), day) for name, day in LISTING_RE.findall(html)]


def storage_path(customer_id, name):
    """The same file always lands at the same place, so a retried upload overwrites rather than duplicates."""
    return f"{customer_id}/jamaal-{hashlib.sha1(name.encode()).hexdigest()[:16]}.jpg"


def thumb_path(path):
    return re.sub(r"\.jpg$", "_t.jpg", path)


def shrink(raw):
    """(full JPEG bytes, thumbnail JPEG bytes), upright, as the phone makes them."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    full = img.copy()
    full.thumbnail((FULL_PX, FULL_PX))

    def jpeg(im, quality):
        out = io.BytesIO()
        im.save(out, "JPEG", quality=quality)
        return out.getvalue()

    full_bytes = jpeg(full, FULL_QUALITY)
    if len(full_bytes) > MAX_BYTES:
        full_bytes = jpeg(full, FULL_QUALITY_LIGHT)
    if len(full_bytes) > MAX_BYTES:
        raise PhotoError("too large even after shrinking")
    thumb = full.copy()
    thumb.thumbnail((THUMB_PX, THUMB_PX))
    return full_bytes, jpeg(thumb, THUMB_QUALITY)


class Source:
    """The source site, GET only."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT

    def get(self, url, **kw):
        resp = self.s.get(url, timeout=60, **kw)
        resp.raise_for_status()
        return resp

    def photos(self, customer_ids):
        """Every photo file, from the folder listing, or from the customer pages if the listing is gone."""
        try:
            found = parse_listing(self.get(UPLOADS_URL).text)
            if found:
                return found, "listing"
        except requests.RequestException:
            pass
        found = []
        for cid in sorted(customer_ids):
            try:
                html = self.get(f"{BASE_URL}/customer.php", params={"id": cid}).text
            except requests.RequestException:
                continue
            found += [(name, cid, None) for name in dict.fromkeys(PAGE_PHOTO_RE.findall(html))]
            time.sleep(DELAY_SEC)
        return found, "customer pages"

    def file(self, name):
        return self.get(UPLOADS_URL + requests.utils.quote(name)).content


class Bucket:
    """The `photos` bucket through Storage's REST API, with the service key."""

    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/storage/v1/object/photos/"
        self.s = requests.Session()
        self.s.headers["apikey"] = key
        if key.startswith("eyJ"):        # a legacy service_role JWT also needs the bearer header
            self.s.headers["Authorization"] = f"Bearer {key}"

    def put(self, path, data):
        resp = self.s.post(self.base + path, data=data, timeout=60,
                           headers={"Content-Type": "image/jpeg", "x-upsert": "true"})
        if resp.status_code >= 300:
            raise PhotoError(f"upload {path}: HTTP {resp.status_code} {resp.text[:200]}")


def noon(day):
    """The listing gives the source server's clock; the day is what matters, so store noon in Vermont."""
    return datetime.fromisoformat(day).replace(hour=12, tzinfo=VERMONT).isoformat() if day else None


def run(db, source, bucket, dry_run=False, nightly=False, limit=None, log=print):
    """Copy new photos. Returns (copied, skipped customers, failures)."""
    if db is not None:
        done = {r["jamaal_file"] for r in db.select("photos", "select=jamaal_file&jamaal_file=not.is.null")}
        known = {r["id"] for r in db.select("customers", "select=id")}
    else:
        done, known = set(), None
    if nightly and not done:
        log("No photo has been copied yet: run the first copy by hand (Actions > Copy source-site photos).")
        return 0, {}, []
    files, how = source.photos(known or [])
    log(f"{len(files)} photo(s) on the source site (from its {how}); {len(done)} already copied")
    new = [f for f in files if f[0] not in done]
    missing = {}
    todo = []
    for name, cid, day in new:
        if known is not None and cid not in known:
            missing.setdefault(cid, []).append(name)
        else:
            todo.append((name, cid, day))
    if missing:
        log(f"Skipped {sum(map(len, missing.values()))} photo(s): customer(s) not in the database yet: "
            + ", ".join(str(c) for c in sorted(missing)))
    if limit is not None:
        todo = todo[:limit]
    copied, failures, raw_total, out_total = 0, [], 0, 0
    for name, cid, day in todo:
        try:
            raw = source.file(name)
            full, thumb = shrink(raw)
            raw_total += len(raw)
            out_total += len(full) + len(thumb)
            path = storage_path(cid, name)
            if not dry_run:
                bucket.put(path, full)
                bucket.put(thumb_path(path), thumb)
                when = noon(day)
                db.insert("photos", [{"customer_id": cid, "storage_path": path, "caption": None,
                                      "taken_at": when, "uploaded_at": when or datetime.now(VERMONT).isoformat(),
                                      "jamaal_file": name}])
            copied += 1
        except (requests.RequestException, OSError, PhotoError, SyncError) as exc:
            failures.append((name, str(exc)[:200]))
            log(f"  failed {name}: {str(exc)[:200]}")
        time.sleep(DELAY_SEC)
    verb = "Would copy" if dry_run else "Copied"
    log(f"{verb} {copied} photo(s): {raw_total / 1e6:.1f} MB on the source site -> {out_total / 1e6:.1f} MB stored"
        + (f"; {len(failures)} failed" if failures else ""))
    return copied, missing, failures


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="download and shrink, write nothing")
    ap.add_argument("--nightly", action="store_true", help="do nothing until a first copy was run by hand")
    ap.add_argument("--limit", type=int, help="copy at most this many")
    args = ap.parse_args()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not key and not args.dry_run:
        print("SUPABASE_SERVICE_KEY is not set: nothing copied", file=sys.stderr)
        return 1
    db = Db(SUPABASE_URL, key, dry_run=args.dry_run) if key else None
    try:
        _, _, failures = run(db, Source(), Bucket(SUPABASE_URL, key) if key else None,
                             dry_run=args.dry_run, nightly=args.nightly, limit=args.limit)
    except (SyncError, requests.RequestException) as exc:
        print(f"Photo copy failed: {exc}", file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
