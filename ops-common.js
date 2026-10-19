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

// Header nav.
const OPS_NAV = [
  { key: "assign", label: "Assign Routes", href: "ops-assign.html" },
  { key: "myroute", label: "My Route", href: "ops-myroute.html" },
  { key: "calendar", label: "Calendar", href: "ops-calendar.html" },
  { key: "outgoing", label: "Outgoing", href: "ops-outgoing.html" },
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
    outgoing: [], trucks: {}, my: null };
}

function loadStore(snap) {
  try {
    const saved = JSON.parse(window.localStorage.getItem(OPS_STORAGE_KEY) || "null");
    if (saved && saved.v === 1 && saved.routes) return { outgoing: [], ...saved };
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
  // One line, so it costs a phone screen as little as possible.
  return `<div class="ops-shadow px-3 py-1 d-flex align-items-center gap-2">
    <span class="flex-grow-1"><strong>Shadow copy</strong> · changes stay on this device${changed
      ? ` · <strong>newer routes available</strong>` : ""}</span>
    <button type="button" class="btn btn-sm btn-outline-dark py-0" data-ops-reset
      title="Start again from the source site's routes read ${escapeHtml(new Date(snap.scraped_at).toLocaleString())}">Reset</button>
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

/**
 * Fill level for colour, as on the projections page: full at 100%, due from
 * the 75% pickup point, ok below; "held" for a customer with no projection.
 */
function fillLevel(row) {
  if (!row || row.pct_full === null || row.pct_full === undefined) return "held";
  return row.pct_full >= 100 ? "full" : row.pct_full >= 75 ? "due" : "ok";
}

/** A projection badge coloured by fill level, or "" when there is nothing to show. */
function projectionBadgeHtml(row) {
  const text = row ? projectionBadgeText(row) : "";
  return text ? `<span class="ops-proj ops-fill-${fillLevel(row)}">${escapeHtml(text)}</span>` : "";
}

/**
 * How well a name matches a search, for ordering results: 0 when the name
 * starts with it, 1 when a word in the name does, 2 for any other match.
 */
function searchRank(query, name) {
  const n = (name || "").toLowerCase();
  if (!query) return 0;
  if (n.startsWith(query)) return 0;
  if (n.split(/[^a-z0-9]+/).some(w => w.startsWith(query))) return 1;
  return 2;
}

/**
 * The figures after a customer's name, as the source site's Today's Routes
 * puts "(capacity) [days]" there:
 *   (~87 gal / 300) [28]   projected gallons over capacity, coloured by % full,
 *                          then days past full (negative = days still to full)
 *   (300) will-call        a customer with no projection: capacity, and why
 * `capacity` is the stop's own when the caller has one.
 */
function stopFiguresHtml(row, capacity) {
  const cap = capacity ?? (row && row.capacity) ?? null;
  const capText = cap === null ? "—" : formatNumber(cap);
  const est = stopEstimate(row);
  if (est.kind !== "projected") {
    return `<span class="graytext">(${capText})</span>` +
      (row ? ` <span class="ops-muted">${escapeHtml(row.status || row.section)}</span>` : "");
  }
  const days = row.days_past_full;
  return `<span class="ops-proj ops-fill-${fillLevel(row)}">(~${formatWhole(est.gal)} gal / ${capText})</span>` +
    (days === null || days === undefined ? "" : ` <span class="ops-days">[${Math.round(days)}]</span>`);
}

/** One stop's projection, for a list line: "76 proj. gal · 77% full". */
function projectionBadgeText(row) {
  const est = stopEstimate(row);
  if (est.kind === "projected") {
    return `${formatWhole(est.gal)} proj. gal` + (row.pct_full !== null && row.pct_full !== undefined
      ? ` · ${Math.round(row.pct_full)}% full` : "");
  }
  if (est.kind === "typical") return row.status || row.section;
  return "";
}

/* ── Month calendars (Calendar and Outgoing) ────────────── */

function shiftMonth(ym, delta) {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(ym) {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("default", { month: "long", year: "numeric" });
}

/**
 * The same day of the month `delta` months away, as the source site's buttons
 * step (09-18-26 | 11-18-26 around Oct 18). A day past the month's end becomes
 * its last day.
 */
function shiftAnchor(date, delta) {
  const [y, m, d] = date.split("-").map(Number);
  const target = new Date(y, m - 1 + delta, 1);
  const last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  return `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, "0")}-` +
    `${String(Math.min(d, last)).padStart(2, "0")}`;
}

/** "2026-09-18" -> "09-18-26", the source site's button label. */
function anchorLabel(date) {
  return `${date.slice(5, 7)}-${date.slice(8, 10)}-${date.slice(2, 4)}`;
}

/** "YYYY-MM" months from `thisMonth` back to `firstMonth`, newest first. */
function monthsBack(thisMonth, firstMonth) {
  const out = [];
  for (let ym = thisMonth; firstMonth && ym >= firstMonth; ym = shiftMonth(ym, -1)) out.push(ym);
  return out;
}

/**
 * The two green month buttons, centred, with the month picker and `extra` to
 * their right. Pages handle [data-anchor] clicks and #cal-month-pick changes.
 */
function calendarNavHtml(anchor, months, extra) {
  const prev = shiftAnchor(anchor, -1), next = shiftAnchor(anchor, 1);
  const month = anchor.slice(0, 7);
  return `<div class="cal-nav mb-2">
      <div></div>
      <div class="cal-nav-center">
        <button type="button" class="btn btn-success btn-lg" data-anchor="${prev}">${anchorLabel(prev)}</button>
        <button type="button" class="btn btn-success btn-lg" data-anchor="${next}">${anchorLabel(next)}</button>
      </div>
      <div class="cal-nav-right">
        <select class="form-select form-select-sm w-auto" id="cal-month-pick" aria-label="Month">
          ${months.map(ym => `<option value="${ym}"${ym === month ? " selected" : ""}>` +
            `${ym.slice(5, 7)}/${ym.slice(2, 4)}</option>`).join("")}
        </select>
        ${extra || ""}
      </div>
    </div>`;
}

/**
 * A month grid in the source site's style. `cell(date)` returns
 * { clickable, html } for each day; a clickable day carries data-day.
 */
function calendarGridHtml(ym, selectedDay, cell) {
  const [y, m] = ym.split("-").map(Number);
  const days = new Date(y, m, 0).getDate();
  const lead = new Date(y, m - 1, 1).getDay();
  const prevDays = new Date(y, m - 1, 0).getDate();
  const cells = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(n => `<div class="cal-name">${n}</div>`);
  for (let i = lead - 1; i >= 0; i--) cells.push(`<div class="cal-day cal-ignore">${prevDays - i}</div>`);
  for (let d = 1; d <= days; d++) {
    const date = `${ym}-${String(d).padStart(2, "0")}`;
    const c = cell(date);
    cells.push(`<div class="cal-day${date === selectedDay ? " cal-selected" : ""}${c.clickable ? " cal-has" : ""}"
        ${c.clickable ? `data-day="${date}" role="button" tabindex="0"` : ""}><span>${d}</span>${c.html}</div>`);
  }
  const tail = (7 - ((lead + days) % 7)) % 7;
  for (let i = 1; i <= tail; i++) cells.push(`<div class="cal-day cal-ignore">${i}</div>`);
  return `<div class="cal-days">${cells.join("")}</div>`;
}

/* ── Gallons entered in the shadow copy ─────────────────── */

/**
 * Record today's collection for a customer, as the source site's Add
 * Collection does (it stamps the day of entry). Codes 0-3 are stored as typed.
 * When the customer is a stop on My Route the entry is tied to that stop, so
 * the route shows it as done. A second entry the same day replaces the first.
 */
function recordCollection(store, customer, gallons) {
  const today = todayIso();
  const routeId = store.my ? Number(store.my.routeId) : null;
  const stop = routeId !== null
    ? storeStops(store, routeId).find(s => s.customer_id === customer.customer_id) : null;
  store.collections = store.collections.filter(c =>
    !(c.date === today && c.customer_id === customer.customer_id));
  store.collections.push({ customer_id: customer.customer_id, name: customer.name, gallons, date: today,
    route_id: stop ? routeId : null, assignment_id: stop ? stop.assignment_id : null });
}

/** Today's shadow entry for a customer, or null. */
function todaysCollection(store, customerId) {
  const today = todayIso();
  return store.collections.find(c => c.date === today && c.customer_id === customerId) || null;
}

/**
 * customer_details.json (addresses, notes, map positions), or an empty set
 * when missing. Its notes have phone numbers and door codes cut out, because
 * the site is public. A local preview may also have customer_notes.local.json
 * (gitignored, never published) with the full notes; when it loads, its notes
 * replace the cut ones.
 */
async function loadCustomerDetails() {
  let customers = {};
  try {
    const resp = await fetch(`customer_details.json?v=${Date.now()}`);
    if (resp.ok) customers = (await resp.json()).customers || {};
  } catch (err) { /* none */ }
  try {
    const resp = await fetch(`customer_notes.local.json?v=${Date.now()}`);
    if (resp.ok) {
      Object.entries(await resp.json()).forEach(([id, notes]) => {
        if (customers[id]) customers[id] = { ...customers[id], notes };
      });
    }
  } catch (err) { /* not a local preview */ }
  return customers;
}

/** Where a phone number or code was cut from a note, a link to it on the source site. */
const CUT_MARK = "[on source site]";
function notesHtml(id, notes) {
  return escapeHtml(notes).split(escapeHtml(CUT_MARK)).join(
    `<a href="https://jammal28.dreamhosters.com/tracking/customer.php?id=${encodeURIComponent(id)}"
       target="_blank" rel="noopener">on Jamaal ›</a>`);
}

/** A numbered red map pin, like the source site's route map. */
function routePinIcon(n, done) {
  return L.divIcon({
    className: "ops-pin",
    html: `<span class="ops-pin-dot${done ? " ops-pin-done" : ""}">${n}</span>`,
    iconSize: [26, 26], iconAnchor: [13, 13],
  });
}

function opsTileLayer() {
  return L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: "&copy; OpenStreetMap contributors",
  });
}

/** A 0-3 code as the customer pages show it: "1 empty", "2 sign-up call", "3 barrel delivery". */
const CODE_WORDS = { 0: "empty", 1: "empty", 2: "sign-up call", 3: "barrel delivery" };
function codeText(qty) {
  return CODE_WORDS[qty] ? `${qty} ${CODE_WORDS[qty]}` : String(qty);
}

/**
 * A route's projected total as the day goes on. A stop collected today counts
 * its actual gallons (0 for an "empty"), every other stop its projection: a
 * route projected at y + z reads x + z once the stop projected at y gives x.
 * A shared barrel counts once, collected if any of its stops is. A held-out
 * stop has no projection, so it adds nothing until collected.
 *   collected  gallons collected today on the route
 *   remaining  projected gallons still to collect (and `remainingHigh`, the
 *              range's high end)
 *   projected / high   collected + remaining, for the totals and the truck bar
 *   done       stops collected today
 */
function liveRouteTotals(lines, store) {
  const key = l => (l.row ? `r${l.row.id}` : `c${l.stop.customer_id}`);
  const collectedKeys = new Set();
  let collected = 0, done = 0;
  lines.forEach(l => {
    const c = todaysCollection(store, l.stop.customer_id);
    if (!c) return;
    done++;
    collected += c.gallons > 3 ? c.gallons : 0;
    collectedKeys.add(key(l));
  });
  let remaining = 0, remainingHigh = 0;
  lines.forEach(l => {
    if (l.dupOf !== null || collectedKeys.has(key(l)) || l.est.kind !== "projected") return;
    remaining += l.est.gal;
    remainingHigh += l.est.high ?? l.est.gal;
  });
  return { collected, remaining, remainingHigh, done,
    projected: collected + remaining, high: collected + remainingHigh };
}
