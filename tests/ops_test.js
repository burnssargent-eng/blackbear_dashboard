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
    addShadowCustomer, createRoute, renameRoute, storeRoutesByCustomer, buildIndex, opsNavHtml, OPS_NAV,
    recordCollection, todaysCollection, liveRouteTotals, routeSummary, recordTheft, theftGallonsText,
    recordOilLeft, todaysOilLeft, clearOilLeft, collectionLines };`)(window);

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

const c2 = api.addShadowCustomer(store, { name: "Test Annex", address: " 12 Main St ", city: "Barre",
  phone: "802-555-0100", notes: "back door", shared_with: String(c.customer_id) });
ok(c2.address === "12 Main St" && c2.phone === "802-555-0100" && c2.notes === "back door" &&
  c2.shared_with === c.customer_id, "an added customer keeps address, phone, notes and its shared barrel");

// ── New Route… ──
const made = api.createRoute(store);
api.assignToRoute(store, made, [fresh], false);
api.renameRoute(store, made, "  Barre loop ");
api.renameRoute(store, made, "   ");
const withNew = api.opsRouteList(snap, store);
ok(made < 0 && withNew.slice(-1)[0].route_id === made && withNew.slice(-1)[0].name === "Barre loop" &&
  withNew.slice(-1)[0].shadow, "a new route gets a negative id, comes last, and keeps its name over a blank rename");
ok(api.opsRouteList(snap).length === withNew.length - 1, "without the copy, the list is the source site's routes only");
api.renameRoute(store, route, "Hijack");
ok(api.opsRouteList(snap, store).find(r => r.route_id === route).name !== "Hijack",
  "the source site's routes cannot be renamed");

const on = api.storeRoutesByCustomer(store, list, byId);
ok((on.get(fresh.customer_id) || []).includes(list.find(r => r.route_id === route).name),
  "the on-route lookup sees the shadow copy, not the snapshot");

// ── Saving ──
api.saveStore(store);
const back = api.loadStore(snap);
ok(back.routes[route].length === before + 1 && back.added.length === 2 && back.newRoutes.length === 1,
  "the copy survives a reload");
memory["bbb-ops-shadow-v1"] = "{not json";
ok(api.loadStore(snap).routes[route].length === before, "a broken saved copy falls back to a fresh one");

// ── Nav ──
const nav = api.opsNavHtml("assign");
["Assign Routes", "My Route", "Calendar", "Dashboard", "Projections", "Historical Totals", "Regions",
  "Years", "Customers", "Schmutz"].forEach(label => ok(nav.includes(label), `nav has ${label}`));
ok(nav.includes('href="ops-calendar.html"'), "Calendar is linked");
ok(!/Lucy|Time ?clock/i.test(nav), "nav leaves out Ms. Lucy and Timeclock");
ok(nav.includes('href="ops-outgoing.html"'), "Outgoing is linked");

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

// ── Outgoing ──
const outHtml = fs.readFileSync("ops-outgoing.html", "utf8");
const outScript = outHtml.slice(outHtml.indexOf("<script>\n/*") + 8, outHtml.lastIndexOf("</script>"))
  .replace(/\n\s*init\(\);\s*$/, "\n");
const outApi = new Function("window", "document", code + "\n" + outScript + `
  return { set(o) { outgoing = o; }, monthSummary, runLabels, gridHtml, TRUCKS };`)(
  window, { getElementById: () => ({}) });
const outData = JSON.parse(fs.readFileSync("outgoing.json", "utf8"));
outApi.set(outData);
const histKeys = new Set(Object.values(outData.days).flatMap(d => Object.keys(d.stillage)));
ok([...histKeys].every(k => outApi.TRUCKS.some(t => t.key === k)), `every truck in the log is a known truck (${[...histKeys].join(", ")})`);
ok(Object.values(outData.days).every(d => d.schmutz.every(g => g > 0)), "every Schmutz entry is a positive volume");
const outMonth = Object.keys(outData.days).sort().pop().slice(0, 7);
const handRuns = Object.entries(outData.days).filter(([d]) => d.startsWith(outMonth))
  .reduce((n, [, v]) => n + Object.values(v.stillage).reduce((a, b) => a + b, 0), 0);
const sum = outApi.monthSummary(outMonth);
ok(Object.values(sum.runs).reduce((a, b) => a + b, 0) === handRuns, `month run count matches the log (${outMonth}: ${handRuns})`);
ok(outApi.runLabels({ babybird: 1, mammabear: 2 }).join(" ") === "MB (2) BB (1)",
  "run labels follow the source site's style, in truck order");
ok(outApi.TRUCKS.find(t => t.key === "newbiedoo").name === "Newbie Doo", "the new truck is Newbie Doo");
const outCells = (outApi.gridHtml(outMonth).match(/class="cal-day(?!s)/g) || []).length;
ok(outCells % 7 === 0, "the outgoing report grid fills whole weeks");

// ── Customer details and gallons entered ──
// customer_details.json is written from the shared database and never
// committed (step 6), so a fresh clone has none: the checks below then skip.
const det = fs.existsSync("customer_details.json")
  ? JSON.parse(fs.readFileSync("customer_details.json", "utf8")).customers : null;
if (det) {
const placed = Object.values(det).filter(d => d.lat != null);
ok(placed.every(d => d.lat > 42 && d.lat < 45.5 && d.lon > -74.5 && d.lon < -70.5),
  `every map position is in the region (${placed.length} placed)`);
ok(Object.values(det).every(d => !/\(?\b\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}\b/.test(`${d.address} ${d.notes || ""}`)),
  "no phone numbers in customer addresses or notes");
ok(Object.values(det).every(d => !/\b(pass\s*code|passcode|pass|code|combo|pin|keypad|alarm)\s*(#|:|is)?\s*[\w-]*\d/i.test(d.notes || "")),
  "no door or lock codes in customer notes");
ok(snap.assign_list.every(a => String(a.customer_id) in det), "every Assign Routes customer has a details entry");
}
const s2 = api.freshStore(snap);
const r0 = snap.routes[0];
s2.my = { routeId: r0.route_id };
const first = r0.stops[0];
api.recordCollection(s2, { customer_id: first.customer_id, name: first.name }, 75);
api.recordCollection(s2, { customer_id: first.customer_id, name: first.name }, 80);
const today = s2.collections.filter(c => c.customer_id === first.customer_id);
ok(today.length === 1 && today[0].gallons === 80 && today[0].assignment_id === first.assignment_id,
  "a second entry the same day replaces the first, tied to the My Route stop");
api.recordCollection(s2, { customer_id: -999, name: "Off route" }, 1);
ok(api.todaysCollection(s2, -999).assignment_id === null, "an entry for a customer off My Route is kept, untied");

// ── Live route totals: a collected stop counts its actual gallons ──
{
  const pc = payload.customers;
  const ranked2 = pc.filter(c => c.section === "ranked" && !c.members && c.projected_gal > 5).slice(0, 2);
  const shared2 = pc.find(c => c.section === "ranked" && c.members && c.members.length > 1);
  const heldOut = pc.find(c => c.section !== "ranked");
  const st = c => ({ customer_id: c.id, name: c.name });
  const stops = [st(ranked2[0]), st(ranked2[1]), ...shared2.members.map(m => ({ customer_id: m.id, name: m.name })), st(heldOut)];
  const lines = api.routeSummary(stops, byId).lines;
  const y = ranked2[0].projected_gal, z = ranked2[1].projected_gal + shared2.projected_gal;
  const s3 = api.freshStore(snap);
  const close2 = (a, b) => Math.abs(a - b) < 1e-6;
  ok(close2(api.liveRouteTotals(lines, s3).projected, y + z), "before any collection the route reads y + z");
  api.recordCollection(s3, st(ranked2[0]), 120);
  let t = api.liveRouteTotals(lines, s3);
  ok(close2(t.projected, 120 + z) && t.collected === 120 && t.done === 1, "collecting x from the stop projected at y makes it x + z");
  api.recordCollection(s3, st(ranked2[0]), 1);
  ok(close2(api.liveRouteTotals(lines, s3).projected, z), "an Empty counts 0 in place of its projection");
  api.recordCollection(s3, { customer_id: shared2.members[1].id, name: "member" }, 40);
  t = api.liveRouteTotals(lines, s3);
  ok(close2(t.projected, ranked2[1].projected_gal + 40), "collecting one member of a shared barrel replaces the barrel's projection once");
  api.recordCollection(s3, st(heldOut), 75);
  ok(close2(api.liveRouteTotals(lines, s3).projected, ranked2[1].projected_gal + 40 + 75),
    "a held-out stop adds its actual gallons once collected");
}

// ── A theft is a collection in red, and keeps every version it was saved as ──
{
  const pc = payload.customers;
  const [a, b] = pc.filter(c => c.section === "ranked" && !c.members && c.projected_gal > 5).slice(0, 2);
  const st = c => ({ customer_id: c.id, name: c.name });
  const lines = api.routeSummary([st(a), st(b)], byId).lines;
  const s4 = api.freshStore(snap);
  const first = api.recordTheft(s4, st(a), 15, "Stolen 10/12, lock on barrel", new Date("2026-10-21T14:00:00Z"));
  let t = api.liveRouteTotals(lines, s4);
  ok(t.collected === 15 && t.done === 1, "gallons left after a theft count as collected, and the stop is done");
  ok(api.theftGallonsText(first) === "Theft · 15 gal", "a theft reads \"Theft · 15 gal\"");
  api.recordTheft(s4, st(a), 0, "", new Date("2026-10-21T15:00:00Z"));
  const e = api.todaysCollection(s4, a.id);
  ok(s4.collections.filter(c => c.customer_id === a.id).length === 1, "an edited theft is still one entry");
  ok(e.gallons === 0 && e.theft.note === "" && api.theftGallonsText(e) === "Theft · empty",
    "an edit replaces the shown gallons and note; 0 reads \"empty\"");
  ok(e.theft.versions.length === 2 && e.theft.versions[0].gallons === 15
    && e.theft.versions[0].note === "Stolen 10/12, lock on barrel" && e.theft.recorded_at === "2026-10-21T14:00:00.000Z",
    "the first account and its time survive the edit");
  ok(api.liveRouteTotals(lines, s4).collected === 0, "an emptied barrel counts 0, like an Empty");
  api.recordCollection(s4, st(b), 90);
  ok(!api.todaysCollection(s4, b.id).theft, "an ordinary collection carries no theft mark");
}

// ── Oil Left is a reading, never a collection ──
{
  const pc = payload.customers;
  const [a, b] = pc.filter(c => c.section === "ranked" && !c.members && c.projected_gal > 5).slice(0, 2);
  const st = c => ({ customer_id: c.id, name: c.name });
  const lines = api.routeSummary([st(a), st(b)], byId).lines;
  const s5 = api.freshStore(snap);
  const before = api.liveRouteTotals(lines, s5);
  api.recordOilLeft(s5, st(b), 120);
  let t = api.liveRouteTotals(lines, s5);
  ok(t.collected === 0 && t.done === 0 && t.projected === before.projected,
    "Oil Left at a skipped stop collects nothing, marks nothing done and leaves the route total alone");
  api.recordCollection(s5, st(a), 40);
  api.recordOilLeft(s5, st(a), 35);
  t = api.liveRouteTotals(lines, s5);
  ok(t.collected === 40 && t.done === 1 && s5.collections.length === 1,
    "after a part pickup only the 40 collected counts; the 35 left is not a pickup");
  ok(api.todaysOilLeft(s5, a.id).gallons === 35 && s5.oilLeft.length === 2, "each stop keeps its own Oil Left");
  api.clearOilLeft(s5, a.id);
  ok(!api.todaysOilLeft(s5, a.id) && api.todaysCollection(s5, a.id).gallons === 40, "removing Oil Left keeps the pickup");
}

// ── Full notes stay local ──
{
  const { execFileSync } = require("child_process");
  let ignored = false;
  try { execFileSync("git", ["check-ignore", "-q", "customer_notes.local.json"]); ignored = true; } catch (e) { /* not ignored */ }
  ok(ignored, "customer_notes.local.json (door codes, phones) is gitignored");
  const tracked = execFileSync("git", ["ls-files", "*.local.json"], { encoding: "utf8" }).trim();
  ok(tracked === "", "no *.local.json file is tracked by git");
  const details = execFileSync("git", ["ls-files", "customer_details.json"], { encoding: "utf8" }).trim();
  ok(details === "", "customer_details.json (addresses, notes) is not tracked by git");
}

// ── Collections tab: this site's entries stand in for the source site's on their day ──
{
  const history = [
    { date: "2026-10-14", gallons: 48 }, { date: "2026-10-09", gallons: 1, code: true },
    { date: "2026-09-30", gallons: 55 }, { date: "2026-09-30", gallons: 10 }];
  const site = [{ client_id: "a", date: "2026-10-14", gallons: 50 }, { client_id: "b", date: "2026-10-24", gallons: 2 },
    { client_id: "c", date: "2026-10-09", gallons: 1, theft: true }];
  const lines = api.collectionLines(history, site);
  ok(lines.map(l => l.date).join() === "2026-10-24,2026-10-14,2026-10-09,2026-09-30,2026-09-30", "newest first, one line per pickup");
  ok(lines[1].site && lines[1].gallons === 50 && lines[1].client_id === "a",
    "a day with a site entry shows the site's figure, never both added together");
  ok(lines[0].code && lines[2].code && lines[2].theft, "site 0-3 entries are codes; a theft stays marked");
  ok(lines.filter(l => !l.site).every(l => l.client_id === undefined) && lines.filter(l => !l.site).length === 2,
    "the source site's own pickups carry no id, so they are not editable here");
  ok(api.collectionLines(history, []).length === history.length, "without site entries the list is the source site's");
}

// ── No page posts to the source site ──
["ops-assign.html", "ops-myroute.html", "ops-calendar.html", "ops-outgoing.html", "ops-customer.html", "ops-common.js"].forEach(f => {
  const text = fs.readFileSync(f, "utf8");
  ok(!/<form[^>]+action=|method=["']?post|\.php["'?]?\s*,\s*\{|delete_routes|update_order|add_to_route/i.test(text),
    `${f} never submits to the source site`);
});

console.log(fails ? `${fails} FAILED` : "all shadow site checks passed");
process.exit(fails ? 1 : 0);
