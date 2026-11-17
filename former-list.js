/*
 * Former customers list: every business we no longer collect from, one row per
 * BUSINESS, not per source-site account (customer_eras.csv splits an account
 * when a new owner takes over the barrel). Shown as a section at the foot of
 * customers.html (#former) since 2026-11-17; one business's page stays at
 * former.html?b=<id>. Only displays customer_history.json and oil_data.json's
 * retention figures; the counting lives in customer_eras.py.
 * Needs dashboard-utils.js loaded first.
 */

const FORMER_LIST_LIMIT = 100;
const FORMER_FILTERS = ["all", "lost", "closed", "changed_hands", "moved", "unknown"];
const FORMER_FILTER_LABELS = { all: "All ended", ...END_REASON_LABELS };

const formerPct = v => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(v < 0.01 ? 2 : 1)}%`);

/** Default order: most recent ending first. */
function endedBusinesses(hist) {
  return (hist.businesses || [])
    .filter(b => b.status === "ended" && b.end_reason !== "non_recurring")
    .sort((a, b) => (b.last_pickup || "").localeCompare(a.last_pickup || "") || b.gallons - a.gallons);
}

function businessHref(id) {
  return `former.html?b=${encodeURIComponent(id)}`;
}

function filterBusinesses(rows, state) {
  const q = (state.q || "").trim().toLowerCase();
  return rows.filter(b =>
    (state.reason === "all" || b.end_reason === state.reason) &&
    (!state.year || b.end_year === state.year) &&
    (!q || matchesNameSearch(q, [b.name, b.town])));
}

const FORMER_COLUMNS = [
  { key: "rank", label: "#", cls: "rank" },
  { key: "name", label: "Business", text: true, value: b => b.name },
  { key: "town", label: "Town", text: true, value: b => b.town },
  { key: "served", label: "Served", value: b => b.first_pickup },
  { key: "pickups", label: "Pickups", cls: "num", value: b => b.pickups },
  { key: "gallons", label: "Gallons", cls: "num", value: b => b.gallons },
  { key: "ended", label: "Ended as", text: true, value: b => END_REASON_LABELS[b.end_reason] },
  { key: "next", label: "Site went to", cls: "", value: b => b.followed_by.length },
];

function formerTableHtml(rows, sort, byId, limit) {
  const shown = rows.slice(0, limit);
  return `<div class="table-wrap"><table class="data-table">
    <thead><tr>${sortableHeadHtml(FORMER_COLUMNS, sort)}</tr></thead>
    <tbody>${shown.map((b, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><a href="${businessHref(b.id)}">${escapeHtml(b.name)}</a></td>
        <td>${escapeHtml(b.town || "—")}</td>
        <td>${escapeHtml(eraYears(b.first_pickup, b.last_pickup, false))}</td>
        <td class="num">${formatNumber(b.pickups)}</td>
        <td class="gal">${formatNumber(b.gallons)}</td>
        <td>${endReasonBadge(b.end_reason)}</td>
        <td>${b.followed_by.map(id => byId.get(id)).filter(Boolean)
          .map(n => `<a href="${businessHref(n.id)}">${escapeHtml(n.name)}</a>`).join(", ") || "—"}</td>
      </tr>`).join("")}
    </tbody></table></div>
    ${rows.length > limit ? `<div class="list-more"><button type="button" data-show-all>Show all ${formatNumber(rows.length)}</button>
      <span class="list-count">Showing ${formatNumber(limit)} of ${formatNumber(rows.length)}</span></div>` : ""}`;
}

/** The headline figures, from oil_data.json's retention_summary. */
function formerHeadlineHtml(s) {
  if (!s) return "";
  const yrs = s.years ? `${s.years[0]}–${s.years[1]}` : "";
  return `<div class="stats-bar">
    <div class="stat"><div class="stat-val">${formerPct(s.avg_churn)}</div>
      <div class="stat-label">True churn per year</div>
      <div class="stat-sub">lost only, ${yrs}; up to ${formerPct(s.avg_churn_high)} if every unclassified ending were a loss</div></div>
    <div class="stat"><div class="stat-val">${formerPct(s.site_retention)}</div>
      <div class="stat-label">Closed sites that reopened with us</div>
      <div class="stat-sub">${formatNumber(s.site_handovers)} of ${formatNumber(s.site_closures)} closures</div></div>
    <div class="stat"><div class="stat-val">${s.avg_gained === null ? "—" : Math.round(s.avg_gained)}</div>
      <div class="stat-label">New customers per year</div>
      <div class="stat-sub">${yrs}: ${(s.avg_gained_new || 0).toFixed(1)} new sites, ${(s.avg_gained_successor || 0).toFixed(1)} successors</div></div>
    <div class="stat"><div class="stat-val">${formatNumber(s.lost_total)}</div>
      <div class="stat-label">Lost (all years)</div>
      <div class="stat-sub">of ${formatNumber(s.ended_total)} businesses that ended</div></div>
    <div class="stat"><div class="stat-val">${formatNumber(s.unknown_total)}</div>
      <div class="stat-label">Not yet classified</div>
      <div class="stat-sub">counted as neither lost nor closed</div></div>
  </div>`;
}

/** Fills `el` (customers.html's #former section) with the figures, filters and list. */
function renderFormerSection(el, data, hist) {
  const all = endedBusinesses(hist);
  const byId = new Map((hist.businesses || []).map(b => [b.id, b]));
  const years = Array.from(new Set(all.map(b => b.end_year))).sort((a, b) => b - a);
  const counts = Object.fromEntries(FORMER_FILTERS.map(f =>
    [f, f === "all" ? all.length : all.filter(b => b.end_reason === f).length]));
  const state = { reason: "all", year: null, q: "", sort: { key: null, dir: "desc" }, limit: FORMER_LIST_LIMIT };

  el.innerHTML = `
    <div class="section-title" id="former">Former Customers</div>
    <div class="section-caption">Businesses we no longer collect from: one row per business, not per account.
      When a new owner takes over a barrel, the old business is a handover, never a loss.
      Click a business for its eras, months and site timeline.</div>
    ${formerHeadlineHtml(data.retention_summary)}
    <div class="controls">
      <div class="fmr-chips" id="fmr-chips">${FORMER_FILTERS.map(f => `
        <button type="button" data-reason="${f}"${f === "all" ? ' class="active"' : ""}>${escapeHtml(FORMER_FILTER_LABELS[f])}<span class="fmr-n">${formatNumber(counts[f])}</span></button>`).join("")}
      </div>
      <label for="fmr-year">Ended</label>
      <select id="fmr-year"><option value="">All years</option>${years.map(y => `<option value="${y}">${y}</option>`).join("")}</select>
      <input type="search" class="fmr-search" id="fmr-q" placeholder="Search business or town" aria-label="Search">
    </div>
    <div id="fmr-table"></div>
    <div class="note">
      <strong>How a business ended.</strong>
      ${["lost", "closed", "changed_hands", "moved", "unknown"].map(r =>
        `${endReasonBadge(r)} ${escapeHtml(END_REASON_HELP[r])}.`).join(" ")}
      <br>A business that moved and kept using us is still a customer, so it is not listed here.
      An ending's year is the year of its last qualifying pickup. Classifications live in
      <code>${escapeHtml(hist.eras_file || "customer_eras.csv")}</code>; an ending nobody has classified stays
      Unclassified rather than being guessed. Pickup history starts in January 2015.
    </div>`;

  const draw = () => {
    const rows = sortRowsBy(filterBusinesses(all, state), FORMER_COLUMNS, state.sort);
    document.getElementById("fmr-table").innerHTML = rows.length
      ? formerTableHtml(rows, state.sort, byId, state.limit)
      : `<div class="note">No former customers match.</div>`;
  };
  draw();

  el.addEventListener("click", e => {
    const chip = e.target.closest("[data-reason]");
    if (chip) {
      state.reason = chip.dataset.reason;
      state.limit = FORMER_LIST_LIMIT;
      el.querySelectorAll("#fmr-chips button").forEach(b => b.classList.toggle("active", b === chip));
      draw();
      return;
    }
    const head = e.target.closest("#fmr-table [data-sort]");
    if (head) { state.sort = nextSortState(FORMER_COLUMNS, state.sort, head.dataset.sort); draw(); return; }
    if (e.target.closest("#fmr-table [data-show-all]")) { state.limit = Infinity; draw(); }
  });
  document.getElementById("fmr-year").addEventListener("change", e => {
    state.year = e.target.value ? Number(e.target.value) : null;
    state.limit = FORMER_LIST_LIMIT;
    draw();
  });
  document.getElementById("fmr-q").addEventListener("input", e => {
    state.q = e.target.value; state.limit = FORMER_LIST_LIMIT; draw();
  });
}
