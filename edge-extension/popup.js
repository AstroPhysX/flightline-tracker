'use strict';

const $ = id => document.getElementById(id);
const statusEl = $('status');
const syncButton = $('sync');
const saveButton = $('save');
const testButton = $('test');
const FALLBACK_TTL_MS = 10 * 60 * 1000;

function setStatus(message, kind='') {
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

function normalizeTrackerUrl(value) {
  const raw=String(value||'').trim().replace(/\/+$/,'');
  if(!raw) throw new Error('Open ⚙ and enter your Flightline Tracker URL.');
  const url=new URL(raw);
  if(!['http:','https:'].includes(url.protocol)) throw new Error('Tracker URL must use http:// or https://.');
  return url.origin + url.pathname.replace(/\/+$/,'');
}
function originPattern(trackerUrl){const u=new URL(trackerUrl);return `${u.protocol}//${u.host}/*`;}

function pageKind(tab) {
  const title=String(tab?.title||'');
  let parsed;
  try{parsed=new URL(tab?.url||'');}catch(_){return null;}
  if(/Crew Jumpseat|\bJumpseat\b/i.test(title)) return 'jumpseat';
  if(/Work Schedule|Time Detail/i.test(title)) return 'schedule';
  if(parsed.hostname==='flightops.inside.ups.com' && /CrewSchedules\/ViewWorkSchedule\.aspx/i.test(parsed.pathname)) return 'schedule';
  if(parsed.hostname.endsWith('.isolation.zscaler.com')){
    const original=decodeURIComponent(parsed.searchParams.get('original_url')||'');
    if(/flightops\.inside\.ups\.com\/Dotnet\/CrewSchedules\/ViewWorkSchedule\.aspx/i.test(original)) return 'schedule';
    if(/jumpseat/i.test(original)) return 'jumpseat';
  }
  return null;
}

async function activeSupportedTab(){
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  if(!tab?.id) throw new Error('Could not identify the active Edge tab.');
  const kind=pageKind(tab);
  if(!kind) throw new Error('Open UPS Work Schedule / Time Detail or Crew Jumpseat before syncing.');
  return {tab,kind};
}

async function loadSettings(){
  const d=await chrome.storage.local.get(['trackerUrl','syncToken','jumpseatAirport','jumpseatOnlyHome','clipboardFallback','calendarSnapshot']);
  $('tracker-url').value=d.trackerUrl||'';
  $('sync-token').value=d.syncToken||'';
  $('jumpseat-airport').value=d.jumpseatAirport||'DFW';
  $('jumpseat-only-home').checked=d.jumpseatOnlyHome!==false;

  let detected=null;
  try{detected=await activeSupportedTab();}catch(_){ }
  const label=$('page-kind');
  if(detected?.kind==='jumpseat') { label.textContent='✓ Confirmed jumpseat page detected'; syncButton.textContent='Sync jumpseats'; }
  else if(detected?.kind==='schedule') { label.textContent='✓ Work Schedule page detected'; syncButton.textContent='Sync schedule'; }
  else { label.textContent='Open a supported UPS page'; syncButton.textContent='Sync'; }

  const fb=d.clipboardFallback;
  if(detected && fb && fb.tabId===detected.tab.id && fb.kind===detected.kind && Number(fb.expiresAt||0)>Date.now()){
    syncButton.textContent='Sync copied text';
    setStatus(`Screen reading needs the text fallback. Press Ctrl+A, Ctrl+C on this UPS page, reopen the extension and click Sync copied text.`, 'warn');
  } else if(fb) {
    await chrome.storage.local.remove('clipboardFallback');
  } else if(!d.trackerUrl || !d.syncToken) {
    setStatus('Open ⚙ once to configure the tracker URL and sync token.', 'warn');
  } else {
    setStatus(detected ? 'Ready. Nothing is sent until you press Sync.' : 'Waiting for a supported UPS page.');
  }
}

async function saveSettings({quiet=false}={}){
  const trackerUrl=normalizeTrackerUrl($('tracker-url').value);
  const syncToken=$('sync-token').value.trim();
  if(!syncToken) throw new Error('Enter the SCHEDULE_SYNC_TOKEN configured on Flightline Tracker.');
  const jumpseatAirport=String($('jumpseat-airport').value||'DFW').trim().toUpperCase().replace(/[^A-Z0-9]/g,'');
  if(jumpseatAirport && jumpseatAirport.length<3) throw new Error('Jumpseat home airport should be an airport code such as DFW.');
  const pattern=originPattern(trackerUrl);
  const granted=await chrome.permissions.request({origins:[pattern]});
  if(!granted) throw new Error(`Edge permission for ${new URL(trackerUrl).origin} was not granted.`);
  const jumpseatOnlyHome=$('jumpseat-only-home').checked;
  await chrome.storage.local.set({trackerUrl,syncToken,jumpseatAirport,jumpseatOnlyHome});
  if(!quiet) setStatus('Settings saved.', 'ok');
  return {trackerUrl,syncToken,jumpseatAirport,jumpseatOnlyHome};
}

async function requestFallback(tab, kind, reason){
  await chrome.storage.local.set({clipboardFallback:{tabId:tab.id,kind,expiresAt:Date.now()+FALLBACK_TTL_MS,reason:String(reason||'')}});
  syncButton.textContent='Sync copied text';
  setStatus(`I couldn't read the screen reliably (${reason}). Nothing was changed. Press Ctrl+A, Ctrl+C on this page, reopen the extension and click Sync copied text.`, 'warn');
}
async function clearFallback(){await chrome.storage.local.remove('clipboardFallback');}
async function fallbackFor(tab,kind){const d=await chrome.storage.local.get('clipboardFallback');const f=d.clipboardFallback;return Boolean(f&&f.tabId===tab.id&&f.kind===kind&&Number(f.expiresAt||0)>Date.now());}

async function screenshotToOcr(tab,trackerUrl,syncToken){
  const dataUrl=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'png'});
  const blob=await (await fetch(dataUrl)).blob();
  const form=new FormData();form.append('screenshot',blob,'ups-page.png');
  const r=await fetch(`${trackerUrl}/api/integrations/ups-schedule/ocr`,{method:'POST',headers:{Authorization:`Bearer ${syncToken}`},body:form});
  const j=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.detail||`Tracker OCR returned HTTP ${r.status}.`);
  return j;
}

async function postJson(url,payload,token){
  const r=await fetch(url,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.detail||`Tracker returned HTTP ${r.status}.`);
  return j;
}

function countSchedule(payload){return (payload.trips||[]).reduce((n,t)=>n+(t.flights||[]).length,0);}
function scheduleSuccess(payload,result,method){const a=result.apply||{};setStatus(`${method}: ${countSchedule(payload)} flights · ${a.added||0} added · ${a.updated||0} updated · ${a.removed||0} removed.`, 'ok');}
function jumpseatSuccess(payload,result,method){const a=result.apply||{};setStatus(`${method}: ${payload.entries.length} confirmed jumpseat(s) · ${a.added||0} added · ${a.updated||0} updated · ${a.removed||0} removed.`, 'ok');}

async function syncScreen(tab,kind){
  const cfg=await saveSettings({quiet:true});
  setStatus('Reading the visible UPS page…');
  let ocr;
  try{ocr=await screenshotToOcr(tab,cfg.trackerUrl,cfg.syncToken);}catch(e){await requestFallback(tab,kind,e?.message||String(e));return;}

  if(kind==='schedule'){
    if(ocr.page_type==='calendar'){
      await clearFallback();
      setStatus('Work Schedule calendar detected. Open Time Detail from the first scheduled entry, then press Sync again.', 'ok');
      return;
    }
    if(ocr.page_type!=='time_detail'||Number(ocr.quality_score||0)<16){await requestFallback(tab,kind,'the capture did not contain a clear Time Detail table');return;}
    if(!ocr.complete_view){await requestFallback(tab,kind,'the complete Time Detail table is not visible in one capture');return;}
    try{
      const payload=FlightlineUpsParser.parseUpsTimeDetailOcr(ocr.text||'',chrome.runtime.getManifest().version);payload.page_url=null;
      const result=await postJson(`${cfg.trackerUrl}/api/integrations/ups-schedule`,payload,cfg.syncToken);
      await chrome.storage.local.remove('calendarSnapshot');await clearFallback();scheduleSuccess(payload,result,'Screen capture');
    }catch(e){await requestFallback(tab,kind,e?.message||String(e));}
    return;
  }

  if(ocr.page_type!=='jumpseat'||Number(ocr.quality_score||0)<10){await requestFallback(tab,kind,'the capture did not contain a clear confirmed-jumpseat table');return;}
  try{
    const payload=FlightlineUpsParser.parseJumpseatText(ocr.text||'',chrome.runtime.getManifest().version,{hubAirport:cfg.jumpseatAirport,onlyHub:cfg.jumpseatOnlyHome});
    const result=await postJson(`${cfg.trackerUrl}/api/integrations/ups-jumpseats`,payload,cfg.syncToken);
    await clearFallback();jumpseatSuccess(payload,result,'Screen capture');
  }catch(e){await requestFallback(tab,kind,e?.message||String(e));}
}

async function syncClipboard(tab,kind){
  const cfg=await saveSettings({quiet:true});
  let text='';
  try{text=await navigator.clipboard.readText();}catch(_){throw new Error('Edge could not read the clipboard. Press Ctrl+A and Ctrl+C on the UPS page, then try again.');}

  if(kind==='jumpseat'){
    const payload=FlightlineUpsParser.parseJumpseatText(text,chrome.runtime.getManifest().version,{hubAirport:cfg.jumpseatAirport,onlyHub:cfg.jumpseatOnlyHome});
    const result=await postJson(`${cfg.trackerUrl}/api/integrations/ups-jumpseats`,payload,cfg.syncToken);
    await clearFallback();jumpseatSuccess(payload,result,'Copied text');return;
  }

  if(/UNOFFICIAL SCHEDULE/i.test(text)&&!/\bTime Detail\b/i.test(text)){
    const calendar=FlightlineUpsParser.parseUpsCalendar(text);await chrome.storage.local.set({calendarSnapshot:calendar});await clearFallback();
    const first=calendar.entries.map(e=>e.flight_date).sort()[0];setStatus(`Calendar captured. Open Time Detail from the first scheduled entry (${first}), copy it, then Sync again.`, 'ok');return;
  }
  let payload=FlightlineUpsParser.parseUpsTimeDetail(text,chrome.runtime.getManifest().version);payload.page_url=null;
  const stored=await chrome.storage.local.get('calendarSnapshot');
  if(stored.calendarSnapshot){const merged=FlightlineUpsParser.useCalendarCoverage(payload,stored.calendarSnapshot);if(merged.missing.length)throw new Error(`Copied Time Detail is incomplete; ${merged.missing.length} calendar flight(s) are missing.`);payload=merged.payload;}
  const result=await postJson(`${cfg.trackerUrl}/api/integrations/ups-schedule`,payload,cfg.syncToken);
  await chrome.storage.local.remove(['calendarSnapshot','clipboardFallback']);scheduleSuccess(payload,result,'Copied text');
}

async function sync(){
  syncButton.disabled=true;saveButton.disabled=true;testButton.disabled=true;
  try{const {tab,kind}=await activeSupportedTab();if(await fallbackFor(tab,kind))await syncClipboard(tab,kind);else await syncScreen(tab,kind);}catch(e){setStatus(e?.message||String(e),'error');}
  finally{syncButton.disabled=false;saveButton.disabled=false;testButton.disabled=false;}
}

$('settings-toggle').addEventListener('click',()=>$('settings-panel').classList.toggle('hidden'));
saveButton.addEventListener('click',async()=>{try{saveButton.disabled=true;await saveSettings();}catch(e){setStatus(e?.message||String(e),'error');}finally{saveButton.disabled=false;}});
testButton.addEventListener('click',async()=>{try{testButton.disabled=true;const c=await saveSettings({quiet:true});const r=await fetch(`${c.trackerUrl}/api/integrations/ups-schedule/ping`,{headers:{Authorization:`Bearer ${c.syncToken}`}});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||`HTTP ${r.status}`);setStatus(`Connected to Flightline Tracker ${j.version||''}; sync token accepted.`,'ok');}catch(e){setStatus(`Connection failed: ${e?.message||e}`,'error');}finally{testButton.disabled=false;}});
syncButton.addEventListener('click',sync);
loadSettings();
