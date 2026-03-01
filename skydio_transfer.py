"""
Skydio Media Transfer - Portable Windows Application
Downloads media from Skydio Cloud to a local folder, organized by date.
"""

import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

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
        """Fetch all flights, optionally filtered by date range. Handles pagination."""
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


def flight_date(flight):
    """Extract date string (YYYY-MM-DD) from a flight's takeoff timestamp."""
    takeoff = flight.get("takeoff", "")
    if takeoff:
        return takeoff[:10]
    return "unknown"


def flight_display(flight):
    """Build a human-readable display string for a flight."""
    date = flight_date(flight)
    fid = flight.get("flight_id", "???")[:8]
    vehicle = flight.get("vehicle_serial", "—")
    media_count = flight.get("media_count", "?")
    return f"{date}  |  Flight {fid}  |  Vehicle {vehicle}  |  {media_count} files"


# ──────────────────────────────────────────────
# GUI Application
# ──────────────────────────────────────────────

class SkydioTransferApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Skydio Media Transfer")
        self.root.geometry("720x680")
        self.root.minsize(600, 550)
        self.root.resizable(True, True)

        self.flights = []
        self.flight_vars = []  # list of (BooleanVar, flight_dict)
        self.api = None
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

        # --- Flights Frame ---
        flights_frame = ttk.LabelFrame(self.root, text="Flights", padding=10)
        flights_frame.pack(fill=tk.BOTH, expand=True, **pad)

        filter_row = ttk.Frame(flights_frame)
        filter_row.pack(fill=tk.X)

        ttk.Label(filter_row, text="Date From:").pack(side=tk.LEFT)
        self.date_from_entry = ttk.Entry(filter_row, width=12)
        self.date_from_entry.pack(side=tk.LEFT, padx=(4, 12))
        self.date_from_entry.insert(0, "YYYY-MM-DD")
        self.date_from_entry.bind("<FocusIn>", lambda e: self._clear_placeholder(e, "YYYY-MM-DD"))

        ttk.Label(filter_row, text="Date To:").pack(side=tk.LEFT)
        self.date_to_entry = ttk.Entry(filter_row, width=12)
        self.date_to_entry.pack(side=tk.LEFT, padx=(4, 12))
        self.date_to_entry.insert(0, "YYYY-MM-DD")
        self.date_to_entry.bind("<FocusIn>", lambda e: self._clear_placeholder(e, "YYYY-MM-DD"))

        ttk.Button(filter_row, text="Fetch Flights", command=self._fetch_flights).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # Scrollable flight list
        list_container = ttk.Frame(flights_frame)
        list_container.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.flight_canvas = tk.Canvas(list_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.flight_canvas.yview)
        self.flight_list_frame = ttk.Frame(self.flight_canvas)

        self.flight_list_frame.bind(
            "<Configure>",
            lambda e: self.flight_canvas.configure(scrollregion=self.flight_canvas.bbox("all")),
        )
        self.flight_canvas.create_window((0, 0), window=self.flight_list_frame, anchor=tk.NW)
        self.flight_canvas.configure(yscrollcommand=scrollbar.set)

        self.flight_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind mousewheel scrolling
        self.flight_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.flight_canvas.yview_scroll(-1 * (e.delta // 120), "units"),
        )

        # Select / Deselect buttons
        btn_row = ttk.Frame(flights_frame)
        btn_row.pack(fill=tk.X, pady=(4, 0))

        ttk.Button(btn_row, text="Select All", command=self._select_all).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="Deselect All", command=self._deselect_all).pack(side=tk.LEFT)

        self.flight_count_label = ttk.Label(btn_row, text="")
        self.flight_count_label.pack(side=tk.RIGHT)

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

    # ── Placeholder Handling ──

    def _clear_placeholder(self, event, placeholder):
        widget = event.widget
        if widget.get() == placeholder:
            widget.delete(0, tk.END)

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

    # ── Fetch Flights ──

    def _fetch_flights(self):
        api = self._get_api()
        if not api:
            return

        date_from = self.date_from_entry.get().strip()
        date_to = self.date_to_entry.get().strip()
        if date_from == "YYYY-MM-DD":
            date_from = None
        if date_to == "YYYY-MM-DD":
            date_to = None

        self._set_status("Fetching flights...")
        self.download_btn.config(state=tk.DISABLED)

        def worker():
            try:
                flights = api.get_flights(date_from, date_to)
                self.root.after(0, lambda: self._populate_flights(flights))
            except requests.exceptions.HTTPError as e:
                self.root.after(0, lambda: self._handle_api_error(e))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_flights(self, flights):
        # Clear existing
        for widget in self.flight_list_frame.winfo_children():
            widget.destroy()
        self.flight_vars.clear()
        self.flights = flights

        if not flights:
            ttk.Label(self.flight_list_frame, text="No flights found.").pack(anchor=tk.W)
            self.flight_count_label.config(text="0 flights")
            self._set_status("No flights found.")
            return

        for flight in flights:
            var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(
                self.flight_list_frame, text=flight_display(flight), variable=var
            )
            cb.pack(anchor=tk.W, pady=1)
            self.flight_vars.append((var, flight))

        self.flight_count_label.config(text=f"{len(flights)} flights")
        self._set_status(f"Loaded {len(flights)} flights.")

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
        self._set_status("Error fetching flights.")

    # ── Select / Deselect ──

    def _select_all(self):
        for var, _ in self.flight_vars:
            var.set(True)

    def _deselect_all(self):
        for var, _ in self.flight_vars:
            var.set(False)

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

        selected = [(var, flight) for var, flight in self.flight_vars if var.get()]
        if not selected:
            messagebox.showinfo("Nothing Selected", "Select at least one flight to download.")
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

        self.downloading = True
        self.download_btn.config(state=tk.DISABLED)
        self.progress_bar["value"] = 0

        flights_to_download = [flight for _, flight in selected]

        def worker():
            try:
                self._download_flights(api, flights_to_download, output_folder)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Download Error", str(e)))
            finally:
                self.downloading = False
                self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def _download_flights(self, api, flights, output_folder):
        # First, gather all media files across selected flights
        all_media = []  # list of (date_str, file_dict)
        total_flights = len(flights)

        for i, flight in enumerate(flights, 1):
            self._set_status_safe(f"Fetching media for flight {i}/{total_flights}...")
            flight_id = flight.get("flight_id", "")
            date_str = flight_date(flight)

            try:
                media_files = api.get_flight_media(flight_id)
                for mf in media_files:
                    all_media.append((date_str, mf))
            except Exception as e:
                self._set_status_safe(f"Error fetching media for flight {flight_id[:8]}: {e}")

        if not all_media:
            self._set_status_safe("No media files found in selected flights.")
            return

        total_files = len(all_media)
        completed = 0
        skipped = 0
        errors = 0

        for date_str, media_file in all_media:
            file_uuid = media_file.get("uuid", "")
            filename = media_file.get("filename", f"media_{file_uuid}")
            file_size = media_file.get("size", 0)

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

            self._set_status_safe(
                f"Downloading {filename} ({completed + 1}/{total_files})..."
            )

            try:
                def file_progress(downloaded, total):
                    pct = downloaded / total * 100 if total else 0
                    self._set_status_safe(
                        f"Downloading {filename} ({completed + 1}/{total_files}) — {pct:.0f}%"
                    )

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
