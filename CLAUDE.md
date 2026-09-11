# Black Bear Biodiesel dashboard

Static site showing waste-oil collection analytics. No build step, no
dependencies at runtime — the pages fetch committed JSON and render it. Python
generates that JSON; the browser does everything else.

Live from `main`. **Merging a PR deploys.**

## Shape of the thing

```
oil_scraper.py ──> oil_data.json          (aggregates: regions, towns, counties, projection)
               └─> oil_collections.json   (27k pickup records + 796-customer roster)
               └─> oil_collections_raw.csv (the local cache — committed)
               └─> oil_collection_report.xlsx

export_schmootz.py ──> schmootz_data.json  (from data/Schmootz.xlsx, gitignored)

index.html      dashboard + Leaflet heatmap. Keeps its OWN inline styles.
region.html     per-region page. The most complex page.
year.html  town.html  customers.html  schmootz.html
dashboard-utils.js   shared helpers for the five detail pages
dashboard.css        shared styles for the five detail pages — NOT index.html
```

## Regenerating data

```
python3 oil_scraper.py            # reuses the committed CSV — no network
python3 oil_scraper.py --rescrape # live scrape; the nightly does this, you rarely should
```

A plain run rewrites the CSV, the Excel report and both JSONs, and is
idempotent. `.github/workflows/nightly-update.yml` runs `--rescrape` at 07:00
UTC and commits straight to `main`, so expect a data diff most mornings.

## Rules that are easy to break

**`EMPTY_QTYS = {0, 1, 2, 3}`** (`oil_scraper.py:36`). Quantities 0–3 mean
"nothing collected" — 2 is a customer call, 3 is a barrel delivery. **4 is NOT
excluded**: it is a data-entry quirk that counts as exactly 4 gallons and is
never rounded up. `validate_data.py` fails if either changes.

**A missing month is a real zero, not missing data.** Months with no pickups
produce no row at all. Southern Ski Slopes has never had a 12-row year — it
shuts down in summer. Never use "has 12 months of rows" as a completeness test;
use "is a past calendar year".

**Regions overlap and are not exhaustive.** Region totals sum to MORE than
`all_time_total` (UVM double-counts into Burlington) while also excluding the
`Other` bucket. Both are intentional and asserted in `validate_data.py`. Do not
"fix" it.

**Region membership is config, not code.** `REGION_TOWNS` in `oil_scraper.py`
maps a region to official town names, matched on the normalised `geo_town` with
a fallback to raw `city` (that fallback is what reaches out-of-state places like
Plattsburgh). Add a town there and every consumer follows. Town spelling
variants belong in `GEO_TOWN_NAME_MAP`, not in the region config.

**Excel sheet names reject `/`.** Region display names contain slashes, so
`region_sheet_names()` sanitises them. Never pass a display name to
`sheet_name=`.

**The projection formula exists twice.** `compute_projection()` in
`oil_scraper.py:926` and `regionProjection()` in `region.html` — the second is
per-region, which the export does not provide. If one changes, change both.
Both are commented to say so.

**`render()` in region.html runs exactly once** and its three Chart instances
are never destroyed. `updateRegionCustomers()` must never call it, or the charts
leak. Anything re-rendered on a filter change must be scoped to its own element.

**`index.html` does not link `dashboard.css`.** It keeps a duplicate inline copy
on purpose, so the homepage cannot be broken by a shared-stylesheet edit. A
change meant for every page goes in both. `dashboard.css` is shared by the other
five, so prefix new classes distinctly.

## Checks

```
python3 validate_data.py          # ~82 checks on the generated data
python3 check_town_mismatches.py  # town names vs the GeoJSON; exits non-zero on a real mismatch
node tests/run_all.js             # six front-end suites; see tests/README.md
```

The one standing warning is `Route 7:New Haven` — a town configured with no
customers yet. Expected.

**The tests cannot see layout.** They evaluate the real functions and templates
against real JSON, which catches broken maths and markup, but every layout
problem in this project was found by opening a browser. Connect Claude in Chrome
early rather than trusting a green suite.

## Working here

Feature branch off current `main`, stage by name, PR, merge. Never commit to
`main` directly — and note that `gh pr merge --delete-branch` drops you back
onto `main`, so re-check the branch before the next commit.

A new file and whatever references it belong in the same commit; the data files
and the config that generated them likewise.

## People

**Jim** sets business direction, including the regional taxonomy. His groupings
are the source of truth for what a region means; when he names some towns and
omits others, ask rather than assume the omission is deliberate.
