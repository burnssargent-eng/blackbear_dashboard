/*
 * Ops search (Sarge, 2026-11-20): the header's search on every Ops page,
 * loaded after ops-db.js.
 *
 * Finds customers first (an id, then names starting with the text, a word
 * starting with it, the text anywhere, then the town), active before
 * inactive, the most urgent first; then people (office_phones, by name, with
 * their role and a Call menu), routes, places (towns, regions), pages (names
 * plus a few aliases) and actions. One typo is forgiven from
 * the 4th letter, only when nothing matches as typed. An empty box shows the
 * last 5 customers opened or added from here, on this device.
 *
 * Each customer has + (add it to a route, with Assign Routes' own bar: the
 * route set as My Route first, Must get, + New Route..., + Task...) and ›
 * (its page). A stop goes at the end of the route, as Assign does it. The
 * change goes through the page's own copy (opsDb.store) and the page redraws
 * through its own onRemote, so My Route shows the new stop at once.
 */

const OPS_RECENT_KEY = "bbb-ops-search-recent";
const OPS_PEOPLE_KEY = "bbb-ops-search-people";   // the last list read, for offline; per signed-in user
const OPS_SEARCH_HINT = "Customer, town, person, or page";
const OPS_SEARCH_NEW_ROUTE = "__new";
const OPS_SEARCH_NEW_TASK = "__task";

// Pages: the nav's, with the words people might type for them.
const OPS_SEARCH_PAGES = [
  { label: "Assign Routes", href: "ops-assign.html", sub: "Ops", aliases: ["routes", "assign"] },
  { label: "My Route", href: "ops-myroute.html", sub: "Ops", aliases: ["todays routes", "driver"] },
  { label: "Calendar", href: "ops-calendar.html", sub: "Ops · pickups by day", aliases: [] },
  { label: "Outgoing", href: "ops-outgoing.html", sub: "Ops · stillage runs", aliases: ["stillage", "gebbie"] },
  { label: "Inactive Accounts", href: "ops-inactive.html", sub: "Ops", aliases: ["inactive", "former", "closed"] },
  { label: "Dashboard", href: "index.html", sub: "Analytics", aliases: ["home", "gallons", "totals"] },
  { label: "Projections", href: "projections.html", sub: "Analytics", aliases: ["projected", "forecast"] },
  { label: "Regions", href: "region.html", sub: "Historical Totals", aliases: ["historical totals"] },
  { label: "Years", href: "year.html", sub: "Historical Totals", aliases: ["yearly", "historical totals"] },
  { label: "Customers", href: "customers.html", sub: "Historical Totals", aliases: ["history", "historical totals", "retention"] },
  { label: "Schmutz", href: "schmootz.html", sub: "Analytics", aliases: ["schmootz", "schmutz"] },
];
const OPS_SEARCH_ACTIONS = [
  { label: "Add customer", sub: "Assign Routes", href: "ops-assign.html#add", aliases: ["new customer", "sign up"] },
  { label: "New route", sub: "Opens on My Route", act: "newroute", aliases: ["create route"] },
  { label: "Sign out", sub: "This device", act: "signout", aliases: ["log out", "logout"] },
];

let opsSearch = {
  q: "", data: null, loading: null, error: "",
  picked: null,            // customer id whose + was tapped: Assign's bar is open for it
  people: [],              // office_phones: { name, role, phone }, signed in only
  callOpen: null,          // the person whose Call menu is open
  route: "", must: false, flash: "",
  task: { kind: "task", text: "", note: "", msg: "" },
};

/* ── Matching ───────────────────────────────────────────── */

/** Lower case, no accents or punctuation: "Ken’s Pizza-Shelburne" -> "kens pizza shelburne". */
function opsNorm(text) {
  return String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/['’`]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

/** At most one letter wrong, missing, extra or swapped between a and b. */
function opsOneEdit(a, b) {
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  const ra = a.slice(i + 1), rb = b.slice(i + 1);
  return ra === rb                                   // one swapped letter
    || a.slice(i) === b.slice(i + 1)                 // one missing from a
    || a.slice(i + 1) === b.slice(i)                 // one extra in a
    || (a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2));   // two swapped
}

/** True when `token` is one typo away from how `word` starts (a token of 4 letters or more). */
function opsNearWord(token, word) {
  if (token.length < 4) return false;
  return [token.length - 1, token.length, token.length + 1].some(n => n > 0 && word.length >= n - 1 &&
    opsOneEdit(token, word.slice(0, n)));
}

/**
 * How well `query` matches an item: 0 the name starts with it, 1 every word of it
 * starts a word of the name, 2 every word of it is in the name, 3 it needs the
 * other fields (the town) too, 4 one typo in the name, 5 one typo using the town;
 * null when it does not match.
 * `names` are the item's names, `others` its town and a shared barrel's other members.
 */
function opsMatchTier(query, names, others = [], { typos = false } = {}) {
  const q = opsNorm(query);
  if (!q) return null;
  const tokens = q.split(" ");
  const ns = names.map(opsNorm).filter(Boolean);
  const os = others.map(opsNorm).filter(Boolean);
  if (ns.some(n => n.startsWith(q))) return 0;
  const words = ns.flatMap(n => n.split(" "));
  if (tokens.every(t => words.some(w => w.startsWith(t)))) return 1;
  const compact = ns.map(n => n.replace(/ /g, ""));
  if (tokens.every(t => ns.some(n => n.includes(t))) || compact.some(c => c.includes(q.replace(/ /g, "")))) return 2;
  const all = [...ns, ...os];
  const allWords = all.flatMap(n => n.split(" "));
  if (tokens.every(t => all.some(n => n.includes(t)))) return 3;
  if (typos) {
    // One typo in all the words typed: in the name first (4), else using the town (5).
    const oneTypo = ws => {
      let edits = 0;
      const fits = tokens.every(t => {
        if (ws.some(w => w.startsWith(t))) return true;
        if (ws.some(w => opsNearWord(t, w))) { edits++; return true; }
        return false;
      });
      return fits && edits === 1;
    };
    if (oneTypo(words)) return 4;
    if (oneTypo(allWords)) return 5;
  }
  return null;
}

/* ── Data ───────────────────────────────────────────────── */

function opsSearchDb() {
  return typeof opsDb !== "undefined" && opsDb ? opsDb : null;
}

/**
 * The lists search reads: the page's own (with the site's name and town edits)
 * when it has them, else fetched. customer_history.json brings the inactive
 * accounts; without it search still finds the active ones.
 */
function opsSearchLoad() {
  if (opsSearch.data) return Promise.resolve(opsSearch.data);
  if (opsSearch.loading) return opsSearch.loading;
  const get = f => fetch(f).then(r => {
    if (!r.ok) throw new Error(`Could not load ${f} (HTTP ${r.status})`);
    return r.json();
  });
  const db = opsSearchDb();
  opsSearch.loading = Promise.all([
    get("oil_projections.json"),
    db && db.snap ? db.snap : get("jamaal_routes.json"),
    get("customer_history.json").catch(() => null),
  ]).then(([projections, snap, hist]) => {
    let index = db && db.index;
    if (!index) {
      index = buildIndex(projections.customers || []);
      index.asOf = projections.as_of || null;
      if (db && db.rows && typeof opsApplySiteEdits === "function") opsApplySiteEdits(snap, index, db.rows.customers);
    }
    opsSearchPeopleFetch().then(list => { opsSearch.people = list; opsSearchDraw(); }, () => {});
    const urgency = new Map((projections.customers || []).filter(c => c.section === "ranked").map((c, i) => [c.id, i]));
    opsSearch.data = { snap, index, hist, urgency, get people() { return opsSearch.people; } };
    return opsSearch.data;
  }).catch(err => { opsSearch.loading = null; opsSearch.error = err.message; throw err; });
  return opsSearch.loading;
}

/**
 * The page's copy of the routes. Every Ops page keeps it in its own top-level
 * `store` (the shared one when signed in, the same object as opsDb.store); a
 * second copy here could be saved over by the page's.
 */
function opsSearchStore() {
  if (typeof store !== "undefined" && store) return store;   // eslint-disable-line no-undef
  const db = opsSearchDb();
  if (db && db.store) return db.store;
  return loadStore(opsSearch.data.snap);
}

/** Save a change and let the page redraw with it, as a change from another device would. */
function opsSearchSave(st) {
  saveStore(st);
  const db = opsSearchDb();
  if (db && db.onRemote) db.onRemote(st);
  else if (typeof render === "function") render();   // eslint-disable-line no-undef
}

/**
 * Accounts not on the source site's Assign list, one line each, from
 * customer_history.json: name, town, last pickup, gallons and how its business
 * ended (the customer page's chip). An account taken over on the site is left
 * out, as the Ops pages hide it.
 */
function opsInactiveAccounts(hist, activeIds, store = null) {
  const lastEra = new Map();
  ((hist && hist.eras) || []).forEach(e => {
    const prev = lastEra.get(e.customer_id);
    if (!prev || e.era > prev.era) lastEra.set(e.customer_id, e);
  });
  const by = new Map();
  ((hist && hist.businesses) || []).forEach(b => (b.customer_ids || []).forEach(id => {
    if (activeIds.has(id) || (store && isReplaced(store, id))) return;
    const prev = by.get(id);
    if (!prev || (b.last_pickup || "") > (prev.last_pickup || "")) by.set(id, b);
  }));
  return [...by].map(([id, b]) => {
    const era = lastEra.get(id);
    return { id, name: (era && era.account_name) || b.name, town: townLabel(b.town || ""),
      last: b.last_pickup || "", gallons: b.gallons || 0,
      ended: b.status === "ended" ? (b.end_reason || "unknown") : "unknown" };
  });
}

/** Marked inactive on the customer page (and no pickup since). */
function opsSearchMarkedInactive(id, row) {
  if (typeof opsSiteStatus !== "function" || id < 0) return false;
  const st = opsSiteStatus(id, (row && row.last_pickup) || "");
  return !!st && st.status === "inactive";
}

/** Every customer search can find: the Assign list, ones added here, then inactive accounts. */
function opsSearchCustomers(d, store) {
  const out = [];
  const activeIds = new Set();
  (d.snap.assign_list || []).forEach(a => {
    if (isReplaced(store, a.customer_id)) return;
    const row = d.index.get(a.customer_id) || null;
    activeIds.add(a.customer_id);
    out.push({ id: a.customer_id, name: a.name || (row && row.name) || `#${a.customer_id}`,
      town: townLabel((row && row.town) || a.city || ""), row, active: true,
      capacity: row && row.capacity != null ? row.capacity : null,
      members: ((row && row.members) || []).map(m => m.name),
      marked: opsSearchMarkedInactive(a.customer_id, row) });
  });
  (store.added || []).filter(c => !isReplaced(store, c.customer_id)).forEach(c => {
    activeIds.add(c.customer_id);
    out.push({ id: c.customer_id, name: c.name, town: townLabel(c.city || ""), row: d.index.get(c.customer_id) || null,
      active: true, capacity: c.capacity ?? null, members: [], marked: false });
  });
  opsInactiveAccounts(d.hist, activeIds, store).forEach(c => out.push({ ...c, active: false, row: null,
    capacity: null, members: [], marked: false }));
  return out;
}

/** Customer id -> [{ name, must }] for every route it is on. */
function opsSearchOnRoutes(routes, store) {
  const on = new Map();
  routes.forEach(r => storeStops(store, r.route_id).forEach(s => {
    if (!on.has(s.customer_id)) on.set(s.customer_id, []);
    on.get(s.customer_id).push({ name: r.name, must: !!s.must_get, route_id: r.route_id });
  }));
  return on;
}

/** Towns (with how many active customers) and regions, for Places. */
function opsSearchPlaces(customers, index) {
  const towns = new Map();
  customers.forEach(c => {
    if (!c.town) return;
    towns.set(c.town, (towns.get(c.town) || 0) + (c.active ? 1 : 0));
  });
  const regions = new Set();
  index.forEach(row => (row.regions || []).forEach(r => regions.add(r)));
  return [
    ...[...towns].map(([town, n]) => ({ label: town, sub: `Town · ${n} customer${n === 1 ? "" : "s"}`,
      href: `town.html?town=${encodeURIComponent(town)}` })),
    ...[...regions].sort().map(r => ({ label: r, sub: "Region", href: `region.html?region=${encodeURIComponent(r)}` })),
  ];
}

/**
 * The results for `query`: { customers, routes, places, pages, actions, typos }.
 * Each section in match order; customers then active first, most urgent first.
 * Typos are tried only when nothing matched as typed.
 */
function opsSearchResults(query, d, store) {
  const customers = opsSearchCustomers(d, store);
  const routes = opsRouteList(d.snap, store);
  const idQuery = /^#?\d+$/.test(query.trim()) ? Number(query.trim().replace("#", "")) : null;
  const run = typos => {
    const tierOf = c => (idQuery !== null && c.id === idQuery ? -1
      : opsMatchTier(query, [c.name], [c.town, ...(c.members || [])], { typos }));
    const urg = c => (c.row && d.urgency.has(c.row.id) ? d.urgency.get(c.row.id) : Infinity);
    const cs = customers.map(c => ({ c, t: tierOf(c) })).filter(x => x.t !== null)
      .sort((a, b) => a.t - b.t || (b.c.active - a.c.active) || (a.c.marked - b.c.marked) || urg(a.c) - urg(b.c) ||
        a.c.name.localeCompare(b.c.name)).map(x => x.c);
    // Pages and actions match from the start of a word ("cal" is not in "historical").
    const pick = (list, names, wordsOnly = false) => list.map(x => ({ x, t: opsMatchTier(query, names(x), [], { typos }) }))
      .filter(y => y.t !== null && (!wordsOnly || y.t <= 1 || y.t >= 4)).sort((a, b) => a.t - b.t).map(y => y.x);
    return {
      customers: cs,
      people: pick(d.people || [], p => [p.name]),   // names only (Sarge, 2026-11-25)
      routes: pick(routes, r => [r.name, ...(r.aliases || [])]),
      places: pick(opsSearchPlaces(customers, d.index), p => [p.label]),
      pages: pick(OPS_SEARCH_PAGES, p => [p.label, ...p.aliases], true),
      actions: pick(OPS_SEARCH_ACTIONS, a => [a.label, ...a.aliases], true),
    };
  };
  const exact = run(false);
  const found = Object.values(exact).some(list => list.length);
  if (found || opsNorm(query).length < 4) return { ...exact, typos: false };
  return { ...run(true), typos: true };
}

/* ── People ─────────────────────────────────────────────── */

/**
 * The office_phones list (Jim, Stacey, ...): name, role and number, read behind the
 * login like My Route's Call menu, the person signed in included. Kept in this
 * browser for offline; nothing without a sign-in. Before the role column exists
 * the roles are blank.
 */
async function opsSearchPeopleFetch() {
  if (typeof opsDbClient === "undefined" || !opsDbClient || typeof opsDbSession === "undefined") return [];
  const session = await opsDbSession;
  if (!session || !session.person) return [];
  const read = cols => opsDbClient.from("office_phones").select(cols).order("position");
  let cached = null;
  try { cached = JSON.parse(window.localStorage.getItem(OPS_PEOPLE_KEY) || "null"); } catch (err) { cached = null; }
  try {
    let res = await read("name,phone,role,position");
    if (res.error) res = await read("name,phone,position");
    if (res.error) throw res.error;
    const list = (res.data || []).map(p => ({ name: p.name, phone: p.phone, role: p.role || "" }));
    try { window.localStorage.setItem(OPS_PEOPLE_KEY, JSON.stringify({ user_id: session.user.id, list })); } catch (err) { /* */ }
    return list;
  } catch (err) {
    return cached && cached.user_id === session.user.id ? cached.list : [];
  }
}

/** "+18025550100" as (802) 555-0100; anything else as stored. */
function opsPhoneText(phone) {
  const d = String(phone || "").replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "");
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : String(phone || "");
}

const OPS_PHONE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2z"></path></svg>`;

/** A person: name, role below; Call ▾ where a customer has + and › (Sarge, 2026-11-25), its menu with the number and Call. */
function opsSearchPersonHtml(p, k, query) {
  const open = opsSearch.callOpen === k;
  const digits = String(p.phone || "").replace(/[^\d+]/g, "");
  return `<div class="ops-s-row ops-s-person">
    <span class="ops-s-text"><span class="ops-s-name">${opsSearchMark(p.name, query)}</span>
      <span class="ops-s-sub">${escapeHtml(p.role || "")}</span></span>
    <span class="ops-s-callwrap">
      <button type="button" class="btn ops-call-btn ops-s-call" data-s-call="${k}" aria-expanded="${open}"
        aria-label="Call ${escapeHtml(p.name)}">${OPS_PHONE_ICON}Call ▾</button>
      ${open ? `<div class="ops-call-list ops-s-call-list">
        <div class="ops-s-call-num">${escapeHtml(opsPhoneText(p.phone))}</div>
        <a class="btn ops-call-btn text-nowrap" href="tel:${escapeHtml(digits)}">${OPS_PHONE_ICON}Call ${escapeHtml(p.name)}</a>
      </div>` : ""}
    </span>
  </div>`;
}

/* ── Recent ─────────────────────────────────────────────── */

function opsRecentIds() {
  try { return JSON.parse(window.localStorage.getItem(OPS_RECENT_KEY) || "[]").filter(Number.isFinite); } catch (err) { return []; }
}

function opsRecentAdd(id) {
  const ids = [id, ...opsRecentIds().filter(x => x !== id)].slice(0, 5);
  try { window.localStorage.setItem(OPS_RECENT_KEY, JSON.stringify(ids)); } catch (err) { /* storage blocked */ }
}

/* ── Drawing ────────────────────────────────────────────── */

/** `text` with the query's words underlined where they appear. */
function opsSearchMark(text, query) {
  const tokens = opsNorm(query).split(" ").filter(t => t.length > 0);
  let html = "";
  let i = 0;
  const lower = String(text || "").toLowerCase();
  const spots = tokens.map(t => lower.indexOf(t)).map((at, k) => ({ at, len: tokens[k].length }))
    .filter(s => s.at >= 0).sort((a, b) => a.at - b.at);
  spots.forEach(s => {
    if (s.at < i) return;
    html += escapeHtml(text.slice(i, s.at)) + `<mark>${escapeHtml(text.slice(s.at, s.at + s.len))}</mark>`;
    i = s.at + s.len;
  });
  return html + escapeHtml(String(text || "").slice(i));
}

function opsShortDate(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** One customer: name, then route chips · town · figures; + and ›. */
function opsSearchCustomerHtml(c, on, query) {
  const routes = on.get(c.id) || [];
  const chips = routes.map(r => `<span class="ops-s-rchip">${escapeHtml(r.name)}</span>` +
    (r.must ? ` <span class="ops-s-must">must get</span>` : "")).join(" ");
  const status = !c.active ? `<span class="ops-s-chip">Inactive</span>`
    : c.marked ? `<span class="ops-s-chip">Marked inactive</span>` : "";
  const figs = c.active ? stopFiguresHtml(c.row, c.capacity)
    : (c.last ? `last pickup ${escapeHtml(opsShortDate(c.last))}` : "no pickups");
  const lead = [chips, status].filter(Boolean).join(" ");
  const sub = `${lead ? `${lead} ` : ""}${c.town ? `${escapeHtml(c.town)} · ` : ""}${figs}`;
  const picked = opsSearch.picked === c.id;
  return `<div class="ops-s-row${picked ? " ops-s-picked" : ""}${c.active ? "" : " ops-s-off"}" data-s-row="${c.id}">
    <span class="ops-s-text"><span class="ops-s-name">${opsSearchMark(c.name, query)}</span>
      <span class="ops-s-sub">${sub}</span></span>
    ${c.active ? `<button type="button" class="ops-s-add${picked ? " ops-s-add-on" : ""}" data-s-add="${c.id}"
      aria-label="Add ${escapeHtml(c.name)} to a route" aria-pressed="${picked}">+</button>`
      : `<span class="ops-s-add-gap"></span>`}
    <a class="btn ops-plain-btn ops-s-page" href="ops-customer.html?id=${c.id}" data-s-page="${c.id}"
      aria-label="Customer page for ${escapeHtml(c.name)}">›</a>
  </div>`;
}

function opsSearchLinkHtml(label, sub, attrs, query) {
  return `<a class="ops-s-link" ${attrs}><span class="ops-s-text"><span class="ops-s-name">${opsSearchMark(label, query)}</span>
    <span class="ops-s-sub">${escapeHtml(sub)}</span></span><span class="btn ops-plain-btn ops-s-page" aria-hidden="true">›</span></a>`;
}

function opsSearchListHtml() {
  const d = opsSearch.data;
  if (!d) return `<div class="ops-s-note">${opsSearch.error ? escapeHtml(opsSearch.error) : "Loading…"}</div>`;
  const store = opsSearchStore();
  const q = opsSearch.q;
  const on = opsSearchOnRoutes(opsRouteList(d.snap, store), store);
  if (!opsNorm(q)) {
    const all = opsSearchCustomers(d, store);
    const recent = opsRecentIds().map(id => all.find(c => c.id === id)).filter(Boolean);
    return recent.length ? `<div class="ops-s-sec">Recent</div>${recent.map(c => opsSearchCustomerHtml(c, on, "")).join("")}`
      : `<div class="ops-s-note">${OPS_SEARCH_HINT}.</div>`;
  }
  const r = opsSearchResults(q, d, store);
  if (!["customers", "people", "routes", "places", "pages", "actions"].some(k => r[k].length)) {
    return `<div class="ops-s-note">No matches for “${escapeHtml(q)}”.</div>`;
  }
  const sec =(title, list, draw) => (list.length ? `<div class="ops-s-sec">${title}</div>${list.map(draw).join("")}` : "");
  const html = [
    r.typos ? `<div class="ops-s-note">Nothing spelled “${escapeHtml(q)}”. Close matches:</div>` : "",
    sec(`Customers · ${r.customers.length}`, r.customers, c => opsSearchCustomerHtml(c, on, q)),
    sec("People", r.people, p => opsSearchPersonHtml(p, d.people.indexOf(p), q)),
    sec("Routes", r.routes, rt => {
      const n = storeStops(store, rt.route_id).length;
      return opsSearchLinkHtml(rt.name, `${n ? `${n} stop${n === 1 ? "" : "s"}` : "empty"} · opens on My Route`,
        `href="ops-myroute.html?route=${rt.route_id}"`, q);
    }),
    sec("Places", r.places, p => opsSearchLinkHtml(p.label, p.sub, `href="${p.href}"`, q)),
    sec("Pages", r.pages, p => opsSearchLinkHtml(p.label, p.sub, `href="${p.href}"`, q)),
    sec("Actions", r.actions, a => opsSearchLinkHtml(a.label, a.sub,
      a.href ? `href="${a.href}"` : `href="#" data-s-act="${a.act}"`, q)),
  ].join("");
  return html;
}

/** The route dropdown: the route set as My Route first, then the rest, then New Route and Task. */
function opsSearchRouteOptions(routes, store) {
  const mine = store.my ? routes.find(r => String(r.route_id) === String(store.my.routeId)) : null;
  const opt = r => `<option value="${r.route_id}"${String(r.route_id) === String(opsSearch.route) ? " selected" : ""}>${escapeHtml(r.name)}</option>`;
  return `<option value="">Assign to route…</option>
    ${mine ? `<optgroup label="My Route">${opt(mine)}</optgroup>` : ""}
    <optgroup label="${mine ? "Other routes" : "Routes"}">${routes.filter(r => r !== mine).map(opt).join("")}</optgroup>
    <option value="${OPS_SEARCH_NEW_ROUTE}"${opsSearch.route === OPS_SEARCH_NEW_ROUTE ? " selected" : ""}>+ New Route…</option>
    ${typeof addTask === "function" ? `<option value="${OPS_SEARCH_NEW_TASK}"${opsSearch.route === OPS_SEARCH_NEW_TASK ? " selected" : ""}>+ Task…</option>` : ""}`;
}

/** Assign Routes' bar for the customer whose + was tapped. */
function opsSearchBarHtml() {
  const flash = opsSearch.flash ? `<div class="text-success">${opsSearch.flash}</div>` : "";
  const d = opsSearch.data;
  if (opsSearch.picked === null || !d) return flash ? `<div class="ops-assign-bar ops-s-bar">${flash}</div>` : "";
  const store = opsSearchStore();
  const c = opsSearchCustomers(d, store).find(x => x.id === opsSearch.picked);
  if (!c) return "";
  const routes = opsRouteList(d.snap, store);
  const stop = { customer_id: c.id, name: c.name, capacity: c.capacity };
  const sel = routeSummary([stop], d.index).totals;
  const route = opsSearch.route;
  let line = "";
  if (route === OPS_SEARCH_NEW_ROUTE) {
    line = `<div class="ops-assign-route">New route: 1 stop · ~${formatWhole(sel.projected)} gal</div>`;
  } else if (route && route !== OPS_SEARCH_NEW_TASK) {
    const current = storeStops(store, route);
    const merged = current.some(s => s.customer_id === c.id) ? current : [...current, stop];
    const after = routeSummary(merged, d.index).totals;
    const fill = truckFill(after, store.trucks && store.trucks[route]);
    const count = merged.length === current.length ? `${current.length}` : `${current.length} → ${merged.length}`;
    line = `<div class="ops-assign-route${fill && fill.pct > 100 ? " text-danger" : ""}">${count} stop${merged.length === 1 ? "" : "s"}` +
      ` · ~${formatWhole(after.projected)} gal${fill ? ` · ${Math.round(fill.pct)}% ${escapeHtml(fill.truck.abbr)}` : ""}` +
      `${merged.length === current.length ? " · already on it" : ""}
      ${fill ? `<div class="progress"><div class="progress-bar ${fill.level === "full" ? "bg-danger" : fill.level === "due" ? "bg-warning" : "bg-success"}"
        style="width:${Math.min(100, fill.pct)}%"></div></div>` : ""}</div>`;
  }
  const t = opsSearch.task;
  const drop = t.kind === "barrel_drop";
  const task = route !== OPS_SEARCH_NEW_TASK ? "" : `<div class="ops-assign-task">
      <div class="btn-group ops-task-kind" role="group" aria-label="Kind">
        <button type="button" class="btn ${drop ? "btn-outline-primary" : "btn-primary"}" data-s-kind="task">Task</button>
        <button type="button" class="btn ${drop ? "btn-primary" : "btn-outline-primary"}" data-s-kind="barrel_drop">Barrel drop</button>
      </div>
      <label for="ops-s-task-route" class="ops-note-label">Task on route</label>
      <select id="ops-s-task-route" class="form-select">${opsSearchTaskRouteOptions(routes, store)}</select>
      ${drop ? "" : `<label for="ops-s-task-text" class="ops-note-label">Task</label>
        <input class="form-control" id="ops-s-task-text" maxlength="200" value="${escapeHtml(t.text)}">`}
      <label for="ops-s-task-note" class="ops-note-label">Note <span class="text-muted fw-normal">(optional)</span></label>
      <input class="form-control" id="ops-s-task-note" maxlength="500" value="${escapeHtml(t.note)}">
      <div class="ops-muted">At ${escapeHtml(c.name)}.</div>
      ${t.msg ? `<div class="text-danger small">${escapeHtml(t.msg)}</div>` : ""}
    </div>`;
  const label = route === OPS_SEARCH_NEW_ROUTE ? "Create" : route === OPS_SEARCH_NEW_TASK ? "Add task" : "Assign";
  return `<div class="ops-assign-bar ops-s-bar">
    ${flash}
    <div class="ops-assign-sel">
      <div class="flex-grow-1"><strong>${escapeHtml(c.name)}</strong></div>
      <button type="button" class="btn ops-plain-btn ops-assign-clear" data-s-clear>Clear</button>
    </div>
    ${line}${task}
    <div class="ops-assign-row">
      <select id="ops-s-route" class="form-select" aria-label="Assign to route">${opsSearchRouteOptions(routes, store)}</select>
      ${route === OPS_SEARCH_NEW_TASK ? "" : `<label class="ops-assign-must"><input type="checkbox" class="form-check-input" id="ops-s-must"
        ${opsSearch.must ? "checked" : ""}> Must get</label>`}
      <button type="button" class="btn btn-primary" data-s-assign ${route ? "" : "disabled"}>${label}</button>
    </div>
  </div>`;
}

function opsSearchTaskRouteOptions(routes, store) {
  const mine = store.my ? String(store.my.routeId) : "";
  const chosen = opsSearch.task.route ?? mine;
  return `<option value="">Choose a route…</option>` + routes.map(r =>
    `<option value="${r.route_id}"${String(r.route_id) === String(chosen) ? " selected" : ""}>${escapeHtml(r.name)}</option>`).join("");
}

/* ── The panel ──────────────────────────────────────────── */

function opsSearchEl() {
  let el = document.getElementById("ops-search");
  if (el) return el;
  el = document.createElement("div");
  el.id = "ops-search";
  el.className = "ops-search";
  el.hidden = true;
  el.innerHTML = `<div class="ops-s-panel" role="dialog" aria-label="Search">
      <div class="ops-s-top">
        <button type="button" class="ops-s-back" data-s-close aria-label="Close search"><svg width="22" height="22" viewBox="0 0 24 24"
          fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M15 5 8 12l7 7"></path></svg></button>
        <input type="search" id="ops-s-q" class="form-control" placeholder="${OPS_SEARCH_HINT}" aria-label="Search"
          autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">
      </div>
      <div class="ops-s-results" id="ops-s-results"></div>
      <div id="ops-s-bar"></div>
    </div>`;
  document.body.appendChild(el);
  opsSearchWire(el);
  return el;
}

function opsSearchDraw() {
  const el = document.getElementById("ops-search");
  if (!el || el.hidden) return;
  el.querySelector("#ops-s-results").innerHTML = opsSearchListHtml();
  opsSearchDrawBar();
}

function opsSearchDrawBar() {
  const el = document.getElementById("ops-search");
  if (!el) return;
  el.querySelector("#ops-s-bar").innerHTML = opsSearchBarHtml();
}

function opsSearchOpen() {
  const el = opsSearchEl();
  if (typeof opsMenuShow === "function") opsMenuShow(false);
  el.hidden = false;
  document.body.classList.add("ops-s-open");
  opsSearch.flash = "";
  const input = el.querySelector("#ops-s-q");
  input.value = opsSearch.q;
  input.focus();
  input.select();
  opsSearchDraw();
  opsSearchLoad().then(opsSearchDraw, opsSearchDraw);
}

function opsSearchClose() {
  const el = document.getElementById("ops-search");
  if (!el) return;
  el.hidden = true;
  document.body.classList.remove("ops-s-open");
  opsSearch.picked = null;
  opsSearch.flash = "";
}

function opsSearchPick(id) {
  const store = opsSearchStore();
  const routes = opsRouteList(opsSearch.data.snap, store);
  const mine = store.my && routes.find(r => String(r.route_id) === String(store.my.routeId));
  opsSearch.picked = opsSearch.picked === id ? null : id;
  opsSearch.route = mine ? String(mine.route_id) : "";
  opsSearch.must = false;
  opsSearch.flash = "";
  opsSearch.task = { kind: "task", text: "", note: "", msg: "", route: mine ? String(mine.route_id) : "" };
  // The keyboard would cover the bar.
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  opsSearchDraw();
}

/** Assign (or Create, or Add task) for the picked customer. */
function opsSearchAssign() {
  const d = opsSearch.data;
  const store = opsSearchStore();
  const c = opsSearchCustomers(d, store).find(x => x.id === opsSearch.picked);
  if (!c) return;
  const stop = { customer_id: c.id, name: c.name, capacity: c.capacity };
  const routes = opsRouteList(d.snap, store);
  const route = opsSearch.route;
  if (route === OPS_SEARCH_NEW_ROUTE) {
    const id = createRoute(store);
    assignToRoute(store, id, [stop], opsSearch.must);
    saveStore(store);
    opsRecentAdd(c.id);
    window.location.href = `ops-myroute.html?new=${id}`;
    return;
  }
  if (route === OPS_SEARCH_NEW_TASK) {
    const t = opsSearch.task;
    t.msg = !t.route ? "Pick the route for the task." : t.kind === "task" && !t.text.trim() ? "Type the task." : "";
    if (t.msg) { opsSearchDrawBar(); return; }
    addTask(store, { routeId: Number(t.route), kind: t.kind, text: t.text, customerId: c.id, note: t.note });
    opsSearchSave(store);
    const name = (routes.find(r => String(r.route_id) === String(t.route)) || {}).name || "the route";
    opsSearch.flash = `Task added to ${escapeHtml(name)}. <a href="ops-myroute.html">Open My Route</a>`;
  } else {
    const n = assignToRoute(store, Number(route), [stop], opsSearch.must);
    opsSearchSave(store);
    const name = (routes.find(r => String(r.route_id) === String(route)) || {}).name || "the route";
    opsSearch.flash = n
      ? `Added ${escapeHtml(c.name)} to ${escapeHtml(name)} (stop ${storeStops(store, route).length}). <a href="ops-myroute.html">Open My Route</a>`
      : `${escapeHtml(c.name)} is already on ${escapeHtml(name)}.`;
  }
  opsRecentAdd(c.id);
  opsSearch.picked = null;
  opsSearchDraw();
}

function opsSearchWire(el) {
  const input = el.querySelector("#ops-s-q");
  input.addEventListener("input", () => {
    opsSearch.q = input.value;
    opsSearch.picked = null;
    opsSearch.flash = "";
    opsSearchDraw();
  });
  input.addEventListener("keydown", e => { if (e.key === "Enter") input.blur(); });
  el.addEventListener("keydown", e => { if (e.key === "Escape") opsSearchClose(); });
  el.addEventListener("click", e => {
    if (!e.target.closest) return;
    if (e.target === el || e.target.closest("[data-s-close]")) { opsSearchClose(); return; }
    const call = e.target.closest("[data-s-call]");
    if (call) {
      const k = Number(call.dataset.sCall);
      opsSearch.callOpen = opsSearch.callOpen === k ? null : k;
      opsSearchDraw();
      return;
    }
    if (opsSearch.callOpen !== null && !e.target.closest(".ops-call-list")) { opsSearch.callOpen = null; opsSearchDraw(); }
    const add = e.target.closest("[data-s-add]");
    if (add) { opsSearchPick(Number(add.dataset.sAdd)); return; }
    const page = e.target.closest("[data-s-page]");
    if (page) { opsRecentAdd(Number(page.dataset.sPage)); return; }   // the link carries on
    const act = e.target.closest("[data-s-act]");
    if (act) {
      e.preventDefault();
      if (act.dataset.sAct === "signout") {
        const btn = document.querySelector("[data-ops-signout]");
        if (btn) btn.click();
      } else if (act.dataset.sAct === "newroute") {
        const store = opsSearchStore();
        const id = createRoute(store);
        saveStore(store);
        window.location.href = `ops-myroute.html?new=${id}`;
      }
      return;
    }
    if (e.target.closest("[data-s-clear]")) { opsSearch.picked = null; opsSearch.flash = ""; opsSearchDraw(); return; }
    const kind = e.target.closest("[data-s-kind]");
    if (kind) { opsSearch.task.kind = kind.dataset.sKind; opsSearch.task.msg = ""; opsSearchDrawBar(); return; }
    if (e.target.closest("[data-s-assign]")) opsSearchAssign();
  });
  el.addEventListener("change", e => {
    if (e.target.id === "ops-s-route") { opsSearch.route = e.target.value; opsSearch.task.msg = ""; opsSearchDrawBar(); }
    if (e.target.id === "ops-s-must") opsSearch.must = e.target.checked;
    if (e.target.id === "ops-s-task-route") opsSearch.task.route = e.target.value;
  });
  el.addEventListener("input", e => {
    if (e.target.id === "ops-s-task-text") opsSearch.task.text = e.target.value;
    if (e.target.id === "ops-s-task-note") opsSearch.task.note = e.target.value;
  });
}

// From 1200px the panel sits under the header: a click elsewhere on the page closes it.
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", e => {
    const el = document.getElementById("ops-search");
    if (!el || el.hidden || !e.target.closest) return;
    if (e.target.closest("#ops-search") || e.target.closest("[data-ops-search-open]")) return;
    if (!document.body.contains(e.target)) return;   // a row redrawn under the click
    opsSearchClose();
  });
}
