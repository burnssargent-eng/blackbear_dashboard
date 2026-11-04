// Former customers page (former.html), the year page's retention table
// (year.html) and the era helpers in dashboard-utils.js, against the committed
// customer_history.json / oil_collections.json / oil_data.json.
//
//   node tests/former_test.js
process.chdir(require("path").join(__dirname, ".."));

const fs = require("fs");
function mkEl(i) {
  const e = { id: i, style: {}, dataset: {}, innerHTML: "", textContent: "", children: [],
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild(c) { e.children.push(c); return c; }, querySelector() { return mkEl("q"); },
    addEventListener() {}, getContext() { return {}; } };
  return e;
}
const els = {};
const document = { getElementById: i => (els[i] = els[i] || mkEl(i)), createElement: t => mkEl("_" + t),
  querySelectorAll: () => [] };
const Chart = function () { return {}; };
const utils = fs.readFileSync("dashboard-utils.js", "utf8");
const page = f => {
  const html = fs.readFileSync(f, "utf8");
  return html.slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>")).replace(/\n\s*init\(\);\s*$/, "\n");
};
const former = new Function("document", "Chart", utils + "\n" + page("former.html") +
  "\nreturn {endedBusinesses, filterBusinesses, listTableHtml, monthSeries, headlineHtml, COLUMNS," +
  " historyIndex, eraRecords, siteTimelineHtml, navHtml, endReasonBadge};")(document, Chart);
const year = new Function("document", "Chart", utils + "\n" + page("year.html") +
  "\nreturn {yearBusinesses, retentionTableHtml};")(document, Chart);

const data = JSON.parse(fs.readFileSync("oil_data.json", "utf8"));
const coll = JSON.parse(fs.readFileSync("oil_collections.json", "utf8"));
const hist = JSON.parse(fs.readFileSync("customer_history.json", "utf8"));
const s = data.retention_summary;

let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("  FAIL " + m); } else console.log("  ok   " + m); };

console.log("1. The list");
const ended = former.endedBusinesses(hist);
ok(ended.length === s.ended_total, `${ended.length} ended businesses, as the summary says`);
ok(ended.every((b, i) => !i || ended[i - 1].last_pickup >= b.last_pickup), "most recent ending first");
const state = r => ({ reason: r, year: null, q: "" });
const lost = former.filterBusinesses(ended, state("lost"));
ok(lost.length === s.lost_total && lost.some(b => /Papa Tirozzi/.test(b.name)), `Lost lists ${lost.length}, Papa Tirozzi's among them`);
ok(former.filterBusinesses(ended, state("unknown")).length === s.unknown_total, "Unclassified matches the summary");
ok(former.filterBusinesses(ended, { reason: "all", year: null, q: "hatchet" }).length === 1, "search finds Hatchet");
const byId = new Map(hist.businesses.map(b => [b.id, b]));
const handed = former.filterBusinesses(ended, state("changed_hands"));
const table = former.listTableHtml(handed, { key: null, dir: "desc" }, byId, 100);
ok(/Hatchet Tap &amp; Table/.test(table) && !/Hatchet Tap & Table/.test(table), "names are escaped");
ok(/Schaefer&#39;s|Schaefer's/.test(table), "a handover names the site's successor");
ok(!/>undefined<|NaN/.test(table), "no undefined or NaN cells");
const capped = former.listTableHtml(ended, { key: null, dir: "desc" }, byId, 100);
ok((capped.match(/<tr>/g) || []).length === 101 && /Show all/.test(capped), "capped at 100 rows with Show all");

console.log("\n2. Headline figures are the exported ones");
const head = former.headlineHtml(s);
ok(head.includes(`${(s.site_retention * 100).toFixed(1)}%`), "site retention shown as exported");
ok(head.includes(`${s.site_handovers} of ${s.site_closures}`), "handovers of closures");

console.log("\n3. Eras hold every pickup of their business");
const idx = former.historyIndex(hist, coll.customers);
const recs = coll.records;
const byCust = new Map();
recs.forEach(r => { if (!byCust.has(r.customer_id)) byCust.set(r.customer_id, []); byCust.get(r.customer_id).push(r); });
const off = hist.businesses.filter(b => {
  const g = b.eras.map(k => idx.eras.get(k)).reduce((t, e) => t + former.eraRecords(e, byCust.get(e.customer_id) || [])
    .reduce((a, r) => a + r.gallons, 0), 0);
  return g !== b.gallons;
});
ok(off.length === 0, `every business's eras sum to its gallons (${off.length} off${off.length ? ": " + off.slice(0, 3).map(b => b.id) : ""})`);
ok(idx.byAccount.size === coll.customers.length, "every account has its eras, registered or not");

console.log("\n4. Site timeline");
const tl = former.siteTimelineHtml(idx.byAccount.get(504), e => e.business);
const order = ["Claire", "Scale House", "Cork"].map(n => tl.indexOf(n));
ok(order.every((v, i) => v > 0 && (!i || v > order[i - 1])), "Hardwick: Claire's, Scale House, Cork & Fork in order");
const widths = [...tl.matchAll(/left:([\d.]+)%;width:([\d.]+)%/g)].map(m => [+m[1], +m[2]]);
ok(widths.length === 3 && widths.every(([l, w]) => l >= 0 && l + w <= 101.5), "segments sit inside the bar");
ok(/site-tl-current/.test(tl) && /now/.test(tl), "the current era runs to now");

console.log("\n5. Monthly chart keeps no-pickup months as zero");
const riley = idx.eras.get("615/1");
const ser = former.monthSeries(former.eraRecords(riley, byCust.get(615)));
ok(ser.months.includes("2022-01") && ser.values[ser.months.indexOf("2022-01")] === 0, "Riley's winter 2021-22 gap is zeros");
ok(ser.values.reduce((a, b) => a + b, 0) === riley.gallons, "and the months sum to the era's gallons");

console.log("\n6. Year page");
const row = data.retention_by_year.find(r => r.year === 2025);
const yb = year.yearBusinesses(hist, 2025);
ok(yb.gained.length === row.gained && yb.ended.length === row.ended, `2025: ${yb.gained.length} gained, ${yb.ended.length} ended, as the table`);
const rt = year.retentionTableHtml(data.retention_by_year, 2025);
ok((rt.match(/fmr-picked/g) || []).length === 1, "the shown year is highlighted once");
ok(rt.includes(" to date") && rt.includes(" *"), "this year is marked to date, the first year starred");

console.log("\n7. Nav");
ok(/former\.html/.test(former.navHtml("former")) && /class="active">Former/.test(former.navHtml("former")), "shared nav links Former");
ok(/<a href="former\.html">Former<\/a>/.test(fs.readFileSync("index.html", "utf8")), "the homepage's own nav too");

console.log(fails ? `\n${fails} FAILED` : "\nALL CHECKS PASSED");
process.exit(fails ? 1 : 0);
