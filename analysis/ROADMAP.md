# Projections roadmap

What comes next for the pickup projections, and what is in flight. The method
and its history are in [`README.md`](README.md); read that first.

Owner: Sarge. Business direction (regions, capacities, what a route means):
Jim. Last updated 2026-10-08 — **update the "In flight" section whenever a step
finishes**, so a fresh session does not redo or skip it.

## In flight

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

## Then: the Projections page (Sarge's vision, 2026-10-01)

Today's beta page is a ranked table with held-out sections. The target:

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

## Later: route builder (parked)

A page under Projections to assemble a route: list of active customers, sort
by name / town / region / gallons / % accumulated, search, a checkbox per
customer with projected gallons summing as you go (how full the truck will
be). Sarge also wants to look at the route-entry tool on the source site
(Jamaal's) — combining "My Route" and "Today's Routes", phone-friendly
reordering instead of finicky drag and drop, and each route showing capacity,
expected gallons (e.g. 15/45, 33%) and days until the 75% pickup point.
**Parked until the two phases above are done.**
