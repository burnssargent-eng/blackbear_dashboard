# Projection model rules

Moved from the root CLAUDE.md on 2026-11-16 so they load only when working in `analysis/`.
The method and every decision so far: `README.md` here; what is in flight: `ROADMAP.md`.

**Projection overrides are config, keyed by id.** `MODEL_OVERRIDES` in
`analysis/build_projection_table.py` routes named customers around the 50/50
ranking (true closer, semi-closer, open-ish, event-driven, on-demand). Each
entry is keyed by customer id, asserts the customer name, and carries a
month-level season where one applies. The customer page's Edit info can set a
projection type (will-call, seasonal with its months, event-driven; `customers.site_model`,
exported in `customer_edits.csv`, `SITE_OVERRIDES` in the builder): it REPLACES the
model's choice and any registry entry for that id (Sarge, 2026-11-12), the report
flags each replacement, and it takes effect at the next nightly. Seasonal is a
semi-closer, but held while closed unless it had a pickup after the season ended (a
coded semi-closer keeps the recent-call threshold). `fringe_seasonal_candidates` informs the
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
