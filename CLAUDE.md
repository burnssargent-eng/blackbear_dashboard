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

jamaal_routes.py ──> jamaal_routes.json   (source site's assigned routes, route names and Assign list; GET only)
jamaal_outgoing.py ──> outgoing.json      (source site's outgoing log: stillage runs, Shop→Gebbie Schmutz; GET only)
jamaal_customers.py ──> customer_details.json (address + map position per active customer; NO phones).
                     NOT committed since step 6 (2026-10-24): the nightly writes it from the database
                     (supabase_sync.py --export-details), reads only new customers, and the sync writes back.

supabase_sync.py ──> the shared database (Supabase; db/schema.sql). Nightly: source-site customers and
                     route names, the source site's whole history (every pickup, 0-3 code and outgoing run, as
                     rows with source='jamaal'; the last 90 days refreshed each night, --history redoes all), and
                     a sync_reports row on where routes and pickups differ. 'jamaal' rows and the site's own
                     'site' rows record the SAME pickups: never add them together; the pages read only 'site'.
                     Never writes a site pickup. Stops: --routes (refresh-routes.yml, started at 7am Vermont
                     time by the database's pg_cron, since GitHub schedules ran hours late) sets every source-site
                     route to Jamaal's Today's Routes; site edits stand until the next morning. --seed
                     (supabase-seed.yml, by hand, once) also copies the assigned routes into an EMPTY route_stops, with the full notes from a fresh jamaal_customers.py --refresh.
                     --sweep (Sundays, after the sync; jamaal_customers.py --sweep reads every page first): address,
                     notes and phone edits made in Jamaal later follow into the database only while ours still matches
                     Jamaal's old value (`jamaal_address`/`_notes`/`_phone`); an edit made here is kept and listed in
                     sync_reports. A customer with no phone takes Jamaal's (how new customers get one).
                     A customer taken over on the site (Add customer's "Taking over from";
                     customers.replaced_by) stays inactive whatever Jamaal says, and --routes puts its new
                     account in its place; the Ops pages hide it and show its photos on the new account.
jamaal_photos.py ──> the shared database's private `photos` bucket: the source site's uploads/ photos (GET only),
                     shrunk like the phone does (1600 px + 400 px thumb), blank caption, `photos.jamaal_file` = the
                     source file name (unique: copied once; removed on the site stays removed). First copy by hand
                     (jamaal-photos.yml: dry-run, then copy); the nightly then copies new ones (--nightly does
                     nothing before that first copy). Photos of customers not yet in the database wait a night.
supabase_sync.py --export-site-edits ──> customer_edits.csv  (committed: customer id, name, town, capacity,
                     region, themes, status, status day, set on the customer page; nightly, BEFORE the scrape). A site edit beats the source
                     site's: the scraper takes its town (regions, map, county; ahead of CITY_TOWN_BY_CUSTOMER
                     and OUT_OF_STATE_BY_CUSTOMER) and roster name, the builder its capacity and name. Pickup
                     records keep the scraped name, so MODEL_OVERRIDES' name check never trips on a rename.
                     The database keeps the source site's values in name/city/capacity and the edits in
                     site_name/site_town/site_capacity; saving the source site's own value clears an edit.
                     --place-pins (after the sync) re-pins an address edited on the site.
                     Region: a site region replaces the town's geographic region(s), site themes replace
                     Ski Slopes / Summer Snack Stops, the composites follow (oil_scraper.region_labels).
                     Status (site_status): inactive = the builder's "inactive" section, no projection, grouped
                     last on Assign and out of Urgent; closed_season = held until the usual season start
                     (MODEL_OVERRIDES or detected season), then the oil clock starts there; open = the clock
                     starts that day. A pickup after the status's day ends it (the sync clears it).
customer_eras.csv ──(oil_scraper.py, customer_eras.py)──> customer_history.json (eras + businesses)
                     └─> oil_data.json retention_by_year / retention_summary. See "Retention is
                     counted in businesses" below. The nightly also lists source-site RENAMES in
                     its sync_reports row: a possible new owner, to check against the registry.
supabase_sync.py --export-oil-left ──> oil_left.csv  (committed: customer id, day, gallons left; nightly,
                     before the projection build). Oil Left = gallons a driver saw left in a barrel and did
                     not collect; its own table, never a collection. The builder starts the barrel's level
                     from it and adds it back into the gap's rate (analysis/ROADMAP.md, Oil Left).
supabase_sync.py --export-thefts ──> thefts.csv  (committed: customer id, day, gallons collected; never the
                     note; nightly, before the build). Every theft marked on the site (My Route or the
                     customer page) joins the builder's THEFTS: its gap leaves the rate, its clock restarts.
db/schema.sql        the database's whole definition. Idempotent: change it, then paste ALL of it into
                     Supabase's SQL editor. Every table is login-only (RLS + `people`); nothing is open to the
                     public key. The secret key lives only in GitHub secrets (SUPABASE_SERVICE_KEY).

site-gate.js         the sign-in card in front of EVERY page (email + password, team list in `people`; one
                     sign-in per device until Sign out). Analytics pages load it with data-protect and stay
                     hidden until a member signs in. A screen, not a lock: the JSON files are still public.
                     Accounts: Supabase > Authentication > Users > Add user (Auto Confirm), then the `people` insert.
ops-db.js            the shadow pages' database link. Signed in, a page's store comes from the database;
                     saveStore diffs it against the last copy sent and queues the writes (kept in localStorage
                     until sent, so offline saves wait). Signed out, the page shows site-gate.js's card; only if
                     the library fails to load does it fall back to the browser copy. ?debug shows a sync log.

index.html      dashboard + Leaflet heatmap. Keeps its OWN inline styles.
region.html     per-region page. The most complex page.
year.html  town.html  customers.html  schmootz.html
projections.html     beta pickup projections. In the nav as "Projections", after Dashboard.
former.html          former customers: every ended BUSINESS (not account) with how it ended, the
                     headline retention figures, and ?b=<id> for one business's eras, months and
                     site timeline. In the nav as "Former", after Customers. Only displays
                     customer_history.json and oil_data.json's retention_summary.
planner.html         route planner beta. UNLISTED (not in the nav). Reads the two JSONs above.
ops-assign.html  ops-myroute.html  ops-calendar.html  ops-outgoing.html  ops-customer.html   SHADOW site: the source site's workflow on a copy of its
                     data, kept in each browser. Light Bootstrap look, own nav (ops-common.js),
                     ops.css. Linked from the dashboard nav as "Ops" (last; "Former" made it
                     eight on 2026-11-03, which fit a 375px phone only at 10px text under 400px).
                     Route maths for both betas: route-utils.js.
ops-photos.js        customer photos (barrel, spot, lock): private bucket `photos` + table, members only. The phone
                     shrinks each to a 1600 px JPEG + 400 px thumbnail (free plan: 1 GB, no server resizing).
                     Added/removed on ops-customer's Home tab; view-only in My Route's Notes. Remove only hides
                     (`removed_at`): a theft photo is evidence. ops-customer's Collections tab edits/deletes
                     SITE pickups of any age; Jamaal's are fixed in Jamaal and arrive overnight.
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

**The source site is read-only to us.** Jamaal belongs to a third party.
`jamaal_routes.py` and `oil_scraper.py` only GET. Never POST, and never request
an action page (`delete_routes.php`, `update_order.php`, ...): its forms load
without a login, so a stray request would change the real routes. A route
assignment stays until someone removes it, so "Today's Routes" means "currently
assigned", not today's. `planner.html` only adds exported per-customer figures:
a shared barrel counts once, and a held-out customer's average pickup is shown
as "typical", outside the projected total. Will-call and limited-data rows are
NOT held out there since 2026-11-02: their estimate is their projection.


**Addresses, notes, door codes and phone numbers never go in the repo.** They
live only in the shared database, behind the login (`customers.address`,
`customers.notes`, codes included), and the pages read them from there.
`customer_details.json` and `customer_notes.local.json` are working files the
nightly writes from the database and never commits; both are gitignored, and
`tests/ops_test.js` and `validate_data.py` fail if either is ever tracked.
Before 2026-10-24 `customer_details.json` WAS committed (addresses and cut
notes, no codes), so it is still in the public git history.

## Regenerating data

```
python3 oil_scraper.py            # reuses the committed CSV — no network
python3 oil_scraper.py --rescrape # live scrape; the nightly does this, you rarely should
python3 analysis/build_projection_table.py   # after either: rebuilds oil_projections.json
```

The nightly builds with `--today`: the file is dated today (Vermont), not the
latest pickup, which lags after a day with no pickups (Saturday 2026-10-25's
file was dated Thursday, so My Route showed a 3-day-old figure). A plain local
run dates it to the latest pickup, so it matches the nightly only on a day
that had pickups.

A plain run rewrites the CSV, the Excel report and both JSONs, and is
idempotent. `.github/workflows/nightly-update.yml` runs `--rescrape` at 07:00
UTC and commits straight to `main`, so expect a data diff most mornings.

## Rules that are easy to break

**Retention is counted in businesses, not accounts.** A source-site account is
a barrel at a place; when a restaurant closes and the next tenant keeps the
barrel, both run on one account. `customer_eras.csv` (committed; names, dates,
reasons and public links only) splits an account into eras at their `end`
dates and says how each ended: `lost` (still open, stopped using us: the only
churn), `closed`, `changed_hands` (a successor took the site and is our
customer), `moved`, `continued` (duplicate account or a partner leaving a
shared barrel), `non_recurring`. An ended era nobody classified is `unknown`,
never lost. `moved`/`continued` chain eras into one business across accounts.
Churn = lost / businesses active on 1 January (Sarge, 2026-11-03); the
"to" figure adds unknowns and moves we did not follow. Reviewed by Sarge
2026-11-03 for the slash-name handovers; the ~286 other inactive accounts are
still `unknown`. `validate_data.py` (section H) fails on a bad row, on a pickup
outside every business, and on a `HISTORY_STARTS` date that starts no era (the
projection builder still keeps its own copy; moving it onto the registry would
change ~25 customers' projections, so it is a model change, not done).

**`EMPTY_QTYS = {0, 1, 2, 3}`** (`oil_scraper.py:36`). Quantities 0–3 mean
"nothing collected" — 2 is the sign-up call (the day Jim enters a new client;
nothing to do with fullness), 3 is a barrel delivery. **4 is NOT
excluded**: it is a data-entry quirk that counts as exactly 4 gallons and is
never rounded up. `validate_data.py` fails if either changes.

**Dropped from totals is not thrown away.** Since 2026-10-05 every 0–3 entry is
kept in `oil_non_pickups.csv` (merged on each `--rescrape`, like the capacity
cache). A 1 means the truck checked and found the container empty, so the
projection restarts its oil clock at the latest `RESET_QTYS` entry after the
last pickup — the rate itself is unchanged. **A 0 is not an empty check:
Jamaal writes it itself, never a person** (Jim, 2026-10-29; one appeared when
he reactivated a customer), so `RESET_QTYS = {1}` since 2026-10-29; see the
comment at `oil_scraper.py` `RESET_QTYS`. 2 (sign-up call) and 3 (barrel
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

**Region rules are additive — except one (and the customer page's Region).** A row takes EVERY label it matches,
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

**The source site's City field is often wrong; correct it by id.** Many
customers carry City "Burlington" whatever their real town (Erin and Jim,
2026-11-01). `CITY_TOWN_BY_CUSTOMER` (`oil_scraper.py`) sets the official
town by customer id, and the county follows it; `OUT_OF_STATE_BY_CUSTOMER`
does the same for out-of-state customers (`Place, ST`, used in place of the
raw city for region matching). Ids and town names only, never an address.
Since 2026-11-02 a town picked in the customer page's Edit does the same job
without a code change (`customer_edits.csv`) and beats both registries; the
county comes from the town map's own county code when `COUNTY_MAP` lacks the
official name (eight Barre/Rutland/Saint Albans Town customers were "Unknown" until then).
Check each town against an address first (the private lookup files, the
site's page, or its map position): two guesses from names alone were wrong on
2026-11-02. The raw `city` column stays as scraped. The town to SHOW is the
roster's `town` in `oil_collections.json` (official town, else the out-of-state
place, else the city); the projection builder copies it into
`oil_projections.json`, which is where the Ops pages and the planner read a
customer's town (falling back to the source site's Assign-list city for a
customer with no projection row).

**Barre is one town in labels, two on the map.** `townLabel()`
(`dashboard-utils.js`, inline copy in `index.html`) shows Barre City and Barre
Town as "Barre" in every town list and label; `town.html?town=Barre` covers
both. The map, the data and the region config keep the official names.

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

**Capacities come from the source site; fix them there — or on the customer page.**
Since 2026-11-02 the customer page's Edit sets a capacity that beats the
source site's (`customer_edits.csv`, above); never hand-edit either CSV. Each customer page
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
(JJ's on the old Langdon Street Tavern account, 2026-09-22; Mad Moose on the
old Blue Stone account, 2026-01-01 — opened 2025-12-26). Renames in either
registry are flagged on the row, never fatal, so they cannot stop the nightly.

**Projection overrides are config, keyed by id.** `MODEL_OVERRIDES` in
`analysis/build_projection_table.py` routes named customers around the 50/50
ranking (true closer, semi-closer, open-ish, event-driven, on-demand). Each
entry is keyed by customer id, asserts the customer name, and carries a
month-level season where one applies. The customer page's Edit info can set a
projection type (will-call, seasonal with its months, event-driven; `customers.site_model`,
exported in `customer_edits.csv`, `SITE_OVERRIDES` in the builder): it REPLACES the
model's choice and any registry entry for that id (Sarge, 2026-11-12), the report
flags each replacement, and it takes effect at the next nightly. Seasonal is a
semi-closer. `fringe_seasonal_candidates` informs the
registry but is never read by it: its labels are review prompts, not
classifications. Overrides change routing and which columns show, never the
50/50 arithmetic or a listed capacity. Operator `% full` is capped at 100; the
raw figure lives in the detail CSV. Shown projected gallons and the range's
high end are capped at listed capacity too (since 2026-10-15); the uncapped
figure is `model_projected_gallons`, and the ranking and days-past columns
keep using it, so the cap changes no order. Since 2026-10-29 every projection
is the **cycle blend** (`CYCLE_BLEND`, `CYCLE_FACTOR`): rate × days leans toward
the customer's typical pickup as its usual gap nears, ×1.04. The straight line
is exported as `line_gal` and scored beside it in the live test until the
comparison is settled (`analysis/ROADMAP.md`).

**Everyone else is placed automatically by the ladder** (since 2026-10-08,
`analysis/replay_newcomers.md`): fewer than 3 pickups is *limited data* ("insufficient
data" until 2026-11-02; the live-test log keeps the old name in its frozen rows) (the
first pickup only starts the clock; its gallons never enter a rate — and a
customer back after more than 365 days counts its pickups from the return,
`COMEBACK_HOLD_DAYS`, since 2026-10-15; back after 181–365 days from a
closure that repeats an earlier year's, its 2nd and 3rd pickups back are a
*return* — same time last year, then half that and half the first rate back —
and any other such return is held, since 2026-10-18); *new* =
pooled rate over at most the last 6 gaps — also used, from the reopening on,
for a customer back after a gap longer than `STALE_DAYS` inside a rate window
(`dormant_restart`, since 2026-10-09; `dormant_restart.md`); *established* (a previous-year rate
exists) = the 50/50; *seasonal* (passes `analysis/seasonal_open.py`, scored
from complete years before this one, shuffles seeded per customer) = half
Sarge's 70/30 (last year around the date / last 2 pickups) + half the last 3
pickups re-timed by the month index (`seasonal_formulas.md`). No growth factor:
it amplified customers whose volume had shifted (`recent_override.md`). A will-call detector (median gap > 120 d, or gap
sd/mean > 0.8 once gaps over 3× the median are dropped as closures) lists an
account separately. **Will-call and limited-data rows still get a projection**
(since 2026-11-02, `held_estimate`): the same cycle blend, leaning by
s = 8 ÷ (8 + gaps in the last year) (`SPARSITY_K`, every row), one pickup ramping to
it over 44 days, a comeback's typical pickup from its whole history. It is their
`projected_gal`: ranked, on every ops page and in route totals, with the section in
grey ("limited data", "will-call"), and they are still listed in their own
section. A seasonal closer gets none while closed. **An override always wins over the detector**,
and stale wins over will-call. Rows without their own WAPE get calibrated
range factors (`RANGE_FACTORS`), never a flat ±20%. A customer NOT in the
registry whose last 2 complete years show a closed block is a *detected
closer* (`seasonal_open.closed_months`), routed like a semi-closer; every
closer in season is ranked on its open-season gaps, and a closer ranked
off-season on a recent call accrues on open-season days only (*closer
off-season*, `model_research_2026_10/reopening.md`). **Monthly shapes keep
zero months as zero** (`seasonal_open.zero_keeping_shape`):
`cf.factor_from_years` turns an all-zero month into 1.0, which hid closures.

## Checks

```
python3 validate_data.py          # ~82 checks on the generated data
python3 check_town_mismatches.py  # town names vs the GeoJSON; exits non-zero on a real mismatch
node tests/run_all.js             # seven front-end suites; see tests/README.md
```

One standing warning, a town configured ahead of its first customer:
`Northwest:Alburgh`. Expected. (`Route 7:New Haven` cleared on 2026-11-02, when
Tourterelle was moved there.)

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
