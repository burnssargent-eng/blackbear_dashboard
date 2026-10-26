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
    trucks: store.trucks || {} });
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
    notes: c.notes || null, shared_with: c.shared_with || null, active: true };
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

  const prevAdded = new Map((prev.added || []).map(c => [c.customer_id, c]));
  (next.added || []).forEach(c => {
    const p = prevAdded.get(c.customer_id);
    if (!p || JSON.stringify(opsCustomerRow(p)) !== JSON.stringify(opsCustomerRow(c))) {
      ops.push({ t: "customer", row: opsCustomerRow(c) });
    }
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
        name: c.name || `#${s.customer_id}`, capacity: c.capacity ?? null, must_get: s.must_get });
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
      phone: c.phone || "", notes: c.notes || "", shared_with: c.shared_with, added: c.created_on })),
    collections: rows.collections.map(c => ({ customer_id: c.customer_id,
      name: (names.get(c.customer_id) || {}).name || `#${c.customer_id}`, gallons: c.gallons,
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
  const [routes, stops, customers, collections, outgoing, trucks, oilLeft] = await Promise.all([
    all(() => c.from("routes").select("id,name,sort").order("id"), "routes"),
    all(() => c.from("route_stops").select("id,route_id,customer_id,position,must_get,jamaal_assignment_id").order("id"), "stops"),
    all(() => c.from("customers").select("id,name,city,address,capacity,periodicity_days,phone,notes,shared_with,created_on,lat,lon,loc").order("id"), "customers"),
    // Only this site's entries: the source site's history is in the database
    // too (source 'jamaal'), but the pages already show it from the nightly files.
    all(() => c.from("collections").select("customer_id,picked_on,gallons,route_id,client_id,theft,theft_note,entered_at")
      .eq("source", "site").gte("picked_on", since).order("id"), "pickups"),
    all(() => c.from("outgoing").select("client_id,day,kind,truck,gallons").eq("source", "site").order("id"), "outgoing"),
    all(() => c.from("route_trucks").select("route_id,day,truck"), "trucks"),
    // Before the oil_left table exists the pages still load, without it.
    all(() => c.from("oil_left").select("customer_id,checked_on,gallons_left,route_id")
      .gte("checked_on", since).order("id"), "oil left").catch(() => []),
  ]);
  return { routes, stops, customers, collections, outgoing, trucks, oilLeft };
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
  if (JSON.stringify(opsSharedPart(store)) === JSON.stringify(opsDb.synced)) return;   // nothing new
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

/** Full notes and addresses for the details panels, from the database (signed-in members only). */
async function opsDbCustomerDetails() {
  const session = await opsDbSession;
  if (!session || !session.person) return null;
  const rows = opsDb && opsDb.rows ? opsDb.rows.customers : await (async () => {
    const out = [];
    for (let from = 0; ; from += 1000) {
      const { data, error } = await opsDbClient.from("customers").select("id,address,notes,lat,lon,loc")
        .order("id").range(from, from + 999);
      if (error) return out;
      out.push(...data);
      if (data.length < 1000) return out;
    }
  })();
  const out = {};
  rows.forEach(c => { out[String(c.id)] = { address: c.address || "", notes: c.notes || "", lat: c.lat, lon: c.lon, loc: c.loc }; });
  return out;
}
