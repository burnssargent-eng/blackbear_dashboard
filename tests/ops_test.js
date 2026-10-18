// Shadow operations site: the shared code in ops-common.js against the
// committed jamaal_routes.json and oil_projections.json. The shadow copy must
// start as an exact copy of the source site's routes, assign like its Assign
// button (no duplicates on a route), name routes by id, and keep new customers
// on ids the source site can never use.

process.chdir(require("path").join(__dirname, ".."));

const fs = require("fs");

const memory = {};
const window = {
  localStorage: {
    getItem: k => (k in memory ? memory[k] : null),
    setItem: (k, v) => { memory[k] = String(v); },
  },
  confirm: () => true,
};

const code = ["dashboard-utils.js", "route-utils.js", "ops-common.js"]
  .map(f => fs.readFileSync(f, "utf8")).join("\n");
const api = new Function("window", code + `
  return { freshStore, loadStore, saveStore, storeStops, assignToRoute, opsRouteList,
    addShadowCustomer, storeRoutesByCustomer, buildIndex, opsNavHtml, OPS_NAV };`)(window);

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log("  FAIL " + msg); } else console.log("  ok   " + msg);
};

const snap = JSON.parse(fs.readFileSync("jamaal_routes.json", "utf8"));
const payload = JSON.parse(fs.readFileSync("oil_projections.json", "utf8"));
const byId = api.buildIndex(payload.customers);

// ── The snapshot has what the shadow site needs ──
ok(Array.isArray(snap.assign_list) && snap.assign_list.length > 100, `assign list present (${snap.assign_list.length} rows)`);
ok(Array.isArray(snap.route_names) && snap.route_names.length > 0, `route names present (${snap.route_names.length})`);

// ── A fresh copy is the source site's routes, stop for stop ──
const store = api.freshStore(snap);
ok(snap.routes.every(r => api.storeStops(store, r.route_id).map(s => s.assignment_id).join() ===
  r.stops.map(s => s.assignment_id).join()), "fresh copy keeps every route's stops in the site's order");
ok(store.base === snap.scraped_at && store.collections.length === 0 && store.my === null,
  "fresh copy records its source time and starts with no entries");

// ── Route list ──
const list = api.opsRouteList(snap);
ok(new Set(list.map(r => r.route_id)).size === list.length, "each route id is listed once");
snap.routes.forEach(r => {
  const entry = list.find(x => x.route_id === r.route_id);
  ok(entry && entry.name === r.name, `route ${r.route_id} is named as Today's Routes names it (${r.name})`);
});
const dupIds = snap.route_names.map(r => r.route_id).filter((id, i, a) => a.indexOf(id) !== i);
dupIds.forEach(id => ok(list.find(x => x.route_id === id).aliases.length > 0,
  `route ${id}, listed twice in the dropdown, keeps its other name as an alias`));

// ── Assigning ──
const route = snap.routes[0].route_id;
const before = api.storeStops(store, route).length;
const already = api.storeStops(store, route)[0];
const fresh = snap.assign_list.find(c => !snap.routes.some(r => r.stops.some(s => s.customer_id === c.customer_id)));
const n = api.assignToRoute(store, route, [already, fresh], true);
ok(n === 1 && api.storeStops(store, route).length === before + 1, "assign skips a customer already on the route");
const last = api.storeStops(store, route).slice(-1)[0];
ok(last.customer_id === fresh.customer_id && last.must_get && last.assignment_id < 0,
  "a new stop goes last, keeps Must Get, and gets a negative assignment id");
ok(snap.routes[0].stops.length === before, "assigning never changes the loaded snapshot");

const c = api.addShadowCustomer(store, { name: "  Test Diner ", city: "Barre", capacity: "150", periodicity: "" });
ok(c.customer_id < 0 && c.name === "Test Diner" && c.capacity === 150 && c.periodicity === null,
  "an added customer gets a negative id and tidy fields");
ok(store.nextId < last.assignment_id && store.nextId < c.customer_id, "ids are never reused");

const on = api.storeRoutesByCustomer(store, list, byId);
ok((on.get(fresh.customer_id) || []).includes(list.find(r => r.route_id === route).name),
  "the on-route lookup sees the shadow copy, not the snapshot");

// ── Saving ──
api.saveStore(store);
const back = api.loadStore(snap);
ok(back.routes[route].length === before + 1 && back.added.length === 1, "the copy survives a reload");
memory["bbb-ops-shadow-v1"] = "{not json";
ok(api.loadStore(snap).routes[route].length === before, "a broken saved copy falls back to a fresh one");

// ── Nav ──
const nav = api.opsNavHtml("assign");
["Assign Routes", "My Route", "Calendar", "Dashboard", "Projections", "Historical Totals", "Regions",
  "Years", "Customers", "Schmootz"].forEach(label => ok(nav.includes(label), `nav has ${label}`));
ok(nav.includes('href="ops-calendar.html"'), "Calendar is linked");
ok(!/Lucy|Time ?clock|Outgoing/i.test(nav), "nav leaves out Ms. Lucy, Timeclock and (for now) Outgoing");

// ── Calendar ──
const calHtml = fs.readFileSync("ops-calendar.html", "utf8");
const calScript = calHtml.slice(calHtml.indexOf("<script>\n/*") + 8, calHtml.lastIndexOf("</script>"))
  .replace(/\n\s*init\(\);\s*$/, "\n");
const cal = new Function("window", "document", code + "\n" + calScript + `
  return { indexCollections, parseNonPickups, set(b) { byDay = b; }, monthTotal, shiftMonth, gridHtml,
    shiftAnchor, anchorLabel, monthOptions };`)(
  window, { getElementById: () => ({}) });
const coll = JSON.parse(fs.readFileSync("oil_collections.json", "utf8")).records;
const days = cal.indexCollections(coll);
cal.set(days);
const someDay = coll[coll.length - 1].date;
const handSum = coll.filter(r => r.date === someDay).reduce((s, r) => s + r.gallons, 0);
ok(days.get(someDay).gallons === handSum, `a day's total is the sum of its pickups (${someDay}: ${handSum})`);
ok(coll.every(r => r.gallons >= 4), "calendar totals hold no 0-3 codes");
const ym = someDay.slice(0, 7);
ok(cal.monthTotal(ym) === coll.filter(r => r.month === ym).reduce((s, r) => s + r.gallons, 0),
  `month total matches the records for ${ym}`);
ok(cal.shiftAnchor("2026-10-18", -1) === "2026-09-18" && cal.anchorLabel(cal.shiftAnchor("2026-10-18", 1)) === "11-18-26",
  "month buttons step a month on the same day, labelled like the source site (11-18-26)");
ok(cal.shiftAnchor("2026-03-31", -1) === "2026-02-28", "a day past the month's end becomes its last day");
const opts = cal.monthOptions("2026-10");
const firstMonth = [...days.keys()].sort()[0].slice(0, 7);
ok(opts[0] === "2026-10" && opts[1] === "2026-09" && opts[12] === "2025-10" && opts[opts.length - 1] === firstMonth,
  `month picker runs newest first, back to the first month of data (${firstMonth})`);
ok(cal.shiftMonth("2026-01", -1) === "2025-12" && cal.shiftMonth("2026-12", 1) === "2027-01",
  "month buttons cross year ends");
const codes = cal.parseNonPickups(fs.readFileSync("oil_non_pickups.csv", "utf8"));
ok([...codes.values()].flat().every(c => c.qty >= 0 && c.qty <= 3), "every listed code is 0-3");
const cells = (cal.gridHtml("2026-02").match(/class="cal-day(?!s)/g) || []).length;
ok(cells % 7 === 0 && cells >= 28, "a month grid fills whole weeks");

// ── No page posts to the source site ──
["ops-assign.html", "ops-myroute.html", "ops-calendar.html", "ops-common.js"].forEach(f => {
  const text = fs.readFileSync(f, "utf8");
  ok(!/<form[^>]+action=|method=["']?post|\.php["'?]?\s*,\s*\{|delete_routes|update_order|add_to_route/i.test(text),
    `${f} never submits to the source site`);
});

console.log(fails ? `${fails} FAILED` : "all shadow site checks passed");
process.exit(fails ? 1 : 0);
