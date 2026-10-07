# v30 changes

## Status card

- Larger takeoff/landing symbols on desktop and mobile.
- Restored explicit time-location labels such as `Last landing - Dallas time`, while keeping the displayed time itself free of timezone abbreviations.
- The custom local clock includes the active airport code: `JEROME TIME - ONT`, `JEROME TIME - SDF`, etc.
- Airborne wording is now `Flying` followed by the existing flight/route and telemetry information (no literal ellipsis).
- `Next flight in` automatically changes to `Flight time left` while airborne and counts down against the best current estimated arrival.
- After landing, Delay / ahead continues to show the last landing result for the first half of the rest unless the next flight already has useful delay/on-time information.
- The local clock name is prominently editable in Admin **Tracker settings**.
- Next Flight is reformatted as heading → Dallas date/time → flight/FR24 + route/destination city → estimated flight time.

## History

- Flights from the same trip are grouped by a light green vertical trip band with a compact vertical `Trip ####` or `Pair ####` label.
- Repeated full trip descriptors were removed from every flight card.
- `Operating` text is green; `Deadhead` text is orange.
- Admin mode adds per-flight delete controls in both Actual / Current and Initial Schedule lanes.
- Admin mode adds a per-day delete control that clears both lanes for that displayed date.
- Deleting an Actual / Current item preserves its initial awarded baseline when available, and removes provider track/logbook/weather data tied to the actual item.

## Retained from v28

- Mobile right-side drawer and Android-safe maskable PWA icons.
- Date-by-date Actual / Current vs Initial Schedule history model.
- Complete-coverage UPS sync safeguards.
- User-triggered one-click Ctrl+A/C copy path in Edge extension v4, with OCR/manual fallback.
- Up to five weather snapshots on normal flights and seven on long-haul flights.


Additional v30 refinements:
- Larger, clearer takeoff/landing SVG icons.
- History timeline now groups trips inside larger schedule-group bands (e.g. BP 2606 · Line 18).
- Actual/Current vertical trip labels are on the left; Initial Schedule labels are on the right.
