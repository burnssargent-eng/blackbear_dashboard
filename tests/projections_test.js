// Beta projections page: runs the real script from projections.html against
// the committed oil_projections.json. The page must only DISPLAY the export --
// never recompute, re-sort or re-section it -- so most checks compare what it
// renders with what the file says.

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
    buildShell, update, filterByRegion, regionOptions, freshnessWarning,
    rankedRowsHtml, heldOutRowsHtml,
  };`)(document, window);

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
ok(rowsIn(list.innerHTML) === Math.min(50, ranked.length),
  `ranked list shows ${Math.min(50, ranked.length)} rows collapsed (got ${rowsIn(list.innerHTML)})`);

// Rendered order is the export's order: compare the first 50 names.
const renderedNames = [...list.innerHTML.matchAll(/<td class="rank">\d+<\/td>\s*<td>([^<]*)/g)].map(m => m[1]);
const expected = ranked.slice(0, 50).map(c => api.rankedRowsHtml([c]).match(/<td>([^<]*)/)[1]);
ok(JSON.stringify(renderedNames) === JSON.stringify(expected), "ranked rows render in the export's order");

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
ok(rowsIn(list.innerHTML) === Math.min(50, regionRanked),
  `region view ranks ${Math.min(50, regionRanked)} rows`);

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

// ── Freshness ──
ok(api.freshnessWarning(payload, { last_updated: payload.data_last_updated }) === null,
  "no warning when built from the live scrape");
ok(/out of date/.test(api.freshnessWarning(payload, { last_updated: "2099-01-01T00:00:00" }) || ""),
  "warning when the live scrape is newer than the projection");

console.log(fails ? `${fails} FAILED` : "ALL CHECKS PASSED");
process.exit(fails ? 1 : 0);
