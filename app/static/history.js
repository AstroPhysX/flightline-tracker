const { t, toggle: toggleLanguage } = window.TrackerI18n;

function updateLanguageButton(){
  const btn=document.getElementById('language-toggle');
  if(!btn) return;
  btn.textContent=(window.TrackerI18n.language || 'en').toUpperCase();
  btn.title=t('language_title');
}

function formatTiming(el){
  const flag=el.dataset.flag || 'unknown';
  const mins=Number(el.dataset.minutes);
  el.className=`history-timing timing-badge timing-${flag}`;
  if(flag==='delayed' && Number.isFinite(mins)) el.textContent=`${t('delayed')} · +${Math.abs(mins)}m`;
  else if(flag==='ahead' && Number.isFinite(mins)) el.textContent=`${t('ahead')} · −${Math.abs(mins)}m`;
  else if(flag==='on_time') el.textContent=t('on_time');
  else el.textContent=t('timing_unknown');
}

function applyDynamicTranslations(){
  document.querySelectorAll('.history-flight-type').forEach(el=>{el.textContent=t(el.dataset.type==='deadhead'?'deadhead':'operating');});
  document.querySelectorAll('.history-flight-status').forEach(el=>{const key=`status_${el.dataset.status}`;const translated=t(key);el.textContent=translated===key?el.dataset.status:translated;});
  document.querySelectorAll('.history-timing').forEach(formatTiming);
  updateLanguageButton();
  setTimeout(()=>{ if(typeof drawHistoryTripBands==='function') drawHistoryTripBands(); },0);
}

updateLanguageButton();
document.getElementById('language-toggle')?.addEventListener('click',()=>toggleLanguage());
document.addEventListener('tracker-language-change',applyDynamicTranslations);
applyDynamicTranslations();


// v29: compact trip bands and granular history deletion.
function shortTripBandLabel(name){
  const text=String(name||'').trim();
  const trip=text.match(/\bTrip\s+[^·]+/i);
  if(trip) return trip[0].trim();
  const pair=text.match(/\bPair\s+[^·]+/i);
  if(pair) return pair[0].trim();
  return text.length>24 ? `${text.slice(0,22)}…` : text;
}

let tripBandResizeTimer=null;
function drawHistoryTripBands(){
  const timeline=document.querySelector('.history-timeline');
  if(!timeline) return;
  timeline.querySelectorAll('.history-trip-band').forEach(el=>el.remove());
  const timelineRect=timeline.getBoundingClientRect();
  for(const laneClass of ['actual-lane','initial-lane']){
    const groups=new Map();
    timeline.querySelectorAll(`.${laneClass} .history-leg-card[data-trip-id]`).forEach(card=>{
      const key=card.dataset.tripId;
      if(!key) return;
      if(!groups.has(key)) groups.set(key,[]);
      groups.get(key).push(card);
    });
    for(const [tripId,cards] of groups){
      if(!cards.length) continue;
      const days=cards.map(c=>c.closest('.history-day')).filter(Boolean);
      const lane=cards[0].closest('.history-day-lane');
      if(!days.length || !lane) continue;
      const laneRect=lane.getBoundingClientRect();
      const top=Math.min(...days.map(d=>d.getBoundingClientRect().top))-timelineRect.top+4;
      const bottom=Math.max(...days.map(d=>d.getBoundingClientRect().bottom))-timelineRect.top-4;
      const band=document.createElement('div');
      band.className=`history-trip-band ${laneClass==='actual-lane'?'actual-band':'initial-band'}`;
      band.dataset.tripId=tripId;
      band.style.left=`${laneRect.left-timelineRect.left}px`;
      band.style.width=`${laneRect.width}px`;
      band.style.top=`${top}px`;
      band.style.height=`${Math.max(28,bottom-top)}px`;
      const fullName=cards[0].dataset.tripName || '';
      band.title=fullName;
      const label=document.createElement('span');
      label.className='history-trip-band-label';
      label.textContent=shortTripBandLabel(fullName);
      band.appendChild(label);
      timeline.appendChild(band);
    }
  }
}

window.addEventListener('resize',()=>{
  clearTimeout(tripBandResizeTimer);
  tripBandResizeTimer=setTimeout(drawHistoryTripBands,100);
});
window.addEventListener('load',drawHistoryTripBands);
requestAnimationFrame(()=>requestAnimationFrame(drawHistoryTripBands));
document.addEventListener('tracker-admin-change',()=>setTimeout(drawHistoryTripBands,0));

const itemDialog=document.getElementById('delete-history-item-dialog');
const itemMessage=document.getElementById('delete-history-item-message');
const itemConfirm=document.getElementById('delete-history-item-confirm');
let pendingHistoryDelete=null;

function openHistoryItemDelete(url,messageText){
  pendingHistoryDelete=url;
  itemMessage.textContent=messageText;
  itemDialog?.showModal();
}

document.querySelectorAll('.history-delete-flight').forEach(button=>{
  button.addEventListener('click',async()=>{
    if(!(await window.TrackerAdmin.ensure())) return;
    const lane=button.dataset.historyLane || 'actual';
    const laneText=lane==='initial'?t('initial_schedule'):t('actual_current');
    const base=t('delete_history_flight_confirm',{lane:laneText});
    openHistoryItemDelete(`/api/history/flight/${encodeURIComponent(button.dataset.flightId)}?lane=${encodeURIComponent(lane)}`,`${button.dataset.flightLabel || ''} — ${base}`);
  });
});

document.querySelectorAll('.history-delete-day').forEach(button=>{
  button.addEventListener('click',async()=>{
    if(!(await window.TrackerAdmin.ensure())) return;
    const day=button.dataset.historyDate || '';
    openHistoryItemDelete(`/api/history/day/${encodeURIComponent(day)}?lane=both`,t('delete_history_day_confirm',{date:day}));
  });
});

document.getElementById('delete-history-item-cancel')?.addEventListener('click',()=>itemDialog?.close());
itemConfirm?.addEventListener('click',async()=>{
  if(!pendingHistoryDelete) return;
  itemConfirm.disabled=true;
  try{
    if(!(await window.TrackerAdmin.ensure())) return;
    const res=await fetch(pendingHistoryDelete,{method:'DELETE'});
    if(res.status===401){window.TrackerAdmin.handleUnauthorized(res);return;}
    const data=await res.json();
    if(!res.ok) throw new Error(data.detail || 'Delete failed');
    itemDialog?.close();
    location.reload();
  }catch(err){ itemMessage.textContent=err.message; }
  finally{itemConfirm.disabled=false;}
});

const dialog=document.getElementById('delete-history-dialog');
const message=document.getElementById('delete-history-message');
const confirmInput=document.getElementById('delete-history-confirm');
const deleteButton=document.getElementById('delete-history-confirm-button');
let pendingTripId=null;
let pendingCard=null;

document.querySelectorAll('.delete-history-button').forEach(button=>{
  button.addEventListener('click',async()=>{
    if(!(await window.TrackerAdmin.ensure())) return;
    pendingTripId=Number(button.dataset.tripId);
    pendingCard=button.closest('.history-card');
    confirmInput.value='';
    message.textContent=t('delete_trip_named',{name:button.dataset.tripName || ''});
    dialog.showModal();
    setTimeout(()=>confirmInput.focus(),50);
  });
});

document.getElementById('delete-history-cancel')?.addEventListener('click',()=>dialog.close());

deleteButton?.addEventListener('click',async()=>{
  if(confirmInput.value.trim().toUpperCase()!=='DELETE'){
    confirmInput.classList.add('danger-input-error');
    setTimeout(()=>confirmInput.classList.remove('danger-input-error'),700);
    return;
  }
  deleteButton.disabled=true;
  try{
    if(!(await window.TrackerAdmin.ensure())) return;
    const res=await fetch(`/api/trip/${pendingTripId}/history`,{method:'DELETE'});
    if(res.status===401){window.TrackerAdmin.handleUnauthorized(res);return;}
    if(!res.ok){const data=await res.json();throw new Error(data.detail || 'Delete failed');}
    dialog.close();
    location.reload();
  }catch(err){
    message.textContent=err.message;
  }finally{deleteButton.disabled=false;}
});

// Logbook Pro archive import / clear controls.
const logbookImportDialog=document.getElementById('logbook-import-dialog');
const logbookClearDialog=document.getElementById('logbook-clear-dialog');
const logbookMessage=document.getElementById('logbook-import-message');

document.getElementById('import-logbook-button')?.addEventListener('click',async()=>{
  if(!(await window.TrackerAdmin.ensure())) return;
  logbookMessage.textContent='';
  logbookImportDialog?.showModal();
});
document.getElementById('logbook-import-close')?.addEventListener('click',()=>logbookImportDialog?.close());

document.getElementById('logbook-import-submit')?.addEventListener('click',async()=>{
  if(!(await window.TrackerAdmin.ensure())) return;
  const input=document.getElementById('logbook-file');
  if(!input?.files?.[0]){logbookMessage.className='hint api-error';logbookMessage.textContent=t('logbook_file');return;}
  const btn=document.getElementById('logbook-import-submit'); btn.disabled=true;
  logbookMessage.className='hint';logbookMessage.textContent=t('logbook_importing');
  try{
    const form=new FormData(); form.append('logbook_file',input.files[0]);
    const res=await fetch('/api/logbook/import',{method:'POST',body:form});
    if(res.status===401){window.TrackerAdmin.handleUnauthorized(res);return;}
    const data=await res.json(); if(!res.ok) throw new Error(data.detail || t('logbook_import_failed'));
    logbookMessage.className='hint api-ok';
    logbookMessage.textContent=t('logbook_imported',{inserted:data.inserted,updated:data.updated,mapped:data.mapped_entries});
  }catch(err){logbookMessage.className='hint api-error';logbookMessage.textContent=err.message;}
  finally{btn.disabled=false;}
});

document.getElementById('logbook-clear-button')?.addEventListener('click',async()=>{
  if(!(await window.TrackerAdmin.ensure())) return;
  document.getElementById('logbook-clear-confirm').value='';
  logbookClearDialog?.showModal();
});
document.getElementById('logbook-clear-cancel')?.addEventListener('click',()=>logbookClearDialog?.close());
document.getElementById('logbook-clear-confirm-button')?.addEventListener('click',async()=>{
  const input=document.getElementById('logbook-clear-confirm');
  if((input?.value||'').trim().toUpperCase()!=='DELETE'){input?.classList.add('danger-input-error');setTimeout(()=>input?.classList.remove('danger-input-error'),700);return;}
  if(!(await window.TrackerAdmin.ensure())) return;
  const res=await fetch('/api/logbook',{method:'DELETE'});
  if(res.status===401){window.TrackerAdmin.handleUnauthorized(res);return;}
  const data=await res.json();
  if(!res.ok){logbookClearDialog?.close();logbookMessage.className='hint api-error';logbookMessage.textContent=data.detail || 'Delete failed';return;}
  logbookClearDialog?.close();logbookMessage.className='hint api-ok';logbookMessage.textContent=t('logbook_cleared');
});
