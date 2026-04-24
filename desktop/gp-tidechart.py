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
INTERFACES = ["eth0", "wlan0", "wg0", "wg1"]
POLL_INTERVAL = 5  # seconds

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
        return True

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        header = self.make_header("TIDE CHART", "Bandwidth Monitor")
        root.pack_start(header, False, False, 0)

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
