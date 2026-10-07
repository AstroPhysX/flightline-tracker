(function (global) {
  'use strict';

  const FLIGHT_ROW_DATE = /^(.*)-(\d{2}\/\d{2}\/\d{2})$/;

  function parseUsDate(text) {
    const m = String(text || '').trim().match(/^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/);
    if (!m) return null;
    let year = Number(m[3]);
    if (year < 100) year += 2000;
    return new Date(Date.UTC(year, Number(m[1]) - 1, Number(m[2])));
  }

  function isoDate(date) {
    return date.toISOString().slice(0, 10);
  }

  function hhmm(text) {
    const m = String(text || '').match(/(\d{4})\s*$/);
    if (!m) return null;
    const h = Number(m[1].slice(0, 2));
    const min = Number(m[1].slice(2));
    if (h > 23 || min > 59) return null;
    return { h, min, minutes: h * 60 + min };
  }

  function atUtc(date, clock, dayOffset = 0) {
    if (!date || !clock) return null;
    return new Date(Date.UTC(
      date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + dayOffset,
      clock.h, clock.min, 0, 0
    ));
  }

  function nearestDay(baseDate, clock, target) {
    if (!clock || !target) return null;
    const choices = [-1, 0, 1].map(offset => atUtc(baseDate, clock, offset));
    choices.sort((a, b) => Math.abs(a - target) - Math.abs(b - target));
    return choices[0];
  }

  function parseCities(value) {
    const s = String(value || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (s.length !== 6) return null;
    return { origin: s.slice(0, 3), destination: s.slice(3) };
  }

  function parseUpsTimeDetail(text, extensionVersion = '1.0.0') {
    const normalized = String(text || '').replace(/\r\n?/g, '\n');
    if (!/\bTime Detail\b/i.test(normalized)) {
      if (/UNOFFICIAL SCHEDULE/i.test(normalized)) {
        throw new Error('The clipboard contains the calendar page. Click the first scheduled flight/date to open Time Detail, then press Ctrl+A, Ctrl+C and Sync again.');
      }
      throw new Error('Clipboard does not look like a UPS Time Detail page.');
    }

    const header = normalized.match(/Pairing Detail for Pay Period\s+(\S+)\s+(\d{1,2}\/\d{1,2}\/\d{4})\s+Thru\s+(\d{1,2}\/\d{1,2}\/\d{4})/i);
    if (!header) throw new Error('Could not find the UPS pay-period range in copied text.');

    const payPeriod = header[1].trim();
    const ppStart = parseUsDate(header[2]);
    const ppEnd = parseUsDate(header[3]);
    if (!ppStart || !ppEnd) throw new Error('Could not parse the UPS pay-period dates.');

    const profile = normalized.match(/Fleet:\s*([^\s]+)\s+Seat:\s*([^\s]+)\s+Domicile:\s*([^\s]+)/i);
    const fleet = profile ? profile[1].trim() : null;
    const seat = profile ? profile[2].trim() : null;
    const domicile = profile ? profile[3].trim().toUpperCase() : null;

    const lines = normalized.split('\n');
    const flights = [];

    for (const line of lines) {
      if (!line.includes('\t')) continue;
      const fields = line.split('\t').map(v => v.trim());
      const fdIndex = fields.findIndex(v => FLIGHT_ROW_DATE.test(v));
      if (fdIndex < 1 || fdIndex + 7 >= fields.length) continue;

      const match = fields[fdIndex].match(FLIGHT_ROW_DATE);
      const flightNumber = match[1].trim().toUpperCase();
      const flightDate = parseUsDate(match[2]);
      const cities = parseCities(fields[fdIndex + 1]);
      if (!flightDate || !cities || !flightNumber) continue;
      if (flightDate < ppStart || flightDate > ppEnd) continue;

      const pair = (fields[0] || '').trim();
      const code = (fields[fdIndex + 2] || '').trim().toUpperCase();
      const schedOutClock = hhmm(fields[fdIndex + 3]);
      const actualOutClock = hhmm(fields[fdIndex + 4]);
      const actualInClock = hhmm(fields[fdIndex + 5]);
      const schedInClock = hhmm(fields[fdIndex + 6]);

      if (!schedOutClock || !schedInClock) continue;

      const scheduledDeparture = atUtc(flightDate, schedOutClock, 0);
      const arrivalOffset = schedInClock.minutes < schedOutClock.minutes ? 1 : 0;
      const scheduledArrival = atUtc(flightDate, schedInClock, arrivalOffset);
      const actualDeparture = nearestDay(flightDate, actualOutClock, scheduledDeparture);
      const actualArrival = nearestDay(flightDate, actualInClock, scheduledArrival);

      flights.push({
        pair,
        external_id: `${pair}:${flightNumber}:${isoDate(flightDate)}`,
        kind: 'flight',
        flight_number: flightNumber,
        flight_date: isoDate(flightDate),
        origin: cities.origin,
        destination: cities.destination,
        deadhead: code === 'DH',
        scheduled_departure_utc: scheduledDeparture.toISOString(),
        scheduled_arrival_utc: scheduledArrival.toISOString(),
        actual_departure_utc: actualDeparture ? actualDeparture.toISOString() : null,
        actual_arrival_utc: actualArrival ? actualArrival.toISOString() : null,
        schedule_code: code || null
      });
    }

    if (!flights.length) throw new Error('No in-pay-period flight rows were found in Time Detail.');

    flights.sort((a, b) => a.scheduled_departure_utc.localeCompare(b.scheduled_departure_utc));
    const grouped = new Map();
    for (const f of flights) {
      const key = f.pair || 'UNASSIGNED';
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(f);
    }

    const trips = [...grouped.entries()].map(([pair, rows]) => ({
      external_id: pair,
      name: `UPS Pair ${pair}`,
      start_date: rows[0].flight_date,
      end_date: rows[rows.length - 1].flight_date,
      flights: rows.map(({ pair: _pair, ...f }) => f)
    }));

    const captureStart = flights[0].flight_date;
    const captureEnd = isoDate(ppEnd);

    return {
      schema_version: 2,
      source: 'ups-edge-extension',
      extension_version: extensionVersion,
      captured_at: new Date().toISOString(),
      bid_period: payPeriod,
      pay_period_start: isoDate(ppStart),
      pay_period_end: isoDate(ppEnd),
      coverage_start_date: captureStart,
      coverage_end_date: captureEnd,
      coverage_complete: false,
      fleet,
      seat,
      domicile,
      trips
    };
  }


  function normalizeSeat(value) {
    const v = String(value || '').trim().toUpperCase();
    if (['FIO', 'F|O', 'F0', 'FO'].includes(v)) return 'F/O';
    return v || null;
  }

  function parseUpsTimeDetailOcr(text, extensionVersion = '2.0.0') {
    const normalized = String(text || '').replace(/\r\n?/g, '\n');
    if (!/\bTime Detail\b/i.test(normalized) && !/Pairing Detail for Pay Period/i.test(normalized)) {
      if (/UNOFFICIAL SCHEDULE/i.test(normalized)) {
        throw new Error('The screen shows the Work Schedule calendar. Open Time Detail from the first scheduled flight/date, then click Sync again.');
      }
      throw new Error('The screen capture does not look like a UPS Time Detail page.');
    }

    const header = normalized.match(/Pairing Detail for Pay Period\s+([A-Z0-9-]+)\s+(\d{1,2}\/\d{1,2}\/\d{4})\s+Thru\s+(\d{1,2}\/\d{1,2}\/\d{4})/i);
    if (!header) throw new Error('Screen reading could not reliably find the UPS pay-period range.');

    const payPeriod = header[1].trim();
    const ppStart = parseUsDate(header[2]);
    const ppEnd = parseUsDate(header[3]);
    if (!ppStart || !ppEnd) throw new Error('Screen reading could not parse the UPS pay-period dates.');

    const profile = normalized.match(/Fleet:\s*([^\s]+)\s+Seat:\s*([^\s]+)\s+Domicile:\s*([^\s]+)/i);
    const fleet = profile ? profile[1].trim().toUpperCase() : null;
    const seat = profile ? normalizeSeat(profile[2]) : null;
    const domicile = profile ? profile[3].trim().toUpperCase().replace(/[^A-Z0-9]/g, '') : null;

    const flights = [];
    let inPeriodRowCandidates = 0;
    const fdRe = /([A-Z0-9]+)-([0-9]{1,2}\/[0-9]{1,2}\/[0-9]{2})/i;

    for (const rawLine of normalized.split('\n')) {
      let line = rawLine.trim();
      if (!line) continue;
      line = line.replace(/[|—–=]+/g, ' ').replace(/\s+/g, ' ').trim();
      const fd = line.match(fdRe);
      if (!fd) continue;

      const flightNumber = fd[1].trim().toUpperCase();
      const flightDate = parseUsDate(fd[2]);
      if (!flightDate || flightDate < ppStart || flightDate > ppEnd) continue;
      inPeriodRowCandidates += 1;

      const prefix = line.slice(0, fd.index).trim().split(/\s+/).filter(Boolean);
      const pair = (prefix[0] || '').trim().toUpperCase();
      if (!pair) continue;

      const afterFd = line.slice(fd.index + fd[0].length).trim();
      const cityMatch = afterFd.match(/\b([A-Z]{6})\b/i);
      if (!cityMatch) continue;
      const cities = parseCities(cityMatch[1]);
      if (!cities) continue;

      const afterCities = afterFd.slice(cityMatch.index + cityMatch[0].length).trim();
      const outMatch = afterCities.match(/[(']?\s*[A-Z]{2}\d{2}\)?\s*(\d{4})/i);
      if (!outMatch) continue;
      const schedOutClock = hhmm(outMatch[1]);
      if (!schedOutClock) continue;

      const beforeOut = afterCities.slice(0, outMatch.index).toUpperCase();
      const deadhead = /\bDH\b/.test(beforeOut);
      const afterOut = afterCities.slice(outMatch.index + outMatch[0].length);
      const inMatch = afterOut.match(/[(']?\s*\d{2}\)\s*(\d{4})/);
      if (!inMatch) continue;
      const schedInClock = hhmm(inMatch[1]);
      if (!schedInClock) continue;

      const actualSection = afterOut.slice(0, inMatch.index);
      const actualTokens = [...actualSection.matchAll(/\b(\d{4})\b/g)].map(m => m[1]);
      const actualOutClock = actualTokens.length >= 1 ? hhmm(actualTokens[0]) : null;
      const actualInClock = actualTokens.length >= 2 ? hhmm(actualTokens[1]) : null;

      const scheduledDeparture = atUtc(flightDate, schedOutClock, 0);
      const arrivalOffset = schedInClock.minutes < schedOutClock.minutes ? 1 : 0;
      const scheduledArrival = atUtc(flightDate, schedInClock, arrivalOffset);
      const actualDeparture = nearestDay(flightDate, actualOutClock, scheduledDeparture);
      const actualArrival = nearestDay(flightDate, actualInClock, scheduledArrival);

      flights.push({
        pair,
        external_id: `${pair}:${flightNumber}:${isoDate(flightDate)}`,
        kind: 'flight',
        flight_number: flightNumber,
        flight_date: isoDate(flightDate),
        origin: cities.origin,
        destination: cities.destination,
        deadhead,
        scheduled_departure_utc: scheduledDeparture.toISOString(),
        scheduled_arrival_utc: scheduledArrival.toISOString(),
        actual_departure_utc: actualDeparture ? actualDeparture.toISOString() : null,
        actual_arrival_utc: actualArrival ? actualArrival.toISOString() : null,
        schedule_code: deadhead ? 'DH' : null
      });
    }

    if (!flights.length) throw new Error('Screen reading did not produce any reliable in-pay-period flight rows.');
    if (flights.length !== inPeriodRowCandidates) {
      throw new Error(`Screen reading found ${inPeriodRowCandidates} in-pay-period row(s) but could safely parse only ${flights.length}.`);
    }

    // Reject duplicate OCR rows rather than silently applying ambiguous data.
    const seen = new Set();
    for (const f of flights) {
      const key = `${f.pair}|${f.flight_number}|${f.flight_date}|${f.origin}|${f.destination}`;
      if (seen.has(key)) throw new Error('Screen reading produced duplicate flight rows and was not considered reliable.');
      seen.add(key);
    }

    flights.sort((a, b) => a.scheduled_departure_utc.localeCompare(b.scheduled_departure_utc));
    const grouped = new Map();
    for (const f of flights) {
      const key = f.pair || 'UNASSIGNED';
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(f);
    }

    const trips = [...grouped.entries()].map(([pair, rows]) => ({
      external_id: pair,
      name: `UPS Pair ${pair}`,
      start_date: rows[0].flight_date,
      end_date: rows[rows.length - 1].flight_date,
      flights: rows.map(({ pair: _pair, ...f }) => f)
    }));

    return {
      schema_version: 2,
      source: 'ups-edge-extension-screen',
      extension_version: extensionVersion,
      captured_at: new Date().toISOString(),
      bid_period: payPeriod,
      pay_period_start: isoDate(ppStart),
      pay_period_end: isoDate(ppEnd),
      coverage_start_date: flights[0].flight_date,
      coverage_end_date: isoDate(ppEnd),
      coverage_complete: false,
      fleet,
      seat,
      domicile,
      trips
    };
  }

  function normalizeFlightToken(value) {
    return String(value || '').trim().toUpperCase().replace(/\s+/g, '').replace(/^UPS/, '');
  }

  function calendarKey(entry) {
    return `${entry.flight_date}|${normalizeFlightToken(entry.flight_number)}|${entry.origin}|${entry.destination}`;
  }

  function parseUpsCalendar(text) {
    const normalized = String(text || '').replace(/\r\n?/g, '\n');
    if (!/UNOFFICIAL SCHEDULE/i.test(normalized)) {
      throw new Error('Clipboard does not look like the UPS Work Schedule calendar.');
    }
    const pp = normalized.match(/Pay Period:\s*\n?\s*([A-Z0-9-]+)/i);
    if (!pp) throw new Error('Could not find the Work Schedule pay period.');

    const profile = normalized.match(/Fleet:\s*([^\s]+)\s+Seat:\s*([^\s]+)\s+Domicile:\s*([^\s]+)/i);
    const allDates = [...normalized.matchAll(/\b(\d{1,2}\/\d{1,2}\/\d{4})\b/g)]
      .map(m => parseUsDate(m[1])).filter(Boolean);
    if (!allDates.length) throw new Error('Could not find calendar dates.');
    allDates.sort((a, b) => a - b);

    let activeDate = null;
    let currentPair = null;
    const entries = [];
    for (const rawLine of normalized.split('\n')) {
      const line = rawLine.trim();
      if (!line) continue;
      const dates = [...line.matchAll(/\b(\d{1,2}\/\d{1,2}\/\d{4})\b/g)];
      if (dates.length) {
        activeDate = parseUsDate(dates[dates.length - 1][1]);
        currentPair = null;
        continue;
      }
      if (!activeDate) continue;
      if (/^[A-Z0-9]+$/i.test(line)) {
        currentPair = line.toUpperCase();
        continue;
      }
      const f = line.match(/^(.+?)\s+([A-Z0-9]{6})$/i);
      if (!f || !currentPair) continue;
      const cities = parseCities(f[2]);
      if (!cities) continue;
      entries.push({
        pair: currentPair,
        flight_number: f[1].trim().toUpperCase(),
        flight_date: isoDate(activeDate),
        origin: cities.origin,
        destination: cities.destination
      });
    }
    if (!entries.length) throw new Error('No scheduled flights were found on the calendar.');
    return {
      bid_period: pp[1].trim(),
      pay_period_start: isoDate(allDates[0]),
      pay_period_end: isoDate(allDates[allDates.length - 1]),
      fleet: profile ? profile[1].trim() : null,
      seat: profile ? profile[2].trim() : null,
      domicile: profile ? profile[3].trim().toUpperCase() : null,
      entries
    };
  }

  function useCalendarCoverage(detailPayload, calendar) {
    if (!calendar || calendar.bid_period !== detailPayload.bid_period) {
      return { payload: detailPayload, complete: false, missing: [] };
    }
    const detailEntries = detailPayload.trips.flatMap(t => t.flights);
    const detailKeys = new Set(detailEntries.map(calendarKey));
    const missing = calendar.entries.filter(e => !detailKeys.has(calendarKey(e)));
    if (missing.length) return { payload: detailPayload, complete: false, missing };
    return {
      payload: {
        ...detailPayload,
        pay_period_start: calendar.pay_period_start,
        pay_period_end: calendar.pay_period_end,
        coverage_start_date: calendar.pay_period_start,
        coverage_end_date: calendar.pay_period_end,
        coverage_complete: true
      },
      complete: true,
      missing: []
    };
  }



  function parseJumpseatText(text, extensionVersion = '3.0.0', options = {}) {
    const normalized = String(text || '').replace(/\r\n?/g, '\n');
    if (!/Upcoming\s+Jumpseats\s+Confirmed/i.test(normalized) && !/Crew\s+Jumpseat/i.test(normalized)) {
      throw new Error('The page does not look like the UPS confirmed jumpseat screen.');
    }
    const hub = String(options.hubAirport || 'DFW').trim().toUpperCase().replace(/[^A-Z0-9]/g,'');
    const onlyHub = options.onlyHub !== false;
    const rowRe = /\b(UPS\s*[0-9O]{1,5})\s+([A-Z]{3})\s+([A-Z]{3})\s+(\d{1,2}\/\d{1,2}\/\d{2})\s+(\d{1,2}):(\d{2})Z\s+(\d{1,2}):(\d{2})Z\b/gi;
    const rows = [];
    const seen = new Set();
    for (const m of normalized.matchAll(rowRe)) {
      const flightNumber = m[1].replace(/\s+/g,'').toUpperCase().replace(/O/g,'0');
      const origin = m[2].toUpperCase();
      const destination = m[3].toUpperCase();
      if (onlyHub && hub && origin !== hub && destination !== hub) continue;
      const flightDate = parseUsDate(m[4]);
      if (!flightDate) continue;
      const depClock = {h:Number(m[5]), min:Number(m[6]), minutes:Number(m[5])*60+Number(m[6])};
      const arrClock = {h:Number(m[7]), min:Number(m[8]), minutes:Number(m[7])*60+Number(m[8])};
      if (depClock.h>23 || depClock.min>59 || arrClock.h>23 || arrClock.min>59) continue;
      const dep = atUtc(flightDate, depClock, 0);
      const arr = atUtc(flightDate, arrClock, arrClock.minutes < depClock.minutes ? 1 : 0);
      const key = `${flightNumber}|${isoDate(flightDate)}|${origin}|${destination}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push({
        external_id:key,
        flight_number:flightNumber,
        flight_date:isoDate(flightDate),
        origin,
        destination,
        deadhead:true,
        scheduled_departure_utc:dep.toISOString(),
        scheduled_arrival_utc:arr.toISOString()
      });
    }
    if (!rows.length) {
      throw new Error(onlyHub && hub
        ? `No confirmed jumpseats touching ${hub} were found on this page.`
        : 'No confirmed jumpseat rows were found on this page.');
    }
    rows.sort((a,b)=>a.scheduled_departure_utc.localeCompare(b.scheduled_departure_utc));
    return {
      schema_version:1,
      source:'ups-edge-extension-jumpseat',
      extension_version:extensionVersion,
      captured_at:new Date().toISOString(),
      hub_airport:hub || null,
      complete_view:/Upcoming\s+Jumpseats\s+Standby/i.test(normalized) || /no\s+standby\s+jumpseats/i.test(normalized),
      entries:rows
    };
  }

  global.FlightlineUpsParser = { parseUpsTimeDetail, parseUpsTimeDetailOcr, parseUpsCalendar, useCalendarCoverage, parseJumpseatText };
})(globalThis);
