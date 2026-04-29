#!/usr/bin/env python3
"""gp-stonefish — Stonefish: ARP Spoofing Detector for Phantom OS"""
import sys, os
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp
import gp_events

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


# ── OUI + hostname enrichment (Stonefish #1) ──────────────────────────
# Tiny built-in OUI map. Not exhaustive — a curated list of the most common
# vendors seen on US home networks. For unknowns we fall back to "Unknown".
# Source: IEEE public OUI registry. Extend via /etc/phantom/oui-extras.json.
_OUI_BASE = {
    "00:1A:11": "Google", "F4:F5:D8": "Google", "FC:AA:14": "Google",
    "A4:77:33": "Google", "20:DF:B9": "Google", "AC:63:BE": "Google-Nest",
    "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    "28:CD:C1": "Raspberry Pi",
    "AC:DE:48": "Apple", "3C:22:FB": "Apple", "DC:A9:04": "Apple",
    "F8:FF:C2": "Apple", "A4:83:E7": "Apple", "F4:1B:A1": "Apple",
    "00:25:00": "Apple", "14:BD:61": "Apple",
    "00:1D:D8": "Microsoft", "00:50:F2": "Microsoft", "7C:1E:52": "Microsoft",
    "D8:BB:2C": "Microsoft-Surface",
    "00:50:56": "VMware", "00:15:5D": "Microsoft-HyperV",
    "F0:18:98": "Samsung", "5C:0A:5B": "Samsung", "78:25:AD": "Samsung",
    "BC:72:B1": "Samsung-TV", "44:65:0D": "Amazon-Echo", "F0:D2:F1": "Amazon-Echo",
    "00:17:88": "Philips-Hue", "EC:B5:FA": "Philips-Hue",
    "D0:52:A8": "TP-Link", "F0:9F:C2": "Ubiquiti", "24:5A:4C": "Ubiquiti",
    "18:E8:29": "Ubiquiti", "70:8B:CD": "Ubiquiti",
    "88:C9:D0": "LG", "B8:AD:3E": "LG-Smart-TV",
    "E0:75:7D": "Roku", "CC:6D:A0": "Roku",
    "00:25:F2": "Vizio", "18:B4:30": "Vizio",
    "00:11:32": "Synology", "00:00:5E": "IANA (virtual)",
    "94:9A:A9": "Dell", "BC:5F:F4": "Dell",
    "E4:1D:2D": "Mellanox",
    "02:00:00": "Locally-Administered",
    "32:00:00": "Random (likely phone)",
}

def _load_oui_extras():
    try:
        with open("/etc/phantom/oui-extras.json") as f:
            return json.load(f)
    except Exception:
        return {}

_OUI_MAP = {**_OUI_BASE, **_load_oui_extras()}

def oui_lookup(mac):
    """Return vendor name for MAC prefix, or 'Unknown' / 'Random' for locally-admin MACs."""
    if not mac or len(mac) < 8:
        return ""
    try:
        first_byte = int(mac[:2], 16)
    except ValueError:
        return ""
    # Locally-administered bit (second-lowest bit of first byte = 1)
    if first_byte & 0x02:
        return "Random (iOS/Android privacy)"
    prefix = mac.upper().replace("-", ":")[:8]
    return _OUI_MAP.get(prefix, "Unknown")


_LEASES_CACHE = {"ts": 0, "map": {}}

def _load_hostname_map():
    """ip → hostname from dnsmasq leases (both stock and Pi-hole variants)."""
    import time as _t
    if _t.time() - _LEASES_CACHE["ts"] < 5:
        return _LEASES_CACHE["map"]
    mapping = {}
    for path in ["/var/lib/misc/dnsmasq.leases", "/etc/pihole/dhcp.leases"]:
        try:
            with open(path) as f:
                for line in f:
                    parts = line.split()
                    # dnsmasq format: <expiry> <mac> <ip> <hostname> <client_id>
                    if len(parts) >= 4 and parts[3] != "*":
                        mapping[parts[2]] = parts[3]
        except FileNotFoundError:
            continue
        except Exception:
            continue
    _LEASES_CACHE["ts"] = _t.time()
    _LEASES_CACHE["map"] = mapping
    return mapping


def hostname_for(ip):
    return _load_hostname_map().get(ip, "")


# ── Action-hint text (Stonefish #1 companion) ─────────────────────────
_ACTION_HINTS = {
    "SPOOFING":   "Disconnect & investigate",
    "GW CHANGED": "Check router, rebaseline if intended",
    "FAILED":     "Device likely offline",
    "CLEAN":      "",
}
def _action_hint(threat):
    return _ACTION_HINTS.get(threat, "")


# ── Mako notifications (Stonefish #4) ────────────────────────────────
# Rate-limit: same (mac, threat) pair won't re-fire within 5 minutes.
_NOTIFY_COOLDOWN_SEC = 300
_NOTIFY_LAST: dict = {}  # (mac, threat) → last_epoch

def _fire_notifications(alert_details):
    import time as _t
    now = _t.time()
    for threat, entry in alert_details:
        key = (entry.get("mac", ""), threat)
        last = _NOTIFY_LAST.get(key, 0)
        if now - last < _NOTIFY_COOLDOWN_SEC:
            continue
        _NOTIFY_LAST[key] = now
        label = hostname_for(entry["ip"]) or entry["ip"]
        vendor = oui_lookup(entry["mac"])
        vendor_tag = f" [{vendor}]" if vendor and vendor != "Unknown" else ""
        body = f"{threat}: {label}{vendor_tag} — MAC {entry['mac']}"
        try:
            subprocess.Popen(
                ["notify-send", "-u", "critical", "-a", "Stonefish",
                 "Phantom: ARP threat", body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass  # desktop may not be present (headless install)

        # Push to cross-app event bus for correlation with Sonar / Crow's
        # Nest. Categories normalized so the correlation engine can join
        # across sources. FAILED is informational — sleeping clients drop
        # out of the ARP table routinely; emitting them at DANGEROUS
        # pollutes the bus and would skew any future correlation pattern.
        category_map = {
            "GW CHANGED": "arp_gateway_change",
            "SPOOFING": "arp_spoof",
            "FAILED": "arp_failed",
        }
        severity_map = {
            "GW CHANGED": gp_events.SEVERITY_DANGEROUS,
            "SPOOFING": gp_events.SEVERITY_DANGEROUS,
            "FAILED": gp_events.SEVERITY_INFO,
        }
        cat = category_map.get(threat)
        if cat:
            gp_events.emit(
                "stonefish", cat,
                severity_map.get(threat, gp_events.SEVERITY_DANGEROUS),
                f"Stonefish: {threat} on {label}{vendor_tag}",
                details={
                    "ip": entry.get("ip"),
                    "mac": entry.get("mac"),
                    "vendor": vendor,
                    "threat": threat,
                },
            )


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
    # Duplicate-MAC detection is scoped per-interface. A device that connects
    # to our LAN AP (wlan0) AND the upstream router (eth0) legitimately shows
    # the same MAC on both subnets — that's roaming, not spoofing. Only
    # collisions within a single interface are real ARP-table conflicts.
    iface_mac_to_ips = {}
    for e in entries:
        if e["mac"] and e["mac"] != "":
            key = (e.get("iface", ""), e["mac"])
            iface_mac_to_ips.setdefault(key, []).append(e["ip"])

    dup_keys = {k for k, ips in iface_mac_to_ips.items() if len(ips) > 1}

    # Check gateway MAC changes
    gw_baseline = baseline.get("gateway_macs", {})

    for e in entries:
        threat = "CLEAN"
        if (e.get("iface", ""), e["mac"]) in dup_keys:
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
         "• Hostname — from DHCP leases (dnsmasq / Pi-hole). Blank for static-IP "
         "devices or ones that skipped DHCP.\n"
         "• MAC Address — the physical hardware address.\n"
         "• Vendor — OUI lookup against the shipped map (50+ common vendors). "
         "MACs with the locally-administered bit set show \"Random (iOS/Android "
         "privacy)\" — modern phones rotate MACs and that's expected, not an attack.\n"
         "• Interface — which network port saw it (wlan0 = LAN WiFi, eth0 = WAN).\n"
         "• State — one of BASELINE (matches the snapshot), NEW (never seen before), "
         "CHANGED (same IP, different MAC — the attack signature).\n"
         "• Threat — color-coded severity: LOW (amber), HIGH (red), TRUSTED (dim).\n"
         "• Action — plain-English guidance per threat level (\"Disconnect & "
         "investigate\" / \"Check router, rebaseline if intended\" / \"Device "
         "likely offline\").\n\n"
         "CHANGED + HIGH threat on your gateway IP (usually 192.168.50.1) is the "
         "\"someone is actively attacking you\" signature. Disconnect from WiFi "
         "immediately and investigate.\n\n"
         "Extend the OUI map by dropping a JSON file at /etc/phantom/oui-extras.json "
         "in the same {\"AA:BB:CC\": \"Vendor\"} format."),

        ("Desktop notifications (critical alerts)",
         "When Stonefish flags a new SPOOFING or GW CHANGED threat, it fires a "
         "critical desktop notification via mako — even if the app window isn't "
         "focused or the screen is locked. You'll see a red banner: "
         "\"Phantom: ARP threat — SPOOFING: <hostname> [<vendor>] — MAC ...\"\n\n"
         "Rate limit: the same (MAC, threat) pair won't re-fire within 5 minutes, "
         "so a flapping gateway (common on some ISPs) won't spam your notification "
         "stack.\n\n"
         "If mako isn't running (headless install / mako crashed) the notification "
         "fails silently — alerts still write to Crow's Nest's event log, so you can "
         "still see them by opening Crow's Nest."),

        ("Block MAC (right-click action)",
         "Right-click any row to Block or Unblock the device at the MAC level. "
         "The menu label includes hostname + vendor so you can identify what you're "
         "about to cut off — \"Block Kids-Tablet [Apple]\" is less scary than "
         "a bare MAC address.\n\n"
         "What blocking does: adds the MAC to an nftables set "
         "(inet phantom_blocklist) with a forward-hook drop rule. The device can't "
         "reach anything through GhostPort — not even DHCP. The block is kernel-"
         "enforced, not DNS-level.\n\n"
         "Persistence: blocked MACs are stored in /etc/phantom/mac-blocklist.json "
         "and restored on boot by phantom-mac-blocklist.service. Survives reboots, "
         "updates, service restarts.\n\n"
         "Block MAC vs Family Shield: Block MAC is TOTAL cutoff. Family Shield is "
         "CATEGORY-selective (e.g., block social media but let homework sites "
         "through). Pick the right tool — cutoff is the nuclear option.\n\n"
         "A determined attacker can spoof or rotate their MAC to bypass the block. "
         "Block MAC is decisive but not foolproof; pair with Bulkhead rules for "
         "deeper defense."),

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
        # Columns: IP, Hostname, MAC, Vendor, Interface, State, Threat, Action
        self.store = Gtk.ListStore(str, str, str, str, str, str, str, str)
        self.treeview = Gtk.TreeView(model=self.store)
        self.treeview.set_headers_visible(True)

        columns = [
            ("IP Address", 0, 135),
            ("Hostname",   1, 160),
            ("MAC Address", 2, 150),
            ("Vendor",     3, 160),
            ("Interface",  4, 80),
            ("State",      5, 85),
            ("Threat",     6, 110),
            ("Action",     7, 220),
        ]

        THREAT_IDX = 6
        for title, idx, width in columns:
            renderer = Gtk.CellRendererText()
            renderer.set_property("font", "monospace 10")
            if idx == THREAT_IDX:
                # Threat column gets colored
                col = Gtk.TreeViewColumn(title, renderer, text=idx)
                col.set_cell_data_func(renderer, self._threat_color)
            elif idx == 7:
                # Action column — dim so it reads as advice, not state
                col = Gtk.TreeViewColumn(title, renderer, text=idx)
                col.set_cell_data_func(renderer, self._action_color)
            else:
                col = Gtk.TreeViewColumn(title, renderer, text=idx)
            col.set_min_width(width)
            col.set_resizable(True)
            self.treeview.append_column(col)

        # Right-click → Block/Unblock MAC (Stonefish #10)
        self.treeview.connect("button-press-event", self._on_treeview_button_press)

        scroll = self.make_scrolled(self.treeview)
        scroll.set_margin_start(8)
        scroll.set_margin_end(8)
        scroll.set_margin_bottom(4)
        root.pack_start(scroll, True, True, 0)

        # Status bar
        self.status_bar = self.make_status_bar("Initializing...")
        root.pack_start(self.status_bar, False, False, 0)

    def _current_blocklist(self):
        """Read current blocked MACs. Returns set for O(1) lookup."""
        try:
            r = subprocess.run(
                ["sudo", "-n", "gp-mac-block", "list"],
                capture_output=True, text=True, timeout=5,
            )
            data = json.loads(r.stdout or "[]")
            return {b["mac"].lower() for b in data}
        except Exception:
            return set()

    def _on_treeview_button_press(self, _widget, event):
        # Right-click (button 3) shows Block/Unblock menu.
        if event.button != 3:
            return False
        path_info = self.treeview.get_path_at_pos(int(event.x), int(event.y))
        if not path_info:
            return False
        path, _col, _cx, _cy = path_info
        self.treeview.set_cursor(path)
        it = self.store.get_iter(path)
        ip = self.store.get_value(it, 0)
        hostname = self.store.get_value(it, 1)
        mac = (self.store.get_value(it, 2) or "").lower()
        vendor = self.store.get_value(it, 3)
        if not mac:
            return False

        menu = Gtk.Menu()
        blocked = self._current_blocklist()
        label = hostname or ip
        vendor_tag = f" [{vendor}]" if vendor and vendor != "Unknown" else ""

        if mac in blocked:
            item = Gtk.MenuItem(label=f"Unblock {label}{vendor_tag}")
            item.connect("activate", lambda *_: self._confirm_unblock_mac(mac, label))
        else:
            item = Gtk.MenuItem(label=f"Block {label}{vendor_tag}")
            item.connect("activate", lambda *_: self._confirm_block_mac(mac, label, vendor))
        menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _confirm_block_mac(self, mac, label, vendor):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Block {label}?",
        )
        dialog.format_secondary_text(
            f"MAC {mac}" + (f"\nVendor: {vendor}" if vendor else "") +
            "\n\nThis device will be unable to access the network "
            "via this router until you unblock it. Rule persists across reboot."
        )
        resp = dialog.run()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            self._apply_block(mac, label)

    def _confirm_unblock_mac(self, mac, label):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Unblock {label}?",
        )
        dialog.format_secondary_text(f"MAC {mac} will regain network access.")
        resp = dialog.run()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            self._apply_unblock(mac, label)

    def _apply_block(self, mac, label):
        r = subprocess.run(
            ["sudo", "-n", "gp-mac-block", "add", mac, f"stonefish: {label}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            self.set_status(f"Blocked {label} ({mac})")
        else:
            self.set_status(f"Block failed: {(r.stderr or r.stdout).strip()[:80]}")

    def _apply_unblock(self, mac, label):
        r = subprocess.run(
            ["sudo", "-n", "gp-mac-block", "remove", mac],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            self.set_status(f"Unblocked {label} ({mac})")
        else:
            self.set_status(f"Unblock failed: {(r.stderr or r.stdout).strip()[:80]}")

    def _threat_color(self, _column, cell, model, iter_, data=None):
        threat = model.get_value(iter_, 6)
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

    def _action_color(self, _column, cell, _model, _iter, _data=None):
        cell.set_property("foreground", self.colors["dim"])
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
        alert_details = []  # For mako notification payload
        for e in entries:
            threat = e["threat"]
            if threat in ("SPOOFING", "GW CHANGED"):
                new_alerts += 1
                alert_details.append((threat, e))
            vendor = oui_lookup(e["mac"])
            hostname = hostname_for(e["ip"])
            action = _action_hint(threat)
            self.store.append([
                e["ip"], hostname, e["mac"], vendor, e["iface"],
                e["state"], threat, action,
            ])

        # Stonefish #4: fire mako critical notification for each *new* alert,
        # rate-limited per (mac, threat) pair so flapping doesn't spam.
        if alert_details:
            _fire_notifications(alert_details)

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
