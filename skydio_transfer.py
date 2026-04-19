"""
Skydio Media Transfer - Portable Windows Application
Downloads media from Skydio Cloud to a local folder, organized by date.
"""

import calendar
import json
import logging
import logging.handlers
import os
import queue
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, date as date_type, time as dtime, timezone
from pathlib import Path

import requests

LOGGER = logging.getLogger("skydio_transfer")

# ──────────────────────────────────────────────
# Configuration Manager
# ──────────────────────────────────────────────

def get_config_path():
    """Config file lives next to the executable (or script)."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return base / "config.json"


def load_config():
    path = get_config_path()
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_config(data):
    path = get_config_path()
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ──────────────────────────────────────────────
# Skydio API Client
# ──────────────────────────────────────────────

BASE_URL = "https://api.skydio.com/api/v0"
FONT_FAMILY = "Segoe UI"
DATE_PLACEHOLDER = "All dates"
MAX_LISTING_PAGES = 1000
LISTING_MAX_RETRIES = 3


class _DownloadCancelled(Exception):
    """Raised when a download is cancelled mid-stream."""


def local_date_to_utc_iso(date_str, *, end_of_day=False, tz=None):
    """Convert YYYY-MM-DD in a local timezone to a UTC ISO-8601 timestamp with 'Z' suffix.

    `tz=None` means the system's local timezone (production use).
    Tests inject a fixed tz so results are deterministic across machines.
    """
    d = date_type.fromisoformat(date_str)
    t = dtime(23, 59, 59) if end_of_day else dtime(0, 0, 0)
    local_dt = datetime.combine(d, t, tzinfo=tz) if tz is not None else datetime.combine(d, t).astimezone()
    utc_dt = local_dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def retry_policy(exception, status_code, retry_after, attempt, max_retries):
    """Decide whether to retry an HTTP attempt and how long to wait.

    Pure function — no side effects. Caller sleeps `delay` seconds before retry.
    """
    if attempt >= max_retries:
        return False, 0.0

    backoff = float(min(2 ** attempt, 60))

    if exception is not None:
        return True, backoff

    if status_code == 429:
        header_delay = 0.0
        if retry_after is not None:
            try:
                header_delay = float(retry_after)
            except (TypeError, ValueError):
                header_delay = 0.0
        return True, min(max(header_delay, backoff), 60.0)

    if status_code is not None and status_code >= 500:
        return True, backoff

    return False, 0.0


def build_queue_item(media, output_folder, use_subfolders, api_token, token_id, q_id):
    return {
        "q_id": str(q_id),
        "uuid": media["uuid"],
        "filename": media["filename"],
        "date": media["date"],
        "size": media["size"],
        "download_url": media.get("download_url", ""),
        "output_folder": output_folder,
        "use_date_subfolders": use_subfolders,
        "api_token": api_token,
        "token_id": token_id,
        "status": "Queued",
    }


def api_from_item(item):
    return SkydioAPI(item["api_token"], item.get("token_id", ""))


class ProgressThrottle:
    def __init__(self, min_interval=0.25):
        self.min_interval = min_interval
        self.last_emit = None

    def tick(self, now):
        if self.last_emit is None or now - self.last_emit >= self.min_interval:
            self.last_emit = now
            return True
        return False


class SkydioAPI:
    def __init__(self, api_token, token_id=""):
        self.api_token = api_token
        self.token_id = token_id

    def _headers(self):
        h = {
            "Accept": "application/json",
            "Authorization": f"ApiToken {self.api_token}",
        }
        if self.token_id:
            h["X-Api-Token-Id"] = self.token_id
        return h

    def _get_with_retries(self, url, params, timeout):
        """GET with retry_policy. Sleeps between attempts. Raises on terminal failure."""
        attempt = 0
        while True:
            exc = None
            resp = None
            try:
                resp = requests.get(url, headers=self._headers(), params=params, timeout=timeout)
            except requests.exceptions.RequestException as e:
                exc = e

            status = resp.status_code if resp is not None else None
            retry_after = resp.headers.get("Retry-After") if resp is not None else None

            should_retry, delay = retry_policy(
                exception=exc,
                status_code=status,
                retry_after=retry_after,
                attempt=attempt,
                max_retries=LISTING_MAX_RETRIES,
            )

            if should_retry:
                LOGGER.warning(
                    "Retrying GET (attempt=%d status=%s exc=%s wait=%.1fs)",
                    attempt, status, type(exc).__name__ if exc else None, delay,
                )
                time.sleep(delay)
                attempt += 1
                continue

            if exc is not None:
                raise exc
            resp.raise_for_status()
            return resp

    def get_media(self, date_from=None, date_to=None, progress_callback=None):
        """Fetch media files directly, filtered by date range. Handles pagination.

        Uses /media_files endpoint with captured_since / captured_before params.
        This is far more efficient than fetching flights first.
        """
        all_files = []
        page = 1

        while True:
            if page > MAX_LISTING_PAGES:
                LOGGER.error(
                    "Listing aborted: exceeded MAX_LISTING_PAGES=%d (server pagination loop?)",
                    MAX_LISTING_PAGES,
                )
                break

            params = {"per_page": 500, "page_number": page}
            if date_from:
                params["captured_since"] = date_from
            if date_to:
                params["captured_before"] = date_to

            resp = self._get_with_retries(
                f"{BASE_URL}/media_files", params=params, timeout=180,
            )
            data = resp.json().get("data", {})
            files = data.get("files", [])
            pagination = data.get("pagination", {})

            all_files.extend(files)

            current = pagination.get("current_page", page)
            total_pages = pagination.get("total_pages", 1)

            if progress_callback:
                progress_callback(current, total_pages, len(all_files))

            if current >= total_pages:
                break
            page += 1

        return all_files

    def download_file(self, download_url, dest_path, progress_callback=None, cancel_check=None):
        """Download a media file using its direct download URL."""
        resp = requests.get(
            download_url, headers=self._headers(), stream=True, timeout=120
        )
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if cancel_check and cancel_check():
                    raise _DownloadCancelled()
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    progress_callback(downloaded, total)

    def download_file_by_uuid(self, file_uuid, dest_path, progress_callback=None, cancel_check=None):
        """Download a media file by UUID (fallback method)."""
        url = f"{BASE_URL}/media/download/{file_uuid}"
        self.download_file(url, dest_path, progress_callback, cancel_check)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def format_size(size_bytes):
    if size_bytes is None or size_bytes == 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ──────────────────────────────────────────────
# Calendar Popup (pure tkinter, no dependencies)
# ──────────────────────────────────────────────

class CalendarPopup(tk.Toplevel):
    """A month-view calendar popup for picking a date."""

    def __init__(self, parent, callback, initial_date=None):
        super().__init__(parent)
        self.callback = callback
        self.transient(parent)
        self.grab_set()
        self.title("Pick a Date")
        self.resizable(False, False)

        today = initial_date or date_type.today()
        self.year = today.year
        self.month = today.month

        self._build()
        self._center_on_parent(parent)

    def _center_on_parent(self, parent):
        self.update_idletasks()
        pw = parent.winfo_rootx()
        ph = parent.winfo_rooty()
        px = parent.winfo_width()
        py = parent.winfo_height()
        w = self.winfo_width()
        h = self.winfo_height()
        x = pw + (px - w) // 2
        y = ph + (py - h) // 2
        self.geometry(f"+{x}+{y}")

    def _build(self):
        self.configure(padx=8, pady=8)

        # Navigation row
        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, pady=(0, 6))

        ttk.Button(nav, text="<<", width=3, command=self._prev_year).pack(side=tk.LEFT)
        ttk.Button(nav, text="<", width=3, command=self._prev_month).pack(side=tk.LEFT, padx=2)

        self.header_label = ttk.Label(nav, text="", font=(FONT_FAMILY, 10, "bold"), anchor=tk.CENTER)
        self.header_label.pack(side=tk.LEFT, expand=True, fill=tk.X)

        ttk.Button(nav, text=">", width=3, command=self._next_month).pack(side=tk.RIGHT, padx=2)
        ttk.Button(nav, text=">>", width=3, command=self._next_year).pack(side=tk.RIGHT)

        # Day-of-week headers
        dow_frame = ttk.Frame(self)
        dow_frame.pack(fill=tk.X)
        for day_name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            lbl = ttk.Label(dow_frame, text=day_name, width=5, anchor=tk.CENTER,
                            font=(FONT_FAMILY, 9, "bold"))
            lbl.pack(side=tk.LEFT, padx=1)

        # Day grid
        self.day_frame = ttk.Frame(self)
        self.day_frame.pack(fill=tk.BOTH, expand=True)

        # Today button
        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(bottom, text="Today", command=self._pick_today).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Clear", command=self._clear).pack(side=tk.RIGHT)

        self._draw_month()

    def _draw_month(self):
        for widget in self.day_frame.winfo_children():
            widget.destroy()

        self.header_label.config(
            text=f"{calendar.month_name[self.month]} {self.year}"
        )

        today = date_type.today()
        cal = calendar.monthcalendar(self.year, self.month)

        for week in cal:
            row_frame = ttk.Frame(self.day_frame)
            row_frame.pack(fill=tk.X)
            for day in week:
                self._create_day_cell(row_frame, day, today)

    def _create_day_cell(self, row_frame, day, today):
        """Create a single day cell (blank label or clickable button) in the calendar grid."""
        if day == 0:
            ttk.Label(row_frame, text="", width=5).pack(side=tk.LEFT, padx=1, pady=1)
            return

        is_today = (day == today.day and self.month == today.month and self.year == today.year)
        tk.Button(
            row_frame, text=str(day), width=4,
            relief=tk.SOLID if is_today else tk.FLAT,
            bg="#e0e8ff" if is_today else "#f0f0f0",
            activebackground="#c0d0ff",
            font=(FONT_FAMILY, 9, "bold" if is_today else "normal"),
            command=lambda d=day: self._pick_day(d),
        ).pack(side=tk.LEFT, padx=1, pady=1)

    def _prev_month(self):
        if self.month == 1:
            self.month = 12
            self.year -= 1
        else:
            self.month -= 1
        self._draw_month()

    def _next_month(self):
        if self.month == 12:
            self.month = 1
            self.year += 1
        else:
            self.month += 1
        self._draw_month()

    def _prev_year(self):
        self.year -= 1
        self._draw_month()

    def _next_year(self):
        self.year += 1
        self._draw_month()

    def _pick_day(self, day):
        picked = date_type(self.year, self.month, day)
        self.callback(picked.isoformat())
        self.destroy()

    def _pick_today(self):
        picked = date_type.today()
        self.callback(picked.isoformat())
        self.destroy()

    def _clear(self):
        self.callback("")
        self.destroy()


# ──────────────────────────────────────────────
# Date Entry Widget (text entry + calendar button)
# ──────────────────────────────────────────────

class DateEntry(ttk.Frame):
    """A date field with a text entry and a calendar popup button."""

    def __init__(self, parent, placeholder=DATE_PLACEHOLDER, on_change=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.placeholder = placeholder
        self.on_change = on_change
        self._has_focus = False

        self.entry = ttk.Entry(self, width=14)
        self.entry.pack(side=tk.LEFT)

        self.cal_btn = ttk.Button(self, text="\U0001f4c5", width=3, command=self._open_calendar)
        self.cal_btn.pack(side=tk.LEFT, padx=(2, 0))

        # Placeholder behavior
        self._show_placeholder()
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<KeyRelease>", self._on_key)

    def _show_placeholder(self):
        if not self.entry.get():
            self.entry.insert(0, self.placeholder)
            self.entry.config(foreground="gray")

    def _on_focus_in(self, event):
        self._has_focus = True
        if self.entry.get() == self.placeholder:
            self.entry.delete(0, tk.END)
            self.entry.config(foreground="black")

    def _on_focus_out(self, event):
        self._has_focus = False
        if not self.entry.get().strip():
            self.entry.delete(0, tk.END)
            self._show_placeholder()
            if self.on_change:
                self.on_change()

    def _on_key(self, event):
        if self.on_change:
            self.on_change()

    def _open_calendar(self):
        # Try to parse current value as initial date
        initial = None
        val = self.get()
        if val:
            try:
                initial = date_type.fromisoformat(val)
            except ValueError:
                pass
        CalendarPopup(self.winfo_toplevel(), self._calendar_callback, initial)

    def _calendar_callback(self, date_str):
        self.entry.delete(0, tk.END)
        if date_str:
            self.entry.config(foreground="black")
            self.entry.insert(0, date_str)
        else:
            self._show_placeholder()
        if self.on_change:
            self.on_change()

    def get(self):
        """Return the date string, or empty string if placeholder/empty."""
        val = self.entry.get().strip()
        if val == self.placeholder:
            return ""
        return val

    def set(self, value):
        self.entry.delete(0, tk.END)
        if value:
            self.entry.config(foreground="black")
            self.entry.insert(0, value)
        else:
            self._show_placeholder()


# ──────────────────────────────────────────────
# GUI Application
# ──────────────────────────────────────────────

class SkydioTransferApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Skydio Media Transfer")
        self.root.geometry("800x900")
        self.root.minsize(650, 700)
        self.root.resizable(True, True)

        # Data stores
        self.all_media = []       # list of dicts with media info

        # Download queue: list of dicts with media info + "output_folder" + "status"
        self.download_queue = []       # ordered list of queue items
        self._queue_lock = threading.Lock()
        self._queue_pending = queue.Queue()  # signals worker that new items exist
        self.cancel_requested = False
        self._queue_counter = 0  # unique id for each queue item

        self._build_ui()
        self._load_saved_config()

        # Start the persistent queue worker thread
        self._worker_thread = threading.Thread(target=self._queue_worker, daemon=True)
        self._worker_thread.start()

    # ── UI Construction ──

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- Settings Frame ---
        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding=10)
        settings_frame.pack(fill=tk.X, **pad)

        ttk.Label(settings_frame, text="API Token:").grid(row=0, column=0, sticky=tk.W)
        self.token_entry = ttk.Entry(settings_frame, show="*", width=50)
        self.token_entry.grid(row=0, column=1, sticky=tk.EW, padx=(4, 4))

        self.show_token_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame, text="Show", variable=self.show_token_var,
            command=self._toggle_token_visibility
        ).grid(row=0, column=2)

        ttk.Label(settings_frame, text="Token ID:").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        self.token_id_entry = ttk.Entry(settings_frame, width=50)
        self.token_id_entry.grid(row=1, column=1, sticky=tk.EW, padx=(4, 4), pady=(4, 0))

        ttk.Button(settings_frame, text="Save", command=self._save_credentials).grid(
            row=1, column=2, pady=(4, 0)
        )

        settings_frame.columnconfigure(1, weight=1)

        # --- Filter Frame ---
        filter_frame = ttk.LabelFrame(self.root, text="Filter & Fetch", padding=10)
        filter_frame.pack(fill=tk.X, **pad)

        filter_row = ttk.Frame(filter_frame)
        filter_row.pack(fill=tk.X)

        ttk.Label(filter_row, text="Date From:").pack(side=tk.LEFT)
        self.date_from = DateEntry(filter_row, placeholder=DATE_PLACEHOLDER)
        self.date_from.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(filter_row, text="Date To:").pack(side=tk.LEFT)
        self.date_to = DateEntry(filter_row, placeholder=DATE_PLACEHOLDER)
        self.date_to.pack(side=tk.LEFT, padx=(4, 12))

        self.fetch_btn = ttk.Button(filter_row, text="Fetch Media", command=self._fetch_all)
        self.fetch_btn.pack(side=tk.LEFT, padx=(8, 0))

        # --- Media List Frame ---
        media_frame = ttk.LabelFrame(self.root, text="Media Files", padding=10)
        media_frame.pack(fill=tk.BOTH, expand=True, **pad)

        # Treeview with columns
        columns = ("filename", "date", "time", "type", "size")
        self.tree = ttk.Treeview(media_frame, columns=columns, show="headings", selectmode="extended")

        self.tree.heading("filename", text="Filename", command=lambda: self._sort_column("filename"))
        self.tree.heading("date", text="Date", command=lambda: self._sort_column("date"))
        self.tree.heading("time", text="Time", command=lambda: self._sort_column("time"))
        self.tree.heading("type", text="Type", command=lambda: self._sort_column("type"))
        self.tree.heading("size", text="Size", command=lambda: self._sort_column("size"))

        self.tree.column("filename", width=260, minwidth=120)
        self.tree.column("date", width=100, minwidth=80)
        self.tree.column("time", width=60, minwidth=50)
        self.tree.column("type", width=70, minwidth=50)
        self.tree.column("size", width=80, minwidth=60, anchor=tk.E)

        tree_scroll = ttk.Scrollbar(media_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._sort_reverse = {}  # track sort direction per column

        # Select / Deselect buttons
        media_frame_bottom = ttk.Frame(self.root)
        media_frame_bottom.pack(fill=tk.X, padx=8)

        ttk.Button(media_frame_bottom, text="Select All", command=self._select_all).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(media_frame_bottom, text="Deselect All", command=self._deselect_all).pack(side=tk.LEFT)

        self.media_count_label = ttk.Label(media_frame_bottom, text="")
        self.media_count_label.pack(side=tk.RIGHT)

        # --- Add to Queue Frame ---
        add_frame = ttk.LabelFrame(self.root, text="Add to Download Queue", padding=10)
        add_frame.pack(fill=tk.X, **pad)

        folder_row = ttk.Frame(add_frame)
        folder_row.pack(fill=tk.X)

        ttk.Label(folder_row, text="Output Folder:").pack(side=tk.LEFT)
        self.output_entry = ttk.Entry(folder_row, width=45)
        self.output_entry.pack(side=tk.LEFT, padx=(4, 4), fill=tk.X, expand=True)
        ttk.Button(folder_row, text="Browse", command=self._browse_folder).pack(side=tk.LEFT)

        options_row = ttk.Frame(add_frame)
        options_row.pack(fill=tk.X, pady=(4, 0))

        self.use_date_subfolders = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_row, text="Organize into date subfolders",
            variable=self.use_date_subfolders
        ).pack(side=tk.LEFT)

        btn_row = ttk.Frame(add_frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))

        self.add_queue_btn = ttk.Button(btn_row, text="Add Selected to Queue", command=self._add_to_queue)
        self.add_queue_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- Download Queue Frame ---
        queue_frame = ttk.LabelFrame(self.root, text="Download Queue", padding=10)
        queue_frame.pack(fill=tk.BOTH, expand=True, **pad)

        # Queue Treeview
        q_columns = ("filename", "status", "destination")
        self.queue_tree = ttk.Treeview(queue_frame, columns=q_columns, show="headings",
                                       selectmode="extended", height=6)

        self.queue_tree.heading("filename", text="Filename")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.heading("destination", text="Destination")

        self.queue_tree.column("filename", width=250, minwidth=120)
        self.queue_tree.column("status", width=100, minwidth=70)
        self.queue_tree.column("destination", width=250, minwidth=100)

        q_scroll = ttk.Scrollbar(queue_frame, orient=tk.VERTICAL, command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=q_scroll.set)

        self.queue_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        q_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Queue control row
        queue_ctrl = ttk.Frame(self.root)
        queue_ctrl.pack(fill=tk.X, padx=8)

        self.cancel_btn = ttk.Button(queue_ctrl, text="Cancel Current", command=self._cancel_download)
        self.cancel_btn.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(queue_ctrl, text="Retry Failed", command=self._retry_failed).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(queue_ctrl, text="Clear Completed", command=self._clear_completed).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(queue_ctrl, text="Clear All", command=self._clear_all_queue).pack(side=tk.LEFT)

        self.queue_count_label = ttk.Label(queue_ctrl, text="Queue: 0 items")
        self.queue_count_label.pack(side=tk.RIGHT)

        # Progress bar and status
        progress_frame = ttk.Frame(self.root)
        progress_frame.pack(fill=tk.X, padx=8, pady=(4, 8))

        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=(0, 4))

        self.status_label = ttk.Label(progress_frame, text="Ready.", anchor=tk.W)
        self.status_label.pack(fill=tk.X)

    # ── Token Visibility ──

    def _toggle_token_visibility(self):
        self.token_entry.config(show="" if self.show_token_var.get() else "*")

    # ── Config Persistence ──

    def _load_saved_config(self):
        cfg = load_config()
        if cfg.get("api_token"):
            self.token_entry.insert(0, cfg["api_token"])
        if cfg.get("token_id"):
            self.token_id_entry.insert(0, cfg["token_id"])
        if cfg.get("output_folder"):
            self.output_entry.insert(0, cfg["output_folder"])

    def _save_credentials(self):
        cfg = load_config()
        cfg["api_token"] = self.token_entry.get().strip()
        cfg["token_id"] = self.token_id_entry.get().strip()
        cfg["output_folder"] = self.output_entry.get().strip()
        save_config(cfg)
        self._set_status("Settings saved.")

    # ── API Client ──

    def _get_api(self):
        token = self.token_entry.get().strip()
        token_id = self.token_id_entry.get().strip()
        if not token:
            messagebox.showerror("Error", "Please enter your API Token.")
            return None
        return SkydioAPI(token, token_id)

    # ── Fetch Media Directly ──

    def _validate_date_input(self, label, value):
        """Validate a date string, showing an error dialog on failure. Returns True if valid."""
        if not value:
            return True
        try:
            date_type.fromisoformat(value)
            return True
        except ValueError:
            messagebox.showerror("Invalid Date", f"{label} '{value}' is not valid.\nUse YYYY-MM-DD format.")
            return False

    @staticmethod
    def _enrich_media(mf):
        """Convert a raw API media dict into an enriched display dict."""
        cap_time = mf.get("captured_time", "")
        if cap_time and len(cap_time) >= 16:
            m_date, m_time = cap_time[:10], cap_time[11:16]
        elif cap_time and len(cap_time) >= 10:
            m_date, m_time = cap_time[:10], "—"
        else:
            m_date, m_time = "unknown", "—"

        return {
            "uuid": mf.get("uuid", ""),
            "filename": mf.get("filename", f"media_{mf.get('uuid', '?')}"),
            "date": m_date,
            "time": m_time,
            "kind": mf.get("kind", "—"),
            "size": mf.get("size", 0),
            "size_display": format_size(mf.get("size", 0)),
            "download_url": mf.get("download_url", ""),
            "flight_id": mf.get("flight_id", ""),
        }

    def _fetch_all(self):
        api = self._get_api()
        if not api:
            return

        date_from = self.date_from.get()
        date_to = self.date_to.get()

        if not self._validate_date_input("Date From", date_from):
            return
        if not self._validate_date_input("Date To", date_to):
            return

        if not date_from and not date_to and not messagebox.askyesno(
            "No Date Filter",
            "No date range is set. This will fetch ALL media files,\n"
            "which can take a while with many files.\n\n"
            "Set a date range first to speed things up.\n\n"
            "Continue anyway?"
        ):
            return

        self._set_status("Fetching media files...")
        self.fetch_btn.config(state=tk.DISABLED)
        self.add_queue_btn.config(state=tk.DISABLED)

        def worker():
            try:
                def on_page(current_page, total_pages, files_so_far):
                    self._set_status_safe(
                        f"Fetching media... page {current_page}/{total_pages} "
                        f"({files_so_far} files loaded)"
                    )

                # Convert user-entered local dates to UTC ISO-8601 so the
                # server-side filter matches what the user intended. Without
                # this, a user in EST asking for "2024-03-15" would miss
                # the first 5 hours of their local day.
                captured_since = local_date_to_utc_iso(date_from) if date_from else None
                captured_before = (
                    local_date_to_utc_iso(date_to, end_of_day=True) if date_to else None
                )

                media_files = api.get_media(
                    date_from=captured_since,
                    date_to=captured_before,
                    progress_callback=on_page,
                )

                enriched = [self._enrich_media(mf) for mf in media_files]
                self.root.after(0, lambda: self._populate_media(enriched))

            except requests.exceptions.HTTPError as e:
                err = e
                LOGGER.warning("Fetch HTTPError: status=%s", getattr(e.response, "status_code", "?"))
                self.root.after(0, lambda: self._handle_api_error(err))
            except Exception as e:
                LOGGER.exception("Fetch failed")
                msg = str(e)
                self.root.after(0, lambda: messagebox.showerror("Error", msg))
            finally:
                self.root.after(0, lambda: self.fetch_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.add_queue_btn.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_media(self, media_list):
        """Populate the treeview with fetched media."""
        self.all_media = media_list
        self._refresh_tree(media_list)
        dates_count = len({m["date"] for m in media_list if m["date"] != "unknown"})
        LOGGER.info("Fetch complete: %d files across %d dates", len(media_list), dates_count)
        self._set_status(f"Loaded {len(media_list)} media files across {dates_count} dates.")

    def _refresh_tree(self, media_list):
        """Clear and repopulate the Treeview."""
        self.tree.delete(*self.tree.get_children())

        for m in media_list:
            self.tree.insert("", tk.END, iid=m["uuid"], values=(
                m["filename"],
                m["date"],
                m["time"],
                m["kind"],
                m["size_display"],
            ))

        count = len(media_list)
        self.media_count_label.config(text=f"{count} files")

    # ── Column Sorting ──

    def _sort_column(self, col):
        """Sort treeview by clicking column headers."""
        reverse = self._sort_reverse.get(col, False)
        self._sort_reverse[col] = not reverse

        if col == "size":
            # Sort by raw byte size from media data, not the display string
            size_by_uuid = {m["uuid"]: m["size"] or 0 for m in self.all_media}
            items = [(size_by_uuid.get(iid, 0), iid) for iid in self.tree.get_children("")]
        else:
            items = [(self.tree.set(iid, col), iid) for iid in self.tree.get_children("")]
        items.sort(key=lambda x: x[0] if col == "size" else x[0].lower(), reverse=reverse)

        for index, (_, iid) in enumerate(items):
            self.tree.move(iid, "", index)

    # ── API Error Handler ──

    def _handle_api_error(self, error):
        resp = getattr(error, "response", None)
        if resp is not None and resp.status_code == 401:
            messagebox.showerror("Authentication Failed", "Invalid API token. Check your credentials.")
        elif resp is not None and resp.status_code == 403:
            messagebox.showerror("Access Denied", "Your token does not have permission for this action.")
        elif resp is not None and resp.status_code == 429:
            retry = resp.headers.get("Retry-After", "a moment")
            messagebox.showwarning("Rate Limited", f"Too many requests. Try again in {retry} seconds.")
        else:
            messagebox.showerror("API Error", str(error))
        self._set_status("Error.")

    # ── Select / Deselect ──

    def _select_all(self):
        children = self.tree.get_children()
        self.tree.selection_set(children)

    def _deselect_all(self):
        self.tree.selection_remove(*self.tree.get_children())

    # ── Folder Picker ──

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Choose Output Folder")
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)

    # ── Download Queue ──

    def _add_to_queue(self):
        selected_ids = self.tree.selection()
        if not selected_ids:
            messagebox.showinfo("Nothing Selected",
                                "Select at least one file to add to the queue.\n\n"
                                "Tip: Click a row to select, Ctrl+click for multiple, or use Select All.")
            return

        output_folder = self.output_entry.get().strip()
        if not output_folder:
            messagebox.showinfo("No Folder", "Choose an output folder first.")
            return

        # Snapshot credentials on the main thread — the download worker
        # must not touch Tk widgets.
        api_token = self.token_entry.get().strip()
        token_id = self.token_id_entry.get().strip()
        if not api_token:
            messagebox.showerror("Error", "Please enter your API Token.")
            return

        # Save output folder preference
        cfg = load_config()
        cfg["output_folder"] = output_folder
        save_config(cfg)

        # Build queue items from selected tree items
        media_by_uuid = {m["uuid"]: m for m in self.all_media}
        added = 0

        # Check which UUIDs are already queued (pending/downloading)
        with self._queue_lock:
            already_queued = {
                item["uuid"] for item in self.download_queue
                if item["status"] in ("Queued", "Downloading")
            }

        use_subfolders = self.use_date_subfolders.get()

        for uid in selected_ids:
            media = media_by_uuid.get(uid)
            if not media:
                continue
            if media["uuid"] in already_queued:
                continue

            self._queue_counter += 1
            q_item = build_queue_item(
                media=media,
                output_folder=output_folder,
                use_subfolders=use_subfolders,
                api_token=api_token,
                token_id=token_id,
                q_id=self._queue_counter,
            )

            with self._queue_lock:
                self.download_queue.append(q_item)

            # Add to queue treeview
            dest_display = str(Path(output_folder) / media["date"]) if use_subfolders else output_folder
            self.queue_tree.insert("", tk.END, iid=q_item["q_id"], values=(
                media["filename"], "Queued", dest_display,
            ))
            added += 1

        self._update_queue_count()

        if added > 0:
            self._set_status(f"Added {added} file(s) to queue.")
            # Signal the worker thread
            self._queue_pending.put(True)
        else:
            self._set_status("Selected files are already in the queue.")

        # Deselect in media tree so user can pick more
        self.tree.selection_remove(*selected_ids)

    def _cancel_download(self):
        self.cancel_requested = True
        self._set_status("Cancelling current download...")

    def _retry_failed(self):
        requeued = 0
        with self._queue_lock:
            for item in self.download_queue:
                if item["status"] in ("Failed", "Cancelled"):
                    item["status"] = "Queued"
                    requeued += 1
                    self._update_queue_item_status(item["q_id"], "Queued")
        if requeued > 0:
            self._set_status(f"Re-queued {requeued} file(s).")
            self._queue_pending.put(True)
        else:
            self._set_status("No failed items to retry.")

    def _clear_completed(self):
        clearable = {"Done", "Skipped", "Failed", "Cancelled"}
        with self._queue_lock:
            to_remove = [item for item in self.download_queue if item["status"] in clearable]
            self.download_queue = [item for item in self.download_queue if item["status"] not in clearable]
        for item in to_remove:
            try:
                self.queue_tree.delete(item["q_id"])
            except tk.TclError:
                pass
        self._update_queue_count()

    def _clear_all_queue(self):
        # Cancel any active download first
        self.cancel_requested = True
        with self._queue_lock:
            to_remove = [item for item in self.download_queue if item["status"] != "Downloading"]
            self.download_queue = [item for item in self.download_queue if item["status"] == "Downloading"]
        for item in to_remove:
            try:
                self.queue_tree.delete(item["q_id"])
            except tk.TclError:
                pass
        self._update_queue_count()

    def _update_queue_count(self):
        with self._queue_lock:
            pending = sum(1 for item in self.download_queue if item["status"] == "Queued")
            total = len(self.download_queue)
        self.queue_count_label.config(text=f"Queue: {pending} pending / {total} total")

    def _update_queue_item_status(self, q_id, status):
        """Thread-safe update of a queue item's status in the Treeview."""
        def update():
            try:
                current_values = self.queue_tree.item(q_id, "values")
                self.queue_tree.item(q_id, values=(current_values[0], status, current_values[2]))
            except tk.TclError:
                pass
            self._update_queue_count()
        self.root.after(0, update)

    def _queue_worker(self):
        """Background worker that processes the download queue."""
        while True:
            self._queue_pending.get()

            while True:
                item = self._claim_next_queued_item()
                if item is None:
                    self._set_status_safe("Queue complete.")
                    self.root.after(0, lambda: self.progress_bar.configure(value=0))
                    break

                self.cancel_requested = False
                self._update_queue_item_status(item["q_id"], "Downloading")
                self._process_queue_item(item)

    def _claim_next_queued_item(self):
        """Find and claim the next pending item in the queue."""
        with self._queue_lock:
            for q_item in self.download_queue:
                if q_item["status"] == "Queued":
                    q_item["status"] = "Downloading"
                    return q_item
        return None

    def _process_queue_item(self, item):
        """Process a single download queue item."""
        if not item.get("api_token"):
            item["status"] = "Failed"
            self._update_queue_item_status(item["q_id"], "Failed")
            self._set_status_safe("No API token — skipping.")
            return

        api = api_from_item(item)
        dest_path = self._resolve_dest_path(item)

        # Skip if already exists with matching size
        if dest_path.exists() and item["size"]:
            if dest_path.stat().st_size == item["size"]:
                item["status"] = "Skipped"
                self._update_queue_item_status(item["q_id"], "Skipped")
                self._set_status_safe(f"Skipped {item['filename']} (exists)")
                return

        self._download_with_retries(item, api, dest_path)

    def _resolve_dest_path(self, item):
        """Determine the destination file path for a queue item."""
        if item.get("use_date_subfolders", True):
            dest_folder = Path(item["output_folder"]) / item["date"]
        else:
            dest_folder = Path(item["output_folder"])
        dest_folder.mkdir(parents=True, exist_ok=True)
        return dest_folder / item["filename"]

    def _download_with_retries(self, item, api, dest_path):
        """Attempt to download a file with retries."""
        max_retries = 3
        cancel_fn = lambda: self.cancel_requested

        for attempt in range(1, max_retries + 1):
            if self.cancel_requested:
                item["status"] = "Cancelled"
                self._update_queue_item_status(item["q_id"], "Cancelled")
                return

            retry_label = f" (attempt {attempt}/{max_retries})" if attempt > 1 else ""
            self._set_status_safe(f"Downloading {item['filename']}{retry_label}...")
            self._update_queue_item_status(
                item["q_id"],
                f"Retry {attempt}/{max_retries}" if attempt > 1 else "Downloading",
            )

            try:
                _fn = item["filename"]
                _attempt = attempt
                _max = max_retries
                throttle = ProgressThrottle(min_interval=0.25)

                def file_progress(downloaded, total, fn=_fn, att=_attempt, mx=_max, th=throttle):
                    is_final = bool(total) and downloaded >= total
                    if not is_final and not th.tick(time.monotonic()):
                        return
                    pct = downloaded / total * 100 if total else 0
                    retry_s = f" (attempt {att}/{mx})" if att > 1 else ""
                    self._set_status_safe(f"Downloading {fn}{retry_s} — {pct:.0f}%")
                    self.root.after(0, lambda p=pct: self.progress_bar.configure(value=p))

                download_url = item.get("download_url", "")
                if download_url:
                    api.download_file(download_url, str(dest_path),
                                      progress_callback=file_progress, cancel_check=cancel_fn)
                else:
                    api.download_file_by_uuid(item["uuid"], str(dest_path),
                                              progress_callback=file_progress, cancel_check=cancel_fn)

                item["status"] = "Done"
                self._update_queue_item_status(item["q_id"], "Done")
                self._set_status_safe(f"Downloaded {item['filename']}")
                return

            except _DownloadCancelled:
                self._cleanup_partial(dest_path)
                item["status"] = "Cancelled"
                self._update_queue_item_status(item["q_id"], "Cancelled")
                self._set_status_safe(f"Cancelled {item['filename']}")
                return

            except Exception as e:
                self._cleanup_partial(dest_path)
                LOGGER.warning(
                    "Download attempt %d/%d failed for %s: %s",
                    attempt, max_retries, item["filename"], e,
                )
                if attempt < max_retries:
                    wait = attempt * 5
                    self._set_status_safe(
                        f"Failed {item['filename']} (attempt {attempt}/{max_retries}): {e} — retrying in {wait}s..."
                    )
                    self._update_queue_item_status(item["q_id"], f"Waiting {wait}s...")
                    time.sleep(wait)
                else:
                    LOGGER.error("Download failed permanently: %s", item["filename"])
                    item["status"] = "Failed"
                    self._update_queue_item_status(item["q_id"], "Failed")
                    self._set_status_safe(f"Failed: {item['filename']} — {e} (all {max_retries} attempts)")

    @staticmethod
    def _cleanup_partial(dest_path):
        """Remove a partially downloaded file."""
        try:
            dest_path.unlink(missing_ok=True)
        except OSError:
            pass

    # ── Thread-safe UI updates ──

    def _set_status(self, text):
        self.status_label.config(text=text)

    def _set_status_safe(self, text):
        self.root.after(0, lambda: self._set_status(text))


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

def log_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "SkydioTransfer"


def setup_logging():
    if LOGGER.handlers:
        return
    LOGGER.setLevel(logging.INFO)
    directory = log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    handler = logging.handlers.RotatingFileHandler(
        directory / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(message)s"
    ))
    LOGGER.addHandler(handler)

    def _main_excepthook(exc_type, exc_value, tb):
        LOGGER.critical("Uncaught exception", exc_info=(exc_type, exc_value, tb))
        sys.__excepthook__(exc_type, exc_value, tb)

    def _thread_excepthook(args):
        LOGGER.critical(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _main_excepthook
    threading.excepthook = _thread_excepthook


def main():
    setup_logging()
    LOGGER.info("Starting Skydio Media Transfer")

    # Set DPI awareness for sharp text on Windows (must be before Tk() creation)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    SkydioTransferApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
