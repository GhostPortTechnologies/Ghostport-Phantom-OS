#!/usr/bin/env python3
"""gp-stonefish — Stonefish: ARP Spoofing Detector for Phantom OS"""
import sys, os
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import json
import re
import time
import subprocess

CONFIG_DIR = os.path.expanduser("~/.config/phantom")
BASELINE_FILE = os.path.join(CONFIG_DIR, "arp-baseline.json")

# ── Helpers ──────────────────────────────────────────────────────────

def run_cmd(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""

def parse_arp_table():
    """Parse `ip -4 neigh show` for ARP entries."""
    out = run_cmd(["ip", "-4", "neigh", "show"])
    entries = []
    for line in out.splitlines():
        # Format: IP dev IFACE lladdr MAC STATE
        parts = line.split()
        if len(parts) < 4:
            continue
        ip_addr = parts[0]
        iface = parts[2] if parts[1] == "dev" else ""
        mac = ""
        state = ""
        for i, p in enumerate(parts):
            if p == "lladdr" and i + 1 < len(parts):
                mac = parts[i + 1]
            if p in ("REACHABLE", "STALE", "FAILED", "DELAY", "PROBE", "INCOMPLETE", "PERMANENT", "NOARP"):
                state = p
        if not state:
            state = parts[-1] if parts[-1] in ("REACHABLE", "STALE", "FAILED", "DELAY", "PROBE", "INCOMPLETE", "PERMANENT", "NOARP") else "UNKNOWN"
        entries.append({
            "ip": ip_addr,
            "mac": mac,
            "iface": iface,
            "state": state,
        })
    return entries

def load_baseline():
    try:
        with open(BASELINE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_baseline(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(BASELINE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def detect_threats(entries, baseline):
    """Detect ARP spoofing threats. Returns entries with 'threat' field added."""
    # Check for duplicate MACs (same MAC, different IPs)
    mac_to_ips = {}
    for e in entries:
        if e["mac"] and e["mac"] != "":
            mac_to_ips.setdefault(e["mac"], []).append(e["ip"])

    dup_macs = {mac for mac, ips in mac_to_ips.items() if len(ips) > 1}

    # Check gateway MAC changes
    gw_baseline = baseline.get("gateway_macs", {})

    for e in entries:
        threat = "CLEAN"
        if e["mac"] in dup_macs:
            threat = "SPOOFING"
        elif e["ip"] in gw_baseline and gw_baseline[e["ip"]] != e["mac"] and e["mac"]:
            threat = "GW CHANGED"
        elif e["state"] == "FAILED":
            threat = "FAILED"
        e["threat"] = threat

    return entries


# ── Main App ─────────────────────────────────────────────────────────

class StonefishApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Stonefish?",
         "Stonefish watches for ARP spoofing — an attacker on your LAN impersonating "
         "the router (or your device) to intercept traffic.\n\n"
         "In a normal network, each device has a fixed MAC address and the router's "
         "MAC is well-known. An attacker can BROADCAST fake ARP replies claiming "
         "\"hi, I'm the router now\" and devices start sending their traffic to the "
         "attacker instead. It's the classic man-in-the-middle trick. Stonefish "
         "spots it by watching for MAC addresses that suddenly change or claim to be "
         "the gateway when they shouldn't."),

        ("Header — MONITORING + alert count",
         "\"MONITORING\" (green) = Stonefish is actively watching ARP traffic. "
         "If it ever says STOPPED, click Save Baseline to restart.\n\n"
         "\"Alerts: N\" = how many suspicious events have been detected since "
         "Stonefish started. 0 is ideal. Any nonzero count deserves a look in the "
         "table below to see what was flagged."),

        ("Save Baseline button",
         "Takes a snapshot of every (IP, MAC, interface) tuple currently on your "
         "network and treats it as the known-good baseline. Any NEW or CHANGED "
         "combination after this point is flagged as suspicious.\n\n"
         "Click Save Baseline:\n"
         "• On first launch, after all your normal devices are connected.\n"
         "• After adding a new trusted device (so it becomes part of the baseline).\n"
         "• After replacing hardware (router, switch) — MACs change legitimately.\n\n"
         "A device joining the network with a fresh MAC AFTER the baseline is set "
         "gets flagged — which is the point. Review before re-baselining."),

        ("Clear Alerts button",
         "Wipes the current alert list but doesn't touch the baseline. Use this "
         "after you've reviewed the alerts and decided they're all benign "
         "(\"my new phone\", \"the kid's laptop got reconnected\").\n\n"
         "If you want a clean slate AND update the baseline to the current state, "
         "click Save Baseline after Clear Alerts."),

        ("The device table",
         "One row per (IP, MAC, interface) seen on your network. Columns:\n\n"
         "• IP Address — the logical address (192.168.50.x on your LAN).\n"
         "• MAC Address — the physical hardware address.\n"
         "• Interface — which network port saw it (wlan0 = LAN WiFi, eth0 = WAN).\n"
         "• State — one of BASELINE (matches the snapshot), NEW (never seen before), "
         "CHANGED (same IP, different MAC — the attack signature).\n"
         "• Threat — color-coded severity: LOW (amber), HIGH (red), TRUSTED (dim).\n\n"
         "CHANGED + HIGH threat on your gateway IP (usually 192.168.50.1) is the "
         "\"someone is actively attacking you\" signature. Disconnect from WiFi "
         "immediately and investigate."),

        ("What ARP is, in one paragraph",
         "ARP (Address Resolution Protocol) translates IP addresses to MAC addresses "
         "on a local network. When your phone wants to send a packet to 192.168.50.1, "
         "it asks \"who has 192.168.50.1?\" and the router answers with its MAC. "
         "Your phone remembers that mapping and uses it for all traffic to the router.\n\n"
         "An attacker can shout \"192.168.50.1 is ME!\" and devices that hear it "
         "update their ARP table. Now traffic flows through the attacker, who can "
         "read, modify, or drop packets. This only works on the same local network "
         "(same WiFi or ethernet segment) — hence why hostile public WiFi is dangerous."),

        ("False positives to expect",
         "• Mobile devices using MAC randomization — iPhones/Androids do this on "
         "purpose for privacy. Each time they reconnect you'll see a NEW MAC for "
         "a familiar IP. Not an attack — just modern phones.\n\n"
         "• Docker / VM / container hosts — can show many MACs from one interface.\n\n"
         "• Network equipment with failover — redundant routers may swap MACs "
         "during a reboot. Usually a blip.\n\n"
         "When in doubt: note the hostname (Crew Manifest can show it), confirm it's "
         "a device you own, then Save Baseline to accept the new state."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    def __init__(self):
        super().__init__("STONEFISH", "stonefish", (800, 550))
        self.alert_count = 0
        self.previous_entries = []
        self.baseline = load_baseline()
        self.build_ui()
        self.refresh()
        self.poll_start(5, self.refresh)

    def build_ui(self):
        c = self.colors
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.get_style_context().add_class("gp-header")

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_label = Gtk.Label(label="STONEFISH")
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class("gp-header-title")
        title_box.pack_start(title_label, False, False, 0)
        sub_label = Gtk.Label(label="ARP Spoofing Detector")
        sub_label.set_halign(Gtk.Align.START)
        sub_label.get_style_context().add_class("gp-header-subtitle")
        title_box.pack_start(sub_label, False, False, 0)
        header_box.pack_start(title_box, True, True, 8)

        # Status indicator
        self.monitor_label = self.make_label("MONITORING", "gp-success")
        self.monitor_label.set_halign(Gtk.Align.END)
        header_box.pack_start(self.monitor_label, False, False, 4)

        # Alert count
        self.alert_label = self.make_label("Alerts: 0", "gp-dim")
        self.alert_label.set_halign(Gtk.Align.END)
        header_box.pack_start(self.alert_label, False, False, 8)

        root.pack_start(header_box, False, False, 0)

        # Button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(8)
        btn_bar.set_margin_end(8)
        btn_bar.set_margin_top(6)
        btn_bar.set_margin_bottom(4)

        save_btn = self.make_button("Save Baseline", self.on_save_baseline, "gp-btn-primary")
        btn_bar.pack_start(save_btn, False, False, 0)

        clear_btn = self.make_button("Clear Alerts", self.on_clear_alerts, "gp-btn")
        btn_bar.pack_start(clear_btn, False, False, 0)

        btn_bar.pack_end(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)

        root.pack_start(btn_bar, False, False, 0)

        # TreeView
        # Columns: IP, MAC, Interface, State, Threat
        self.store = Gtk.ListStore(str, str, str, str, str)
        self.treeview = Gtk.TreeView(model=self.store)
        self.treeview.set_headers_visible(True)

        columns = [
            ("IP Address", 0, 150),
            ("MAC Address", 1, 170),
            ("Interface", 2, 100),
            ("State", 3, 100),
            ("Threat", 4, 120),
        ]

        for title, idx, width in columns:
            renderer = Gtk.CellRendererText()
            renderer.set_property("font", "monospace 10")
            if idx == 4:
                # Threat column gets colored
                col = Gtk.TreeViewColumn(title, renderer, text=idx)
                col.set_cell_data_func(renderer, self._threat_color)
            else:
                col = Gtk.TreeViewColumn(title, renderer, text=idx)
            col.set_min_width(width)
            col.set_resizable(True)
            self.treeview.append_column(col)

        scroll = self.make_scrolled(self.treeview)
        scroll.set_margin_start(8)
        scroll.set_margin_end(8)
        scroll.set_margin_bottom(4)
        root.pack_start(scroll, True, True, 0)

        # Status bar
        self.status_bar = self.make_status_bar("Initializing...")
        root.pack_start(self.status_bar, False, False, 0)

    def _threat_color(self, _column, cell, model, iter_, data=None):
        threat = model.get_value(iter_, 4)
        if threat == "SPOOFING":
            cell.set_property("foreground", self.colors["danger"])
            cell.set_property("weight", 700)
        elif threat == "GW CHANGED":
            cell.set_property("foreground", self.colors["warning"])
            cell.set_property("weight", 700)
        elif threat == "FAILED":
            cell.set_property("foreground", self.colors["dim"])
            cell.set_property("weight", 400)
        else:
            cell.set_property("foreground", self.colors["success"])
            cell.set_property("weight", 400)

    def refresh(self):
        self.run_async(self._scan, self._update_table)
        return True

    def _scan(self):
        entries = parse_arp_table()
        entries = detect_threats(entries, self.baseline)
        return entries

    def _update_table(self, entries):
        if isinstance(entries, Exception):
            self.set_status(f"Error: {entries}")
            return

        # Track previous IPs for new-entry detection
        prev_ips = {e["ip"] for e in self.previous_entries}

        self.store.clear()
        new_alerts = 0
        for e in entries:
            threat = e["threat"]
            if threat in ("SPOOFING", "GW CHANGED"):
                new_alerts += 1
            self.store.append([e["ip"], e["mac"], e["iface"], e["state"], threat])

        if new_alerts > 0:
            self.alert_count += new_alerts
            self.monitor_label.set_text("!! ALERT !!")
            self.monitor_label.get_style_context().remove_class("gp-success")
            self.monitor_label.get_style_context().add_class("gp-danger")
        else:
            self.monitor_label.set_text("MONITORING")
            self.monitor_label.get_style_context().remove_class("gp-danger")
            self.monitor_label.get_style_context().add_class("gp-success")

        self.alert_label.set_text(f"Alerts: {self.alert_count}")
        self.previous_entries = entries
        self.set_status(f"ARP entries: {len(entries)}  |  Scanned: {time.strftime('%H:%M:%S')}  |  Baseline: {'saved' if self.baseline.get('gateway_macs') else 'none'}")

    def on_save_baseline(self, btn):
        """Save current gateway MACs as baseline."""
        entries = parse_arp_table()
        gw_macs = {}
        for e in entries:
            if e["mac"] and e["state"] not in ("FAILED", "INCOMPLETE"):
                gw_macs[e["ip"]] = e["mac"]
        self.baseline = {"gateway_macs": gw_macs, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        save_baseline(self.baseline)
        self.set_status(f"Baseline saved: {len(gw_macs)} entries at {time.strftime('%H:%M:%S')}")

    def on_clear_alerts(self, btn):
        self.alert_count = 0
        self.alert_label.set_text("Alerts: 0")
        self.monitor_label.set_text("MONITORING")
        self.monitor_label.get_style_context().remove_class("gp-danger")
        self.monitor_label.get_style_context().add_class("gp-success")
        self.set_status("Alerts cleared")


if __name__ == "__main__":
    app = StonefishApp()
    app.run()
