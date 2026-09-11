// Data and page files are read by their repo-relative names, so anchor the
// working directory to the repo root and this runs from anywhere.
process.chdir(require('path').join(__dirname, '..'));

const fs=require('fs');
let footerStore=null;
function mkEl(id){const el={id,style:{},dataset:{},innerHTML:'',textContent:'',value:'',_classes:new Set(),children:[],
 classList:{toggle(c,o){o?el._classes.add(c):el._classes.delete(c)},add(c){el._classes.add(c)},remove(c){el._classes.delete(c)},contains(c){return el._classes.has(c)}},
 appendChild(c){el.children.push(c);if(c.className==='list-more')footerStore=c;return c},
 querySelector(){ if(!el._btn) el._btn=mkEl('_btn'); return el._btn; },
 addEventListener(ev,fn){el['_on'+ev]=fn}};return el;}
const els={};const document={getElementById:id=>(els[id]=els[id]||mkEl(id)),createElement:t=>mkEl('_'+t)};
const utils=fs.readFileSync('dashboard-utils.js','utf8');
const html=fs.readFileSync('region.html','utf8');
let script=html.slice(html.indexOf('<script>\n/*')+8, html.lastIndexOf('</script>')).replace(/\n\s*init\(\);\s*$/,'\n');
const data=JSON.parse(fs.readFileSync('oil_data.json','utf8'));
const coll=JSON.parse(fs.readFileSync('oil_collections.json','utf8'));
const api=new Function('document',utils+'\n'+script+`\nreturn {set(a,b,c,d,e,f){regionRecords=a;regionMonths=b;regionYears=c;regionMonthTotals=d;regionMemberCount=e;customerSortMode=f},u:updateRegionCustomers};`)(document);
const $=id=>document.getElementById(id);

const region='Burlington / South Burlington';
const ids=new Set(data.region_customers[region]);
const mt={};data.monthly_by_region.filter(r=>r.region===region).forEach(r=>mt[r.month]=(mt[r.month]||0)+r.gallons);
const months=Object.keys(mt).sort();
api.set(coll.records.filter(r=>ids.has(r.customer_id)),months,[...new Set(months.map(m=>m.slice(0,4)))].sort(),mt,ids.size,'gallons');
const set=(mode,m)=>{$('cust-mode-select').value=mode;$('cust-month-select').value=m||months[months.length-1];$('cust-year-select').value='2026';};

let fails=0; const ok=(c,m)=>{if(!c){fails++;console.log('  FAIL '+m)}else console.log('  ok   '+m)};
// count data rows via the rank cell, so the <thead> row is not included
const rows=()=>($('region-customer-list').innerHTML.match(/<td class="rank">/g)||[]).length;
const list=$('region-customer-list');

set('alltime'); api.u();
console.log(`Region has ${ids.size} customers`);
ok(rows()===50, `collapsed shows 50 rows (got ${rows()})`);
ok(list.children.length===1, 'expand footer appended');
ok(footerStore && /Show all 239/.test(footerStore.innerHTML), `footer label: ${footerStore?footerStore.innerHTML.replace(/<[^>]+>/g,' ').trim():'none'}`);

// simulate the expand click
list.dataset.expanded='true';
api.u.call(null);
set('alltime'); list.dataset.expanded='true';
// re-render directly through the util to mimic the click path
new Function('document','utils','rows','renderFn', '')  // noop
;(function(){
  const f=new Function('document',utils+`\nreturn renderExpandableCustomerList;`)(document);
  const all=JSON.parse(JSON.stringify([...Array(239)].map((_,i)=>({customer_id:i,name:'C'+i,geo_town:'T',gallons:239-i,pickups:1,is_active:true}))));
  const c=document.getElementById('_probe'); c.dataset.expanded='true';
  const g=new Function('document',utils+`\nreturn {r:renderExpandableCustomerList,t:customerTableHtml};`)(document);
  g.r(c, all, s=>g.t(s,{town:true,dates:false}));
  const n=(c.innerHTML.match(/<td class="rank">/g)||[]).length;
  ok(n===239, `expanded shows all 239 when <=250 (got ${n})`);
})();

// filter change must reset expansion
list.dataset.expanded='true';
set('month','2026-09'); api.u();
ok(list.dataset.expanded==='false', 'expansion resets to collapsed on filter change');
ok(rows()<=50, `after reset shows <=50 rows (got ${rows()})`);
console.log(fails?`\n${fails} FAILURE(S)`:'\nEXPANSION OK'); process.exit(fails?1:0);
