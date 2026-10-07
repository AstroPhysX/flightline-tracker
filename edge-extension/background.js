'use strict';

function pageKind(tab) {
  const title = String(tab?.title || '');
  const rawUrl = String(tab?.url || '');
  let url;
  try { url = new URL(rawUrl); } catch (_) { return null; }

  if (/Crew Jumpseat|\bJumpseat\b/i.test(title)) return 'jumpseat';
  if (/Work Schedule|Time Detail/i.test(title)) return 'schedule';

  if (url.hostname === 'flightops.inside.ups.com' && /CrewSchedules\/ViewWorkSchedule\.aspx/i.test(url.pathname)) return 'schedule';
  if (url.hostname.endsWith('.isolation.zscaler.com')) {
    const original = decodeURIComponent(url.searchParams.get('original_url') || '');
    if (/flightops\.inside\.ups\.com\/Dotnet\/CrewSchedules\/ViewWorkSchedule\.aspx/i.test(original)) return 'schedule';
    if (/jumpseat/i.test(original)) return 'jumpseat';
  }
  return null;
}

async function update(tab) {
  if (!tab?.id) return;
  const kind = pageKind(tab);
  await chrome.action.setBadgeText({tabId: tab.id, text: kind ? 'SYNC' : ''});
  if (!kind) return;

  const key = `notified:${tab.id}`;
  const store = chrome.storage.session || chrome.storage.local;
  const saved = await store.get(key);
  const fingerprint = `${kind}|${tab.url || ''}|${tab.title || ''}`;
  if (saved[key] === fingerprint) return;
  await store.set({[key]: fingerprint});

  const message = kind === 'jumpseat'
    ? 'Confirmed jumpseats detected. Open Flightline UPS Sync when you are ready.'
    : 'UPS schedule page detected. Open Flightline UPS Sync when you are ready.';
  try {
    const notificationId=`flightline-sync-${tab.id}`;
    await chrome.notifications.create(notificationId, {
      type: 'basic',
      iconUrl: 'icons/icon-128.png',
      title: 'Flightline Tracker',
      message,
      priority: 0
    });
    setTimeout(()=>chrome.notifications.clear(notificationId).catch(()=>{}), 5000);
  } catch (_) {}
}

chrome.tabs.onActivated.addListener(async info => {
  try { await update(await chrome.tabs.get(info.tabId)); } catch (_) {}
});
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' || changeInfo.title || changeInfo.url) update(tab).catch(()=>{});
});


async function sleep(ms){ return new Promise(resolve=>setTimeout(resolve,ms)); }
async function dispatchKey(tabId, params){ return chrome.debugger.sendCommand({tabId}, 'Input.dispatchKeyEvent', params); }

async function copyRemotePageText(tabId){
  const tab=await chrome.tabs.get(tabId);
  if(!pageKind(tab)) throw new Error('This tab is not a supported UPS page.');
  const target={tabId};
  await chrome.debugger.attach(target,'1.3');
  try{
    // User-triggered only. The short-lived debugger attachment injects the same
    // Select All / Copy keystrokes the user previously performed by hand.
    await dispatchKey(tabId,{type:'keyDown',key:'Control',code:'ControlLeft',windowsVirtualKeyCode:17,nativeVirtualKeyCode:17,modifiers:2});
    await dispatchKey(tabId,{type:'rawKeyDown',key:'a',code:'KeyA',windowsVirtualKeyCode:65,nativeVirtualKeyCode:65,modifiers:2});
    await dispatchKey(tabId,{type:'keyUp',key:'a',code:'KeyA',windowsVirtualKeyCode:65,nativeVirtualKeyCode:65,modifiers:2});
    await sleep(120);
    await dispatchKey(tabId,{type:'rawKeyDown',key:'c',code:'KeyC',windowsVirtualKeyCode:67,nativeVirtualKeyCode:67,modifiers:2});
    await dispatchKey(tabId,{type:'keyUp',key:'c',code:'KeyC',windowsVirtualKeyCode:67,nativeVirtualKeyCode:67,modifiers:2});
    await dispatchKey(tabId,{type:'keyUp',key:'Control',code:'ControlLeft',windowsVirtualKeyCode:17,nativeVirtualKeyCode:17,modifiers:0});
    await sleep(220);
    // Best-effort clear of the remote selection without navigating/clicking.
    await dispatchKey(tabId,{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27,modifiers:0}).catch(()=>{});
    await dispatchKey(tabId,{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27,modifiers:0}).catch(()=>{});
    await sleep(80);
    return {ok:true};
  } finally {
    await chrome.debugger.detach(target).catch(()=>{});
  }
}

chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.action!=='copy-active-page-text') return;
  copyRemotePageText(Number(message.tabId)).then(sendResponse).catch(err=>sendResponse({ok:false,error:err?.message||String(err)}));
  return true;
});
