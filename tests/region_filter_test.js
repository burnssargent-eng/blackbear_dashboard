// Data and page files are read by their repo-relative names, so anchor the
// working directory to the repo root and this runs from anywhere.
process.chdir(require('path').join(__dirname, '..'));

const fs = require('fs');

/* ---- minimal DOM shim: enough to run the real functions ---- */
function mkEl(id) {
  const el = {
    id, style: {}, dataset: {}, innerHTML: '', textContent: '', value: '',
    _classes: new Set(), children: [], _listeners: {},
    classList: {
      toggle(c, on) { on ? el._classes.add(c) : el._classes.delete(c); },
      add(c) { el._classes.add(c); }, remove(c) { el._classes.delete(c); },
      contains(c) { return el._classes.has(c); }
    },
    appendChild(c) { el.children.push(c); return c; },
    querySelector() { return mkEl('_q'); },
    addEventListener(ev, fn) { (el._listeners[ev] = el._listeners[ev] || []).push(fn); }
  };
  return el;
}
const els = {};
const document = {
  getElementById: id => (els[id] = els[id] || mkEl(id)),
  createElement: tag => mkEl('_' + tag)
};

/* ---- real source ---- */
const utils = fs.readFileSync('dashboard-utils.js', 'utf8');
const html = fs.readFileSync('region.html', 'utf8');
let script = html.slice(html.indexOf('<script>\n/*') + '<script>'.length, html.lastIndexOf('</script>'));
script = script.replace(/\n\s*init\(\);\s*$/, '\n');   // don't auto-run the page

const data = JSON.parse(fs.readFileSync('oil_data.json', 'utf8'));
const coll = JSON.parse(fs.readFileSync('oil_collections.json', 'utf8'));

const harness = `
  return {
    setState(records, months, years, monthTotals, memberCount, sort) {
      regionRecords = records; regionMonths = months; regionYears = years;
      regionMonthTotals = monthTotals; regionMemberCount = memberCount;
      customerSortMode = sort;
    },
    update: updateRegionCustomers,
    setSort: setCustomerSort,
    months: (mode, months, sel) => getMonthsForMode(mode, months, sel),
    label: (mode, months, sel) => periodLabel(mode, months, sel),
    esc: escapeHtml,
    agg: aggregateCustomers,
    byGallons: sortCustomersByGallons,
    byStatus: sortCustomersByStatusThenGallons,
    setMonths(m) { regionMonths = m; }
  };
`;
const api = new Function('document', utils + '\n' + script + '\n' + harness)(document);

/* ---- fixtures ---- */
function regionFixture(region) {
  const ids = new Set(data.region_customers[region] || []);
  const monthTotals = {};
  data.monthly_by_region.filter(r => r.region === region)
    .forEach(r => { monthTotals[r.month] = (monthTotals[r.month] || 0) + r.gallons; });
  const months = Object.keys(monthTotals).sort();
  const years = [...new Set(months.map(m => m.slice(0, 4)))].sort();
  return {
    region, ids, months, years, monthTotals,
    records: coll.records.filter(r => ids.has(r.customer_id))
  };
}
function load(f, sort = 'gallons') {
  api.setState(f.records, f.months, f.years, f.monthTotals, f.ids.size, sort);
}
const $ = id => document.getElementById(id);
function setControls(mode, month, year) {
  $('cust-mode-select').value = mode;
  $('cust-month-select').value = month || f0.months[f0.months.length - 1];
  $('cust-year-select').value = year || f0.years[f0.years.length - 1];
}
const REGIONS = ['Outer Burlington', 'Burlington / South Burlington', 'Central South'];
let f0;
let fails = 0;
const ok = (cond, msg) => { if (!cond) { fails++; console.log('  FAIL ' + msg); } };

/* ---- 1. All Time must reproduce today's roster-derived list exactly ---- */
console.log('1. All Time reproduces the pre-change list');
const roster = Object.fromEntries(coll.customers.map(c => [c.customer_id, c]));
for (const region of REGIONS) {
  const f = f0 = regionFixture(region);
  load(f);
  setControls('alltime', f.months[f.months.length - 1], f.years[f.years.length - 1]);
  api.update();

  const before = [...f.ids].map(i => roster[i]).sort((a, b) => b.gallons - a.gallons);
  const html = $('region-customer-list').innerHTML;
  const shown = before.slice(0, 50);
  ok(shown.every(c => html.includes(api.esc(c.name))), `${region}: top-50 names all present`);
  // order and gallons, not just presence
  const gal = [...html.matchAll(/<td class="gal">([\d,]+)<\/td>/g)].map(m => Number(m[1].replace(/,/g, '')));
  const wantGal = shown.map(c => c.gallons);
  ok(JSON.stringify(gal) === JSON.stringify(wantGal),
     `${region}: gallons sequence matches roster order exactly`);
  ok($('region-customer-count').textContent ===
     `(${f.ids.size} of ${f.ids.size} in this region)`.replace(/(\d)(?=(\d{3})+\b)/g, '$1,'),
     `${region}: count reads "${$('region-customer-count').textContent}"`);
  console.log(`   ${region.padEnd(30)} ${$('region-customer-count').textContent}`);
}

/* ---- 2. every period mode: month set + gallons must match monthly_by_region ---- */
console.log('\n2. Period math agrees with monthly_by_region');
for (const region of REGIONS) {
  const f = f0 = regionFixture(region);
  load(f);
  for (const mode of ['month', 'ytd', 'year', 'alltime_todate', 'alltime']) {
    const sel = f.months[f.months.length - 1];
    setControls(mode, sel, sel.slice(0, 4));
    api.update();
    const pm = api.months(mode, f.months, sel);
    const expect = pm.reduce((s, m) => s + (f.monthTotals[m] || 0), 0);
    const rows = f.records.filter(r => pm.includes(r.month));
    const got = rows.reduce((s, r) => s + r.gallons, 0);
    ok(got === expect, `${region} / ${mode}: records ${got} vs monthly_by_region ${expect}`);
    if (region === 'Outer Burlington')
      console.log(`   ${mode.padEnd(16)} ${String(pm.length).padStart(3)} months  ${expect.toLocaleString().padStart(9)} gal  ${api.label(mode, f.months, sel)}`);
  }
}

/* ---- 3. picker visibility ---- */
console.log('\n3. Picker visibility per mode');
f0 = regionFixture('Outer Burlington'); load(f0);
for (const [mode, wantMonth, wantYear] of [
  ['month', true, false], ['ytd', true, false], ['year', false, true],
  ['alltime_todate', true, false], ['alltime', false, false]]) {
  setControls(mode);
  api.update();
  const mShown = $('cust-month-select').style.display !== 'none';
  const yShown = $('cust-year-select').style.display !== 'none';
  const mLab = $('cust-month-label').style.display !== 'none';
  const yLab = $('cust-year-label').style.display !== 'none';
  ok(mShown === wantMonth && mLab === wantMonth, `${mode}: month picker ${mShown}`);
  ok(yShown === wantYear && yLab === wantYear, `${mode}: year picker ${yShown}`);
  console.log(`   ${mode.padEnd(16)} Month=${mShown ? 'shown' : 'hidden'}  Year=${yShown ? 'shown' : 'hidden'}`);
}

/* ---- 4. Active first ordering ---- */
console.log('\n4. Sort behaviour');
f0 = regionFixture('Burlington / South Burlington'); load(f0);
setControls('alltime');
api.update();
const gallonsHtml = $('region-customer-list').innerHTML;
api.setSort('status');
const statusHtml = $('region-customer-list').innerHTML;
ok($('cust-sort-status')._classes.has('active'), 'Active-first button gets .active');
ok(!$('cust-sort-gallons')._classes.has('active'), 'Top-producers button loses .active');
ok(gallonsHtml !== statusHtml, 'the two sorts render different order');
const rank = s => s === 'Active' ? 0 : s === 'Inactive' ? 2 : 1;
const statuses = [...statusHtml.matchAll(/status-badge[^>]*>([^<]+)</g)].map(m => m[1].trim());
let monotonic = true;
for (let i = 1; i < statuses.length; i++) if (rank(statuses[i]) < rank(statuses[i - 1])) monotonic = false;
ok(monotonic, 'Active first: rendered statuses are non-decreasing by rank');
// full-set check against the real comparator, since the rendered 50 may be all-active
const full = api.agg(f0.records);
const wantStatus = api.byStatus(full);
let groupsOk = true, lastRank = -1, seen = [];
wantStatus.forEach(c => {
  const r = c.is_active === true ? 0 : c.is_active === false ? 2 : 1;
  if (r < lastRank) groupsOk = false;
  lastRank = Math.max(lastRank, r);
  if (!seen.includes(r)) seen.push(r);
});
ok(groupsOk, 'Active first: full set partitions Active -> Unknown -> Inactive');
// within each group, gallons descending
let withinOk = true;
for (let i = 1; i < wantStatus.length; i++) {
  const a = wantStatus[i - 1], b = wantStatus[i];
  if (a.is_active === b.is_active && b.gallons > a.gallons) withinOk = false;
}
ok(withinOk, 'Active first: gallons descending inside each status group');
console.log(`   rendered top-50 statuses: ${[...new Set(statuses)].join(' -> ')}`);
console.log(`   full set groups present:  ${seen.map(r => ['Active','Unknown','Inactive'][r]).join(' -> ')} (${wantStatus.length} customers)`);

/* ---- 5. empty period ---- */
console.log('\n5. Empty period');
const fs2 = f0 = regionFixture('Central South'); load(fs2);
// The pickers only ever offer months this region has data in, so an empty
// period is unreachable through the UI. Force it to prove the guard works.
const unreachable = fs2.months.find(m => !fs2.records.some(r => r.month === m));
ok(unreachable === undefined, 'every offered month has data (empty period unreachable in UI)');
api.setMonths(fs2.months.concat(['2099-01']));
setControls('month', '2099-01');
api.update();
const emptyNote = $('region-customer-note').innerHTML;
const emptyList = $('region-customer-list').innerHTML;
ok(/had a qualifying pickup/.test(emptyNote), 'empty period shows the fallback message');
ok(emptyList === '', 'empty period renders no table');
ok($('region-customer-count').textContent.startsWith('(0 of'), 'empty period count reads 0 of N');
console.log(`   count: ${$('region-customer-count').textContent}`);
console.log(`   note:  ${emptyNote.replace(/<[^>]+>/g, '')}`);

/* ---- 6. no bad tokens anywhere ---- */
console.log('\n6. Rendered output hygiene');
let bad = [];
for (const region of REGIONS) {
  const f = f0 = regionFixture(region); load(f);
  for (const mode of ['month', 'ytd', 'year', 'alltime_todate', 'alltime']) {
    for (const sel of [f.months[f.months.length - 1], f.months[0], f.months[Math.floor(f.months.length / 2)]]) {
      setControls(mode, sel, sel.slice(0, 4));
      api.update();
      const blob = $('region-customer-list').innerHTML +
                   $('region-customer-note').innerHTML +
                   $('region-customer-count').textContent;
      if (/undefined|NaN|>null<|\[object/.test(blob)) bad.push(`${region}/${mode}/${sel}`);
    }
  }
}
ok(bad.length === 0, `bad tokens in: ${bad.slice(0, 5).join(', ')}`);
console.log(`   checked ${REGIONS.length * 5 * 3} combinations: ${bad.length ? 'BAD' : 'clean'}`);

console.log('\n' + (fails ? `${fails} FAILURE(S)` : 'ALL CHECKS PASSED'));
process.exit(fails ? 1 : 0);
