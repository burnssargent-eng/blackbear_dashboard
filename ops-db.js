/*
 * The shared database for the shadow pages (Supabase; db/schema.sql). Loaded
 * after ops-common.js, with the supabase-js library before it.
 *
 * Signed in and on the team list (`people`), a page's store comes from the
 * database instead of this browser, and every saveStore() sends what changed:
 * the store is compared with the copy last sent, so the pages edit it exactly
 * as before. Changes wait in a queue kept on the device until they are sent,
 * so a save in a dead zone goes out once there is signal. Changes made by
 * anyone else arrive live and the page redraws.
 *
 * Not signed in, a page shows only the sign-in screen (email and password;
 * a device stays signed in until Sign out). Only if the library cannot load
 * does a page fall back to this browser's own copy, as before.
 *
 * Kept on the device: the queue, the last copy loaded (to open offline), and
 * which route is My Route (each phone drives its own).
 */

// The project address and key come from site-gate.js, loaded just before.
// A browser can pair a saved older page (no site-gate.js) with this file for a
// few minutes after a deploy: then the page stays on this browser's copy for
// that one load instead of failing.
const opsGateLoaded = typeof gateShow === "function";
// A saved older page (it loads the library but not site-gate.js) reloads
// once, which fetches the current page; the flag stops a reload loop.
(function opsReloadStalePage() {
  if (typeof window === "undefined" || !window.location || !window.sessionStorage) return;
  try {
    if (opsGateLoaded) { window.sessionStorage.removeItem("bbb-stale-reload"); return; }
    if (!window.supabase || window.sessionStorage.getItem("bbb-stale-reload")) return;
    window.sessionStorage.setItem("bbb-stale-reload", "1");
    window.location.reload();
  } catch (err) { /* storage blocked: stay on the browser copy */ }
})();
const OPS_DB_URL = opsGateLoaded ? GATE_DB_URL : "";
const OPS_DB_KEY = opsGateLoaded ? GATE_DB_KEY : "";
const OPS_QUEUE_KEY = "bbb-ops-queue-v1";
const OPS_CACHE_KEY = "bbb-ops-shared-cache-v1";
const OPS_COLLECTION_DAYS = 60;   // how far back pickups are loaded
const OPS_SEND_MS = 15000;        // one write gives up after this and is retried

let opsDb = null;   // set once signed in as a team member: { client, user, person, ... }

/* ── Comparing the store with what was last sent (no network: tested) ── */

const opsClone = x => JSON.parse(JSON.stringify(x));

/** The parts of a store that live in the database. */
function opsSharedPart(store) {
  return opsClone({ routes: store.routes || {}, newRoutes: store.newRoutes || [], added: store.added || [],
    collections: store.collections || [], outgoing: store.outgoing || [], oilLeft: store.oilLeft || [],
    trucks: store.trucks || {}, replaced: store.replaced || {} });
}

/**
 * A random id. crypto.randomUUID exists only on secure pages (https, localhost),
 * so a phone testing over the LAN (http://10.x...) builds one from random bytes.
 */
function opsRandomId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** Give each new pickup and outgoing entry the id that makes a resent save land once. */
function opsEnsureClientIds(store, makeId) {
  (store.collections || []).forEach(c => { if (!c.client_id) c.client_id = makeId(); });
  (store.outgoing || []).forEach(e => { if (!e.client_id) e.client_id = makeId(); });
}

/** A customer added on the site, as a `customers` row. */
function opsCustomerRow(c) {
  return { id: c.customer_id, name: c.name, address: c.address || null, city: c.city || null,
    capacity: c.capacity ?? null, periodicity_days: c.periodicity ?? null, phone: c.phone || null,
    notes: c.notes || null, shared_with: c.shared_with || null,
    collected_with_town: c.collected_with || null, active: true };
}

/**
 * The writes that turn `prev` into `next`, in an order the database accepts
 * (routes and customers before the stops and pickups that point at them).
 * `projectedFor(customerId)` freezes the projection on a new pickup.
 */
function opsDiff(prev, next, { today, projectedFor = () => null }) {
  const ops = [];
  const prevRoutes = new Map((prev.newRoutes || []).map(r => [r.route_id, r]));
  (next.newRoutes || []).forEach(r => {
    const p = prevRoutes.get(r.route_id);
    if (!p) ops.push({ t: "route-new", id: r.route_id, name: r.name });
    else if (p.name !== r.name) ops.push({ t: "route-name", id: r.route_id, name: r.name });
  });

  // A customer added on the site and since taken over is sent inactive, so an
  // edit never turns it back on.
  const nextReplaced = next.replaced || {}, prevReplaced = prev.replaced || {};
  const row = c => ({ ...opsCustomerRow(c), active: !nextReplaced[c.customer_id] });
  const prevAdded = new Map((prev.added || []).map(c => [c.customer_id, c]));
  const nextAdded = new Set();
  (next.added || []).forEach(c => {
    nextAdded.add(c.customer_id);
    const p = prevAdded.get(c.customer_id);
    if (!p || JSON.stringify(opsCustomerRow(p)) !== JSON.stringify(opsCustomerRow(c))) {
      ops.push({ t: "customer", row: row(c) });
    }
  });

  // Taking over from: after the new customer exists, before the stops that name it.
  Object.entries(nextReplaced).forEach(([old, by]) => {
    if (prevReplaced[old] !== by) ops.push({ t: "replace", id: Number(old), by });
  });
  Object.entries(prevReplaced).forEach(([old, by]) => {
    if (!(old in nextReplaced)) ops.push({ t: "unreplace", id: Number(old), by });
  });

  const routeIds = new Set([...Object.keys(prev.routes || {}), ...Object.keys(next.routes || {})]);
  routeIds.forEach(id => {
    const sig = list => JSON.stringify((list || []).map(s => [s.customer_id, !!s.must_get]));
    const after = (next.routes || {})[id] || [];
    if (sig((prev.routes || {})[id]) === sig(after)) return;
    const seen = new Set();
    const stops = after.filter(s => !seen.has(s.customer_id) && seen.add(s.customer_id))
      .map((s, k) => ({ customer_id: s.customer_id, position: k + 1, must_get: !!s.must_get,
        jamaal_assignment_id: s.assignment_id > 0 ? s.assignment_id : null }));
    ops.push({ t: "stops", route_id: Number(id), stops });
  });

  const values = c => ({ customer_id: c.customer_id, picked_on: c.date, gallons: c.gallons,
    route_id: c.route_id ?? null, theft: !!c.theft, theft_note: c.theft ? (c.theft.note || null) : null });
  const prevColl = new Map((prev.collections || []).map(c => [c.client_id, c]));
  const nextColl = new Set();
  (next.collections || []).forEach(c => {
    nextColl.add(c.client_id);
    const p = prevColl.get(c.client_id);
    if (!p) ops.push({ t: "pickup-new", row: { ...values(c), client_id: c.client_id,
      projected_gal: projectedFor(c.customer_id) } });
    else if (JSON.stringify(values(p)) !== JSON.stringify(values(c))) {
      ops.push({ t: "pickup-edit", client_id: c.client_id, values: values(c) });
    }
  });
  prevColl.forEach((c, id) => { if (!nextColl.has(id)) ops.push({ t: "pickup-delete", client_id: id }); });

  // Oil Left: one row per customer and day, so the pair is the key.
  const leftKey = e => `${e.customer_id}|${e.date}`;
  const leftRow = e => ({ customer_id: e.customer_id, checked_on: e.date, gallons_left: e.gallons,
    route_id: e.route_id ?? null });
  const prevLeft = new Map((prev.oilLeft || []).map(e => [leftKey(e), e]));
  const nextLeft = new Set();
  (next.oilLeft || []).forEach(e => {
    nextLeft.add(leftKey(e));
    const p = prevLeft.get(leftKey(e));
    if (!p || JSON.stringify(leftRow(p)) !== JSON.stringify(leftRow(e))) ops.push({ t: "left-set", row: leftRow(e) });
  });
  prevLeft.forEach((e, k) => {
    if (!nextLeft.has(k)) ops.push({ t: "left-delete", customer_id: e.customer_id, day: e.date });
  });

  // A site customer removed (Undo straight after Add): last, once no stop names it.
  prevAdded.forEach((c, id) => { if (!nextAdded.has(id) && id < 0) ops.push({ t: "customer-delete", id }); });

  const prevOut = new Set((prev.outgoing || []).map(e => e.client_id));
  const nextOut = new Set();
  (next.outgoing || []).forEach(e => {
    nextOut.add(e.client_id);
    if (!prevOut.has(e.client_id)) {
      ops.push({ t: "outgoing-new", row: { client_id: e.client_id, day: e.date, kind: e.kind,
        truck: e.truck || null, gallons: e.gallons ?? null } });
    }
  });
  prevOut.forEach(id => { if (!nextOut.has(id)) ops.push({ t: "outgoing-delete", client_id: id }); });

  const truckIds = new Set([...Object.keys(prev.trucks || {}), ...Object.keys(next.trucks || {})]);
  truckIds.forEach(id => {
    const before = (prev.trucks || {})[id] || "", after = (next.trucks || {})[id] || "";
    if (before === after) return;
    const truck = truckByName(after);
    ops.push(truck ? { t: "truck", route_id: Number(id), day: today, truck: truck.key }
      : { t: "truck-clear", route_id: Number(id), day: today });
  });
  return ops;
}

/**
 * Rows from the database as a store, in the shape the pages already use.
 * `keep` carries what stays on the device: My Route.
 */
function opsStoreFromRows(rows, snap, keep = {}) {
  const truckName = key => (TRUCKS.find(t => t.key === key) || {}).name || "";
  const names = new Map(rows.customers.map(c => [c.id, c]));
  const routes = {};
  rows.routes.forEach(r => { routes[r.id] = []; });
  rows.stops.slice().sort((a, b) => a.route_id - b.route_id || a.position - b.position || a.id - b.id)
    .forEach(s => {
      const c = names.get(s.customer_id) || {};
      (routes[s.route_id] = routes[s.route_id] || []).push({
        assignment_id: s.jamaal_assignment_id || -s.id, customer_id: s.customer_id,
        name: c.site_name || c.name || `#${s.customer_id}`, capacity: c.site_capacity ?? c.capacity ?? null,
        must_get: s.must_get });
    });
  const trucks = {};
  rows.trucks.slice().sort((a, b) => b.day.localeCompare(a.day)).forEach(t => {
    if (!(t.route_id in trucks)) trucks[t.route_id] = truckName(t.truck);
  });
  return {
    v: 1, shared: true, base: snap.scraped_at,
    // Ids for what this device makes (routes, customers): negative, from a
    // random point, so two phones never hand out the same one.
    nextId: -(1100000000 + Math.floor(Math.random() * 1000000000)),
    routes,
    newRoutes: rows.routes.filter(r => r.id < 0).map(r => ({ route_id: r.id, name: r.name })),
    added: rows.customers.filter(c => c.id < 0).map(c => ({ customer_id: c.id, name: c.name,
      address: c.address || "", city: c.city || "", capacity: c.capacity, periodicity: c.periodicity_days,
      phone: c.phone || "", notes: c.notes || "", shared_with: c.shared_with,
      collected_with: c.collected_with_town || null, added: c.created_on })),
    replaced: Object.fromEntries(rows.customers.filter(c => c.replaced_by).map(c => [c.id, c.replaced_by])),
    collections: rows.collections.map(c => ({ customer_id: c.customer_id,
      name: (names.get(c.customer_id) || {}).site_name || (names.get(c.customer_id) || {}).name || `#${c.customer_id}`,
      gallons: c.gallons,
      date: c.picked_on, route_id: c.route_id, assignment_id: null, client_id: c.client_id,
      ...(c.theft ? { theft: { note: c.theft_note || "", recorded_at: c.entered_at, versions: [] } } : {}) })),
    oilLeft: (rows.oilLeft || []).map(e => ({ customer_id: e.customer_id, date: e.checked_on,
      gallons: e.gallons_left, route_id: e.route_id })),
    outgoing: rows.outgoing.map(e => ({ id: e.client_id, client_id: e.client_id, date: e.day, kind: e.kind,
      ...(e.truck ? { truck: e.truck } : {}), ...(e.gallons !== null ? { gallons: e.gallons } : {}) })),
    trucks,
    my: keep.my || null,
  };
}

/* ── The connection ──────────────────────────────────────── */

function opsDbAvailable() {
  return opsGateLoaded && typeof window !== "undefined" && !!window.supabase && typeof window.supabase.createClient === "function";
}

function opsReadJson(key, fallback) {
  try { return JSON.parse(window.localStorage.getItem(key) || "null") ?? fallback; } catch (err) { return fallback; }
}
function opsWriteJson(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (err) { /* storage blocked */ }
}

/* ── Sync recorder: ?debug on a page's address shows a timed log of every
   request, send, login renewal and connection change, with a Copy button. ── */
const opsDebugOn = typeof location !== "undefined" && /[?&]debug\b/.test(location.search);
const opsDebugStart = Date.now();
const opsDebugLines = [];
function opsLog(...parts) {
  if (!opsDebugOn) return;
  const line = `${((Date.now() - opsDebugStart) / 1000).toFixed(1).padStart(6)}s  ${parts.join(" ")}`;
  opsDebugLines.push(line);
  const pre = document.getElementById("ops-debug-log");
  if (pre) { pre.textContent = opsDebugLines.slice(-200).join("\n"); pre.scrollTop = pre.scrollHeight; }
}
if (opsDebugOn) {
  document.addEventListener("DOMContentLoaded", () => {
    const box = document.createElement("div");
    box.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:9999;background:#111;color:#cfc;font:11px/1.35 monospace;";
    box.innerHTML = `<button type="button" style="float:right;margin:4px">Copy log</button>
      <pre id="ops-debug-log" style="max-height:35vh;overflow:auto;margin:0;padding:6px;white-space:pre-wrap"></pre>`;
    box.querySelector("button").onclick = () => navigator.clipboard.writeText(opsDebugLines.join("\n"))
      .then(() => opsLog("(log copied)"));
    document.body.appendChild(box);
    opsLog("page loaded; navigator.onLine =", navigator.onLine);
  });
  window.addEventListener("online", () => opsLog("EVENT online"));
  window.addEventListener("offline", () => opsLog("EVENT offline"));
}
const opsDebugFetch = (url, init) => {
  const u = String(url).replace(OPS_DB_URL, "").split("?")[0];
  const t0 = Date.now();
  opsLog("fetch start", (init && init.method) || "GET", u);
  return fetch(url, init).then(
    r => { opsLog("fetch done ", r.status, u, `${Date.now() - t0}ms`); return r; },
    e => { opsLog("fetch FAIL ", u, `${Date.now() - t0}ms`, e && e.name, e && e.message); throw e; });
};

const opsDbClient = opsDbAvailable()
  ? window.supabase.createClient(OPS_DB_URL, OPS_DB_KEY, { auth: { persistSession: true, detectSessionInUrl: true },
    ...(opsDebugOn ? { global: { fetch: opsDebugFetch } } : {}) })
  : null;
if (opsDbClient && opsDebugOn) {
  opsDbClient.auth.onAuthStateChange((event, session) => opsLog("AUTH", event,
    session ? `token expires ${new Date(session.expires_at * 1000).toLocaleTimeString()}` : "no session"));
}

/**
 * Resolves to the signed-in team member, or null: not signed in, or signed in
 * but not on the team list (`state` says which, for the banner).
 */
const opsDbSession = (async () => {
  if (!opsDbClient) return null;
  try {
    const { data } = await opsDbClient.auth.getSession();
    const user = data && data.session && data.session.user;
    if (!user) return null;
    const cache = opsReadJson(OPS_CACHE_KEY, null);
    const { data: people, error } = await opsDbClient.from("people").select("name,role").eq("user_id", user.id);
    if (error) {
      // Offline: trust a copy this same person loaded before.
      return cache && cache.user_id === user.id ? { user, person: cache.person, offline: true } : null;
    }
    gateRemember(people.length ? user.id : null);
    if (!people.length) return { user, person: null };
    return { user, person: people[0] };
  } catch (err) { return null; }
})();

const OPS_CUSTOMER_COLS = "id,name,city,address,capacity,periodicity_days,phone,notes,shared_with,created_on,lat,lon,loc";
// Name, town and capacity edited on the customer page (db/schema.sql); empty = the source site's.
const OPS_SITE_EDIT_COLS = "site_name,site_town,site_capacity,site_region,site_themes,site_status,site_status_since";

async function opsFetchRows() {
  const since = opsStoreSince();
  const all = async (q, label) => {
    const out = [];
    for (let from = 0; ; from += 1000) {
      const { data, error } = await q().range(from, from + 999);
      if (error) throw new Error(`${label}: ${error.message}`);
      out.push(...data);
      if (data.length < 1000) return out;
    }
  };
  const c = opsDbClient;
  const [routes, stops, customers, collections, outgoing, trucks, oilLeft, dailyNotes] = await Promise.all([
    all(() => c.from("routes").select("id,name,sort").order("id"), "routes"),
    all(() => c.from("route_stops").select("id,route_id,customer_id,position,must_get,jamaal_assignment_id").order("id"), "stops"),
    // Before collected_with_town and replaced_by exist (db/schema.sql not yet pasted) the pages load without them.
    all(() => c.from("customers").select(`${OPS_CUSTOMER_COLS},collected_with_town,replaced_by,${OPS_SITE_EDIT_COLS}`).order("id"), "customers")
      .catch(() => all(() => c.from("customers").select(`${OPS_CUSTOMER_COLS},collected_with_town,replaced_by`).order("id"), "customers"))
      .catch(() => all(() => c.from("customers").select(`${OPS_CUSTOMER_COLS},collected_with_town`).order("id"), "customers"))
      .catch(() => all(() => c.from("customers").select(OPS_CUSTOMER_COLS).order("id"), "customers")),
    // Only this site's entries: the source site's history is in the database
    // too (source 'jamaal'), but the pages already show it from the nightly files.
    all(() => c.from("collections").select("customer_id,picked_on,gallons,route_id,client_id,theft,theft_note,entered_at")
      .eq("source", "site").gte("picked_on", since).order("id"), "pickups"),
    all(() => c.from("outgoing").select("client_id,day,kind,truck,gallons").eq("source", "site").order("id"), "outgoing"),
    all(() => c.from("route_trucks").select("route_id,day,truck"), "trucks"),
    // Before the oil_left table exists the pages still load, without it.
    all(() => c.from("oil_left").select("customer_id,checked_on,gallons_left,route_id")
      .gte("checked_on", since).order("id"), "oil left").catch(() => []),
    // Notes written yesterday or today (older ones have cleared); none before the table exists.
    all(() => c.from("daily_notes").select("customer_id,day,note").gte("day", opsDayBefore(todayIso()))
      .order("customer_id"), "today's notes").catch(() => []),
  ]);
  return { routes, stops, customers, collections, outgoing, trucks, oilLeft, dailyNotes };
}

/**
 * The store for a page. Signed in as a team member: from the database (or the
 * last copy on this device when offline), with live updates calling
 * `onRemote(store)`. Otherwise this browser's shadow copy, as before.
 */
async function opsOpenStore(snap, { index = null, onRemote = null } = {}) {
  const session = await opsDbSession;
  if (!opsDbClient) return loadStore(snap);   // library did not load: this browser's copy
  if (!session || !session.person) {
    gateShow(opsDbClient, session);
    return new Promise(() => {});   // the page waits behind the sign-in screen; signing in reloads it
  }
  const cache = opsReadJson(OPS_CACHE_KEY, null);
  const mine = cache && cache.user_id === session.user.id ? cache : null;
  opsDb = { client: opsDbClient, user: session.user, person: session.person, snap, index, onRemote,
    queue: opsReadJson(OPS_QUEUE_KEY, []), failed: [], offline: false, synced: null, store: null,
    rows: null, flushing: null, refreshWanted: false };
  await opsFlush();
  let store;
  try {
    opsDb.rows = await opsFetchRows();
    opsApplySiteEdits(snap, index, opsDb.rows.customers);
    store = opsStoreFromRows(opsDb.rows, snap, { my: mine && mine.store ? mine.store.my : null });
  } catch (err) {
    if (!mine) throw new Error(`Could not load the shared routes (${err.message}).`);
    opsDb.offline = true;
    store = mine.store;
  }
  opsDb.store = store;
  opsDb.synced = opsSharedPart(store);
  opsCacheStore(store);
  opsListen();
  // "online" fires a moment before the connection works, so try a few times.
  window.addEventListener("online", () => [1000, 4000, 10000].forEach(ms =>
    setTimeout(() => { if (opsDb.queue.length) opsFlush().then(opsRefresh); }, ms)));
  setInterval(() => { if (opsDb.queue.length) opsFlush().then(opsRefresh); }, 10000);
  return store;
}

function opsCacheStore(store) {
  opsWriteJson(OPS_CACHE_KEY, { user_id: opsDb.user.id, person: opsDb.person, store });
}

/**
 * saveStore() in shared mode: queue what changed since the last send, and
 * send it. Returns false when not signed in, so saveStore keeps the browser copy.
 */
function opsDbSave(store) {
  if (!opsDb || !opsDb.person || !store.shared) return false;
  opsEnsureClientIds(store, opsRandomId);
  const row = opsDb.index ? id => opsDb.index.get(id) : () => null;
  const ops = opsDiff(opsDb.synced, opsSharedPart(store), { today: todayIso(),
    projectedFor: id => {
      const est = stopEstimate(row(id));
      return est.kind === "projected" ? Math.round(est.gal) : null;
    } });
  opsDb.synced = opsSharedPart(store);
  opsDb.store = store;
  opsCacheStore(store);
  if (ops.length) {
    opsDb.queue.push(...ops);
    opsWriteJson(OPS_QUEUE_KEY, opsDb.queue);
    opsFlush();
  }
  opsShowStatus();
  return true;
}

/**
 * Changes sent straight to the queue, for pickups older than the store holds
 * (the customer page edits a site pickup of any age). Waits offline like the rest.
 */
function opsQueueOps(ops) {
  if (!opsDb || !opsDb.person) return false;
  opsDb.queue.push(...ops);
  opsWriteJson(OPS_QUEUE_KEY, opsDb.queue);
  opsFlush();
  opsShowStatus();
  return true;
}

/** The first day the store holds pickups for; older ones come from opsSitePickups. */
function opsStoreSince() {
  return new Date(Date.now() - OPS_COLLECTION_DAYS * 86400000).toISOString().slice(0, 10);
}

/** Every pickup entered on this site for one customer, any age, or null when signed out or offline. */
async function opsSitePickups(customerId) {
  if (!opsDb || !opsDb.person) return null;
  const { data, error } = await opsDb.client.from("collections")
    .select("client_id,picked_on,gallons,route_id,theft,theft_note,projected_gal,entered_at")
    .eq("source", "site").eq("customer_id", customerId).order("picked_on", { ascending: false });
  if (error) return null;
  return data.map(r => ({ client_id: r.client_id, date: r.picked_on, gallons: r.gallons, route_id: r.route_id,
    theft: r.theft, theft_note: r.theft_note, projected_gal: r.projected_gal, entered_at: r.entered_at }));
}

/** One write. Returns "ok", "retry" (no signal: keep it) or "failed" (refused: set aside). */
async function opsSend(op) {
  // A request begun as the signal drops can hang for minutes and hold up the
  // whole queue: give up after OPS_SEND_MS (the fetch is cancelled) and retry.
  const sig = AbortSignal.timeout(OPS_SEND_MS);
  const c = { from: table => {
    const t = opsDb.client.from(table);
    const wrap = name => (...args) => t[name](...args).abortSignal(sig);
    return { upsert: wrap("upsert"), update: wrap("update"), delete: wrap("delete") };
  } };
  let res;
  switch (op.t) {
    case "route-new": res = await c.from("routes").upsert({ id: op.id, name: op.name, sort: 1000 }); break;
    case "route-name": res = await c.from("routes").update({ name: op.name }).eq("id", op.id); break;
    case "customer": res = await c.from("customers").upsert(op.row); break;
    case "replace":
      res = await c.from("customers").update({ replaced_by: op.by, active: false }).eq("id", op.id); break;
    case "unreplace":
      res = await c.from("customers").update({ replaced_by: null, active: true }).eq("id", op.id).eq("replaced_by", op.by);
      break;
    case "customer-delete": res = await c.from("customers").delete().eq("id", op.id); break;
    case "stops": {
      const ids = op.stops.map(s => s.customer_id);
      let del = c.from("route_stops").delete().eq("route_id", op.route_id);
      if (ids.length) del = del.not("customer_id", "in", `(${ids.join(",")})`);
      res = await del;
      if (!res.error && ids.length) {
        res = await c.from("route_stops").upsert(op.stops.map(s => ({ route_id: op.route_id, ...s })),
          { onConflict: "route_id,customer_id" });
      }
      break;
    }
    case "pickup-new":
      res = await c.from("collections").upsert(op.row, { onConflict: "client_id", ignoreDuplicates: true }); break;
    case "pickup-edit": res = await c.from("collections").update(op.values).eq("client_id", op.client_id); break;
    case "pickup-delete": res = await c.from("collections").delete().eq("client_id", op.client_id); break;
    case "outgoing-new":
      res = await c.from("outgoing").upsert(op.row, { onConflict: "client_id", ignoreDuplicates: true }); break;
    case "outgoing-delete": res = await c.from("outgoing").delete().eq("client_id", op.client_id); break;
    case "left-set":
      res = await c.from("oil_left").upsert(op.row, { onConflict: "customer_id,checked_on" }); break;
    case "left-delete":
      res = await c.from("oil_left").delete().eq("customer_id", op.customer_id).eq("checked_on", op.day); break;
    case "truck":
      res = await c.from("route_trucks").upsert({ route_id: op.route_id, day: op.day, truck: op.truck },
        { onConflict: "route_id,day" });
      break;
    case "truck-clear":
      res = await c.from("route_trucks").delete().eq("route_id", op.route_id).eq("day", op.day); break;
    default: return "failed";
  }
  if (res.error) opsLog("send error", op.t, "status", res.status, String(res.error.message || "").slice(0, 120));
  if (!res.error) return "ok";
  // No answer at all, a timeout, or a lapsed login: try again later.
  if (!res.status || res.status === 401 || res.status === 408 || res.status === 429 || res.status >= 500) return "retry";
  console.warn("[ops-db] refused", op, res.error);
  return "failed";
}

/** Send the queue in order; stop at the first change that cannot go yet. */
function opsFlush() {
  if (!opsDb || !opsDb.person) return Promise.resolve();
  if (opsDb.flushing) { opsLog("flush: one already running"); return opsDb.flushing; }
  opsLog("flush start, queue", opsDb.queue.length);
  // The busy mark is cleared in .finally, never inside the run: a run with
  // nothing to send finishes before the assignment below, and clearing it
  // there left every later flush waiting on a run that was already over.
  const run = async () => {
    while (opsDb.queue.length) {
      let result;
      try {
        // The timer also covers a wait before the request starts (a login refresh).
        result = await Promise.race([opsSend(opsDb.queue[0]),
          new Promise(done => setTimeout(() => done("retry"), OPS_SEND_MS + 5000))]);
      } catch (err) { result = "retry"; }
      opsLog("send", opsDb.queue[0].t, "->", result, `(queue ${opsDb.queue.length})`);
      if (result === "retry") { opsDb.offline = true; break; }
      opsDb.offline = false;
      const op = opsDb.queue.shift();
      if (result === "failed") opsDb.failed.push(op);
      opsWriteJson(OPS_QUEUE_KEY, opsDb.queue);
    }
  };
  opsDb.flushing = run().finally(() => {
    opsDb.flushing = null;
    opsShowStatus();
    if (!opsDb.queue.length && opsDb.refreshWanted) { opsDb.refreshWanted = false; opsRefresh(); }
  });
  return opsDb.flushing;
}

/* ── Notes: Today's note and the permanent Notes (My Route and the customer page) ── */

/** The calendar day before a "YYYY-MM-DD" day. */
function opsDayBefore(iso) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * Today's note for a customer, or "". A note shows the day it is written and all of the next day
 * (Jim writes them the evening before a route), then clears; the newest wins. Checked on every
 * draw, so a page left open overnight drops it too.
 */
function opsTodayNote(id) {
  const today = todayIso(), from = opsDayBefore(today);
  const rows = (opsDb && opsDb.rows && opsDb.rows.dailyNotes || [])
    .filter(n => n.customer_id === id && n.day >= from && n.day <= today);
  rows.sort((a, b) => (a.day < b.day ? 1 : -1));
  return rows.length ? rows[0].note : "";
}

/**
 * Save a customer's Notes and Today's note, sending only what differs from `was`. Notes go on the
 * customer (the nightly copies notes only for new customers, so the edit stays; it never reaches the
 * source site); Today's note is its own row dated today, so an edit restarts its two days. Saving
 * or emptying it removes yesterday's row, which would otherwise show again. Throws when not saved.
 */
async function opsSaveNotes(id, now, was) {
  const c = opsDb && opsDb.client;
  if (!c) throw new Error("sign in to edit notes");
  const notes = now.notes.trim(), today = now.today.trim(), day = todayIso();
  if (notes !== (was.notes || "").trim()) {
    const { error } = await c.from("customers").update({ notes: notes || null }).eq("id", id);
    if (error) throw new Error(error.message);
    const row = opsDb.rows && opsDb.rows.customers && opsDb.rows.customers.find(r => r.id === id);
    if (row) row.notes = notes;
  }
  if (today !== (was.today || "").trim()) {
    // Write today's first, then clear older ones, so a failed save never loses the note.
    const yesterday = opsDayBefore(day);
    if (today) {
      const { error } = await c.from("daily_notes").upsert({ customer_id: id, day, note: today }, { onConflict: "customer_id,day" });
      if (error) throw new Error(error.message);
    }
    const { error } = today
      ? await c.from("daily_notes").delete().eq("customer_id", id).eq("day", yesterday)
      : await c.from("daily_notes").delete().eq("customer_id", id).gte("day", yesterday);
    if (error) throw new Error(error.message);
    if (opsDb.rows) {
      opsDb.rows.dailyNotes = (opsDb.rows.dailyNotes || []).filter(r => !(r.customer_id === id && r.day >= yesterday));
      if (today) opsDb.rows.dailyNotes.push({ customer_id: id, day, note: today });
    }
  }
  return { notes, today };
}

/* ── Collected with: a one-off town's customer picked up on another town's run ── */

/** { town, with } for a customer collected with another town's run ("New Haven", "Middlebury"), or null. */
function opsCollectedWith(id) {
  const c = opsDb && opsDb.rows && (opsDb.rows.customers || []).find(r => r.id === id);
  return c && c.collected_with_town ? { town: c.city || "", with: c.collected_with_town } : null;
}

/** "New Haven · w/ Middlebury" (or "w/ Middlebury" with no town of its own). */
function opsCollectedWithText(cw) {
  return [cw.town, `w/ ${cw.with}`].filter(Boolean).join(" · ");
}

/** Set or clear (empty) the town whose run collects this customer. Throws when not saved. */
async function opsSaveCollectedWith(id, town) {
  const c = opsDb && opsDb.client;
  if (!c) throw new Error("sign in to change this");
  const value = String(town || "").trim() || null;
  const { error } = await c.from("customers").update({ collected_with_town: value }).eq("id", id);
  if (error) throw new Error(error.message);
  const row = opsDb.rows && opsDb.rows.customers && opsDb.rows.customers.find(r => r.id === id);
  if (row) row.collected_with_town = value;
}

/* ── Customer page edits: name, town, capacity, address, phones ── */

/**
 * Show the site's name and town edits on this page's copy of the source site's
 * lists (Assign list, Today's Routes, projection rows), so every Ops page uses
 * them at once; the nightly carries them into the files. The source site's own
 * value is kept beside each (_jamaal_name, ...) so clearing an edit restores it.
 */
function opsApplySiteEdits(snap, index, customers) {
  const by = new Map((customers || []).filter(c => c.id > 0).map(c => [c.id, c]));
  const fix = (obj, key, value) => {
    const orig = `_jamaal_${key}`;
    if (!(orig in obj)) obj[orig] = obj[key];
    obj[key] = value || obj[orig];
  };
  (snap && snap.assign_list || []).forEach(a => {
    const c = by.get(a.customer_id);
    if (c) { fix(a, "name", c.site_name); fix(a, "city", c.site_town); }
  });
  (snap && snap.routes || []).forEach(r => (r.stops || []).forEach(s => {
    const c = by.get(s.customer_id);
    if (c) fix(s, "name", c.site_name);
  }));
  if (index) by.forEach((c, id) => {
    const row = index.get(id);
    if (!row || row.id !== id || row.members) return;   // a shared barrel's row keeps its group name
    fix(row, "name", c.site_name);
    fix(row, "town", c.site_town);
    // A status marked since the projections were built shows at once; after
    // that the nightly's row says it (and knows when a closed season reopens).
    const fresh = c.site_status && c.site_status !== "open" && c.site_status_since
      && (!index.asOf || c.site_status_since >= index.asOf) && !(row.last_pickup > c.site_status_since);
    if (!("_held" in row)) row._held = null;
    if (fresh && !row._held) {
      row._held = { section: row.section, status: row.status, projected_gal: row.projected_gal,
        range_low: row.range_low, range_high: row.range_high, days_past_full: row.days_past_full, pct_full: row.pct_full };
      Object.assign(row, { projected_gal: null, range_low: null, range_high: null, days_past_full: null, pct_full: null });
    } else if (!fresh && row._held) {
      Object.assign(row, row._held);
      row._held = null;
    }
    if (fresh) Object.assign(row, c.site_status === "inactive"
      ? { section: "inactive", status: "inactive (marked on the site)" }
      : { section: "seasonal holdout", status: "closed for the season (marked on the site)" });
  });
}

/**
 * The status set on the customer page that still stands, or null (open):
 * { status: "inactive" | "closed_season" | "open", since: "YYYY-MM-DD" }.
 * A pickup after its day (`lastPickup`, ISO; over 3 gallons) ends it, as the
 * nightly does in the database.
 */
function opsSiteStatus(id, lastPickup = "") {
  const c = opsCustomerDbRow(id);
  if (!c || !c.site_status || !c.site_status_since) return null;
  if (lastPickup && lastPickup > c.site_status_since) return null;
  return { status: c.site_status, since: c.site_status_since };
}

/** Set (or clear, with null) a customer's status from `since`. Throws when not saved. */
async function opsSaveStatus(id, status, since) {
  const c = opsDb && opsDb.client;
  if (!c) throw new Error("sign in to change this");
  const up = { site_status: status || null, site_status_since: status ? since : null };
  const { error } = await c.from("customers").update(up).eq("id", id);
  if (error) throw new Error(error.message);
  const k = opsDb.rows && opsDb.rows.customers ? opsDb.rows.customers.findIndex(r => r.id === id) : -1;
  if (k >= 0) opsDb.rows.customers[k] = { ...opsDb.rows.customers[k], ...up };
  opsApplySiteEdits(opsDb.snap, opsDb.index, opsDb.rows && opsDb.rows.customers);
}

/** This customer's database row, or null. */
function opsCustomerDbRow(id) {
  return (opsDb && opsDb.rows && (opsDb.rows.customers || []).find(r => r.id === id)) || null;
}

/** "(802) 555-0100" or "802-555-0100" as +18025550100; anything not 10 digits as typed. */
function opsPhoneStored(text) {
  const raw = String(text || "").trim();
  const d = raw.replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "");
  return d.length === 10 ? `+1${d}` : raw;
}

/**
 * Save the customer page's edit form for a source-site customer (id > 0).
 * `now` and `opened` hold name, town, capacity, address, phone and
 * collected_with: now and as the form opened. A name, town or capacity is only
 * touched when it was changed: set to the source site's own value (the row's
 * name, city, capacity, which the nightly keeps writing) it empties the site_
 * column, so the source site is followed again; anything else is kept as the
 * site's edit. An address change empties `loc`: the nightly moves the pin.
 * Throws when not saved.
 */
async function opsSaveCustomer(id, now, opened) {
  const c = opsDb && opsDb.client;
  if (!c) throw new Error("sign in to edit");
  const row = opsCustomerDbRow(id) || {};
  const up = {};
  const set = (col, value) => { if ((row[col] ?? null) !== value) up[col] = value; };
  [["name", "site_name", "name"], ["town", "site_town", "city"], ["capacity", "site_capacity", "capacity"]]
    .forEach(([field, col, theirs]) => {
      if (now[field] === opened[field]) return;
      const v = now[field];
      set(col, v === "" || v === null || v === (row[theirs] ?? null) ? null : v);
    });
  if (now.region !== undefined && now.region !== opened.region) set("site_region", now.region || null);
  if (now.themes !== undefined && (now.themes || []).join(";") !== (opened.themes || []).join(";")) {
    up.site_themes = [...now.themes];   // an explicit list: [] = in neither theme
  }
  set("collected_with_town", now.collected_with || null);
  const address = now.address.trim() || null;
  if ((row.address || null) !== address) { up.address = address; up.loc = null; }
  const phone = opsPhoneStored(now.phone) || null;
  if ((row.phone || null) !== phone) {
    Object.assign(up, { phone, phone_source: phone ? "site" : null, phone_checked: phone ? todayIso() : null });
  }
  if (!Object.keys(up).length) return row;
  const { error } = await c.from("customers").update(up).eq("id", id);
  if (error) throw new Error(error.message);
  if (opsDb.rows && opsDb.rows.customers) {
    const k = opsDb.rows.customers.findIndex(r => r.id === id);
    if (k >= 0) opsDb.rows.customers[k] = { ...opsDb.rows.customers[k], ...up };
    opsApplySiteEdits(opsDb.snap, opsDb.index, opsDb.rows.customers);
  }
  return { ...row, ...up };
}

/** A customer's other phone numbers (customer_contacts), oldest first; [] when signed out or none. */
async function opsContactsLoad(id) {
  const c = opsDb && opsDb.client;
  if (!c) return [];
  const { data, error } = await c.from("customer_contacts").select("id,value,label")
    .eq("customer_id", id).eq("kind", "phone").order("id");
  return error ? [] : data;
}

/**
 * Make the customer's other phones match `now` ([{ id?, value, label }]),
 * given the list as loaded (`was`): new ones added, changed ones updated,
 * missing ones removed. Throws when not saved.
 */
async function opsContactsSave(id, now, was) {
  const c = opsDb && opsDb.client;
  if (!c) throw new Error("sign in to edit");
  const keep = new Set(now.filter(p => p.id).map(p => p.id));
  for (const p of now) {
    const value = opsPhoneStored(p.value), label = (p.label || "").trim() || null;
    if (!value) continue;
    const old = p.id && was.find(w => w.id === p.id);
    if (old && old.value === value && (old.label || null) === label) continue;
    const { error } = old
      ? await c.from("customer_contacts").update({ value, label }).eq("id", p.id)
      : await c.from("customer_contacts").insert({ customer_id: id, kind: "phone", value, label, source: "site" });
    if (error) throw new Error(error.message.includes("duplicate") ? `${value} is already listed` : error.message);
  }
  const gone = was.filter(w => !keep.has(w.id)).map(w => w.id);
  if (gone.length) {
    const { error } = await c.from("customer_contacts").delete().in("id", gone);
    if (error) throw new Error(error.message);
  }
}

/* ── Taking over from ── */

/** Accounts this one took over from, nearest first (A took over from B, which took over from C). */
function opsPredecessors(id) {
  const rows = (opsDb && opsDb.rows && opsDb.rows.customers) || [];
  const out = [];
  let cur = id;
  for (let n = 0; n < 10; n++) {
    const prev = rows.find(r => r.replaced_by === cur && !out.includes(r.id));
    if (!prev) break;
    out.push(prev.id);
    cur = prev.id;
  }
  return out;
}

/* ── Live updates ────────────────────────────────────────── */

let opsRefreshTimer = null;

function opsListen() {
  // A phone left asleep misses live updates, and a route edited from its old
  // copy would send yesterday's stops back over the 7am reset
  // (refresh-routes.yml): reload whenever the page is shown again.
  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") opsRefresh();
    });
  }
  if (!opsDb.client.channel) return;
  opsDb.client.channel("ops-shared")
    .on("postgres_changes", { event: "*", schema: "public" }, () => {
      clearTimeout(opsRefreshTimer);
      opsRefreshTimer = setTimeout(opsRefresh, 700);
    })
    .subscribe();
}

/**
 * Reload from the database and hand the page the new store. Waits while this
 * device still has changes to send, and while someone is typing in a box, so
 * a redraw never wipes what they are entering.
 */
async function opsRefresh() {
  if (!opsDb || !opsDb.person || !opsDb.onRemote) return;
  if (opsDb.queue.length) { opsDb.refreshWanted = true; return; }   // the flush calls back once the queue is empty
  const typing = document.activeElement && document.activeElement.matches("input, textarea, select");
  if (typing) {
    document.activeElement.addEventListener("blur", () => setTimeout(opsRefresh, 50), { once: true });
    return;
  }
  let rows;
  try { rows = await opsFetchRows(); } catch (err) { opsDb.offline = true; opsShowStatus(); return; }
  if (opsDb.queue.length) { opsDb.refreshWanted = true; return; }   // a save went in while loading
  const store = opsStoreFromRows(rows, opsDb.snap, { my: opsDb.store ? opsDb.store.my : null });
  opsApplySiteEdits(opsDb.snap, opsDb.index, rows.customers);
  const withTowns = r => (r && r.customers || []).filter(c => c.collected_with_town || c.site_name || c.site_town ||
    c.site_capacity != null || c.site_region || c.site_themes || c.site_status).map(c => [c.id, c.collected_with_town,
    c.site_name, c.site_town, c.site_capacity, c.site_region, c.site_themes, c.site_status, c.site_status_since]);
  const sameNotes = JSON.stringify(rows.dailyNotes) === JSON.stringify(opsDb.rows && opsDb.rows.dailyNotes)
    && JSON.stringify(withTowns(rows)) === JSON.stringify(withTowns(opsDb.rows));
  if (sameNotes && JSON.stringify(opsSharedPart(store)) === JSON.stringify(opsDb.synced)) return;   // nothing new
  opsDb.rows = rows;
  opsDb.store = store;
  opsDb.synced = opsSharedPart(store);
  opsDb.offline = false;
  opsCacheStore(store);
  opsDb.onRemote(store);
}

/* ── Banner, sign-in ─────────────────────────────────────── */

function opsStatusText() {
  if (!opsDb) return "";
  const parts = [];
  if (opsDb.queue.length) parts.push(`${opsDb.queue.length} waiting to send`);
  if (opsDb.failed.length) parts.push(`${opsDb.failed.length} not saved`);
  if (opsDb.offline) parts.push("offline");
  return parts.length ? parts.join(" · ") : "all saved";
}

function opsShowStatus() {
  const el = document.getElementById("ops-sync-status");
  if (el) {
    el.textContent = opsStatusText();
    el.className = opsDb && (opsDb.queue.length || opsDb.failed.length || opsDb.offline) ? "ops-sync-wait" : "";
  }
}

/** The shared-mode banner, or null when the page is on this browser's copy. */
function opsDbBannerHtml() {
  if (!opsDb || !opsDb.person) return null;
  return `<div class="ops-shadow ops-shared px-3 py-1 d-flex align-items-center gap-2">
    <span class="flex-grow-1"><strong>Shared</strong> · ${escapeHtml(opsDb.person.name)} ·
      <span id="ops-sync-status">${escapeHtml(opsStatusText())}</span></span>
    <button type="button" class="btn btn-sm btn-outline-dark py-0" data-ops-password>Password</button>
    <button type="button" class="btn btn-sm btn-outline-dark py-0" data-ops-signout>Sign out</button>
  </div>`;
}

/** Sign-out and change-password clicks in the green banner (signing in is site-gate.js's card). */
function opsWireSignIn(container) {
  container.addEventListener("click", async e => {
    if (!e.target.closest) return;
    if (e.target.closest("[data-ops-password]")) {
      const open = container.querySelector("[data-ops-password-form]");
      if (open) { open.remove(); return; }
      container.insertAdjacentHTML("beforeend", `<form class="ops-signin px-3 py-2 d-flex flex-wrap align-items-center gap-2" data-ops-password-form>
        <input type="password" class="form-control form-control-sm w-auto" name="password" placeholder="New password"
          required minlength="8" autocomplete="new-password">
        <button class="btn btn-sm btn-dark">Save password</button>
        <span class="small" data-ops-password-msg>At least 8 characters.</span>
      </form>`);
      container.querySelector("[data-ops-password-form] input").focus();
    } else if (e.target.closest("[data-ops-signout]")) {
      if (opsDb && opsDb.queue && opsDb.queue.length &&
          !window.confirm(`${opsDb.queue.length} change(s) have not been sent yet. Sign out and lose them?`)) return;
      await opsDbClient.auth.signOut();
      gateRemember(null);
      [OPS_QUEUE_KEY, OPS_CACHE_KEY].forEach(k => { try { window.localStorage.removeItem(k); } catch (err) { /* */ } });
      window.location.reload();
    }
  });
  container.addEventListener("submit", async e => {
    const form = e.target.closest && e.target.closest("[data-ops-password-form]");
    if (!form) return;
    e.preventDefault();
    form.querySelector("button").disabled = true;
    const { error } = await opsDbClient.auth.updateUser({ password: String(new FormData(form).get("password")) });
    form.querySelector("[data-ops-password-msg]").textContent = error ? `Not saved: ${error.message}` : "Password saved.";
    form.querySelector("input").value = "";
    form.querySelector("button").disabled = false;
  });
}

/** Full notes, addresses and phones for the details panels, from the database (signed-in members only). */
async function opsDbCustomerDetails() {
  const session = await opsDbSession;
  if (!session || !session.person) return null;
  const rows = opsDb && opsDb.rows ? opsDb.rows.customers : await (async () => {
    const out = [];
    for (let from = 0; ; from += 1000) {
      const { data, error } = await opsDbClient.from("customers").select("id,address,notes,phone,lat,lon,loc")
        .order("id").range(from, from + 999);
      if (error) return out;
      out.push(...data);
      if (data.length < 1000) return out;
    }
  })();
  const out = {};
  rows.forEach(c => { out[String(c.id)] = { address: c.address || "", notes: c.notes || "", phone: c.phone || "", lat: c.lat, lon: c.lon, loc: c.loc }; });
  return out;
}
