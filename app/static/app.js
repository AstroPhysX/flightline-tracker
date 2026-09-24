const { t, toggle: toggleLanguage, locale: currentLocale } = window.TrackerI18n;
const admin = window.TrackerAdmin;

async function adminFetch(url, options={}) {
  if (!(await admin.ensure())) return null;
  const res = await fetch(url, options);
  if (res.status === 401) admin.handleUnauthorized(res);
  return res;
}
const map = L.map('map', {
  zoomControl: false,
  worldCopyJump: false,
  minZoom: 1,
  maxBounds: [[-85.0511, -1000000], [85.0511, 1000000]],
  maxBoundsViscosity: 1,
}).setView([28, 0], 2);
L.control.zoom({ position: 'bottomright' }).addTo(map);

const WeatherControl = L.Control.extend({
  options:{position:'bottomright'},
  onAdd(){
    const wrap=L.DomUtil.create('div','leaflet-bar weather-map-control');
    const btn=L.DomUtil.create('button','weather-map-button',wrap);
    btn.type='button'; btn.id='weather-toggle'; btn.title=t('weather'); btn.textContent='☁';
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.on(btn,'click',()=>setWeatherEnabled(!weatherEnabled));
    return wrap;
  }
});
new WeatherControl().addTo(map);

const MapOnlyControl = L.Control.extend({
  options:{position:'bottomright'},
  onAdd(){
    const wrap=L.DomUtil.create('div','leaflet-bar map-only-control');
    const btn=L.DomUtil.create('button','map-only-button',wrap);
    btn.type='button'; btn.id='map-only-toggle'; btn.textContent='▣'; btn.title=t('map_only');
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.on(btn,'click',()=>{
      document.body.classList.toggle('map-only');
      const hidden=document.body.classList.contains('map-only');
      btn.classList.toggle('active',hidden);
      btn.title=hidden?t('show_ui'):t('map_only');
      setTimeout(()=>map.invalidateSize(),80);
    });
    return wrap;
  }
});
new MapOnlyControl().addTo(map);
map.createPane('nightPane');
map.getPane('nightPane').style.zIndex = 330;
map.getPane('nightPane').style.pointerEvents = 'none';
map.createPane('routeIndicatorPane');
map.getPane('routeIndicatorPane').style.zIndex = 455;
map.getPane('routeIndicatorPane').style.pointerEvents = 'none';
map.createPane('aircraftPane');
map.getPane('aircraftPane').style.zIndex = 470;
map.createPane('weatherPane');
map.getPane('weatherPane').style.zIndex = 325;
map.getPane('weatherPane').style.pointerEvents = 'none';

// IMPORTANT: MapLibre GL JS v5 had a known incompatibility with the 0.0.22
// Leaflet adapter used by v4 of this tracker. Pinning the GL layer to v4 keeps
// Leaflet and the vector basemap synchronized while retaining language-switchable vector labels.
function mapStyleForTheme(theme = window.TrackerTheme?.theme || 'light') {
  // v17: Liberty is the normal/light map palette the user preferred. Fiord is
  // the darker map: clearly darker than Liberty without going back to the
  // almost-black Dark style that hid the day/night terminator.
  return `https://tiles.openfreemap.org/styles/${theme === 'light' ? 'liberty' : 'fiord'}`;
}
const vectorBase = L.maplibreGL({
  style: mapStyleForTheme(),
  interactive: false,
}).addTo(map);

const glMap = vectorBase.getMaplibreMap();
const originalLabelFields = new Map();
const appliedLabelLanguages = new Map();
let mapLabelLanguage = window.TrackerI18n.language || 'en';
function localizedNameField(original, lang) {
  const localized = [
    ['get', `name:${lang}`],
    ['get', `name_${lang}`],
  ];
  if (lang !== 'en') localized.push(['get', 'name:en'], ['get', 'name_en']);
  localized.push(['get', 'name:latin'], original);
  return ['coalesce', ...localized];
}
function applyMapLanguage(lang = mapLabelLanguage) {
  mapLabelLanguage = ['en','fr','ru'].includes(lang) ? lang : 'en';
  try {
    const style = glMap.getStyle();
    for (const layer of style?.layers || []) {
      if (layer.type !== 'symbol') continue;
      const current = layer.layout?.['text-field'];
      if (!current) continue;
      if (!originalLabelFields.has(layer.id)) originalLabelFields.set(layer.id, current);
      const original = originalLabelFields.get(layer.id);
      if (appliedLabelLanguages.get(layer.id) === mapLabelLanguage) continue;
      const serialized = JSON.stringify(original);
      if (!serialized.includes('name')) continue;
      try { appliedLabelLanguages.set(layer.id, mapLabelLanguage); glMap.setLayoutProperty(layer.id, 'text-field', localizedNameField(original, mapLabelLanguage)); } catch (_) { appliedLabelLanguages.delete(layer.id); }
    }
  } catch (_) {}
}
glMap.on('styledata', () => applyMapLanguage(mapLabelLanguage));
glMap.on('load', () => applyMapLanguage(mapLabelLanguage));
function applyMapTheme(theme = window.TrackerTheme?.theme || 'light') {
  originalLabelFields.clear();
  appliedLabelLanguages.clear();
  try { glMap.setStyle(mapStyleForTheme(theme)); } catch (_) {}
}

let routeLayers = [];
let stopLayers = [];
let aircraftLayers = [];
let routeIndicatorLayers = [];
let nightLayers = [];
let dashboard = null;
let didInitialFit = false;
let currentMapView = new URLSearchParams(location.search).get('view') === 'awarded' ? 'awarded' : 'current';
let weatherLayer = null;
let weatherEnabled = false;
let weatherFrameGenerated = null;
let replayWeatherLayer = null;
let replayWeatherSnapshots = [];
let replayRainViewerPayload = null;
let replayWeatherKey = null;
let replayRestoreLiveWeather = false;

function cssVar(name, fallback='') {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
function routeColors() {
  return {
    future: cssVar('--future', '#2f9dff'),
    deadhead: cssVar('--deadhead', '#ff9a1f'),
    current: cssVar('--current', '#f2ff2e'),
    flown: cssVar('--flown', '#c5ccd5'),
  };
}

const warning = document.createElement('div');
warning.className = 'map-warning';
document.body.appendChild(warning);
const weatherCredit=document.createElement('div');
weatherCredit.className='weather-credit';
weatherCredit.innerHTML=`${t('radar_credit').replace('RainViewer','')}<a href="https://www.rainviewer.com/" target="_blank" rel="noopener">RainViewer</a>`;
document.body.appendChild(weatherCredit);

function showWarning(text) {
  warning.textContent = text || '';
  warning.style.display = text ? 'block' : 'none';
}

function clearMapLayers() {
  [...routeLayers, ...stopLayers, ...aircraftLayers, ...routeIndicatorLayers].forEach(x => map.removeLayer(x));
  routeLayers = [];
  stopLayers = [];
  aircraftLayers = [];
  routeIndicatorLayers = [];
}

function greatCirclePoints(a, b, steps=96) {
  const toRad = d => d * Math.PI / 180, toDeg = r => r * 180 / Math.PI;
  const lat1=toRad(a[0]), lon1=toRad(a[1]), lat2=toRad(b[0]), lon2=toRad(b[1]);
  const d = 2*Math.asin(Math.sqrt(Math.sin((lat2-lat1)/2)**2 + Math.cos(lat1)*Math.cos(lat2)*Math.sin((lon2-lon1)/2)**2));
  if (d === 0) return [a,b];
  const pts=[];
  for (let i=0;i<=steps;i++) {
    const f=i/steps, A=Math.sin((1-f)*d)/Math.sin(d), B=Math.sin(f*d)/Math.sin(d);
    const x=A*Math.cos(lat1)*Math.cos(lon1)+B*Math.cos(lat2)*Math.cos(lon2);
    const y=A*Math.cos(lat1)*Math.sin(lon1)+B*Math.cos(lat2)*Math.sin(lon2);
    const z=A*Math.sin(lat1)+B*Math.sin(lat2);
    pts.push([toDeg(Math.atan2(z, Math.sqrt(x*x+y*y))), toDeg(Math.atan2(y,x))]);
  }
  return pts;
}

function unwrapLongitudes(points) {
  if (!points?.length) return [];
  const out=[[points[0][0], points[0][1]]];
  for (let i=1;i<points.length;i++) {
    let lon=points[i][1];
    const prev=out[out.length-1][1];
    while (lon-prev > 180) lon -= 360;
    while (lon-prev < -180) lon += 360;
    out.push([points[i][0], lon]);
  }
  return out;
}

function shiftLonNear(lon, reference) {
  let value=lon;
  while (value-reference > 180) value-=360;
  while (value-reference < -180) value+=360;
  return value;
}

function addRepeatedPolyline(points, style, tooltip) {
  const unwrapped=unwrapLongitudes(points);
  for (const offset of [-720,-360,0,360,720]) {
    const shifted=unwrapped.map(([lat,lon])=>[lat,lon+offset]);
    // A dark casing underneath every route keeps the colored centerline visible
    // over bright land, weather radar, and the night overlay without making the
    // route itself opaque or excessively thick.
    const casing=L.polyline(shifted,{
      ...style, color:cssVar('--route-casing','#03070b'), opacity:.88, weight:(style.weight||4)+2,
      interactive:false, lineCap:'round', lineJoin:'round'
    }).addTo(map);
    routeLayers.push(casing);
    const line=L.polyline(shifted,{...style,lineCap:'round',lineJoin:'round'}).addTo(map);
    if (tooltip) line.bindTooltip(tooltip);
    routeLayers.push(line);
  }
}

function routeStyle(f) {
  const colors=routeColors();
  // Deadheads always retain a dashed identity, including after completion.
  // Completed deadheads turn grey like other flown legs but never become solid.
  if (f.deadhead) {
    const completed=f.status==='completed' || f.status==='past';
    const current=f.status==='current';
    return {
      color: completed ? colors.flown : (current ? colors.current : colors.deadhead),
      opacity: current ? 1 : .96,
      weight: current ? 5.6 : completed ? 3.6 : 4.5,
      dashArray: '10 8',
    };
  }
  if (f.status === 'completed' || f.status === 'past') return { color: colors.flown, opacity: .94, weight: 3.6 };
  if (f.status === 'current') return { color: colors.current, opacity: 1, weight: 6.5 };
  return { color: colors.future, opacity: .94, weight: 4.2 };
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function stopLabel(airport, code) {
  const city=airport?.city || airport?.name || code;
  return `${escapeHtml(city)} <b>${escapeHtml(code)}</b>`;
}

function airportDisplay(airport, code) {
  const place=airport?.city || airport?.name || '';
  return place && String(place).toUpperCase()!==String(code).toUpperCase() ? `${code} (${place})` : code;
}

function flightAirportDisplay(f, side) {
  const code=side==='origin'?f?.origin:f?.destination;
  const airport=side==='origin'?f?.origin_airport:f?.destination_airport;
  return airportDisplay(airport, code||'—');
}

function flightRouteText(f) {
  return `${flightAirportDisplay(f,'origin')} → ${flightAirportDisplay(f,'destination')}`;
}

function formatMinutesDuration(minutes) {
  const n=Number(minutes);
  if(!Number.isFinite(n) || n<0) return '—';
  const h=Math.floor(n/60), m=Math.round(n%60);
  return h ? `${h}h ${String(m).padStart(2,'0')}m` : `${m}m`;
}

function formatHoursDuration(hours) {
  const n=Number(hours);
  return Number.isFinite(n) && n>0 ? formatMinutesDuration(Math.round(n*60)) : '—';
}

function addStopMarker(airport, code, isRest) {
  if (!airport) return;
  const html=`<div class="stop-marker ${isRest?'rest-stop':''}"><span class="stop-symbol">${isRest?'★':'●'}</span><span class="stop-label">${stopLabel(airport, code)}</span></div>`;
  const icon=L.divIcon({className:'stop-div-icon',html,iconSize:[190,34],iconAnchor:[9,17]});
  for (const offset of [-720,-360,0,360,720]) {
    const marker=L.marker([airport.lat,airport.lon+offset],{icon,interactive:false}).addTo(map);
    stopLayers.push(marker);
  }
}

function pointAtFraction(points, fraction) {
  const pts=unwrapLongitudes(points);
  if (!pts.length) return null;
  if (pts.length===1) return {point:pts[0], before:pts[0], after:pts[0]};
  const f=Math.max(0,Math.min(1,fraction));
  const target=(pts.length-1)*f;
  const i=Math.min(pts.length-2,Math.floor(target));
  const t=target-i;
  const a=pts[i], b=pts[i+1];
  return {
    point:[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t],
    before:a,
    after:b,
  };
}

function bearingDegrees(a,b) {
  const r=Math.PI/180;
  const lat1=a[0]*r, lat2=b[0]*r, dLon=(b[1]-a[1])*r;
  const y=Math.sin(dLon)*Math.cos(lat2);
  const x=Math.cos(lat1)*Math.sin(lat2)-Math.sin(lat1)*Math.cos(lat2)*Math.cos(dLon);
  return (Math.atan2(y,x)*180/Math.PI+360)%360;
}

function providerProjectedIso(scheduledIso, delaySeconds, estimatedIso) {
  const seconds=Number(delaySeconds);
  if (scheduledIso && Number.isFinite(seconds)) {
    return new Date(new Date(scheduledIso).getTime()+seconds*1000).toISOString();
  }
  return estimatedIso || scheduledIso || null;
}

function bestDepartureIso(f, {actual=true}={}) {
  if (!f) return null;
  if (actual && f.actual_departure_utc) return f.actual_departure_utc;
  return providerProjectedIso(
    f.provider_scheduled_departure_utc,
    f.provider_departure_delay_seconds,
    f.estimated_departure_utc,
  ) || f.scheduled_departure_utc;
}

function bestArrivalIso(f, {actual=true}={}) {
  if (!f) return null;
  if (actual && f.actual_arrival_utc) return f.actual_arrival_utc;
  return providerProjectedIso(
    f.provider_scheduled_arrival_utc,
    f.provider_arrival_delay_seconds,
    f.estimated_arrival_utc,
  ) || f.scheduled_arrival_utc;
}

function flightAwareUrl(f) {
  return `https://www.flightaware.com/live/flight/${encodeURIComponent(String(f?.flight_number||'').toUpperCase())}`;
}

function fr24FlightIdent(f) {
  const ident=String(f?.flight_number||'').toUpperCase().replace(/\s+/g,'');
  const ups=ident.match(/^UPS(\d+)$/);
  return ups ? `5X${ups[1]}` : ident;
}

function flightradar24Url(f) {
  return `https://www.flightradar24.com/data/flights/${encodeURIComponent(fr24FlightIdent(f).toLowerCase())}`;
}

function flightExternalLinksHtml(f, label=null) {
  const text=escapeHtml(label || f?.flight_number || '—');
  return `<span class="flight-external-links"><a class="flight-number-link" href="${flightAwareUrl(f)}" target="_blank" rel="noopener" title="FlightAware">${text}</a><a class="flight-site-mini" href="${flightradar24Url(f)}" target="_blank" rel="noopener" title="Flightradar24">FR24</a></span>`;
}

function flightLegPopupHtml(f) {
  const statusKey=f.status==='current'?'status_airborne':`status_${f.status}`;
  const status=t(statusKey) || f.status;
  const dep=fmtDallas(bestDepartureIso(f));
  const arr=fmtDallas(bestArrivalIso(f));
  const replay=(f.track||[]).length>=2 ? `<button type="button" class="replay-flight-button" onclick="window.startFlightReplay(${Number(f.id)})">${escapeHtml(t('replay_flight'))}</button>` : '';
  const aircraft=[f.registration,f.aircraft_type].filter(Boolean).map(escapeHtml).join(' · ');
  const type=f.deadhead ? ` · ${escapeHtml(t('deadhead'))}` : '';
  const scheduled=f.scheduled_duration_minutes!=null?formatMinutesDuration(f.scheduled_duration_minutes):'—';
  const typical=f.historical_average_hours!=null?formatHoursDuration(f.historical_average_hours):'—';
  const typicalNote=f.historical_samples?` (${f.historical_samples}×${f.historical_duration_estimated?'*':''})`:'';
  return `<div class="leg-popup"><h3>${escapeHtml(t('leg'))} ${escapeHtml(f.sequence)} · ${flightExternalLinksHtml(f)}</h3><div class="leg-popup-route">${escapeHtml(flightRouteText(f))}${type}</div><div class="leg-popup-grid"><span>${escapeHtml(t('status'))}</span><b>${escapeHtml(status)}</b><span>${escapeHtml(t('takeoff_dallas'))}</span><b>${escapeHtml(dep)}</b><span>${escapeHtml(t('landing_dallas'))}</span><b>${escapeHtml(arr)}</b><span>${escapeHtml(t('delay'))}</span><b>${escapeHtml(fmtDelay(f))}</b><span>${escapeHtml(t('scheduled_flight_time'))}</span><b>${escapeHtml(scheduled)}</b><span>${escapeHtml(t('typical_flight_time'))}</span><b>${escapeHtml(typical+typicalNote)}</b>${aircraft?`<span>${escapeHtml(t('aircraft'))}</span><b>${aircraft}</b>`:''}</div><div class="leg-popup-actions"><a href="${flightAwareUrl(f)}" target="_blank" rel="noopener">FlightAware ↗</a><a href="${flightradar24Url(f)}" target="_blank" rel="noopener">Flightradar24 ↗</a>${replay}</div></div>`;
}


function addRouteIndicator(f, points, repeatIndex=0) {
  if (!points?.length) return;
  const fractions=[.38,.52,.66,.30,.74];
  const sample=pointAtFraction(points,fractions[repeatIndex % fractions.length]);
  if (!sample) return;
  const heading=bearingDegrees(sample.before,sample.after)-90;
  const html=`<div class="route-indicator" title="${escapeHtml(t('leg'))} ${f.sequence}: ${escapeHtml(f.flight_number)} ${escapeHtml(flightRouteText(f))}"><span class="route-sequence">${escapeHtml(f.sequence)}</span><span class="route-arrow" style="transform:rotate(${heading}deg)">➤</span></div>`;
  const icon=L.divIcon({className:'route-indicator-icon',html,iconSize:[52,28],iconAnchor:[26,14]});
  for (const offset of [-720,-360,0,360,720]) {
    const marker=L.marker([sample.point[0],sample.point[1]+offset],{icon,pane:'routeIndicatorPane',interactive:true,keyboard:true}).addTo(map);
    marker.bindPopup(flightLegPopupHtml(f),{maxWidth:390});
    routeIndicatorLayers.push(marker);
  }
}

function drawNightOverlay(now=new Date()) {
  nightLayers.forEach(x=>map.removeLayer(x));
  nightLayers=[];

  const start=new Date(Date.UTC(now.getUTCFullYear(),0,0));
  const day=(now-start)/86400000;
  const hour=now.getUTCHours()+now.getUTCMinutes()/60+now.getUTCSeconds()/3600;
  const gamma=2*Math.PI/365*(day-1+(hour-12)/24);
  const decl=0.006918-0.399912*Math.cos(gamma)+0.070257*Math.sin(gamma)-0.006758*Math.cos(2*gamma)+0.000907*Math.sin(2*gamma)-0.002697*Math.cos(3*gamma)+0.00148*Math.sin(3*gamma);
  const eqtime=229.18*(0.000075+0.001868*Math.cos(gamma)-0.032077*Math.sin(gamma)-0.014615*Math.cos(2*gamma)-0.040849*Math.sin(2*gamma));
  const subsolarLon=-15*(hour-12+eqtime/60);
  const pole=decl>=0 ? -90 : 90;
  const term=[];
  for (let lon=-180;lon<=180;lon+=2) {
    const H=(lon-subsolarLon)*Math.PI/180;
    let lat=Math.atan(-Math.cos(H)/Math.tan(decl || 1e-9))*180/Math.PI;
    lat=Math.max(-89.9,Math.min(89.9,lat));
    term.push([lat,lon]);
  }
  const poly=[[pole,-180],...term,[pole,180]];
  const theme=window.TrackerTheme?.theme || 'light';
  const nightFill=cssVar('--night-fill', theme==='dark' ? '#081a31' : '#000000');
  const nightOpacity=Number(cssVar('--night-opacity', theme==='dark' ? '.30' : '.20'));
  const edgeColor=cssVar('--night-edge', theme==='dark' ? '#91b6d7' : '#4a6075');
  const edgeOpacity=Number(cssVar('--night-edge-opacity', theme==='dark' ? '.42' : '.10'));
  for (const offset of [-720,-360,0,360,720]) {
    const shifted=poly.map(([lat,lon])=>[lat,lon+offset]);
    nightLayers.push(L.polygon(shifted,{pane:'nightPane',stroke:true,color:edgeColor,weight:.75,opacity:edgeOpacity,fillColor:nightFill,fillOpacity:nightOpacity,interactive:false}).addTo(map));
  }
}

function fitTrip(data) {
  const endpoints=[];
  let reference=null;
  for (const f of data.flights || []) {
    for (const ap of [f.origin_airport,f.destination_airport]) {
      if (!ap) continue;
      const lon=reference==null ? ap.lon : shiftLonNear(ap.lon,reference);
      endpoints.push([ap.lat,lon]);
      reference=lon;
    }
  }
  if (endpoints.length) {
    map.fitBounds(endpoints,{padding:[90,90],maxZoom:4});
    didInitialFit=true;
  }
}

function routePathForFlight(f) {
  if (!f.origin_airport || !f.destination_airport) return [];
  const a=[f.origin_airport.lat,f.origin_airport.lon];
  const b=[f.destination_airport.lat,f.destination_airport.lon];
  const track=(f.track || []).map(p=>[p.lat,p.lon]);
  if ((f.status==='current' || f.status==='completed') && track.length>=2) return track;
  return greatCirclePoints(a,b);
}

function drawDashboard(data) {
  dashboard=data;
  clearMapLayers();
  showWarning('');
  document.getElementById('trip-title').textContent = data.trip ? data.trip.name : t('no_trip');
  document.getElementById('trip-mode-label').textContent = data.view==='awarded' ? t('status_awarded') : (data.trip?.historical_view ? t('status_historical') : t('status_current_actual'));
  const viewToggle=document.getElementById('view-toggle');
  viewToggle.classList.toggle('hidden', !data.trip?.has_awarded_view);
  viewToggle.classList.toggle('active', data.view==='awarded');
  viewToggle.textContent=data.view==='awarded' ? t('show_current') : t('show_awarded');
  document.getElementById('current-trip-link').classList.toggle('hidden', !data.trip?.historical_view);

  let drawableFlights=0;
  const stops=new Map();
  const routeOccurrences=new Map();
  const addStop=(code,airport,rest=false)=>{
    if(!airport)return;
    const old=stops.get(code);
    stops.set(code,{airport,rest:Boolean(rest || old?.rest)});
  };

  for (const f of data.flights || []) {
    if (!f.origin_airport || !f.destination_airport) continue;
    drawableFlights++;
    addStop(f.origin,f.origin_airport,false);
    addStop(f.destination,f.destination_airport,(f.scheduled_rest_minutes||0)>0);

    const a=[f.origin_airport.lat,f.origin_airport.lon];
    const b=[f.destination_airport.lat,f.destination_airport.lon];
    const tooltip=`${t('leg')} ${f.sequence} · ${f.flight_number} · ${flightRouteText(f)}${f.deadhead ? ' · '+t('deadhead') : ''}`;
    const track=(f.track || []).map(p=>[p.lat,p.lon]);
    let indicatorPath=[];

    if (f.status==='current' && track.length>=2) {
      addRepeatedPolyline(track,routeStyle(f),tooltip);
      indicatorPath=track;
      const last=track[track.length-1];
      addRepeatedPolyline(greatCirclePoints(last,b),{...routeStyle(f),opacity:.62,weight:4,dashArray:'6 7'},`${tooltip} · ${t('planned_remainder')}`);
    } else if (f.status==='completed' && track.length>=2) {
      addRepeatedPolyline(track,routeStyle(f),tooltip);
      indicatorPath=track;
    } else {
      indicatorPath=greatCirclePoints(a,b);
      addRepeatedPolyline(indicatorPath,routeStyle(f),tooltip);
    }

    const routeKey=[f.origin,f.destination].sort().join('-');
    const occurrence=routeOccurrences.get(routeKey) || 0;
    routeOccurrences.set(routeKey,occurrence+1);
    addRouteIndicator(f,indicatorPath,occurrence);
  }

  for (const [code,info] of stops) addStopMarker(info.airport,code,info.rest);

  const current=(data.flights || []).find(f=>f.status==='current');
  if (current && current.latitude != null && current.longitude != null) {
    const track=(current.track || []).map(p=>[p.lat,p.lon]);
    let heading=0;
    if (track.length>=2) heading=bearingDegrees(track[track.length-2],track[track.length-1])-90;
    const html=`<div class="aircraft-marker"><span style="transform:rotate(${heading}deg)">✈</span></div>`;
    const icon=L.divIcon({className:'aircraft-div-icon',html,iconSize:[38,38],iconAnchor:[19,19]});
    const age=current.last_position_utc ? ` · ${t('position')} ${new Date(current.last_position_utc).toLocaleTimeString(currentLocale(),{hour12:false})}` : '';
    for (const offset of [-720,-360,0,360,720]) {
      aircraftLayers.push(L.marker([current.latitude,current.longitude+offset],{icon,pane:'aircraftPane'}).addTo(map).bindTooltip(`${current.flight_number}${age}`));
    }
  }

  if (!didInitialFit && data.trip) fitTrip(data);
  if (data.trip && (data.flights || []).length && drawableFlights !== data.flights.length) {
    const missing=(data.diagnostics?.missing_airports || []).join(', ');
    if (missing) showWarning(t('some_routes_missing',{airports:missing}));
  }
  updateStatusText();
}

function fmtDallas(iso) {
  if (!iso) return '—';
  return new Intl.DateTimeFormat(currentLocale(),{timeZone:'America/Chicago',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false,timeZoneName:'short'}).format(new Date(iso));
}

function timingLabel(f) {
  const timing=f?.timing || {};
  const mins=Number(timing.minutes);
  if(timing.flag==='delayed' && Number.isFinite(mins)) return `${t('delayed')} · +${Math.abs(mins)}m`;
  if(timing.flag==='ahead' && Number.isFinite(mins)) return `${t('ahead')} · −${Math.abs(mins)}m`;
  if(timing.flag==='on_time') return t('on_time');
  return t('timing_unknown');
}

function fmtDelay(f) {
  const timing=f?.timing || {};
  const mins=Number(timing.minutes);
  if(timing.flag==='delayed' && Number.isFinite(mins)) return t('delay_late',{minutes:Math.abs(mins)});
  if(timing.flag==='ahead' && Number.isFinite(mins)) return t('delay_ahead',{minutes:Math.abs(mins)});
  if(timing.flag==='on_time') return t('delay_on_time');
  return '—';
}

function updateTimingBadge(f) {
  const badge=document.getElementById('status-timing-badge');
  if(!badge) return;
  const flag=f?.timing?.flag || 'unknown';
  if(flag==='unknown') { badge.className='timing-badge hidden'; badge.textContent=''; return; }
  badge.className=`timing-badge timing-${flag}`;
  badge.textContent=timingLabel(f);
}

function setMetricLabels(primaryKey, secondaryKey) {
  const a=document.getElementById('primary-time-label');
  const b=document.getElementById('secondary-time-label');
  if(a) a.textContent=t(primaryKey);
  if(b) b.textContent=t(secondaryKey);
}

function nextFlightDescriptionHtml(next) {
  if (!next) return '';
  const when=fmtDallas(bestDepartureIso(next,{actual:false}));
  const token='__FLIGHT_LINK__';
  const typical=next.historical_average_hours!=null?` · ${t('typical_short',{time:formatHoursDuration(next.historical_average_hours)})}`:'';
  const raw=t('next_flight_status',{when,flight:token,route:flightRouteText(next)})+typical;
  return escapeHtml(raw).replace(token,flightExternalLinksHtml(next));
}

function statusMainFlightHtml(f) {
  return `${flightExternalLinksHtml(f)} · ${escapeHtml(flightRouteText(f))}`;
}

function updateStatusText() {
  const mainEl=document.getElementById('status-main');
  const detailEl=document.getElementById('status-detail');
  if (!dashboard || !dashboard.trip) {
    mainEl.textContent=t('waiting_trip');
    detailEl.textContent='';
    ['takeoff-dallas','landing-dallas','delay','rest-timer'].forEach(id=>document.getElementById(id).textContent='—');
    setMetricLabels('takeoff_dallas','landing_dallas');
    updateTimingBadge(null);
    return;
  }
  const flights=dashboard.flights || [];
  const current=flights.find(f=>f.status==='current');
  const completed=flights.filter(f=>f.status==='completed' || f.status==='past');
  const last=completed.length ? completed[completed.length-1] : null;
  const next=flights.find(f=>f.status==='scheduled');
  let mainHtml='',detailHtml='',delayFocus=null,primaryIso=null,secondaryIso=null;

  if (current) {
    mainHtml=statusMainFlightHtml(current);
    const live=[current.registration,current.aircraft_type,current.altitude_ft?`FL${Math.round(current.altitude_ft/100)}`:null,current.groundspeed_kt?`${current.groundspeed_kt} kt`:null,current.last_position_utc?`${t('position')} ${new Date(current.last_position_utc).toLocaleTimeString(currentLocale(),{hour12:false})}`:null].filter(Boolean).map(escapeHtml).join(' · ');
    const publicDelay=Number(dashboard?.status?.position_delay_minutes || 0);
    const delayNote=publicDelay>0?escapeHtml(t('public_position_delayed',{minutes:publicDelay})):'';
    const nextDescription=nextFlightDescriptionHtml(next);
    detailHtml=[live,delayNote,nextDescription].filter(Boolean).join('  •  ');
    delayFocus=current;
    primaryIso=bestDepartureIso(current);
    secondaryIso=bestArrivalIso(current,{actual:false});
    setMetricLabels('takeoff_dallas','expected_landing_dallas');
  } else if (last && next) {
    const resting=(last.status==='completed' && dashboard?.status?.is_resting);
    mainHtml=escapeHtml(resting ? t('resting_at',{place:flightAirportDisplay(last,'destination')}) : t('on_ground_at',{place:flightAirportDisplay(last,'destination')}));
    detailHtml=nextFlightDescriptionHtml(next)+(next.deadhead?` · ${escapeHtml(t('deadhead'))}`:'');
    delayFocus=next;
    primaryIso=bestArrivalIso(last);
    secondaryIso=bestDepartureIso(next,{actual:false});
    setMetricLabels('last_landing_dallas','next_takeoff_dallas');
  } else if (next) {
    mainHtml=escapeHtml(flightRouteText(next));
    detailHtml=nextFlightDescriptionHtml(next)+(next.deadhead?` · ${escapeHtml(t('deadhead'))}`:'');
    delayFocus=next;
    primaryIso=bestDepartureIso(next,{actual:false});
    secondaryIso=bestArrivalIso(next,{actual:false});
    setMetricLabels('next_takeoff_dallas','expected_landing_dallas');
  } else if (last) {
    mainHtml=escapeHtml(t('trip_complete',{place:flightAirportDisplay(last,'destination')}));
    detailHtml=escapeHtml(last.status==='completed' ? t('all_completed') : t('all_past'));
    delayFocus=last;
    primaryIso=bestArrivalIso(last);
    secondaryIso=null;
    setMetricLabels('landed_dallas','next_takeoff_dallas');
  }

  mainEl.innerHTML=mainHtml || escapeHtml(t('waiting_trip'));
  detailEl.innerHTML=detailHtml;
  updateTimingBadge(delayFocus);
  document.getElementById('takeoff-dallas').textContent=fmtDallas(primaryIso);
  document.getElementById('landing-dallas').textContent=fmtDallas(secondaryIso);
  document.getElementById('delay').textContent=fmtDelay(delayFocus);
}

function updateClocks() {
  const now=new Date();
  document.getElementById('dallas-time').textContent=new Intl.DateTimeFormat(currentLocale(),{timeZone:'America/Chicago',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(now);
  const tz=dashboard?.status?.local_timezone || 'UTC';
  const label=dashboard?.status?.local_label;
  document.getElementById('local-label').textContent=label ? `${t('jerome_time')} · ${label}` : t('jerome_time');
  try {
    document.getElementById('local-time').textContent=new Intl.DateTimeFormat(currentLocale(),{timeZone:tz,hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(now);
  } catch { document.getElementById('local-time').textContent='--:--'; }

  const next=(dashboard?.flights||[]).find(f=>f.status==='scheduled');
  const nextIso=bestDepartureIso(next,{actual:false});
  if (!nextIso || dashboard?.status?.state==='airborne') {
    document.getElementById('rest-timer').textContent='—';
    return;
  }
  const diff=Math.floor((new Date(nextIso)-now)/1000);
  if(diff<=0){document.getElementById('rest-timer').textContent=t('due_now');return;}
  const days=Math.floor(diff/86400),h=Math.floor((diff%86400)/3600),m=Math.floor((diff%3600)/60),sec=diff%60;
  const clock=`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  document.getElementById('rest-timer').textContent=days?`${days}d ${clock}`:clock;
}

let replayTimer=null;
let replayMarker=null;
let replayFlight=null;
let replayIndex=0;
let replaySpeed=1;

function ensureReplayPanel(){
  let panel=document.getElementById('replay-panel');
  if(panel) return panel;
  panel=document.createElement('div');
  panel.id='replay-panel'; panel.className='replay-panel glass hidden';
  panel.innerHTML=`<div><strong id="replay-title">Replay</strong><div id="replay-time" class="muted"></div><div id="replay-weather-status" class="muted"></div></div><div class="replay-actions"><button id="replay-pause" type="button">⏸</button><button id="replay-speed" type="button">1×</button><button id="replay-stop" type="button">×</button></div>`;
  document.body.appendChild(panel);
  panel.querySelector('#replay-pause').onclick=()=>{
    if(replayTimer){clearInterval(replayTimer);replayTimer=null;panel.querySelector('#replay-pause').textContent='▶';}
    else {startReplayTimer();panel.querySelector('#replay-pause').textContent='⏸';}
  };
  panel.querySelector('#replay-speed').onclick=()=>{replaySpeed=replaySpeed===1?2:replaySpeed===2?4:1;panel.querySelector('#replay-speed').textContent=`${replaySpeed}×`;};
  panel.querySelector('#replay-stop').onclick=()=>stopFlightReplay();
  return panel;
}

function clearReplayWeather(){
  if(replayWeatherLayer){map.removeLayer(replayWeatherLayer);replayWeatherLayer=null;}
  replayWeatherKey=null;
  if(!weatherEnabled) weatherCredit.style.display='none';
}

function replayWeatherStatus(text){const el=document.getElementById('replay-weather-status');if(el)el.textContent=text||'';}

async function loadReplayWeather(flightId){
  replayWeatherSnapshots=[]; replayRainViewerPayload=null; replayWeatherKey=null;
  try{
    const res=await fetch(`/api/flight/${Number(flightId)}/weather-replay`,{cache:'no-store'});
    if(res.ok){const data=await res.json();replayWeatherSnapshots=data.snapshots||[];}
  }catch(_){ }
  try{
    const res=await fetch('https://api.rainviewer.com/public/weather-maps.json',{cache:'no-store'});
    if(res.ok) replayRainViewerPayload=await res.json();
  }catch(_){ }
}

function closestByTime(rows, targetMs, field, maxDiffMs=Infinity){
  let best=null,bestDiff=Infinity;
  for(const row of rows||[]){const raw=row?.[field];if(raw==null)continue;const ms=typeof raw==='number'?raw*1000:new Date(raw).getTime();if(!Number.isFinite(ms))continue;const diff=Math.abs(ms-targetMs);if(diff<bestDiff){best=row;bestDiff=diff;}}
  return bestDiff<=maxDiffMs?best:null;
}

function updateReplayWeather(iso){
  if(!iso)return;
  const targetMs=new Date(iso).getTime();
  const archived=closestByTime(replayWeatherSnapshots,targetMs,'radar_time',45*60*1000);
  if(archived){
    const key=`archive:${archived.file}`;
    if(replayWeatherKey!==key){
      clearReplayWeather();
      const b=archived.bounds||{};
      const mid=((Number(b.west)||0)+(Number(b.east)||0))/2;
      const shiftedMid=shiftLonNear(mid,map.getCenter().lng);const offset=shiftedMid-mid;
      replayWeatherLayer=L.imageOverlay(archived.url,[[Number(b.south),Number(b.west)+offset],[Number(b.north),Number(b.east)+offset]],{pane:'weatherPane',opacity:.58,interactive:false}).addTo(map);
      replayWeatherKey=key;
    }
    weatherCredit.style.display='block';
    replayWeatherStatus(t('replay_weather_archived',{time:new Date((archived.radar_time||0)*1000).toISOString().slice(11,16)+'Z'}));
    return;
  }
  const frames=replayRainViewerPayload?.radar?.past||[];
  const recent=closestByTime(frames,targetMs,'time',15*60*1000);
  if(recent && replayRainViewerPayload?.host && recent.path){
    const key=`recent:${recent.time}`;
    if(replayWeatherKey!==key){
      clearReplayWeather();
      replayWeatherLayer=L.tileLayer(`${replayRainViewerPayload.host}${recent.path}/256/{z}/{x}/{y}/2/1_1.png`,{pane:'weatherPane',opacity:.56,maxNativeZoom:7,maxZoom:12,noWrap:false,attribution:'Weather radar © RainViewer'}).addTo(map);
      replayWeatherKey=key;
    }
    weatherCredit.style.display='block';
    replayWeatherStatus(t('replay_weather_recent',{time:new Date(Number(recent.time)*1000).toISOString().slice(11,16)+'Z'}));
    return;
  }
  clearReplayWeather(); replayWeatherStatus(t('replay_weather_unavailable'));
}

function replayPoint(){
  const track=replayFlight?.track||[];
  if(!track.length) return null;
  return track[Math.min(track.length-1,replayIndex)];
}

function renderReplayPoint(){
  const p=replayPoint(); if(!p) return;
  if(replayMarker) map.removeLayer(replayMarker);
  const icon=L.divIcon({className:'aircraft-div-icon replay-aircraft-icon',html:'<div class="aircraft-marker replay-aircraft"><span>✈</span></div>',iconSize:[38,38],iconAnchor:[19,19]});
  const replayLon=shiftLonNear(p.lon,map.getCenter().lng);
  replayMarker=L.marker([p.lat,replayLon],{icon,pane:'aircraftPane',interactive:false}).addTo(map);
  const panel=ensureReplayPanel();
  panel.querySelector('#replay-time').textContent=p.captured_utc ? `${fmtDallas(p.captured_utc)} · ${new Date(p.captured_utc).toISOString().replace('.000','')}` : '';
  if(p.captured_utc){drawNightOverlay(new Date(p.captured_utc));updateReplayWeather(p.captured_utc);}
}

function startReplayTimer(){
  if(replayTimer) clearInterval(replayTimer);
  replayTimer=setInterval(()=>{
    const track=replayFlight?.track||[];
    if(!track.length){stopFlightReplay();return;}
    replayIndex=Math.min(track.length-1,replayIndex+replaySpeed);
    renderReplayPoint();
    if(replayIndex>=track.length-1){clearInterval(replayTimer);replayTimer=null;const b=document.getElementById('replay-pause');if(b)b.textContent='▶';}
  },120);
}

async function startFlightReplay(flightId){
  const f=(dashboard?.flights||[]).find(x=>Number(x.id)===Number(flightId));
  if(!f || (f.track||[]).length<2){showWarning(t('replay_unavailable'));return;}
  stopFlightReplay(false);
  replayRestoreLiveWeather=weatherEnabled;
  if(weatherLayer){map.removeLayer(weatherLayer);weatherLayer=null;}
  replayFlight=f; replayIndex=0; replaySpeed=1;
  await loadReplayWeather(f.id);
  const panel=ensureReplayPanel(); panel.classList.remove('hidden');
  panel.querySelector('#replay-title').textContent=`${t('replay_flight')} · ${f.flight_number} · ${flightRouteText(f)}`;
  panel.querySelector('#replay-speed').textContent='1×'; panel.querySelector('#replay-pause').textContent='⏸';
  map.closePopup(); renderReplayPoint(); startReplayTimer();
}
window.startFlightReplay=startFlightReplay;

function stopFlightReplay(hide=true){
  if(replayTimer){clearInterval(replayTimer);replayTimer=null;}
  if(replayMarker){map.removeLayer(replayMarker);replayMarker=null;}
  clearReplayWeather(); replayWeatherSnapshots=[]; replayRainViewerPayload=null; replayWeatherStatus('');
  replayFlight=null; replayIndex=0;
  if(hide) document.getElementById('replay-panel')?.classList.add('hidden');
  drawNightOverlay(new Date());
  if(replayRestoreLiveWeather && weatherEnabled) setWeatherEnabled(true);
  replayRestoreLiveWeather=false;
}

async function refresh() {
  try {
    const params=new URLSearchParams(location.search);
    const tripId=params.get('trip_id');
    const q=new URLSearchParams();
    if(tripId) q.set('trip_id',tripId);
    q.set('view',currentMapView);
    const url=`/api/dashboard?${q.toString()}`;
    const res=await fetch(url,{cache:'no-store'});
    if (!res.ok) throw new Error(`Dashboard request failed (${res.status})`);
    drawDashboard(await res.json());
  } catch (err) {
    console.error(err);
    showWarning(t('could_not_refresh',{error:err.message}));
  }
}


async function setWeatherEnabled(enabled) {
  const btn=document.getElementById('weather-toggle');
  weatherEnabled=Boolean(enabled);
  btn.classList.toggle('active',weatherEnabled);
  btn.title=weatherEnabled?t('weather_on'):t('weather_off');
  btn.setAttribute('aria-label',btn.title);
  btn.textContent=weatherEnabled?'☔':'☁';
  weatherCredit.style.display=weatherEnabled?'block':'none';
  if(!weatherEnabled){
    if(weatherLayer){map.removeLayer(weatherLayer);weatherLayer=null;}
    return;
  }
  try{
    const res=await fetch('https://api.rainviewer.com/public/weather-maps.json',{cache:'no-store'});
    if(!res.ok) throw new Error(`RainViewer ${res.status}`);
    const payload=await res.json();
    const frames=payload?.radar?.past || [];
    const frame=frames[frames.length-1];
    if(!payload?.host || !frame?.path) throw new Error('No radar frame available');
    if(weatherLayer) map.removeLayer(weatherLayer);
    weatherLayer=L.tileLayer(`${payload.host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`,{
      pane:'weatherPane',opacity:.56,maxNativeZoom:7,maxZoom:12,noWrap:false,
      attribution:'Weather radar © RainViewer'
    }).addTo(map);
    weatherFrameGenerated=payload.generated || frame.time || null;
  }catch(err){
    weatherEnabled=false;btn.classList.remove('active');btn.textContent='☁';btn.title=t('weather_off');weatherCredit.style.display='none';
    showWarning(t('weather_unavailable',{error:err.message}));
  }
}

document.getElementById('view-toggle').onclick=async()=>{
  currentMapView=currentMapView==='current'?'awarded':'current';
  const params=new URLSearchParams(location.search);
  if(currentMapView==='awarded') params.set('view','awarded'); else params.delete('view');
  history.replaceState({},'',`${location.pathname}${params.toString()?'?'+params.toString():''}`);
  didInitialFit=false;
  await refresh();
};

function isoToUtcTime(iso){ return iso ? new Date(iso).toISOString().slice(11,16) : ''; }
function isoToUtcDate(iso, fallback=''){ return iso ? new Date(iso).toISOString().slice(0,10) : fallback; }
function validHHMM(value){ if(!value) return true; return /^([01]\d|2[0-3]):[0-5]\d$/.test(value); }
function combineUtcExact(dateValue,timeValue){
  if(!timeValue) return null;
  if(!dateValue) throw new Error(t('required_departure_date'));
  if(!validHHMM(timeValue)) throw new Error(t('time_format_error'));
  return `${dateValue}T${timeValue}:00Z`;
}
function plusUtcDays(dateValue, days){
  if(!dateValue) return '';
  const d=new Date(`${dateValue}T00:00:00Z`); d.setUTCDate(d.getUTCDate()+days); return d.toISOString().slice(0,10);
}
function maybeSuggestArrivalDate(depDateId,depTimeId,arrDateId,arrTimeId){
  const depDate=document.getElementById(depDateId)?.value || '';
  const depTime=document.getElementById(depTimeId)?.value || '';
  const arrDateEl=document.getElementById(arrDateId);
  const arrTime=document.getElementById(arrTimeId)?.value || '';
  if(!arrDateEl || !depDate || arrDateEl.dataset.userEdited==='1') return;
  arrDateEl.value=(depTime && arrTime && arrTime < depTime) ? plusUtcDays(depDate,1) : depDate;
}
function exactManualBody(prefix, tripId=null){
  const flightId=prefix==='manual'?'manual-flight-number':'schedule-add-flight';
  const originId=prefix==='manual'?'manual-origin':'schedule-add-origin';
  const destinationId=prefix==='manual'?'manual-destination':'schedule-add-destination';
  const depDateId=prefix==='manual'?'manual-departure-date':'schedule-add-dep-date';
  const depTimeId=prefix==='manual'?'manual-departure-time':'schedule-add-dep';
  const arrDateId=prefix==='manual'?'manual-arrival-date':'schedule-add-arr-date';
  const arrTimeId=prefix==='manual'?'manual-arrival-time':'schedule-add-arr';
  const deadId=prefix==='manual'?'manual-deadhead':'schedule-add-deadhead';
  const flight=document.getElementById(flightId).value.trim();
  const origin=document.getElementById(originId).value.trim();
  const destination=document.getElementById(destinationId).value.trim();
  const depDate=document.getElementById(depDateId).value;
  const depTime=document.getElementById(depTimeId).value;
  let arrDate=document.getElementById(arrDateId).value;
  const arrTime=document.getElementById(arrTimeId).value;
  if(!depDate || !flight || !origin || !destination) throw new Error(t('required_manual'));
  if(!validHHMM(depTime) || !validHHMM(arrTime)) throw new Error(t('time_format_error'));
  if(arrTime && !arrDate) arrDate=depDate;
  const depIso=combineUtcExact(depDate,depTime);
  const arrIso=combineUtcExact(arrDate,arrTime);
  if(depIso && arrIso && new Date(arrIso) <= new Date(depIso)) throw new Error(t('arrival_after_departure'));
  const body={flight_number:flight,flight_date:depDate,origin,destination,deadhead:document.getElementById(deadId).checked,scheduled_departure_utc:depIso,scheduled_arrival_utc:arrIso};
  if(tripId) body.trip_id=tripId;
  return body;
}

let currentScheduleTripId=null;

async function postManualFlight(body, messageEl){
  const res=await adminFetch('/api/manual-flight',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  if(!res) return false;
  const out=await res.json();
  if(!res.ok){ if(messageEl){messageEl.className='hint api-error';messageEl.textContent=out.detail || JSON.stringify(out);} return false; }
  if(messageEl){messageEl.className='hint api-ok';messageEl.textContent=t('flight_added');}
  currentMapView='current';
  await refresh();
  return true;
}

function formatRestMinutes(value){
  const mins=Number(value||0);
  if(!mins) return '';
  const h=Math.floor(mins/60), m=mins%60;
  return m?`${h}h ${String(m).padStart(2,'0')}`:`${h}h`;
}

async function loadScheduleEditor(explicitTripId=null){
  const params=new URLSearchParams(location.search);
  const tripId=explicitTripId || params.get('trip_id');
  const url=tripId?`/api/schedule?trip_id=${encodeURIComponent(tripId)}`:'/api/schedule';
  const res=await adminFetch(url,{cache:'no-store'});
  if(!res) return;
  const data=await res.json();
  const list=document.getElementById('schedule-list');
  const restoreAll=document.getElementById('restore-full-awarded');
  list.innerHTML='';
  currentScheduleTripId=data.trip?.id || null;
  if(!data.trip){ list.innerHTML=`<div class="hint">${escapeHtml(t('no_trip_loaded'))}</div>`; restoreAll.classList.add('hidden'); document.getElementById('schedule-add-panel').classList.add('hidden'); return; }
  document.getElementById('schedule-add-panel').classList.remove('hidden');
  const hasAwarded=Boolean(data.trip?.has_awarded_baseline);
  restoreAll.classList.toggle('hidden',!hasAwarded);

  for(const f of data.flights){
    const row=document.createElement('div'); row.className='schedule-row';
    const locked=Boolean(f.actual_departure_utc);
    const badges=[
      f.schedule_added?`<span class="schedule-badge added">${escapeHtml(t('added'))}</span>`:'',
      f.has_awarded_baseline?`<span class="schedule-badge">${escapeHtml(t('awarded_saved'))}</span>`:'',
      f.scheduled_rest_minutes?`<span class="schedule-badge rest">★ ${escapeHtml(t('rest_badge',{time:formatRestMinutes(f.scheduled_rest_minutes)}))}</span>`:''
    ].join('');
    row.innerHTML=`
      <div class="schedule-row-head"><div class="schedule-row-title">${escapeHtml(t('leg'))} ${f.sequence||'—'} · ${escapeHtml(f.flight_number)} · ${escapeHtml(flightRouteText(f))}</div><div class="schedule-badges">${badges}</div></div>
      <div class="schedule-fields compact-schedule-fields">
        <label>${escapeHtml(t('flight'))}<input class="sf-flight" value="${escapeHtml(f.flight_number)}" ${locked?'disabled':''}></label>
        <label>${escapeHtml(t('origin'))}<input class="sf-origin" value="${escapeHtml(f.origin)}" ${locked?'disabled':''}></label>
        <label>${escapeHtml(t('destination'))}<input class="sf-destination" value="${escapeHtml(f.destination)}" ${locked?'disabled':''}></label>
      </div>
      <div class="schedule-datetime-row">
        <label>${escapeHtml(t('departure_date_utc'))}<input class="sf-dep-date" type="date" value="${escapeHtml(isoToUtcDate(f.scheduled_departure_utc,f.flight_date))}" ${locked?'disabled':''}></label>
        <label>${escapeHtml(t('departure_time_utc'))}<input class="sf-dep hhmm-input" inputmode="numeric" maxlength="5" placeholder="HH:MM" value="${escapeHtml(isoToUtcTime(f.scheduled_departure_utc))}" ${locked?'disabled':''}></label>
        <label>${escapeHtml(t('arrival_date_utc'))}<input class="sf-arr-date" type="date" value="${escapeHtml(isoToUtcDate(f.scheduled_arrival_utc,f.flight_date))}" ${locked?'disabled':''}></label>
        <label>${escapeHtml(t('arrival_time_utc'))}<input class="sf-arr hhmm-input" inputmode="numeric" maxlength="5" placeholder="HH:MM" value="${escapeHtml(isoToUtcTime(f.scheduled_arrival_utc))}" ${locked?'disabled':''}></label>
      </div>
      <div class="inline-checks">
        <label class="check"><input class="sf-deadhead" type="checkbox" ${f.deadhead?'checked':''} ${locked?'disabled':''}> ${escapeHtml(t('deadhead'))}</label>
      </div>
      <div class="schedule-actions">
        ${locked?`<span class="hint">${escapeHtml(t('already_departed'))}</span>`:`<button type="button" class="sf-save">${escapeHtml(t('save_changes'))}</button><button type="button" class="sf-remove danger-button">${escapeHtml(t('remove_leg'))}</button><button type="button" class="sf-rebuild warning-button">${escapeHtml(t('rebuild_here'))}</button>${f.has_awarded_baseline?`<button type="button" class="sf-restore">${escapeHtml(t('restore_this_leg'))}</button>`:''}`}
      </div>`;
    if(!locked){
      row.querySelector('.sf-save').onclick=async()=>{
        const msg=document.getElementById('schedule-message');
        try{
          const depDate=row.querySelector('.sf-dep-date').value;
          const arrDate=row.querySelector('.sf-arr-date').value;
          const depIso=combineUtcExact(depDate,row.querySelector('.sf-dep').value);
          const arrIso=combineUtcExact(arrDate,row.querySelector('.sf-arr').value);
          if(depIso && arrIso && new Date(arrIso) <= new Date(depIso)) throw new Error(t('arrival_after_departure'));
          const body={flight_number:row.querySelector('.sf-flight').value,flight_date:depDate,origin:row.querySelector('.sf-origin').value,destination:row.querySelector('.sf-destination').value,deadhead:row.querySelector('.sf-deadhead').checked,scheduled_departure_utc:depIso,scheduled_arrival_utc:arrIso,note:'Edited in schedule editor'};
          const r=await adminFetch(`/api/flight/${f.id}/schedule`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
          if(!r) return;
          const out=await r.json(); if(!r.ok){msg.textContent=out.detail||t('could_not_save');return;}
          msg.className='hint api-ok';msg.textContent=t('schedule_updated'); await loadScheduleEditor(); await refresh();
        }catch(err){msg.className='hint api-error';msg.textContent=err.message;}
      };
      row.querySelector('.sf-remove').onclick=async()=>{
        const msg=document.getElementById('schedule-message');
        const r=await adminFetch(`/api/flight/${f.id}/remove-from-schedule`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({remove_later_flights:false})});
        if(!r) return;
        const out=await r.json(); if(!r.ok){msg.className='hint api-error';msg.textContent=out.detail||t('could_not_remove');return;}
        await loadScheduleEditor(); await refresh();
      };
      row.querySelector('.sf-rebuild').onclick=async()=>{
        if(!confirm(t('rebuild_confirm',{flight:f.flight_number}))) return;
        const msg=document.getElementById('schedule-message');
        const r=await adminFetch(`/api/flight/${f.id}/remove-from-schedule`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({remove_later_flights:true})});
        if(!r) return;
        const out=await r.json(); if(!r.ok){msg.className='hint api-error';msg.textContent=out.detail||t('could_not_rebuild');return;}
        msg.className='hint api-ok';msg.textContent=t('rebuild_done',{count:out.removed}); await loadScheduleEditor(); await refresh();
      };
      const restore=row.querySelector('.sf-restore');
      if(restore) restore.onclick=async()=>{
        const msg=document.getElementById('schedule-message');
        const r=await adminFetch(`/api/flight/${f.id}/restore-awarded`,{method:'POST'}); if(!r) return; const out=await r.json();
        if(!r.ok){msg.className='hint api-error';msg.textContent=out.detail||t('restore_unavailable');return;}
        msg.className='hint api-ok';msg.textContent=t('restore_leg_done'); await loadScheduleEditor(); await refresh();
      };
    }
    list.appendChild(row);
  }
}

const scheduleDialog=document.getElementById('schedule-dialog');
document.getElementById('edit-schedule-button').onclick=async()=>{if(!(await admin.ensure()))return;document.getElementById('schedule-message').textContent='';await loadScheduleEditor();scheduleDialog.showModal();};

document.getElementById('restore-full-awarded').onclick=async()=>{
  if(!currentScheduleTripId) return;
  if(!confirm(t('restore_full_confirm'))) return;
  const msg=document.getElementById('schedule-message');
  const res=await adminFetch(`/api/schedule/restore-awarded?trip_id=${encodeURIComponent(currentScheduleTripId)}`,{method:'POST'});
  if(!res) return;
  const out=await res.json();
  if(!res.ok){msg.className='hint api-error';msg.textContent=out.detail||t('restore_unavailable');return;}
  msg.className='hint api-ok';msg.textContent=t('restore_full_done',{restored:out.restored,removed:out.removed_added});
  await loadScheduleEditor(); await refresh();
};

document.getElementById('schedule-add-button').onclick=async()=>{
  const msg=document.getElementById('schedule-message');
  try{
    const body=exactManualBody('schedule-add',currentScheduleTripId);
    if(await postManualFlight(body,msg)){
      ['schedule-add-flight','schedule-add-origin','schedule-add-destination','schedule-add-dep-date','schedule-add-dep','schedule-add-arr-date','schedule-add-arr'].forEach(id=>document.getElementById(id).value='');
      document.getElementById('schedule-add-deadhead').checked=false;
      document.getElementById('schedule-add-arr-date').dataset.userEdited='0';
      await loadScheduleEditor();
    }
  }catch(err){msg.className='hint api-error';msg.textContent=err.message;}
};

const dialog=document.getElementById('add-dialog');
document.getElementById('add-flight-button').onclick=async()=>{if(await admin.ensure())dialog.showModal();};
document.querySelectorAll('.tab').forEach(btn=>btn.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(x=>x.classList.remove('active'));
  btn.classList.add('active'); document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
});

document.getElementById('create-manual-trip').onclick=async()=>{
  const msg=document.getElementById('dialog-message');
  const name=document.getElementById('manual-trip-name').value.trim();
  if(!name){msg.className='hint api-error';msg.textContent=t('trip_name_required');return;}
  const res=await adminFetch('/api/manual-trip',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name})});
  if(!res) return;
  const out=await res.json();
  if(!res.ok){msg.className='hint api-error';msg.textContent=out.detail||t('could_not_create_trip');return;}
  const params=new URLSearchParams(); params.set('trip_id',String(out.trip_id));
  history.replaceState({},'',`/?${params.toString()}`);
  currentMapView='current'; didInitialFit=false;
  dialog.close();
  document.getElementById('schedule-message').textContent='';
  await loadScheduleEditor(out.trip_id);
  scheduleDialog.showModal();
  await refresh();
};

document.getElementById('import-line').onclick=async()=>{
  const msg=document.getElementById('dialog-message');
  const lines=document.getElementById('lines-pdf').files[0];
  const trips=document.getElementById('trips-pdf').files[0];
  const line=Number(document.getElementById('line-number').value);
  if(!lines || !trips || !line){msg.className='hint api-error';msg.textContent=t('choose_pdfs');return;}
  const form=new FormData(); form.append('lines_pdf',lines); form.append('trips_pdf',trips); form.append('line_number',String(line));
  msg.className='hint'; msg.textContent=t('extracting');
  const res=await adminFetch('/api/import-awarded-line-pdfs',{method:'POST',body:form}); if(!res) return; const data=await res.json();
  if(!res.ok){msg.className='hint api-error';msg.textContent=data.detail || t('import_failed');return;}
  msg.className='hint api-ok';msg.textContent=t('imported',{count:data.trips_imported,line:data.line_number});
  didInitialFit=false; currentMapView='current'; history.replaceState({},'', '/'); await refresh();
};

document.getElementById('add-manual-flight').onclick=async()=>{
  const msg=document.getElementById('dialog-message');
  try{
    const body=exactManualBody('manual');
    if(await postManualFlight(body,msg)){
      const params=new URLSearchParams(location.search);params.delete('view');history.replaceState({},'',`${location.pathname}${params.toString()?'?'+params.toString():''}`);
    }
  }catch(err){msg.className='hint api-error';msg.textContent=err.message;}
};

const settingsDialog=document.getElementById('settings-dialog');
const providerSelect=document.getElementById('tracking-provider');
const providerHelp=document.getElementById('provider-help');
function updateProviderHelp(){
  const p=providerSelect.value;
  const keys={disabled:'provider_disabled',aeroapi:'provider_aero',fr24:'provider_fr24',adsbx:'provider_adsbx'};
  providerHelp.textContent=keys[p]?t(keys[p]):'';
  document.getElementById('api-key-label').style.display=p==='disabled'?'none':'flex';
}
providerSelect.addEventListener('change',()=>{updateProviderHelp(); if(settingsDialog?.open) refreshActualUsage(false);});

const VIEWER_ID_KEY='upsTrackerViewerId';
let viewerId=localStorage.getItem(VIEWER_ID_KEY);
if(!viewerId){ viewerId=(crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`); localStorage.setItem(VIEWER_ID_KEY,viewerId); }
let lastViewerCount=0;
async function sendViewerHeartbeat(){
  if(document.visibilityState!=='visible') return;
  try{
    const res=await fetch('/api/viewers/heartbeat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({viewer_id:viewerId}),cache:'no-store'});
    if(res.ok){const data=await res.json();lastViewerCount=Number(data.active_viewers||0);updateViewerText();}
  }catch(_){ }
}
function updateViewerText(){
  const el=document.getElementById('tracking-viewers'); if(!el) return;
  const mode=lastViewerCount>0?t('viewer_position_on'):t('viewer_position_off');
  el.textContent=t('active_viewers',{count:lastViewerCount,mode});
}
async function refreshActualUsage(force=false){
  const el=document.getElementById('tracking-provider-usage'); if(!el) return;
  if(providerSelect.value!=='aeroapi'){el.textContent='';el.style.display='none';return;}
  try{
    const res=await adminFetch(`/api/settings/tracking/usage?refresh=${force?'true':'false'}`,{cache:'no-store'});
    const data=await res.json();
    el.style.display='block';
    if(data.available){
      el.className='usage-card api-ok';
      el.textContent=t('actual_usage',{total:Number(data.total_cost||0).toFixed(2),discounted:Number(data.discounted_total_cost||0).toFixed(2),calls:Number(data.total_calls||0)});
    }else{el.className='usage-card';el.textContent=t('usage_unavailable');}
  }catch(_){el.style.display='block';el.className='usage-card';el.textContent=t('usage_unavailable');}
}

document.getElementById('settings-button').onclick=async()=>{
  if(!(await admin.ensure())) return;
  const res=await adminFetch('/api/settings/tracking',{cache:'no-store'});
  if(!res) return;
  const cfg=await res.json();
  providerSelect.value=cfg.provider || 'disabled';
  const key=document.getElementById('tracking-api-key');
  key.value='';
  key.placeholder=cfg.api_key_set ? t('saved_key') : t('paste_key');
  document.getElementById('tracking-poll-seconds').value=cfg.poll_seconds || 600;
  document.getElementById('tracking-budget').value=cfg.monthly_budget_usd ?? 4.5;
  document.getElementById('tracking-public-delay').value=cfg.public_delay_minutes ?? 10;
  document.getElementById('tracking-message').textContent='';
  const spent=Number(cfg.local_estimated_spend_usd || 0).toFixed(3);
  const remaining=Number(cfg.local_estimated_remaining_usd || 0).toFixed(3);
  document.getElementById('tracking-usage').textContent=t('usage',{spent,remaining});
  updateProviderHelp();
  updateViewerText();
  await refreshActualUsage(false);
  settingsDialog.showModal();
};

document.getElementById('save-tracking-settings').onclick=async()=>{
  const msg=document.getElementById('tracking-message');
  msg.className='hint'; msg.textContent=t('saving');
  const body={
    provider:providerSelect.value,
    api_key:document.getElementById('tracking-api-key').value || null,
    poll_seconds:Number(document.getElementById('tracking-poll-seconds').value),
    monthly_budget_usd:Number(document.getElementById('tracking-budget').value),
    public_delay_minutes:Number(document.getElementById('tracking-public-delay').value),
  };
  const res=await adminFetch('/api/settings/tracking',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  if(!res) return;
  if(!res.ok){const e=await res.json();msg.className='hint api-error';msg.textContent=e.detail || t('settings_save_failed');return;}
  const cfg=await res.json();
  msg.className='hint api-ok';msg.textContent=t('settings_saved');
  document.getElementById('tracking-api-key').value='';
  document.getElementById('tracking-usage').textContent=t('usage',{spent:Number(cfg.local_estimated_spend_usd||0).toFixed(3),remaining:Number(cfg.local_estimated_remaining_usd||0).toFixed(3)});
  await refreshActualUsage(true);
};

document.getElementById('test-tracking-settings').onclick=async()=>{
  const msg=document.getElementById('tracking-message');
  msg.className='hint';msg.textContent=t('testing_provider');
  const res=await adminFetch('/api/settings/tracking/test',{method:'POST'});
  if(!res) return;
  const data=await res.json();
  if(!res.ok){msg.className='hint api-error';msg.textContent=data.detail || t('test_unavailable');return;}
  msg.className='hint api-ok';msg.textContent=data.message || t('connection_ok');
  await refreshActualUsage(true);
};

document.getElementById('sync-tracking-now').onclick=async()=>{
  const msg=document.getElementById('tracking-message');
  msg.className='hint';msg.textContent=t('syncing');
  const res=await adminFetch('/api/settings/tracking/sync-now',{method:'POST'});
  if(!res) return;
  const data=await res.json();
  if(!res.ok){msg.className='hint api-error';msg.textContent=data.detail || t('sync_failed');return;}
  const errors=(data.results || []).filter(x=>x.error).map(x=>x.error);
  const warnings=(data.results || []).flatMap(x=>x.warnings || []);
  if(errors.length){msg.className='hint api-error';msg.textContent=errors.join(' · ');}
  else if(warnings.length){msg.className='hint';msg.textContent=t('sync_warning',{warning:warnings.join(' · ')});}
  else {msg.className='hint api-ok';msg.textContent=data.polled ? t('tracking_refreshed') : t('no_tracking_window');}
  const cfg=data.settings || {};
  document.getElementById('tracking-usage').textContent=t('usage',{spent:Number(cfg.local_estimated_spend_usd||0).toFixed(3),remaining:Number(cfg.local_estimated_remaining_usd||0).toFixed(3)});
  lastViewerCount=Number(data.active_viewers ?? lastViewerCount); updateViewerText();
  await refreshActualUsage(false);
  await refresh();
};

function updateLanguageButton(){
  const btn=document.getElementById('language-toggle');
  btn.textContent=(window.TrackerI18n.language || 'en').toUpperCase();
  btn.title=t('language_title');
}
updateLanguageButton();
document.getElementById('language-toggle').onclick=()=>toggleLanguage();
document.addEventListener('tracker-admin-change', async()=>{ await refresh(); });

document.addEventListener('tracker-theme-change', async(ev)=>{
  applyMapTheme(ev.detail?.theme || window.TrackerTheme?.theme || 'light');
  if (dashboard) drawDashboard(dashboard);
});

document.addEventListener('tracker-language-change',async(ev)=>{
  updateLanguageButton();
  applyMapLanguage(ev.detail?.language || window.TrackerI18n.language);
  weatherCredit.innerHTML=`${t('radar_credit').replace('RainViewer','')}<a href="https://www.rainviewer.com/" target="_blank" rel="noopener">RainViewer</a>`;
  const weatherBtn=document.getElementById('weather-toggle'); if(weatherBtn) weatherBtn.title=weatherEnabled?t('weather_on'):t('weather_off');
  const mapOnlyBtn=document.getElementById('map-only-toggle'); if(mapOnlyBtn) mapOnlyBtn.title=document.body.classList.contains('map-only')?t('show_ui'):t('map_only');
  updateProviderHelp();
  updateClocks();
  updateStatusText();
  updateViewerText();
  if(settingsDialog.open) await refreshActualUsage(false);
  if(scheduleDialog.open) await loadScheduleEditor();
});

for (const cfg of [
  ['manual-departure-date','manual-departure-time','manual-arrival-date','manual-arrival-time'],
  ['schedule-add-dep-date','schedule-add-dep','schedule-add-arr-date','schedule-add-arr'],
]) {
  const [dd,dt,ad,at]=cfg;
  [dd,dt,at].forEach(id=>document.getElementById(id)?.addEventListener('change',()=>maybeSuggestArrivalDate(dd,dt,ad,at)));
  document.getElementById(ad)?.addEventListener('change',ev=>{ev.target.dataset.userEdited='1';});
}

if ('serviceWorker' in navigator) {
  const localHosts=new Set(['127.0.0.1','localhost']);
  if (localHosts.has(location.hostname)) navigator.serviceWorker.getRegistrations().then(regs=>regs.forEach(r=>r.unregister()));
  else navigator.serviceWorker.register('/static/sw.js');
}

sendViewerHeartbeat();
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')sendViewerHeartbeat();});
setInterval(sendViewerHeartbeat,30000);

drawNightOverlay();
refresh();
setInterval(refresh,120000);
setInterval(updateClocks,1000);
setInterval(()=>drawNightOverlay(new Date()),60000);
setInterval(()=>{if(weatherEnabled)setWeatherEnabled(true);},300000);
updateClocks();
