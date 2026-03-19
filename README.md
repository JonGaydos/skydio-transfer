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

## Building from Source

Requires Python 3.10+:

```bash
pip install -r requirements.txt
pyinstaller --onefile --windowed --name SkydioTransfer skydio_transfer.py
```

The `.exe` will be in the `dist/` folder.

Alternatively, push to GitHub and the Actions workflow builds it automatically.
