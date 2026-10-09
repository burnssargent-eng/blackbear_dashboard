// Beta projections page: runs the real script from projections.html against
// the committed oil_projections.json. The page must only DISPLAY the export --
// never recompute or re-section it, and keep the export's order unless the
// viewer picks a sort -- so most checks compare what it renders with what the
// file says.

process.chdir(require("path").join(__dirname, ".."));

const fs = require("fs");

function mkEl(id) {
  const el = {
    id, style: {}, dataset: {}, innerHTML: "", textContent: "", value: "",
    children: [],
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild(c) { el.children.push(c); return c; },
    querySelector() { return mkEl("_btn"); },
    addEventListener(ev, fn) { el["_on" + ev] = fn; },
  };
  return el;
}
const els = {};
const document = {
  getElementById: id => (els[id] = els[id] || mkEl(id)),
  createElement: t => mkEl("_" + t),
};
const window = {
  location: { search: "", href: "http://localhost/projections.html" },
  history: { replaceState() {} },
};

const utils = fs.readFileSync("dashboard-utils.js", "utf8");
const html = fs.readFileSync("projections.html", "utf8");
const script = html
  .slice(html.indexOf("<script>\n/*") + 8, html.lastIndexOf("</script>"))
  .replace(/\n\s*init\(\);\s*$/, "\n");

const payload = JSON.parse(fs.readFileSync("oil_projections.json", "utf8"));
const oilData = JSON.parse(fs.readFileSync("oil_data.json", "utf8"));

const api = new Function("document", "window", utils + "\n" + script + `
  return {
    set(p, d) { projections = p; oilData = d; },
    setRegion(r) { selectedRegion = r; },
    setSearch(t) { searchText = t; },
    buildShell, update, filterByRegion, regionOptions, freshnessWarning,
    rankedRowsHtml, heldOutRowsHtml, setSort, renderRanked, daysPastHtml,
    liveTestHtml, formatSigned,
  };`)(document, window);

// formatDate from dashboard-utils, for comparing rendered dates.
const formatDateForTest = new Function(utils + "\nreturn formatDate;")();

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log("  FAIL " + msg); } else console.log("  ok   " + msg);
};
const rowsIn = markup => (markup.match(/<tr>/g) || []).length - 1;   // minus the header row

// ── The export itself ──
const customers = payload.customers;
const sections = new Set(payload.section_order);
ok(customers.every(c => sections.has(c.section)), "every customer's section is in section_order");
ok(customers.every(c => c.pct_full === null || c.pct_full <= 100), "no exported % full above 100");
ok(customers.filter(c => c.section !== "ranked").every(c =>
  c.projected_gal === null && c.pct_full === null && c.days_past_75 === null),
  "held-out rows export no projection or capacity timing");

const byId = Object.fromEntries(customers.map(c => [c.id, c]));
ok(byId[423] && byId[423].section === "on-demand / unlimited", "Perrigo (423) is on-demand, not ranked");
ok(byId[1388] && byId[1388].status === "true closer", "Okemo Mountain Resort (1388) is a true closer");
ok(byId[413] && byId[413].section === "event-driven", "Champlain Valley Expo (413) is event-driven");
ok(byId[270] && byId[270].section === "ranked" && byId[270].status === "seasonal open-ish",
  "Spruce Peak (270) stays ranked as seasonal open-ish");

// ── The page ──
api.set(payload, oilData);
api.setRegion("");
api.buildShell(document.getElementById("page"));
api.update();

const ranked = customers.filter(c => c.section === "ranked");
const list = document.getElementById("ranked-list");
ok(rowsIn(list.innerHTML) === Math.min(250, ranked.length),
  `ranked list shows ${Math.min(250, ranked.length)} rows collapsed (got ${rowsIn(list.innerHTML)})`);

// Rendered order is the export's order by default.
const nameOf = c => api.rankedRowsHtml([c]).match(/<td>([^<]*)/)[1];
const renderedNames = () =>
  [...list.innerHTML.matchAll(/<td class="rank">\d+<\/td>\s*<td>([^<]*)/g)].map(m => m[1]);
const expected = ranked.slice(0, 250).map(nameOf);
ok(JSON.stringify(renderedNames()) === JSON.stringify(expected), "ranked rows render in the export's order");

const pcts = [...list.innerHTML.matchAll(/class="proj-pct">(\d+)%/g)].map(m => Number(m[1]));
ok(pcts.length > 0 && Math.max(...pcts) <= 100, `rendered % full never above 100 (max ${Math.max(...pcts)})`);

const held = document.getElementById("held-out").innerHTML;
ok(!/proj-pct/.test(held), "held-out sections render no % full");
ok(/Seasonal holdout/.test(held) && /On-demand \/ unlimited/.test(held) && /Event-driven/.test(held),
  "held-out sections present: seasonal, on-demand, event-driven");
ok(!/Lump-sum/.test(held), "empty lump-sum section is not rendered");

// ── Region filter ──
const region = api.regionOptions(payload)[0];
const inRegion = api.filterByRegion(customers, region);
ok(inRegion.length > 0 && inRegion.every(c => c.regions.includes(region)),
  `filter "${region}" keeps only its customers (${inRegion.length})`);
ok(inRegion.length === customers.filter(c => c.regions.includes(region)).length,
  `filter "${region}" drops none of its customers`);
api.setRegion(region);
api.update();
const regionRanked = inRegion.filter(c => c.section === "ranked").length;
ok(rowsIn(list.innerHTML) === Math.min(250, regionRanked),
  `region view ranks ${Math.min(250, regionRanked)} rows`);

// ── Header boxes ──
const statVals = () => [...document.getElementById("summary").innerHTML
  .matchAll(/stat-val">([^<]*)/g)].map(m => m[1]);
api.setRegion("");
api.update();
const totalGal = Math.round(ranked.reduce((s, c) => s + (c.projected_gal || 0), 0));
const countedHeld = customers.filter(c =>
  ["seasonal holdout", "will-call", "stale", "insufficient data"].includes(c.section)).length;
const vals = statVals();
ok(vals.length === 5, `five header boxes (got ${vals.length})`);
ok(vals[0] === totalGal.toLocaleString(), `total projected oil = sum of ranked projected_gal (${totalGal})`);
ok(vals[1] === ranked.length.toLocaleString(), "route candidates box");
ok(vals[2] === String(ranked.filter(c => c.days_past_75 > 0).length), "past pickup point box");
ok(vals[3] === String(ranked.filter(c => c.pct_full > 50).length), "past 50% full box");
ok(vals[4] === String(countedHeld), `held-out box counts seasonal/will-call/stale/insufficient (${countedHeld})`);

// ── Columns ──
const notYet = ranked.find(c => c.days_until_75 > 0.5);
const past = ranked.find(c => c.days_past_75 > 0.5);
ok(notYet && api.daysPastHtml(notYet) === String(-Math.round(notYet.days_until_75)),
  `not yet at the pickup point shows negative days (${notYet && api.daysPastHtml(notYet)})`);
ok(past && api.daysPastHtml(past) === Math.round(past.days_past_75).toLocaleString(),
  `past the pickup point shows positive days (${past && api.daysPastHtml(past)})`);
ok(api.daysPastHtml({ days_until_75: 0.2 }) === "0" && api.daysPastHtml({ days_until_75: null }) === "—",
  "zero shows as 0, missing as a dash");
const sample = ranked[0];
const sampleRow = api.rankedRowsHtml([sample]);
ok(sampleRow.includes(`(${Math.round(sample.range_low).toLocaleString()}–${Math.round(sample.range_high).toLocaleString()})`),
  "projected gal carries its range in brackets");
ok(sample.periodicity_days !== null && sampleRow.includes(`>${Math.round(sample.periodicity_days)}</td>`),
  "periodicity cell shows the exported periodicity_days");
ok(sample.avg_collection_all !== null && sampleRow.includes(`>${sample.avg_collection_all.toFixed(1)}</td>`),
  "avg collection cell shows the exported avg_collection_all");
ok(ranked.every(c => c.capacity === null || c.rate_gpd === null || c.periodicity_days === null ||
  Math.abs(c.periodicity_days / (payload.target_fraction * c.capacity / c.rate_gpd) - 1) < 0.02),
  "exported periodicity = target fraction x capacity / gal per day (gal/day is rounded)");
ok(!/Likely range|Days building/.test(list.innerHTML), "old Likely range and Days building columns gone");

// ── Sorting ──
api.setSort("projected");
const maxProj = Math.max(...ranked.map(c => c.projected_gal));
ok(renderedNames()[0] === nameOf(ranked.find(c => c.projected_gal === maxProj)),
  "projected gal sorts by the projected figure, highest first");
api.setSort("projected");
const minProj = Math.min(...ranked.map(c => c.projected_gal));
ok(renderedNames()[0] === nameOf(ranked.find(c => c.projected_gal === minProj)),
  "a second click flips to lowest first");
api.setSort("name");
const firstName = renderedNames()[0];
ok(ranked.every(c => nameOf(c).localeCompare(firstName) >= 0), "customer sorts A to Z first");
const rankCells = [...list.innerHTML.matchAll(/<td class="rank">(\d+)</g)].map(m => Number(m[1]));
ok(rankCells.slice(0, 5).some((r, i) => r !== i + 1), "# keeps the urgency rank through a re-sort");
api.setSort("rank");
ok(JSON.stringify(renderedNames()) === JSON.stringify(expected), "clicking # restores the export's order");

// ── Show all ──
list.dataset.expanded = "true";
api.renderRanked();
ok(rowsIn(list.innerHTML) === ranked.length, `show all renders every ranked row (${ranked.length})`);

// ── Search ──
const needle = sample.name.slice(0, 6).toLowerCase();
api.setSearch(needle);
api.update();
const hits = ranked.filter(c => [c.name, ...(c.members || []).map(m => m.name)]
  .some(n => n.toLowerCase().includes(needle)));
ok(rowsIn(list.innerHTML) === hits.length && hits.length > 0,
  `search "${needle}" shows its ${hits.length} matching ranked rows`);
ok(statVals()[1] === ranked.length.toLocaleString(), "search leaves the header boxes alone");
api.setSearch("zzzz-no-such-customer");
api.update();
ok(/No route candidates match/.test(list.innerHTML), "a search with no match says so");
api.setSearch("");
api.update();

// ── Empty checks restart the clock ──
const checked = customers.filter(c => c.last_empty_check);
ok(checked.length > 0 && checked.every(c => c.days_accumulating < c.days_since &&
  c.last_empty_check > c.last_pickup), `${checked.length} customers with a later empty check count from it`);
ok(customers.filter(c => !c.last_empty_check).every(c => c.days_accumulating === c.days_since),
  "every other customer counts from its last pickup");
const rankedChecked = checked.find(c => c.section === "ranked");
ok(rankedChecked && /Checked empty/.test(api.rankedRowsHtml([rankedChecked])),
  "a ranked row with an empty check renders the note");
ok(byId[510] && byId[510].last_empty_check && byId[510].pct_full < 75,
  `10 Railroad Street (510) counts from its last empty check (${byId[510] && byId[510].pct_full}% full)`);

// ── Shared barrels and new owners ──
const shared = customers.filter(c => c.members);
const memberIds = shared.flatMap(c => c.members.map(m => m.id));
const rowIds = new Set(customers.map(c => c.id));
ok(shared.length >= 15, `${shared.length} shared barrels exported`);
ok(shared.every(c => c.id === Math.min(...c.members.map(m => m.id))),
  "each shared barrel is listed under its lowest member id");
ok(memberIds.filter(id => !shared.some(c => c.id === id)).every(id => !rowIds.has(id)),
  "no member of a shared barrel appears as its own row");
ok(new Set(memberIds).size === memberIds.length, "no customer is in two barrels");
const sharedRanked = shared.find(c => c.section === "ranked");
ok(sharedRanked && /Shared barrel:/.test(api.rankedRowsHtml([sharedRanked])),
  "a ranked shared barrel renders its members");
ok(byId[133] && byId[133].history_start && byId[133].section !== "ranked",
  "JJ's (133) restarts its history and is not ranked on the old tavern's");

// ── The ladder ──
const STAGES = ["new", "established", "seasonal", "closer in season"];
ok(ranked.every(c => STAGES.includes(c.stage)), "every ranked row has a stage");
ok(customers.filter(c => c.section === "insufficient data" && !c.history_start)
  .every(c => c.stage === null), "insufficient rows have no stage");
const willCall = customers.filter(c => c.section === "will-call");
ok(willCall.length > 0 && willCall.every(c => c.status === "will-call (detected)" &&
  c.projected_gal === null && c.reason), `${willCall.length} will-call rows, none projected, each with a reason`);
ok(willCall.every(c => !["on-demand / unlimited", "event-driven", "true closer",
  "semi-closer / call-driven", "seasonal open-ish"].includes(c.status)),
  "no override is detected as will-call");
ok(/Will-call/.test(held), "will-call section rendered");
const newRows = ranked.filter(c => c.stage === "new");
ok(newRows.length > 0 && newRows.every(c => c.range_high >= c.projected_gal * 1.3 - 1),
  `${newRows.length} new customers carry a range wider than +/-20% on the high side`);
ok(/New customer/.test(api.rankedRowsHtml([newRows[0]])), "a new customer renders its note");
const seasonalRows = ranked.filter(c => c.stage === "seasonal");
ok(seasonalRows.length > 0 && /Seasonal rate/.test(api.rankedRowsHtml([seasonalRows[0]])),
  `${seasonalRows.length} seasonal-rate customers, note rendered`);

const detected = customers.filter(c => c.status === "closer (detected)");
ok(detected.length > 0 && detected.every(c => c.season &&
  ["ranked", "seasonal holdout"].includes(c.section)),
  `${detected.length} detected closers, each with a season, ranked or held out`);
ok(byId[119] && byId[119].status === "closer (detected)", "Thunder Road (119) is a detected closer");
ok(byId[225] && byId[225].status === "true closer", "the registry still wins: Mount Ellen (225) stays a true closer");
const inSeason = ranked.filter(c => c.stage === "closer in season");
ok(inSeason.length > 0 && /In-season rate/.test(api.rankedRowsHtml([inSeason[0]])),
  `${inSeason.length} closers ranked on their open-season rate, note rendered`);

// ── Live test ──
const live = JSON.parse(fs.readFileSync("oil_live_test.json", "utf8"));
const liveHtml = api.liveTestHtml(live);
const summaries = (liveHtml.match(/<summary>/g) || []).length;
ok(summaries === live.days.length, `one block per pickup date (${summaries} of ${live.days.length})`);
ok((liveHtml.match(/<details class="proj-section" open>/g) || []).length === 1,
  "only the newest day is open");
const dayDates = [...liveHtml.matchAll(/<summary>([^<]*?)\s*<span/g)].map(m => m[1].trim());
ok(JSON.stringify(dayDates) === JSON.stringify(live.days.map(d => formatDateForTest(d.date))),
  "days render newest first, in the export's order");
const liveRows = live.days.flatMap(d => d.rows);
const scoredRow = liveRows.find(r => r.projected !== null && r.error !== null);
ok(scoredRow && liveHtml.includes(api.formatSigned(scoredRow.error)),
  `a scored row shows its exported error (${scoredRow && api.formatSigned(scoredRow.error)})`);
ok(api.formatSigned(11.7) === "+11.7" && api.formatSigned(-17.1) === "−17.1",
  "errors are signed: + over-projected, − under");
const heldRow = liveRows.find(r => r.projected === null);
ok(!heldRow || /Not projected \(/.test(liveHtml), "rows without a projection say so");
ok(liveRows.every(r => r.projected === null || r.range_low === null ||
  (r.in_range === "yes") === (r.range_low <= r.actual && r.actual <= r.range_high)),
  "every exported in-range flag matches its range");
ok(/not available yet/.test(api.liveTestHtml(null)), "a missing live test file shows a note, not an error");

// ── Freshness ──
ok(api.freshnessWarning(payload, { last_updated: payload.data_last_updated }) === null,
  "no warning when built from the live scrape");
ok(/out of date/.test(api.freshnessWarning(payload, { last_updated: "2099-01-01T00:00:00" }) || ""),
  "warning when the live scrape is newer than the projection");

console.log(fails ? `${fails} FAILED` : "ALL CHECKS PASSED");
process.exit(fails ? 1 : 0);
