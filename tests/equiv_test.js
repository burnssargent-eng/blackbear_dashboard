// Data and page files are read by their repo-relative names, so anchor the
// working directory to the repo root and this runs from anywhere.
process.chdir(require('path').join(__dirname, '..'));

const fs=require('fs');
function mkEl(id){const el={id,style:{},dataset:{},innerHTML:'',textContent:'',children:[],
 classList:{toggle(){},add(){},remove(){},contains(){return false}},
 appendChild(c){el.children.push(c);return c},querySelector(){return mkEl('_q')},addEventListener(){},
 getContext(){return {}}};return el;}
const els={};
const document={getElementById:id=>(els[id]=els[id]||mkEl(id)),createElement:t=>mkEl('_'+t)};
global.Chart=function(){return {};};
const utils=fs.readFileSync('dashboard-utils.js','utf8');
const html=fs.readFileSync('schmootz.html','utf8');
const start=html.lastIndexOf('<script>')+'<script>'.length;
let script=html.slice(start, html.lastIndexOf('</script>')).replace(/\n\s*init\(\);\s*$/,'\n');
const data=JSON.parse(fs.readFileSync('schmootz_data.json','utf8'));
const api=new Function('document','Chart',
  utils+'\n'+script+'\nreturn {render, equivalents, EQUIV, DRUM_SVG, POOL_SVG, FIELD_SVG};')(document,global.Chart);

let fails=0; const ok=(c,m)=>{if(!c){fails++;console.log('  FAIL '+m)}else console.log('  ok   '+m)};

console.log('1. equivalents() from the live total');
const eq=api.equivalents(data.combined_total);
console.log(`   ${eq.total.toLocaleString()} gal -> ${eq.drums.toLocaleString()} drums, ${eq.blocks} blocks (${eq.drumsPerBlock}/block), ${eq.pools} pools, ${eq.fieldDepthFt} ft ("${eq.fieldDepthWords}")`);
ok(eq.drums===43754,'drums = 43,754');
ok(eq.blocks===44,'blocks = 44');
ok(eq.drumsPerBlock===1000,'each block = 1,000 drums');
ok(eq.pools==='3.6','pools = 3.6');
ok(eq.fieldDepthFt==='5.6','field depth = 5.6 ft');
ok(eq.fieldDepthWords==='more than 5 feet','prose form = "more than 5 feet"');
// independent recomputation of the field math
const indep=data.combined_total/(57600*7.48052);
ok(Math.abs(indep-Number(eq.fieldDepthFt))<0.05,`field math matches independent calc (${indep.toFixed(3)})`);

console.log('\n2. rendered panel');
const page=document.getElementById('page'); api.render(page,data);
const out=page.innerHTML;
ok(/equiv-panel/.test(out),'panel present');
ok(/Waste Displaced in Real Terms/.test(out),'title present');
const drums=(out.match(/class="equiv-drum"/g)||[]).length;
const cardIcons=(out.match(/class="equiv-card-icon"/g)||[]).length;
ok(drums===44,`exactly 44 drum icons (got ${drums})`);
ok(cardIcons===2,`exactly 2 card pictograms (got ${cardIcons})`);
ok((out.match(/<svg/g)||[]).length===46,'46 SVGs total = 44 drums + 2 card icons');
ok(/43,754/.test(out),'headline 43,754');
ok(/standard 55-gallon drums/.test(out),'says "standard 55-gallon drums"');
ok(!/standard barrels/.test(out),'old "standard barrels" wording gone');
ok(/Each drum icon represents about 1,000\s+standard 55-gallon drums/.test(out),'drum caption present');
ok(/equiv-grid/.test(out),'drums render in a grid, not a strip');
ok(!/equiv-wall|equiv-convoy/.test(out),'old strip markup gone');
// drum icon must read as a cylinder, not stacked blocks
const drumSvg=api.DRUM_SVG;
ok((drumSvg.match(/<ellipse/g)||[]).length===2,'drum has an open elliptical top rim');
ok((drumSvg.match(/<path/g)||[]).length===3,'three body bands, hoops as the gaps between them');
ok(/q19 6 38 0/.test(drumSvg),'band edges curve with the cylinder');
ok(!/<rect/.test(drumSvg),'no rectangles left — not keycaps');
ok(!/stroke=/.test(drumSvg),'solid silhouette, matching the reference');
ok(!/#[0-9a-f]{3,6}/i.test(drumSvg),'currentColor only, no hardcoded hex');
ok(/equiv-card-icon/.test(out),'supporting cards carry pictograms');
ok(/<svg[^>]*equiv-card-icon[\s\S]*?<\/svg>/.test(out),'card icons are inline SVG');
ok(/Olympic-size swimming pools/.test(out),'pools card present');
ok(/660,000 gallons per pool/.test(out),'pool basis stated');
ok(/5\.6 ft/.test(out) && /football field/.test(out),'football field card present');
ok(/360 × 160 ft/.test(out),'field basis stated');

console.log('\n3. removed content');
ok(!/tanker/i.test(out),'no tanker trucks anywhere in output');
ok(!/tote/i.test(out),'no totes anywhere in output');
ok(!/equiv-truck|equiv-convoy/.test(out),'no stale truck markup');
const css=fs.readFileSync('dashboard.css','utf8');
ok(!/equiv-truck|equiv-convoy/.test(css),'no stale truck CSS');

console.log('\n4. copy + no unverified claims');
ok(/enough to fill/.test(out),'summary sentence present');
ok(/more than 5 feet deep/.test(out),'summary hedges the depth');
for(const w of ['carbon','emission','CO2','offset','greenhouse']) ok(!new RegExp(w,'i').test(out),`no "${w}" claim`);

console.log('\n5. existing content intact');
ok(/hero-value">2,406,455</.test(out),'hero still 2,406,455');
ok(/id="chart-barr-hill"/.test(out)&&/id="chart-shop"/.test(out),'both charts survive');
ok(/class="stats-bar"/.test(out),'three source cards kept');
ok(/Yearly Totals/.test(out)&&/Monthly Totals/.test(out),'both tables kept');
ok(out.indexOf('hero-stat')<out.indexOf('equiv-panel')&&out.indexOf('equiv-panel')<out.indexOf('stats-bar'),'panel between hero and source cards');
ok(!/undefined|NaN|>null<|\[object/.test(out),'no undefined / NaN / null / [object');

console.log('\n6. degradation + guard');
for(const bad of [undefined,null,0,-5,'abc']) ok(api.equivalents(bad)===null,`equivalents(${JSON.stringify(bad)}) -> null`);
const p2=document.getElementById('p2'); api.render(p2,Object.assign({},data,{combined_total:0}));
ok(!/equiv-panel/.test(p2.innerHTML),'panel omitted when total unusable');
ok(/hero-value/.test(p2.innerHTML),'rest of page still renders');
const big=api.equivalents(data.combined_total*100);
console.log(`   100x total -> ${big.drums.toLocaleString()} drums, ${big.blocks} blocks (${big.drumsPerBlock.toLocaleString()}/block, cap ${api.EQUIV.MAX_BLOCKS})`);
ok(big.blocks<=api.EQUIV.MAX_BLOCKS,'blocks stay within MAX_BLOCKS');
ok(big.drumsPerBlock===Math.round(big.drums/big.blocks),'caption switches to the real ratio when clamped');
ok(eq.drumsPerBlock===1000 && Math.abs(eq.drums/eq.blocks-1000)<10,'unclamped caption states 1,000 and is accurate to <1%');
const small=api.equivalents(100000);
console.log(`   small total -> ${small.fieldDepthFt} ft ("${small.fieldDepthWords}")`);
ok(!/NaN/.test(small.fieldDepthWords),'small total still yields sane prose');

console.log('\n'+(fails?`${fails} FAILURE(S)`:'ALL CHECKS PASSED'));
process.exit(fails?1:0);
