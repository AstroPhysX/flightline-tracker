# Flightline Tracker UPS Sync — Edge extension v3

The extension stays deliberately **user-triggered**. It does not log in to UPS, handle MFA, click UPS controls, search/book/cancel jumpseats, submit forms, read UPS cookies, or poll UPS in the background.

## What v3 adds

- much smaller normal popup — one Sync button plus a hidden ⚙ settings panel;
- brief Edge notification/badge when a supported UPS page is detected;
- Work Schedule / Time Detail sync using the existing screen-capture-first, copied-text-fallback workflow;
- confirmed Jumpseat screen sync, imported into Flightline Tracker as **deadhead (DH)** legs;
- DFW is the default jumpseat filter; turn off **Only import confirmed jumpseats touching this airport** to import all confirmed rows shown;
- a **Test connection** button for the tracker URL/token;
- store-ready packaging via `.github/workflows/package-edge-extension.yml`.

## Schedule sync

1. Sign in to UPS normally.
2. Open Work Schedule and click the first scheduled flight/date so Time Detail shows the full period from its first leg onward.
3. Open Flightline Sync and press **Sync schedule**.
4. The extension captures the visible tab only after your click. Your self-hosted tracker performs in-memory OCR.
5. If the entire Time Detail table is not visible or OCR is uncertain, nothing is changed and the popup asks you to use Ctrl+A / Ctrl+C, then **Sync copied text**.

## Jumpseat sync

On the **Crew Jumpseat** page, press **Sync jumpseats**. Only the **Upcoming Jumpseats Confirmed** table is read. Search Flights, Autobook, View, standby booking and other controls are not touched.

By default, only confirmed jumpseats whose origin or destination is `DFW` are imported. They are added to the nearest current trip when they clearly position into/out of that trip; otherwise a separate jumpseat trip is created. All imported jumpseats are marked DH.

## First-time configuration

Open ⚙ and set:

- Flightline Tracker URL
- the same `SCHEDULE_SYNC_TOKEN` configured in Docker/Portainer
- Jumpseat home airport (default `DFW`)

## Desktop Edge installation for testing

1. Open `edge://extensions`.
2. Turn on Developer mode.
3. Click **Load unpacked**.
4. Select this `edge-extension` folder.

If Developer mode is disabled/reset by browser or company policy, the extension cannot force it back on. For a persistent normal installation, publish the extension to Microsoft Edge Add-ons (a hidden/unlisted listing is appropriate) and install it from that listing instead of using Load unpacked. The GitHub packaging workflow produces the ZIP to upload.

## Permissions

- `activeTab` — one-time visible-tab capture after you click Sync;
- `clipboardRead` — explicit fallback only;
- `storage` — settings and short-lived fallback state;
- `tabs` — recognizes supported Work Schedule / Jumpseat tabs and changes the extension badge;
- `notifications` — brief “ready to sync” notification when a supported page is recognized;
- tracker host permission is requested only for the tracker URL you configure.

There is no `debugger`, `cookies`, UPS/Zscaler host permission, persistent content script, or automated UPS request.

## Company iPad

The desktop extension cannot normally be sideloaded by the user into Edge on a company-managed iPad. It would require organizational deployment/support. The tracker remains usable there without the extension.
