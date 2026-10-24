# Front-end tests

`validate_data.py` checks the generated data. These check the **pages that
render it** — the arithmetic and the markup that `validate_data.py` never sees.

```
node tests/run_all.js          # everything
node tests/seasonality_test.js # one suite
```

Node only, no dependencies, no install. They read the committed
`oil_data.json` / `oil_collections.json`, so they test against real numbers.

## Why they are worth keeping

Each suite pulls the **real** functions and template strings out of the `.html`
files and evaluates them — it does not re-implement the logic. So when a page
changes, the test breaks, which is the point. The pattern is:

```js
const html = fs.readFileSync("region.html", "utf8");
const script = html.slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>"));
const api = new Function("document", "Chart", utils + script + "return { ... };")(...)
```

A minimal DOM shim stands in for the browser, which is enough for maths and
markup. It is **not** enough for layout — a test passing says nothing about how
the page looks. Every layout problem found in this project was found by opening
a browser, not by these.

## The suites

| Suite | Covers |
|---|---|
| `seasonality_test.js` | Region seasonality profile: the 40/30/20/10 weighting, the 5/3 renormalisation for months the current year has not reached, and that the per-region projection reproduces `compute_projection` in `oil_scraper.py` exactly. Asserts shares sum to 1 and gallons sum to the projection, for every region. |
| `subline_test.js` | Region stat-card context lines: pickup counts, projection and percent, prior-month resolution including the January rollback, and active-customers-with-a-current-year-pickup. Guards against `Infinity` from a zero previous year. |
| `region_filter_test.js` | Top Producing Customers period filter: month sets per mode, picker visibility, both sorts, and that All Time reproduces the pre-filter list exactly. |
| `expand_test.js` | The shared 50/250 expandable customer list. |
| `customers_regression.js` | The `applyPeriodPickers` contract shared by the customers and region pages. |
| `customers_table_test.js` | Customers page table: Region column (one non-theme region per customer, Perrigo as Perrigo), column sorting both ways with `#` restoring the preset order, the preset buttons, and the search. |
| `planner_test.js` | Route planner beta (`planner.html` + `route-utils.js`): route totals add a shared barrel once (gallons and capacity), held-out stops stay out of the projected total, each source-site route's total matches a hand sum, reordering by arrows and typed stop numbers, truck fill, and that the page never calls the source site. |
| `ops_test.js` | Shadow operations site (`ops-*.html`, `ops-common.js`): the shadow copy starts as the source site's routes stop for stop, Assign skips duplicates, route ids are named as Today's Routes names them, the nav, the calendar's day and month totals (no 0-3 codes), its 09-18-26-style month buttons and month picker, Oil Left never counting as collected, the Outgoing report (run counts, truck labels, every logged truck known), customer details (positions in the region, no phone numbers), Add Collection tying an entry to its My Route stop, and that no page submits to the source site. |
| `ops_db_test.js` | Shared database on the shadow pages (`ops-db.js`, `site-gate.js`), no network: the sign-in card (email and password, password-manager fields; "not on the team list" with Sign out), without the library every page keeps its browser copy, the queue is sent after an empty flush and after going offline, a store built from database rows reads back as the same routes (stop order, assignment ids, new routes and customers, latest truck, thefts), and each page action becomes exactly the right writes: a reorder or Assign sends that route once, a second same-day save is an edit, Undo deletes, a theft is a flagged pickup, Oil Left is its own row (set, replaced the same day, removed; never a pickup), a truck choice is today's, a new route and customer go before the stop that points at them. |
| `projections_test.js` | Beta projections page against `oil_projections.json`: renders the export's order by default, column sorting (projected gallons, # back to default), search, Show all, the five header boxes, % full never above 100, held-out sections show no fill figures, the region filter, the stale-build warning, and that Perrigo, Okemo, Champlain Valley Expo and Spruce Peak land in their override sections. |

`oil_left_test.py` is Python too: `python3 tests/oil_left_test.py`. It runs the
projection builder's Oil Left rules on a made-up steady customer: a part pickup
plus what it left reads as the full rate, a check starts the barrel at what was
left (on a pickup day or between pickups), a later empty check wins, a level
above capacity starts at capacity, and shared barrels and new owners map like
the other registries.

`supabase_sync_test.py` is Python, so `run_all.js` does not run it: run it with
`python3 tests/supabase_sync_test.py`. It runs `supabase_sync.py` against the
committed data files and a stand-in database (no network, no key): the seed
copies every customer and the assigned routes in order, a second seed never
writes over the site's routes, the nightly never overwrites notes or writes
stops, the route and pickup comparisons, and the details export (codes cut
from the details file, full notes kept apart, site-made customers left out; an
empty database exports nothing and a missing details file stops the sync), and the source site's history: one row per pickup and code with keys
stable from night to night (two pickups on one day included), every run's
truck accepted, history-only customers added as not active, the first run
copying everything, the nightly sending only the last 90 days, removing a
pickup gone from the source site and never touching the site's own entries),
and the Oil Left export (id, day and gallons only).

## Adding one

Anchor the working directory first, so the suite runs from anywhere:

```js
process.chdir(require("path").join(__dirname, ".."));
```

Then exit non-zero on failure — `run_all.js` relies on the exit code.

## Note on the fixtures

Several suites assert exact figures (Southern Ski Slopes projecting 7,217
gallons, 43,754 drums). Those move when the nightly scrape adds data. A failure
there is a **stale expectation, not a regression** — check the number changed
for a reason, then update it.
