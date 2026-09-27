'use strict';

const $ = id => document.getElementById(id);
const statusEl = $('status');
const saveButton = $('save');
const syncButton = $('sync');
const FALLBACK_TTL_MS = 10 * 60 * 1000;

function setStatus(message, kind = '') {
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

function normalizeTrackerUrl(value) {
  const raw = String(value || '').trim().replace(/\/+$/, '');
  if (!raw) throw new Error('Enter your Flightline Tracker URL.');
  const url = new URL(raw);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Tracker URL must use http:// or https://.');
  return url.origin + url.pathname.replace(/\/+$/, '');
}

function originPattern(trackerUrl) {
  const u = new URL(trackerUrl);
  return `${u.protocol}//${u.host}/*`;
}

async function getActiveScheduleTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab?.url) throw new Error('Could not identify the active Edge tab.');

  let parsed;
  try { parsed = new URL(tab.url); } catch (_) { throw new Error('The active tab does not have a readable URL.'); }

  let supported = false;
  if (parsed.hostname === 'flightops.inside.ups.com') {
    supported = /\/Dotnet\/CrewSchedules\/ViewWorkSchedule\.aspx/i.test(parsed.pathname);
  } else if (parsed.hostname.endsWith('.isolation.zscaler.com')) {
    const original = parsed.searchParams.get('original_url') || '';
    supported = /flightops\.inside\.ups\.com\/Dotnet\/CrewSchedules\/ViewWorkSchedule\.aspx/i.test(original);
  }

  if (!supported) {
    throw new Error('Open the UPS Flight Operations Work Schedule / Time Detail page before syncing.');
  }
  return tab;
}

async function loadSettings() {
  const data = await chrome.storage.local.get(['trackerUrl', 'syncToken', 'calendarSnapshot', 'clipboardFallback']);
  $('tracker-url').value = data.trackerUrl || '';
  $('sync-token').value = data.syncToken || '';

  let tab = null;
  try { tab = await getActiveScheduleTab(); } catch (_) {}
  const fallback = data.clipboardFallback;
  if (tab && fallback && fallback.tabId === tab.id && Number(fallback.expiresAt || 0) > Date.now()) {
    syncButton.textContent = 'Sync copied text';
    setStatus('Screen reading needs the text fallback. On the UPS Time Detail page press Ctrl+A, then Ctrl+C, reopen this popup and click Sync copied text.', 'warn');
    return;
  }

  if (fallback) await chrome.storage.local.remove('clipboardFallback');
  syncButton.textContent = 'Sync current page';
  if (data.calendarSnapshot) {
    setStatus(`Copied-calendar fallback is staged for PP ${data.calendarSnapshot.bid_period}. Open Time Detail from the first scheduled entry, copy the page, then click Sync.`, 'warn');
  } else {
    setStatus('Open UPS Time Detail and click Sync current page. No Ctrl+A / Ctrl+C is normally needed.');
  }
}

async function saveSettings({ quiet = false } = {}) {
  const trackerUrl = normalizeTrackerUrl($('tracker-url').value);
  const syncToken = $('sync-token').value.trim();
  if (!syncToken) throw new Error('Enter the SCHEDULE_SYNC_TOKEN configured on Flightline Tracker.');

  const pattern = originPattern(trackerUrl);
  const granted = await chrome.permissions.request({ origins: [pattern] });
  if (!granted) throw new Error(`Edge permission for ${new URL(trackerUrl).origin} was not granted.`);

  await chrome.storage.local.set({ trackerUrl, syncToken });
  if (!quiet) setStatus('Settings saved. The token stays in this Edge profile.', 'ok');
  return { trackerUrl, syncToken };
}

async function fallbackForTab(tab) {
  const data = await chrome.storage.local.get(['clipboardFallback']);
  const fallback = data.clipboardFallback;
  return Boolean(fallback && fallback.tabId === tab.id && Number(fallback.expiresAt || 0) > Date.now());
}

async function requestClipboardFallback(tab, reason) {
  await chrome.storage.local.set({
    clipboardFallback: {
      tabId: tab.id,
      expiresAt: Date.now() + FALLBACK_TTL_MS,
      reason: String(reason || '')
    }
  });
  syncButton.textContent = 'Sync copied text';
  setStatus(`I couldn't read the screen reliably (${reason}). No schedule was changed. Press Ctrl+A, then Ctrl+C on UPS Time Detail, reopen this popup and click Sync copied text.`, 'warn');
}

async function clearFallback() {
  await chrome.storage.local.remove('clipboardFallback');
  syncButton.textContent = 'Sync current page';
}

function flightCount(payload) {
  return (payload.trips || []).reduce((n, t) => n + (t.flights || []).length, 0);
}

async function sendNormalizedPayload(payload, trackerUrl, syncToken) {
  const response = await fetch(`${trackerUrl}/api/integrations/ups-schedule`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${syncToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
  let result = {};
  try { result = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(result.detail || `Tracker returned HTTP ${response.status}.`);
  return result;
}

function reportSuccess(payload, result, methodLabel) {
  const applied = result.apply || {};
  const summary = [
    `${flightCount(payload)} flight(s)`,
    `${applied.added || 0} added`,
    `${applied.updated || 0} updated`,
    `${applied.removed || 0} removed`
  ].join(' · ');
  setStatus(`${methodLabel} synced PP ${payload.bid_period} (${payload.coverage_start_date} through ${payload.coverage_end_date}). ${summary}.`, 'ok');
}

async function screenshotToText(tab, trackerUrl, syncToken) {
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
  const imageBlob = await (await fetch(dataUrl)).blob();
  const form = new FormData();
  form.append('screenshot', imageBlob, 'ups-schedule.png');

  const response = await fetch(`${trackerUrl}/api/integrations/ups-schedule/ocr`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${syncToken}` },
    body: form
  });
  let result = {};
  try { result = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(result.detail || `Tracker OCR returned HTTP ${response.status}.`);
  return result;
}

async function syncCurrentScreen(tab) {
  const { trackerUrl, syncToken } = await saveSettings({ quiet: true });
  setStatus('Reading the visible UPS page…');

  let ocr;
  try {
    ocr = await screenshotToText(tab, trackerUrl, syncToken);
  } catch (error) {
    await requestClipboardFallback(tab, error?.message || String(error));
    return;
  }

  if (ocr.page_type === 'calendar') {
    await clearFallback();
    setStatus('Work Schedule calendar detected. Click the first scheduled flight/date to open Time Detail, then click Sync current page. Nothing has been sent to Current yet.', 'ok');
    return;
  }

  if (ocr.page_type !== 'time_detail' || Number(ocr.quality_score || 0) < 16) {
    await requestClipboardFallback(tab, 'the captured page did not contain a clear Time Detail table');
    return;
  }
  if (!ocr.complete_view) {
    await requestClipboardFallback(tab, 'the complete Time Detail table is not visible in one screen capture');
    return;
  }

  try {
    const version = chrome.runtime.getManifest().version;
    const payload = FlightlineUpsParser.parseUpsTimeDetailOcr(ocr.text || '', version);
    payload.page_url = null; // Never transmit the signed Zscaler session URL.
    const result = await sendNormalizedPayload(payload, trackerUrl, syncToken);
    await chrome.storage.local.remove('calendarSnapshot');
    await clearFallback();
    reportSuccess(payload, result, 'Screen capture');
  } catch (error) {
    await requestClipboardFallback(tab, error?.message || String(error));
  }
}

async function syncClipboard(tab) {
  setStatus('Reading copied UPS text…');
  let text;
  try {
    text = await navigator.clipboard.readText();
  } catch (error) {
    throw new Error('Edge could not read the clipboard. Press Ctrl+A and Ctrl+C on the UPS page, then try again.');
  }

  if (/UNOFFICIAL SCHEDULE/i.test(text) && !/\bTime Detail\b/i.test(text)) {
    const calendar = FlightlineUpsParser.parseUpsCalendar(text);
    await chrome.storage.local.set({ calendarSnapshot: calendar });
    await clearFallback();
    const first = calendar.entries.map(e => e.flight_date).sort()[0];
    setStatus(`Copied calendar captured: ${calendar.entries.length} flight(s), PP ${calendar.bid_period}. Now open Time Detail from the first scheduled entry (${first}), press Ctrl+A / Ctrl+C, then click Sync again.`, 'ok');
    return;
  }

  const { trackerUrl, syncToken } = await saveSettings({ quiet: true });
  const version = chrome.runtime.getManifest().version;
  let payload = FlightlineUpsParser.parseUpsTimeDetail(text, version);
  payload.page_url = null;

  const stored = await chrome.storage.local.get(['calendarSnapshot']);
  if (stored.calendarSnapshot) {
    const merged = FlightlineUpsParser.useCalendarCoverage(payload, stored.calendarSnapshot);
    if (merged.missing.length) {
      const firstMissing = merged.missing.sort((a, b) => a.flight_date.localeCompare(b.flight_date))[0];
      throw new Error(`Copied Time Detail is incomplete for the captured calendar. It is missing ${merged.missing.length} flight(s), starting ${firstMissing.flight_date} ${firstMissing.flight_number} ${firstMissing.origin}-${firstMissing.destination}. Open Time Detail from the first scheduled entry and copy again.`);
    }
    payload = merged.payload;
  }

  const result = await sendNormalizedPayload(payload, trackerUrl, syncToken);
  await chrome.storage.local.remove(['calendarSnapshot', 'clipboardFallback']);
  syncButton.textContent = 'Sync current page';
  reportSuccess(payload, result, 'Copied text fallback');
}

async function sync() {
  syncButton.disabled = true;
  saveButton.disabled = true;
  try {
    const tab = await getActiveScheduleTab();
    if (await fallbackForTab(tab)) {
      await syncClipboard(tab);
    } else {
      await syncCurrentScreen(tab);
    }
  } catch (error) {
    setStatus(error?.message || String(error), 'error');
  } finally {
    syncButton.disabled = false;
    saveButton.disabled = false;
  }
}

saveButton.addEventListener('click', async () => {
  try {
    saveButton.disabled = true;
    await saveSettings();
  } catch (error) {
    setStatus(error?.message || String(error), 'error');
  } finally {
    saveButton.disabled = false;
  }
});

syncButton.addEventListener('click', sync);
loadSettings();
