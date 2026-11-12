/*
 * Shared code for the shadow operations site (ops-*.html), loaded after
 * dashboard-utils.js and route-utils.js.
 *
 * The shadow site replays the source site's routing workflow on a COPY of its
 * data. Not signed in, the copy starts from jamaal_routes.json and lives in
 * this browser's storage, and "Reset" starts over from the latest snapshot.
 * Signed in as a team member, the copy is the shared database (ops-db.js),
 * which Sarge and Jim both edit. Nothing here ever reaches the source site.
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
  { key: "schmootz", label: "Schmutz", href: "schmootz.html" },
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
  const index = buildIndex(projections.customers || []);
  index.asOf = projections.as_of || null;   // a status marked since then shows before the nightly says it
  return { projections, snap, index };
}

/**
 * Every route id once, in the Assign dropdown's order, named as Today's Routes
 * names it where it has stops (the two pages disagree: route 4 is "MamaBear"
 * in the dropdown and "Stowe/Wtbry/MRV" on Today's Routes). With the shadow
 * copy, the routes made in it ("New Route…") follow, under their own names.
 */
function opsRouteList(snap, store = null) {
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
  ((store && store.newRoutes) || []).forEach(r =>
    seen.set(r.route_id, { route_id: r.route_id, name: r.name, aliases: [], shadow: true }));
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
    outgoing: [], oilLeft: [], trucks: {}, my: null, newRoutes: [], replaced: {} };
}

function loadStore(snap) {
  try {
    const saved = JSON.parse(window.localStorage.getItem(OPS_STORAGE_KEY) || "null");
    if (saved && saved.v === 1 && saved.routes) return { outgoing: [], newRoutes: [], oilLeft: [], replaced: {}, ...saved };
  } catch (err) { /* blocked storage: work in memory */ }
  return freshStore(snap);
}

function saveStore(store) {
  // Signed in (ops-db.js): the change goes to the shared database instead.
  if (typeof opsDbSave === "function" && opsDbSave(store)) return;
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

/**
 * A new customer added in the shadow copy; negative ids never clash with the
 * site's. The source site's Add Customer fields, less zip code and
 * Periodicity (worked out from pickups, never typed). The phone
 * stays in this browser's storage: it never reaches the repo or the site.
 * `shared_with` is the customer id whose barrel this one shares;
 * `collected_with` the town whose run picks it up (as on the customer page).
 */
function addShadowCustomer(store, fields) {
  const id = store.nextId--;
  const text = k => String(fields[k] || "").trim();
  const c = { customer_id: id, name: text("name"), address: text("address"), city: text("city"),
    capacity: Number(fields.capacity) || null, periodicity: null,
    phone: text("phone"), notes: text("notes"), shared_with: Number(fields.shared_with) || null,
    collected_with: text("collected_with") || null,
    added: new Date().toISOString().slice(0, 10) };
  store.added.push(c);
  return c;
}

/* ── Taking over from: a place under a new owner gets its own account ── */

/**
 * The new customer `newId` takes over from `old` ({ customer_id, name, capacity }):
 * the old account is marked replaced (inactive for good: the nightly never turns
 * it back on) and the new one takes its place on every route. Returns what
 * undoTakeOver needs. The old account's pickups stay its own history.
 */
function takeOverCustomer(store, old, newC) {
  store.replaced = { ...(store.replaced || {}), [old.customer_id]: newC.customer_id };
  const swapped = [];
  Object.entries(store.routes || {}).forEach(([routeId, stops]) => {
    const i = stops.findIndex(s => s.customer_id === old.customer_id);
    if (i < 0) return;
    const prev = stops[i];
    swapped.push({ routeId, i, stop: prev });
    if (stops.some(s => s.customer_id === newC.customer_id)) stops.splice(i, 1);
    else stops[i] = { ...prev, assignment_id: -Math.abs(newC.customer_id) - i,
      customer_id: newC.customer_id, name: newC.name, capacity: newC.capacity ?? prev.capacity };
  });
  return { old, newId: newC.customer_id, swapped };
}

/** Undo a takeover straight after Add: the old account back, the new one gone. */
function undoTakeOver(store, t) {
  const replaced = { ...(store.replaced || {}) };
  delete replaced[t.old.customer_id];
  store.replaced = replaced;
  t.swapped.slice().reverse().forEach(({ routeId, i, stop }) => {
    const stops = (store.routes[routeId] = (store.routes[routeId] || []).filter(s => s.customer_id !== t.newId));
    if (!stops.some(s => s.customer_id === stop.customer_id)) stops.splice(Math.min(i, stops.length), 0, stop);
  });
  store.added = store.added.filter(c => c.customer_id !== t.newId);
}

/** True when a customer has been taken over by a new account (hidden from lists). */
function isReplaced(store, id) {
  return !!(store && store.replaced && store.replaced[id]);
}

/* ─────────────────────────────────────────────
 * Add Customer's Town pick-list
 * ───────────────────────────────────────────── */

/**
 * Vermont's official towns, exactly as vermont_towns.geojson names them
 * (TOWNNAMEMC): the names the map, the regions and the analytics match on.
 * tests/ops_test.js asserts this list equals the GeoJSON's.
 */
const VT_TOWNS = [
  "Addison", "Albany", "Alburgh", "Andover", "Arlington", "Athens", "Averill", "Avery's Gore",
  "Bakersfield", "Baltimore", "Barnard", "Barnet", "Barre City", "Barre Town", "Barton",
  "Belvidere", "Bennington", "Benson", "Berkshire", "Berlin", "Bethel", "Bloomfield", "Bolton",
  "Bradford", "Braintree", "Brandon", "Brattleboro", "Bridgewater", "Bridport", "Brighton",
  "Bristol", "Brookfield", "Brookline", "Brownington", "Brunswick", "Buels Gore", "Burke",
  "Burlington", "Cabot", "Calais", "Cambridge", "Canaan", "Castleton", "Cavendish", "Charleston",
  "Charlotte", "Chelsea", "Chester", "Chittenden", "Clarendon", "Colchester", "Concord", "Corinth",
  "Cornwall", "Coventry", "Craftsbury", "Danby", "Danville", "Derby", "Dorset", "Dover",
  "Dummerston", "Duxbury", "East Haven", "East Montpelier", "Eden", "Elmore", "Enosburgh", "Essex",
  "Essex Junction", "Fair Haven", "Fairfax", "Fairfield", "Fairlee", "Fayston", "Ferdinand",
  "Ferrisburgh", "Fletcher", "Franklin", "Georgia", "Glastenbury", "Glover", "Goshen", "Grafton",
  "Granby", "Grand Isle", "Granville", "Greensboro", "Groton", "Guildhall", "Guilford", "Halifax",
  "Hancock", "Hardwick", "Hartford", "Hartland", "Highgate", "Hinesburg", "Holland", "Hubbardton",
  "Huntington", "Hyde Park", "Ira", "Irasburg", "Isle La Motte", "Jamaica", "Jay", "Jericho",
  "Johnson", "Killington", "Kirby", "Landgrove", "Leicester", "Lemington", "Lewis", "Lincoln",
  "Londonderry", "Lowell", "Ludlow", "Lunenburg", "Lyndon", "Maidstone", "Manchester", "Marlboro",
  "Marshfield", "Mendon", "Middlebury", "Middlesex", "Middletown Springs", "Milton", "Monkton",
  "Montgomery", "Montpelier", "Moretown", "Morgan", "Morristown", "Mount Holly", "Mount Tabor",
  "New Haven", "Newark", "Newbury", "Newfane", "Newport City", "Newport Town", "North Hero",
  "Northfield", "Norton", "Norwich", "Orange", "Orwell", "Panton", "Pawlet", "Peacham", "Peru",
  "Pittsfield", "Pittsford", "Plainfield", "Plymouth", "Pomfret", "Poultney", "Pownal", "Proctor",
  "Putney", "Randolph", "Reading", "Readsboro", "Richford", "Richmond", "Ripton", "Rochester",
  "Rockingham", "Roxbury", "Royalton", "Rupert", "Rutland City", "Rutland Town", "Ryegate",
  "Saint Albans City", "Saint Albans Town", "Saint George", "Saint Johnsbury", "Salisbury",
  "Sandgate", "Searsburg", "Shaftsbury", "Sharon", "Sheffield", "Shelburne", "Sheldon", "Shoreham",
  "Shrewsbury", "Somerset", "South Burlington", "South Hero", "Springfield", "Stamford",
  "Stannard", "Starksboro", "Stockbridge", "Stowe", "Strafford", "Stratton", "Sudbury",
  "Sunderland", "Sutton", "Swanton", "Thetford", "Tinmouth", "Topsham", "Townshend", "Troy",
  "Tunbridge", "Underhill", "Vergennes", "Vernon", "Vershire", "Victory", "Waitsfield", "Walden",
  "Wallingford", "Waltham", "Wardsboro", "Warner's Grant", "Warren", "Warren's Gore", "Washington",
  "Waterbury", "Waterford", "Waterville", "Weathersfield", "Wells", "West Fairlee", "West Haven",
  "West Rutland", "West Windsor", "Westfield", "Westford", "Westminster", "Westmore", "Weston",
  "Weybridge", "Wheelock", "Whiting", "Whitingham", "Williamstown", "Williston", "Wilmington",
  "Windham", "Windsor", "Winhall", "Winooski", "Wolcott", "Woodbury", "Woodford", "Woodstock",
  "Worcester"
];

/** Out-of-state places customers have been in, as "Place, ST". Any other "Place, ST" is accepted too. */
const OUT_OF_STATE_TOWNS = [
  "Berlin, NH", "Claremont, NH", "Dalton, NH", "Hanover, NH", "Lancaster, NH",
  "Northumberland, NH", "West Lebanon, NH", "Plattsburgh, NY", "Pawtucket, RI",
];

/**
 * Villages, mailing names and old spellings people type, and the official
 * town each lies in. Mirrors the village part of GEO_TOWN_NAME_MAP in
 * oil_scraper.py, plus a few common ones with no customer yet.
 */
const TOWN_ALIASES = {
  "Hinesburgh": "Hinesburg", "Betlin": "Berlin", "Enosburg": "Enosburgh",
  "Enosburg Falls": "Enosburgh", "Enosburgh Falls": "Enosburgh", "Alburg": "Alburgh",
  "Lunenberg": "Lunenburg", "Montgomery Center": "Montgomery", "Craftsbury Common": "Craftsbury",
  "Jericho Center": "Jericho", "Eden Mills": "Eden", "Randolph Center": "Randolph",
  "South Pomfret": "Pomfret", "South Royalton": "Royalton", "Manchester Center": "Manchester",
  "Bolton Valley": "Bolton", "West Dover": "Dover", "Quechee": "Hartford",
  "White River Junction": "Hartford", "WRJ": "Hartford", "Wilder": "Hartford",
  "Morrisville": "Morristown", "West Glover": "Glover", "Jeffersonville": "Cambridge",
  "North Troy": "Troy", "Island Pond": "Brighton", "Wells River": "Newbury",
  "Lyndonville": "Lyndon", "Orleans": "Barton", "East Burke": "Burke",
  "Jacksonville": "Whitingham", "Lower Waterford": "Waterford", "Bellows Falls": "Rockingham",
  "Gilman": "Lunenburg", "Perkinsville": "Weathersfield", "Belmont": "Mount Holly",
  "East Barre": "Barre Town", "Graniteville": "Barre Town", "South Barre": "Barre Town",
  "S. Barre": "Barre Town", "Websterville": "Barre Town", "Underhill Flats": "Underhill",
  "Underhill Center": "Underhill", "Essex Center": "Essex", "Stowe Village": "Stowe",
  "Waterbury Center": "Waterbury", "Proctorsville": "Cavendish", "Saxtons River": "Rockingham",
  "North Bennington": "Bennington", "East Arlington": "Arlington", "Bomoseen": "Castleton",
  "Center Rutland": "West Rutland", "Hyde Park Village": "Hyde Park", "Jonesville": "Richmond",
};

/**
 * "Barre" is accepted as it is (Sarge, 2026-11-02): it lists as one town
 * everywhere (townLabel), and the scraper files a bare "Barre" under Barre
 * City on the map, as it does the source site's. Barre City and Barre Town
 * are still accepted when someone knows which.
 */
const ONE_NAME_TOWNS = ["Barre"];

/** The Town suggestions: official towns, with Barre offered as one name. */
const TOWN_CHOICES = [...ONE_NAME_TOWNS, ...VT_TOWNS.filter(t => !/^Barre (City|Town)$/.test(t))]
  .sort((a, b) => a.localeCompare(b));

/**
 * Town suggestions drawn by the page (Sarge, 2026-11-12), in place of the
 * browser's <datalist>: Chrome drew that popup in the Mac's dark mode, white
 * text on a light list, whatever the page declared. Any input with
 * data-town-suggest gets it: typed letters match the start of a town first,
 * then anywhere in it; tap or Enter picks one (an input and a change event
 * follow, as typing would); Escape or leaving the field closes it.
 */
const TOWN_SUGGEST_MAX = 8;
const townSuggest = { input: null, box: null, items: [], active: -1 };

function townSuggestMatches(text) {
  const q = String(text || "").trim().toLowerCase();
  if (!q) return [];
  const all = [...TOWN_CHOICES, ...(typeof OUT_OF_STATE_TOWNS !== "undefined" ? OUT_OF_STATE_TOWNS : [])];
  const starts = all.filter(t => t.toLowerCase().startsWith(q));
  const inside = all.filter(t => !t.toLowerCase().startsWith(q) && t.toLowerCase().includes(q));
  return [...starts, ...inside].slice(0, TOWN_SUGGEST_MAX);
}

function townSuggestClose() {
  if (townSuggest.box) townSuggest.box.remove();
  Object.assign(townSuggest, { input: null, box: null, items: [], active: -1 });
}

function townSuggestDraw(input) {
  const items = townSuggestMatches(input.value);
  if (!items.length || (items.length === 1 && items[0] === input.value.trim())) { townSuggestClose(); return; }
  if (!townSuggest.box || townSuggest.input !== input) {
    townSuggestClose();
    const box = document.createElement("div");
    box.className = "ops-suggest";
    box.setAttribute("role", "listbox");
    box.addEventListener("pointerdown", e => e.preventDefault());   // keep the field focused
    box.addEventListener("click", e => {
      const el = e.target.closest("[data-suggest]");
      if (el) townSuggestPick(Number(el.dataset.suggest));
    });
    document.body.appendChild(box);
    Object.assign(townSuggest, { input, box });
  }
  townSuggest.items = items;
  townSuggest.active = -1;
  townSuggest.box.innerHTML = items.map((t, k) =>
    `<div class="ops-suggest-item" role="option" data-suggest="${k}">${escapeHtml(t)}</div>`).join("");
  const r = input.getBoundingClientRect();
  Object.assign(townSuggest.box.style, { left: `${r.left + window.scrollX}px`, top: `${r.bottom + window.scrollY + 2}px`,
    width: `${r.width}px` });
}

function townSuggestPick(k) {
  const input = townSuggest.input;
  const town = townSuggest.items[k];
  if (!input || town === undefined) return;
  input.value = town;
  townSuggestClose();
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

// Wired once per page; the test harness loads this file without a document.
if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
  document.addEventListener("input", e => {
    if (e.target.matches && e.target.matches("input[data-town-suggest]") && e.isTrusted) townSuggestDraw(e.target);
  });
  document.addEventListener("keydown", e => {
    if (!townSuggest.box || e.target !== townSuggest.input) return;
    const n = townSuggest.items.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const was = townSuggest.active;
      townSuggest.active = e.key === "ArrowDown" ? (was + 1) % n : (was <= 0 ? n - 1 : was - 1);
      [...townSuggest.box.children].forEach((el, k) => el.classList.toggle("ops-suggest-on", k === townSuggest.active));
    } else if (e.key === "Enter" && townSuggest.active >= 0) {
      e.preventDefault();
      townSuggestPick(townSuggest.active);
    } else if (e.key === "Escape") {
      townSuggestClose();
    }
  });
  document.addEventListener("focusout", e => { if (e.target === townSuggest.input) townSuggestClose(); });
  window.addEventListener("resize", townSuggestClose);
}

/** Names that are two towns on the map; the person adding has to say which. */
const SPLIT_TOWNS = {
  "newport": ["Newport City", "Newport Town"],
  "rutland": ["Rutland City", "Rutland Town"],
  "saint albans": ["Saint Albans City", "Saint Albans Town"],
};

/**
 * What a person typed in Town, as the official name to save, or why not:
 * { town, note? } or { error }. Case and spacing are forgiven, "St." reads as
 * "Saint", a village resolves to its town (with a note saying so), and
 * "Place, ST" outside Vermont is kept as an out-of-state place.
 */
function resolveTownInput(text) {
  const raw = String(text || "").trim().replace(/\s+/g, " ");
  if (!raw) return { error: "Pick the customer's town." };
  const out = raw.match(/^(.+?),\s*([A-Za-z]{2})$/);
  if (out && out[2].toUpperCase() !== "VT") {
    const place = out[1].replace(/\b\w/g, ch => ch.toUpperCase());
    return { town: `${place}, ${out[2].toUpperCase()}` };
  }
  const name = (out ? out[1] : raw).replace(/^st\.?\s+/i, "Saint ");
  const key = name.toLowerCase();
  const town = [...ONE_NAME_TOWNS, ...VT_TOWNS].find(t => t.toLowerCase() === key);
  if (town) return { town };
  const alias = Object.keys(TOWN_ALIASES).find(a => a.toLowerCase() === key)
    || Object.keys(TOWN_ALIASES).find(a => a.toLowerCase() === raw.toLowerCase());
  if (alias) return { town: TOWN_ALIASES[alias], note: `Saved as ${TOWN_ALIASES[alias]}, the official town for ${alias}.` };
  if (SPLIT_TOWNS[key]) {
    const [a, b] = SPLIT_TOWNS[key];
    return { error: `${raw} is two towns on the map: pick ${a} or ${b}.` };
  }
  return { error: `"${raw}" is not a Vermont town. Pick one from the list (a village goes under its town), or write "Town, NH" for out of state.` };
}

/** A route made in the shadow copy (Assign Routes' "New Route…"); its id is negative. */
function createRoute(store, name = "New route") {
  const id = store.nextId--;
  store.newRoutes = [...(store.newRoutes || []), { route_id: id, name }];
  store.routes[id] = [];
  return id;
}

/** Rename a route made in the shadow copy; a blank name keeps the old one. */
function renameRoute(store, routeId, name) {
  const r = (store.newRoutes || []).find(x => x.route_id === routeId);
  if (r && name.trim()) r.name = name.trim();
}

/** True when the page is on the shared database (ops-db.js, signed in as a team member). */
function opsShared() {
  return typeof opsDb !== "undefined" && !!(opsDb && opsDb.person);
}

/** Where a change is kept, for the pages' messages. */
function opsWhere() {
  return opsShared() ? "saved for the team" : "this browser only";
}

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The yellow strip on every shadow page, with the Reset button. */
function shadowBannerHtml(store, snap) {
  const shared = typeof opsDbBannerHtml === "function" ? opsDbBannerHtml() : null;
  if (shared) return shared;
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
  if (typeof opsWireSignIn === "function") opsWireSignIn(container);
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
  // A will-call / limited-data estimate carries its section in grey.
  const label = projectionLabel(row);
  return `<span class="ops-proj ops-fill-${fillLevel(row)}">(~${formatWhole(est.gal)} gal / ${capText})</span>` +
    daysBracketHtml(row.days_past_full) +
    (label ? ` <span class="ops-muted">${escapeHtml(label)}</span>` : "");
}

/**
 * " [28]": days past full, shown capped at ±90 ("[90]", "[-90]"); nothing when there is no figure.
 * Display only: the export, the ranking and every sort keep the real days_past_full.
 */
const DAYS_SHOWN_CAP = 90;
function daysBracketHtml(days) {
  if (days === null || days === undefined) return "";
  const shown = Math.max(-DAYS_SHOWN_CAP, Math.min(DAYS_SHOWN_CAP, Math.round(days)));
  return ` <span class="ops-days">[${shown}]</span>`;
}

/** One stop's projection, for a list line: "76 proj. gal · 77% full". */
function projectionBadgeText(row) {
  const est = stopEstimate(row);
  if (est.kind === "projected") {
    const label = projectionLabel(row);
    return `${formatWhole(est.gal)} proj. gal` + (row.pct_full !== null && row.pct_full !== undefined
      ? ` · ${Math.round(row.pct_full)}% full` : "") + (label ? ` · ${label}` : "");
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
  const prev = todaysCollection(store, customer.customer_id);
  store.collections = store.collections.filter(c =>
    !(c.date === today && c.customer_id === customer.customer_id));
  // A re-save keeps the entry's id, so the shared database records an edit.
  store.collections.push({ customer_id: customer.customer_id, name: customer.name, gallons, date: today,
    route_id: stop ? routeId : null, assignment_id: stop ? stop.assignment_id : null,
    ...(prev && prev.client_id ? { client_id: prev.client_id } : {}) });
}

/** Today's shadow entry for a customer, or null. */
function todaysCollection(store, customerId) {
  const today = todayIso();
  return store.collections.find(c => c.date === today && c.customer_id === customerId) || null;
}

/**
 * Record a theft found at a stop: today's collection (the gallons collected --
 * what was left after the theft, 0 when it was emptied) marked as a theft,
 * with the driver's note.
 * Every save is kept in `versions` with the time it was made, so the first
 * account survives later edits — the record is evidence. `recorded_at` is the
 * first save. The shadow copy has no logins yet, so who recorded it is not known.
 */
function recordTheft(store, customer, gallons, note, now = new Date()) {
  const prev = todaysCollection(store, customer.customer_id);
  const versions = prev && prev.theft ? prev.theft.versions : [];
  recordCollection(store, customer, gallons);
  const entry = todaysCollection(store, customer.customer_id);
  const at = now.toISOString();
  entry.theft = { note, recorded_at: versions.length ? versions[0].at : at,
    versions: [...versions, { at, gallons, note }] };
  return entry;
}

/** A theft's figures: "Theft · 15 gal", or "Theft · empty" for 0-3. */
function theftGallonsText(c) {
  return `Theft · ${c.gallons <= 3 ? "empty" : `${formatNumber(c.gallons)} gal`}`;
}

/* ── Oil Left ───────────────────────────────────────────── */

/**
 * Gallons a driver saw left in a barrel and did not collect (Sarge,
 * 2026-10-24): after a part pickup when the truck is filling, or at a stop a
 * full truck checks and skips. Never a collection, so no total counts it.
 * One per customer and day: a second save that day replaces the first. The
 * nightly exports it to oil_left.csv, where the projections start the barrel
 * at what was left.
 */
function recordOilLeft(store, customer, gallons) {
  const today = todayIso();
  const routeId = store.my ? Number(store.my.routeId) : null;
  const onRoute = routeId !== null && storeStops(store, routeId).some(s => s.customer_id === customer.customer_id);
  clearOilLeft(store, customer.customer_id);
  store.oilLeft.push({ customer_id: customer.customer_id, date: today, gallons,
    route_id: onRoute ? routeId : null });
}

/** Today's Oil Left for a customer, or null. */
function todaysOilLeft(store, customerId) {
  const today = todayIso();
  return (store.oilLeft || []).find(e => e.date === today && e.customer_id === customerId) || null;
}

function clearOilLeft(store, customerId) {
  const today = todayIso();
  store.oilLeft = (store.oilLeft || []).filter(e => !(e.date === today && e.customer_id === customerId));
}

/** "Oil left: 30 gal" */
function oilLeftText(e) {
  return `Oil left: ${formatNumber(e.gallons)} gal`;
}

/**
 * Addresses, notes (codes included) and map positions, from the shared
 * database (ops-db.js; signed-in team members only), or an empty set. They are
 * never in a published file (step 6, 2026-10-24).
 */
async function loadCustomerDetails() {
  const shared = typeof opsDbCustomerDetails === "function" ? await opsDbCustomerDetails() : null;
  return shared || {};
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

/**
 * A customer's Collections list: the source site's history (`history`, from the
 * nightly files: { date, gallons, code }) and this site's own entries
 * (`siteRows`: { client_id, date, gallons, theft }), newest first. The two
 * record the same pickups (gallons go into both systems), so on a day with a
 * site entry the site's entries stand in for the source site's and are never
 * added to them. Site lines carry `client_id`, which makes them editable.
 */
function collectionLines(history, siteRows) {
  const siteDays = new Set(siteRows.map(r => r.date));
  const lines = history.filter(h => !siteDays.has(h.date)).map(h => ({ ...h, site: false }))
    .concat(siteRows.map(r => ({ date: r.date, gallons: r.gallons, code: r.gallons <= 3, site: true,
      client_id: r.client_id, theft: !!r.theft })));
  return lines.sort((a, b) => b.date.localeCompare(a.date));
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
