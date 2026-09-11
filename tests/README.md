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
| `seasonality_test.js` | Region seasonality profile: the 40/30/20/10 weighting, the 5/3 renormalisation for months the current year has not reached, and that the per-region projection reproduces `compute_projection` in `oil_scraper.py` exactly. Asserts shares sum to 1 and gallons sum to the projection, for all 14 regions. |
| `subline_test.js` | Region stat-card context lines: pickup counts, projection and percent, prior-month resolution including the January rollback, and active-customers-with-a-current-year-pickup. Guards against `Infinity` from a zero previous year. |
| `region_filter_test.js` | Top Producing Customers period filter: month sets per mode, picker visibility, both sorts, and that All Time reproduces the pre-filter list exactly. |
| `equiv_test.js` | Schmootz "real terms" panel: drum / pool / football-field equivalents, the block-count cap, and that the panel is omitted rather than showing zeros when the total is unusable. |
| `expand_test.js` | The shared 50/250 expandable customer list. |
| `customers_regression.js` | The `applyPeriodPickers` contract shared by the customers and region pages. |

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
