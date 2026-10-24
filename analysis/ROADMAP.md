# Projections roadmap

What comes next for the pickup projections, and what is in flight. The method
and its history are in [`README.md`](README.md); read that first.

Owner: Sarge. Business direction (regions, capacities, what a route means):
Jim. Last updated 2026-10-17 — **update the "In flight" section whenever a step
finishes**, so a fresh session does not redo or skip it.

## In flight

### Route planner beta (started 2026-10-17)

The parked route builder (below), started as Milestone 1 of the operations
platform plan. Jamaal belongs to a third party and is hard to change, so it stays
the system of record. **We only read from it.**

- `planner.html` is unlisted: not in the nav until Jim has seen it.
- `jamaal_routes.py` GETs `routes_assigned.php` into `jamaal_routes.json`. It
  runs nightly and on demand via the `Refresh Assigned Routes` workflow.
- The planner scores each site route and drafts routes in the browser.
  Reordering is by arrows or typed stop numbers. Truck fill is shown against
  `TRUCKS`.
- Tank capacities set (Sarge, 2026-10-18): Mamma Bear 1,350, Mack Daddy 2,000,
  Baby Bird 750, Newbie Doo (the new truck) 1,200.
- **Open:** whether a truck is tied to a route.
- **Then Jim:** route names, and whether the totals help.
- **Next (Phase 2):** a Ms. Lucy (`dayssince.php`) vs model comparison, and a
  route-level range calibrated from the live test log.

### Shadow operations site (started 2026-10-18)

This is the first step towards replacing Jamaal. The source site's workflow
runs on a copy of its data, and every change stays in the viewer's browser.

- Target nav: Assign Routes · My Route · Calendar · Outgoing · Dashboard ·
  Projections · Historical Totals (Regions, Years, Customers) · Schmutz.
- Dropped: Ms. Lucy, Timeclock and Home.
- Built (checkpoint 1):
  - `ops-assign.html`: the list by town or by urgency, region filter, a
    running total of ticked customers, route before/after against its truck,
    and Add customer.
  - `ops-myroute.html`: My Route and Today's Routes in one page, with arrows,
    typed stop numbers, Must Get, remove, Add stop from search, and a driver
    view for gallons.
- Built (checkpoint 2): `ops-calendar.html`.
  - The source site's month grid. Its latest day doubles as Today's Totals.
  - A day's total counts gallons only; the 0-3 codes are listed in the day by
    meaning. The source site adds them in, so it reads a few gallons higher.
  - Header as on the source site: two green buttons a month either side
    (09-18-26 | 11-18-26), and "This month / Last year" (the full same month).
  - Adds a month picker (10/26 back to 01/15).
- Built (checkpoint 3): `ops-outgoing.html`.
  - The source site's Outgoing page and its report in one page: a stillage
    button per truck (Newbie Doo included), Schmutz volume, and the month
    report.
  - History from `outgoing.json` (`jamaal_outgoing.py`, Jul 2021 on; the
    nightly re-reads the last two months).
- Built (checkpoint 4, 2026-10-18):
  - `ops-customer.html`: the customer page, with Add Collection, details,
    projection, Assign, a map pin and history. No phone.
  - The My Route driver view now matches the source site's My Route: linked
    names with capacity and proj. gal, "Total:", and a numbered pin map.
    Positions come from `customer_details.json`: the site's own when stored,
    else the US Census geocoder, else the town centre.
  - Label rule: one hedge only, "~", with figures beside the name as on the
    source site. "Name (~87 gal / 300) [28]", where [28] is days past full
    (negative = to full), from the new `days_past_full` export. % full only
    sets the colour; it is not shown as a column. Only the route table's
    total row is written out, in bold: "Total projected gal: 500 / 1000".
  - The driver view enters gallons inline again (box, Save, Empty), with a
    Details dropdown: address and directions, notes, barrel photos (shown
    from the source site's uploads), the last 3 pickups, and a link to the
    full customer page.
- Live route totals (2026-10-19): on My Route, a stop collected today counts
  its actual gallons in place of its projection (y + z → x + z), in the
  collecting header, the route's line and its total row. The truck bar shows
  collected gallons solid and the projected remainder light.
- Collecting page rebuilt for phones (2026-10-19, from the stop-layout mock):
  - One line per stop: the name, then "(~87 gal / 300) [28]", with no stop
    numbers. Compact (on by default) shows controls on one stop only: the
    next to collect, or whichever line is tapped. Save moves on; switching
    stops closes Details.
  - Controls are one 33px row: gallons, Save, Maps, Details. Empty is gone;
    typing 1-3 means empty, as on the source site.
  - Details shows the address (a Maps link), notes, and "Customer page ›".
    The name itself is not a link.
  - Collected stops drop below the rest. The tally ("Total: x of y projected
    gal") and the truck bar sit under the list. The shadow banner is one line.
  - About 11 stops fit at 375px.
  - Door codes and phones: `customer_notes.local.json` holds the full notes
    for a local preview only. It is gitignored, and a test fails if it is ever
    tracked. The published file keeps them cut out, and the page links "on
    Jamaal ›" in their place. Getting codes to drivers on the live site still
    needs encryption, a login-protected host, or the database.
- **Open:** the source site's My Route lists the stops in a different order
  from Today's Routes; ask which order drivers follow.
- **Schmutz check for Jim / Erin.** Jamaal's Schmutz is Shop → Gebbie only.
  Its yearly totals are close to, but not equal to, the Schmootz workbook's
  Shop → Gebbie block (2026-10-18):

  | Year | Jamaal | Workbook |
  |---|---|---|
  | 2023 | 27,300 | 24,300 |
  | 2024 | 47,750 | 46,850 |
  | 2025 | 45,575 | 45,075 |
  | 2026 | 29,000 | 21,550 (workbook last saved Sep 10) |

  Barr Hill → Gebbie is only in Erin's spreadsheet.
- Route names differ between the source site's pages. Ask Jim:
  - id 4 is "MamaBear" in the Assign dropdown but "Stowe/Wtbry/MRV" on
    Today's Routes;
  - id 15 appears twice.

### Shared database (Supabase, started 2026-10-21)

The shadow site's copy moves from each browser into one database, so Sarge and
Jim see the same routes and gallons. Both systems are sources for now (Sarge,
2026-10-21): routes are copied in once, then edited on the site; gallons are
entered in both, and projections still read only the source site's, so nothing
counts twice. Logins: Sarge (added 2026-10-24); Jim to be invited.

- **Done:** project `blackbear-ops`; `db/schema.sql` run (tables, login-only
  rules, theft fields and history, photos bucket); public sign-up off; secret
  key in GitHub secrets.
- **Step 3, done (#54):** `supabase_sync.py`. The seed (2026-10-24) copied
  572 customers with full notes, 15 routes and 35 stops; the nightly keeps
  customers and route names current and writes a `sync_reports` row listing
  route and pickup differences. It never overwrites a route or a pickup.
- **Step 4 (this branch):** `ops-db.js`. Sign-in on the shadow pages (emailed
  link, no sign-up); signed in, every page reads and saves to the database
  with live updates, and saves made offline wait in a queue (most of step 5).
  Sarge signed in 2026-10-24. Signed out, the browser copy stays.
- **Then:** addresses and notes out of the public repo (step 6); thefts from
  `theft_log` into `THEFTS`; show `sync_reports` on the pages; invite Jim.

### Theft registry (2026-10-23)

`THEFTS` in the builder: a confirmed theft leaves its gap out of every rate and
restarts the clock (README decision log, 2026-10-23).
Farmhouse (325, 2026-10-21) is the only entry; it changes a rate from its next
pickup on.

- **Next:** thefts marked on the shadow site reach the registry through the
  shared database (Supabase plan), instead of by hand; then the theft
  calculator (gallons only, no dollar figure), reading the `theft_log` view.
- **Shelved (Sarge, 2026-10-23):** a review list of past qty-1s for Jim. The
  focus is marking thefts from now on.

### Comeback hold (2026-10-15)

Customers back after more than a year without a pickup are insufficient data
until 3 pickups since the return (7 ranked rows today). Re-test a 2-pickup
hold in 2027, once its 2024–26 win (17 pickups) has more behind it.

### Return stage, seasonal reopenings after 181–365 days (2026-10-18)

A reopening's 2nd and 3rd pickups back are projected from the same time last
year (then half that, half the first rate back); any other 181–365-day return
is held as insufficient data (`comeback_level_shift_research/return_guards.md`,
V2). 5 return rows and 15 held today. Sugarbush Resort Golf Course and Enosburgh
Falls CC, golf courses under the detector's 300 gal/yr floor, were made Jun–Oct
true closers on 2026-10-18. Other small seasonal accounts may need the same:
the detector cannot see them. **Watch the *return* stage in the live test through the 2027
spring reopenings**: dev years alone were inconclusive, 2021 was worse, and
the reopening split was chosen after seeing 2024–26. Also watch what the 15
held rows do — the hold hides some real stops (the 50/50 caught 102 of 758
pickup-weeks there). The reopening test misses seasonal accounts whose earlier
closures don't line up month-for-month (Enosburgh Falls CC, now a registry
closer). Still worth
asking Jim how the first visit of a season is timed. From the same study, not
adopted: open-day exposure (A1) and a smoothed level for the 50/50 (C4) —
re-test C4 on 2027 pickups with D frozen at 90.

### Projection capped at capacity (2026-10-15)

Shown projected gallons and the range's high end are capped at listed
capacity; ranking and days-past columns are not. Watch in the live test
whether capped rows come in near capacity or well under it (the backtest says
under: capped over-capacity rows still ran ~47% high).

### Off-season calls on open days (2026-10-11)

Closers ranked off-season on a recent call now accrue oil on open-season days
only (stage *closer off-season*). Two rows today (Jay Peak Hotel Jay, Okemo
Jackson Gore Inn); more as closers go off-season. Watch the off-season
rows in the live test this winter. From the same study
(`model_research_2026_10/model_recommendations.md`), still open:
**ask Jim when the closers actually open and close, and how the first visit of
a season is timed** (month-level seasons over-project the first pickup by
~94%), and **re-test a faster-decaying rate for the new stage in 2027**.

### Dormant-gap restart (2026-10-09)

Customers back after a gap > 180 days are projected from the reopening only
(`dormant_restart.md`). 26 ranked customers moved; several are summer-only
(Beach House North Beach, Bravo Zulu, food trucks, golf clubs) and will keep
accumulating after closing until stale. **Sarge to decide which belong in
`MODEL_OVERRIDES` as closers.** Watch them in the live test.

### Live test (started 2026-10-09)

The nightly scores every new pickup against the projection served before it
(`analysis/live_test.py`; Live test section at the bottom of the Projections
page). Backfilled to 2026-09-21. Watch: the net proj − actual (−740 gal on
12,101 collected so far, −6%: under-projecting), and stages once more post-ladder pickups arrive —
only 10 so far. Early rows were projected by models before the ladder.

### Capacity cleanup (started 2026-09-29)

Goal: every listed capacity on the source site matches the real container, so
% full and urgency mean something.

1. ~~Nightly refreshes capacities from the site~~ — done, PR #25 (2026-10-01).
2. ~~Review worksheet of customers whose pickups exceeded capacity~~ — done.
3. ~~Jim's numbers received~~ — `analysis/jim_capacities.csv`, 153 rows
   (2026-10-05). Checked against the live site: 41 already matched, **103 to
   enter, 9 to confirm with Jim first**.
4. **Sarge enters the 103 on the source site** from
   `analysis/capacity_entry_checklist.csv` (has a direct link per customer).
5. **Confirm the 9 with Jim** — rows marked `CONFIRM WITH JIM FIRST` in the
   checklist: his value is below several real pickups, or reverses an increase
   someone made on the site since April (Vermont Country Club-Waterbury, Essex
   House, Henrys Diner, Sante, Friendly Toast, Mountain Valley-Winooski, Three
   Penny, 802 Deli, Tourterelle).
6. **Verify**: re-fetch the 153 customers' pages (read-only, `parse_capacity`
   in `oil_scraper.py`) and diff against `jim_capacities.csv`. List anything
   missed or mistyped.
7. The next nightly pulls the new capacities and rebuilds the projections.

Background worth knowing: a pickup above capacity was checked against the
barrel-delivery records (quantity 3, dropped by the scraper but visible on the
site). Measured on 2026-10-01 against the April capacities: only 10 of 276
over-capacity pickups had a delivery since the previous pickup, so extra barrels explain almost none of them.

### Local worksheets (gitignored, this machine only)

| File | What it is |
|---|---|
| `analysis/capacity_review.csv` | The review list Jim worked from: groups A–D, B1–B4, S, R, with `my_guess` (Sarge's own guesses — never overwrite) and a blank `new_capacity` |
| `analysis/jim_capacities.csv` | Jim's answers: group, customer_id, customer, town, new_capacity |
| `analysis/capacity_entry_checklist.csv` | What to type on the site, ENTER vs CONFIRM WITH JIM FIRST |
| `capacity_review.csv` at the repo root | A **Numbers** document saved with a `.csv` name. Superseded; safe to delete. Never commit it |

Numbers saves its own format even under a `.csv` name. Use **File → Export
To → CSV**, or read one with `osascript` (`export d … as CSV`).

### Shared barrels (started 2026-10-06)

Convention: every member of a shared barrel lists the **full** barrel capacity
on the site. `SHARED_CONTAINERS` in the builder shows each barrel as one row.

1. ~~Sizes confirmed and set on the site for all 15 groups~~ (Sarge,
   2026-10-06; verified read-only the same day, every member agrees).
2. **Pitchers Inn / Warren Store are NOT grouped**: two 65-gal barrels in one
   shed, pumped together, total split evenly. Kept as two stops; Sarge is
   asking Jim.
3. **Pingala**'s 100 is its whole barrel now that Volcano is gone.
4. Answered 2026-10-07: **Farmhouse/Ken's Pizza** share a parking lot but
   not a barrel — separate. **Agave** looks inactive and was never shared
   with Grazers. **Fox Farm/Suicide 6 DO share** (Fox Farm dumps at Suicide
   6, ~30 gal/yr) — not grouped yet: Suicide 6 (817) is a true-closer
   override, and `_check_registries` forbids an id in both registries.
   Decide whether the group takes the override.
5. Duo/Tulip (300 gal, Brattleboro) shows as stale: no pickup since
   2026-03-30. Still a customer?

### Open questions on the other non-pickup codes

`oil_non_pickups.csv` now keeps every 0–3 entry. Two are unused:

- **3 = barrel delivery.** Counting from a delivery under-projects the next
  pickup by 34%; from the previous pickup it over-projects by 213% (81
  cases). So a delivery is partly a reset — perhaps a full container swapped
  out, perhaps extra capacity. Ask Jim what a delivery usually means.
- **2 = sign-up call** (Jim, 2026-10-08): the day a new client is entered
  in the system. Says nothing about fullness — not a signal.

### Small fixes

- `tests/seasonality_test.js` fails on `main` since 2026-10-01: its fixed
  expected numbers (Southern Ski Slopes 7,217 gal) drifted with the nightly
  data. Update the expectations; it is not a regression.
- `Okemo-Jackson Gore Inn` prints a season-mismatch note on every build: its
  configured Dec–Apr comes from pickup months, the inferred Oct–Mar from the
  index shape. Expected.

## Next: newcomer replay (Phase 3, started 2026-10-08)

Phase 2 is done (`phase2_rate_models.md`, README decision log 2026-10-07):
level × month index wins for seasonal-open customers, the recency gate is
rejected. Its adoption in the builder is pending.

The goal: classify and project customers **automatically** as they join (~60
a year; the median newcomer has 5 pickups in its first 12 months), with no
manual survey. Design agreed with Sarge 2026-10-08:

| Stage | When | Rate | Page |
|---|---|---|---|
| Insufficient | 1–2 pickups | – | last pickup X days ago |
| New | ≥ 3 pickups | pooled rate since the first pickup (≤ last 6 gaps) | wide band, "new" label |
| Established | ≥ 1 year of history | 50/50 | normal band |
| Seasonal | passes the seasonal-open test | level × month index | seasonal band |
| Will-call | lumpy / infrequent detector | none, ever | last pickup X days ago |

- The **first pickup is a starting point only** — never a rate (oil may
  predate the barrel; deliveries are logged only sometimes).
- **Lean high, never inflate:** an unbiased estimate plus a calibrated
  ~80th-percentile band for "as full as" / "hits 75% as early as". Both
  key metrics: % full now and date reaching 75%.
- Site periodicity is Jim's sign-up guess, rarely updated; 180/365 usually
  means will-call. Used to check the detector, not as an input.
- `backtest_replay_newcomers.py` replays every customer starting 2021+ from
  its first pickup (choose 2021–23, confirm 2024–26) to set the promotion
  thresholds, band widths and the will-call rule. Learned shapes and
  credibility blending (Phase 3b) only if the year-one gap proves costly.
  Route / truck-tank totals deferred to the route builder.

**Replay done 2026-10-08** (`replay_newcomers.md`; README decision log). The
ladder holds; bands calibrated; will-call rule made season-aware (25 ranked
moved instead of 62). **Builder change built 2026-10-08** on `builder-ladder`
(stages, will-call section, calibrated ranges), merged as PR #28.
**Seasonal-closed detector built 2026-10-08** (`seasonal_closed.md`): 8
detected closers, open-season rate for every in-season closer, and the
zero-month shape fix. Seasonal stage now ½ × 70/30 + ½ × last 3 ÷ index (growth tried and
removed; frequency override rejected — `recent_override.md`)
(`seasonal_formulas.md`). Open: Jim's word on Texas Roadhouse, Mad Taco Middlebury (both Nov–Dec "closures") and
Grand Summit (May–Jul).

## Then: the Projections page (Sarge's vision, 2026-10-01)

Today's beta page is a ranked table with held-out sections. Done 2026-10-08:
total projected oil and the count past 75% for All regions and each region,
the urgent ranking with sortable columns, search, implied periodicity and an
all-history avg collection exported. Still open below: the raw-oil ranking with
the gallons / % toggle, the colour scale, collecting vs idle, and the rest of
the per-customer columns. The target:

**Regions view**
- Total projected oil per region
- Number of stops at ~75% / urgent
- Ranking of the most urgent stops
- Ranking of the most raw oil accumulated, with a toggle between gallons and %
  of capacity

**Customers view**
- Total projected oil across the state
- Count at 75% / urgent, coloured on a scale: green near 0%, yellow at 50%,
  increasing reds toward and past 75%
- The same two rankings and the same toggle
- **Collecting vs idle** (off for the season) in place of active/inactive,
  sortable

**Per-customer columns** (order still open): avg collection, collections/yr,
oil rate (gal/day), current projected gal (± range), capacity, % full, days
until and since pickup, implied periodicity, current month index, top month
(index), bottom month (index), prev-year rate, last-6 rate, projected rate,
region (so urgent stops show how they group by route).

Most of these fields already exist in `oil_projection_table_detail.csv`; the
page work is mostly exporting them into `oil_projections.json` and building
the views. The page must keep displaying, never recomputing.

## Later: route builder (parked; now In flight as the route planner beta)

A page under Projections to assemble a route: list of active customers, sort
by name / town / region / gallons / % accumulated, search, a checkbox per
customer with projected gallons summing as you go (how full the truck will
be). Sarge also wants to look at the route-entry tool on the source site
(Jamaal's) — combining "My Route" and "Today's Routes", phone-friendly
reordering instead of finicky drag and drop, and each route showing capacity,
expected gallons (e.g. 15/45, 33%) and days until the 75% pickup point.
**Parked until the two phases above are done.**
