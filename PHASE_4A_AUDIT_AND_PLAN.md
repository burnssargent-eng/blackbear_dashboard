# Phase 4A — Audit and Implementation Plan

_Audit only. No code changes in Phase 4A. Nothing committed or pushed._

---

## Context

Phases 1–3 and the Phase 4 detail pages are committed on `dashboard-updates`
(`8b22e95`). Phase 4A is an audit only: confirm the real data rules before
building branding changes, projections, lifecycle metrics and the Schmootz page.

The audit was prompted by uncertainty about the counted-gallons exclusion rule
(notes suggested `{0,1,3}`, memory suggested `{1,2,3}`) and by lifecycle metrics
that appeared to need service events the pipeline might be discarding.

**Both questions are now resolved, and neither requires a rescrape.**

---

## 1. Audit findings

| # | Question | Finding |
|---|---|---|
| 1 | Exact exclusion rule | **`EMPTY_QTYS = {0, 1, 2, 3}`** — `oil_scraper.py:31`. Matches neither prior guess. |
| 2 | Impact of excluding 2 | **Zero.** Already excluded. |
| 3 | Impact of excluding 4 | 36 rows, 144 gallons, **0.0069%** of all gallons. **Not being excluded.** |
| 4 | Are 0/1/2/3 events retained anywhere? | **No.** Min gallons is 4 in `oil_collections.json`, `oil_collections_raw.csv` and the Excel report. Zero rows below 4. |
| 5 | `is_active` populated? | **Yes, zero nulls.** 22,175 true / 4,714 false → **515 active / 277 inactive** customers. |
| 6 | Customer→region membership available? | **Derivable and verified exact.** |
| 7 | `Schmootz.xlsx` at `data/`? | **No** — repo root, untracked, 4.6 MB. |
| 8 | Detail pages present? | **Yes** — `town/year/region/customers.html`, `dashboard-utils.js`, `dashboard.css`, committed at `8b22e95`. |
| 9 | Homepage nav present? | **Yes**, order is `Dashboard \| Top Customers \| Years \| Regions`. Needs reorder + Schmootz. |
| 10 | Projection computable in Python? | **Yes**, from `monthly_totals` + `yearly_totals`. Recommended. |

### Where the 0/1/2/3 rows are lost

Two filter points, and the CSV is written *after* both:

- `oil_scraper.py:461` — `make_record()` returns `None` for `qty in EMPTY_QTYS`,
  so the row is never created at scrape time.
- `oil_scraper.py:320` — `prepare_dataframe()` filters again on CSV reload.
- `oil_scraper.py:651` — `build_reports()` writes the CSV from the already
  filtered frame.

**This no longer matters** (see §2), but it is recorded so nobody re-derives it.

---

## 2. Confirmed business rules

Per Jim's clarification:

- `2` = customer call received / entered in system → **operational event, not a pickup**
- `3` = barrel/tote delivery → **operational event, not a pickup**
- `4` = data-entry typo, but **counted as 4 gallons exactly**

Decisions locked:

- **Keep `EMPTY_QTYS = {0, 1, 2, 3}` unchanged.**
- **Do not exclude 4. Do not round 4 to 5. 4 counts as 4 gallons.**
- Counted-gallons behavior already satisfies this — **no scraper change needed**
  and **headline totals do not move** (`all_time_total` stays **2,092,880**).
- **Ignore the New Leads column** for now.
- **Do not use 2s as gained customers.**

### The key consequence

> **qualifying pickup = a counted oil pickup with gallons ≥ 4**

Every retained record already has `gallons >= 4` (verified: min = 4). Therefore
**`oil_collections.json` *is* the qualifying-pickup dataset**, and every
lifecycle metric is computable from data on disk today.

**No rescrape is required for lifecycle under the final definition.**

---

## 3. Data fields available

`oil_data.json` (0.95 MB) — 15 fields:
`last_updated`, `monthly_by_town`, `monthly_by_county`, `yearly_totals`,
`all_time_total`, `current_year_total`, `customer_count`, `unmapped_total`,
`monthly_totals`, `monthly_by_region`, `yearly_by_region`, `region_names` (13),
`collections_file`, `collections_count`, `out_of_state_total`.

`oil_collections.json` (5.2 MB, 26,889 records) — per record:
`customer_id`, `name`, `city` (raw), `geo_town` (normalized), `county`, `date`,
`year`, `month`, `gallons`, `is_active`.

Consistency verified: `is_active` and `geo_town` are constant per customer
(0 of 792 mixed), so customer status and home town are unambiguous.

---

## 4. Lifecycle definitions (final)

Labels must read **"first qualifying pickup"** and **"last qualifying pickup"**,
never "first pickup"/"last pickup".

```
DORMANT_CUTOFF = "2025-01-01"     # fixed for now; named constant, easy to change
```

- **Places serviced in a year** — unique customers with ≥1 qualifying pickup in
  that calendar year.
- **Gained in a year** — the customer's **first** qualifying pickup falls in that year.
- **Lost in a year** — the customer's **final** qualifying pickup falls in that year
  **and** the customer is either
  (a) source-site **inactive**, or
  (b) source-site **active but dormant** — no qualifying pickup since `DORMANT_CUTOFF`.
- **Net** = Gained − Lost.

### Computed today (fixed 2025-01-01 cutoff, data through 2026-07-24)

| Year | Places Serviced | Gained | Lost | Net |
|---|---|---|---|---|
| 2015 | 136 | 136 | 1 | +135 |
| 2016 | 190 | 60 | 17 | +43 |
| 2017 | 215 | 52 | 18 | +34 |
| 2018 | 288 | 100 | 36 | +64 |
| 2019 | 299 | 56 | 30 | +26 |
| 2020 | 292 | 42 | 33 | +9 |
| 2021 | 330 | 55 | 28 | +27 |
| 2022 | 397 | 85 | 25 | +60 |
| 2023 | 431 | 56 | 40 | +16 |
| 2024 | 451 | 60 | 46 | +14 |
| 2025 | 476 | 60 | 18 | +42 |
| 2026 | 441 | 30 | 7 | +23 |
| **All** | **792** | **792** | **299** | **+493** |

Lost total 299 = **277 source-site inactive** + **22 active-but-dormant**.

**Boundary artifact:** 2015 Gained = 136 because 2015 is the first year of data —
every pre-existing customer's first *recorded* qualifying pickup lands there. It
is not 136 genuinely new customers. The 2015 row must be annotated on-page.

---

## 5. Projection formula

Month-proration against the previous year, anchored to the **latest data date**,
never today's real date, so a lagging scrape cannot skew the result.

```
previousProratedYTD  = previousThroughPriorMonth
                       + (dayOfMonth / daysInMonth) * previousSameMonthTotal
projectedCurrentYear = previousYearFull / previousProratedYTD * currentYTD
percentVsPreviousYear = projectedCurrentYear / previousYearFull - 1
```

All inputs are counted gallons. Verified against current data:

| Term | Value |
|---|---|
| `latestDataDate` | 2026-07-24 (day 24 of 31) |
| `currentYTD` (2026) | 174,113 |
| `previousYearFull` (2025) | 269,909 |
| `previousThroughPriorMonth` (Jan–Jun 2025) | 135,570 |
| `previousSameMonthTotal` (Jul 2025) | 22,672 |
| `previousProratedYTD` | 153,122.5 |
| **`projectedCurrentYear`** | **306,909** |
| **`percentVsPreviousYear`** | **+13.7%** |

Computed in the Python export and stored as compact scalars in `oil_data.json`
so it is a single source of truth and `validate_data.py` can assert it.

---

## 6. Region top-25 feasibility — confirmed

`CUSTOM_REGIONS` matchers (`oil_scraper.py:328-344`) reference only
`r["city"]`, `r["customer_id"]`, `r["name"]` — **all three exist on every
`oil_collections.json` record**, so membership is fully derivable.

Verified exact against the existing aggregate export:
derived Stowe total **220,401** = `oil_data.json` Stowe **220,401**.

Customers per region: Burlington 205, Central Vermont 84, Stowe 45,
Warren+Waitsfield 30, Winooski 24, Middlebury+Vergennes 24, Waterbury 19,
Woodstock+Quechee 19, Jay+Montgomery+Troy 14, Mt. Snow 6, UVM 5, Manchester 5,
Okemo 2.

**Regions remain non-exclusive.** A customer matching two regions appears in
both. Region totals are not additive. Preserve existing Excel behavior; do not
make regions exclusive.

Recommendation: export membership from Python (single source of truth) rather
than re-implementing the matchers in JavaScript, where they could silently drift
from the Excel report.

---

## 7. Schmootz workbook status

- **Location: repo root — `Schmootz.xlsx`, not `data/`. Untracked. 4.6 MB.**
- One sheet, `Schmootz`, with two usable blocks (rows 3–16):

| Block | Cells | Years | Yearly totals |
|---|---|---|---|
| Barr Hill → Gebbie | `B3:K16` | 2020–2026 | 270,300 / 376,050 / 352,230 / 336,900 / 364,300 / 277,950 / 156,938 (partial) |
| BBB Shop → Gebbie | `M3:S16` | 2023–2026 | 24,300 / 46,850 / 45,075 / 17,250 (partial) |

All-time: Barr Hill **2,134,668**, BBB Shop **133,475**.

Notes: `-` is used as a no-data marker (string, not a number) and must be
coerced. The rest of the sheet (cols V–AR, rows 39–107) is derived/scratch
working and should be ignored. `max_row` reports 50,502 but real data ends
around row 107.

**Recommendation for 4D:** move to `data/Schmootz.xlsx`, gitignore it, and add
`export_schmootz.py` producing a ~3 KB committed `schmootz.json`. This keeps a
4.6 MB binary — which grows a new copy in git history on every re-save — out of
the repo, and matches the existing `oil_data.json` pattern. No reason found not
to do this.

---

## 8. Implementation plan

### Phase 4B — branding, navigation, homepage stats *(no lifecycle)*

1. **Rename visible text** across all five pages: brand → **Black Bear Biodiesel**,
   product → **Oil Collection Analytics**. `<title>`, `<h1>`, `.subtitle`, `alt`
   text, and the `document.title` templates in each detail page.
   **Keep `logo.png` exactly as-is.**
2. **Nav reorder** to `Dashboard | Regions | Customers | Years | Schmootz` in
   `index.html` and `navHtml()` in `dashboard-utils.js`. "Top Customers" → "Customers".
   Schmootz link present but inert until 4D.
3. **Detail-page header treatment** — page name more central and slightly larger.
4. **Homepage "This Year" card** gains projected gallons and % vs previous full
   year, from new `oil_data.json` scalars.
5. **Homepage "Customers" card** gains active count (source-site `is_active`).
6. **`region.html`** — replace the 4th stat "Months With Data" with **active
   customers in region**.
7. **`year.html`** — monthly totals chart becomes a **bar** chart with hover
   tooltips, styled like the homepage yearly chart, **no click-through**.
8. **`oil_scraper.py` `export_json()`** — add projection scalars and per-region
   active/customer counts. Regenerated by `python3 oil_scraper.py` with **no
   `--rescrape`** (~3 s, no network).
9. **`validate_data.py`** — assert projection inputs and that
   `min(gallons) >= 4`.

### Phase 4C — lifecycle metrics and region customers

1. **Export lifecycle** from Python: per customer, first/last qualifying pickup,
   status, derived region list; per year, serviced/gained/lost/net.
   `DORMANT_CUTOFF = "2025-01-01"` as a documented named constant.
2. **`customers.html`** — add customers-serviced-by-year, gained, lost, net, and
   the summary table `Year | Places Serviced | Gained | Lost | Net`. Annotate the
   2015 boundary artifact.
3. **`year.html`** — gained list (name, city, first qualifying pickup) and lost
   list (name, city, last qualifying pickup).
4. **`region.html`** — top 25 producing customers, matching the year page layout.
5. **`validate_data.py`** — assert Net = Gained − Lost and that lost-flagged
   customers reconcile to inactive + dormant.

### Phase 4D — Schmootz page

1. Move `Schmootz.xlsx` → `data/Schmootz.xlsx`; add to `.gitignore`.
2. Add `export_schmootz.py` → `schmootz.json` (~3 KB), coercing `-` to null.
3. Add `schmootz.html`: Barr Hill → Gebbie and BBB Shop → Gebbie displacement,
   monthly + yearly, existing dark/green styling, wired into nav.
4. Decide whether the nightly workflow regenerates `schmootz.json` (it only
   changes when the workbook is re-saved manually, so probably not).

---

## 9. Not to implement yet

- **HQ pin** — postponed to the very end of the project.
- Any change to `EMPTY_QTYS`, or to how 4-gallon rows are counted.
- Rescraping to recover 2/3 operational events.
- New Leads column.
- 2s as gained customers.
- Making regions mutually exclusive.
- GitHub Actions restructuring, incremental scraping, framework migration.
- Map functionality changes.

---

## 10. Risks and open questions

**Risks**

- *Counted totals must not move.* 4B/4C only add fields. `all_time_total` must
  stay **2,092,880** and customer count **792** through every step.
- *Region drift.* Deriving membership in JS could diverge from the Excel report —
  mitigated by exporting from Python.
- *2015 boundary artifact* (Gained 136) will look wrong if unannotated.
- *`DORMANT_CUTOFF` ages.* Fixed at 2025-01-01 per instruction; as a named
  constant it is a one-line change later.
- *Schmootz workbook is fragile* — hardcoded cell ranges will break if the sheet
  is restructured. The exporter should fail loudly rather than emit silence.

**Open questions**

1. **Projection display** — the homepage "This Year" card currently shows one
   number. Should projected gallons and % be a second line inside that card, or
   a new 5th stat card? (A 5th card changes the 4-column grid.)
2. **"Customers" card** — show `515 active / 792 total` in one card, or split
   into two cards?
3. **Schmootz page scope** — displacement totals only, or also a computed
   comparison against oil collection volumes?
4. **Nightly workflow** — should it regenerate `schmootz.json`, given the source
   only changes on a manual re-save?
5. **Lost-year attribution** — a customer whose final qualifying pickup was 2019
   but who only went inactive recently is counted as lost in **2019**. Confirm
   that is intended, since it moves historical Net figures as statuses change.

---

## 11. Testing plan

**Data layer**

```bash
python3 oil_scraper.py          # CSV reload, no network, ~3 s
python3 validate_data.py        # must stay 0 FAIL
python3 check_town_mismatches.py
```

Assertions to add:

- `all_time_total == 2092880`, `customer_count == 792` (unchanged by 4B/4C)
- `min(gallons) >= 4` across `oil_collections.json`
- projection reproduces **306,909 / +13.7%** on current data
- per-region derived totals equal `monthly_by_region` (Stowe = 220,401)
- `Net == Gained - Lost` for every year; lost total `299 == 277 + 22`

**Browser** — `python3 -m http.server 8000`, then verify per phase:

- 4B: every page reads "Black Bear Biodiesel" / "Oil Collection Analytics";
  logo unchanged; nav order correct on all five pages; homepage projection and
  active count render; region 4th stat is active customers; year monthly chart
  is bars with hover and **no** navigation on click; map, binning, slider,
  town click-through all still work; **no HQ pin**.
- 4C: lifecycle table matches the §4 table exactly; gained/lost lists populate;
  region top-25 matches the §6 verification.
- 4D: Schmootz page totals match §7 (2,134,668 and 133,475).

Every phase: no `NaN`/`undefined`/`null` on screen, clean console, dark theme
and green accents unchanged, and `git diff --stat` reviewed before any commit.

---

## Version control

Working tree is clean apart from **untracked `Schmootz.xlsx`** (4.6 MB) —
deliberately not added; 4D moves it to `data/` and gitignores it.
Branch `dashboard-updates`, HEAD `8b22e95`. Nothing committed or pushed.
