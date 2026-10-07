# Flightline Tracker UPS Sync — Edge extension v4

The extension is user-triggered. It does not log in to UPS, handle MFA, navigate UPS, search/book/cancel jumpseats, use Autobook, submit forms, read UPS cookies, or poll UPS in the background.

## Normal interface

The popup stays intentionally small: page status, one Sync button, last result, and a ⚙ settings drawer. A brief notification and `SYNC` badge can appear when Edge recognizes a supported Work Schedule / Time Detail / Crew Jumpseat tab. Detection alone does not copy or transmit the page.

## One-click schedule text capture

The default setting **One-click text capture** is designed for Zscaler Browser Isolation, where ordinary DOM scraping cannot see the remote UPS table.

After — and only after — you press Sync, the extension briefly attaches Edge's debugger API to the active supported tab, sends Ctrl+A then Ctrl+C, sends Escape to clear selection, and detaches. It then parses the clipboard text locally and sends only normalized schedule/jumpseat data to your Flightline Tracker.

Fallback order:

1. user-triggered one-click text capture;
2. visible-tab screenshot + OCR on your own Flightline Tracker;
3. manual Ctrl+A / Ctrl+C if both automated methods are unavailable or uncertain.

You can disable **One-click text capture** in ⚙ at any time. This removes debugger use from the normal sync path, though the permission remains declared in the installed build.

Managed Edge/browser policy may block or warn about the debugger permission. The extension cannot and should not bypass those controls.

## Complete schedule coverage

For safest removals, first press Sync while on the Work Schedule calendar. That stores the calendar flight list locally; it does not change Flightline Tracker yet. Then manually open Time Detail from the first scheduled date and press Sync again.

When the calendar and Time Detail lists agree, the extension marks the payload as complete pay-period coverage. This allows Flightline Tracker to remove a leg that UPS no longer lists, including a previously tracked leg, while keeping the immutable PDF-awarded baseline for history comparison.

A Time Detail page opened from a later date is treated as partial and cannot erase older tracked history.

## Jumpseat sync

On **Crew Jumpseat**, press Sync. Only **Upcoming Jumpseats Confirmed** rows are parsed. Imported jumpseats are marked **DH**.

By default, only confirmed jumpseats whose origin or destination is `DFW` are imported. Change the airport or turn off the filter in ⚙ if desired. Search Flights, View, Autobook and booking controls are not touched.

## First-time configuration

Open ⚙ and set:

- Flightline Tracker URL;
- the same `SCHEDULE_SYNC_TOKEN` configured in Docker/Portainer;
- jumpseat home airport (default `DFW`);
- optional one-click text capture toggle.

Use **Test connection** to verify the URL and token without changing a schedule.

## Desktop Edge installation for testing

1. Open `edge://extensions`.
2. Turn on Developer mode.
3. Click **Load unpacked**.
4. Select this `edge-extension` folder.

An unpacked extension cannot force Developer mode to remain enabled when Edge or company policy disables/resets it. For a persistent normal installation, publish it to Microsoft Edge Add-ons as a hidden/unlisted extension and install from that listing.

## Permissions

- `activeTab` — visible-tab capture after Sync;
- `clipboardRead` — reads the result of the user-triggered copy or manual fallback;
- `storage` — settings/calendar snapshot/fallback state;
- `tabs` — recognizes supported tabs and updates badge;
- `notifications` — brief ready-to-sync notice;
- `debugger` — only for the explicit one-click Ctrl+A / Ctrl+C operation;
- tracker host permission — requested only for the Flightline Tracker URL you configure.

There is no cookies permission and no permanent UPS/Zscaler host permission.
