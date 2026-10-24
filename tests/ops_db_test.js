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
    addShadowCustomer, createRoute, renameRoute, todaysCollection, todayIso, opsDiff, opsSharedPart,
    opsEnsureClientIds, opsStoreFromRows, opsDbAvailable, opsShared, opsWhere, shadowBannerHtml, OPS_STORAGE_KEY };`)(window);

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
ok(shared.nextId < -1000000000 && shared.nextId > -2147483648, "new ids start at a random point, inside the column's range");

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
s.trucks[rid] = "Newbie Doo";
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

  console.log(fails ? `\n${fails} FAILURE(S)` : "\nALL CHECKS PASSED");
  process.exit(fails ? 1 : 0);
})();
