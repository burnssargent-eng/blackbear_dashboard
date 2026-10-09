# Black Bear Biodiesel dashboard

Static site showing waste-oil collection analytics. No build step, no
dependencies at runtime — the pages fetch committed JSON and render it. Python
generates that JSON; the browser does everything else.

Live from `main`. **Merging a PR deploys.**

**Projection, capacity or seasonality work: read `analysis/README.md` first**
(the pipeline, the method and every decision so far), then
`analysis/ROADMAP.md` (what is in flight and what comes next). Update the
roadmap's "In flight" section when a step finishes.

## Shape of the thing

```
oil_scraper.py ──> oil_data.json          (aggregates: regions, towns, counties, projection)
               └─> oil_collections.json   (27k pickup records + 796-customer roster)
               └─> oil_collections_raw.csv (the local cache — committed)
               └─> oil_collection_report.xlsx
               └─> capacity_cache.csv     (capacity + site periodicity; --rescrape only)
               └─> oil_non_pickups.csv    (the 0-3 entries totals drop; --rescrape only)

export_schmootz.py ──> schmootz_data.json  (from data/Schmootz.xlsx, gitignored)

analysis/build_projection_table.py ──> oil_projections.json  (beta pickup projections)
                                   └─> analysis/oil_projection_table.* (csv gitignored)

analysis/live_test.py ──> analysis/live_test_log.csv  (projected vs collected, per pickup)
                      └─> oil_live_test.json          (the Live test section of projections.html)

index.html      dashboard + Leaflet heatmap. Keeps its OWN inline styles.
region.html     per-region page. The most complex page.
year.html  town.html  customers.html  schmootz.html
projections.html     beta pickup projections. In the nav as "Projections", after Dashboard.
dashboard-utils.js   shared helpers for the detail pages
dashboard.css        shared styles for the detail pages — NOT index.html
```

**`projections.html` only displays `oil_projections.json`.** It never
recomputes a projection or re-derives a section — the maths lives once, in
`build_projection_table.py`, and a per-customer value the page needs is
exported there, not derived in the page (the header boxes only count and sum
exported rows). Rows show in the builder's order by default; the only
re-sort is one the viewer picks by clicking a column heading (`#` restores the
builder's order). The nightly runs that builder
after the scraper; a build failure is tolerated (the old JSON stays) and the
page warns when its `data_last_updated` no longer matches `oil_data.json`.
Its confidence bands come from the committed `analysis/customer_wape.json`, not
the gitignored backtest CSVs, so the nightly and a local run agree byte for
byte. After re-running those backtests, refresh it with
`python3 analysis/build_projection_table.py --refresh-wape` and commit it.

**Live test predictions are frozen.** `analysis/live_test.py` scores each new
pickup against the `oil_projections.json` the site was serving before it, so the
nightly runs it AFTER the scrape and BEFORE the projection build — swap those
steps and every pickup is scored by a file that already contains it. Never
rescore or edit old rows in `analysis/live_test_log.csv` (committed, unlike the
other analysis CSVs): they cannot be regenerated once the served files move on.
Only actual gallons are refreshed, from the scrape. `--backfill` rebuilds from
git history (first-parent `main`) and is a one-off.

## Regenerating data

```
python3 oil_scraper.py            # reuses the committed CSV — no network
python3 oil_scraper.py --rescrape # live scrape; the nightly does this, you rarely should
python3 analysis/build_projection_table.py   # after either: rebuilds oil_projections.json
```

A plain run rewrites the CSV, the Excel report and both JSONs, and is
idempotent. `.github/workflows/nightly-update.yml` runs `--rescrape` at 07:00
UTC and commits straight to `main`, so expect a data diff most mornings.

## Rules that are easy to break

**`EMPTY_QTYS = {0, 1, 2, 3}`** (`oil_scraper.py:36`). Quantities 0–3 mean
"nothing collected" — 2 is the sign-up call (the day Jim enters a new client;
nothing to do with fullness), 3 is a barrel delivery. **4 is NOT
excluded**: it is a data-entry quirk that counts as exactly 4 gallons and is
never rounded up. `validate_data.py` fails if either changes.

**Dropped from totals is not thrown away.** Since 2026-10-05 every 0–3 entry is
kept in `oil_non_pickups.csv` (merged on each `--rescrape`, like the capacity
cache). `RESET_QTYS = {0, 1}` means the truck checked and found the container
empty, so the projection restarts its oil clock at the latest one after the
last pickup — the rate itself is unchanged. 2 (sign-up call) and 3 (barrel
delivery) are kept but not used yet. The builder keeps its own copy of
`RESET_QTYS`; `validate_data.py` asserts the two match. Never put a 0–3 into
`oil_collections_raw.csv`: `backtest_steady_rate.load_pickups` refuses it.

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

**Capacities come from the source site; fix them there.** Each customer page
shows `Capacity:` and `Periodicity:`; every `--rescrape` (so every nightly)
rewrites `capacity_cache.csv` from them. Never hand-edit the cache: a wrong
capacity is corrected on the site and arrives overnight. The write is a merge —
a page that fails to load, or shows no Capacity label, keeps its old row, and
if most pages lose the label the file is left alone. Until 2026-10-01 nothing
refreshed it: the April 2026 snapshot was 47 customers out of date, including
27 with no capacity at all.

**Shared barrels: the full barrel capacity goes on EVERY member.** Some
accounts share one barrel, and each records only its own share of the gallons
(Three Penny/Namaste split 67/33 on every pickup). Convention agreed with Jim
on 2026-10-06: each member lists the WHOLE barrel's capacity on the site, and
the split lives in the names — for people; no code reads a % from a name.
`SHARED_CONTAINERS` in `analysis/build_projection_table.py` (keyed by id)
folds each group into one row under its lowest id: gallons summed by date,
capacity = the members' common value, flagged while they disagree. Never add
member capacities together — some were per-share, some full-barrel.
`HISTORY_STARTS` there restarts a renamed account's history for a new owner
(JJ's on the old Langdon Street Tavern account, 2026-09-22). Renames in either
registry are flagged on the row, never fatal, so they cannot stop the nightly.

**Projection overrides are config, keyed by id.** `MODEL_OVERRIDES` in
`analysis/build_projection_table.py` routes named customers around the 50/50
ranking (true closer, semi-closer, open-ish, event-driven, on-demand). Each
entry is keyed by customer id, asserts the customer name, and carries a
month-level season where one applies. `fringe_seasonal_candidates` informs the
registry but is never read by it: its labels are review prompts, not
classifications. Overrides change routing and which columns show, never the
50/50 arithmetic or a listed capacity. Operator `% full` is capped at 100; the
raw figure lives in the detail CSV.

**Everyone else is placed automatically by the ladder** (since 2026-10-08,
`analysis/replay_newcomers.md`): fewer than 3 pickups is insufficient (the
first pickup only starts the clock; its gallons never enter a rate); *new* =
pooled rate over at most the last 6 gaps; *established* (a previous-year rate
exists) = the 50/50; *seasonal* (passes `analysis/seasonal_open.py`, scored
from complete years before this one, shuffles seeded per customer) = half
Sarge's 70/30 (last year around the date / last 2 pickups) + half the last 3
pickups re-timed by the month index (`seasonal_formulas.md`). No growth factor:
it amplified customers whose volume had shifted (`recent_override.md`). A will-call detector (median gap > 120 d, or gap
sd/mean > 0.8 once gaps over 3× the median are dropped as closures) lists an
account without a projection. **An override always wins over the detector**,
and stale wins over will-call. Rows without their own WAPE get calibrated
range factors (`RANGE_FACTORS`), never a flat ±20%. A customer NOT in the
registry whose last 2 complete years show a closed block is a *detected
closer* (`seasonal_open.closed_months`), routed like a semi-closer; every
closer in season is ranked on its open-season gaps. **Monthly shapes keep
zero months as zero** (`seasonal_open.zero_keeping_shape`):
`cf.factor_from_years` turns an all-zero month into 1.0, which hid closures.

## Checks

```
python3 validate_data.py          # ~82 checks on the generated data
python3 check_town_mismatches.py  # town names vs the GeoJSON; exits non-zero on a real mismatch
node tests/run_all.js             # seven front-end suites; see tests/README.md
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
