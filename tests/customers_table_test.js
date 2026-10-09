// Customers page table: search, column sorting and the Region column. Runs the
// real script from customers.html against the committed JSON.

process.chdir(require("path").join(__dirname, ".."));

const fs = require("fs");

function mkEl(id) {
  const el = {
    id, style: {}, dataset: {}, innerHTML: "", textContent: "", value: "",
    children: [], _c: new Set(),
    classList: {
      toggle(c, on) { on ? el._c.add(c) : el._c.delete(c); },
      add(c) { el._c.add(c); }, remove(c) { el._c.delete(c); },
      contains(c) { return el._c.has(c); },
    },
    appendChild(c) { el.children.push(c); return c; },
    querySelector() { return mkEl("_btn"); },
    addEventListener() {},
  };
  return el;
}
const els = {};
const document = {
  getElementById: id => (els[id] = els[id] || mkEl(id)),
  createElement: t => mkEl("_" + t),
};

const utils = fs.readFileSync("dashboard-utils.js", "utf8");
const html = fs.readFileSync("customers.html", "utf8");
const script = html
  .slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>"))
  .replace(/\ninit\(\);\s*$/, "\n");

const oilData = JSON.parse(fs.readFileSync("oil_data.json", "utf8"));
const collections = JSON.parse(fs.readFileSync("oil_collections.json", "utf8"));

const api = new Function("document", utils + "\n" + script + `
  return {
    load(data, records) {
      allRecords = records;
      allMonths = Array.from(new Set(records.map(r => r.month))).sort();
      allYears = Array.from(new Set(records.map(r => Number(r.year)))).sort((a, b) => a - b);
      regionByCustomer = customerRegions(data);
    },
    setSearch(t) { searchText = t; },
    regionOf(id) { return regionByCustomer[id]; },
    update, setSort, setColumnSort, renderTable,
  };`)(document);

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log("  FAIL " + msg); } else console.log("  ok   " + msg);
};

api.load(oilData, collections.records);
document.getElementById("mode-select").value = "alltime";
api.update();

const area = document.getElementById("table-area");
const rows = () => [...area.innerHTML.matchAll(
  /<td class="rank">(\d+)<\/td>\s*<td>([^<]*)<\/td>\s*<td>(?:<a[^>]*>)?([^<]*)(?:<\/a>)?<\/td>\s*<td>([^<]*)<\/td>\s*<td class="gal">([\d,]+)<\/td>/g)]
  .map(m => ({ rank: Number(m[1]), name: m[2], town: m[3], region: m[4],
               gallons: Number(m[5].replace(/,/g, "")) }));

// ── Region column ──
const themes = new Set(oilData.theme_regions);
ok(!/>County</.test(area.innerHTML) && /data-sort="region">Region/.test(area.innerHTML),
  "County column replaced by Region");
const geoCount = {};
Object.entries(oilData.region_customers).forEach(([r, ids]) => {
  if (!themes.has(r)) ids.forEach(id => { geoCount[id] = (geoCount[id] || 0) + 1; });
});
ok(Object.values(geoCount).every(n => n === 1), "no customer sits in two non-theme regions");
ok(api.regionOf(423) === "Perrigo", `Perrigo (423) shows region Perrigo (${api.regionOf(423)})`);
ok(rows().every(r => !themes.has(r.region)), "no theme region is shown as a customer's region");

// ── Default order ──
const first = rows();
ok(first.length === 50, `50 rows collapsed (got ${first.length})`);
ok(first.every((r, i) => i === 0 || first[i - 1].gallons >= r.gallons), "default order is top producers");
ok(first.every((r, i) => r.rank === i + 1), "# numbers the default order");

// ── Column sorting ──
api.setColumnSort("gallons");
let r = rows();
ok(r.every((x, i) => i === 0 || r[i - 1].gallons >= x.gallons), "gallons sorts high to low first");
api.setColumnSort("gallons");
r = rows();
ok(r.every((x, i) => i === 0 || r[i - 1].gallons <= x.gallons), "a second click flips to low to high");
ok(r[0].rank > 50, `# keeps the top-producer rank through a re-sort (first row #${r[0].rank})`);
api.setColumnSort("name");
r = rows();
ok(r.every((x, i) => i === 0 || r[i - 1].name.localeCompare(x.name) <= 0), "customer sorts A to Z");
api.setColumnSort("region");
r = rows();
ok(r.every((x, i) => i === 0 || r[i - 1].region === "—" || x.region === "—" ||
  r[i - 1].region.localeCompare(x.region) <= 0), "region sorts A to Z");
ok(!document.getElementById("sort-gallons").classList.contains("active"),
  "a column sort clears the preset button highlight");
api.setColumnSort("rank");
ok(JSON.stringify(rows()) === JSON.stringify(first), "clicking # restores the top-producer order");
ok(document.getElementById("sort-gallons").classList.contains("active"), "and re-highlights the preset");

// ── Presets still work ──
api.setSort("status");
ok(/group-row/.test(area.innerHTML), "Active first keeps its group rows");
api.setColumnSort("gallons");
ok(!/group-row/.test(area.innerHTML), "a column sort drops the group rows");
api.setSort("gallons");

// ── Search ──
const needle = first[0].name.slice(0, 5).toLowerCase();
api.setSearch(needle);
api.renderTable(area, true);
const hits = new Set(collections.records.filter(x => x.name.toLowerCase().includes(needle))
  .map(x => x.customer_id)).size;
ok(rows().length === Math.min(50, hits) && rows().every(x => x.name.toLowerCase().includes(needle)),
  `search "${needle}" shows its ${hits} matching customers`);
ok(/ of /.test(document.getElementById("table-title").textContent), "title shows matches of total");
api.setSearch("zzzz-no-such-customer");
api.renderTable(area, true);
ok(/No customers match/.test(area.innerHTML), "a search with no match says so");

console.log(fails ? `${fails} FAILED` : "ALL CHECKS PASSED");
process.exit(fails ? 1 : 0);
