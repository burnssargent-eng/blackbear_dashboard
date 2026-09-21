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
`all_time_total` while also excluding the `Other` bucket. The overlap is
deliberate and is now entirely the theme regions': `Ski Slopes`,
`South / Ski Combined`, `Summer Snack Stops` and `Winter / Summer Combined`
are overlays drawn from customers who also sit in their own geography, and
`South / Ski Combined` is built from the South and Southern Ski Slopes rules.
There are FOUR of them — `THEME_REGIONS` is the list, and this file named only
three until 2026-09-21. `Perrigo` is id-based too but is NOT one: it is
exclusive, so it ranks among the geography instead of being pinned after it. No geographic town belongs
to two regions any more — Newport City was in both Northwest and
North-Northeast until Jim made it North-Northeast only on 2026-09-20, and
`EXPECTED_SHARED_TOWNS` in `validate_data.py` is empty as a result, so any new
shared town warns. All of it is asserted in `validate_data.py`. Do not "fix" it.

**`Other` is not the count of unassigned customers.** It means matched NO
region at all (`oil_scraper.py:953-955`), theme regions included, so a customer
with no geography but a theme tag never reaches the bucket and the dashboard's
figure reads low. On 2026-09-21: 13 in `Other`, but 17 with no geographic
region — Arlington, Pittsford, Rockingham and Roxbury were masked by
`Winter / Summer Combined` + `Summer Snack Stops`. Five of the 17 are
out-of-state and will never take a Vermont region, so the list actually worth
sending Jim was 12. To count them, subtract the union of the non-theme regions
from the roster; do not read `Other`.

**Theme regions are not places.** They are listed in `THEME_REGIONS`, exported as
`theme_regions`, pinned after every geographic region in the display order, and
left out of the homepage's Top Regions — where they would rank beside the
regions they are drawn from. Membership is by customer id, never by name match:
"Winooski" contains "ski" and "Blodgett" contains "lodge".

**Region membership is config, not code.** `REGION_TOWNS` in `oil_scraper.py`
maps a region to official town names, matched on the normalised `geo_town` with
a fallback to raw `city` (that fallback is what reaches out-of-state places like
Plattsburgh). Add a town there and every consumer follows. Town spelling
variants belong in `GEO_TOWN_NAME_MAP`, not in the region config.

**Region rules are additive — except one.** A row takes EVERY label it matches,
so adding a town to a region can never remove a customer from another.
`REGION_EXCLUDED_IDS` (`oil_scraper.py`) is the single subtractive rule: it
keeps named customer ids out of a town-based region they would otherwise match.
Today it holds only Perrigo (customer 423), split out of Northwest on
2026-09-21 because a bulk on-demand account was 54% of that region and made its
numbers a reading of one contract rather than a route. Milton stays in
Northwest for its six other customers. If an exclusion is ever dropped without
dropping the id-based region that replaces it, the customer silently rejoins
and the region inflates — so it is asserted from the EXPORTED membership by
`REQUIRED_CUSTOMER_REGION` and `FORBIDDEN_CUSTOMER_REGION` in
`validate_data.py`, not from the config that produced it.

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
node tests/run_all.js             # five front-end suites; see tests/README.md
```

Two standing warnings, both towns configured ahead of their first customer:
`Route 7:New Haven` and `Northwest:Alburgh`. Expected.

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

**Stage explicitly, and verify the staged set in a separate step.** `git checkout
<branch> -- <paths>` and `git rm` both leave their changes STAGED, so a later
`git add` for a different commit silently inherits them and one commit swallows
the other's files. Check `git diff --cached --name-only` before committing, in
its own command — printing it inside the same chain as the commit is too late.

## Open follow-ups

Raised by the data, not yet decided by Jim:

- **Rutland has collected nothing in 2026.** Jim put it in Route 7 on
  2026-09-21 and its 11,123 all-time gallons are real, but the last pickup was
  2025-09-10. Service dates went 15 in 2024 to 4 in 2025 to 0 since, and the
  three accounts still flagged active — Hannaford, Mad Rose, Uncle Sam's — all
  stopped on that same day, on a combined run. Three accounts lapsing
  separately would stop on three dates; this reads as the route being dropped.
  Ask Jim whether that was deliberate. Note the committed CSV is written after
  the `EMPTY_QTYS` filter (`oil_scraper.py:337`, written at `:989`), so a
  zero-quantity customer call in 2026 would not show up either way — the claim
  is no collected volume, not no contact.
- **Pittsford** (customer 1090, Proctor-Pittsford Country Club, 435 gallons) is
  one letter from Pittsfield, which Jim put in Central South on 2026-09-20. He
  named Pittsfield and it was taken literally; Pittsford is a different town, in
  Rutland County on US-7. It is NOT in `Other` — it is masked by two theme
  regions — and the case for Route 7 is now concrete: all six of its pickups
  happened on days the truck was also in Rutland City, three of them alongside
  Middlebury and Vergennes, and Jim put Rutland in Route 7 on 2026-09-21, which
  leaves Pittsford sitting in the gap. Ask him; the club is named for Proctor,
  the next town over, which is likely why nobody said "Pittsford" out loud.
  Brandon is the other gap town and has not been checked for customers.
- **Twelve Vermont towns have no geographic region**, one customer each, 7,118
  gallons: Arlington, Mount Holly, Westford, Whitingham, Pittsford, Waterford,
  Rockingham, Rochester, Roxbury, Weathersfield, Barnard, Lincoln. Eleven are
  active. Arlington, Whitingham and Rockingham are all Windham County and may be
  one question rather than three.

## People

**Jim** sets business direction, including the regional taxonomy. His groupings
are the source of truth for what a region means; when he names some towns and
omits others, ask rather than assume the omission is deliberate.
