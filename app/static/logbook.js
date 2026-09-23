const {t,toggle:toggleLanguage,locale:currentLocale}=window.TrackerI18n;
const map=L.map('logbook-map',{zoomControl:false,worldCopyJump:false,minZoom:1,maxBounds:[[-85.0511,-1000000],[85.0511,1000000]],maxBoundsViscosity:1}).setView([25,0],2);
L.control.zoom({position:'bottomright'}).addTo(map);
function mapStyleForTheme(theme=window.TrackerTheme?.theme||'light'){return `https://tiles.openfreemap.org/styles/${theme==='light'?'bright':'liberty'}`;}
const vectorBase=L.maplibreGL({style:mapStyleForTheme(),interactive:false}).addTo(map);
const glMap=vectorBase.getMaplibreMap();
const originalLabelFields=new Map(); let mapLabelLanguage=window.TrackerI18n.language||'en';
function localizedNameField(original,lang){const arr=[['get',`name:${lang}`],['get',`name_${lang}`]];if(lang!=='en')arr.push(['get','name:en'],['get','name_en']);arr.push(['get','name:latin'],original);return ['coalesce',...arr];}
function applyMapLanguage(lang=mapLabelLanguage){mapLabelLanguage=['en','fr','ru'].includes(lang)?lang:'en';try{for(const layer of glMap.getStyle()?.layers||[]){if(layer.type!=='symbol')continue;const cur=layer.layout?.['text-field'];if(!cur)continue;if(!originalLabelFields.has(layer.id))originalLabelFields.set(layer.id,cur);const original=originalLabelFields.get(layer.id);if(!JSON.stringify(original).includes('name'))continue;try{glMap.setLayoutProperty(layer.id,'text-field',localizedNameField(original,mapLabelLanguage));}catch(_){}}}catch(_){}}
glMap.on('styledata',()=>applyMapLanguage());glMap.on('load',()=>applyMapLanguage());
function applyMapTheme(theme=window.TrackerTheme?.theme||'light'){originalLabelFields.clear();try{glMap.setStyle(mapStyleForTheme(theme));}catch(_){}}
function cssVar(name,fallback=''){return getComputedStyle(document.documentElement).getPropertyValue(name).trim()||fallback;}

let routeLayers=[],pointLayers=[],lastData=null,didFit=false;
function clearLayers(){[...routeLayers,...pointLayers].forEach(l=>map.removeLayer(l));routeLayers=[];pointLayers=[];}
function greatCirclePoints(a,b,steps=72){const toR=x=>x*Math.PI/180,toD=x=>x*180/Math.PI;const lat1=toR(a[0]),lon1=toR(a[1]),lat2=toR(b[0]),lon2=toR(b[1]);const v1=[Math.cos(lat1)*Math.cos(lon1),Math.cos(lat1)*Math.sin(lon1),Math.sin(lat1)],v2=[Math.cos(lat2)*Math.cos(lon2),Math.cos(lat2)*Math.sin(lon2),Math.sin(lat2)];let dot=Math.max(-1,Math.min(1,v1[0]*v2[0]+v1[1]*v2[1]+v1[2]*v2[2]));const omega=Math.acos(dot),sin=Math.sin(omega);if(omega<1e-8)return[a,b];const out=[];for(let i=0;i<=steps;i++){const f=i/steps,s1=Math.sin((1-f)*omega)/sin,s2=Math.sin(f*omega)/sin,x=s1*v1[0]+s2*v2[0],y=s1*v1[1]+s2*v2[1],z=s1*v1[2]+s2*v2[2];out.push([toD(Math.atan2(z,Math.hypot(x,y))),toD(Math.atan2(y,x))]);}return out;}
function unwrap(points){if(!points.length)return[];const out=[[points[0][0],points[0][1]]];let prev=points[0][1];for(const [lat,raw] of points.slice(1)){let lon=raw;while(lon-prev>180)lon-=360;while(lon-prev<-180)lon+=360;out.push([lat,lon]);prev=lon;}return out;}
function addRepeatedPolyline(points,opts,tooltip,popupHtml){
  const base=unwrap(points);
  const lineOpts={...opts};
  const casingExtra=Number(lineOpts.casingExtra??1.2);
  const casingOpacity=Number(lineOpts.casingOpacity??Math.min(.75,(lineOpts.opacity??.7)+.16));
  delete lineOpts.casingExtra; delete lineOpts.casingOpacity;
  for(const offset of[-720,-360,0,360,720]){
    const shifted=base.map(([lat,lon])=>[lat,lon+offset]);
    const casing=L.polyline(shifted,{...lineOpts,color:cssVar('--logbook-casing','#041016'),opacity:casingOpacity,weight:(lineOpts.weight||1)+casingExtra,interactive:false,lineCap:'round',lineJoin:'round'}).addTo(map);
    routeLayers.push(casing);
    const layer=L.polyline(shifted,{...lineOpts,lineCap:'round',lineJoin:'round'}).addTo(map);
    if(tooltip)layer.bindTooltip(tooltip);if(popupHtml)layer.bindPopup(popupHtml,{maxWidth:340});routeLayers.push(layer);
  }
}
function mergeBreakdown(target,items,keyName){
  for(const item of items||[]){
    const key=item?.[keyName]; if(!key)continue;
    if(!target.has(key))target.set(key,{name:key,flights:0,hours:0});
    const row=target.get(key); row.flights+=Number(item.flights||0); row.hours+=Number(item.hours||0);
  }
}
function mergedConnections(routes){
  const grouped=new Map();
  for(const r of routes||[]){
    if(!r?.origin||!r?.destination)continue;
    const forward=r.origin.localeCompare(r.destination)<=0;
    const a=forward?r.origin:r.destination,b=forward?r.destination:r.origin;
    const key=`${a}|${b}`;
    if(!grouped.has(key)){
      grouped.set(key,{origin:a,destination:b,origin_lat:forward?r.origin_lat:r.destination_lat,origin_lon:forward?r.origin_lon:r.destination_lon,destination_lat:forward?r.destination_lat:r.origin_lat,destination_lon:forward?r.destination_lon:r.origin_lon,count:0,hours:0,duration_samples:0,duration_estimated:false,modelMap:new Map(),identMap:new Map(),directions:[]});
    }
    const g=grouped.get(key);
    g.count+=Number(r.count||0);
    g.hours+=Number(r.hours||0);
    g.duration_samples+=Number(r.duration_samples||0);
    g.duration_estimated ||= Boolean(r.duration_estimated);
    mergeBreakdown(g.modelMap,r.aircraft_models,'model');
    mergeBreakdown(g.identMap,r.registrations,'ident');
    g.directions.push({origin:r.origin,destination:r.destination,count:Number(r.count||0)});
  }
  return [...grouped.values()].map(g=>({
    ...g,
    average_hours:g.duration_samples?g.hours/g.duration_samples:null,
    aircraft_models:[...g.modelMap.values()].sort((a,b)=>b.flights-a.flights),
    registrations:[...g.identMap.values()].sort((a,b)=>b.flights-a.flights),
  }));
}
function routeVisual(count,maxCount){
  const max=Math.max(1,Number(maxCount||1));
  const raw=Math.log1p(Math.max(1,Number(count||1)))/Math.log1p(max);
  // Compress low-frequency routes aggressively so the lifetime map does not
  // become a wall of ink. Frequently flown connections still stand out.
  const strength=Math.pow(raw,1.55);
  return {weight:.35+3.85*strength,opacity:.10+.78*strength,casingExtra:.55+strength*.55,casingOpacity:.16+.42*strength};
}
function routeDetailsHtml(r){
  const avg=r.average_hours==null?'—':`${fmt(r.average_hours,2)} h`;
  const modelRows=(r.aircraft_models||[]).slice(0,8).map(x=>`<div><span>${escapeHtml(x.name)}</span><b>${fmt(x.flights)}×</b></div>`).join('')||`<div><span>—</span></div>`;
  const identRows=(r.registrations||[]).slice(0,6).map(x=>`<div><span>${escapeHtml(x.name)}</span><b>${fmt(x.flights)}×</b></div>`).join('');
  const dirRows=(r.directions||[]).filter(x=>x.count).map(x=>`<div><span>${escapeHtml(x.origin)} → ${escapeHtml(x.destination)}</span><b>${fmt(x.count)}×</b></div>`).join('');
  return `<div class="route-popup"><h3>${escapeHtml(r.origin)} ↔ ${escapeHtml(r.destination)}</h3><div class="route-popup-grid"><span>${t('times_flown')}</span><b>${fmt(r.count)}</b><span>${t('total_route_time')}</span><b>${fmt(r.hours,1)} h</b><span>${t('average_flight_time')}</span><b>${avg}</b></div>${dirRows?`<div class="route-popup-breakdown"><strong>${t('direction_breakdown')}</strong>${dirRows}</div>`:''}<div class="route-popup-breakdown"><strong>${t('aircraft_breakdown')}</strong>${modelRows}</div>${identRows?`<div class="route-popup-breakdown"><strong>${t('registration_breakdown')}</strong>${identRows}</div>`:''}${r.duration_estimated?`<div class="route-popup-note">${t('average_time_estimated')}</div>`:''}</div>`;
}
function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmt(n,d=0){return Number(n||0).toLocaleString(currentLocale(),{maximumFractionDigits:d,minimumFractionDigits:d});}
function draw(data){clearLayers();lastData=data;const s=data.summary||{};document.getElementById('lb-entries').textContent=fmt(s.entries);document.getElementById('lb-hours').textContent=fmt(s.hours,1);document.getElementById('lb-airports').textContent=fmt(s.unique_airports);document.getElementById('lb-routes').textContent=fmt(s.unique_routes);document.getElementById('lb-range').textContent=s.date_start?`${s.date_start} → ${s.date_end||s.date_start}`:'';const coverage=document.getElementById('lb-coverage');if(coverage)coverage.textContent=s.entries?t('map_coverage',{mapped:s.mapped_entries||0,total:s.entries||0,unmapped:s.unmapped_entries||0}):'';document.getElementById('logbook-empty').style.display=s.entries?'none':'block';
  const connections=mergedConnections(data.routes||[]).sort((a,b)=>a.count-b.count);
  const maxRouteCount=Math.max(1,...connections.map(r=>Number(r.count||1)));
  for(const r of connections){const visual=routeVisual(r.count,maxRouteCount);addRepeatedPolyline(greatCirclePoints([r.origin_lat,r.origin_lon],[r.destination_lat,r.destination_lon]),{...visual,color:cssVar('--logbook-route','#0071d9')},`${r.origin} ↔ ${r.destination} · ${t('flights_count',{count:r.count})} · ${t('hours_count',{hours:fmt(r.hours,1)})}`,routeDetailsHtml(r));}
  const bounds=[];for(const p of data.airports||[]){const radius=Math.min(12,3+Math.log2((p.visits||1)+1)*1.25);for(const offset of[-360,0,360]){const c=L.circleMarker([p.lat,p.lon+offset],{radius,weight:1.8,color:cssVar('--logbook-airport-stroke','#07131f'),fillColor:cssVar('--logbook-airport-fill','#35b9f2'),fillOpacity:.92,className:'logbook-airport-marker'}).addTo(map);c.bindTooltip(`${p.city||p.name||p.code} ${p.code} · ${t('visits_count',{count:p.visits})}`);pointLayers.push(c);}bounds.push([p.lat,p.lon]);}
  if(!didFit&&bounds.length){map.fitBounds(bounds,{padding:[45,45],maxZoom:4});didFit=true;}
  renderInsights(data);
}
function renderInsights(data){const models=document.getElementById('lb-top-models'),aps=document.getElementById('lb-top-airports'),routes=document.getElementById('lb-top-routes');models.innerHTML=(data.top_models||[]).slice(0,6).map(x=>`<div><strong>${x.model}</strong><span>${t('flights_count',{count:x.flights})} · ${t('mapped_count',{count:x.mapped||0})} · ${t('hours_count',{hours:fmt(x.hours,1)})}</span></div>`).join('');aps.innerHTML=(data.top_airports||[]).slice(0,6).map(x=>`<div><strong>${x.city||x.code} ${x.code}</strong><span>${t('visits_count',{count:x.visits})}</span></div>`).join('');routes.innerHTML=(data.top_routes||[]).slice(0,6).map(x=>`<div><strong>${x.origin} → ${x.destination}</strong><span>${t('flights_count',{count:x.flights})}</span></div>`).join('');}
async function loadOptions(){const res=await fetch('/api/logbook/options');const data=await res.json();const m=document.getElementById('logbook-model'),i=document.getElementById('logbook-ident');for(const x of data.models||[]){const o=document.createElement('option');o.value=x;o.textContent=x;m.appendChild(o);}for(const x of data.idents||[]){const o=document.createElement('option');o.value=x;o.textContent=x;i.appendChild(o);}document.getElementById('logbook-start').min=data.date_start||'';document.getElementById('logbook-end').max=data.date_end||'';}
async function refresh(){const q=new URLSearchParams();const vals={aircraft_model:'logbook-model',aircraft_ident:'logbook-ident',start_date:'logbook-start',end_date:'logbook-end'};for(const[k,id]of Object.entries(vals)){const v=document.getElementById(id).value;if(v)q.set(k,v);}const res=await fetch(`/api/logbook/map?${q}`);draw(await res.json());}
document.getElementById('logbook-apply').onclick=()=>{didFit=false;refresh();};document.getElementById('logbook-reset').onclick=()=>{for(const id of['logbook-model','logbook-ident','logbook-start','logbook-end'])document.getElementById(id).value='';didFit=false;refresh();};
function updateLangBtn(){const b=document.getElementById('language-toggle');b.textContent=(window.TrackerI18n.language||'en').toUpperCase();b.title=t('language_title');}
updateLangBtn();document.getElementById('language-toggle').onclick=()=>toggleLanguage();
document.addEventListener('tracker-language-change',ev=>{updateLangBtn();applyMapLanguage(ev.detail?.language);if(lastData)draw(lastData);});
document.addEventListener('tracker-theme-change',ev=>{applyMapTheme(ev.detail?.theme);if(lastData)draw(lastData);});
loadOptions().then(refresh);
