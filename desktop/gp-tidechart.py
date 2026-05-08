#!/usr/bin/env python3
"""
GhostPort Tide Chart — Bandwidth heatmap monitor with anomaly detection.
Replaces TUI tool gp-heatmap.
"""

import sys
import os
import json
import time
import math
from datetime import datetime

sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo

HISTORY_DIR = os.path.expanduser("~/.config/phantom")
# Separate from TUI heatmap data (different format/granularity)
HISTORY_FILE = os.path.join(HISTORY_DIR, "tidechart-history.json")
INTRO_MARKER = os.path.join(HISTORY_DIR, ".tidechart-intro-seen")
INTERFACES = ["eth0", "wlan0", "wg0", "wg1"]
POLL_INTERVAL = 5  # seconds
# Anomaly baseline needs ~7 days of history to be stable
SPARSE_HISTORY_THRESHOLD_DAYS = 7

RETENTION_CONFIG = "/etc/phantom/retention.json"


def _load_retention_days(default=30, floor=1, cap=60):
    """Read history retention window (days) from shared config; clamped."""
    try:
        with open(RETENTION_CONFIG) as f:
            days = int(json.load(f).get("days", default))
        return max(floor, min(cap, days))
    except Exception:
        return default

CELL_W = 34
CELL_H = 18
MARGIN_LEFT = 50
MARGIN_TOP = 30
LEGEND_H = 40
SUMMARY_ROW_H = 22

# Anomaly threshold: cell value must exceed baseline p90 by this factor
ANOMALY_FACTOR = 1.5


def _hex_to_rgb(hex_str):
    """Convert '#rrggbb' hex to (r, g, b) floats 0.0-1.0."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


class TideChart(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Tide Chart?",
         "Tide Chart is a long-window bandwidth heatmap. Each row is one day; each "
         "column is one hour. Cells are color-coded by the peak throughput observed "
         "during that hour, so you can spot \"when does this device get loud?\" at "
         "a glance.\n\n"
         "Data updates every 5 seconds and is stored locally in "
         "~/.config/phantom/tidechart-history.json. Retention is configurable in "
         "/etc/phantom/retention.json (default 30 days, range 1–60)."),

        ("Interface selector",
         "The dropdown on the left picks which network interface the heatmap shows:\n\n"
         "• eth0 — wired WAN uplink\n"
         "• wlan0 — your AP (LAN-side WiFi)\n"
         "• wg0 — control-plane tunnel (low-volume; spikes here are unusual)\n"
         "• wg1 — data-plane tunnel (where most internet traffic flows in DoubleHop/ZHop)\n\n"
         "Switching interface re-paints the heatmap and changes which throughput "
         "live-rate shows on the right of the toolbar."),

        ("Reading the heatmap",
         "The y-axis (rows) is dates, newest at the top. The x-axis (columns) is "
         "hours 0–23 in your local time.\n\n"
         "Cell color goes from dim (background) at idle to your accent color at peak. "
         "The mapping is logarithmic — a cell that's twice as bright is roughly an "
         "order of magnitude more bandwidth, not double. This makes overnight idle "
         "patterns and daytime spikes both visible without one drowning the other.\n\n"
         "Hover any cell to see the exact peak rate for that hour."),

        ("Compare mode",
         "The \"Compare\" dropdown adds a second heatmap below the first, showing a "
         "different interface side-by-side. Useful for: \"is the wg1 spike at 3am "
         "matched on eth0?\" (yes → real traffic; no → tunnel artifact).\n\n"
         "Pick \"(none)\" to hide the compare panel."),

        ("Anomaly detection",
         "When \"Anomalies: ON\" is active (the default), Tide Chart computes a "
         "per-hour baseline (the 90th percentile across recent days) and visually "
         "flags any hour that exceeds 1.5× that baseline. The flagged cells get a "
         "small marker so they stand out from the normal color gradient.\n\n"
         "Use it to catch \"this Tuesday afternoon is way busier than every other "
         "Tuesday afternoon\" without staring at the chart yourself.\n\n"
         "Toggle off if the markers get noisy during onboarding (need ~7 days of "
         "history before the baseline is stable)."),

        ("Throughput display",
         "The right side of the toolbar shows the live RX/TX rate in bytes-per-second "
         "for the currently-selected interface. Updates every 5 seconds with the "
         "rest of the chart. This is a real-time value, not a chart cell."),

        ("Export",
         "The Export button opens a save dialog with two formats:\n\n"
         "• CSV — raw 5-second samples for ALL interfaces, all retained days. One "
         "row per sample (interface, date, hour, rx_bps, tx_bps). Useful when you "
         "want to crunch the numbers yourself or share with a technical helper.\n\n"
         "• PNG — a screenshot of the currently-displayed heatmap canvas. Useful "
         "for slack/email/incident reports.\n\n"
         "Pick the format by typing the extension into the filename (.csv or .png) "
         "or selecting from the format filter."),

        ("Reset History",
         "Wipes the entire history file. Affects ALL interfaces and ALL retained "
         "days — there's no per-interface or per-date partial reset. Use when you've "
         "got noisy startup data or want a clean baseline.\n\n"
         "Confirmation dialog will ask before actually wiping. The "
         "tidechart-history.json file is rewritten empty; nothing is recoverable."),

        ("Gotchas",
         "• Baselines need history. The first ~7 days after install, anomaly "
         "detection will be jumpy as it builds the percentile baseline. Trust it more "
         "after a week of normal traffic.\n\n"
         "• Mode switches affect what you see. In ISP mode, wg1 stays flat (no traffic). "
         "In DoubleHop, eth0 only shows tunnel-encapsulated traffic, not your real "
         "browsing — for that, watch wg1.\n\n"
         "• Retention applies per-interface. If you change /etc/phantom/retention.json "
         "from 30 to 7, the next poll will trim each interface's history to 7 days."),
    ]

    def __init__(self):
        super().__init__("TIDE CHART", "tidechart", (950, 650))
        self.current_iface = "eth0"
        self.compare_iface = None  # Second interface for comparison
        self.history = {}
        self.prev_bytes = {}  # {iface: (rx, tx, timestamp)}
        self.current_throughput = (0, 0)  # (rx_bps, tx_bps)
        self.hover_cell = None  # (col, row) or None
        self._anomaly_enabled = True
        self.retention_days = _load_retention_days()

        os.makedirs(HISTORY_DIR, exist_ok=True)
        self._load_history()
        self._read_initial_bytes()
        self._build_ui()

        self.poll_start(POLL_INTERVAL, self._poll_bandwidth)

        # First-run explainer (defer to idle so the window paints first)
        if not os.path.exists(INTRO_MARKER):
            GLib.idle_add(self._show_first_run_intro)

    def _history_day_count(self):
        """Number of distinct days in history across all interfaces."""
        days = set()
        for iface_data in self.history.values():
            if isinstance(iface_data, dict):
                days.update(iface_data.keys())
        return len(days)

    def _update_sparse_banner(self):
        if self._history_day_count() < SPARSE_HISTORY_THRESHOLD_DAYS:
            self._sparse_banner.show_all()
        else:
            self._sparse_banner.hide()

    def _show_first_run_intro(self):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text="What Tide Chart catches",
        )
        dlg.format_secondary_markup(
            "<b>Rows = days</b> (newest at top). <b>Columns = hours</b> (0–23).\n"
            "Bright cells = heavy traffic; dim = idle. Anomaly markers flag\n"
            "unusually busy hours compared to your normal pattern.\n\n"
            "<b>Real wins:</b>\n"
            "• Compromised IoT beaconing at 3 AM (vertical streak)\n"
            "• Streaming binge on a school night (wide bright band)\n"
            "• VPN leak (wg1 should mirror eth0 in DoubleHop)\n"
            "• ISP throttling patterns (consistent dim cells at peak hours)\n\n"
            "Hover any cell for the exact peak rate. Anomaly baseline\n"
            "needs ~7 days of normal traffic to be reliable."
        )
        dlg.add_button("Got it — let me look around", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.run()
        dlg.destroy()
        try:
            with open(INTRO_MARKER, "w") as f:
                f.write("seen\n")
        except OSError:
            pass
        return False  # don't reschedule from idle_add

    # ── History persistence ───────────────────────────────────────────

    def _load_history(self):
        try:
            with open(HISTORY_FILE) as f:
                self.history = json.load(f)
        except Exception:
            self.history = {}

    def _save_history(self):
        try:
            data = json.dumps(self.history, separators=(",", ":"))
            # Cap at 500KB to prevent disk bloat
            if len(data) > 512000:
                # Prune oldest day from each interface
                for iface in list(self.history.keys()):
                    dates = sorted(self.history[iface].keys())
                    if len(dates) > 1:
                        del self.history[iface][dates[0]]
                data = json.dumps(self.history, separators=(",", ":"))
            with open(HISTORY_FILE, "w") as f:
                f.write(data)
        except Exception:
            pass

    # ── /proc/net/dev reading ─────────────────────────────────────────

    def _read_proc_net_dev(self):
        result = {}
        try:
            with open("/proc/net/dev") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    iface, data = line.split(":", 1)
                    iface = iface.strip()
                    fields = data.split()
                    if len(fields) >= 9:
                        result[iface] = (int(fields[0]), int(fields[8]))
        except Exception:
            pass
        return result

    def _read_initial_bytes(self):
        stats = self._read_proc_net_dev()
        now = time.time()
        for iface in INTERFACES:
            if iface in stats:
                self.prev_bytes[iface] = (stats[iface][0], stats[iface][1], now)

    # ── Polling ───────────────────────────────────────────────────────

    def _poll_bandwidth(self):
        stats = self._read_proc_net_dev()
        now = time.time()
        today = datetime.now().strftime("%Y-%m-%d")
        hour = datetime.now().hour

        for iface in INTERFACES:
            if iface not in stats:
                continue
            rx, tx = stats[iface]

            if iface in self.prev_bytes:
                prx, ptx, pt = self.prev_bytes[iface]
                dt = now - pt
                if dt > 0:
                    rx_bps = max(0, (rx - prx) / dt)
                    tx_bps = max(0, (tx - ptx) / dt)

                    if iface == self.current_iface:
                        self.current_throughput = (rx_bps, tx_bps)

                    if iface not in self.history:
                        self.history[iface] = {}
                    if today not in self.history[iface]:
                        self.history[iface][today] = []

                    self.history[iface][today].append([hour, int(rx_bps), int(tx_bps)])

                    # Cap entries per day (1 per minute = 1440 max)
                    if len(self.history[iface][today]) > 1440:
                        self.history[iface][today] = self.history[iface][today][-1440:]

                    # Keep only the last N days per interface (shared retention config)
                    dates = sorted(self.history[iface].keys())
                    while len(dates) > self.retention_days:
                        del self.history[iface][dates.pop(0)]

            self.prev_bytes[iface] = (rx, tx, now)

        self._save_history()
        self.drawing_area.queue_draw()
        if self.compare_iface:
            self._compare_da.queue_draw()
        self._update_status()
        self._update_sparse_banner()
        return True

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        header = self.make_header("TIDE CHART", "Bandwidth Monitor")
        root.pack_start(header, False, False, 0)

        # Sparse-history banner — visible until anomaly baseline is reliable.
        # Hidden once the user has at least SPARSE_HISTORY_THRESHOLD_DAYS of data.
        self._sparse_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._sparse_banner.get_style_context().add_class("gp-card")
        self._sparse_banner.set_margin_start(12)
        self._sparse_banner.set_margin_end(12)
        self._sparse_banner.set_margin_top(4)
        self._sparse_banner.set_no_show_all(True)
        banner_lbl = self.make_label(
            "Building baseline — anomaly markers stabilize after a week of normal traffic. "
            "Try comparing eth0 (wired in/out) vs wg1 (VPN data plane) using the dropdowns.",
            "gp-warning"
        )
        banner_lbl.set_line_wrap(True)
        banner_lbl.set_xalign(0)
        self._sparse_banner.pack_start(banner_lbl, True, True, 6)
        root.pack_start(self._sparse_banner, False, False, 0)
        self._update_sparse_banner()

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(4)

        iface_label = self.make_label("Interface:", "gp-text")
        toolbar.pack_start(iface_label, False, False, 0)

        self.iface_combo = Gtk.ComboBoxText()
        for iface in INTERFACES:
            self.iface_combo.append_text(iface)
        self.iface_combo.set_active(0)
        self.iface_combo.connect("changed", self._on_iface_changed)
        toolbar.pack_start(self.iface_combo, False, False, 0)

        # Compare toggle
        sep_v = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep_v.set_margin_start(8)
        sep_v.set_margin_end(8)
        toolbar.pack_start(sep_v, False, False, 0)

        compare_label = self.make_label("Compare:", "gp-text")
        toolbar.pack_start(compare_label, False, False, 0)

        self.compare_combo = Gtk.ComboBoxText()
        self.compare_combo.append_text("(none)")
        for iface in INTERFACES:
            self.compare_combo.append_text(iface)
        self.compare_combo.set_active(0)
        self.compare_combo.connect("changed", self._on_compare_changed)
        toolbar.pack_start(self.compare_combo, False, False, 0)

        # Anomaly toggle
        self._anomaly_btn = self.make_button(
            "Anomalies: ON", self._on_toggle_anomaly, "gp-btn-primary"
        )
        toolbar.pack_start(self._anomaly_btn, False, False, 8)

        # Throughput display
        self.throughput_label = self.make_label("", "gp-accent")
        self.throughput_label.set_halign(Gtk.Align.END)
        toolbar.pack_end(self.throughput_label, False, False, 0)

        # Reset button
        reset_btn = self.make_button("Reset History", self._on_reset, "gp-btn-danger")
        toolbar.pack_end(reset_btn, False, False, 0)

        # Export button (CSV / PNG)
        export_btn = self.make_button("Export", self._on_export, "gp-btn")
        toolbar.pack_end(export_btn, False, False, 0)

        # Help button (uses gp_app_base shared dialog pattern)
        toolbar.pack_end(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)

        root.pack_start(toolbar, False, False, 0)

        # Main drawing area (primary interface)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_events(
            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.drawing_area.connect("draw", self._on_draw)
        self.drawing_area.connect("motion-notify-event", self._on_motion)
        self.drawing_area.connect("leave-notify-event", self._on_leave)

        scrolled = self.make_scrolled(self.drawing_area)
        scrolled.set_margin_start(12)
        scrolled.set_margin_end(12)
        scrolled.set_margin_top(4)
        scrolled.set_margin_bottom(2)
        root.pack_start(scrolled, True, True, 0)

        # Compare drawing area (hidden until compare selected)
        self._compare_da = Gtk.DrawingArea()
        self._compare_da.set_events(
            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._compare_da.connect("draw", self._on_draw_compare)
        self._compare_scroll = self.make_scrolled(self._compare_da)
        self._compare_scroll.set_margin_start(12)
        self._compare_scroll.set_margin_end(12)
        self._compare_scroll.set_margin_bottom(2)
        self._compare_scroll.set_no_show_all(True)
        self._compare_scroll.hide()
        root.pack_start(self._compare_scroll, True, True, 0)

        # Status bar
        self.status_bar = self.make_status_bar("Monitoring bandwidth...")
        root.pack_start(self.status_bar, False, False, 0)

    def _on_iface_changed(self, combo):
        self.current_iface = combo.get_active_text() or "eth0"
        self.current_throughput = (0, 0)
        self.drawing_area.queue_draw()
        self._update_status()

    def _on_compare_changed(self, combo):
        text = combo.get_active_text()
        if text == "(none)" or not text:
            self.compare_iface = None
            self._compare_scroll.hide()
        else:
            self.compare_iface = text
            self._compare_scroll.show()
            self._compare_da.queue_draw()

    def _on_toggle_anomaly(self, btn):
        self._anomaly_enabled = not self._anomaly_enabled
        if self._anomaly_enabled:
            btn.set_label("Anomalies: ON")
            btn.get_style_context().remove_class("gp-btn")
            btn.get_style_context().add_class("gp-btn-primary")
        else:
            btn.set_label("Anomalies: OFF")
            btn.get_style_context().remove_class("gp-btn-primary")
            btn.get_style_context().add_class("gp-btn")
        self.drawing_area.queue_draw()

    def _on_reset(self, btn):
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Reset all bandwidth history?"
        )
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self.history = {}
            self._save_history()
            self.drawing_area.queue_draw()
            self.set_status("History cleared")

    def _update_status(self):
        rx, tx = self.current_throughput
        self.throughput_label.set_text(
            f"IN: {self._fmt_rate(rx)}  OUT: {self._fmt_rate(tx)}"
        )
        iface = self.current_iface
        days = len(self.history.get(iface, {}))
        samples = sum(len(v) for v in self.history.get(iface, {}).values())
        cmp = f" | Compare: {self.compare_iface}" if self.compare_iface else ""
        self.set_status(f"{iface} | {days} day{'s' if days != 1 else ''} | {samples} samples{cmp}")

    def _fmt_rate(self, bps):
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        elif bps < 1024 * 1024 * 1024:
            return f"{bps / (1024 * 1024):.1f} MB/s"
        else:
            return f"{bps / (1024 * 1024 * 1024):.1f} GB/s"

    def _fmt_bytes(self, b):
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.1f} GB"

    # ── Heatmap data ──────────────────────────────────────────────────

    def _get_heatmap_data(self, iface=None):
        """Return list of (date_str, [max_bps_per_hour x 24]) rows, newest first."""
        if iface is None:
            iface = self.current_iface
        iface_data = self.history.get(iface, {})
        rows = []

        for date_str in sorted(iface_data.keys(), reverse=True):
            samples = iface_data[date_str]
            hourly = [0] * 24
            for hour, rx, tx in samples:
                if 0 <= hour < 24:
                    total = rx + tx
                    hourly[hour] = max(hourly[hour], total)
            rows.append((date_str, hourly))

        return rows[:20]

    # ── Anomaly Detection ─────────────────────────────────────────────

    def _compute_baselines(self, rows):
        """Compute hourly p90 baselines from historical data.
        Returns dict: hour -> p90_value. Needs >=24h of data."""
        hourly_values = {h: [] for h in range(24)}
        for _, hourly in rows:
            for h in range(24):
                if hourly[h] > 0:
                    hourly_values[h].append(hourly[h])

        baselines = {}
        for h in range(24):
            vals = sorted(hourly_values[h])
            if len(vals) >= 4:  # Need at least 4 samples for meaningful p90
                idx = int(len(vals) * 0.9)
                baselines[h] = vals[min(idx, len(vals) - 1)]
        return baselines

    def _get_daily_summary(self, hourly, date_str=None):
        """Compute daily summary: total_bytes, peak_hour, avg_rate.
        Total bytes are estimated from raw samples (each sample = POLL_INTERVAL seconds of throughput).
        hourly[] contains max BPS per hour; for total bytes we need raw samples."""
        peak_hour = hourly.index(max(hourly)) if any(hourly) else 0
        non_zero = [v for v in hourly if v > 0]
        avg = sum(non_zero) / len(non_zero) if non_zero else 0

        # Compute total bytes from raw samples if available
        total_bytes = 0
        iface = getattr(self, 'current_iface', 'eth0')
        iface_data = self.history.get(iface, {})
        if date_str and date_str in iface_data:
            for _hour, rx, tx in iface_data[date_str]:
                total_bytes += (rx + tx) * POLL_INTERVAL
        else:
            # Fallback: approximate from hourly max BPS * 3600
            total_bytes = sum(h * 3600 for h in hourly)

        return total_bytes, peak_hour, avg

    # ── Drawing ───────────────────────────────────────────────────────

    def _draw_heatmap(self, widget, cr, iface=None, label=None):
        """Shared heatmap drawing logic for primary and compare views."""
        alloc = widget.get_allocation()
        width = alloc.width
        height = alloc.height
        c = self.colors
        r, g, b = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0

        rows = self._get_heatmap_data(iface)
        baselines = self._compute_baselines(rows) if self._anomaly_enabled else {}

        # Find global max for color scaling
        global_max = 1
        for _, hourly in rows:
            for v in hourly:
                if v > global_max:
                    global_max = v

        # Calculate total height needed
        summary_space = len(rows) * SUMMARY_ROW_H if rows else 0
        needed_h = MARGIN_TOP + len(rows) * CELL_H + LEGEND_H + summary_space + 40
        widget.set_size_request(MARGIN_LEFT + 24 * CELL_W + 160, max(needed_h, 300))

        # Interface label if comparing
        if label:
            cr.set_source_rgba(r, g, b, 0.8)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(11)
            cr.move_to(MARGIN_LEFT, 14)
            cr.show_text(label)

        # Column headers (hours)
        cr.set_source_rgba(r, g, b, 0.6)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9)
        for h in range(24):
            x = MARGIN_LEFT + h * CELL_W + CELL_W / 2
            lbl = f"{h:02d}"
            extents = cr.text_extents(lbl)
            cr.move_to(x - extents.width / 2, MARGIN_TOP - 6)
            cr.show_text(lbl)

        # Summary column header
        summary_x = MARGIN_LEFT + 24 * CELL_W + 10
        cr.set_source_rgba(r, g, b, 0.5)
        cr.set_font_size(8)
        cr.move_to(summary_x, MARGIN_TOP - 6)
        cr.show_text("TOTAL  PEAK  AVG")

        # Rows
        for row_idx, (date_str, hourly) in enumerate(rows):
            y = MARGIN_TOP + row_idx * CELL_H

            # Date label
            short_date = date_str[5:]  # MM-DD
            cr.set_source_rgba(r, g, b, 0.5)
            cr.set_font_size(9)
            cr.move_to(4, y + CELL_H / 2 + 3)
            cr.show_text(short_date)

            # Cells
            for h in range(24):
                x = MARGIN_LEFT + h * CELL_W
                val = hourly[h]

                if val > 0:
                    intensity = min(1.0, max(0.1, math.log1p(val) / math.log1p(global_max)))
                else:
                    intensity = 0.0

                # Cell background
                if intensity > 0:
                    cr.set_source_rgba(r, g, b, intensity)
                else:
                    cr.set_source_rgba(r, g, b, 0.03)
                cr.rectangle(x + 1, y + 1, CELL_W - 2, CELL_H - 2)
                cr.fill()

                # Cell border
                cr.set_source_rgba(r, g, b, 0.1)
                cr.rectangle(x + 1, y + 1, CELL_W - 2, CELL_H - 2)
                cr.stroke()

                # Anomaly marker: orange/red border if exceeds baseline by ANOMALY_FACTOR
                if self._anomaly_enabled and h in baselines and val > 0:
                    baseline = baselines[h]
                    if val > baseline * ANOMALY_FACTOR:
                        wr, wg, wb = _hex_to_rgb(self.colors["warning"]); cr.set_source_rgba(wr, wg, wb, 0.9)
                        cr.set_line_width(2)
                        cr.rectangle(x + 1, y + 1, CELL_W - 2, CELL_H - 2)
                        cr.stroke()
                        cr.set_line_width(1)

                # Hover highlight
                if self.hover_cell and self.hover_cell == (h, row_idx):
                    cr.set_source_rgba(r, g, b, 0.3)
                    cr.rectangle(x + 1, y + 1, CELL_W - 2, CELL_H - 2)
                    cr.stroke()

            # Daily summary (right of row)
            total_bytes, peak_hour, avg = self._get_daily_summary(hourly, date_str)
            cr.set_source_rgba(r, g, b, 0.4)
            cr.set_font_size(8)
            summary_text = f"{self._fmt_bytes(total_bytes)}  {peak_hour:02d}h  {self._fmt_rate(avg)}"
            cr.move_to(summary_x, y + CELL_H / 2 + 3)
            cr.show_text(summary_text)

        # Legend bar
        legend_y = MARGIN_TOP + len(rows) * CELL_H + 20
        cr.set_source_rgba(r, g, b, 0.5)
        cr.set_font_size(9)
        cr.move_to(MARGIN_LEFT, legend_y)
        cr.show_text("Low")

        legend_w = 200
        legend_x = MARGIN_LEFT + 30
        for i in range(legend_w):
            intensity = i / legend_w
            cr.set_source_rgba(r, g, b, max(0.05, intensity))
            cr.rectangle(legend_x + i, legend_y - 10, 1, 12)
            cr.fill()

        cr.set_source_rgba(r, g, b, 0.5)
        cr.move_to(legend_x + legend_w + 6, legend_y)
        cr.show_text("High")

        cr.move_to(legend_x + legend_w + 60, legend_y)
        cr.show_text(f"Peak: {self._fmt_rate(global_max)}")

        # Anomaly legend
        if self._anomaly_enabled and baselines:
            wr, wg, wb = _hex_to_rgb(self.colors["warning"]); cr.set_source_rgba(wr, wg, wb, 0.9)
            cr.rectangle(legend_x + legend_w + 180, legend_y - 10, 12, 12)
            cr.stroke()
            cr.set_source_rgba(r, g, b, 0.5)
            cr.move_to(legend_x + legend_w + 196, legend_y)
            cr.show_text("Anomaly (>1.5x p90)")

        # Hover tooltip
        if self.hover_cell and rows:
            col, row = self.hover_cell
            if 0 <= row < len(rows) and 0 <= col < 24:
                date_str, hourly = rows[row]
                val = hourly[col]
                tip = f"{date_str} {col:02d}:00 - {self._fmt_rate(val)}"

                # Add anomaly info if applicable
                if self._anomaly_enabled and col in baselines:
                    baseline = baselines[col]
                    if val > baseline * ANOMALY_FACTOR:
                        tip += f" (ANOMALY: {val/baseline:.1f}x baseline)"

                cr.set_font_size(10)
                extents = cr.text_extents(tip)
                tx = MARGIN_LEFT + col * CELL_W
                ty = MARGIN_TOP + row * CELL_H - 4

                pad = 4
                bgr, bgg, bgb = _hex_to_rgb(self.colors.get("bg", "#0a0a0a")); cr.set_source_rgba(bgr, bgg, bgb, 0.92)
                cr.rectangle(
                    tx - pad, ty - extents.height - pad,
                    extents.width + pad * 2, extents.height + pad * 2
                )
                cr.fill()

                cr.set_source_rgba(r, g, b, 1.0)
                cr.move_to(tx, ty)
                cr.show_text(tip)

        # Empty state
        if not rows:
            cr.set_source_rgba(r, g, b, 0.3)
            cr.set_font_size(14)
            msg = f"No bandwidth data for {iface or self.current_iface}. Monitoring..."
            extents = cr.text_extents(msg)
            cr.move_to(width / 2 - extents.width / 2, height / 2)
            cr.show_text(msg)

    def _on_draw(self, widget, cr):
        label = f"PRIMARY: {self.current_iface}" if self.compare_iface else None
        self._draw_heatmap(widget, cr, self.current_iface, label)

    def _on_draw_compare(self, widget, cr):
        if self.compare_iface:
            self._draw_heatmap(widget, cr, self.compare_iface, f"COMPARE: {self.compare_iface}")

    def _on_motion(self, widget, event):
        col = int((event.x - MARGIN_LEFT) / CELL_W)
        row = int((event.y - MARGIN_TOP) / CELL_H)
        if 0 <= col < 24 and row >= 0:
            self.hover_cell = (col, row)
        else:
            self.hover_cell = None
        widget.queue_draw()

    def _on_leave(self, widget, event):
        self.hover_cell = None
        widget.queue_draw()

    # ── Export ────────────────────────────────────────────────────────

    def _on_export(self, _btn):
        """Open a save dialog and dispatch to PNG or CSV based on the chosen filename."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dialog = Gtk.FileChooserDialog(
            title="Export Tide Chart",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_folder(os.path.expanduser("~"))
        dialog.set_current_name(f"tidechart_{self.current_iface}_{ts}.csv")

        f_csv = Gtk.FileFilter()
        f_csv.set_name("CSV (raw samples, all interfaces)")
        f_csv.add_pattern("*.csv")
        dialog.add_filter(f_csv)

        f_png = Gtk.FileFilter()
        f_png.set_name("PNG (heatmap screenshot)")
        f_png.add_pattern("*.png")
        dialog.add_filter(f_png)

        try:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                return
            path = dialog.get_filename()
        finally:
            dialog.destroy()

        # Default to .csv if the user typed no extension
        ext = os.path.splitext(path)[1].lower()
        if ext == ".png":
            self._export_png(path)
        elif ext == ".csv":
            self._export_csv(path)
        else:
            self._export_csv(path + ".csv")

    def _export_csv(self, path):
        """Dump every retained sample to CSV."""
        try:
            import csv
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["interface", "date", "hour", "rx_bps", "tx_bps"])
                for iface in sorted(self.history.keys()):
                    for date_str in sorted(self.history[iface].keys()):
                        for sample in self.history[iface][date_str]:
                            # samples are [hour, rx_bps, tx_bps]
                            if len(sample) >= 3:
                                w.writerow([iface, date_str, sample[0], sample[1], sample[2]])
            self.set_status(f"Exported CSV: {path}")
        except Exception as e:
            self.set_status(f"Export failed: {e}")

    def _export_png(self, path):
        """Capture the live drawing area as PNG (what the user sees on screen)."""
        try:
            win = self.drawing_area.get_window()
            if win is None:
                self.set_status("Export failed: drawing area not realized")
                return
            w = self.drawing_area.get_allocated_width()
            h = self.drawing_area.get_allocated_height()
            if w <= 0 or h <= 0:
                self.set_status("Export failed: drawing area has no size")
                return
            pixbuf = Gdk.pixbuf_get_from_window(win, 0, 0, w, h)
            if pixbuf is None:
                self.set_status("Export failed: could not capture canvas")
                return
            pixbuf.savev(path, "png", [], [])
            self.set_status(f"Exported PNG: {path}")
        except Exception as e:
            self.set_status(f"Export failed: {e}")

    def _on_destroy(self, *args):
        self._save_history()
        super()._on_destroy(*args)

    def on_theme_changed(self):
        self.drawing_area.queue_draw()
        if self.compare_iface:
            self._compare_da.queue_draw()


if __name__ == "__main__":
    app = TideChart()
    app.run()
