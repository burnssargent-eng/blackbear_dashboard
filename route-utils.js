/*
 * Route maths shared by planner.html and myroute.html (both beta).
 *
 * Plain browser script, loaded after dashboard-utils.js. Nothing here computes
 * a projection: route figures only ADD the per-customer values exported in
 * oil_projections.json, with two rules:
 *   - a shared barrel is one row in the export, so members on the same route
 *     count once (Namaste Garden and Pick Thai are the same barrel);
 *   - a held-out customer has no projection, so its average pickup is shown
 *     separately as "typical", never mixed into the projected total.
 */

// The trucks, from the source site's Outgoing page, with tank capacity in
// gallons (Sarge, 2026-10-18).
const TRUCKS = [
  { name: "Mamma Bear", capacity: 1350 },
  { name: "Mack Daddy", capacity: 2000 },
  { name: "Baby Bird", capacity: 750 },
  { name: "New truck", capacity: 1200 },
];

function truckByName(name) {
  return TRUCKS.find(t => t.name === name) || null;
}

/**
 * Customer id -> export row. A shared barrel is exported once, under its
 * lowest member id, so every member id points at that one row.
 */
function buildIndex(customers) {
  const byId = new Map();
  customers.forEach(c => {
    byId.set(c.id, c);
    (c.members || []).forEach(m => { if (!byId.has(m.id)) byId.set(m.id, c); });
  });
  return byId;
}

/**
 * What one stop is expected to hold, from its export row:
 *   projected  ranked: projected gallons with its range
 *   typical    held out (will-call, stale, seasonal, ...): average pickup only
 *   none       not in the export: a shop task, a tote run, an inactive or new account
 */
function stopEstimate(row) {
  if (!row) return { kind: "none", gal: null, low: null, high: null };
  if (row.projected_gal !== null && row.projected_gal !== undefined) {
    return { kind: "projected", gal: row.projected_gal, low: row.range_low, high: row.range_high };
  }
  return { kind: "typical", gal: row.avg_collection, low: null, high: null };
}

/**
 * Totals for an ordered list of stops ({customer_id, name, capacity?, ...}).
 * lines[i] describes stops[i]. `dupOf` is the 0-based position of the earlier
 * stop on the same barrel; such a stop adds nothing, gallons or capacity.
 * A stop's capacity is its export row's, else the one the source site listed.
 */
function routeSummary(stops, byId) {
  const seen = new Map();
  const totals = { projected: 0, low: 0, high: 0, typical: 0, capacity: 0,
    projectedStops: 0, typicalStops: 0, noneStops: 0, sharedRepeats: 0 };
  const lines = stops.map((stop, i) => {
    const row = byId.get(stop.customer_id) || null;
    const est = stopEstimate(row);
    const key = row ? row.id : `none:${stop.customer_id}`;
    if (seen.has(key)) {
      totals.sharedRepeats++;
      return { stop, row, est, capacity: null, dupOf: seen.get(key) };
    }
    seen.set(key, i);
    const capacity = row && row.capacity !== null && row.capacity !== undefined
      ? row.capacity : (stop.capacity ?? null);
    totals.capacity += capacity || 0;
    if (est.kind === "projected") {
      totals.projected += est.gal;
      totals.low += est.low ?? est.gal;
      totals.high += est.high ?? est.gal;
      totals.projectedStops++;
    } else if (est.kind === "typical") {
      totals.typical += est.gal || 0;
      totals.typicalStops++;
    } else {
      totals.noneStops++;
    }
    return { stop, row, est, capacity, dupOf: null };
  });
  return { totals, lines };
}

/** Customer id -> names of the source-site routes it is currently on. */
function routesByCustomer(snap, byId) {
  const on = new Map();
  ((snap && snap.routes) || []).forEach(r => r.stops.forEach(s => {
    const row = byId.get(s.customer_id);
    [s.customer_id, ...(row ? [row.id, ...(row.members || []).map(m => m.id)] : [])]
      .forEach(id => {
        const names = on.get(id) || [];
        if (!names.includes(r.name)) names.push(r.name);
        on.set(id, names);
      });
  }));
  return on;
}

/* ── Stop order (pure: each returns a new array) ─────────── */

function moveStop(stops, from, to) {
  if (from < 0 || from >= stops.length) return stops.slice();
  const clamped = Math.max(0, Math.min(stops.length - 1, to));
  const out = stops.slice();
  const [item] = out.splice(from, 1);
  out.splice(clamped, 0, item);
  return out;
}

/** Typing a stop number: the stop moves there and the rest renumber. */
function setStopNumber(stops, from, number) {
  const n = parseInt(number, 10);
  if (!Number.isFinite(n)) return stops.slice();
  return moveStop(stops, from, n - 1);
}

function addStop(stops, stop) {
  if (stops.some(s => s.customer_id === stop.customer_id)) return stops.slice();
  return [...stops, stop];
}

function removeStop(stops, i) {
  return stops.filter((_, j) => j !== i);
}

/* ── Wording ─────────────────────────────────────────────── */

function formatWhole(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return formatNumber(Math.round(Number(value)));
}

/** "419 gal projected (range 303–592)": the route-level figure. No range when nothing is projected. */
function projectedRangeText(totals) {
  if (!totals.projectedStops) return `${formatWhole(totals.projected)} gal projected`;
  return `${formatWhole(totals.projected)} gal projected ` +
    `(range ${formatWhole(totals.low)}–${formatWhole(totals.high)})`;
}

/** Projected and its high end as a share of a truck's tank. */
function truckFill(totals, truckName) {
  const truck = truckByName(truckName);
  if (!truck || !truck.capacity) return null;
  const pct = 100 * totals.projected / truck.capacity;
  const highPct = 100 * totals.high / truck.capacity;
  return { truck, pct, highPct,
    level: pct > 100 ? "full" : (highPct > 100 ? "due" : "ok") };
}
