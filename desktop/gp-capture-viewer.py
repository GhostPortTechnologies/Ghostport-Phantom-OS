#!/usr/bin/env python3
"""gp-capture-viewer — GTK3 pcap file viewer for Phantom OS.
Opens .pcap files captured by gp-capture and displays packet summaries,
protocol breakdowns, and top talkers in a themed GUI.

Usage: gp-capture-viewer [file.pcap]
  If no file given, opens a file chooser.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import subprocess
import json
import os
import sys
import threading

# ── Theme ──────────────────────────────────────────────────────────────

THEME_FILE = "/etc/phantom/theme.json"

def read_theme_color():
    try:
        with open(THEME_FILE) as f:
            data = json.load(f)
            return data.get("color", "#39ff8f").lstrip("#")
    except Exception:
        return "39ff8f"

def hex_to_rgb(h):
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def derive_colors(hex_color):
    r, g, b = hex_to_rgb(hex_color)
    accent = f"#{hex_color}"
    dim_r, dim_g, dim_b = r*40//100, g*40//100, b*40//100
    dim = f"#{dim_r:02x}{dim_g:02x}{dim_b:02x}"
    br_r = min(255, r + (255-r)*30//100)
    br_g = min(255, g + (255-g)*30//100)
    br_b = min(255, b + (255-b)*30//100)
    bright = f"#{br_r:02x}{br_g:02x}{br_b:02x}"
    bg = f"#{r*4//100:02x}{g*4//100:02x}{b*4//100:02x}"
    bg2 = f"#{r*7//100:02x}{g*7//100:02x}{b*7//100:02x}"
    return {
        "accent": accent, "dim": dim, "bright": bright,
        "bg": bg, "bg2": bg2,
        "text": f"#{min(255,r*22//100+200):02x}{min(255,g*22//100+200):02x}{min(255,b*22//100+200):02x}",
    }

# ── tshark helpers ──────────────────────────────────────────────────────

def run_tshark(args, timeout=30):
    try:
        result = subprocess.run(
            ["tshark"] + args,
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception as e:
        return f"Error: {e}"

def get_packet_list(pcap_file, limit=500):
    output = run_tshark(["-r", pcap_file, "-c", str(limit)])
    return output.split("\n") if output else []

def get_protocol_hierarchy(pcap_file):
    output = run_tshark(["-r", pcap_file, "-q", "-z", "io,phs"])
    return output

def get_top_talkers(pcap_file, field="ip.src", limit=10):
    # Limit to first 50k packets to avoid memory explosion on large captures
    output = run_tshark(["-r", pcap_file, "-c", "50000", "-T", "fields", "-e", field])
    if not output:
        return []
    counts = {}
    for line in output.split("\n"):
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
            if len(counts) > 10000:
                break  # cap unique entries to prevent memory bloat
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:limit]

def get_dns_queries(pcap_file, limit=20):
    # Limit to first 50k packets to avoid memory explosion on large captures
    output = run_tshark(["-r", pcap_file, "-c", "50000", "-T", "fields", "-e", "dns.qry.name",
                         "-Y", "dns.flags.response == 0"])
    if not output:
        return []
    counts = {}
    for line in output.split("\n"):
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
            if len(counts) > 10000:
                break  # cap unique entries to prevent memory bloat
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:limit]

def get_packet_count(pcap_file):
    output = run_tshark(["-r", pcap_file, "-q", "-z", "io,stat,0"])
    for line in output.split("\n"):
        if "frames" in line.lower() or "|" in line:
            parts = line.split("|")
            for p in parts:
                p = p.strip()
                if p.isdigit() and int(p) > 0:
                    return int(p)
    # Fallback: count lines
    packets = run_tshark(["-r", pcap_file, "-T", "fields", "-e", "frame.number"])
    return len(packets.split("\n")) if packets else 0


# ── Main Window ─────────────────────────────────────────────────────────

class CaptureViewer(Gtk.Window):
    def __init__(self, pcap_file=None):
        super().__init__(title="GhostPort Packet Viewer")
        self.set_default_size(900, 650)
        self.pcap_file = pcap_file
        self.colors = derive_colors(read_theme_color())
        self._build_ui()
        self._apply_css()
        if pcap_file:
            self._load_file(pcap_file)

    def _apply_css(self):
        c = self.colors
        css = f"""
        window {{ background-color: {c['bg']}; }}
        .header-label {{ color: {c['accent']}; font-size: 16px; font-weight: bold; font-family: monospace; }}
        .dim-label {{ color: {c['dim']}; font-size: 11px; font-family: monospace; }}
        .bright-label {{ color: {c['bright']}; font-size: 12px; font-family: monospace; }}
        .text-label {{ color: {c['text']}; font-size: 11px; font-family: monospace; }}
        .packet-view {{ background-color: {c['bg2']}; color: {c['text']}; font-family: monospace; font-size: 10px; }}
        .stats-view {{ background-color: {c['bg2']}; color: {c['text']}; font-family: monospace; font-size: 11px; }}
        notebook tab {{ background-color: {c['bg2']}; color: {c['dim']}; padding: 6px 12px; }}
        notebook tab:checked {{ background-color: {c['bg']}; color: {c['accent']}; }}
        button {{ background-color: {c['bg2']}; color: {c['accent']}; border: 1px solid {c['dim']}; padding: 4px 12px; font-family: monospace; }}
        button:hover {{ background-color: {c['dim']}; }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.set_margin_start(12)
        header.set_margin_end(12)
        header.set_margin_top(8)
        header.set_margin_bottom(4)

        title = Gtk.Label(label="PACKET VIEWER")
        title.get_style_context().add_class("header-label")
        header.pack_start(title, False, False, 0)

        self.file_label = Gtk.Label(label="No file loaded")
        self.file_label.get_style_context().add_class("dim-label")
        header.pack_start(self.file_label, True, True, 0)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self._on_open)
        header.pack_end(open_btn, False, False, 0)

        vbox.pack_start(header, False, False, 0)

        # Notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_margin_start(8)
        self.notebook.set_margin_end(8)
        self.notebook.set_margin_bottom(8)

        # Tab 1: Packets
        self.packet_scroll = Gtk.ScrolledWindow()
        self.packet_view = Gtk.TextView()
        self.packet_view.set_editable(False)
        self.packet_view.set_cursor_visible(False)
        self.packet_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.packet_view.get_style_context().add_class("packet-view")
        self.packet_view.set_monospace(True)
        self.packet_scroll.add(self.packet_view)
        self.notebook.append_page(self.packet_scroll, Gtk.Label(label="Packets"))

        # Tab 2: Protocols
        self.proto_scroll = Gtk.ScrolledWindow()
        self.proto_view = Gtk.TextView()
        self.proto_view.set_editable(False)
        self.proto_view.set_cursor_visible(False)
        self.proto_view.get_style_context().add_class("stats-view")
        self.proto_view.set_monospace(True)
        self.proto_scroll.add(self.proto_view)
        self.notebook.append_page(self.proto_scroll, Gtk.Label(label="Protocols"))

        # Tab 3: Top Talkers
        self.talkers_scroll = Gtk.ScrolledWindow()
        self.talkers_view = Gtk.TextView()
        self.talkers_view.set_editable(False)
        self.talkers_view.set_cursor_visible(False)
        self.talkers_view.get_style_context().add_class("stats-view")
        self.talkers_view.set_monospace(True)
        self.talkers_scroll.add(self.talkers_view)
        self.notebook.append_page(self.talkers_scroll, Gtk.Label(label="Top Talkers"))

        # Tab 4: DNS
        self.dns_scroll = Gtk.ScrolledWindow()
        self.dns_view = Gtk.TextView()
        self.dns_view.set_editable(False)
        self.dns_view.set_cursor_visible(False)
        self.dns_view.get_style_context().add_class("stats-view")
        self.dns_view.set_monospace(True)
        self.dns_scroll.add(self.dns_view)
        self.notebook.append_page(self.dns_scroll, Gtk.Label(label="DNS Queries"))

        # Tab 5: Help
        help_scroll = Gtk.ScrolledWindow()
        help_view = Gtk.TextView()
        help_view.set_editable(False)
        help_view.set_cursor_visible(False)
        help_view.get_style_context().add_class("stats-view")
        help_view.set_monospace(True)
        help_buf = help_view.get_buffer()
        help_buf.set_text(HELP_TEXT)
        help_scroll.add(help_view)
        self.notebook.append_page(help_scroll, Gtk.Label(label="Help"))

        vbox.pack_start(self.notebook, True, True, 0)

        # Status bar
        self.status = Gtk.Label(label="Open a .pcap file to begin")
        self.status.get_style_context().add_class("dim-label")
        self.status.set_margin_start(12)
        self.status.set_margin_bottom(6)
        self.status.set_halign(Gtk.Align.START)
        vbox.pack_start(self.status, False, False, 0)

    def _on_open(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Open Capture File",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OPEN, Gtk.ResponseType.OK)

        pcap_filter = Gtk.FileFilter()
        pcap_filter.set_name("Packet Captures")
        pcap_filter.add_pattern("*.pcap")
        pcap_filter.add_pattern("*.pcapng")
        dialog.add_filter(pcap_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All Files")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)

        captures_dir = os.path.expanduser("~/captures")
        if os.path.isdir(captures_dir):
            dialog.set_current_folder(captures_dir)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self._load_file(dialog.get_filename())
        dialog.destroy()

    def _load_file(self, filepath):
        self.pcap_file = filepath
        self.file_label.set_text(os.path.basename(filepath))
        self.status.set_text("Loading...")

        # Clear views
        for view in [self.packet_view, self.proto_view, self.talkers_view, self.dns_view]:
            view.get_buffer().set_text("")

        # Load in background thread
        thread = threading.Thread(target=self._analyze, args=(filepath,), daemon=True)
        thread.start()

    def _analyze(self, filepath):
        # Packets
        packets = get_packet_list(filepath, limit=500)
        GLib.idle_add(self._set_text, self.packet_view,
                      "\n".join(packets) if packets else "No packets found")

        # Protocol hierarchy
        proto = get_protocol_hierarchy(filepath)
        GLib.idle_add(self._set_text, self.proto_view, proto or "No protocol data")

        # Top talkers
        src = get_top_talkers(filepath, "ip.src")
        dst = get_top_talkers(filepath, "ip.dst")
        talker_text = "TOP SOURCE IPs\n" + "=" * 40 + "\n"
        for ip, count in src:
            talker_text += f"  {count:>6}  {ip}\n"
        talker_text += f"\nTOP DESTINATION IPs\n" + "=" * 40 + "\n"
        for ip, count in dst:
            talker_text += f"  {count:>6}  {ip}\n"
        GLib.idle_add(self._set_text, self.talkers_view, talker_text)

        # DNS
        dns = get_dns_queries(filepath)
        dns_text = "DNS QUERIES\n" + "=" * 40 + "\n"
        if dns:
            for domain, count in dns:
                dns_text += f"  {count:>6}  {domain}\n"
        else:
            dns_text += "  No DNS queries captured\n"
        GLib.idle_add(self._set_text, self.dns_view, dns_text)

        # Status
        pcount = get_packet_count(filepath)
        size = os.path.getsize(filepath)
        size_str = f"{size/1024:.1f} KB" if size < 1048576 else f"{size/1048576:.1f} MB"
        GLib.idle_add(self.status.set_text,
                      f"{pcount} packets  |  {size_str}  |  {os.path.basename(filepath)}")

    def _set_text(self, view, text):
        view.get_buffer().set_text(text)


HELP_TEXT = """
PACKET VIEWER — How to Use
===========================

WHAT THIS TOOL DOES
  Opens .pcap capture files saved by gp-capture and shows you
  what's inside — without needing Wireshark expertise.

TABS
  Packets     Raw packet list (first 500 packets)
  Protocols   Protocol hierarchy breakdown (what % is HTTP, DNS, etc.)
  Top Talkers Which IPs send and receive the most traffic
  DNS Queries Which domains were looked up during the capture

HOW TO READ PACKETS
  Each line shows: packet#, timestamp, source -> destination, protocol, info
  Example:
    1  0.000000  192.168.50.101 -> 1.1.1.1  DNS  Standard query A example.com
    This means: Your device (192.168.50.101) asked Cloudflare DNS (1.1.1.1)
    for the IP address of example.com.

WHAT TO LOOK FOR
  - Unencrypted HTTP traffic (should be HTTPS instead)
  - DNS queries to suspicious domains
  - Traffic to unexpected IPs (possible tracking/phishing)
  - ARP requests from unknown MACs (possible network intruder)

SAVING CAPTURES
  Captures are saved to ~/captures/ by gp-capture.
  You can also open captures from Wireshark or tcpdump.

PRIVACY NOTE
  Packet captures contain raw network data. They may include
  unencrypted passwords, URLs, and personal information.
  Handle capture files with care and delete when no longer needed.
"""


def main():
    pcap_file = sys.argv[1] if len(sys.argv) > 1 else None

    if pcap_file and not os.path.isfile(pcap_file):
        print(f"File not found: {pcap_file}")
        sys.exit(1)

    win = CaptureViewer(pcap_file)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
