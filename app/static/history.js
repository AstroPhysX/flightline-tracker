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
}

updateLanguageButton();
document.getElementById('language-toggle')?.addEventListener('click',()=>toggleLanguage());
document.addEventListener('tracker-language-change',applyDynamicTranslations);
applyDynamicTranslations();

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
    pendingCard?.remove();
    if(!document.querySelector('.history-card')) location.reload();
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
  const input=document.getElementById('logbook-csv');
  if(!input?.files?.[0]){logbookMessage.className='hint api-error';logbookMessage.textContent=t('logbook_csv');return;}
  const btn=document.getElementById('logbook-import-submit'); btn.disabled=true;
  logbookMessage.className='hint';logbookMessage.textContent=t('logbook_importing');
  try{
    const form=new FormData(); form.append('logbook_csv',input.files[0]);
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
