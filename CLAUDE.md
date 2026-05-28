# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project shape

Single-file Tk desktop app (`skydio_transfer.py`, ~1400 lines) that downloads media from the Skydio Cloud API to a local folder. Ships as a standalone Windows `.exe` built with PyInstaller. There is no application package — everything lives in `skydio_transfer.py` plus a `tests/` folder. Python 3.10+ (CI uses 3.12).

## Commands

```bash
pip install -r requirements-dev.txt           # installs runtime + pytest
pytest                                        # run the whole test suite
pytest tests/test_retry_policy.py             # one file
pytest tests/test_retry_policy.py::test_429_caps_delay_at_60_seconds   # one test
python skydio_transfer.py                     # run the GUI locally (needs a display)

# Build the Windows .exe (run on Windows):
pyinstaller --onefile --windowed --name SkydioTransfer --icon NONE \
            --collect-submodules keyring skydio_transfer.py
```

`--collect-submodules keyring` is **required** — `keyring` lazy-imports its Windows credential backend at runtime, and PyInstaller will otherwise miss it and the built `.exe` will fail to read/write credentials. Don't drop the flag.

CI (`.github/workflows/build.yml`) runs the tests and builds the `.exe` on `windows-latest` for every push to `main` or `v2`. Pushing a `v*` tag additionally creates a GitHub Release with the artifact attached.

## Architecture

### Pure-logic core, Tk shell

The codebase is deliberately split between pure functions (top of the file, easy to test) and a Tk class (`SkydioTransferApp`, bottom of the file, hard to test). When adding behavior, extract the decision logic into a module-level pure function first, then call it from the app. Existing examples of this pattern that already have tests:

- `retry_policy(exception, status_code, retry_after, attempt, max_retries)` — the single source of truth for HTTP retry/backoff. Used by both the listing client and the download loop. Pure; caller sleeps.
- `sanitize_windows_filename` — NTFS rules (illegal chars, reserved basenames like `CON`/`PRN`, trailing dots/spaces).
- `parse_captured_time` — Skydio timestamp → `(YYYY-MM-DD, HH:MM)`, robust to format drift.
- `local_date_to_utc_iso(date_str, *, end_of_day, tz)` — user types a local date, API wants UTC. Accepts an injectable `tz` so tests are deterministic across machines.
- `extract_legacy_credentials` / `has_legacy_credentials` — split a legacy `config.json` into secrets vs. non-secrets without mutating the input.
- `build_queue_item` / `api_from_item` — snapshot the API token onto each queue item at enqueue time (see threading note below).
- `ProgressThrottle` — rate-limits UI progress callbacks to `PROGRESS_INTERVAL_S` (0.25s) so the Tk main loop isn't flooded.

### HTTP client (`SkydioAPI`)

Wraps `requests`. Hits `https://api.skydio.com/api/v0`. Listing goes through `/media_files` directly (with `captured_since` / `captured_before` server-side date filtering and `MAX_LISTING_PAGES=1000` as a pagination-loop safeguard) — do not reintroduce the older per-flight iteration. All listing GETs go through `_get_with_retries`, which delegates to `retry_policy`. Downloads stream in `DOWNLOAD_CHUNK_BYTES` (64 KiB) chunks and honor a `cancel_check` callback that raises `_DownloadCancelled`.

### Queue worker and threading

`SkydioTransferApp` owns a single persistent daemon worker thread (`_queue_worker`) started in `__init__`. The worker blocks on `self._queue_pending.get()` (a `queue.Queue` used purely as a wake-up signal), then drains every item whose status is `"Queued"` by calling `_claim_next_queued_item()` under `self._queue_lock`. Cancellation flows through `self.cancel_requested` (a `threading.Event`) and is checked both between attempts and inside the per-chunk download loop.

Two thread-safety rules — do not break them:

1. **All Tk widget writes from the worker thread must go through `self.root.after(0, ...)`.** The helpers `_set_status_safe` and `_update_queue_item_status` already do this; use them instead of touching widgets directly from background code.
2. **Queue items snapshot `api_token` / `token_id` at enqueue time** (`build_queue_item`). The user can edit credentials in the UI while downloads are in flight — the running item must keep using the token that was active when it was queued. Don't read `self.token_entry` from the worker.

### Persistence layout

Both `config.json` and `app.log` live **next to the executable** when frozen (`sys.frozen`) and next to the script otherwise — see `get_config_path()` and `log_dir()`. They are intentionally not in `%LOCALAPPDATA%` because this app is meant to be portable. If you add a new persisted file, follow the same pattern.

The API token and token ID are stored in **Windows Credential Manager** via `keyring` under service `SkydioMediaTransfer` (users `api_token`, `token_id`). `config.json` holds only non-secret settings (output folder, date subfolder toggle, etc.). `migrate_legacy_config()` runs on every launch and moves credentials out of any pre-existing `config.json` that still has them — it's a no-op once migrated. Never write the API token into `config.json` or into log messages.

### Logging

`setup_logging()` installs a `RotatingFileHandler` (5 MB × 3 backups) at `app.log` next to the exe, plus `sys.excepthook` and `threading.excepthook` hooks so crashes in either the main thread or any worker get captured. Use the module-level `LOGGER` (`logging.getLogger("skydio_transfer")`); don't `print`.

## Conventions worth keeping

- New retry/backoff behavior should extend `retry_policy` rather than add a parallel ladder, so listing and download stay consistent.
- Date strings crossing the API boundary go through `local_date_to_utc_iso`; don't hand-roll timezone math.
- Filenames written to disk go through `sanitize_windows_filename` — Skydio metadata can contain characters that NTFS rejects.
- When adding tests for time-, network-, or filesystem-dependent behavior, inject the dependency (see `tz=` on `local_date_to_utc_iso`, `now=` on `ProgressThrottle.tick`, `monkeypatch` on `sys.frozen` in `test_log_dir.py`) rather than mocking globals.
- `tests/conftest.py` puts the repo root on `sys.path` so tests can `from skydio_transfer import ...` directly — no package install needed.
