# Flightline Tracker UPS Schedule Sync — Edge extension v2

This extension is intentionally **user-triggered only**.

It does **not** automate UPS login, Microsoft Authenticator, Zscaler navigation, jumpseat requests, schedule-adjustment controls, form submissions, cookies, or background polling. Nothing happens until you click **Sync current page**.

## Normal use

1. Sign in to UPS normally and open **Work Schedule**.
2. Click the **first scheduled flight/date** so the **Time Detail** page shows the pay period from its first flight onward.
3. Click the Flightline Sync extension icon.
4. Click **Sync current page**.

The extension captures only the currently visible tab after that click and sends the image to **your configured self-hosted Flightline Tracker**. The tracker performs in-memory OCR, returns recognized text to the extension, and does not store the screenshot. The extension parses/validates that text and only then sends normalized schedule records to the normal schedule-sync endpoint.

### Automatic copied-text fallback

The screen path is deliberately conservative. If OCR is unclear, a flight row cannot be parsed safely, or the entire Time Detail table does not fit in one visible screen, **no schedule change is made**.

The popup changes to **Sync copied text** and tells you to:

1. Stay on UPS Time Detail.
2. Press **Ctrl+A**, then **Ctrl+C**.
3. Reopen the extension.
4. Click **Sync copied text**.

The fallback state is remembered for about ten minutes so closing the popup to copy the page does not lose it.

The older two-step clipboard workflow is also still supported: copy the calendar first to stage its exact flight list, then copy Time Detail from the first scheduled entry. This is optional and mainly useful for troubleshooting.

## Why the screen path sometimes falls back

`chrome.tabs.captureVisibleTab()` captures the visible browser viewport, not an arbitrarily long off-screen page. Flightline Tracker therefore requires both the Time Detail header and a bottom/footer marker before treating a screen capture as complete. If the table is longer than one screen, copied text is safer because Ctrl+A / Ctrl+C includes the whole remote page.

## First-time configuration

Set:

- **Flightline Tracker URL** — for example `https://tracker.example.com`
- **Schedule sync token** — the same random value configured as `SCHEDULE_SYNC_TOKEN` in the tracker container/stack

The token is stored only in this Edge profile using extension local storage.

## Install in desktop Microsoft Edge

1. Open `edge://extensions`.
2. Turn on **Developer mode**.
3. Click **Load unpacked**.
4. Select this `edge-extension` folder (the folder containing `manifest.json`).
5. Pin **Flightline Tracker UPS Schedule Sync** from Edge's Extensions menu if desired.

After updating the extension files from GitHub, return to `edge://extensions` and click **Reload** on its card.

If company policy disables extension Developer mode or blocks unpacked extensions, do not bypass that policy.

## Permissions

- `activeTab`: used only after you invoke the extension; validates the Work Schedule tab and permits the one-time visible-tab capture.
- `clipboardRead`: used only for the explicit fallback after you copy the page.
- `storage`: saves the tracker URL/token and short-lived fallback state.
- Tracker host access is requested only for the URL you configure.

There is deliberately no `debugger`, `cookies`, UPS/Zscaler host permission, persistent content script, or background polling permission.

## Privacy

The full signed Zscaler URL is never included in the normalized payload. The screenshot endpoint processes the image in memory and does not archive it. Normalized schedule snapshots are retained by Flightline Tracker under `/data/schedule_sync` according to the tracker's normal sync history behavior.

## Company iPad

The unpacked Edge extension is for desktop Edge. A company-managed iPad generally cannot have this user-sideloaded extension unless the company's administrators deploy it. The copied-text/manual tracker workflow remains the practical fallback on that device.
