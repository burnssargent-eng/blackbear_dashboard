#!/usr/bin/env python3
"""
Fingerprint the pages' own .css/.js links: href="ops.css?v=3fa9c1e2", the first
8 hex digits of the file's SHA-1.

GitHub Pages lets a browser keep any file for 10 minutes (max-age=600; iPhone
Safari sometimes longer), so after a deploy a phone can pair the new page with
the OLD stylesheet or script. On 2026-11-19 Jim's phone drew the new My Route
stop lines with the old ops.css: a bare "Remove" over every stop and a
full-width number box. A fingerprint changes whenever the file does, so the
browser fetches it fresh; unchanged files stay cached.

    python3 asset_versions.py           # rewrite every page's fingerprints
    python3 asset_versions.py --check   # exit 1 if any is missing or stale

Run it after changing any .css or .js a page loads, and commit the pages with
the file. tests/asset_versions_test.js fails while a fingerprint is stale.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# A page's own file: a bare local name (no folder, no scheme), with or without ?v=.
LINK = re.compile(r'\b(src|href)="([a-z][a-z0-9_-]*\.(?:js|css))(?:\?v=([0-9a-f]*))?"')


def fingerprint(name):
    return hashlib.sha1((ROOT / name).read_bytes()).hexdigest()[:8]


def pages():
    out = subprocess.run(["git", "ls-files", "*.html"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.split() if "/" not in p]


def main():
    check = "--check" in sys.argv[1:]
    stale = []
    for page in pages():
        text = page.read_text(encoding="utf-8")

        def fix(m):
            attr, name, have = m.groups()
            if not (ROOT / name).exists():
                return m.group(0)
            want = fingerprint(name)
            if have != want:
                stale.append(f"{page.name}: {name} has ?v={have or '(none)'}, wants ?v={want}")
            return f'{attr}="{name}?v={want}"'

        new = LINK.sub(fix, text)
        if not check and new != text:
            page.write_text(new, encoding="utf-8")
    if check:
        for s in stale:
            print(s)
        print(f"{len(stale)} stale fingerprint(s)" if stale else "all fingerprints current")
        return 1 if stale else 0
    print(f"updated {len(stale)} link(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
