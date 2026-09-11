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
const data=JSON.parse(fs.readFileSync('oil_data.json','utf8'));
const api=new Function('document','Chart',utils+'\n'+script+
  '\nreturn {regionProjection, seasonalityProfile, seasonalityHighlights, SEASONALITY_WEIGHTS, MONTH_LABELS, MONTH_NAMES, WINTER_MONTHS, MIN_COMPLETE_YEARS};')(document,global.Chart);

let fails=0; const ok=(c,m)=>{if(!c){fails++;console.log('  FAIL '+m)}else console.log('  ok   '+m)};
const LDD=data.projection.latest_data_date;
const totalsFor=reg=>{const t={};data.monthly_by_region.filter(r=>r.region===reg).forEach(r=>t[r.month]=(t[r.month]||0)+r.gallons);return t;};

console.log('1. Per-region projection reproduces the company formula');
// company-wide: feed it monthly_totals and compare to the published projection
const compTotals={}; data.monthly_totals.forEach(r=>compTotals[r.month]=r.gallons);
const compProj=(api.regionProjection(compTotals,LDD)||{}).projected;
console.log(`   derived ${Math.round(compProj).toLocaleString()} vs published ${data.projection.projected_current_year.toLocaleString()}`);
ok(Math.abs(compProj-data.projection.projected_current_year)<1,'matches published projected_current_year');

console.log('\n2. Southern Ski Slopes matches the verified plan figures');
const ssTot=totalsFor('Southern Ski Slopes');
const ssProj=(api.regionProjection(ssTot,LDD)||{}).projected;
const ss=api.seasonalityProfile(ssTot,LDD,ssProj);
ok(Math.round(ssProj)===7217,`projection 7,217 (got ${Math.round(ssProj).toLocaleString()})`);
const expectPct=[23.2,20.4,25.4,6.5,4.5,1.4,2.0,1.1,1.5,1.9,1.8,10.4];
const gotPct=ss.shares.map(s=>+(s*100).toFixed(1));
ok(JSON.stringify(gotPct)===JSON.stringify(expectPct),`shares match plan: ${gotPct.join('/')}`);
const expectGal=[1677,1469,1832,466,326,100,148,76,107,138,129,750];
const gotGal=ss.gallons.map(g=>Math.round(g));
ok(JSON.stringify(gotGal)===JSON.stringify(expectGal),`gallons match plan: ${gotGal.slice(0,3).join('/')}...`);
ok(ss.fullyElapsed===8,`2026 contributes through month ${ss.fullyElapsed} (Aug)`);

console.log('\n3. Invariants hold for ALL regions');
let bad=[];
for(const reg of data.region_names){
  const t=totalsFor(reg); const pj=(api.regionProjection(t,LDD)||{}).projected; const p=api.seasonalityProfile(t,LDD,pj);
  if(!p){bad.push(reg+':null');continue}
  const sumShares=p.shares.reduce((a,b)=>a+b,0);
  const sumGal=p.gallons.reduce((a,b)=>a+b,0);
  if(Math.abs(sumShares-1)>1e-9) bad.push(reg+':shares='+sumShares);
  if(Math.abs(sumGal-p.scale)>0.01) bad.push(reg+':gallons!=scale');
  if(p.shares.some(s=>!Number.isFinite(s)||s<0)) bad.push(reg+':bad share');
}
ok(bad.length===0,`all ${data.region_names.length} regions: shares sum to 1 and gallons sum to scale ${bad.length?'-- '+bad.join(', '):''}`);

console.log('\n4. Per-month weight renormalization (the 5/3 rule)');
const W=api.SEASONALITY_WEIGHTS;
ok(Math.abs(Object.values(W).reduce((a,b)=>a+b,0)-1)<1e-9,'base weights sum to 1.0 (40/30/20/10, float-tolerant)');
const drop=1-W[0];
console.log(`   dropping ${W[0]*100}% scales the rest by 1/${drop.toFixed(2)} = ${(1/drop).toFixed(4)} (5/3 = ${(5/3).toFixed(4)})`);
ok(Math.abs(1/drop-5/3)<1e-9,'renormalization factor is exactly 5/3');
[[W[1]/drop,0.5],[W[2]/drop,1/3],[W[3]/drop,1/6]].forEach(([got,want],i)=>
  ok(Math.abs(got-want)<1e-9,`Sep-Dec weight ${[2025,2024,2023][i]} = ${(got*100).toFixed(1)}%`));

console.log('\n5. Fallback and degradation');
ok(api.seasonalityProfile(ssTot,null,ssProj)===null,'null latest_data_date -> null');
const oneYear={}; for(let m=1;m<=12;m++) oneYear[`2025-${String(m).padStart(2,'0')}`]=100;
ok(api.seasonalityProfile(oneYear,LDD,1200)===null,`only 1 complete year -> null (min ${api.MIN_COMPLETE_YEARS})`);
ok(api.regionProjection({},LDD)===null,'empty totals -> null projection');
ok(api.regionProjection(ssTot,'garbage')===null,'garbage date -> null');
const noProj=api.seasonalityProfile(ssTot,LDD,null);
ok(noProj && noProj.scale>0,`no projection -> falls back to mean annual (${Math.round(noProj.scale).toLocaleString()})`);

console.log('\n6. Highlights come from the same shares as the chart');
const ssH=api.seasonalityHighlights(ss);
console.log(`   peak ${ssH.peakMonth} ${(ssH.peakShare*100).toFixed(1)}% | low ${ssH.lowMonth} ${(ssH.lowShare*100).toFixed(1)}% | ${ssH.multiple.toFixed(1)}x | Nov-Mar ${(ssH.winterShare*100).toFixed(0)}%`);
ok(ssH.peakMonth==='March','Southern Ski Slopes peak = March');
ok(ssH.lowMonth==='August','low = August');
ok(Math.abs(ssH.multiple-24.1)<0.1,'peak vs low = 24.1x');
ok(Math.abs(ssH.winterShare-0.81)<0.005,'Nov-Mar share = 81%');
// derived from the chart's own array, not recomputed
const maxShare=Math.max(...ss.shares), minShare=Math.min(...ss.shares);
ok(ssH.peakShare===maxShare && ssH.lowShare===minShare,'peak/low taken straight from profile.shares');
ok(Math.abs(ssH.multiple-maxShare/minShare)<1e-12,'multiple is exactly max/min of those shares');
ok(api.WINTER_MONTHS.join()==='10,11,0,1,2','Nov-Mar = months 10,11,0,1,2');
// every region: finite, sane
let hbad=[];
for(const reg of data.region_names){
  const t=totalsFor(reg); const p2=api.seasonalityProfile(t,LDD,(api.regionProjection(t,LDD)||{}).projected);
  const h=api.seasonalityHighlights(p2); if(!h){hbad.push(reg);continue}
  if(!(h.winterShare>=0&&h.winterShare<=1)) hbad.push(reg+':winter');
  if(h.multiple!==null&&!(h.multiple>=1)) hbad.push(reg+':mult');
  if(!api.MONTH_NAMES.includes(h.peakMonth)) hbad.push(reg+':peak');
}
ok(hbad.length===0,`all ${data.region_names.length} regions produce sane highlights ${hbad.length?hbad.join(','):''}`);
// zero-month guard
const zeroProfile={shares:[0.5,0.5,0,0,0,0,0,0,0,0,0,0]};
ok(api.seasonalityHighlights(zeroProfile).multiple===null,'zero low share -> multiple null (renders as a dash)');
ok(api.seasonalityHighlights(null)===null,'null profile -> null');

console.log('\n'+(fails?`${fails} FAILURE(S)`:'ALL CHECKS PASSED'));
process.exit(fails?1:0);
