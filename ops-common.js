/*
 * Shared code for the shadow operations site (ops-*.html), loaded after
 * dashboard-utils.js and route-utils.js.
 *
 * The shadow site replays the source site's routing workflow on a COPY of its
 * data. The copy starts from jamaal_routes.json and lives in this browser's
 * storage: assignments, reorders and gallons entered here never reach the
 * source site, and never reach anyone else's browser. "Reset" starts over from
 * the latest snapshot.
 */

const OPS_STORAGE_KEY = "bbb-ops-shadow-v1";

// Header nav. Outgoing is not built yet.
const OPS_NAV = [
  { key: "assign", label: "Assign Routes", href: "ops-assign.html" },
  { key: "myroute", label: "My Route", href: "ops-myroute.html" },
  { key: "calendar", label: "Calendar", href: "ops-calendar.html" },
  { key: "dashboard", label: "Dashboard", href: "index.html" },
  { key: "projections", label: "Projections", href: "projections.html" },
  { key: "history", label: "Historical Totals", children: [
    { label: "Regions", href: "region.html" },
    { label: "Years", href: "year.html" },
    { label: "Customers", href: "customers.html" },
  ] },
  { key: "schmootz", label: "Schmootz", href: "schmootz.html" },
];

function opsNavHtml(current) {
  const items = OPS_NAV.map(item => {
    if (item.children) {
      return `<li class="nav-item dropdown">
        <a class="nav-link dropdown-toggle" href="#" role="button" data-bs-toggle="dropdown"
           aria-expanded="false">${escapeHtml(item.label)}</a>
        <ul class="dropdown-menu">${item.children.map(c =>
          `<li><a class="dropdown-item" href="${c.href}">${escapeHtml(c.label)}</a></li>`).join("")}</ul>
      </li>`;
    }
    if (!item.href) {
      return `<li class="nav-item"><span class="nav-link disabled" title="Not built yet">${escapeHtml(item.label)}</span></li>`;
    }
    const active = item.key === current;
    return `<li class="nav-item"><a href="${item.href}" class="nav-link${active ? " active" : ""}"` +
      `${active ? ' aria-current="page"' : ""}>${escapeHtml(item.label)}</a></li>`;
  }).join("");
  return `
  <nav class="navbar navbar-expand-lg navbar-light bg-light">
    <div class="container-fluid">
      <img src="logo.png" height="50" alt="Black Bear Biodiesel" onerror="this.style.display='none'">
      <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#ops-nav-items"
              aria-controls="ops-nav-items" aria-expanded="false" aria-label="Toggle navigation">
        <span class="navbar-toggler-icon"></span>
      </button>
      <div class="collapse navbar-collapse" id="ops-nav-items">
        <ul class="navbar-nav me-auto mb-2 mb-lg-0">${items}</ul>
      </div>
    </div>
  </nav>`;
}

/* ── Data ───────────────────────────────────────────────── */

async function loadOpsData() {
  const [projections, snap] = await Promise.all(
    ["oil_projections.json", "jamaal_routes.json"].map(f =>
      fetch(`${f}?v=${Date.now()}`).then(resp => {
        if (!resp.ok) throw new Error(`Could not load ${f} (HTTP ${resp.status})`);
        return resp.json();
      })));
  return { projections, snap, index: buildIndex(projections.customers || []) };
}

/**
 * Every route id once, in the Assign dropdown's order, named as Today's Routes
 * names it where it has stops (the two pages disagree: route 4 is "MamaBear"
 * in the dropdown and "Stowe/Wtbry/MRV" on Today's Routes).
 */
function opsRouteList(snap) {
  const todays = new Map((snap.routes || []).map(r => [r.route_id, r.name]));
  const seen = new Map();
  (snap.route_names || []).forEach(r => {
    if (seen.has(r.route_id)) {
      seen.get(r.route_id).aliases.push(r.name);
      return;
    }
    seen.set(r.route_id, { route_id: r.route_id, name: todays.get(r.route_id) || r.name,
      aliases: todays.has(r.route_id) && todays.get(r.route_id) !== r.name ? [r.name] : [] });
  });
  // A route with stops but missing from the dropdown still shows.
  (snap.routes || []).forEach(r => {
    if (!seen.has(r.route_id)) seen.set(r.route_id, { route_id: r.route_id, name: r.name, aliases: [] });
  });
  return [...seen.values()];
}

/* ── The shadow copy ────────────────────────────────────── */

function freshStore(snap) {
  const routes = {};
  (snap.routes || []).forEach(r => {
    routes[r.route_id] = r.stops.map(s => ({
      assignment_id: s.assignment_id, customer_id: s.customer_id, name: s.name,
      capacity: s.capacity, must_get: s.must_get,
    }));
  });
  return { v: 1, base: snap.scraped_at, nextId: -1, routes, added: [], collections: [],
    trucks: {}, my: null };
}

function loadStore(snap) {
  try {
    const saved = JSON.parse(window.localStorage.getItem(OPS_STORAGE_KEY) || "null");
    if (saved && saved.v === 1 && saved.routes) return saved;
  } catch (err) { /* blocked storage: work in memory */ }
  return freshStore(snap);
}

function saveStore(store) {
  try { window.localStorage.setItem(OPS_STORAGE_KEY, JSON.stringify(store)); } catch (err) { /* ignore */ }
}

/** Stops on a route in the shadow copy (an empty list for a route with none). */
function storeStops(store, routeId) {
  return store.routes[routeId] || [];
}

/**
 * Put customers on a route, as the source site's Assign button does. A
 * customer already on that route is skipped. Returns how many were added.
 */
function assignToRoute(store, routeId, customers, mustGet) {
  const stops = storeStops(store, routeId).slice();
  let added = 0;
  customers.forEach(c => {
    if (stops.some(s => s.customer_id === c.customer_id)) return;
    stops.push({ assignment_id: store.nextId--, customer_id: c.customer_id, name: c.name,
      capacity: c.capacity ?? null, must_get: !!mustGet });
    added++;
  });
  store.routes[routeId] = stops;
  return added;
}

/** Route names each customer is on in the shadow copy. */
function storeRoutesByCustomer(store, routeList, byId) {
  return routesByCustomer({ routes: routeList.map(r =>
    ({ name: r.name, stops: storeStops(store, r.route_id) })) }, byId);
}

/** A new customer added in the shadow copy; negative ids never clash with the site's. */
function addShadowCustomer(store, fields) {
  const id = store.nextId--;
  const c = { customer_id: id, name: fields.name.trim(), city: (fields.city || "").trim(),
    capacity: Number(fields.capacity) || null, periodicity: Number(fields.periodicity) || null,
    added: new Date().toISOString().slice(0, 10) };
  store.added.push(c);
  return c;
}

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The yellow strip on every shadow page, with the Reset button. */
function shadowBannerHtml(store, snap) {
  const changed = store.base !== snap.scraped_at;
  return `<div class="ops-shadow px-3 py-2">
    <strong>Shadow copy.</strong> Changes here stay in this browser and never reach the
    source site. Started from the source site's routes read
    ${escapeHtml(new Date(store.base).toLocaleString())}.
    ${changed ? `<strong>Newer routes were read ${escapeHtml(new Date(snap.scraped_at).toLocaleString())}.</strong>` : ""}
    <button type="button" class="btn btn-sm btn-outline-dark ms-2" data-ops-reset>Reset to the source site's routes</button>
  </div>`;
}

/** Wire the Reset button; `onReset` re-renders the page. */
function wireShadowReset(container, snap, onReset) {
  container.addEventListener("click", e => {
    if (!e.target.closest || !e.target.closest("[data-ops-reset]")) return;
    if (!window.confirm("Throw away every change in this browser and start again from the source site's routes?")) return;
    const store = freshStore(snap);
    saveStore(store);
    onReset(store);
  });
}

/** "Name · Town" style line for one stop's projection: "~76 gal · 77% full". */
function projectionBadgeText(row) {
  const est = stopEstimate(row);
  if (est.kind === "projected") {
    return `~${formatWhole(est.gal)} gal` + (row.pct_full !== null && row.pct_full !== undefined
      ? ` · ${Math.round(row.pct_full)}% full` : "");
  }
  if (est.kind === "typical") return row.status || row.section;
  return "";
}
