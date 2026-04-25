#!/usr/bin/env python3
"""gp-dragnet — Dragnet: Packet Capture & Analysis for Phantom OS"""

import sys, os
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo
import subprocess
import threading
import signal
import shutil
import time
import re
from collections import Counter
from datetime import datetime

# ── Constants ─────────────────────────────────────────────────────────

CAPTURES_DIR = os.path.expanduser("~/captures")  # where Save .pcap persists files
BUFFER_DIR = "/tmp/dragnet-buffer"                # scratch path for live capture (cleared on Start/Stop)
MAX_LIVE_PACKETS = 500
MAX_PCAP_SIZE_KB = 102400   # 100 MB max pcap file
MIN_DISK_FREE_MB = 200

INTERFACES = ["eth0", "wlan0", "wg0", "wg1", "any"]

FILTER_PRESETS = [
    ("All Traffic", ""),
    ("DNS Only", "udp port 53"),
    ("HTTPS", "tcp port 443"),
    ("HTTP (Unencrypted!)", "tcp port 80"),
    ("No DNS", "not port 53"),
]


class DragnetApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Dragnet?",
         "Dragnet is a packet capture and analysis tool — a fishing net that catches "
         "every packet traveling across a network interface so you can study them later.\n\n"
         "You pick an interface (like eth0 or wlan0), optionally filter by protocol "
         "or port, hit Start, and Dragnet collects packets in real time. Later you "
         "can save the capture to a .pcap file or load existing ones for analysis "
         "(protocol breakdown, top talkers, DNS queries, flow summaries).\n\n"
         "Under the hood this is tshark — the command-line cousin of Wireshark. "
         "If you've used Wireshark, everything here will feel familiar."),

        ("Live Capture tab — controls bar",
         "Across the top of the Live Capture tab:\n\n"
         "• Interface — which network port to listen on. `eth0` = upstream WAN, "
         "`wlan0` = LAN-side AP traffic, `wg0`/`wg1` = inside the VPN tunnels, "
         "`tailscale0` = Tailscale mesh, `any` = everything.\n\n"
         "• Preset — starter filters. \"DNS\" for port 53, \"HTTP/S\" for web, "
         "\"VPN\" for WireGuard handshakes, etc. Or pick \"Custom\" and write your own.\n\n"
         "• Filter — a BPF (Berkeley Packet Filter) expression. Examples: "
         "`tcp port 443`, `host 8.8.8.8`, `udp and port 53`. Leave blank to capture "
         "everything on the interface.\n\n"
         "• Limit — max packets to collect. Default 500 keeps the UI responsive. "
         "Large captures go to a .pcap file, not the live view."),

        ("Live Capture tab — action buttons",
         "Start Capture — begin sniffing with the current interface/filter settings. "
         "The packet list below fills up in real time.\n\n"
         "Stop — halt capture. The collected packets stay in the view; you can "
         "scroll, sort, save, or clear them.\n\n"
         "Clear — empty the packet list (doesn't stop capture).\n\n"
         "Save .pcap — copy the current capture into ~/captures/ (or anywhere "
         "you pick) so it persists. Live captures live in /tmp/dragnet-buffer/ "
         "until you save them; if you don't save, the buffer is wiped on the "
         "next Start or on reboot — so test captures don't pile up. You can "
         "open saved files in Wireshark or with Dragnet's File Analysis tab. "
         "Enabled once you have packets to save."),

        ("Packet list columns",
         "Every row is one packet. Columns, left to right:\n\n"
         "• # — packet number within this capture.\n"
         "• Time — relative seconds since capture start (not wall-clock).\n"
         "• Source — sender IP + port.\n"
         "• Destination — receiver IP + port.\n"
         "• Proto — top-level protocol (TCP, UDP, ICMP, DNS, TLS...).\n"
         "• Length — packet size in bytes.\n"
         "• Info — protocol-specific summary (e.g. \"Standard query A google.com\").\n\n"
         "Rows are color-coded by protocol so TCP/UDP/DNS/etc. are distinguishable "
         "at a glance. Click any row for full packet details."),

        ("Live stats sidebar",
         "The right-hand panel in Live Capture aggregates the packets you're seeing:\n\n"
         "• Packet counter — running total.\n"
         "• Protocol breakdown — how much of the traffic is TCP vs UDP vs DNS etc.\n"
         "• Top talkers — which IPs are sending/receiving the most.\n\n"
         "This updates every second or two as packets flow in. Good for spotting "
         "\"that one device is eating all my bandwidth\" at a glance."),

        ("File Analysis tab",
         "Load an existing .pcap file (from your Save button, or from another tool "
         "like Wireshark) and Dragnet breaks it down across five sub-tabs:\n\n"
         "• Packets — same table view as Live Capture but against saved packets.\n"
         "• Protocols — tree showing every protocol encountered and hit counts.\n"
         "• Top Talkers — leaderboard of busiest IPs by bytes sent/received.\n"
         "• DNS — every DNS query in the capture with response codes.\n"
         "• Flows — TCP/UDP conversations grouped by 5-tuple (the \"who talked to "
         "whom and for how long\" view).\n\n"
         "Useful for \"why was my network slow from 2-4pm\" investigations."),

        ("Gotchas",
         "• Capture runs unprivileged via the `wireshark` group + dumpcap capabilities. "
         "If you ever rebuild from source, make sure your user is in `wireshark` and "
         "dumpcap has the right caps, or the capture button does nothing.\n\n"
         "• Captures auto-stop at 50,000 packets or 10 minutes (whichever first) — "
         "this prevents runaway memory use (tshark has documented leaks on long runs).\n\n"
         "• If the interface you want isn't in the dropdown, it's probably down. "
         "`ip link show` in a terminal will tell you what's up.\n\n"
         "• BPF filter errors show in the status bar at the bottom — typos like "
         "`tcp port = 443` won't start capture. Correct form is `tcp port 443`.\n\n"
         "• If the Dragnet window says \"tshark not found\", install it with "
         "`sudo apt install tshark` (the scoped sudo allows this)."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    """Unified packet capture and pcap analysis tool."""

    def __init__(self):
        super().__init__("Dragnet", "dragnet", (1050, 750))

        self.capture_proc = None
        self.capture_thread = None
        self.capturing = False
        self.packet_count = 0
        self.capture_output_file = None
        self._tshark_available = shutil.which("tshark") is not None

        # Live stats tracking
        self._live_stats = {
            "start_time": 0,
            "total_bytes": 0,
            "protocols": Counter(),
            "sources": Counter(),
            "destinations": Counter(),
        }

        os.makedirs(CAPTURES_DIR, exist_ok=True)

        self._apply_css(self._extra_css())
        self._build_ui()
        # cleanup handled via _on_destroy override

    @staticmethod
    def _hex_to_rgba_css(hex_str, alpha):
        h = hex_str.lstrip("#")
        return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"

    def _extra_css(self):
        c = self.colors
        return f"""
.proto-dns {{
    background-color: {self._hex_to_rgba_css(c['info'], 0.08)};
}}
.proto-https {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.06);
}}
.proto-http {{
    background-color: {self._hex_to_rgba_css(c['danger'], 0.08)};
}}
.gp-mono {{
    font-family: monospace;
    font-size: 11px;
    color: {c['text']};
}}
.gp-entry-wide {{
    min-width: 160px;
}}
.gp-tab-label {{
    font-family: monospace;
    font-size: 11px;
}}
"""

    # ── UI Construction ───────────────────────────────────────────────

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        root.pack_start(
            self.make_header("DRAGNET", "Packet Capture & Analysis"), False, False, 0
        )

        if not self._tshark_available:
            root.pack_start(self._build_missing_tshark(), True, True, 0)
            root.pack_start(self.make_status_bar("tshark not found"), False, False, 0)
            return

        # Main notebook (Live Capture | File Analysis)
        self.main_notebook = Gtk.Notebook()
        self.main_notebook.set_margin_start(6)
        self.main_notebook.set_margin_end(6)
        self.main_notebook.set_margin_top(4)
        self.main_notebook.set_margin_bottom(4)

        self.main_notebook.append_page(
            self._build_live_tab(), Gtk.Label(label="Live Capture")
        )
        self.main_notebook.append_page(
            self._build_file_tab(), Gtk.Label(label="File Analysis")
        )

        root.pack_start(self.main_notebook, True, True, 0)

        root.pack_start(self.make_status_bar("Ready"), False, False, 0)

    def _build_missing_tshark(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        icon_label = self.make_label("DRAGNET", "gp-header-title")
        icon_label.set_halign(Gtk.Align.CENTER)
        box.pack_start(icon_label, False, False, 0)

        msg = self.make_label(
            "tshark (Wireshark CLI) is required but not installed.", "gp-accent"
        )
        msg.set_halign(Gtk.Align.CENTER)
        box.pack_start(msg, False, False, 0)

        hint = self.make_label(
            "Install with:  sudo apt install tshark", "gp-dim"
        )
        hint.set_halign(Gtk.Align.CENTER)
        box.pack_start(hint, False, False, 0)

        return box

    # ── Tab 1: Live Capture ───────────────────────────────────────────

    def _build_live_tab(self):
        # Use paned: left=packet list, right=live stats sidebar
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

        # Left: controls + packet list
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left_box.set_margin_top(6)
        left_box.set_margin_start(4)
        left_box.set_margin_end(4)

        # Controls bar
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_margin_bottom(4)

        controls.pack_start(self.make_label("Interface:", "gp-dim"), False, False, 0)
        self.iface_combo = Gtk.ComboBoxText()
        for iface in INTERFACES:
            self.iface_combo.append_text(iface)
        self.iface_combo.set_active(0)
        self.iface_combo.get_style_context().add_class("gp-mono")
        controls.pack_start(self.iface_combo, False, False, 0)

        controls.pack_start(self.make_label("Preset:", "gp-dim"), False, False, 4)
        self.preset_combo = Gtk.ComboBoxText()
        for name, _ in FILTER_PRESETS:
            self.preset_combo.append_text(name)
        self.preset_combo.set_active(0)
        self.preset_combo.connect("changed", self._on_preset_changed)
        controls.pack_start(self.preset_combo, False, False, 0)

        controls.pack_start(self.make_label("Filter:", "gp-dim"), False, False, 4)
        self.filter_entry = Gtk.Entry()
        self.filter_entry.set_placeholder_text("BPF filter (e.g. tcp port 443)")
        self.filter_entry.get_style_context().add_class("gp-entry-wide")
        controls.pack_start(self.filter_entry, True, True, 0)

        controls.pack_start(self.make_label("Limit:", "gp-dim"), False, False, 4)
        self.limit_entry = Gtk.Entry()
        self.limit_entry.set_text("500")
        self.limit_entry.set_width_chars(5)
        controls.pack_start(self.limit_entry, False, False, 0)

        left_box.pack_start(controls, False, False, 0)

        # Action buttons
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_bar.set_margin_bottom(4)

        self.start_btn = self.make_button(
            "Start Capture", self._on_start_capture, "gp-btn-primary"
        )
        btn_bar.pack_start(self.start_btn, False, False, 0)

        self.stop_btn = self.make_button("Stop", self._on_stop_capture, "gp-btn-danger")
        self.stop_btn.set_sensitive(False)
        btn_bar.pack_start(self.stop_btn, False, False, 0)

        self.clear_btn = self.make_button("Clear", self._on_clear_live, "gp-btn")
        btn_bar.pack_start(self.clear_btn, False, False, 0)

        self.save_btn = self.make_button("Save .pcap", self._on_save_capture, "gp-btn")
        self.save_btn.set_sensitive(False)
        btn_bar.pack_start(self.save_btn, False, False, 0)

        btn_bar.pack_end(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)

        self.pkt_counter_label = self.make_label("0 packets", "gp-dim")
        self.pkt_counter_label.set_halign(Gtk.Align.END)
        btn_bar.pack_end(self.pkt_counter_label, False, False, 4)

        left_box.pack_start(btn_bar, False, False, 0)

        # Packet TreeView
        self.live_store = Gtk.ListStore(str, str, str, str, str, str, str, str)
        self.live_tree = Gtk.TreeView(model=self.live_store)
        self.live_tree.set_headers_visible(True)
        self.live_tree.set_enable_search(False)

        col_defs = [
            ("#", 0, 50),
            ("Time", 1, 80),
            ("Source", 2, 130),
            ("Destination", 3, 130),
            ("Protocol", 4, 65),
            ("Length", 5, 55),
            ("Info", 6, 250),
        ]
        for title, idx, width in col_defs:
            renderer = Gtk.CellRendererText()
            renderer.set_property("font", "monospace 10")
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title, renderer, text=idx, background=7)
            col.set_min_width(width)
            col.set_resizable(True)
            if title == "Info":
                col.set_expand(True)
            self.live_tree.append_column(col)

        scroll = self.make_scrolled(self.live_tree)
        left_box.pack_start(scroll, True, True, 0)

        paned.pack1(left_box, True, False)

        # Right: live stats sidebar
        self._stats_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._stats_sidebar.get_style_context().add_class("gp-sidebar")
        self._stats_sidebar.set_size_request(220, -1)

        stats_title = self.make_label("LIVE STATS", "gp-accent")
        stats_title.set_halign(Gtk.Align.CENTER)
        self._stats_sidebar.pack_start(stats_title, False, False, 4)

        sep = Gtk.Separator()
        self._stats_sidebar.pack_start(sep, False, False, 0)

        # Stats fields
        self._stats_pps_label = self.make_label("Packets/sec: --", "gp-text")
        self._stats_sidebar.pack_start(self._stats_pps_label, False, False, 2)

        self._stats_bps_label = self.make_label("Bytes/sec: --", "gp-text")
        self._stats_sidebar.pack_start(self._stats_bps_label, False, False, 2)

        self._stats_total_label = self.make_label("Total: 0 packets", "gp-text")
        self._stats_sidebar.pack_start(self._stats_total_label, False, False, 2)

        self._stats_bytes_label = self.make_label("Total bytes: 0", "gp-text")
        self._stats_sidebar.pack_start(self._stats_bytes_label, False, False, 2)

        sep2 = Gtk.Separator()
        self._stats_sidebar.pack_start(sep2, False, False, 4)

        # Protocol distribution (Cairo pie chart)
        proto_title = self.make_label("PROTOCOLS", "gp-accent")
        self._stats_sidebar.pack_start(proto_title, False, False, 2)

        self._proto_da = Gtk.DrawingArea()
        self._proto_da.set_size_request(200, 120)
        self._proto_da.connect("draw", self._draw_proto_pie)
        self._stats_sidebar.pack_start(self._proto_da, False, False, 0)

        sep3 = Gtk.Separator()
        self._stats_sidebar.pack_start(sep3, False, False, 4)

        # Top 5 talkers
        talkers_title = self.make_label("TOP SOURCES", "gp-accent")
        self._stats_sidebar.pack_start(talkers_title, False, False, 2)

        self._stats_talkers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self._stats_sidebar.pack_start(self._stats_talkers_box, False, False, 0)

        sep4 = Gtk.Separator()
        self._stats_sidebar.pack_start(sep4, False, False, 4)

        # Active Flows (live conntrack during capture)
        flows_title = self.make_label("ACTIVE FLOWS", "gp-accent")
        self._stats_sidebar.pack_start(flows_title, False, False, 2)

        self._live_flows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self._stats_sidebar.pack_start(self._live_flows_box, False, False, 0)

        scrolled_stats = self.make_scrolled(self._stats_sidebar)
        scrolled_stats.set_min_content_width(220)
        paned.pack2(scrolled_stats, False, True)
        paned.set_position(750)

        return paned

    # ── Tab 2: File Analysis ──────────────────────────────────────────

    def _build_file_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_top(6)
        vbox.set_margin_start(4)
        vbox.set_margin_end(4)

        file_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        file_bar.set_margin_bottom(4)

        open_btn = self.make_button("Open File", self._on_open_file, "gp-btn-primary")
        file_bar.pack_start(open_btn, False, False, 0)

        self.file_label = self.make_label("No file loaded", "gp-dim")
        file_bar.pack_start(self.file_label, True, True, 6)

        vbox.pack_start(file_bar, False, False, 0)

        # Sub-notebook: Packets | Protocols | Top Talkers | DNS | Flows
        self.analysis_notebook = Gtk.Notebook()

        self.file_packet_view = self._make_text_view()
        self.analysis_notebook.append_page(
            self.make_scrolled(self.file_packet_view), Gtk.Label(label="Packets")
        )

        self.file_proto_view = self._make_text_view()
        self.analysis_notebook.append_page(
            self.make_scrolled(self.file_proto_view), Gtk.Label(label="Protocols")
        )

        self.file_talkers_view = self._make_text_view()
        self.analysis_notebook.append_page(
            self.make_scrolled(self.file_talkers_view), Gtk.Label(label="Top Talkers")
        )

        self.file_dns_view = self._make_text_view()
        self.analysis_notebook.append_page(
            self.make_scrolled(self.file_dns_view), Gtk.Label(label="DNS")
        )

        # New: Flows tab
        self.file_flows_view = self._make_text_view()
        self.analysis_notebook.append_page(
            self.make_scrolled(self.file_flows_view), Gtk.Label(label="Flows")
        )

        vbox.pack_start(self.analysis_notebook, True, True, 0)

        return vbox

    def _make_text_view(self):
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_wrap_mode(Gtk.WrapMode.NONE)
        tv.set_monospace(True)
        tv.set_left_margin(8)
        tv.set_top_margin(6)
        tv.get_style_context().add_class("gp-mono")
        return tv

    # ── Live Stats Drawing ───────────────────────────────────────────

    def _draw_proto_pie(self, widget, cr):
        """Draw a simple protocol distribution pie chart."""
        c = self.colors
        alloc = widget.get_allocation()
        w = alloc.width
        h = alloc.height
        r_a, g_a, b_a = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0

        protos = self._live_stats["protocols"]
        if not protos:
            cr.set_source_rgba(r_a, g_a, b_a, 0.3)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(10)
            cr.move_to(w / 2 - 30, h / 2)
            cr.show_text("No data")
            return

        total = sum(protos.values())
        if total == 0:
            return

        top = protos.most_common(6)
        cx, cy = w / 2 - 30, h / 2
        radius = min(cx - 10, cy - 10, 45)

        # Color palette (derived from theme)
        def _h2f(h):
            h = h.lstrip("#")
            return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
        c = self.colors
        ir, ig, ib = _h2f(c["info"])
        wr, wg, wb = _h2f(c["warning"])
        dr, dg, db = _h2f(c["danger"])
        sr, sg, sb = _h2f(c["success"])
        palette = [
            (r_a, g_a, b_a, 0.9),
            (r_a * 0.7, g_a * 0.7, b_a * 0.7, 0.8),
            (ir, ig, ib, 0.7),
            (wr, wg, wb, 0.7),
            (dr, dg, db, 0.7),
            (sr, sg, sb, 0.7),
        ]

        import math
        start_angle = -math.pi / 2
        for i, (proto, count) in enumerate(top):
            fraction = count / total
            end_angle = start_angle + fraction * 2 * math.pi
            color = palette[i % len(palette)]

            cr.set_source_rgba(*color)
            cr.move_to(cx, cy)
            cr.arc(cx, cy, radius, start_angle, end_angle)
            cr.close_path()
            cr.fill()

            # Legend entry
            legend_x = cx + radius + 15
            legend_y = 10 + i * 16
            cr.set_source_rgba(*color)
            cr.rectangle(legend_x, legend_y, 10, 10)
            cr.fill()

            cr.set_source_rgba(r_a, g_a, b_a, 0.8)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8)
            pct = int(fraction * 100)
            cr.move_to(legend_x + 14, legend_y + 9)
            cr.show_text(f"{proto} {pct}%")

            start_angle = end_angle

    def _update_live_stats(self):
        """Update live stats sidebar labels."""
        stats = self._live_stats
        elapsed = time.time() - stats["start_time"] if stats["start_time"] > 0 else 1
        elapsed = max(elapsed, 1)

        pps = self.packet_count / elapsed
        bps = stats["total_bytes"] / elapsed

        self._stats_pps_label.set_text(f"Packets/sec: {pps:.1f}")
        self._stats_bps_label.set_text(f"Bytes/sec: {self._fmt_rate(bps)}")
        self._stats_total_label.set_text(f"Total: {self.packet_count} packets")
        self._stats_bytes_label.set_text(f"Total bytes: {self._fmt_bytes(stats['total_bytes'])}")

        # Update top talkers
        for child in self._stats_talkers_box.get_children():
            self._stats_talkers_box.remove(child)

        top_sources = stats["sources"].most_common(5)
        for src, count in top_sources:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            lbl_src = self.make_label(src[:18], "gp-text")
            lbl_src.set_size_request(120, -1)
            row.pack_start(lbl_src, False, False, 0)
            lbl_count = self.make_label(str(count), "gp-dim")
            row.pack_start(lbl_count, False, False, 0)
            self._stats_talkers_box.pack_start(row, False, False, 0)

        if not top_sources:
            self._stats_talkers_box.pack_start(
                self.make_label("--", "gp-dim"), False, False, 0
            )
        self._stats_talkers_box.show_all()

        # Redraw protocol pie
        self._proto_da.queue_draw()

    @staticmethod
    def _fmt_rate(bps):
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        else:
            return f"{bps / (1024 * 1024):.1f} MB/s"

    @staticmethod
    def _fmt_bytes(b):
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        else:
            return f"{b / (1024 * 1024):.1f} MB"

    # ── Live Capture Logic ────────────────────────────────────────────

    def _on_preset_changed(self, combo):
        idx = combo.get_active()
        if 0 <= idx < len(FILTER_PRESETS):
            _, bpf = FILTER_PRESETS[idx]
            self.filter_entry.set_text(bpf)

    def _on_start_capture(self, _btn):
        if self.capturing:
            return

        try:
            stat = os.statvfs(CAPTURES_DIR)
            free_mb = (stat.f_bavail * stat.f_frsize) // (1024 * 1024)
            if free_mb < MIN_DISK_FREE_MB:
                self.set_status(f"Error: Only {free_mb} MB free — need at least {MIN_DISK_FREE_MB} MB")
                return
        except Exception:
            pass

        iface = self.iface_combo.get_active_text() or "eth0"
        bpf_filter = self.filter_entry.get_text().strip()
        limit_text = self.limit_entry.get_text().strip()

        try:
            pkt_limit = int(limit_text) if limit_text else MAX_LIVE_PACKETS
            if pkt_limit < 1:
                pkt_limit = MAX_LIVE_PACKETS
        except ValueError:
            pkt_limit = MAX_LIVE_PACKETS

        # Live capture writes to a scratch buffer in /tmp. The file becomes
        # permanent only when the user clicks Save .pcap. Any prior buffer
        # file is purged here so test captures don't accumulate.
        os.makedirs(BUFFER_DIR, exist_ok=True)
        if self.capture_output_file and os.path.isfile(self.capture_output_file):
            try:
                os.unlink(self.capture_output_file)
            except OSError:
                pass
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.capture_output_file = os.path.join(BUFFER_DIR, f"dragnet_{ts}.pcap")

        cmd = [
            "tshark",
            "-i", iface,
            "-l",
            "-w", self.capture_output_file,
            "-P",
            "-T", "fields",
            "-e", "frame.number",
            "-e", "frame.time_relative",
            "-e", "ip.src",
            "-e", "ip.dst",
            "-e", "_ws.col.Protocol",
            "-e", "frame.len",
            "-e", "_ws.col.Info",
            "-E", "separator=\t",
        ]

        if bpf_filter:
            cmd.extend(["-f", bpf_filter])
        cmd.extend(["-c", str(pkt_limit)])
        cmd.extend(["-a", f"filesize:{MAX_PCAP_SIZE_KB}"])
        cmd.extend(["-a", "duration:600"])

        try:
            self.capture_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except PermissionError:
            self.set_status("Error: Permission denied. Need wireshark group membership.")
            return
        except FileNotFoundError:
            self.set_status("Error: tshark not found.")
            return
        except Exception as e:
            self.set_status(f"Error: {e}")
            return

        self.capturing = True
        self.packet_count = 0
        self.live_store.clear()
        self._live_stats = {
            "start_time": time.time(),
            "total_bytes": 0,
            "protocols": Counter(),
            "sources": Counter(),
            "destinations": Counter(),
        }

        self.start_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)
        self.save_btn.set_sensitive(False)
        self.iface_combo.set_sensitive(False)
        self.preset_combo.set_sensitive(False)
        self.filter_entry.set_sensitive(False)
        self.limit_entry.set_sensitive(False)

        self.set_status(f"Capturing on {iface}...")
        self.pkt_counter_label.set_text("0 packets")

        # Stats update timer (every 5s during capture)
        self._stats_timer = GLib.timeout_add_seconds(5, self._stats_tick)
        self._timers.append(self._stats_timer)

        self.capture_thread = threading.Thread(
            target=self._capture_reader, daemon=True
        )
        self.capture_thread.start()

    def _stats_tick(self):
        """Update live stats periodically during capture."""
        if not self.capturing:
            return False  # Stop timer
        self._update_live_stats()
        self._update_live_flows()
        return True

    def _update_live_flows(self):
        """Parse conntrack and show top 10 flows by bytes in the sidebar."""
        for child in self._live_flows_box.get_children():
            self._live_flows_box.remove(child)

        try:
            result = subprocess.run(
                ["sudo", "conntrack", "-L"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                self._live_flows_box.pack_start(
                    self.make_label("conntrack unavailable", "gp-dim"), False, False, 0
                )
                self._live_flows_box.show_all()
                return

            flows = []
            for line in result.stdout.strip().split("\n")[:100]:
                if not line.strip():
                    continue
                # Extract protocol, src, dst, sport, dport, bytes
                proto_m = re.match(r'(\w+)', line)
                src_m = re.search(r'src=(\S+)', line)
                dst_m = re.search(r'dst=(\S+)', line)
                sport_m = re.search(r'sport=(\d+)', line)
                dport_m = re.search(r'dport=(\d+)', line)
                bytes_m = re.search(r'bytes=(\d+)', line)

                if src_m and dst_m:
                    proto = proto_m.group(1) if proto_m else "?"
                    src = src_m.group(1)
                    dst = dst_m.group(1)
                    port = dport_m.group(1) if dport_m else "?"
                    byte_count = int(bytes_m.group(1)) if bytes_m else 0
                    flows.append((byte_count, proto, src, dst, port))

            # Sort by bytes descending, show top 10
            flows.sort(key=lambda f: f[0], reverse=True)
            for byte_count, proto, src, dst, port in flows[:10]:
                if byte_count > 1048576:
                    bstr = f"{byte_count / 1048576:.1f}M"
                elif byte_count > 1024:
                    bstr = f"{byte_count / 1024:.0f}K"
                else:
                    bstr = f"{byte_count}B"
                label_text = f"{proto} {src}->{dst}:{port} {bstr}"
                lbl = self.make_label(label_text[:40], "gp-text")
                self._live_flows_box.pack_start(lbl, False, False, 0)

            if not flows:
                self._live_flows_box.pack_start(
                    self.make_label("No flows", "gp-dim"), False, False, 0
                )
        except Exception:
            self._live_flows_box.pack_start(
                self.make_label("--", "gp-dim"), False, False, 0
            )

        self._live_flows_box.show_all()

    def _capture_reader(self):
        try:
            for line in self.capture_proc.stdout:
                if not self.capturing:
                    break
                stripped = line.strip()
                if stripped:
                    GLib.idle_add(self._add_packet_row, stripped)
        except Exception:
            pass
        finally:
            GLib.idle_add(self._capture_finished)

    def _add_packet_row(self, line):
        if not self.capturing:
            return False

        parts = line.split("\t")
        while len(parts) < 7:
            parts.append("")

        num, time_rel, src, dst, proto, length, info = parts[:7]

        # Track live stats
        try:
            pkt_len = int(length.strip()) if length.strip() else 0
        except ValueError:
            pkt_len = 0
        self._live_stats["total_bytes"] += pkt_len

        proto_clean = proto.strip()
        if proto_clean:
            self._live_stats["protocols"][proto_clean] += 1
            # Cap counter size
            if len(self._live_stats["protocols"]) > 100:
                self._live_stats["protocols"] = Counter(
                    dict(self._live_stats["protocols"].most_common(50))
                )

        src_clean = src.strip()
        if src_clean:
            self._live_stats["sources"][src_clean] += 1
            if len(self._live_stats["sources"]) > 500:
                self._live_stats["sources"] = Counter(
                    dict(self._live_stats["sources"].most_common(100))
                )

        dst_clean = dst.strip()
        if dst_clean:
            self._live_stats["destinations"][dst_clean] += 1
            if len(self._live_stats["destinations"]) > 500:
                self._live_stats["destinations"] = Counter(
                    dict(self._live_stats["destinations"].most_common(100))
                )

        # Row color
        proto_lower = proto_clean.lower()
        bg_color = None
        if "dns" in proto_lower:
            bg_color = self._hex_to_rgba_css(self.colors['info'], 0.08)
        elif proto_lower in ("tls", "ssl", "https", "quic"):
            bg_color = f"rgba({self.colors['r']},{self.colors['g']},{self.colors['b']}, 0.05)"
        elif proto_lower == "http":
            bg_color = self._hex_to_rgba_css(self.colors['danger'], 0.08)

        if self.packet_count >= MAX_LIVE_PACKETS:
            first_iter = self.live_store.get_iter_first()
            if first_iter:
                self.live_store.remove(first_iter)

        self.live_store.append([
            num.strip(),
            time_rel.strip(),
            src_clean,
            dst_clean,
            proto_clean,
            length.strip(),
            info.strip(),
            bg_color or "",
        ])

        self.packet_count += 1
        self.pkt_counter_label.set_text(f"{self.packet_count} packets")

        # Auto-scroll
        adj = self.live_tree.get_parent()
        if isinstance(adj, Gtk.ScrolledWindow):
            vadj = adj.get_vadjustment()
            vadj.set_value(vadj.get_upper() - vadj.get_page_size())

        return False

    def _capture_finished(self):
        self.capturing = False
        self.start_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        self.iface_combo.set_sensitive(True)
        self.preset_combo.set_sensitive(True)
        self.filter_entry.set_sensitive(True)
        self.limit_entry.set_sensitive(True)

        # Final stats update
        self._update_live_stats()

        if (self.capture_output_file
                and os.path.isfile(self.capture_output_file)
                and os.path.getsize(self.capture_output_file) > 0):
            self.save_btn.set_sensitive(True)
            size = os.path.getsize(self.capture_output_file)
            size_str = f"{size/1024:.1f} KB" if size < 1048576 else f"{size/1048576:.1f} MB"
            self.set_status(
                f"Capture complete: {self.packet_count} packets | {size_str} | "
                f"{os.path.basename(self.capture_output_file)}"
            )
        else:
            self.set_status(f"Capture ended: {self.packet_count} packets")
            if (self.capture_output_file
                    and os.path.isfile(self.capture_output_file)
                    and os.path.getsize(self.capture_output_file) == 0):
                try:
                    os.unlink(self.capture_output_file)
                except Exception:
                    pass

        return False

    def _on_stop_capture(self, _btn):
        self.capturing = False
        if self.capture_proc:
            try:
                self.capture_proc.terminate()
                self.capture_proc.wait(timeout=5)
            except Exception:
                try:
                    self.capture_proc.kill()
                except Exception:
                    pass
            self.capture_proc = None
        self.set_status("Capture stopped")

    def _on_clear_live(self, _btn):
        self.live_store.clear()
        self.packet_count = 0
        self.pkt_counter_label.set_text("0 packets")
        self._live_stats = {
            "start_time": 0,
            "total_bytes": 0,
            "protocols": Counter(),
            "sources": Counter(),
            "destinations": Counter(),
        }
        self._update_live_stats()
        self.set_status("Cleared")

    def _on_save_capture(self, _btn):
        if not self.capture_output_file or not os.path.isfile(self.capture_output_file):
            self.set_status("No capture file to save")
            return

        dialog = Gtk.FileChooserDialog(
            title="Save Capture As",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_folder(CAPTURES_DIR)
        dialog.set_current_name(os.path.basename(self.capture_output_file))

        pcap_filter = Gtk.FileFilter()
        pcap_filter.set_name("Packet Captures (*.pcap)")
        pcap_filter.add_pattern("*.pcap")
        dialog.add_filter(pcap_filter)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            dest = dialog.get_filename()
            try:
                if os.path.abspath(dest) != os.path.abspath(self.capture_output_file):
                    import shutil as _shutil
                    _shutil.copy2(self.capture_output_file, dest)
                self.set_status(f"Saved: {dest}")
            except Exception as e:
                self.set_status(f"Save error: {e}")
        dialog.destroy()

    # ── File Analysis Logic ───────────────────────────────────────────

    def _on_open_file(self, _btn):
        dialog = Gtk.FileChooserDialog(
            title="Open Capture File",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )

        pcap_filter = Gtk.FileFilter()
        pcap_filter.set_name("Packet Captures")
        pcap_filter.add_pattern("*.pcap")
        pcap_filter.add_pattern("*.pcapng")
        pcap_filter.add_pattern("*.cap")
        dialog.add_filter(pcap_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All Files")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)

        if os.path.isdir(CAPTURES_DIR):
            dialog.set_current_folder(CAPTURES_DIR)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            dialog.destroy()
            self._analyze_file(filepath)
        else:
            dialog.destroy()

    def _analyze_file(self, filepath):
        if not os.path.isfile(filepath):
            self.set_status(f"File not found: {filepath}")
            return

        self.file_label.set_text(os.path.basename(filepath))
        self.set_status(f"Analyzing {os.path.basename(filepath)}...")

        for view in [
            self.file_packet_view,
            self.file_proto_view,
            self.file_talkers_view,
            self.file_dns_view,
            self.file_flows_view,
        ]:
            view.get_buffer().set_text("Loading...")

        self.run_async(
            lambda: self._do_analysis(filepath),
            self._on_analysis_done,
        )

    def _do_analysis(self, filepath):
        results = {}

        # Packets
        stdout, stderr, rc = self.run_cmd(
            ["tshark", "-r", filepath, "-c", "500"], timeout=60
        )
        results["packets"] = stdout if rc == 0 else f"Error: {stderr}"

        # Protocols
        stdout, stderr, rc = self.run_cmd(
            ["tshark", "-r", filepath, "-q", "-z", "io,phs"], timeout=60
        )
        results["protocols"] = stdout if rc == 0 else f"Error: {stderr}"

        # Top talkers
        stdout, stderr, rc = self.run_cmd(
            ["tshark", "-r", filepath, "-T", "fields", "-e", "ip.src"], timeout=60
        )
        if rc == 0 and stdout:
            src_counts = Counter(
                line.strip() for line in stdout.split("\n") if line.strip()
            )
            src_top = src_counts.most_common(20)
        else:
            src_top = []

        stdout, stderr, rc = self.run_cmd(
            ["tshark", "-r", filepath, "-T", "fields", "-e", "ip.dst"], timeout=60
        )
        if rc == 0 and stdout:
            dst_counts = Counter(
                line.strip() for line in stdout.split("\n") if line.strip()
            )
            dst_top = dst_counts.most_common(20)
        else:
            dst_top = []

        talkers_text = "TOP SOURCE IPs\n" + "=" * 50 + "\n"
        if src_top:
            for ip, count in src_top:
                talkers_text += f"  {count:>8}  {ip}\n"
        else:
            talkers_text += "  No IP source data\n"
        talkers_text += f"\nTOP DESTINATION IPs\n" + "=" * 50 + "\n"
        if dst_top:
            for ip, count in dst_top:
                talkers_text += f"  {count:>8}  {ip}\n"
        else:
            talkers_text += "  No IP destination data\n"
        results["talkers"] = talkers_text

        # DNS
        stdout, stderr, rc = self.run_cmd(
            [
                "tshark", "-r", filepath,
                "-T", "fields", "-e", "dns.qry.name",
                "-Y", "dns.qry.name",
            ],
            timeout=60,
        )
        if rc == 0 and stdout:
            dns_counts = Counter(
                line.strip() for line in stdout.split("\n") if line.strip()
            )
            dns_top = dns_counts.most_common(30)
            dns_text = "DNS QUERIES\n" + "=" * 50 + "\n"
            for domain, count in dns_top:
                dns_text += f"  {count:>8}  {domain}\n"
        else:
            dns_text = "DNS QUERIES\n" + "=" * 50 + "\n  No DNS queries found\n"
        results["dns"] = dns_text

        # Flows (conversations)
        stdout, stderr, rc = self.run_cmd(
            ["tshark", "-r", filepath, "-q", "-z", "conv,ip"], timeout=60
        )
        if rc == 0 and stdout:
            results["flows"] = stdout
        else:
            # Try TCP conversations
            stdout2, stderr2, rc2 = self.run_cmd(
                ["tshark", "-r", filepath, "-q", "-z", "conv,tcp"], timeout=60
            )
            if rc2 == 0 and stdout2:
                results["flows"] = stdout2
            else:
                results["flows"] = "No flow data available\n(Requires IP or TCP conversations)"

        # File stats
        try:
            size = os.path.getsize(filepath)
            size_str = (
                f"{size/1024:.1f} KB" if size < 1048576 else f"{size/1048576:.1f} MB"
            )
        except Exception:
            size_str = "unknown"

        stdout2, _, rc2 = self.run_cmd(
            ["tshark", "-r", filepath, "-T", "fields", "-e", "frame.number"],
            timeout=60,
        )
        total = len(stdout2.strip().split("\n")) if rc2 == 0 and stdout2.strip() else 0
        results["status"] = f"{total} packets | {size_str} | {os.path.basename(filepath)}"

        return results

    def _on_analysis_done(self, results):
        if isinstance(results, Exception):
            self.set_status(f"Analysis error: {results}")
            return

        self.file_packet_view.get_buffer().set_text(
            results.get("packets", "No data")
        )
        self.file_proto_view.get_buffer().set_text(
            results.get("protocols", "No data")
        )
        self.file_talkers_view.get_buffer().set_text(
            results.get("talkers", "No data")
        )
        self.file_dns_view.get_buffer().set_text(
            results.get("dns", "No data")
        )
        self.file_flows_view.get_buffer().set_text(
            results.get("flows", "No data")
        )
        self.set_status(results.get("status", "Analysis complete"))

    # ── Theme ────────────────────────────────────────────────────────

    def on_theme_changed(self):
        self._apply_css(self._extra_css())
        self._proto_da.queue_draw()

    # ── Cleanup ───────────────────────────────────────────────────────

    def _on_destroy(self, *args):
        self.capturing = False
        if self.capture_proc:
            try:
                self.capture_proc.terminate()
                self.capture_proc.wait(timeout=3)
            except Exception:
                try:
                    self.capture_proc.kill()
                except Exception:
                    pass
            self.capture_proc = None
        super()._on_destroy(*args)


# ── Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    app = DragnetApp()

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        GLib.idle_add(lambda: (
            app.main_notebook.set_current_page(1) if hasattr(app, 'main_notebook') else None,
            app._analyze_file(sys.argv[1]) if hasattr(app, '_analyze_file') else None,
        ))

    app.run()
