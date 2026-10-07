# v28 changes

## Mobile / PWA

- Added dedicated Android-safe maskable 192 px and 512 px icons. The aircraft is centered inside a larger safe area so circular and rounded-square launchers do not crop the nose, tail or wingtips.
- Mobile navigation is now an off-canvas right drawer. The hamburger stays above the drawer/backdrop, while the status card remains below it.
- The status card keeps the full mobile status instead of trading it for the navigation bar.

## Current-trip status

- Jerome/Dallas clocks no longer append timezone abbreviations such as `CDT` or offset labels such as `UTC(+5)`.
- Takeoff and landing labels include quick visual aircraft symbols.
- An airborne leg begins with **Flying…**.
- Delay/ahead state moved out of the status text and into the Delay / ahead metric as a colored dot.
- The four metrics appear before the compact Next Flight card.
- Next Flight was rebuilt into a compact heading/time + flight/route row rather than a paragraph of mixed information.
- The map-only control uses a clearer expand/show-interface symbol.
- Leaflet's default attribution box is removed. Required map/weather provider credits are retained in one compact unobtrusive source line.

## Flight history comparison

History is now date-centered rather than trip-card-centered. Every date has two parallel lanes:

- **Actual / Current** — the schedule currently considered yours, including tracked actual status;
- **Initial Schedule** — the immutable PDF-awarded baseline saved in the `awarded_*` fields.

This keeps the original bid/award visible even after UPS Work Schedule sync replaces or removes overlapping current legs. The older per-trip maps and delete tools remain under a collapsed **Saved trip tools** section.

## Authoritative schedule removal

A schedule sync can now mark itself `coverage_complete=true`. The Edge extension only does this after a Work Schedule calendar snapshot and matching Time Detail snapshot prove the entire pay-period flight list was captured.

For a complete coverage sync, a flight that is no longer present in UPS may be removed from Actual / Current even if it previously acquired FlightAware actuals or track data. Provider state, saved positions, generated logbook copy, and archived weather for that removed actual leg are discarded. If the leg originated from the PDF award, its immutable awarded baseline remains visible in the Initial Schedule history lane.

A partial/date-forward Time Detail sync is deliberately not allowed to erase a previously tracked historical leg.

## Edge extension v4

The normal workflow is now:

1. User opens a supported UPS page manually.
2. User explicitly presses **Sync**.
3. If enabled, the extension attaches Edge's debugger interface briefly to that one active tab, sends `Ctrl+A`, `Ctrl+C`, then `Escape`, detaches immediately, and parses the copied Zscaler text.
4. If that user-triggered text capture is unavailable, it falls back to visible-tab OCR.
5. If OCR is also uncertain, it asks for the manual Ctrl+A / Ctrl+C fallback.

The extension does not perform this in the background. It does not navigate UPS, search/book/cancel jumpseats, submit forms, automate login/MFA, read UPS cookies, or poll UPS endpoints. The one-click text-capture option can be disabled in extension settings.

Because the `debugger` permission is a broader browser permission and managed Edge policy can restrict or report it, no claim is made that a company-managed browser will permit it. When disabled or blocked, the OCR/manual fallback remains available.

## Weather replay

The richer v27 archive remains: up to five replay snapshots on normal flights and up to seven on flights planned for eight hours or more, still bounded by `WEATHER_ARCHIVE_MAX_MB`.
