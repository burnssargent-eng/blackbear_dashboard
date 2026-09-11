// Data and page files are read by their repo-relative names, so anchor the
// working directory to the repo root and this runs from anywhere.
process.chdir(require('path').join(__dirname, '..'));

const fs=require('fs');
function mkEl(i){const e={id:i,style:{},dataset:{},innerHTML:'',textContent:'',children:[],
 classList:{toggle(){},add(){},remove(){},contains(){return false}},appendChild(c){e.children.push(c);return c},
 querySelector(){return mkEl('q')},addEventListener(){},getContext(){return{}}};return e;}
const els={};const document={getElementById:i=>(els[i]=els[i]||mkEl(i)),createElement:t=>mkEl('_'+t)};
global.Chart=function(){return{}};
const utils=fs.readFileSync('dashboard-utils.js','utf8');
const html=fs.readFileSync('region.html','utf8');
const st=html.lastIndexOf('<script>')+8;
const script=html.slice(st,html.lastIndexOf('</script>')).replace(/\n\s*init\(\);\s*$/,'\n');
const d=JSON.parse(fs.readFileSync('oil_data.json','utf8'));
const coll=JSON.parse(fs.readFileSync('oil_collections.json','utf8'));
const api=new Function('document','Chart',utils+'\n'+script+
 '\nreturn {regionProjection, priorMonthGallons, activeCollectedThisYear, MONTH_KEYS};')(document,global.Chart);

let fails=0; const ok=(c,m)=>{if(!c){fails++;console.log('  FAIL '+m)}else console.log('  ok   '+m)};
const LDD=d.projection.latest_data_date;
const CY=Number(LDD.slice(0,4));
const totals=r=>{const t={};d.monthly_by_region.filter(x=>x.region===r).forEach(x=>t[x.month]=(t[x.month]||0)+x.gallons);return t;};
const recsFor=r=>{const s=new Set(d.region_customers[r]);return coll.records.filter(x=>s.has(x.customer_id));};

console.log('1. Projection object + percent');
const comp={};d.monthly_totals.forEach(r=>comp[r.month]=r.gallons);
const cp=api.regionProjection(comp,LDD);
ok(Math.abs(cp.projected-d.projection.projected_current_year)<1,`company projected ${Math.round(cp.projected).toLocaleString()} matches published`);
ok(Math.abs(cp.percent-d.projection.percent_vs_previous_year)<1e-6,`percent ${(cp.percent*100).toFixed(4)}% matches published`);
ok(cp.previousYear===CY-1,`previousYear = ${cp.previousYear}`);
ok(Math.abs(cp.percent-(cp.projected/cp.previousFull-1))<1e-12,'percent == projected/previousFull - 1');

console.log('\n2. All 14 regions');
const bad=[];
for(const reg of d.region_names){
  const t=totals(reg), rs=recsFor(reg);
  const p=api.regionProjection(t,LDD);
  const months=Object.keys(t).sort();
  const pm=api.priorMonthGallons(t,months[months.length-1],months[0]);
  const act=api.activeCollectedThisYear(rs,CY);
  const cardActive=d.region_stats[reg].active;
  // independent recomputation
  const indep=new Set(rs.filter(r=>r.is_active===true&&r.year===CY).map(r=>r.customer_id)).size;
  if(act!==indep) bad.push(reg+':active-mismatch');
  if(act>cardActive) bad.push(reg+':active>card');
  if(p&&!Number.isFinite(p.percent)) bad.push(reg+':percent-not-finite');
  if(p&&!Number.isFinite(p.projected)) bad.push(reg+':proj-not-finite');
  if(pm&&!Number.isFinite(pm.gallons)) bad.push(reg+':prior-not-finite');
  if(rs.length===0) bad.push(reg+':no-records');
}
ok(bad.length===0,`all sane: finite projection/percent, active<=card, records present ${bad.length?bad.join(','):''}`);

console.log('\n3. Pickups = the retained counted-pickup set');
for(const reg of ['Burlington / South Burlington','Southern Ski Slopes']){
  const rs=recsFor(reg);
  ok(rs.length>0 && rs.every(r=>r.gallons>=4),
     `${reg}: ${rs.length.toLocaleString()} pickups, every one >= 4 gal (EMPTY_QTYS already stripped)`);
}

console.log('\n4. Prior month resolution');
const t=totals('Burlington / South Burlington');
const m=Object.keys(t).sort();
const pm=api.priorMonthGallons(t,'2026-09',m[0]);
ok(pm.month==='2026-08'&&pm.gallons===4858,`Sep 2026 -> ${pm.month}: ${pm.gallons.toLocaleString()}`);
const jan=api.priorMonthGallons(t,'2026-01',m[0]);
ok(jan.month==='2025-12',`Jan rolls back a year -> ${jan.month}`);
// absent-but-inside-history => real zero
const gap={'2020-01':10,'2020-03':20};
const z=api.priorMonthGallons(gap,'2020-03','2020-01');
ok(z && z.gallons===0 && z.month==='2020-02','month absent inside history -> 0, not "unavailable"');
// before history => unavailable
ok(api.priorMonthGallons(gap,'2020-01','2020-01')===null,'month before region history -> null (renders "unavailable")');
ok(api.priorMonthGallons(t,null,m[0])===null,'no month -> null');

console.log('\n5. Guards: no Infinity / NaN');
ok(api.regionProjection({},LDD)===null,'empty totals -> null');
ok(api.regionProjection({'2025-01':0,'2026-01':5},LDD)===null,'zero previous year -> null, not Infinity');
ok(api.regionProjection(t,'garbage')===null,'garbage date -> null');
ok(api.activeCollectedThisYear([],CY)===0,'no records -> 0');
ok(api.activeCollectedThisYear(null,CY)===0,'null records -> 0');
const inactive=[{customer_id:1,is_active:false,year:CY},{customer_id:2,is_active:true,year:CY-1}];
ok(api.activeCollectedThisYear(inactive,CY)===0,'inactive, or active-but-no-current-year -> excluded');
const both=[{customer_id:3,is_active:true,year:CY},{customer_id:3,is_active:true,year:CY}];
ok(api.activeCollectedThisYear(both,CY)===1,'same customer counted once');

console.log('\n'+(fails?`${fails} FAILURE(S)`:'ALL CHECKS PASSED'));
process.exit(fails?1:0);
