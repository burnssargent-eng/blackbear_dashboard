// Shared database (ops-db.js): what a save sends, with no network. The pages
// edit the store as before; saveStore compares it with the copy last sent, so
// every page action must turn into exactly the writes the database needs, and
// a store built from database rows must read back as the same routes.

process.chdir(require("path").join(__dirname, ".."));

const fs = require("fs");

const memory = {};
const window = {
  localStorage: {
    getItem: k => (k in memory ? memory[k] : null),
    setItem: (k, v) => { memory[k] = String(v); },
    removeItem: k => { delete memory[k]; },
  },
  confirm: () => true,
  // No supabase library: the page stays on this browser's copy.
};

const code = ["dashboard-utils.js", "route-utils.js", "ops-common.js", "site-gate.js", "ops-db.js"]
  .map(f => fs.readFileSync(f, "utf8")).join("\n");
const api = new Function("window", "const document = { getElementById: () => null };\n" + code + `
  return { gateCardHtml, setDb: v => { opsDb = v; }, getDb: () => opsDb, opsFlush, freshStore, saveStore, loadStore, assignToRoute, moveStop, removeStop, recordCollection, recordTheft,
    recordOilLeft, clearOilLeft, todaysOilLeft, oilLeftText, addShadowCustomer, createRoute, renameRoute, todaysCollection, todayIso, opsDiff, opsSharedPart,
    opsEnsureClientIds, opsStoreFromRows, opsDbAvailable, opsShared, opsWhere, shadowBannerHtml, OPS_STORAGE_KEY,
    opsTodayNote, opsSaveNotes, opsDayBefore, opsCollectedWith, opsCollectedWithText, opsSaveCollectedWith,
    takeOverCustomer, undoTakeOver, isReplaced, opsPredecessors };`)(window);

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log("  FAIL " + msg); } else console.log("  ok   " + msg);
};

const snap = JSON.parse(fs.readFileSync("jamaal_routes.json", "utf8"));
const route = snap.routes.find(r => r.stops.length >= 3);
const rid = route.route_id;
let n = 0;
const makeId = () => `cid-${++n}`;
const today = api.todayIso();
const send = (before, store) => {
  api.opsEnsureClientIds(store, makeId);
  return api.opsDiff(before, api.opsSharedPart(store), { today, projectedFor: () => 42 });
};

// ── Not signed in: nothing changes ──
ok(!api.opsDbAvailable(), "without the library the database is off");
ok(!api.opsShared() && api.opsWhere() === "this browser only", "pages say 'this browser only'");
const local = api.freshStore(snap);
api.saveStore(local);
ok(memory[api.OPS_STORAGE_KEY] !== undefined, "saveStore still keeps this browser's copy");
ok(!api.shadowBannerHtml(local, snap).includes("Sign in"), "without the library the page falls back to this browser's copy");

// ── Sign-in screen: email and password, no emailed link ──
const gate = api.gateCardHtml(null);
ok(/type="email"[^>]*autocomplete="username"/.test(gate) && /type="password"[^>]*autocomplete="current-password"/.test(gate),
  "the sign-in screen asks for email and password, in a form password managers fill");
ok(!/link/i.test(gate), "no emailed link");
const notListed = api.gateCardHtml({ user: { email: "x@example.com" }, person: null });
ok(notListed.includes("not on the team list") && notListed.includes("data-gate-signout") && !notListed.includes("password"),
  "signed in but not on the team list: told so, with Sign out");

// ── Mixed versions after a deploy: a saved older page can load this ops-db.js
// without site-gate.js (Safari did, 2026-10-24), and a new page can load an
// older ops-db.js. Neither may crash. ──
{
  const fakeSupabase = { createClient: () => { throw new Error("no client without site-gate.js"); } };
  const w = { ...window, supabase: fakeSupabase };
  const lone = ["dashboard-utils.js", "route-utils.js", "ops-common.js", "ops-db.js"].map(f => fs.readFileSync(f, "utf8")).join("\n");
  let res = null, err = null;
  try {
    res = new Function("window", "const document = { getElementById: () => null };\n" + lone +
      "\nreturn { client: opsDbClient, available: opsDbAvailable() };")(w);
  } catch (e) { err = e; }
  ok(!err && res.client === null && !res.available,
    "ops-db.js without site-gate.js stays on the browser copy instead of failing" + (err ? ` (${err.message})` : ""));
  const session = {};
  let reloads = 0;
  const stale = { ...w, location: { reload: () => { reloads++; } },
    sessionStorage: { getItem: k => session[k] || null, setItem: (k, v) => { session[k] = v; }, removeItem: k => { delete session[k]; } } };
  const run = () => new Function("window", "const document = { getElementById: () => null };\n" + lone)(stale);
  run(); run();
  ok(reloads === 1, "a saved older page reloads itself once to fetch the current page, never in a loop");
  let older = null;
  try { older = require("child_process").execSync("git show 5e414a7:ops-db.js", { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }); } catch (e) { /* shallow clone */ }
  if (older) {
    let clash = null;
    try { new Function("window", fs.readFileSync("site-gate.js", "utf8") + "\n" + older); } catch (e) { clash = e; }
    ok(!clash, "site-gate.js declares nothing an older ops-db.js also declares" + (clash ? ` (${clash.message})` : ""));
  }
}

// ── A store from the database reads as the same routes ──
const customers = snap.assign_list.map(a => ({ id: a.customer_id, name: a.name, capacity: 100 }));
route.stops.forEach(s => { if (!customers.some(c => c.id === s.customer_id)) customers.push({ id: s.customer_id, name: s.name }); });
let dbId = 0;
const rows = {
  routes: snap.route_names.map(r => ({ id: r.route_id, name: r.name })).concat([{ id: -7, name: "Made on site" }]),
  stops: route.stops.map((s, k) => ({ id: ++dbId, route_id: rid, customer_id: s.customer_id, position: k + 1,
    must_get: s.must_get, jamaal_assignment_id: s.assignment_id })),
  customers: customers.concat([{ id: -9, name: "Site customer", city: "Stowe", capacity: 55, created_on: "2026-10-24" }]),
  collections: [{ customer_id: route.stops[0].customer_id, picked_on: today, gallons: 60, route_id: rid,
    client_id: "c-old", theft: true, theft_note: "lock cut", entered_at: "2026-10-24T13:00:00Z" }],
  outgoing: [{ client_id: "o-old", day: today, kind: "stillage", truck: "mammabear", gallons: null }],
  trucks: [{ route_id: rid, day: "2026-10-20", truck: "babybird" }, { route_id: rid, day: "2026-10-23", truck: "mackdaddy" }],
  oilLeft: [{ customer_id: route.stops[2].customer_id, checked_on: today, gallons_left: 30, route_id: rid }],
};
const shared = api.opsStoreFromRows(rows, snap, { my: { routeId: rid } });
ok(shared.routes[rid].map(s => s.customer_id).join() === route.stops.map(s => s.customer_id).join(),
  "stops come back in position order");
ok(shared.routes[rid].map(s => s.assignment_id).join() === route.stops.map(s => s.assignment_id).join(),
  "source-site stops keep their assignment ids");
ok(shared.newRoutes.length === 1 && shared.newRoutes[0].route_id === -7, "routes made on the site come back as new routes");
ok(shared.added.length === 1 && shared.added[0].customer_id === -9 && shared.added[0].city === "Stowe",
  "customers made on the site come back as added customers");
ok(shared.trucks[rid] === "Mack Daddy", "a route's truck is its latest choice");
ok(shared.collections[0].theft && shared.collections[0].theft.note === "lock cut", "a theft comes back with its note");
ok(shared.outgoing[0].truck === "mammabear" && shared.outgoing[0].gallons === undefined, "a stillage run comes back");
ok(shared.my.routeId === rid, "My Route stays the device's own");
ok(api.todaysOilLeft(shared, route.stops[2].customer_id).gallons === 30 && shared.collections.length === 1,
  "Oil Left comes back as its own entry, not a pickup");
ok(api.opsStoreFromRows({ ...rows, oilLeft: undefined }, snap).oilLeft.length === 0,
  "before the oil_left table exists the store still loads, with no Oil Left");
ok(shared.nextId < -1000000000 && shared.nextId > -2147483648, "new ids start at a random point, inside the column's range");

// ── Taking over from: the old account replaced, the new one on its routes ──
{
  const st = api.opsStoreFromRows({ ...rows, customers: rows.customers.concat([
    { id: -20, name: "Old site account", city: "Stowe", replaced_by: -21 },
    { id: -21, name: "New site account", city: "Stowe", collected_with_town: "Waterbury" }]) }, snap);
  ok(st.replaced["-20"] === -21 && api.isReplaced(st, -20) && !api.isReplaced(st, -21),
    "a replaced customer comes back marked, and its new account does not");
  ok(st.added.find(c => c.customer_id === -21).collected_with === "Waterbury", "an added customer keeps its Collected with town");

  const before = api.opsSharedPart(shared);
  const work = JSON.parse(JSON.stringify(shared));
  const old = route.stops[1];
  const newC = api.addShadowCustomer(work, { name: "New owner", city: "Stowe", capacity: "", collected_with: "Waterbury" });
  const t = api.takeOverCustomer(work, { customer_id: old.customer_id, name: old.name, capacity: old.capacity }, newC);
  const ids = work.routes[rid].map(s => s.customer_id);
  ok(ids[1] === newC.customer_id && !ids.includes(old.customer_id) && ids.length === route.stops.length,
    "the new account takes the old one's place on its route, same position");
  const ops = api.opsDiff(before, api.opsSharedPart(work), { today });
  const at = type => ops.findIndex(o => o.t === type);
  ok(ops[at("customer")].row.collected_with_town === "Waterbury" && ops[at("customer")].row.periodicity_days === null,
    "the new customer is sent with its Collected with town and no Periodicity");
  ok(at("customer") < at("replace") && at("replace") < at("stops")
     && ops[at("replace")].id === old.customer_id && ops[at("replace")].by === newC.customer_id,
    "writes: new customer, then the old one marked replaced, then the route's stops");
  const stopOp = ops.find(o => o.t === "stops");
  ok(stopOp.stops[1].customer_id === newC.customer_id && stopOp.stops[1].jamaal_assignment_id === null,
    "the swapped stop is sent as a site stop");

  const afterAdd = api.opsSharedPart(work);
  api.undoTakeOver(work, t);
  ok(work.routes[rid].map(s => s.customer_id).join() === route.stops.map(s => s.customer_id).join()
     && !api.isReplaced(work, old.customer_id) && !work.added.some(c => c.customer_id === newC.customer_id),
    "Undo puts the old account back on its route and removes the new one");
  const undoOps = api.opsDiff(afterAdd, api.opsSharedPart(work), { today });
  const u = type => undoOps.findIndex(o => o.t === type);
  ok(u("unreplace") >= 0 && u("unreplace") < u("stops") && u("stops") < u("customer-delete")
     && undoOps[u("customer-delete")].id === newC.customer_id,
    "Undo writes: old account active again, stops back, then the new customer deleted");

  // A site customer taken over is sent inactive if it is ever re-sent.
  const w2 = JSON.parse(JSON.stringify(st));
  const b2 = api.opsSharedPart(w2);
  w2.added.find(c => c.customer_id === -20).notes = "edited";
  const o2 = api.opsDiff(b2, api.opsSharedPart(w2), { today });
  ok(o2.length === 1 && o2[0].row.id === -20 && o2[0].row.active === false, "an edit never turns a replaced site customer back on");

  api.setDb({ rows: { customers: [{ id: 5, replaced_by: 6 }, { id: 6, replaced_by: 7 }, { id: 7 }] } });
  ok(api.opsPredecessors(7).join() === "6,5" && api.opsPredecessors(5).length === 0,
    "an account's predecessors follow a chain of takeovers");
  api.setDb(null);
}

// ── Page actions -> writes ──
const synced = api.opsSharedPart(shared);
ok(send(synced, shared).length === 0, "an untouched store sends nothing");

let s = JSON.parse(JSON.stringify(shared));
s.routes[rid] = api.moveStop(s.routes[rid], 0, 2);
let ops = send(synced, s);
ok(ops.length === 1 && ops[0].t === "stops" && ops[0].route_id === rid, "a reorder sends that route's stops once");
ok(ops[0].stops.map(x => x.position).join() === ops[0].stops.map((x, k) => k + 1).join(), "positions are 1..n");
ok(ops[0].stops[2].customer_id === route.stops[0].customer_id, "in the new order");
ok(ops[0].stops.every(x => x.jamaal_assignment_id > 0), "source-site stops keep their assignment ids");

s = JSON.parse(JSON.stringify(shared));
const extra = snap.assign_list.find(a => !route.stops.some(x => x.customer_id === a.customer_id));
api.assignToRoute(s, rid, [{ customer_id: extra.customer_id, name: extra.name, capacity: 50 }], true);
ops = send(synced, s);
const added = ops[0].stops.find(x => x.customer_id === extra.customer_id);
ok(ops.length === 1 && added && added.must_get && added.jamaal_assignment_id === null, "Assign sends the route with the new Must Get stop");

s = JSON.parse(JSON.stringify(shared));
s.routes[rid] = api.removeStop(s.routes[rid], 1);
ops = send(synced, s);
ok(ops.length === 1 && ops[0].stops.length === route.stops.length - 1, "removing a stop sends the shorter route");

s = JSON.parse(JSON.stringify(shared));
const stop = s.routes[rid][1];
api.recordCollection(s, stop, 75);
ops = send(synced, s);
ok(ops.length === 1 && ops[0].t === "pickup-new" && ops[0].row.gallons === 75 && ops[0].row.picked_on === today &&
  ops[0].row.projected_gal === 42 && ops[0].row.client_id === s.collections[1].client_id, "Save sends one new pickup, projection frozen");
const afterSave = api.opsSharedPart(s);
api.recordCollection(s, stop, 80);
ops = send(afterSave, s);
ok(ops.length === 1 && ops[0].t === "pickup-edit" && ops[0].values.gallons === 80, "saving again the same day is an edit, not a new pickup");
const afterEdit = api.opsSharedPart(s);
{
  // The customer page's edit form on a pickup the store holds: gallons and date.
  const t = JSON.parse(JSON.stringify(s));
  const c = t.collections.find(x => x.customer_id === stop.customer_id);
  c.gallons = 64;
  c.date = "2026-10-01";
  const edit = send(afterEdit, t);
  ok(edit.length === 1 && edit[0].t === "pickup-edit" && edit[0].client_id === c.client_id &&
    edit[0].values.gallons === 64 && edit[0].values.picked_on === "2026-10-01",
    "the customer page's Update on a pickup is one edit, with its new gallons and date");
}
s.collections = s.collections.filter(c => c.customer_id !== stop.customer_id);   // My Route's Undo
ops = send(afterEdit, s);
ok(ops.length === 1 && ops[0].t === "pickup-delete" && ops[0].client_id === afterSave.collections[1].client_id,
  "Undo deletes the pickup");

s = JSON.parse(JSON.stringify(shared));
api.recordTheft(s, s.routes[rid][1], 0, "barrel empty, lid off", new Date());
ops = send(synced, s);
ok(ops.length === 1 && ops[0].t === "pickup-new" && ops[0].row.theft === true && ops[0].row.theft_note === "barrel empty, lid off",
  "a theft is a pickup with the theft flag and note");
const firstTheft = api.opsSharedPart(s);
api.recordTheft(s, s.routes[rid][1], 5, "barrel empty, lid off; 5 gal left", new Date());
ops = send(firstTheft, s);
ok(ops.length === 1 && ops[0].t === "pickup-edit" && ops[0].values.gallons === 5, "editing a theft is an edit (the history keeps the first)");

s = JSON.parse(JSON.stringify(shared));
s.my = { routeId: rid };
const part = s.routes[rid][1];
api.recordCollection(s, part, 40);
send(synced, s);   // the pickup goes first, with its id
const afterPart = api.opsSharedPart(s);
api.recordOilLeft(s, part, 35);
ops = send(afterPart, s);
ok(ops.length === 1 && ops[0].t === "left-set" && ops[0].row.gallons_left === 35 && ops[0].row.checked_on === today &&
  ops[0].row.route_id === rid && ops[0].row.customer_id === part.customer_id,
  "Oil Left after a part pickup sends one oil_left row and leaves the pickup alone");
ok(api.todaysCollection(s, part.customer_id).gallons === 40 && api.oilLeftText(api.todaysOilLeft(s, part.customer_id)) === "Oil left: 35 gal",
  "the pickup still reads 40 collected; the 35 left is shown apart");
const afterLeft = api.opsSharedPart(s);
api.recordOilLeft(s, part, 20);
ops = send(afterLeft, s);
ok(ops.length === 1 && ops[0].t === "left-set" && ops[0].row.gallons_left === 20 && s.oilLeft.length === 2,
  "a second Oil Left the same day replaces the first");
api.clearOilLeft(s, part.customer_id);
ops = send(afterLeft, s);
ok(ops.length === 1 && ops[0].t === "left-delete" && ops[0].customer_id === part.customer_id && ops[0].day === today,
  "Remove deletes that day's Oil Left");
s = JSON.parse(JSON.stringify(shared));
api.recordOilLeft(s, { customer_id: extra.customer_id, name: extra.name }, 0);
ops = send(synced, s);
ok(ops.length === 1 && ops[0].row.gallons_left === 0 && ops[0].row.route_id === null,
  "0 left is recorded; a customer not on My Route has no route");
const browser = api.freshStore(snap);
api.recordOilLeft(browser, { customer_id: extra.customer_id, name: extra.name }, 12);
ok(browser.oilLeft.length === 1 && browser.collections.length === 0, "the browser copy keeps Oil Left too, outside collections");

s = JSON.parse(JSON.stringify(shared));
s.trucks[rid] = "Cub";
ops = send(synced, s);
ok(ops.length === 1 && ops[0].t === "truck" && ops[0].truck === "newbiedoo" && ops[0].day === today, "a truck choice is today's, by key");
s.trucks[rid] = "";
ok(send(synced, s)[0].t === "truck-clear", "clearing the truck clears today's choice");

s = JSON.parse(JSON.stringify(shared));
s.outgoing.push({ id: s.nextId--, date: today, kind: "schmutz", gallons: 300 });
ops = send(synced, s);
ok(ops.length === 1 && ops[0].t === "outgoing-new" && ops[0].row.gallons === 300 && ops[0].row.truck === null, "Schmutz logs one outgoing row");
s.outgoing = s.outgoing.filter(e => e.client_id !== "o-old");
ops = send(synced, s);
ok(ops.some(o => o.t === "outgoing-delete" && o.client_id === "o-old"), "removing a logged run deletes it");

s = JSON.parse(JSON.stringify(shared));
const rNew = api.createRoute(s);
const cNew = api.addShadowCustomer(s, { name: "Pancake Hut", city: "Barre", capacity: "60", phone: "555" });
api.assignToRoute(s, rNew, [cNew], false);
api.renameRoute(s, rNew, "Barre run");
ops = send(synced, s);
const order = ops.map(o => o.t);
ok(order.indexOf("route-new") < order.indexOf("stops") && order.indexOf("customer") < order.indexOf("stops"),
  "a new route and customer go before the stop that points at them");
ok(ops.find(o => o.t === "route-new").name === "Barre run" && ops.find(o => o.t === "customer").row.phone === "555",
  "the route's name and the customer's phone go with them");
ok(rNew < -1000000000 && cNew.customer_id < -1000000000, "ids made on the device are far from the source site's");

// ── Sending the queue (opsFlush with a stand-in database) ──
(async () => {
  let online = true;
  const sent = [];
  const call = (table, kind) => ({
    abortSignal: () => {
      const req = { eq: () => req, not: () => req,
        then: (done, fail) => Promise.resolve(online
          ? (sent.push(`${kind} ${table}`), { error: null, status: 201 })
          : { error: { message: "TypeError: Load failed" }, status: 0 }).then(done, fail) };
      return req;
    },
  });
  const client = { from: t => ({ upsert: () => call(t, "upsert"), update: () => call(t, "update"), delete: () => call(t, "delete") }) };
  const truckOp = { t: "truck", route_id: rid, day: today, truck: "babybird" };
  api.setDb({ client, person: { name: "Test" }, user: { id: "u" }, queue: [], failed: [], offline: false,
    flushing: null, refreshWanted: false, onRemote: null });
  const db = api.getDb();

  await api.opsFlush();   // nothing to send, as when a page opens
  ok(db.flushing === null, "an empty flush leaves nothing marked as running");
  db.queue.push(truckOp);
  await api.opsFlush();
  ok(sent.length === 1 && db.queue.length === 0, "a save after an empty flush is sent (the 2026-10-24 bug)");

  online = false;
  db.queue.push({ ...truckOp, truck: "mammabear" });
  await api.opsFlush();
  ok(db.queue.length === 1 && db.offline && db.flushing === null, "offline: the change waits in the queue");
  online = true;
  await api.opsFlush();
  ok(db.queue.length === 0 && !db.offline && sent.length === 2, "back online: the next flush sends it");

  // Today's note shows the day it is written and all of the next, then clears, also on a page
  // left open (Jim writes them the night before a route).
  const RealDate = Date;
  const at = iso => { global.Date = class extends RealDate { constructor(...a) { super(...(a.length ? a : [iso])); } }; };
  const notesSent = [];
  // A delete ends on its second .eq (customer, day) or on .gte (customer, from a day on).
  const del = name => {
    const args = [];
    const chain = {
      eq: (_, v) => { args.push(v); if (args.length < 2) return chain; notesSent.push(["delete", name, ...args]); return { error: null }; },
      gte: (_, v) => { notesSent.push(["delete", name, ...args, ">=" + v]); return { error: null }; },
    };
    return chain;
  };
  const table = name => ({
    update: row => ({ eq: (_, id) => { notesSent.push(["update", name, id, row]); return { error: null }; } }),
    upsert: row => { notesSent.push(["upsert", name, row]); return { error: null }; },
    delete: () => del(name),
  });
  api.setDb({ client: { from: table }, rows: { customers: [{ id: 7, notes: "gate code" }], dailyNotes: [] } });
  at("2026-11-02T21:30:00");   // Jim, Monday night
  await api.opsSaveNotes(7, { notes: "gate code", today: " drop a barrel by the door " }, { notes: "gate code", today: "" });
  ok(notesSent[0][0] === "upsert" && notesSent[0][2].day === "2026-11-02" && notesSent[0][2].note === "drop a barrel by the door"
     && !notesSent.some(x => x[0] === "update"),
     "Today's note is saved for today, trimmed, and an unchanged Notes is not sent");
  ok(notesSent[1][0] === "delete" && notesSent[1][3] === "2026-11-01", "saving it clears yesterday's row, which would show again");
  ok(api.opsTodayNote(7) === "drop a barrel by the door", "it shows the day it is written");
  at("2026-11-03T23:59:00");
  ok(api.opsTodayNote(7) === "drop a barrel by the door", "and all of the next day (the route day)");
  at("2026-11-04T00:01:00");
  ok(api.opsTodayNote(7) === "", "it is gone the day after that, with the page still open");
  at("2026-11-03T10:00:00");
  api.getDb().rows.dailyNotes.push({ customer_id: 7, day: "2026-11-03", note: "both barrels full" });
  ok(api.opsTodayNote(7) === "both barrels full", "with yesterday's and today's rows, the newest shows");
  notesSent.length = 0;
  await api.opsSaveNotes(7, { notes: "gate code 1234", today: "" }, { notes: "gate code", today: "both barrels full" });
  ok(notesSent.length === 2 && notesSent[0][0] === "update" && notesSent[1][0] === "delete" && notesSent[1][3] === ">=2026-11-02"
     && api.opsTodayNote(7) === "", "emptying it deletes today's and yesterday's rows; Notes goes on the customer");
  ok(api.opsDayBefore("2026-03-01") === "2026-02-28" && api.opsDayBefore("2027-01-01") === "2026-12-31"
     && api.opsDayBefore("2026-11-02") === "2026-11-01", "the day before crosses month and year ends");
  global.Date = RealDate;

  // Collected with: a one-off town's customer picked up on another town's run.
  notesSent.length = 0;
  api.setDb({ client: { from: table }, rows: { customers: [
    { id: 1496, city: "New Haven", collected_with_town: "Middlebury" }, { id: 8, city: "Barre", collected_with_town: null }] } });
  const cw = api.opsCollectedWith(1496);
  ok(cw && cw.town === "New Haven" && cw.with === "Middlebury" && api.opsCollectedWithText(cw) === "New Haven · w/ Middlebury"
     && api.opsCollectedWith(8) === null && api.opsCollectedWith(9) === null,
     "collected with: \"New Haven · w/ Middlebury\" for a marked customer, nothing for the rest");
  ok(api.opsCollectedWithText({ town: "", with: "Middlebury" }) === "w/ Middlebury", "no town of its own: just w/ Middlebury");
  await api.opsSaveCollectedWith(1496, "  ");
  ok(notesSent[0][0] === "update" && notesSent[0][2] === 1496 && notesSent[0][3].collected_with_town === null
     && api.opsCollectedWith(1496) === null, "clearing it stores null and the marker goes");

  console.log(fails ? `\n${fails} FAILURE(S)` : "\nALL CHECKS PASSED");
  process.exit(fails ? 1 : 0);
})();
