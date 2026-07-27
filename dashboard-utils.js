/*
 * Shared helpers for the Black Bear Bio detail pages.
 *
 * Plain browser script — no modules, no build step, no dependencies beyond
 * Chart.js where a page draws charts. Loaded with a normal <script> tag.
 *
 * index.html does NOT use this file. The homepage keeps its own self-contained
 * code so nothing here can change how it behaves.
 */

/* ─────────────────────────────────────────────
 * Data loading
 * ───────────────────────────────────────────── */

/**
 * Load the small summary file.
 *
 * Always cache-busted with a fresh timestamp: it is under 1 MB and must never
 * be stale, because the nightly job rewrites this exact URL.
 */
async function loadOilData() {
  const resp = await fetch(`oil_data.json?v=${Date.now()}`);
  if (!resp.ok) {
    throw new Error(`Could not load oil_data.json (HTTP ${resp.status})`);
  }
  return resp.json();
}

/**
 * Load the per-record detail file (about 5 MB).
 *
 * Cache-busted with last_updated rather than a timestamp on purpose. That value
 * only changes when the scraper regenerates the data, so moving between pages
 * reuses the browser's cached copy instead of re-downloading 5 MB every time,
 * while a nightly update still produces a new URL and is picked up at once.
 */
async function loadCollections(data) {
  const file = (data && data.collections_file) || "oil_collections.json";
  const version = encodeURIComponent((data && data.last_updated) || Date.now());

  const resp = await fetch(`${file}?v=${version}`);
  if (!resp.ok) {
    throw new Error(`Could not load ${file} (HTTP ${resp.status})`);
  }

  const payload = await resp.json();
  return payload.records || [];
}

/* ─────────────────────────────────────────────
 * Formatting
 *
 * Everything user-facing goes through these, so a missing or malformed value
 * renders as "0" or "—" instead of NaN, undefined or null.
 * ───────────────────────────────────────────── */

function formatNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString() : "0";
}

function formatGallons(value) {
  return `${formatNumber(value)} gal`;
}

/** "2026-06-13" -> "Jun 13, 2026". Returns an em dash for anything unusable. */
function formatDate(value) {
  if (!value) return "—";

  const text = String(value).slice(0, 10);
  const parts = text.split("-");
  if (parts.length !== 3) return "—";

  const date = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  if (isNaN(date.getTime())) return "—";

  return date.toLocaleDateString("default", {
    month: "short",
    day: "numeric",
    year: "numeric"
  });
}

/** "2026-06" -> "Jun 2026". */
function formatMonth(value) {
  if (!value) return "—";

  const parts = String(value).split("-");
  if (parts.length < 2) return String(value);

  const date = new Date(Number(parts[0]), Number(parts[1]) - 1);
  if (isNaN(date.getTime())) return String(value);

  return date.toLocaleDateString("default", { month: "short", year: "numeric" });
}

/** Escape text before putting it in innerHTML. Customer names contain quotes. */
function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ─────────────────────────────────────────────
 * URL parameters
 * ───────────────────────────────────────────── */

/** Read a query-string value, already decoded. Returns "" when absent. */
function getUrlParam(name) {
  const params = new URLSearchParams(window.location.search);
  const value = params.get(name);
  return value == null ? "" : value.trim();
}

/* ─────────────────────────────────────────────
 * Aggregation
 * ───────────────────────────────────────────── */

function sumGallons(rows) {
  return (rows || []).reduce((total, row) => total + (Number(row.gallons) || 0), 0);
}

/**
 * Sum a numeric field grouped by an arbitrary key.
 * Returns a plain object of key -> total.
 */
function groupSum(rows, keyFn, valueField) {
  const field = valueField || "gallons";
  const totals = {};

  (rows || []).forEach(row => {
    const key = keyFn(row);
    if (key === undefined || key === null || key === "") return;
    totals[key] = (totals[key] || 0) + (Number(row[field]) || 0);
  });

  return totals;
}

/** Sorted [[key, total], ...], largest first. */
function sortedEntries(totals) {
  return Object.entries(totals).sort((a, b) => b[1] - a[1]);
}

/**
 * Roll collection records up to one row per customer.
 *
 * geo_town and is_active are constant per customer in this dataset, so the
 * first record's values are taken as the customer's town and status.
 */
function aggregateCustomers(records) {
  const byId = new Map();

  (records || []).forEach(row => {
    let entry = byId.get(row.customer_id);

    if (!entry) {
      entry = {
        customer_id: row.customer_id,
        name: row.name,
        geo_town: row.geo_town || "",
        city: row.city || "",
        county: row.county || "",
        is_active: row.is_active === undefined ? null : row.is_active,
        gallons: 0,
        pickups: 0,
        first: row.date,
        last: row.date
      };
      byId.set(row.customer_id, entry);
    }

    entry.gallons += Number(row.gallons) || 0;
    entry.pickups += 1;
    if (row.date < entry.first) entry.first = row.date;
    if (row.date > entry.last) entry.last = row.date;
  });

  return Array.from(byId.values());
}

/* ─────────────────────────────────────────────
 * Customer status
 *
 * Status comes from the source site's active/inactive customer lists and is
 * NEVER inferred from how recently a customer had a pickup. A customer with no
 * pickup in years may still be active on the source site, and vice versa.
 * ───────────────────────────────────────────── */

function customerStatusLabel(isActive) {
  if (isActive === true) return { text: "Active", cls: "status-active" };
  if (isActive === false) return { text: "Inactive", cls: "status-inactive" };
  return { text: "Unknown", cls: "status-unknown" };
}

/** Renders the badge markup for a status. */
function statusBadge(isActive) {
  const status = customerStatusLabel(isActive);
  return `<span class="status-badge ${status.cls}">${status.text}</span>`;
}

/** Active first, then Unknown, then Inactive. */
function statusRank(isActive) {
  if (isActive === true) return 0;
  if (isActive === false) return 2;
  return 1;
}

/**
 * Sort customers into status groups, then by gallons descending inside each.
 * Returns a new array; the input is not modified.
 */
function sortCustomersByStatusThenGallons(customers) {
  return (customers || []).slice().sort((a, b) => {
    const rank = statusRank(a.is_active) - statusRank(b.is_active);
    if (rank !== 0) return rank;
    return b.gallons - a.gallons;
  });
}

/** Straight gallons descending, regardless of status. */
function sortCustomersByGallons(customers) {
  return (customers || []).slice().sort((a, b) => b.gallons - a.gallons);
}

/* ─────────────────────────────────────────────
 * Time period modes
 *
 * The same five definitions the homepage map uses, so a period means the same
 * thing everywhere on the site.
 * ───────────────────────────────────────────── */

const VIEW_MODES = [
  { value: "month", label: "Monthly" },
  { value: "ytd", label: "Year to Date" },
  { value: "year", label: "Selected Year" },
  { value: "alltime_todate", label: "All Time To Date" },
  { value: "alltime", label: "All Time" }
];

/**
 * Which months a mode covers, relative to a selected "YYYY-MM".
 * months must be a sorted array of every month present in the data.
 */
function getMonthsForMode(mode, months, selected) {
  if (!months || !months.length) return [];
  if (!selected) selected = months[months.length - 1];

  const year = selected.slice(0, 4);

  switch (mode) {
    case "month":
      return [selected];
    case "ytd":
      return months.filter(m => m.slice(0, 4) === year && m <= selected);
    case "year":
      return months.filter(m => m.slice(0, 4) === year);
    case "alltime_todate":
      return months.filter(m => m <= selected);
    case "alltime":
      return months.slice();
    default:
      return [selected];
  }
}

/** Human-readable description of the selected period. */
function periodLabel(mode, months, selected) {
  if (!months || !months.length) return "—";
  if (!selected) selected = months[months.length - 1];

  const year = selected.slice(0, 4);

  switch (mode) {
    case "month":
      return formatMonth(selected);
    case "ytd":
      return selected.slice(5) === "01"
        ? `Jan ${year}`
        : `Jan–${formatMonth(selected).split(" ")[0]} ${year}`;
    case "year":
      return year;
    case "alltime_todate":
      return `Through ${formatMonth(selected)}`;
    case "alltime":
      return `All Time (${months[0].slice(0, 4)}–${months[months.length - 1].slice(0, 4)})`;
    default:
      return formatMonth(selected);
  }
}

/* ─────────────────────────────────────────────
 * Charts — same dark palette as the homepage
 * ───────────────────────────────────────────── */

const CHART_GREEN = "#3fb950";
const CHART_GRID = "#21262d";
const CHART_TICK = "#8b949e";

function chartDefaults() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        // Exact gallons on hover, comma-formatted.
        callbacks: {
          label: ctx => formatGallons(ctx.parsed.y != null ? ctx.parsed.y : ctx.parsed.x)
        }
      }
    },
    scales: {
      x: { ticks: { color: CHART_TICK, maxTicksLimit: 12 }, grid: { color: CHART_GRID } },
      y: { ticks: { color: CHART_TICK }, grid: { color: CHART_GRID } }
    }
  };
}

function lineChartConfig(labels, values) {
  return {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: CHART_GREEN,
        backgroundColor: "rgba(63, 185, 80, 0.15)",
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.25
      }]
    },
    options: chartDefaults()
  };
}

function barChartConfig(labels, values, horizontal) {
  const options = chartDefaults();
  if (horizontal) options.indexAxis = "y";

  return {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: CHART_GREEN }] },
    options
  };
}

/* ─────────────────────────────────────────────
 * Page states
 * ───────────────────────────────────────────── */

/**
 * Replace a container's contents with a loading / error / no-data message.
 * kind is "loading", "error" or "empty".
 */
function renderState(el, kind, message, detail) {
  if (!el) return;

  const extra = detail ? `<div class="state-detail">${escapeHtml(detail)}</div>` : "";

  el.innerHTML = `
    <div class="page-state state-${kind}">
      <div class="state-message">${escapeHtml(message)}</div>
      ${extra}
      <a class="state-link" href="index.html">← Back to the dashboard</a>
    </div>
  `;
}

/* ─────────────────────────────────────────────
 * Navigation
 * ───────────────────────────────────────────── */

/**
 * Build the shared header nav. `current` marks the active link.
 * latestYear and defaultRegion come from the loaded data so the links always
 * point somewhere real rather than a hardcoded value.
 */
function navHtml(current, latestYear, defaultRegion) {
  const year = latestYear || new Date().getFullYear();
  const region = defaultRegion || "Stowe";

  const links = [
    { key: "dashboard", href: "index.html", label: "Dashboard" },
    { key: "region", href: `region.html?region=${encodeURIComponent(region)}`, label: "Regions" },
    { key: "customers", href: "customers.html", label: "Customers" },
    { key: "year", href: `year.html?year=${encodeURIComponent(year)}`, label: "Years" },
    // schmootz.html is built in Phase 4D. The link is present now so the nav
    // order is final; it 404s until that page exists.
    { key: "schmootz", href: "schmootz.html", label: "Schmootz" }
  ];

  return links
    .map(l => `<a href="${l.href}"${l.key === current ? ' class="active"' : ""}>${l.label}</a>`)
    .join("");
}

/** Fill the #main-nav element on a detail page. */
function renderNav(current, data) {
  const el = document.getElementById("main-nav");
  if (!el) return;

  let latestYear = null;
  let defaultRegion = null;

  if (data) {
    if (Array.isArray(data.yearly_totals) && data.yearly_totals.length) {
      latestYear = Math.max(...data.yearly_totals.map(r => Number(r.year) || 0));
    }
    if (Array.isArray(data.region_names) && data.region_names.length) {
      defaultRegion = data.region_names[0];
    }
  }

  el.innerHTML = navHtml(current, latestYear, defaultRegion);
}

/** Shows "Updated: ..." in the header, matching the homepage. */
function renderLastUpdated(data) {
  const el = document.getElementById("last-updated");
  if (!el || !data || !data.last_updated) return;
  el.textContent = "Updated: " + new Date(data.last_updated).toLocaleString();
}

/** Sets the centered page name in the detail-page header. */
function renderPageName(text) {
  const el = document.getElementById("page-name");
  if (el) el.textContent = text || "";
}

/* ─────────────────────────────────────────────
 * Shared footnote wording
 * ───────────────────────────────────────────── */

/** Shown anywhere lifecycle figures appear. */
const LIFECYCLE_FOOTNOTE =
  "Lost customers are assigned to the year of their last qualifying pickup, " +
  "based on current source-site status as of the latest scrape.";
