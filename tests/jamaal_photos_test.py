#!/usr/bin/env python3
"""
jamaal_photos.py with a stand-in source site, bucket and database (no
network, no key). Run from the repo root:

    python3 tests/jamaal_photos_test.py
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image  # noqa: E402

import jamaal_photos as jp  # noqa: E402

failures = 0


def ok(cond, label):
    global failures
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


def jpeg(w, h, orientation=None, noise=False):
    img = Image.new("RGB", (w, h), (90, 120, 80))
    if noise:
        img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    out = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    img.save(out, "JPEG", quality=95, exif=exif.tobytes())
    return out.getvalue()


# ── The folder listing (made-up names, the source server's layout) ──
LISTING = '''<pre><a href="?C=N;O=D">Name</a> <a href="?C=M;O=A">Last modified</a><hr><a href="/tracking/">Parent Directory</a>
      <a href="1037__IMG_1114.jpeg">1037__IMG_1114.jpeg</a>     2026-02-24 07:17  2.9M
      <a href="1109__734__F607-787E.jpeg">1109__734__F..&gt;</a> 2024-04-10 08:13  4.1M
      <a href="12__IMG_1.JPG">12__IMG_1.JPG</a>     2025-10-14 11:01  3.7M
      <a href="notes.txt">notes.txt</a>     2025-10-14 11:01  1K
</pre>'''
found = jp.parse_listing(LISTING)
ok([f[0] for f in found] == ["1037__IMG_1114.jpeg", "1109__734__F607-787E.jpeg", "12__IMG_1.JPG"],
   "the listing yields every photo, a cut-short label included, and nothing else")
ok(found[0][1:] == (1037, "2026-02-24"), "each photo's customer id comes from its name, its day from the listing")

# ── Paths: stable, and the thumbnail beside the photo as the pages expect ──
p = jp.storage_path(1037, "1037__IMG_1114.jpeg")
ok(p == jp.storage_path(1037, "1037__IMG_1114.jpeg") and p.startswith("1037/jamaal-") and p.endswith(".jpg"),
   "the same file always goes to the same path, so a retry overwrites instead of duplicating")
ok(jp.thumb_path(p) == p[:-4] + "_t.jpg", "the thumbnail is <path>_t.jpg, as ops-photos.js reads it")
ok(jp.noon("2026-02-24").startswith("2026-02-24T12:00:00-05:00"), "the listing day is stored as noon in Vermont")

# ── Shrinking: upright, 1600 / 400 px, under the bucket's limit ──
full, thumb = jp.shrink(jpeg(4032, 3024, orientation=6))
fi, ti = Image.open(io.BytesIO(full)), Image.open(io.BytesIO(thumb))
ok(fi.size == (1200, 1600) and ti.size == (300, 400), f"a sideways phone photo comes out upright at 1600 px, thumb 400 px ({fi.size}, {ti.size})")
full, _ = jp.shrink(jpeg(5712, 4284, noise=True))
ok(len(full) <= jp.MAX_BYTES, f"even pure noise fits under the 2 MB limit ({len(full)} bytes)")


# ── A run against stand-ins ──
class FakeSource:
    def __init__(self, files):
        self.files = files
        self.fetched = []

    def photos(self, ids):
        return [(n, c, d) for n, c, d in self.files], "listing"

    def file(self, name):
        self.fetched.append(name)
        if name.startswith("1109"):
            raise jp.PhotoError("broken file")
        return jpeg(800, 600)


class FakeBucket:
    def __init__(self):
        self.put_paths = []

    def put(self, path, data):
        self.put_paths.append(path)


class FakeDb:
    def __init__(self, photos=(), customers=()):
        self.t = {"photos": [dict(r) for r in photos], "customers": [{"id": c} for c in customers]}
        self.inserted = []

    def select(self, table, query):
        rows = self.t[table]
        if "jamaal_file=not.is.null" in query:
            rows = [r for r in rows if r.get("jamaal_file")]
        return rows

    def insert(self, table, rows):
        self.inserted += rows
        self.t[table] += rows


files = [("1037__IMG_1114.jpeg", 1037, "2026-02-24"), ("1109__734__F607-787E.jpeg", 1109, "2024-04-10"),
         ("12__IMG_1.JPG", 12, "2025-10-14"), ("9999__new.jpeg", 9999, "2026-10-20")]
quiet = lambda *a: None

db = FakeDb(customers=[1037, 1109, 12])
n, missing, failed = jp.run(db, FakeSource(files), FakeBucket(), nightly=True, log=quiet)
ok(n == 0 and not db.inserted, "the nightly copies nothing until a first copy was run by hand")

src, bucket = FakeSource(files), FakeBucket()
n, missing, failed = jp.run(db, src, bucket, log=quiet)
ok(n == 2 and [r["jamaal_file"] for r in db.inserted] == ["1037__IMG_1114.jpeg", "12__IMG_1.JPG"],
   "a first copy stores every photo whose customer is known")
ok(missing == {9999: ["9999__new.jpeg"]} and "9999__new.jpeg" not in src.fetched,
   "a photo of a customer not in the database yet is skipped and reported, not downloaded")
ok(failed and failed[0][0].startswith("1109"), "a broken file is reported and the rest still copy")
row = db.inserted[0]
ok(row["caption"] is None and row["storage_path"] == jp.storage_path(1037, "1037__IMG_1114.jpeg")
   and row["uploaded_at"] == row["taken_at"] == jp.noon("2026-02-24"),
   "blank caption; added on the source site's day, so the full view does not say today")
ok(bucket.put_paths[:2] == [row["storage_path"], jp.thumb_path(row["storage_path"])], "both sizes are stored before the row")
ok(len({tuple(sorted(r)) for r in db.inserted}) == 1, "every row has the same keys (PostgREST bulk rule)")

db.t["customers"].append({"id": 9999})
db.t["photos"][0]["removed_at"] = "2026-10-25T10:00:00Z"   # removed on the site
src2 = FakeSource(files)
n, missing, failed = jp.run(db, src2, FakeBucket(), nightly=True, log=quiet)
ok(n == 1 and db.inserted[-1]["jamaal_file"] == "9999__new.jpeg" and not missing,
   "the nightly copies only what is new, once its customer exists")
ok("1037__IMG_1114.jpeg" not in src2.fetched, "a photo removed on the site is never copied back")
ok(src2.fetched == ["1109__734__F607-787E.jpeg", "9999__new.jpeg"], "the one that failed is retried the next night")

dry_db = FakeDb(customers=[1037, 1109, 12])
dry_bucket = FakeBucket()
n, _, _ = jp.run(dry_db, FakeSource(files), dry_bucket, dry_run=True, log=quiet)
ok(n == 2 and not dry_db.inserted and not dry_bucket.put_paths, "--dry-run downloads and shrinks but writes nothing")
n, missing, _ = jp.run(None, FakeSource(files), None, dry_run=True, log=quiet)
ok(n == 3 and not missing, "--dry-run without a key treats every customer as known")

print(f"\n{'All passed' if not failures else 'FAILURES'} ({failures} failure(s))")
sys.exit(1 if failures else 0)
