# Skydio Media Transfer

Portable Windows application to download media from Skydio Cloud to a local folder, organized by date.

## Download

Grab the latest `SkydioTransfer.exe` from the [Releases](../../releases) page.

No installation required. Just run the `.exe`.

## Setup

1. Create a custom integration in [Skydio Cloud](https://cloud.skydio.com) under **Settings > Integrations**
2. Generate an API Token and note both the **token** and the **token ID**
3. Run `SkydioTransfer.exe`
4. Paste your API Token and Token ID, then click **Save**

## Usage

1. Optionally set a date range to filter media
2. Click **Fetch Media** to load your media list
3. Select files in the list (click, Ctrl+click, or **Select All**)
4. Choose an output folder with **Browse**
5. Click **Add Selected to Queue** — downloads start in the background
6. Keep browsing and adding more files while the queue runs

Files are saved in date subfolders: `OutputFolder/2024-03-15/photo.jpg`

Already-downloaded files (matching name and size) are automatically skipped.

### Queue Features

- **Auto-retry** — failed downloads retry up to 3 times automatically
- **Retry Failed** — re-queue all failed/cancelled items with one click
- **Cancel Current** — stops the active download immediately
- **Clear Completed** / **Clear All** — manage the queue list
- Column headers in the media list are sortable (click to toggle)

## Saving to SharePoint

You can download media directly into a SharePoint document library by using a locally synced folder.

1. Open the SharePoint document library in your browser
2. Click **Sync** to sync the library to your computer via OneDrive for Business
3. In Skydio Media Transfer, click **Browse** and select the synced folder (typically under `C:\Users\<you>\OneDrive - <org>\...`)
4. Downloaded files will automatically sync to SharePoint via OneDrive

**Tip:** Enable **Files On-Demand** in OneDrive settings to automatically free up local disk space after files sync to the cloud. Your local machine acts as a temporary pass-through — files remain accessible in SharePoint without taking up local storage. You can also right-click synced files in File Explorer and choose **Free up space** to manually reclaim disk space.

## Building from Source

Requires Python 3.10+:

```bash
pip install -r requirements.txt
pyinstaller --onefile --windowed --name SkydioTransfer skydio_transfer.py
```

The `.exe` will be in the `dist/` folder.

Alternatively, push to GitHub and the Actions workflow builds it automatically.

## Disclaimer

This is an independent personal project built with the assistance of [Claude](https://claude.ai). **This project is not affiliated with, endorsed by, or associated with Skydio, Inc. in any way.** Use of this software is at your own risk.

This application is not intended for use on government systems or networks.

## Privacy

This application does not collect, transmit, or store any data externally. Your API token is stored in **Windows Credential Manager** (under the `SkydioMediaTransfer` service); other settings live in a local `config.json` file next to the `.exe`. Neither is uploaded anywhere. All communication is directly between your computer and the official Skydio Cloud API.

A rotating log file is written to `%LOCALAPPDATA%\SkydioTransfer\app.log` to help diagnose issues. Log entries never contain your API token.

If you previously used a version that stored the token in `config.json`, it will be migrated to Credential Manager on first launch.
