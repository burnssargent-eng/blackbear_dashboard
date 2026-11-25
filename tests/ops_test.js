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
    recordOilLeft, todaysOilLeft, clearOilLeft, collectionLines, daysBracketHtml,
    resolveTownInput, VT_TOWNS, OUT_OF_STATE_TOWNS, TOWN_ALIASES, SPLIT_TOWNS, TOWN_CHOICES,
    stopFiguresHtml, projectionBadgeText, daysPast75,
    typedEntry, codeText, entryText, isCodeQty, CLOSING_QTY, todaysDrop, recordDrop, clearDrop, clearTodaysEntry,
    routeTasks, addTask, setTaskDone, setTaskRemoved, taskCarriedLabel, taskTitle, todayIso, localDay };`)(window);

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log("  FAIL " + msg); } else console.log("  ok   " + msg);
};

const snap = JSON.parse(fs.readFileSync("jamaal_routes.json", "utf8"));

// ── Add Customer's Town pick-list ──
{
  const geo = JSON.parse(fs.readFileSync("vermont_towns.geojson", "utf8"));
  const official = [...new Set(geo.features.map(f => f.properties.TOWNNAMEMC))].sort();
  ok(JSON.stringify([...api.VT_TOWNS].sort()) === JSON.stringify(official),
    `pick-list towns equal the GeoJSON's ${official.length} official names`);
  const strays = [...Object.values(api.TOWN_ALIASES), ...Object.values(api.SPLIT_TOWNS).flat()]
    .filter(t => !api.VT_TOWNS.includes(t));
  ok(!strays.length, `every alias and split resolves to an official town ${strays.join(", ")}`);
  const r = t => api.resolveTownInput(t);
  ok(r("  burlington ").town === "Burlington", "case and spacing forgiven");
  ok(r("St. Johnsbury").town === "Saint Johnsbury", '"St." reads as Saint');
  ok(r("White River Junction").town === "Hartford" && /Hartford/.test(r("White River Junction").note),
    "a village resolves to its town, with a note");
  ok(r("barre").town === "Barre" && r("Barre Town").town === "Barre Town", "Barre accepted as one name; City/Town still accepted");
  ok(api.TOWN_CHOICES.includes("Barre") && !api.TOWN_CHOICES.includes("Barre City"), "suggestions offer one Barre");
  ok(/Rutland City or Rutland Town/.test(r("Rutland").error || ""), "Rutland still asks City or Town");
  ok(r("berlin, nh").town === "Berlin, NH", "out of state kept as Place, ST");
  ok(r("Middlebury, VT").town === "Middlebury", '", VT" is dropped');
  ok(!!r("").error && !!r("Narnia").error, "blank and unknown towns are refused");
}
const payload = JSON.parse(fs.readFileSync("oil_projections.json", "utf8"));
const byId = api.buildIndex(payload.customers);

// ── Will-call / limited-data estimates show like a projection, labelled in grey ──
{
  const limited = payload.customers.find(c => c.section === "limited data" && c.projected_gal !== null);
  const ranked1 = payload.customers.find(c => c.section === "ranked" && c.projected_gal !== null);
  const figs = api.stopFiguresHtml(limited, limited.capacity);
  ok(figs.includes(`(~${Math.round(limited.projected_gal).toLocaleString()} gal / `) &&
    figs.includes('<span class="ops-muted">limited data</span>') && !/est\./.test(figs),
    `a limited-data stop reads (~gal / cap) with "limited data" in grey (${limited.name})`);
  ok(/ · limited data$/.test(api.projectionBadgeText(limited)), "its list badge ends with · limited data");
  ok(!/ops-muted/.test(api.stopFiguresHtml(ranked1, ranked1.capacity)), "a ranked stop has no grey label");
}

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

const c = api.addShadowCustomer(store, { name: "  Test Diner ", city: "Barre", capacity: "150", periodicity: "30" });
ok(c.periodicity === null, "Periodicity is never typed: worked out from pickups");
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
ok(nav.includes('href="ops-inactive.html"') && />Inactive</.test(nav), "Inactive is linked, named Inactive");
ok(nav.includes("data-ops-search-open") && nav.includes("data-ops-menu"), "the header has search and the menu");
ok(/ops-hdr-tab active" aria-current="page"><span class="ops-hdr-long">Assign Routes/.test(nav),
  "Assign Routes and My Route are always in the bar, the current one marked");
ok(/ops-calendar\.html[^]*ops-assign\.html[^]*ops-myroute\.html[^]*ops-hdr-wide/.test(nav),
  "the bar's tabs: Calendar, Assign Routes, My Route");
const menuHtml = nav.slice(nav.indexOf('id="ops-menu"'));
ok(/Outgoing[^]*Inactive[^]*Dashboard[^]*Historical Totals[^]*Regions[^]*Schmutz/.test(menuHtml) &&
  !menuHtml.includes("ops-calendar.html") && !/ops-menu-sec|ops-menu-line|>Ops<|>Analytics</.test(menuHtml),
  "the menu: one plain list, no labels or lines");
ok(/data-ops-menu-group="history" aria-expanded="false"[^]*▸[^]*class="ops-menu-group" hidden/.test(menuHtml),
  "Historical Totals starts closed, with an arrow");
ok(/logo-hdr\.png" width="210" height="150"/.test(nav), "the header logo is the small copy, its size fixed");

// ── Search (ops-search.js) ──
{
  const doc = { getElementById: () => null, activeElement: null };
  const searchCode = code + "\n" + fs.readFileSync("ops-search.js", "utf8");
  const s = new Function("window", "document", searchCode + `
    return { opsSearchResults, opsInactiveAccounts, opsMatchTier, opsNorm, opsSearchRouteOptions, opsSearchAssign,
      opsSearchPick, opsRecentIds, state: () => opsSearch, freshStore, loadStore, saveStore, storeStops, buildIndex,
      opsRouteList };`)(window, doc);
  const hist = JSON.parse(fs.readFileSync("customer_history.json", "utf8"));
  const proj = JSON.parse(fs.readFileSync("oil_projections.json", "utf8"));
  const index = s.buildIndex(proj.customers);
  const d = { snap, index, hist, urgency: new Map(proj.customers.filter(c => c.section === "ranked").map((c, i) => [c.id, i])) };
  const store = s.freshStore(snap);
  const assignIds = new Set(snap.assign_list.map(a => a.customer_id));
  const roster = JSON.parse(fs.readFileSync("oil_collections.json", "utf8")).customers;
  const inactive = s.opsInactiveAccounts(hist, assignIds, store);
  const expected = roster.filter(c => !assignIds.has(c.customer_id)).length;
  ok(inactive.length === expected, `Inactive lists every roster account off the Assign list (${inactive.length} of ${expected})`);
  ok(inactive.every(r => r.name && "ended" in r), "each inactive account has a name and how it ended");

  ok(s.opsNorm("Ken’s Pizza-Shelburne") === "kens pizza shelburne", "search ignores case, accents and punctuation");
  ok(s.opsMatchTier("pizza", ["Pizza Hut Berlin"]) === 0 && s.opsMatchTier("hut", ["Pizza Hut Berlin"]) === 1 &&
    s.opsMatchTier("zza", ["Pizza Hut Berlin"]) === 2 && s.opsMatchTier("berlin", ["Pho"], ["Berlin"]) === 3,
    "tiers: name starts, a word starts, anywhere, the town");
  ok(s.opsMatchTier("hardwik", ["Hardwick House of Pizza"]) === null &&
    s.opsMatchTier("hardwik", ["Hardwick House of Pizza"], [], { typos: true }) === 4,
    "one typo is forgiven only when asked for");
  ok(s.opsMatchTier("hrd", ["Hardwick"], [], { typos: true }) === null, "no typos under 4 letters");

  const first = snap.assign_list.find(a => a.name && d.index.get(a.customer_id));
  const byId = s.opsSearchResults(String(first.customer_id), d, store);
  ok(byId.customers[0] && byId.customers[0].id === first.customer_id, "a customer number finds that customer first");
  const pizza = s.opsSearchResults("pizza", d, store);
  ok(pizza.customers.length > 0 && !pizza.typos, "a plain search finds customers, no typos");
  const east = s.opsSearchResults("east", d, store).customers;
  const tierOf = c => s.opsMatchTier("east", [c.name], [c.town]);
  ok(east.every((c, i) => i === 0 || tierOf(east[i - 1]) < tierOf(c) || east[i - 1].active >= c.active),
    "within a tier, active customers come before inactive ones");
  const cal = s.opsSearchResults("cal", d, store);
  ok(cal.pages.map(p => p.label).join() === "Calendar", "pages match from a word's start (cal is not in historical)");
  ok(s.opsSearchResults("calender", d, store).typos && s.opsSearchResults("calender", d, store).pages[0].label === "Calendar",
    "a misspelt page is a close match");
  ok(s.opsSearchResults("schmootz", d, store).pages[0].label === "Schmutz", "an alias finds its page");
  const withPeople = { ...d, people: [{ name: "Jim", role: "Owner", phone: "+18025550100" },
    { name: "Patrick", role: "Driver", phone: "+18025550102" }] };
  ok(s.opsSearchResults("jim", withPeople, store).people.map(p => p.name).join() === "Jim", "people are found by name");
  ok(s.opsSearchResults("driver", withPeople, store).people.length === 0, "not by role");
  const someRoute = snap.route_names.find(r => r.name.length > 4);
  ok(s.opsSearchResults(someRoute.name.slice(0, 4), d, store).routes.length > 0, "route names are found");

  // + : the route set as My Route is offered first, and Assign puts the stop at the end.
  const st = s.state();
  st.data = d;
  const target = snap.routes.find(r => r.stops.length > 1);
  const saved = s.freshStore(snap);
  saved.my = { routeId: target.route_id };
  s.saveStore(saved);
  const loose = snap.assign_list.find(a => a.name && !snap.routes.some(r => r.stops.some(x => x.customer_id === a.customer_id)));
  s.opsSearchPick(loose.customer_id);
  ok(st.route === String(target.route_id), "the bar starts on the route set as My Route");
  const html = s.opsSearchRouteOptions(s.opsRouteList(snap, s.loadStore(snap)), s.loadStore(snap));
  ok(html.indexOf('label="My Route"') > -1 && html.indexOf('label="My Route"') < html.indexOf('label="Other routes"') &&
    html.indexOf("+ New Route") > html.indexOf('label="Other routes"') && html.includes("+ Task"),
    "My Route first, then the other routes, then + New Route and + Task");
  const before = s.storeStops(s.loadStore(snap), target.route_id).length;
  st.must = true;
  s.opsSearchAssign();
  const after = s.storeStops(s.loadStore(snap), target.route_id);
  ok(after.length === before + 1 && after[after.length - 1].customer_id === loose.customer_id && after[after.length - 1].must_get,
    "Assign adds the customer at the end of the chosen route, with Must get");
  ok(/Added .* \(stop \d+\)/.test(st.flash) && s.opsRecentIds()[0] === loose.customer_id, "it says so, and the customer is recent");
  s.opsSearchPick(loose.customer_id);
  s.opsSearchAssign();
  ok(s.storeStops(s.loadStore(snap), target.route_id).length === before + 1 && /already on/.test(st.flash),
    "adding it again changes nothing");
  const onNow = s.opsSearchResults(loose.name, d, s.loadStore(snap)).customers.find(c => c.id === loose.customer_id);
  ok(!!onNow, "it is still found");
}

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
ok([...codes.values()].flat().every(c => (c.qty >= 0 && c.qty <= 3) || c.qty === 7), "every listed code is 0-3 or a closing 7");
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
ok(outApi.TRUCKS.find(t => t.key === "newbiedoo").name === "Cub", "the new truck is Cub (key newbiedoo)");
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
  const heldOut = pc.find(c => c.section !== "ranked" && c.projected_gal === null);
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

// The [days] after a stop's figures is shown capped at ±90; the row keeps the real figure.
ok(api.daysBracketHtml(28.4).includes("[28]") && api.daysBracketHtml(90).includes("[90]")
   && api.daysBracketHtml(213).includes("[90]") && api.daysBracketHtml(-1661.3).includes("[-90]")
   && api.daysBracketHtml(-89.6).includes("[-90]") && api.daysBracketHtml(null) === "" && api.daysBracketHtml(0).includes("[0]"),
   "[days] shows capped at ±90, nothing without a figure");

// The [n] counts to the 75% pickup point, not to full: negative = days to go, positive = days past.
{
  const toGo = { section: "ranked", projected_gal: 70, capacity: 165, pct_full: 42, days_until_75: 30.6, days_past_full: -76.4 };
  const past = { ...toGo, days_until_75: -12.2, days_past_full: 20 };
  ok(api.daysPast75(toGo) === -30.6 && api.daysPast75(past) === 12.2 && api.daysPast75({}) === null && api.daysPast75(null) === null,
    "daysPast75: negative before 75%, positive after, null without a figure");
  ok(api.stopFiguresHtml(toGo, 165).includes("[-31]") && !api.stopFiguresHtml(toGo, 165).includes("[-76]")
     && api.stopFiguresHtml(past, 165).includes("[12]"), "a stop's [n] is days from 75%, not from full");
}

// ── Named outcomes, barrel drops and route tasks (Sarge, 2026-11-14) ──
{
  ok([0, 1, 2, 3, 4, 52].map(api.typedEntry).join() === "1,1,2,3,4,52",
    "a typed 0 saves as Empty (1); 2 and 3 stay; 4 and up are gallons");
  ok(api.codeText(1) === "Empty" && api.codeText(2) === "Sign-up call" && api.codeText(3) === "Barrel delivery" && api.codeText(7) === "Closing"
     && !/\d/.test(api.codeText(3)), "codes show by name, never the number");
  ok(api.entryText(3) === "Barrel delivery" && api.entryText(52) === "52 gal", "an entry reads as a name or gallons");
  ok(api.typedEntry(7) === api.CLOSING_QTY && api.entryText(7) === "Closing" && api.isCodeQty(7) && !api.isCodeQty(6)
     && !api.isCodeQty(8) && !api.isCodeQty(4), "a typed 7 is Closing, a code; 4, 6 and 8 are gallons");
  ok(api.collectionLines([], [{ date: "2026-11-19", gallons: 7, client_id: "x" }])[0].code === true,
    "a site 7 is listed as a code");
  ok(api.collectionLines([{ date: "2026-11-19", gallons: 15 }, { date: "2026-11-19", gallons: 7, code: true },
    { date: "2026-11-18", gallons: 20 }], []).map(l => l.gallons).join() === "7,15,20",
    "a closing 7 lists above the same day's pickup");

  const lines = api.collectionLines(
    [{ date: "2026-10-01", gallons: 0, code: true }, { date: "2026-10-02", gallons: 1, code: true },
     { date: "2026-10-03", gallons: 140 }],
    [{ client_id: "x", date: "2026-10-03", gallons: 3, extra: true }]);
  ok(!lines.some(l => l.date === "2026-10-01"), "a source-site 0 (written on reactivation) is hidden");
  ok(lines.some(l => l.date === "2026-10-03" && l.gallons === 140 && !l.site),
    "a barrel drop beside the day does not hide the source site's pickup that day");

  const s = api.freshStore(snap);
  const r = snap.routes.find(x => x.stops.length >= 2);
  const [a, b] = r.stops;
  s.my = { routeId: r.route_id };
  const drop = api.recordDrop(s, a, r.route_id);
  ok(drop.extra && drop.gallons === 3 && drop.route_id === r.route_id && api.todaysDrop(s, a.customer_id) === drop,
    "Barrel drop records a 3 beside the stop");
  ok(api.todaysCollection(s, a.customer_id) === null, "a drop does not close the stop");
  ok(api.recordDrop(s, a, r.route_id) === drop && s.collections.length === 1, "a second drop the same day is the same entry");
  api.recordCollection(s, a, 120);
  ok(api.todaysCollection(s, a.customer_id).gallons === 120 && api.todaysDrop(s, a.customer_id) === drop,
    "gallons saved after a drop sit beside it");
  api.recordCollection(s, a, 130);
  api.clearTodaysEntry(s, a.customer_id);
  ok(api.todaysCollection(s, a.customer_id) === null && api.todaysDrop(s, a.customer_id) === drop,
    "Edit/Undo of the stop's entry keeps the drop");
  api.recordCollection(s, b, 3);
  ok(api.todaysCollection(s, b.customer_id).gallons === 3 && api.todaysDrop(s, b.customer_id) === null,
    "a typed 3 is the stop's own entry (it closes the stop), not a drop");

  // Tasks
  const t1 = api.addTask(s, { routeId: r.route_id, text: "  Load cones " });
  const t2 = api.addTask(s, { routeId: r.route_id, kind: "barrel_drop", customerId: b.customer_id, note: "2 x 55" });
  ok(t1.text === "Load cones" && t1.position === 1 && t2.position === 2 && t2.text === "" && t2.note === "2 x 55",
    "tasks are added in order, words trimmed; a Barrel drop keeps only its note");
  ok(api.taskTitle(t2) === "Barrel drop" && api.routeTasks(s, r.route_id).length === 2
     && api.routeTasks(s, String(r.route_id)).length === 2, "both show on the route");
  const entry = api.setTaskDone(s, t2, true, { name: b.name });
  ok(entry && entry.extra && t2.drop_client_id === entry.client_id && api.todaysDrop(s, b.customer_id) === entry,
    "ticking a Barrel drop task records the delivery");
  api.setTaskDone(s, t2, false);
  ok(!api.todaysDrop(s, b.customer_id) && t2.drop_client_id === null && !t2.done_at, "unticking takes it back");
  api.setTaskDone(s, t2, true, { name: b.name });
  api.clearDrop(s, t2.drop_client_id);
  ok(!t2.done_at && !api.todaysDrop(s, b.customer_id), "undoing the drop on the stop unticks its task");
  const plain = api.setTaskDone(s, t1, true);
  ok(plain === null && s.collections.filter(c => c.extra).length === 1, "a plain task records nothing");

  const today = api.todayIso();
  const old = new Date(Date.now() - 3 * 86400000);
  t1.done_at = old.toISOString();
  ok(!api.routeTasks(s, r.route_id).includes(t1), "a task ticked on an earlier day leaves the list");
  t1.done_at = null;
  t1.day = api.localDay(old.toISOString());
  ok(api.routeTasks(s, r.route_id).includes(t1) && api.taskCarriedLabel(t1, today).length === 3,
    "an unticked task carries over, tagged with the weekday it was written");
  ok(api.taskCarriedLabel(t2, today) === "", "a task written today has no tag");
  api.setTaskRemoved(s, t1.client_id, true);
  ok(!api.routeTasks(s, r.route_id).includes(t1) && s.tasks.includes(t1), "Remove hides a task and keeps its row");
  api.setTaskRemoved(s, t1.client_id, false);
  ok(api.routeTasks(s, r.route_id).includes(t1), "Undo puts it back");
}

console.log(fails ? `${fails} FAILED` : "all shadow site checks passed");
process.exit(fails ? 1 : 0);
