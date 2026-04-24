#!/usr/bin/env python3
"""LOGBOOK — Event Log Viewer for Phantom OS."""

import sys
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo

import json
import os
import time
import math
from collections import Counter
from datetime import datetime

ACTIVITY_FILE = "/etc/phantom/activity.json"


def _parse_timestamp(ts):
    """Parse a timestamp string, trying multiple formats. Returns time.struct_time or None."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return time.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    # Try ISO 8601 with timezone offset by stripping it
    if ts and ('+' in ts[10:] or ts.endswith('Z')):
        clean = ts.replace('Z', '').rsplit('+', 1)[0].rsplit('-', 1)[0] if 'T' in ts else ts
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return time.strptime(clean, fmt)
            except (ValueError, TypeError):
                continue
    return None

CATEGORIES = ["All", "mode", "config", "security", "network"]
CATEGORY_LABELS = {
    "All": "All Events",
    "mode": "Mode",
    "config": "Config",
    "security": "Security",
    "network": "Network",
}

def _hex_to_rgb(hex_str):
    """Convert '#rrggbb' hex to (r, g, b) floats 0.0-1.0."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)

# Fallback category colors (overridden by theme in _get_category_colors)
CATEGORY_COLORS = {
    "mode": (0.3, 0.7, 1.0),
    "config": (0.5, 0.9, 0.5),
    "security": (1.0, 0.4, 0.4),
    "network": (1.0, 0.8, 0.3),
}


class LogbookApp(GhostPortApp):
    def __init__(self):
        super().__init__("LOGBOOK", "logbook", (900, 650))
        self._events = []
        self._selected_event_idx = None  # For correlation highlighting
        self.build_ui()
        self._load_events()
        self.poll_start(30, self._poll)

    def build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.get_style_context().add_class("gp-header")

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_lbl = self.make_label("LOGBOOK", "gp-header-title")
        title_box.pack_start(title_lbl, False, False, 0)
        sub_lbl = self.make_label("Event Log", "gp-header-subtitle")
        title_box.pack_start(sub_lbl, False, False, 0)
        header_box.pack_start(title_box, False, False, 0)

        self._count_label = self.make_label("0 events", "gp-dim")
        self._count_label.set_halign(Gtk.Align.END)
        self._count_label.set_valign(Gtk.Align.CENTER)
        header_box.pack_end(self._count_label, False, False, 8)

        vbox.pack_start(header_box, False, False, 0)

        # Summary stats bar
        self._summary_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self._summary_bar.get_style_context().add_class("gp-card")
        self._summary_bar.set_margin_start(12)
        self._summary_bar.set_margin_end(12)
        self._summary_bar.set_margin_top(4)

        self._lbl_today = self.make_label("Today: 0", "gp-accent")
        self._summary_bar.pack_start(self._lbl_today, False, False, 8)

        self._lbl_top_cat = self.make_label("Top: --", "gp-text")
        self._summary_bar.pack_start(self._lbl_top_cat, False, False, 8)

        self._lbl_per_hour = self.make_label("Avg/hr: --", "gp-text")
        self._summary_bar.pack_start(self._lbl_per_hour, False, False, 8)

        vbox.pack_start(self._summary_bar, False, False, 0)

        # Notebook: Events | Timeline
        self._notebook = Gtk.Notebook()
        self._notebook.set_margin_start(12)
        self._notebook.set_margin_end(12)
        self._notebook.set_margin_top(4)

        # ── Tab 1: Events ──
        events_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Filter bar
        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filter_bar.set_margin_top(4)
        filter_bar.set_margin_bottom(4)

        filter_lbl = self.make_label("Filter:", "gp-dim")
        filter_bar.pack_start(filter_lbl, False, False, 0)

        self._cat_combo = Gtk.ComboBoxText()
        for cat in CATEGORIES:
            self._cat_combo.append_text(CATEGORY_LABELS.get(cat, cat))
        self._cat_combo.set_active(0)
        self._cat_combo.connect("changed", self._on_filter_changed)
        filter_bar.pack_start(self._cat_combo, False, False, 0)

        self._search_entry = Gtk.Entry()
        self._search_entry.set_placeholder_text("Search events...")
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("changed", self._on_filter_changed)
        filter_bar.pack_start(self._search_entry, True, True, 0)

        btn_export = self.make_button("Export CSV", self._on_export_csv, "gp-btn")
        self.attach_tooltip(btn_export,
                            "Save the currently-visible events to a CSV file "
                            "(respects the category filter + search).")
        filter_bar.pack_start(btn_export, False, False, 0)

        events_page.pack_start(filter_bar, False, False, 0)

        # TreeView
        self._store = Gtk.ListStore(str, str, str)
        self._filter_model = self._store.filter_new()
        self._filter_model.set_visible_func(self._filter_func)

        self._treeview = Gtk.TreeView(model=self._filter_model)
        self._treeview.set_headers_visible(True)
        self._treeview.set_enable_search(False)
        self._treeview.connect("cursor-changed", self._on_event_selected)

        col_time = Gtk.TreeViewColumn("Timestamp", Gtk.CellRendererText(), text=0)
        col_time.set_min_width(160)
        col_time.set_resizable(True)
        col_time.set_sort_column_id(0)
        self._treeview.append_column(col_time)

        col_cat = Gtk.TreeViewColumn("Category", Gtk.CellRendererText(), text=1)
        col_cat.set_min_width(90)
        col_cat.set_resizable(True)
        col_cat.set_sort_column_id(1)
        self._treeview.append_column(col_cat)

        renderer_desc = Gtk.CellRendererText()
        renderer_desc.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_desc = Gtk.TreeViewColumn("Event Description", renderer_desc, text=2)
        col_desc.set_expand(True)
        col_desc.set_resizable(True)
        col_desc.set_sort_column_id(2)
        self._treeview.append_column(col_desc)

        scrolled = self.make_scrolled(self._treeview)
        scrolled.set_vexpand(True)
        events_page.pack_start(scrolled, True, True, 0)

        # Correlation panel (below tree)
        self._corr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._corr_box.get_style_context().add_class("gp-card")
        self._corr_box.set_margin_top(4)
        self._corr_box.set_no_show_all(True)
        self._corr_box.hide()

        self._corr_title = self.make_label("RELATED EVENTS (within 5 min)", "gp-accent")
        self._corr_box.pack_start(self._corr_title, False, False, 4)

        self._corr_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self._corr_box.pack_start(self._corr_list, False, False, 0)

        events_page.pack_start(self._corr_box, False, False, 0)

        self._notebook.append_page(events_page, Gtk.Label(label="Events"))

        # ── Tab 2: Timeline ──
        timeline_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._timeline_da = Gtk.DrawingArea()
        self._timeline_da.set_size_request(-1, 250)
        self._timeline_da.connect("draw", self._draw_timeline)

        scrolled_timeline = self.make_scrolled(self._timeline_da)
        timeline_page.pack_start(scrolled_timeline, True, True, 4)

        self._notebook.append_page(timeline_page, Gtk.Label(label="Timeline"))

        vbox.pack_start(self._notebook, True, True, 0)

        # Status bar
        self._status_bar = self.make_status_bar("Ready")
        vbox.pack_start(self._status_bar, False, False, 0)

    def _load_events(self):
        self._store.clear()
        try:
            if not os.path.exists(ACTIVITY_FILE):
                self._events = []
                self._count_label.set_text("0 events")
                self.set_status("No activity log found")
                self._update_summary()
                return

            with open(ACTIVITY_FILE) as f:
                data = json.load(f)

            if isinstance(data, list):
                self._events = data
            else:
                self._events = []

            self._events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            # Cap at 2000 most recent events for resource safety
            if len(self._events) > 2000:
                self._events = self._events[:2000]

            for evt in self._events:
                ts = evt.get("timestamp", "?")
                cat = evt.get("category", "?")
                msg = evt.get("message", "")
                self._store.append([ts, cat, msg])

            total = len(self._events)
            self._count_label.set_text(f"{total} event{'s' if total != 1 else ''}")
            self.set_status(f"Loaded {total} events from {ACTIVITY_FILE}")

        except json.JSONDecodeError as e:
            self.set_status(f"Error parsing activity log: {e}")
            self._events = []
        except Exception as e:
            self.set_status(f"Error loading events: {e}")
            self._events = []

        self._update_summary()
        self._timeline_da.queue_draw()

    def _update_summary(self):
        """Update summary stats bar."""
        if not self._events:
            self._lbl_today.set_text("Today: 0")
            self._lbl_top_cat.set_text("Top: --")
            self._lbl_per_hour.set_text("Avg/hr: --")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        today_count = sum(1 for e in self._events if e.get("timestamp", "").startswith(today))
        self._lbl_today.set_text(f"Today: {today_count}")

        # Top category
        cat_counts = Counter(e.get("category", "?") for e in self._events)
        if cat_counts:
            top_cat, top_count = cat_counts.most_common(1)[0]
            self._lbl_top_cat.set_text(f"Top: {top_cat} ({top_count})")
        else:
            self._lbl_top_cat.set_text("Top: --")

        # Events per hour (last 24h)
        now = time.time()
        recent = 0
        for e in self._events:
            ts = e.get("timestamp", "")
            parsed = _parse_timestamp(ts)
            if parsed is not None:
                try:
                    evt_time = time.mktime(parsed)
                    if now - evt_time < 86400:
                        recent += 1
                except Exception:
                    pass
        avg_per_hour = recent / 24.0 if recent > 0 else 0
        self._lbl_per_hour.set_text(f"Avg/hr: {avg_per_hour:.1f}")

    def _filter_func(self, model, iter, data=None):
        cat_idx = self._cat_combo.get_active()
        search = self._search_entry.get_text().strip().lower()

        if cat_idx > 0:
            selected_cat = CATEGORIES[cat_idx]
            row_cat = model[iter][1]
            if row_cat != selected_cat:
                return False

        if search:
            ts = model[iter][0].lower()
            cat = model[iter][1].lower()
            msg = model[iter][2].lower()
            if search not in ts and search not in cat and search not in msg:
                return False

        return True

    def _on_filter_changed(self, widget):
        self._filter_model.refilter()
        self._timeline_da.queue_draw()
        visible = len(self._filter_model)
        total = len(self._events)
        if visible == total:
            self._count_label.set_text(f"{total} event{'s' if total != 1 else ''}")
        else:
            self._count_label.set_text(f"{visible}/{total} events")

    def _on_export_csv(self, btn):
        """Export currently-visible (filtered) events to CSV."""
        import csv as _csv
        dialog = Gtk.FileChooserDialog(
            title="Export Logbook (CSV)",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_current_name(f"logbook-{time.strftime('%Y%m%d-%H%M%S')}.csv")
        filt = Gtk.FileFilter()
        filt.set_name("CSV spreadsheet (*.csv)")
        filt.add_pattern("*.csv")
        dialog.add_filter(filt)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            try:
                rows = []
                for row in self._filter_model:
                    rows.append({
                        "timestamp": row[0],
                        "category": row[1],
                        "event": row[2],
                    })
                with open(filepath, "w", newline="") as f:
                    w = _csv.DictWriter(f, fieldnames=["timestamp", "category", "event"])
                    w.writeheader()
                    for r in rows:
                        w.writerow(r)
                self.set_status(f"Exported {len(rows)} events to {os.path.basename(filepath)}")
            except Exception as e:
                self.set_status(f"Export failed: {e}")
                try:
                    self.show_error("Export failed",
                                    f"Could not write {os.path.basename(filepath)}.",
                                    detail=str(e))
                except Exception:
                    pass
        dialog.destroy()

    # ── Event Correlation ────────────────────────────────────────────

    def _on_event_selected(self, treeview):
        """When an event is selected, find related events within 5 min."""
        selection = treeview.get_selection()
        model, treeiter = selection.get_selected()
        if not treeiter:
            self._corr_box.hide()
            return

        ts_str = model[treeiter][0]
        cat = model[treeiter][1]

        # Parse selected event timestamp
        parsed = _parse_timestamp(ts_str)
        if parsed is None:
            self._corr_box.hide()
            return
        try:
            sel_time = time.mktime(parsed)
        except Exception:
            self._corr_box.hide()
            return

        # Find related: same category within 5 minutes
        related = []
        for evt in self._events:
            evt_ts = evt.get("timestamp", "")
            evt_cat = evt.get("category", "")
            if evt_cat != cat or evt_ts == ts_str:
                continue
            evt_parsed = _parse_timestamp(evt_ts)
            if evt_parsed is not None:
                try:
                    evt_time = time.mktime(evt_parsed)
                    if abs(evt_time - sel_time) <= 300:  # 5 minutes
                        related.append(evt)
                except Exception:
                    pass

        # Show correlation panel
        for child in self._corr_list.get_children():
            self._corr_list.remove(child)

        if related:
            self._corr_box.show()
            for evt in related[:10]:  # Cap display at 10
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                lbl_ts = self.make_label(evt.get("timestamp", "?"), "gp-dim")
                lbl_ts.set_size_request(140, -1)
                row.pack_start(lbl_ts, False, False, 0)
                lbl_msg = self.make_label(evt.get("message", ""), "gp-text")
                row.pack_start(lbl_msg, True, True, 0)
                self._corr_list.pack_start(row, False, False, 0)
            self._corr_list.show_all()
        else:
            self._corr_box.hide()

    def _get_cat_color(self, cat, default):
        """Get category color derived from theme."""
        c = self.colors
        mapping = {
            "mode": _hex_to_rgb(c.get("info", "#4db8ff")),
            "config": _hex_to_rgb(c.get("success", "#80e680")),
            "security": _hex_to_rgb(c.get("danger", "#ff6666")),
            "network": _hex_to_rgb(c.get("warning", "#ffcc4d")),
        }
        return mapping.get(cat, default)

    # ── Timeline Drawing ─────────────────────────────────────────────

    def _draw_timeline(self, widget, cr):
        """Draw hourly event density chart for last 24h."""
        c = self.colors
        alloc = widget.get_allocation()
        w = alloc.width
        h = alloc.height
        r, g, b = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0

        margin_left = 50
        margin_right = 20
        margin_top = 30
        margin_bottom = 40
        chart_w = w - margin_left - margin_right
        chart_h = h - margin_top - margin_bottom

        widget.set_size_request(margin_left + 24 * 30 + margin_right, 250)

        # Title
        cr.set_source_rgba(r, g, b, 0.8)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        cr.move_to(margin_left, 18)
        cr.show_text("EVENT DENSITY (last 24h)")

        if not self._events:
            cr.set_source_rgba(r, g, b, 0.3)
            cr.set_font_size(12)
            cr.move_to(w / 2 - 50, h / 2)
            cr.show_text("No event data")
            return

        # Bucket events by hour and category
        now = time.time()
        hourly = {}  # hour_offset -> {category: count}
        for evt in self._events:
            ts = evt.get("timestamp", "")
            cat = evt.get("category", "?")
            parsed = _parse_timestamp(ts)
            if parsed is not None:
                try:
                    evt_time = time.mktime(parsed)
                    hours_ago = (now - evt_time) / 3600
                    if 0 <= hours_ago < 24:
                        hour_idx = int(hours_ago)
                        bucket = hourly.setdefault(hour_idx, Counter())
                        bucket[cat] += 1
                except Exception:
                    pass

        if not hourly:
            cr.set_source_rgba(r, g, b, 0.3)
            cr.set_font_size(12)
            cr.move_to(w / 2 - 60, h / 2)
            cr.show_text("No recent events")
            return

        # Find max for scaling
        max_total = max(sum(cats.values()) for cats in hourly.values()) if hourly else 1
        max_total = max(max_total, 1)

        bar_w = max(8, chart_w / 24 - 2)

        # Draw bars (stacked by category)
        all_cats = sorted({cat for cats in hourly.values() for cat in cats})

        for hour_idx in range(24):
            x = margin_left + (23 - hour_idx) * (chart_w / 24)
            cats = hourly.get(hour_idx, Counter())
            total = sum(cats.values())

            y_offset = 0
            for cat in all_cats:
                count = cats.get(cat, 0)
                if count == 0:
                    continue

                bar_h = (count / max_total) * chart_h
                cat_color = self._get_cat_color(cat, (r, g, b))

                cr.set_source_rgba(cat_color[0], cat_color[1], cat_color[2], 0.7)
                cr.rectangle(
                    x + 1,
                    margin_top + chart_h - y_offset - bar_h,
                    bar_w,
                    bar_h
                )
                cr.fill()
                y_offset += bar_h

            # Hour label
            hour_label = datetime.fromtimestamp(now - hour_idx * 3600).strftime("%H")
            cr.set_source_rgba(r, g, b, 0.4)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8)
            ext = cr.text_extents(hour_label)
            cr.move_to(x + bar_w / 2 - ext.width / 2, margin_top + chart_h + 14)
            cr.show_text(hour_label)

        # Y-axis labels
        cr.set_font_size(9)
        for i in range(5):
            y = margin_top + chart_h * (1 - i / 4)
            val = int(max_total * i / 4)
            cr.set_source_rgba(r, g, b, 0.3)
            cr.move_to(4, y + 3)
            cr.show_text(str(val))
            # Grid line
            cr.set_source_rgba(r, g, b, 0.06)
            cr.move_to(margin_left, y)
            cr.line_to(w - margin_right, y)
            cr.stroke()

        # Legend
        legend_y = margin_top + chart_h + 28
        legend_x = margin_left
        cr.set_font_size(9)
        for cat in all_cats:
            cat_color = self._get_cat_color(cat, (r, g, b))
            cr.set_source_rgba(cat_color[0], cat_color[1], cat_color[2], 0.8)
            cr.rectangle(legend_x, legend_y, 10, 10)
            cr.fill()
            cr.set_source_rgba(r, g, b, 0.6)
            cr.move_to(legend_x + 14, legend_y + 9)
            cr.show_text(cat)
            ext = cr.text_extents(cat)
            legend_x += ext.width + 30

    def on_theme_changed(self):
        self._timeline_da.queue_draw()

    def _poll(self):
        self._load_events()
        return True


if __name__ == "__main__":
    app = LogbookApp()
    app.run()
