// Data and page files are read by their repo-relative names, so anchor the
// working directory to the repo root and this runs from anywhere.
process.chdir(require('path').join(__dirname, '..'));

const fs=require('fs');
function mkEl(id){const el={id,style:{},dataset:{},innerHTML:'',textContent:'',value:'',_c:new Set(),children:[],
 classList:{toggle(c,o){o?el._c.add(c):el._c.delete(c)},add(c){el._c.add(c)},remove(c){el._c.delete(c)},contains(c){return el._c.has(c)}},
 appendChild(c){el.children.push(c);return c},querySelector(){return mkEl('_q')},addEventListener(){}};return el;}
const els={};const document={getElementById:id=>(els[id]=els[id]||mkEl(id)),createElement:t=>mkEl('_'+t)};
const utils=fs.readFileSync('dashboard-utils.js','utf8');
const api=new Function('document',utils+'\nreturn {apply:applyPeriodPickers,picker:pickerForMode,MODES:VIEW_MODES};')(document);

// The exact contract customers.html and region.html both rely on
const cases=[['month','month'],['ytd','month'],['year','year'],['alltime_todate','month'],['alltime',null]];
let fails=0;const ok=(c,m)=>{if(!c){fails++;console.log('  FAIL '+m)}else console.log('  ok   '+m)};

console.log('VIEW_MODES (shared by both pages):');
api.MODES.forEach(m=>console.log(`   ${m.value.padEnd(16)} "${m.label}"  picker=${m.picker}`));

console.log('\napplyPeriodPickers contract:');
for(const [mode,want] of cases){
  const els2={monthSelect:mkEl('ms'),monthLabel:mkEl('ml'),yearSelect:mkEl('ys'),yearLabel:mkEl('yl')};
  const got=api.apply(mode,els2);
  ok(got===want,`${mode}: returns ${got}`);
  ok(els2.monthSelect.style.display===(want==='month'?'':'none'),`${mode}: month select ${want==='month'?'shown':'hidden'}`);
  ok(els2.monthLabel.style.display===(want==='month'?'':'none'),`${mode}: month label matches select`);
  ok(els2.yearSelect.style.display===(want==='year'?'':'none'),`${mode}: year select ${want==='year'?'shown':'hidden'}`);
  ok(els2.yearLabel.style.display===(want==='year'?'':'none'),`${mode}: year label matches select`);
}
// tolerates missing elements (region page could omit one)
const partial=api.apply('year',{yearSelect:mkEl('y')});
ok(partial==='year','tolerates missing elements without throwing');
ok(api.apply('bogus',{})==='month','unknown mode falls back to month, as before');

// customers.html must no longer carry its own copy
const ch=fs.readFileSync('customers.html','utf8');
ok(/applyPeriodPickers\(/.test(ch),'customers.html calls the shared helper');
ok(!/showMonth/.test(ch),'customers.html inline picker copy removed');
ok(!/monthSel\.style\.display/.test(ch),'no leftover direct style writes');
const rh=fs.readFileSync('region.html','utf8');
ok(/applyPeriodPickers\(/.test(rh),'region.html calls the same shared helper');
console.log(fails?`\n${fails} FAILURE(S)`:'\nCUSTOMERS PAGE CONTRACT INTACT');process.exit(fails?1:0);
