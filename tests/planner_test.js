// Route planner (beta): runs the real script from planner.html against the
// committed oil_projections.json and jamaal_routes.json. The page must only ADD
// exported per-customer figures -- count a shared barrel once, keep held-out
// customers' typical pickups out of the projected total -- and its reordering
// must keep every stop exactly once.

process.chdir(require("path").join(__dirname, ".."));

const fs = require("fs");

function mkEl(id) {
  const el = {
    id, style: {}, dataset: {}, innerHTML: "", textContent: "", value: "",
    addEventListener() {}, scrollIntoView() {},
  };
  return el;
}
const els = {};
const document = { getElementById: id => (els[id] = els[id] || mkEl(id)) };
const window = {
  location: { search: "", href: "http://localhost/planner.html" },
  localStorage: { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } },
  confirm() { return true; },
};

const utils = fs.readFileSync("dashboard-utils.js", "utf8") + "\n" +
  fs.readFileSync("route-utils.js", "utf8");
const html = fs.readFileSync("planner.html", "utf8");
const script = html
  .slice(html.indexOf("<script>\n/*") + 8, html.lastIndexOf("</script>"))
  .replace(/\n\s*init\(\);\s*$/, "\n");

const api = new Function("document", "window", "navigator", utils + "\n" + script + `
  return {
    set(p, r) { projections = p; routesSnap = r; index = buildIndex(p.customers); },
    TRUCKS, buildIndex, stopEstimate, routeSummary, routesByCustomer, moveStop,
    setStopNumber, addStop, removeStop, truckFillHtml, draftText, loadState,
    getState() { return state; }, renderDraft, renderJamaal, candidateRows, projectedRangeText,
  };`)(document, window, {});

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log("  FAIL " + msg); } else console.log("  ok   " + msg);
};
const close = (a, b) => Math.abs(a - b) < 1e-6;

const payload = JSON.parse(fs.readFileSync("oil_projections.json", "utf8"));
const snap = JSON.parse(fs.readFileSync("jamaal_routes.json", "utf8"));
api.set(payload, snap);
const byId = api.buildIndex(payload.customers);

// ── Lookup ──
const shared = payload.customers.find(c => c.members && c.members.length > 1 && c.section === "ranked");
ok(shared && shared.members.every(m => byId.get(m.id) === shared),
  `every member of a shared barrel maps to its one row (${shared && shared.name})`);
const ranked = payload.customers.find(c => c.section === "ranked" && !c.members);
const held = payload.customers.find(c => c.section !== "ranked" && c.avg_collection);
ok(api.stopEstimate(ranked).kind === "projected", "a ranked customer is projected");
ok(api.stopEstimate(held).kind === "typical" && api.stopEstimate(held).gal === held.avg_collection,
  "a held-out customer gives its average pickup as typical");
ok(api.stopEstimate(null).kind === "none", "a stop not in the export has no figure");

// ── Totals ──
const stop = c => ({ customer_id: c.id, name: c.name, must_get: false });
const memberStops = shared.members.map(m => ({ customer_id: m.id, name: m.name }));
const mixed = [stop(ranked), ...memberStops, stop(held), { customer_id: 999999, name: "BBB- shop task" }];
const { totals, lines } = api.routeSummary(mixed, byId);
ok(close(totals.projected, ranked.projected_gal + shared.projected_gal),
  "projected total adds a shared barrel once, not once per member");
ok(close(totals.low, ranked.range_low + shared.range_low) &&
   close(totals.high, ranked.range_high + shared.range_high), "range ends add the same way");
ok(totals.sharedRepeats === shared.members.length - 1 && lines[2].dupOf === 1,
  "the later member is marked as the same barrel as the earlier stop");
ok(close(totals.typical, held.avg_collection) && totals.typicalStops === 1,
  "a held-out stop is counted as typical, outside the projected total");
ok(totals.noneStops === 1, "a task row is counted as a stop with no figure");
ok(totals.projectedStops === 2, "projected stop count excludes repeats and held-out stops");
ok(close(totals.capacity, ranked.capacity + shared.capacity + held.capacity),
  "capacity total counts a shared barrel's capacity once");
ok(lines[2].capacity === null, "the repeat member carries no capacity of its own");
const siteOnly = api.routeSummary([{ customer_id: 999999, name: "BBB- task", capacity: 55 }], byId);
ok(siteOnly.totals.capacity === 55, "a stop outside the export uses the capacity the site listed");

// Every source-site route's total is the plain sum of its distinct ranked rows.
snap.routes.forEach(r => {
  const seen = new Set();
  let expect = 0;
  r.stops.forEach(s => {
    const row = byId.get(s.customer_id);
    if (!row || seen.has(row.id)) return;
    seen.add(row.id);
    if (row.projected_gal !== null) expect += row.projected_gal;
  });
  ok(close(api.routeSummary(r.stops, byId).totals.projected, expect),
    `${r.name}: projected total matches a hand sum (${Math.round(expect)} gal)`);
});

// ── Reordering ──
const ids = s => s.map(x => x.customer_id).join(",");
const five = [1, 2, 3, 4, 5].map(i => ({ customer_id: i, name: "S" + i }));
ok(ids(api.moveStop(five, 4, 0)) === "5,1,2,3,4", "moving the last stop to the top shifts the rest down");
ok(ids(api.moveStop(five, 0, 1)) === "2,1,3,4,5", "down arrow swaps with the next stop");
ok(ids(api.setStopNumber(five, 1, "4")) === "1,3,4,2,5", "typing 4 for stop 2 puts it fourth");
ok(ids(api.setStopNumber(five, 1, "99")) === "1,3,4,5,2", "a number past the end goes last");
ok(ids(api.setStopNumber(five, 3, "0")) === "4,1,2,3,5", "a number below 1 goes first");
ok(ids(api.setStopNumber(five, 2, "abc")) === "1,2,3,4,5", "a non-number changes nothing");
ok(ids(api.moveStop(five, 0, 0)) === "1,2,3,4,5" && ids(five) === "1,2,3,4,5",
  "moves never change the input array");
ok(ids(api.addStop(five, { customer_id: 3 })) === "1,2,3,4,5", "adding a stop already in the draft does nothing");
ok(ids(api.removeStop(five, 2)) === "1,2,4,5", "remove takes out only that stop");

// ── Truck fill ──
api.TRUCKS.push({ name: "Test truck", capacity: 400 });
const t = { projected: 300, low: 250, high: 450 };
ok(/75% of Test truck/.test(api.truckFillHtml(t, "Test truck")) &&
   /could overflow at the high end/.test(api.truckFillHtml(t, "Test truck")),
  "fill reads projected against the tank and warns when the high end overflows");
ok(/over the tank/.test(api.truckFillHtml({ projected: 500, low: 400, high: 600 }, "Test truck")),
  "a projected total over the tank says so");
api.TRUCKS.pop();
ok(api.TRUCKS.every(t => t.capacity > 0), "every truck has a tank capacity");

// ── Page ──
api.loadState();   // storage blocked: must start empty, not throw
ok(api.getState().draft.stops.length === 0, "blocked browser storage starts an empty draft");
api.renderJamaal();
const jam = document.getElementById("rp-jamaal").innerHTML;
ok(snap.routes.every(r => jam.includes(r.name.replace(/&/g, "&amp;"))), "every source-site route is rendered");
ok(!/proj-range/.test(jam), "stops show projected gallons without their own range");
ok((jam.match(/proj-live-total/g) || []).length === snap.routes.length, "every route ends in a total row");
snap.routes.forEach(r => {
  const t = api.routeSummary(r.stops, byId).totals;
  ok(jam.includes(api.projectedRangeText(t)), `${r.name}: header reads "${api.projectedRangeText(t)}"`);
});
ok(!/dreamhosters|\.php/.test(html), "the page never links to or requests the source site");
api.renderDraft();
ok(api.candidateRows().length > 0 &&
   api.candidateRows().every(c => c.section === "ranked"), "empty search lists ranked candidates");

const text = api.draftText({ name: "Test", truck: "", stops: mixed }, byId);
ok(text.split("\n").length === mixed.length + 2 && /same barrel/.test(text),
  "copied text has a heading, one line per stop and a total");

console.log(fails ? `${fails} FAILED` : "all planner checks passed");
process.exit(fails ? 1 : 0);
