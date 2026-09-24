const {t,toggle:toggleLanguage,locale:currentLocale}=window.TrackerI18n;
const map=L.map('logbook-map',{zoomControl:false,worldCopyJump:false,minZoom:1,preferCanvas:true,maxBounds:[[-85.0511,-1000000],[85.0511,1000000]],maxBoundsViscosity:1}).setView([25,0],2);
const pathRenderer=L.canvas({padding:.35});
L.control.zoom({position:'bottomright'}).addTo(map);

const MapOnlyControl=L.Control.extend({
  options:{position:'bottomright'},
  onAdd(){
    const wrap=L.DomUtil.create('div','leaflet-bar map-only-control');
    const btn=L.DomUtil.create('button','map-only-button',wrap);
    btn.type='button'; btn.id='map-only-toggle'; btn.textContent='▣'; btn.title=t('map_only');
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.on(btn,'click',()=>{
      document.body.classList.toggle('map-only');
      btn.classList.toggle('active',document.body.classList.contains('map-only'));
      btn.title=document.body.classList.contains('map-only')?t('show_ui'):t('map_only');
      setTimeout(()=>map.invalidateSize(),80);
    });
    return wrap;
  }
});
new MapOnlyControl().addTo(map);

function mapStyleForTheme(theme=window.TrackerTheme?.theme||'light'){return `https://tiles.openfreemap.org/styles/${theme==='light'?'liberty':'fiord'}`;}
const vectorBase=L.maplibreGL({style:mapStyleForTheme(),interactive:false}).addTo(map);
const glMap=vectorBase.getMaplibreMap();
const originalLabelFields=new Map(); let mapLabelLanguage=window.TrackerI18n.language||'en';
function localizedNameField(original,lang){const arr=[['get',`name:${lang}`],['get',`name_${lang}`]];if(lang!=='en')arr.push(['get','name:en'],['get','name_en']);arr.push(['get','name:latin'],original);return ['coalesce',...arr];}
function applyMapLanguage(lang=mapLabelLanguage){mapLabelLanguage=['en','fr','ru'].includes(lang)?lang:'en';try{for(const layer of glMap.getStyle()?.layers||[]){if(layer.type!=='symbol')continue;const cur=layer.layout?.['text-field'];if(!cur)continue;if(!originalLabelFields.has(layer.id))originalLabelFields.set(layer.id,cur);const original=originalLabelFields.get(layer.id);if(!JSON.stringify(original).includes('name'))continue;try{glMap.setLayoutProperty(layer.id,'text-field',localizedNameField(original,mapLabelLanguage));}catch(_){}}}catch(_){} }
glMap.on('styledata',()=>applyMapLanguage());glMap.on('load',()=>applyMapLanguage());
function applyMapTheme(theme=window.TrackerTheme?.theme||'light'){originalLabelFields.clear();try{glMap.setStyle(mapStyleForTheme(theme));}catch(_){} }
function cssVar(name,fallback=''){return getComputedStyle(document.documentElement).getPropertyValue(name).trim()||fallback;}

let routeLayers=[],pointLayers=[],routeRecords=[],pointRecords=[],lastData=null,didFit=false,focusMode=null;
function clearLayers(){[...routeLayers,...pointLayers].forEach(l=>map.removeLayer(l));routeLayers=[];pointLayers=[];routeRecords=[];pointRecords=[];focusMode=null;}
function greatCirclePoints(a,b,steps=32){const toR=x=>x*Math.PI/180,toD=x=>x*180/Math.PI;const lat1=toR(a[0]),lon1=toR(a[1]),lat2=toR(b[0]),lon2=toR(b[1]);const v1=[Math.cos(lat1)*Math.cos(lon1),Math.cos(lat1)*Math.sin(lon1),Math.sin(lat1)],v2=[Math.cos(lat2)*Math.cos(lon2),Math.cos(lat2)*Math.sin(lon2),Math.sin(lat2)];let dot=Math.max(-1,Math.min(1,v1[0]*v2[0]+v1[1]*v2[1]+v1[2]*v2[2]));const omega=Math.acos(dot),sin=Math.sin(omega);if(omega<1e-8)return[a,b];const out=[];for(let i=0;i<=steps;i++){const f=i/steps,s1=Math.sin((1-f)*omega)/sin,s2=Math.sin(f*omega)/sin,x=s1*v1[0]+s2*v2[0],y=s1*v1[1]+s2*v2[1],z=s1*v1[2]+s2*v2[2];out.push([toD(Math.atan2(z,Math.hypot(x,y))),toD(Math.atan2(y,x))]);}return out;}
function unwrap(points){if(!points.length)return[];const out=[[points[0][0],points[0][1]]];let prev=points[0][1];for(const [lat,raw] of points.slice(1)){let lon=raw;while(lon-prev>180)lon-=360;while(lon-prev<-180)lon+=360;out.push([lat,lon]);prev=lon;}return out;}
function routeKey(a,b){return [a,b].sort().join('|');}
function addRepeatedRoute(r,points,opts,tooltip,popupHtml){
  const base=unwrap(points), key=routeKey(r.origin,r.destination);
  const lineOpts={...opts}; const casingExtra=Number(lineOpts.casingExtra??1.2); const casingOpacity=Number(lineOpts.casingOpacity??Math.min(.75,(lineOpts.opacity??.7)+.16));
  delete lineOpts.casingExtra; delete lineOpts.casingOpacity;
  for(const offset of[-360,0,360]){
    const shifted=base.map(([lat,lon])=>[lat,lon+offset]);
    const casing=L.polyline(shifted,{...lineOpts,renderer:pathRenderer,color:cssVar('--logbook-casing','#041016'),opacity:casingOpacity,weight:(lineOpts.weight||1)+casingExtra,interactive:false,lineCap:'round',lineJoin:'round'}).addTo(map);
    const line=L.polyline(shifted,{...lineOpts,renderer:pathRenderer,interactive:false,lineCap:'round',lineJoin:'round'}).addTo(map);
    // Wide invisible hit target: visually thin lifetime routes remain easy to click.
    const hit=L.polyline(shifted,{renderer:pathRenderer,color:'#000',opacity:.001,weight:Math.max(14,(lineOpts.weight||1)+10),interactive:true,lineCap:'round',lineJoin:'round'}).addTo(map);
    hit.bindTooltip(tooltip,{sticky:true}); hit.bindPopup(popupHtml,{maxWidth:360});
    hit.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);focusRoute(key);});
    routeLayers.push(casing,line,hit);
    routeRecords.push({key,origin:r.origin,destination:r.destination,casing,line,hit,baseOpacity:lineOpts.opacity??.7,baseWeight:lineOpts.weight||1,baseCasingOpacity:casingOpacity,baseCasingWeight:(lineOpts.weight||1)+casingExtra});
  }
}
function mergeBreakdown(target,items,keyName){for(const item of items||[]){const key=item?.[keyName];if(!key)continue;if(!target.has(key))target.set(key,{name:key,flights:0,hours:0});const row=target.get(key);row.flights+=Number(item.flights||0);row.hours+=Number(item.hours||0);}}
function mergedConnections(routes){
  const grouped=new Map();
  for(const r of routes||[]){if(!r?.origin||!r?.destination)continue;const forward=r.origin.localeCompare(r.destination)<=0;const a=forward?r.origin:r.destination,b=forward?r.destination:r.origin;const key=`${a}|${b}`;
    if(!grouped.has(key))grouped.set(key,{origin:a,destination:b,origin_lat:forward?r.origin_lat:r.destination_lat,origin_lon:forward?r.origin_lon:r.destination_lon,destination_lat:forward?r.destination_lat:r.origin_lat,destination_lon:forward?r.destination_lon:r.origin_lon,origin_city:forward?r.origin_city:r.destination_city,destination_city:forward?r.destination_city:r.origin_city,origin_name:forward?r.origin_name:r.destination_name,destination_name:forward?r.destination_name:r.origin_name,origin_aliases:forward?r.origin_aliases:r.destination_aliases,destination_aliases:forward?r.destination_aliases:r.origin_aliases,count:0,hours:0,duration_samples:0,duration_estimated:false,modelMap:new Map(),identMap:new Map(),directions:[]});
    const g=grouped.get(key);g.count+=Number(r.count||0);g.hours+=Number(r.hours||0);g.duration_samples+=Number(r.duration_samples||0);g.duration_estimated||=Boolean(r.duration_estimated);mergeBreakdown(g.modelMap,r.aircraft_models,'model');mergeBreakdown(g.identMap,r.registrations,'ident');g.directions.push({origin:r.origin,destination:r.destination,origin_city:r.origin_city,destination_city:r.destination_city,count:Number(r.count||0)});
  }
  return [...grouped.values()].map(g=>({...g,average_hours:g.duration_samples?g.hours/g.duration_samples:null,aircraft_models:[...g.modelMap.values()].sort((a,b)=>b.flights-a.flights),registrations:[...g.identMap.values()].sort((a,b)=>b.flights-a.flights)}));
}

function routeVisual(count,maxCount){const max=Math.max(1,Number(maxCount||1));const raw=Math.log1p(Math.max(1,Number(count||1)))/Math.log1p(max);const strength=Math.pow(raw,1.65);return{weight:.28+3.45*strength,opacity:.09+.78*strength,casingExtra:.48+strength*.50,casingOpacity:.13+.40*strength};}
function airportDisplay(code,city,name){const place=city||name||'';return place?`${code} (${place})`:code;}
function aliasesHtml(aliases,code){const rest=(aliases||[]).filter(x=>x&&x!==code);return rest.length?`<div class="route-popup-note">${escapeHtml(t('airport_aliases'))}: ${rest.map(escapeHtml).join(', ')}</div>`:'';}
function routeDetailsHtml(r){const avg=r.average_hours==null?'—':`${fmt(r.average_hours,2)} h`;const modelRows=(r.aircraft_models||[]).slice(0,10).map(x=>`<div><span>${escapeHtml(x.name)}</span><b>${fmt(x.flights)}×</b></div>`).join('')||`<div><span>—</span></div>`;const identRows=(r.registrations||[]).slice(0,8).map(x=>`<div><span>${escapeHtml(x.name)}</span><b>${fmt(x.flights)}×</b></div>`).join('');const dirRows=(r.directions||[]).filter(x=>x.count).map(x=>`<div><span>${escapeHtml(airportDisplay(x.origin,x.origin_city))} → ${escapeHtml(airportDisplay(x.destination,x.destination_city))}</span><b>${fmt(x.count)}×</b></div>`).join('');return `<div class="route-popup"><h3>${escapeHtml(airportDisplay(r.origin,r.origin_city,r.origin_name))} ↔ ${escapeHtml(airportDisplay(r.destination,r.destination_city,r.destination_name))}</h3>${aliasesHtml(r.origin_aliases,r.origin)}${aliasesHtml(r.destination_aliases,r.destination)}<div class="route-popup-grid"><span>${t('times_flown')}</span><b>${fmt(r.count)}</b><span>${t('total_route_time')}</span><b>${fmt(r.hours,1)} h</b><span>${t('average_flight_time')}</span><b>${avg}</b></div>${dirRows?`<div class="route-popup-breakdown"><strong>${t('direction_breakdown')}</strong>${dirRows}</div>`:''}<div class="route-popup-breakdown"><strong>${t('aircraft_breakdown')}</strong>${modelRows}</div>${identRows?`<div class="route-popup-breakdown"><strong>${t('registration_breakdown')}</strong>${identRows}</div>`:''}${r.duration_estimated?`<div class="route-popup-note">${t('average_time_estimated')}</div>`:''}</div>`;}
function airportDetailsHtml(p){
  const models=(p.aircraft_models||[]).slice(0,10).map(x=>`<div><span>${escapeHtml(x.model)}</span><b>${fmt(x.flights)}×</b></div>`).join('')||'<div><span>—</span></div>';
  const regs=(p.registrations||[]).slice(0,6).map(x=>`<div><span>${escapeHtml(x.ident)}</span><b>${fmt(x.flights)}×</b></div>`).join('');
  const conns=(p.connections||[]).slice(0,10).map(x=>`<div><span>${escapeHtml(airportDisplay(x.code,x.city,x.name))}</span><b>${fmt(x.flights)}×</b></div>`).join('')||'<div><span>—</span></div>';
  return `<div class="route-popup airport-popup"><h3>${escapeHtml(airportDisplay(p.code,p.city,p.name))}</h3>${aliasesHtml(p.aliases,p.code)}<div class="route-popup-grid"><span>${t('visits')}</span><b>${fmt(p.visits)}</b><span>${t('flights_touching_airport')}</span><b>${fmt(p.flights)}</b><span>${t('departures')}</span><b>${fmt(p.departures)}</b><span>${t('arrivals')}</span><b>${fmt(p.arrivals)}</b><span>${t('logged_hours')}</span><b>${fmt(p.hours,1)} h</b><span>${t('first_visit')}</span><b>${escapeHtml(p.date_first||'—')}</b><span>${t('last_visit')}</span><b>${escapeHtml(p.date_last||'—')}</b></div><div class="route-popup-breakdown"><strong>${t('aircraft_breakdown')}</strong>${models}</div><div class="route-popup-breakdown"><strong>${t('top_connections')}</strong>${conns}</div>${regs?`<div class="route-popup-breakdown"><strong>${t('registration_breakdown')}</strong>${regs}</div>`:''}</div>`;
}

function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmt(n,d=0){return Number(n||0).toLocaleString(currentLocale(),{maximumFractionDigits:d,minimumFractionDigits:d});}

function restoreFocusStyles(){
  for(const r of routeRecords){r.line.setStyle({opacity:r.baseOpacity,weight:r.baseWeight});r.casing.setStyle({opacity:r.baseCasingOpacity,weight:r.baseCasingWeight});}
  for(const p of pointRecords){p.layer.setStyle({opacity:1,fillOpacity:.92,weight:1.8,radius:p.baseRadius});}
}
function focusRoute(key){
  focusMode={type:'route',key}; const selected=routeRecords.find(r=>r.key===key); if(!selected)return;
  const codes=new Set([selected.origin,selected.destination]);
  for(const r of routeRecords){const active=r.key===key;r.line.setStyle({opacity:active?1:.045,weight:active?r.baseWeight+1.8:r.baseWeight});r.casing.setStyle({opacity:active?.88:.035,weight:active?r.baseCasingWeight+1.4:r.baseCasingWeight});}
  for(const p of pointRecords){const active=codes.has(p.code);p.layer.setStyle({opacity:active?1:.12,fillOpacity:active?1:.10,weight:active?3:1,radius:active?p.baseRadius+2:p.baseRadius});}
}
function focusAirport(code){
  focusMode={type:'airport',code}; const connected=new Set([code]);
  for(const r of routeRecords){if(r.origin===code||r.destination===code){connected.add(r.origin);connected.add(r.destination);}}
  for(const r of routeRecords){const active=r.origin===code||r.destination===code;r.line.setStyle({opacity:active?Math.max(.76,r.baseOpacity):.035,weight:active?r.baseWeight+1.2:r.baseWeight});r.casing.setStyle({opacity:active?.78:.025,weight:active?r.baseCasingWeight+1:r.baseCasingWeight});}
  for(const p of pointRecords){const active=connected.has(p.code);const selected=p.code===code;p.layer.setStyle({opacity:active?1:.10,fillOpacity:active?1:.08,weight:selected?3.4:active?2:1,radius:selected?p.baseRadius+3:active?p.baseRadius+1:p.baseRadius});}
}
function clearFocus(closePopup=true){focusMode=null;restoreFocusStyles();if(closePopup)map.closePopup();}
map.on('click',()=>{if(focusMode)clearFocus(true);});

function draw(data){
  clearLayers(); lastData=data; const s=data.summary||{};
  document.getElementById('lb-entries').textContent=fmt(s.entries);document.getElementById('lb-hours').textContent=fmt(s.hours,1);document.getElementById('lb-airports').textContent=fmt(s.unique_airports);document.getElementById('lb-routes').textContent=fmt(s.unique_routes);document.getElementById('lb-range').textContent=s.date_start?`${s.date_start} → ${s.date_end||s.date_start}`:'';
  const coverage=document.getElementById('lb-coverage');if(coverage){let text=s.entries?t('map_coverage',{mapped:s.mapped_entries||0,total:s.entries||0,unmapped:s.unmapped_entries||0}):'';if(s.suspicious_entries)text+=` · ${t('suspicious_hidden',{count:s.suspicious_entries})}`;coverage.textContent=text;}document.getElementById('logbook-empty').style.display=s.entries?'none':'block';
  const connections=mergedConnections(data.routes||[]).sort((a,b)=>a.count-b.count);const maxRouteCount=Math.max(1,...connections.map(r=>Number(r.count||1)));
  for(const r of connections){const visual=routeVisual(r.count,maxRouteCount);addRepeatedRoute(r,greatCirclePoints([r.origin_lat,r.origin_lon],[r.destination_lat,r.destination_lon]),{...visual,color:cssVar('--logbook-route','#0071d9')},`${airportDisplay(r.origin,r.origin_city,r.origin_name)} ↔ ${airportDisplay(r.destination,r.destination_city,r.destination_name)} · ${t('flights_count',{count:r.count})} · ${t('hours_count',{hours:fmt(r.hours,1)})}`,routeDetailsHtml(r));}
  const bounds=[];
  for(const p of data.airports||[]){const radius=Math.min(12,3+Math.log2((p.visits||1)+1)*1.25);for(const offset of[-360,0,360]){const c=L.circleMarker([p.lat,p.lon+offset],{renderer:pathRenderer,radius,weight:1.8,color:cssVar('--logbook-airport-stroke','#07131f'),fillColor:cssVar('--logbook-airport-fill','#35b9f2'),fillOpacity:.92,className:'logbook-airport-marker'}).addTo(map);c.bindTooltip(`${airportDisplay(p.code,p.city,p.name)} · ${t('visits_count',{count:p.visits})}`,{sticky:true});c.bindPopup(airportDetailsHtml(p),{maxWidth:360});c.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);focusAirport(p.code);});pointLayers.push(c);pointRecords.push({code:p.code,layer:c,baseRadius:radius});}bounds.push([p.lat,p.lon]);}
  if(!didFit&&bounds.length){map.fitBounds(bounds,{padding:[45,45],maxZoom:4});didFit=true;} renderInsights(data);
}
function renderInsights(data){const models=document.getElementById('lb-top-models'),aps=document.getElementById('lb-top-airports'),routes=document.getElementById('lb-top-routes');models.innerHTML=(data.top_models||[]).slice(0,6).map(x=>`<div><strong>${x.model}</strong><span>${t('flights_count',{count:x.flights})} · ${t('mapped_count',{count:x.mapped||0})} · ${t('hours_count',{hours:fmt(x.hours,1)})}</span></div>`).join('');aps.innerHTML=(data.top_airports||[]).slice(0,6).map(x=>`<div><strong>${x.city||x.code} ${x.code}</strong><span>${t('visits_count',{count:x.visits})}</span></div>`).join('');routes.innerHTML=(data.top_routes||[]).slice(0,6).map(x=>`<div><strong>${airportDisplay(x.origin,x.origin_city)} → ${airportDisplay(x.destination,x.destination_city)}</strong><span>${t('flights_count',{count:x.flights})}</span></div>`).join('');}

function selectedModels(){return [...document.querySelectorAll('#logbook-model-options input[type="checkbox"]:checked')].map(x=>x.value);}
function updateModelSummary(){const vals=selectedModels();const el=document.getElementById('logbook-model-summary');el.textContent=!vals.length?t('all_aircraft'):vals.length<=2?vals.join(' + '):t('aircraft_types_selected',{count:vals.length});}
function setModels(predicate){for(const cb of document.querySelectorAll('#logbook-model-options input[type="checkbox"]'))cb.checked=predicate?predicate(cb.value):false;updateModelSummary();}
async function loadOptions(){
  const res=await fetch('/api/logbook/options');const data=await res.json();const box=document.getElementById('logbook-model-options'),i=document.getElementById('logbook-ident');
  for(const x of data.models||[]){const label=document.createElement('label');label.className='multi-picker-option';const cb=document.createElement('input');cb.type='checkbox';cb.value=x;cb.addEventListener('change',updateModelSummary);const span=document.createElement('span');span.textContent=x;label.append(cb,span);box.appendChild(label);}
  for(const x of data.idents||[]){const o=document.createElement('option');o.value=x;o.textContent=x;i.appendChild(o);}document.getElementById('logbook-start').min=data.date_start||'';document.getElementById('logbook-end').max=data.date_end||'';
  document.getElementById('logbook-model-all').onclick=()=>setModels(null);
  updateModelSummary();
}
async function refresh(){const q=new URLSearchParams();for(const v of selectedModels())q.append('aircraft_model',v);const vals={aircraft_ident:'logbook-ident',start_date:'logbook-start',end_date:'logbook-end'};for(const[k,id]of Object.entries(vals)){const v=document.getElementById(id).value;if(v)q.set(k,v);}const res=await fetch(`/api/logbook/map?${q}`);draw(await res.json());}
document.getElementById('logbook-apply').onclick=()=>{didFit=false;refresh();};
document.getElementById('logbook-reset').onclick=()=>{setModels(null);for(const id of['logbook-ident','logbook-start','logbook-end'])document.getElementById(id).value='';didFit=false;refresh();};
function updateLangBtn(){const b=document.getElementById('language-toggle');b.textContent=(window.TrackerI18n.language||'en').toUpperCase();b.title=t('language_title');updateModelSummary();}
updateLangBtn();document.getElementById('language-toggle').onclick=()=>toggleLanguage();
document.addEventListener('tracker-language-change',ev=>{updateLangBtn();applyMapLanguage(ev.detail?.language);const b=document.getElementById('map-only-toggle');if(b)b.title=document.body.classList.contains('map-only')?t('show_ui'):t('map_only');if(lastData)draw(lastData);});
document.addEventListener('tracker-theme-change',ev=>{applyMapTheme(ev.detail?.theme);if(lastData)draw(lastData);});
loadOptions().then(refresh);
