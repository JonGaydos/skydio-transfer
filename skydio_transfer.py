"""
Skydio Media Transfer - Portable Windows Application
Downloads media from Skydio Cloud to a local folder, organized by date.
"""

import calendar
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, date as date_type
from pathlib import Path

import requests

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

    def get_flights(self, date_from=None, date_to=None):
        """Fetch flights, optionally filtered by date range. Handles pagination."""
        all_flights = []
        page = 1

        while True:
            params = {"per_page": 100, "page_number": page}
            if date_from:
                params["takeoff_after"] = f"{date_from}T00:00:00Z"
            if date_to:
                params["takeoff_before"] = f"{date_to}T23:59:59Z"
            resp = requests.get(
                f"{BASE_URL}/flights", headers=self._headers(), params=params, timeout=30
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            flights = data.get("flights", [])
            pagination = data.get("pagination", {})

            all_flights.extend(flights)

            current = pagination.get("current_page", page)
            total = pagination.get("total_pages", 1)
            if current >= total:
                break
            page += 1

        return all_flights

    def get_flight_media(self, flight_id):
        """Fetch all media files for a given flight. Handles pagination."""
        all_files = []
        page = 1

        while True:
            params = {"flight_id": flight_id, "per_page": 500, "page_number": page}
            resp = requests.get(
                f"{BASE_URL}/media_files", headers=self._headers(), params=params, timeout=30
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            files = data.get("files", [])
            pagination = data.get("pagination", {})

            all_files.extend(files)

            current = pagination.get("current_page", page)
            total = pagination.get("total_pages", 1)
            if current >= total:
                break
            page += 1

        return all_files

    def download_file(self, file_uuid, dest_path, progress_callback=None):
        """Download a single media file by UUID to dest_path."""
        url = f"{BASE_URL}/media/download/{file_uuid}"
        resp = requests.get(url, headers=self._headers(), stream=True, timeout=120)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    progress_callback(downloaded, total)


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


def parse_takeoff(flight):
    """Extract (date_str, time_str) from a flight's takeoff timestamp."""
    takeoff = flight.get("takeoff", "")
    if takeoff and len(takeoff) >= 16:
        return takeoff[:10], takeoff[11:16]  # YYYY-MM-DD, HH:MM
    if takeoff and len(takeoff) >= 10:
        return takeoff[:10], "—"
    return "unknown", "—"


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

        self.header_label = ttk.Label(nav, text="", font=("Segoe UI", 10, "bold"), anchor=tk.CENTER)
        self.header_label.pack(side=tk.LEFT, expand=True, fill=tk.X)

        ttk.Button(nav, text=">", width=3, command=self._next_month).pack(side=tk.RIGHT, padx=2)
        ttk.Button(nav, text=">>", width=3, command=self._next_year).pack(side=tk.RIGHT)

        # Day-of-week headers
        dow_frame = ttk.Frame(self)
        dow_frame.pack(fill=tk.X)
        for day_name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            lbl = ttk.Label(dow_frame, text=day_name, width=5, anchor=tk.CENTER,
                            font=("Segoe UI", 9, "bold"))
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
                if day == 0:
                    lbl = ttk.Label(row_frame, text="", width=5)
                    lbl.pack(side=tk.LEFT, padx=1, pady=1)
                else:
                    is_today = (day == today.day and self.month == today.month
                                and self.year == today.year)
                    btn = tk.Button(
                        row_frame, text=str(day), width=4,
                        relief=tk.FLAT if not is_today else tk.SOLID,
                        bg="#e0e8ff" if is_today else "#f0f0f0",
                        activebackground="#c0d0ff",
                        font=("Segoe UI", 9, "bold" if is_today else "normal"),
                        command=lambda d=day: self._pick_day(d),
                    )
                    btn.pack(side=tk.LEFT, padx=1, pady=1)

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

    def __init__(self, parent, placeholder="All dates", on_change=None, **kwargs):
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
        self.root.geometry("800x720")
        self.root.minsize(650, 580)
        self.root.resizable(True, True)

        # Data stores
        self.all_media = []       # list of dicts with enriched media info
        self.available_dates = [] # sorted unique date strings
        self.downloading = False

        self._build_ui()
        self._load_saved_config()

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
        self.date_from = DateEntry(filter_row, placeholder="All dates",
                                   on_change=self._apply_date_filter)
        self.date_from.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(filter_row, text="Date To:").pack(side=tk.LEFT)
        self.date_to = DateEntry(filter_row, placeholder="All dates",
                                 on_change=self._apply_date_filter)
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

        # --- Download Frame ---
        dl_frame = ttk.LabelFrame(self.root, text="Download", padding=10)
        dl_frame.pack(fill=tk.X, **pad)

        folder_row = ttk.Frame(dl_frame)
        folder_row.pack(fill=tk.X)

        ttk.Label(folder_row, text="Output Folder:").pack(side=tk.LEFT)
        self.output_entry = ttk.Entry(folder_row, width=45)
        self.output_entry.pack(side=tk.LEFT, padx=(4, 4), fill=tk.X, expand=True)
        ttk.Button(folder_row, text="Browse", command=self._browse_folder).pack(side=tk.LEFT)

        self.download_btn = ttk.Button(dl_frame, text="Download Selected", command=self._start_download)
        self.download_btn.pack(fill=tk.X, pady=(8, 4))

        self.progress_bar = ttk.Progressbar(dl_frame, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=(0, 4))

        self.status_label = ttk.Label(dl_frame, text="Ready.", anchor=tk.W)
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

    # ── Fetch All Flights + Media ──

    def _fetch_all(self):
        api = self._get_api()
        if not api:
            return

        # Read and validate date filters
        date_from = self.date_from.get()
        date_to = self.date_to.get()

        if date_from:
            try:
                date_type.fromisoformat(date_from)
            except ValueError:
                messagebox.showerror("Invalid Date", f"Date From '{date_from}' is not valid.\nUse YYYY-MM-DD format.")
                return

        if date_to:
            try:
                date_type.fromisoformat(date_to)
            except ValueError:
                messagebox.showerror("Invalid Date", f"Date To '{date_to}' is not valid.\nUse YYYY-MM-DD format.")
                return

        # Warn if no date range set — could be very slow
        if not date_from and not date_to:
            if not messagebox.askyesno(
                "No Date Filter",
                "No date range is set. This will fetch ALL flights and media,\n"
                "which can take a long time with many flights.\n\n"
                "Set a date range first to speed things up.\n\n"
                "Continue anyway?"
            ):
                return

        self._set_status("Fetching flights...")
        self.fetch_btn.config(state=tk.DISABLED)
        self.download_btn.config(state=tk.DISABLED)

        def worker():
            try:
                # Step 1: Fetch flights (filtered by date at the API level)
                flights = api.get_flights(date_from=date_from, date_to=date_to)
                total = len(flights)
                range_desc = ""
                if date_from and date_to:
                    range_desc = f" ({date_from} to {date_to})"
                elif date_from:
                    range_desc = f" (from {date_from})"
                elif date_to:
                    range_desc = f" (through {date_to})"
                self._set_status_safe(f"Found {total} flights{range_desc}. Loading media...")

                # Step 2: Fetch media for each flight
                enriched_media = []
                for i, flight in enumerate(flights, 1):
                    flight_id = flight.get("flight_id", "")
                    date_str, time_str = parse_takeoff(flight)
                    vehicle = flight.get("vehicle_serial", "—")

                    self._set_status_safe(f"Loading media for flight {i}/{total}...")

                    try:
                        media_files = api.get_flight_media(flight_id)
                    except Exception:
                        media_files = []

                    for mf in media_files:
                        # Use the media's own captured_time if available, fall back to flight takeoff
                        cap_time = mf.get("captured_time", "")
                        if cap_time and len(cap_time) >= 16:
                            m_date = cap_time[:10]
                            m_time = cap_time[11:16]
                        else:
                            m_date = date_str
                            m_time = time_str

                        enriched_media.append({
                            "uuid": mf.get("uuid", ""),
                            "filename": mf.get("filename", f"media_{mf.get('uuid', '?')}"),
                            "date": m_date,
                            "time": m_time,
                            "kind": mf.get("kind", "—"),
                            "size": mf.get("size", 0),
                            "size_display": format_size(mf.get("size", 0)),
                            "flight_id": flight_id,
                            "vehicle": vehicle,
                        })

                self.root.after(0, lambda: self._populate_media(enriched_media))

            except requests.exceptions.HTTPError as e:
                err = e
                self.root.after(0, lambda: self._handle_api_error(err))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda: messagebox.showerror("Error", msg))
            finally:
                self.root.after(0, lambda: self.fetch_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_media(self, media_list):
        """Populate the treeview with fetched media."""
        self.all_media = media_list

        # Show all media (date filter will apply if user has typed dates)
        self._apply_date_filter()
        dates_count = len(set(m["date"] for m in media_list if m["date"] != "unknown"))
        self._set_status(f"Loaded {len(media_list)} media files from {dates_count} flight dates.")

    def _apply_date_filter(self):
        """Filter the displayed media based on the date entry values."""
        if not self.all_media:
            return

        date_from = self.date_from.get()
        date_to = self.date_to.get()

        filtered = self.all_media

        if date_from:
            # Validate format
            try:
                date_type.fromisoformat(date_from)
                filtered = [m for m in filtered if m["date"] >= date_from]
            except ValueError:
                pass  # ignore invalid partial typing

        if date_to:
            try:
                date_type.fromisoformat(date_to)
                filtered = [m for m in filtered if m["date"] <= date_to]
            except ValueError:
                pass

        self._refresh_tree(filtered)

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

        items = [(self.tree.set(iid, col), iid) for iid in self.tree.get_children("")]
        items.sort(key=lambda x: x[0].lower(), reverse=reverse)

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

    # ── Download ──

    def _start_download(self):
        if self.downloading:
            return

        selected_ids = self.tree.selection()
        if not selected_ids:
            messagebox.showinfo("Nothing Selected", "Select at least one file to download.\n\nTip: Click a row to select, Ctrl+click for multiple, or use Select All.")
            return

        output_folder = self.output_entry.get().strip()
        if not output_folder:
            messagebox.showinfo("No Folder", "Choose an output folder first.")
            return

        api = self._get_api()
        if not api:
            return

        # Save output folder preference
        cfg = load_config()
        cfg["output_folder"] = output_folder
        save_config(cfg)

        # Build download list from selected tree items
        media_by_uuid = {m["uuid"]: m for m in self.all_media}
        to_download = [media_by_uuid[uid] for uid in selected_ids if uid in media_by_uuid]

        if not to_download:
            return

        self.downloading = True
        self.download_btn.config(state=tk.DISABLED)
        self.fetch_btn.config(state=tk.DISABLED)
        self.progress_bar["value"] = 0

        def worker():
            try:
                self._download_media(api, to_download, output_folder)
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda: messagebox.showerror("Download Error", msg))
            finally:
                self.downloading = False
                self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.fetch_btn.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def _download_media(self, api, media_list, output_folder):
        total_files = len(media_list)
        completed = 0
        skipped = 0
        errors = 0

        for media in media_list:
            file_uuid = media["uuid"]
            filename = media["filename"]
            file_size = media["size"]
            date_str = media["date"]

            # Create date subfolder
            date_folder = Path(output_folder) / date_str
            date_folder.mkdir(parents=True, exist_ok=True)
            dest_path = date_folder / filename

            # Skip if already exists with matching size
            if dest_path.exists():
                existing_size = dest_path.stat().st_size
                if file_size and existing_size == file_size:
                    skipped += 1
                    completed += 1
                    self._update_progress_safe(completed, total_files, f"Skipped {filename} (exists)")
                    continue

            self._set_status_safe(f"Downloading {filename} ({completed + 1}/{total_files})...")

            try:
                # Capture current values for the closure
                _filename = filename
                _completed = completed
                _total = total_files

                def file_progress(downloaded, total, fn=_filename, comp=_completed, tot=_total):
                    pct = downloaded / total * 100 if total else 0
                    self._set_status_safe(f"Downloading {fn} ({comp + 1}/{tot}) — {pct:.0f}%")

                api.download_file(file_uuid, str(dest_path), progress_callback=file_progress)
                completed += 1
                self._update_progress_safe(completed, total_files, f"Downloaded {filename}")
            except Exception as e:
                errors += 1
                completed += 1
                self._update_progress_safe(
                    completed, total_files, f"Failed: {filename} — {e}"
                )

        summary = f"Done! {completed - skipped - errors} downloaded, {skipped} skipped, {errors} errors."
        self._set_status_safe(summary)
        self.root.after(0, lambda: messagebox.showinfo("Complete", summary))

    # ── Thread-safe UI updates ──

    def _set_status(self, text):
        self.status_label.config(text=text)

    def _set_status_safe(self, text):
        self.root.after(0, lambda: self._set_status(text))

    def _update_progress_safe(self, current, total, status_text):
        def update():
            if total > 0:
                self.progress_bar["value"] = current / total * 100
            self.status_label.config(text=status_text)

        self.root.after(0, update)


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

def main():
    root = tk.Tk()

    # Set DPI awareness for sharp text on Windows
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = SkydioTransferApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
