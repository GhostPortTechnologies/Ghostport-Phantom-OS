#!/usr/bin/env python3
"""gp-crewmanifest — CREW MANIFEST: Connected Device Manager for Phantom OS"""
import sys, os, re, json, time, subprocess
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

# ── Constants ─────────────────────────────────────────────────────────

LEASES_CMD = ["sudo", "-n", "gp-leases"]
DEVICE_PROFILES_FILE = "/etc/phantom/device-profiles.json"
MAX_PROFILES = 100

# ── MAC OUI Vendor Lookup (common prefixes) ──────────────────────────

OUI_TABLE = {
    "00:50:56": "VMware",
    "00:0c:29": "VMware",
    "00:1a:11": "Google",
    "3c:22:fb": "Apple",
    "a4:83:e7": "Apple",
    "f0:18:98": "Apple",
    "ac:de:48": "Apple",
    "14:98:77": "Apple",
    "dc:a6:32": "Raspberry Pi",
    "d8:3a:dd": "Raspberry Pi",
    "2c:cf:67": "Raspberry Pi",
    "e4:5f:01": "Raspberry Pi",
    "b8:27:eb": "Raspberry Pi",
    "28:cd:c1": "Raspberry Pi",
    "34:39:16": "Google Pixel",
    "94:e6:f7": "Intel",
    "00:1b:21": "Intel",
    "a0:36:9f": "Intel",
    "88:e9:fe": "Intel",
    "00:26:b0": "Intel",
    "a4:c3:f0": "Intel",
    "f8:75:a4": "Dell",
    "18:66:da": "Dell",
    "34:17:eb": "Dell",
    "00:25:b3": "Hewlett-Packard",
    "00:1e:68": "Hewlett-Packard",
    "3c:d9:2b": "Hewlett-Packard",
    "30:24:a9": "Samsung",
    "bc:14:01": "Samsung",
    "34:c3:d2": "Samsung",
    "f0:5c:d5": "Samsung",
    "00:23:14": "Intel",
    "fc:f5:c4": "Samsung",
    "d0:d2:b0": "Apple",
    "a4:b1:c1": "Intel",
    "00:16:3e": "Xen VM",
    "08:00:27": "VirtualBox",
    "52:54:00": "QEMU/KVM",
    "9c:b6:d0": "Rivet Networks",
    "20:47:da": "Dell",
    "e8:84:a5": "Zyxel",
    "00:1a:2b": "Cisco",
    "00:1d:e5": "Cisco",
    "00:50:f2": "Microsoft",
    "00:15:5d": "Microsoft Hyper-V",
    "4c:32:75": "TP-Link",
    "50:c7:bf": "TP-Link",
    "ec:08:6b": "TP-Link",
    "c0:25:e9": "TP-Link",
    "44:d9:e7": "Ubiquiti",
    "24:a4:3c": "Ubiquiti",
    "f0:9f:c2": "Ubiquiti",
    "00:11:32": "Synology",
    "00:50:43": "Marvell",
    "b0:be:76": "TP-Link",
    "a8:5e:45": "ASUSTek",
    "04:d4:c4": "ASUSTek",
    "00:0e:c6": "ASIX Electronics",
    "00:e0:4c": "Realtek",
    "48:5d:60": "Realtek",
    "52:4f:4d": "Realtek",
}


def lookup_vendor(mac):
    prefix = mac[:8].lower()
    return OUI_TABLE.get(prefix, "Unknown")


def is_mac_randomized(mac):
    try:
        second_char = mac[1].lower()
        return second_char in ('2', '6', 'a', 'e')
    except (IndexError, TypeError):
        return False


def read_leases():
    clients = []
    try:
        result = subprocess.run(LEASES_CMD, capture_output=True, text=True, timeout=5)
        lease_text = result.stdout
    except Exception:
        return clients
    for line in lease_text.splitlines():
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        try:
            expiry = int(parts[0])
        except ValueError:
            continue
        mac = parts[1].lower()
        ip = parts[2]
        hostname = parts[3] if parts[3] != "*" else ""
        clients.append({
            "mac": mac,
            "ip": ip,
            "hostname": hostname,
            "expiry": expiry,
            "vendor": lookup_vendor(mac),
            "randomized": is_mac_randomized(mac),
            "online": False,
            "arp_state": "UNKNOWN",
        })
    return clients


def check_arp_status(clients):
    try:
        result = subprocess.run(
            ["ip", "-4", "neigh", "show", "dev", "wlan0"],
            capture_output=True, text=True, timeout=5
        )
    except Exception:
        return

    arp_map = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 4 and "lladdr" in parts:
            ip = parts[0]
            mac_idx = parts.index("lladdr") + 1
            if mac_idx < len(parts):
                mac = parts[mac_idx].lower()
                state = parts[-1] if parts[-1] in ("REACHABLE", "STALE", "DELAY", "PROBE", "FAILED", "INCOMPLETE", "PERMANENT") else "UNKNOWN"
                arp_map[mac] = state

    for client in clients:
        state = arp_map.get(client["mac"], "")
        if state:
            client["arp_state"] = state
            client["online"] = state in ("REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT")
        else:
            client["online"] = False
            client["arp_state"] = "ABSENT"


def sort_clients(clients):
    def ip_key(c):
        try:
            parts = c["ip"].split(".")
            return tuple(int(p) for p in parts)
        except (ValueError, AttributeError):
            return (999, 999, 999, 999)
    clients.sort(key=lambda c: (0 if c["online"] else 1, ip_key(c)))


# ── Device Activity Data ─────────────────────────────────────────────

def get_conntrack_by_ip(ip):
    """Get active connection count and bytes for a specific IP from conntrack."""
    # Module-level: can't use base class methods, timeout enforced manually
    try:
        result = subprocess.run(
            ["sudo", "conntrack", "-L", "-s", ip],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().splitlines()[:500]  # Cap at 500
        total_bytes = 0
        ports = {}
        for line in lines:
            # Parse bytes= fields
            for m in re.finditer(r'bytes=(\d+)', line):
                total_bytes += int(m.group(1))
            # Parse dport= fields
            for m in re.finditer(r'dport=(\d+)', line):
                port = m.group(1)
                ports[port] = ports.get(port, 0) + 1
        # Also count replies (dst=ip)
        result2 = subprocess.run(
            ["sudo", "conntrack", "-L", "-d", ip],
            capture_output=True, text=True, timeout=5
        )
        lines2 = result2.stdout.strip().splitlines()[:500]
        for line in lines2:
            for m in re.finditer(r'bytes=(\d+)', line):
                total_bytes += int(m.group(1))

        flow_count = len(lines) + len(lines2)
        # Top 5 ports
        top_ports = sorted(ports.items(), key=lambda x: -x[1])[:5]
        return flow_count, total_bytes, top_ports
    except Exception:
        return 0, 0, []


def get_pihole_client_queries(ip):
    """Get DNS query count for a client from Pi-hole v6 API via gp-pihole helper.

    T-0045: replaced the deprecated /admin/api.php?getQuerySources path with
    a subprocess call to gp-pihole, which owns sid lifecycle and v6 endpoint
    mapping. Helper exits 0 with the integer on stdout, or non-zero on auth /
    network failure — caller treats failure as 0 queries.
    """
    try:
        r = subprocess.run(
            ["gp-pihole", "client-queries", ip],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return int((r.stdout or "0").strip() or 0)
    except (subprocess.SubprocessError, ValueError):
        pass
    return 0


def classify_device(vendor, hostname, top_ports):
    """Auto-classify device type based on vendor, hostname, and port behavior."""
    vendor_lower = vendor.lower()
    hostname_lower = hostname.lower() if hostname else ""

    # IoT indicators
    iot_vendors = {"tp-link", "zyxel", "ubiquiti", "lifx", "tuya", "shelly", "sonoff", "wemo"}
    if any(v in vendor_lower for v in iot_vendors):
        return "IoT"

    # Printer indicators
    if "printer" in hostname_lower or ("hp" in vendor_lower and "print" in hostname_lower):
        return "Printer"
    printer_ports = {"631", "9100", "515"}
    if any(p in printer_ports for p, _ in top_ports):
        return "Printer"

    # Mobile indicators
    mobile_vendors = {"samsung", "google pixel", "oneplus", "xiaomi", "huawei"}
    if any(v in vendor_lower for v in mobile_vendors):
        if any(kw in hostname_lower for kw in ("pixel", "galaxy", "android")):
            return "Mobile"
        return "Mobile"
    # Apple: distinguish phone/tablet from Mac
    if "apple" in vendor_lower:
        if any(kw in hostname_lower for kw in ("iphone", "ipad", "ipod")):
            return "Mobile"
        return "Desktop"  # MacBook, iMac, Mac Mini, etc.

    # Desktop/laptop indicators
    desktop_vendors = {"intel", "dell", "hewlett-packard", "asustek", "msi", "lenovo", "acer"}
    if any(v in vendor_lower for v in desktop_vendors):
        return "Desktop"
    if any(v in vendor_lower for v in ("vmware", "virtualbox", "qemu", "xen", "hyper-v")):
        return "VM"

    # Raspberry Pi
    if "raspberry" in vendor_lower:
        return "SBC"

    return "Unknown"


# ── Device Profiles Persistence ──────────────────────────────────────

def load_profiles():
    try:
        with open(DEVICE_PROFILES_FILE) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_profiles(profiles):
    """Save device profiles, capped at MAX_PROFILES."""
    # Module-level: can't use base class methods, timeout enforced manually
    try:
        # Cap entries
        if len(profiles) > MAX_PROFILES:
            # Keep most recently seen
            items = sorted(profiles.items(), key=lambda x: x[1].get("last_seen", 0), reverse=True)
            profiles = dict(items[:MAX_PROFILES])
        raw = json.dumps(profiles, indent=2)
        # Cap file size at 100KB
        if len(raw) > 100 * 1024:
            items = sorted(profiles.items(), key=lambda x: x[1].get("last_seen", 0), reverse=True)
            profiles = dict(items[:50])
            raw = json.dumps(profiles, indent=2)
        subprocess.run(
            ["sudo", "tee", DEVICE_PROFILES_FILE],
            input=raw, capture_output=True, text=True, timeout=5
        )
    except Exception:
        pass


def update_profile(profiles, client):
    """Update device profile for a client."""
    mac = client["mac"]
    now = int(time.time())
    profile = profiles.get(mac, {})
    profile["first_seen"] = profile.get("first_seen", now)
    profile["last_seen"] = now
    profile["hostname"] = client.get("hostname", profile.get("hostname", ""))
    profile["ip"] = client["ip"]
    profile["vendor"] = client.get("vendor", "")
    if client["online"]:
        # Compute elapsed since last update instead of hardcoded increment
        elapsed = now - profile.get("last_update_ts", now)
        elapsed = min(elapsed, 60)  # Cap to prevent large gaps inflating the count
        profile["online_seconds"] = profile.get("online_seconds", 0) + elapsed
    profile["last_update_ts"] = now
    profiles[mac] = profile


# ── Crew Manifest App ────────────────────────────────────────────────

class CrewManifestApp(GhostPortApp):

    def __init__(self):
        super().__init__("CREW MANIFEST", "crewmanifest", (850, 600))
        self.clients = []
        self.selected_client = None
        self._profiles = load_profiles()
        self._activity_cache = {}  # ip -> (flows, bytes, top_ports, dns_queries, timestamp)
        self.build_ui()
        self.run_async(self._load_clients, self._on_clients_loaded)
        self.poll_start(10, self._poll_tick)

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header with client counts
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.get_style_context().add_class("gp-header")

        header_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_label = Gtk.Label(label="CREW MANIFEST")
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class("gp-header-title")
        header_left.pack_start(title_label, False, False, 0)

        sub_label = Gtk.Label(label="Connected Devices")
        sub_label.set_halign(Gtk.Align.START)
        sub_label.get_style_context().add_class("gp-header-subtitle")
        header_left.pack_start(sub_label, False, False, 0)

        header_box.pack_start(header_left, True, True, 0)

        count_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        count_box.set_valign(Gtk.Align.CENTER)

        self.lbl_online_count = self.make_label("0 online", "gp-success")
        count_box.pack_start(self.lbl_online_count, False, False, 0)

        self.lbl_offline_count = self.make_label("0 offline", "gp-dim")
        count_box.pack_start(self.lbl_offline_count, False, False, 0)

        self.lbl_total_count = self.make_label("0 total", "gp-accent")
        count_box.pack_start(self.lbl_total_count, False, False, 0)

        header_box.pack_end(count_box, False, False, 8)
        root.pack_start(header_box, False, False, 0)

        # Main content: paned with client list + detail
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(480)

        # Left: client list
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)

        scrolled_list = self.make_scrolled(self.listbox)
        paned.pack1(scrolled_list, True, False)

        # Right: detail panel with notebook (Info | Activity)
        self._detail_notebook = Gtk.Notebook()

        # Tab 1: Info
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.detail_box.get_style_context().add_class("gp-sidebar")

        self.lbl_detail_placeholder = self.make_label(
            "Select a device to view details", "gp-dim"
        )
        self.lbl_detail_placeholder.set_halign(Gtk.Align.CENTER)
        self.lbl_detail_placeholder.set_valign(Gtk.Align.CENTER)
        self.detail_box.pack_start(self.lbl_detail_placeholder, True, True, 20)

        scrolled_detail = self.make_scrolled(self.detail_box)
        scrolled_detail.set_min_content_width(300)
        self._detail_notebook.append_page(scrolled_detail, Gtk.Label(label="Info"))

        # Tab 2: Activity
        self._activity_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._activity_box.get_style_context().add_class("gp-sidebar")

        self._activity_placeholder = self.make_label(
            "Select a device to view activity", "gp-dim"
        )
        self._activity_placeholder.set_halign(Gtk.Align.CENTER)
        self._activity_placeholder.set_valign(Gtk.Align.CENTER)
        self._activity_box.pack_start(self._activity_placeholder, True, True, 20)

        scrolled_activity = self.make_scrolled(self._activity_box)
        scrolled_activity.set_min_content_width(300)
        self._detail_notebook.append_page(scrolled_activity, Gtk.Label(label="Activity"))

        paned.pack2(self._detail_notebook, False, True)

        root.pack_start(paned, True, True, 0)

        # Status bar
        root.pack_start(self.make_status_bar("Loading..."), False, False, 0)

    # ── Data Loading ─────────────────────────────────────────────────

    def _load_clients(self):
        clients = read_leases()
        check_arp_status(clients)
        sort_clients(clients)

        # Update profiles
        for c in clients:
            update_profile(self._profiles, c)
        save_profiles(self._profiles)

        return clients

    def _on_clients_loaded(self, result):
        if isinstance(result, Exception):
            self.set_status(f"Error: {result}")
            return
        self.clients = result
        self._update_counts()
        self._rebuild_list()

        self.set_status(
            f"Monitoring {len(self.clients)} devices | "
            f"Polling every 10s | Last update: {time.strftime('%H:%M:%S')}"
        )

    def _update_counts(self):
        online = sum(1 for c in self.clients if c["online"])
        offline = len(self.clients) - online
        self.lbl_online_count.set_text(f"{online} online")
        self.lbl_offline_count.set_text(f"{offline} offline")
        self.lbl_total_count.set_text(f"{len(self.clients)} total")

    # ── Polling ──────────────────────────────────────────────────────

    def _poll_tick(self):
        self.run_async(self._load_clients, self._on_clients_loaded)
        return True

    # ── List Rebuild ─────────────────────────────────────────────────

    def _rebuild_list(self):
        selected_mac = self.selected_client["mac"] if self.selected_client else None

        for child in self.listbox.get_children():
            self.listbox.remove(child)

        select_row = None
        for i, client in enumerate(self.clients):
            row = Gtk.ListBoxRow()
            row.client_index = i
            card = self._make_client_card(client)
            row.add(card)
            self.listbox.add(row)
            if selected_mac and client["mac"] == selected_mac:
                select_row = row

        self.listbox.show_all()

        if select_row:
            self.listbox.select_row(select_row)

    def _make_client_card(self, client):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if client["online"]:
            card.get_style_context().add_class("gp-card-info")
        else:
            card.get_style_context().add_class("gp-card")

        # Row 1: status dot + hostname + device class + vendor
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        dot_text = "\u25cf"
        dot_css = "gp-success" if client["online"] else "gp-danger"
        lbl_dot = self.make_label(dot_text, dot_css)
        row1.pack_start(lbl_dot, False, False, 0)

        hostname = client["hostname"] if client["hostname"] else "(unnamed)"
        lbl_hostname = self.make_label(hostname, "gp-accent")
        row1.pack_start(lbl_hostname, True, True, 0)

        # Device class badge
        profile = self._profiles.get(client["mac"], {})
        cached = self._activity_cache.get(client["ip"])
        top_ports = cached[2] if cached else []
        dev_class = classify_device(client["vendor"], client["hostname"], top_ports)
        if dev_class != "Unknown":
            lbl_class = self.make_label(dev_class.upper(), "gp-dim")
            row1.pack_end(lbl_class, False, False, 0)

        if client["randomized"]:
            lbl_rand = self.make_label("RANDOMIZED", "gp-warning")
            row1.pack_end(lbl_rand, False, False, 0)

        card.pack_start(row1, False, False, 0)

        # Row 2: IP + MAC + vendor
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        lbl_ip = self.make_label(client["ip"], "gp-text")
        row2.pack_start(lbl_ip, False, False, 0)

        lbl_mac = self.make_label(client["mac"], "gp-dim")
        row2.pack_start(lbl_mac, False, False, 0)

        lbl_vendor = self.make_label(client["vendor"], "gp-dim")
        row2.pack_end(lbl_vendor, False, False, 0)

        card.pack_start(row2, False, False, 0)

        return card

    # ── Detail Panel ─────────────────────────────────────────────────

    def _on_row_selected(self, listbox, row):
        if row is None:
            self.selected_client = None
            return

        idx = row.client_index
        if idx >= len(self.clients):
            return

        self.selected_client = self.clients[idx]
        self._show_detail(self.selected_client)
        # Load activity data async
        self._show_activity_loading()
        client = self.selected_client
        self.run_async(
            lambda c=client: self._load_activity(c),
            self._on_activity_loaded
        )

    def _show_detail(self, client):
        for child in self.detail_box.get_children():
            self.detail_box.remove(child)

        hostname = client["hostname"] if client["hostname"] else "(unnamed)"
        lbl_title = self.make_label(hostname, "gp-accent")
        lbl_title.override_font(Pango.FontDescription("monospace bold 14"))
        self.detail_box.pack_start(lbl_title, False, False, 4)

        status_text = "ONLINE" if client["online"] else "OFFLINE"
        status_css = "gp-success" if client["online"] else "gp-danger"
        lbl_status = self.make_label(f"\u25cf  {status_text}", status_css)
        lbl_status.override_font(Pango.FontDescription("monospace bold 12"))
        self.detail_box.pack_start(lbl_status, False, False, 4)

        sep = Gtk.Separator()
        self.detail_box.pack_start(sep, False, False, 4)

        # Detail fields
        expiry = client["expiry"]
        if expiry == 0:
            expiry_text = "Static"
        else:
            remaining = expiry - int(time.time())
            if remaining > 0:
                hours = remaining // 3600
                mins = (remaining % 3600) // 60
                expiry_text = f"{hours}h {mins}m remaining"
            else:
                expiry_text = "Expired"

        # Device class
        cached = self._activity_cache.get(client["ip"])
        top_ports = cached[2] if cached else []
        dev_class = classify_device(client["vendor"], client["hostname"], top_ports)

        # Profile data
        profile = self._profiles.get(client["mac"], {})
        first_seen = profile.get("first_seen", 0)
        last_seen = profile.get("last_seen", 0)
        online_secs = profile.get("online_seconds", 0)

        first_seen_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(first_seen)) if first_seen else "Unknown"
        last_seen_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(last_seen)) if last_seen else "Unknown"
        online_hours = online_secs // 3600
        online_mins = (online_secs % 3600) // 60
        online_text = f"{online_hours}h {online_mins}m" if online_hours > 0 else f"{online_mins}m"

        fields = [
            ("IP Address", client["ip"]),
            ("MAC Address", client["mac"]),
            ("Vendor", client["vendor"]),
            ("Device Type", dev_class),
            ("ARP State", client["arp_state"]),
            ("Lease Expiry", expiry_text),
            ("First Seen", first_seen_text),
            ("Last Seen", last_seen_text),
            ("Total Online", online_text),
        ]

        for label_text, value_text in fields:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl_key = self.make_label(f"{label_text}:", "gp-dim")
            lbl_key.set_size_request(100, -1)
            row.pack_start(lbl_key, False, False, 0)

            val_css = "gp-text"
            if label_text == "ARP State":
                val_css = "gp-success" if value_text in ("REACHABLE", "PERMANENT") else (
                    "gp-warning" if value_text in ("STALE", "DELAY", "PROBE") else "gp-danger"
                )
            elif label_text == "Device Type" and value_text != "Unknown":
                val_css = "gp-accent"

            lbl_val = self.make_label(value_text, val_css)
            row.pack_start(lbl_val, False, False, 0)
            self.detail_box.pack_start(row, False, False, 2)

        # MAC randomization warning
        if client["randomized"]:
            sep2 = Gtk.Separator()
            self.detail_box.pack_start(sep2, False, False, 4)

            warn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            warn_box.get_style_context().add_class("gp-card-warning")

            lbl_warn_title = self.make_label("MAC RANDOMIZATION", "gp-warning")
            warn_box.pack_start(lbl_warn_title, False, False, 0)

            lbl_warn_desc = self.make_label(
                "This device is using a locally-administered MAC address. "
                "It may appear as a different device after reconnecting.",
                "gp-text"
            )
            lbl_warn_desc.set_line_wrap(True)
            lbl_warn_desc.set_max_width_chars(28)
            warn_box.pack_start(lbl_warn_desc, False, False, 0)

            self.detail_box.pack_start(warn_box, False, False, 2)

        self.detail_box.show_all()

    # ── Activity Tab ─────────────────────────────────────────────────

    def _show_activity_loading(self):
        for child in self._activity_box.get_children():
            self._activity_box.remove(child)
        lbl = self.make_label("Loading activity data...", "gp-dim")
        lbl.set_halign(Gtk.Align.CENTER)
        lbl.set_margin_top(20)
        self._activity_box.pack_start(lbl, False, False, 0)
        self._activity_box.show_all()

    def _load_activity(self, client):
        """Background: load conntrack + DNS data for selected client."""
        ip = client["ip"]
        # Check cache (valid for 30s)
        cached = self._activity_cache.get(ip)
        if cached and time.time() - cached[4] < 30:
            return ip, cached[0], cached[1], cached[2], cached[3]

        flows, total_bytes, top_ports = get_conntrack_by_ip(ip)
        dns_queries = get_pihole_client_queries(ip)

        self._activity_cache[ip] = (flows, total_bytes, top_ports, dns_queries, time.time())
        # Cap cache size
        if len(self._activity_cache) > 50:
            oldest = sorted(self._activity_cache.items(), key=lambda x: x[1][4])
            for k, _ in oldest[:10]:
                del self._activity_cache[k]

        return ip, flows, total_bytes, top_ports, dns_queries

    def _on_activity_loaded(self, result):
        if isinstance(result, Exception):
            self._show_activity_error(str(result))
            return

        ip, flows, total_bytes, top_ports, dns_queries = result

        # Make sure we're still looking at the same client
        if not self.selected_client or self.selected_client["ip"] != ip:
            return

        for child in self._activity_box.get_children():
            self._activity_box.remove(child)

        # Activity header
        lbl_title = self.make_label("NETWORK ACTIVITY", "gp-accent")
        lbl_title.override_font(Pango.FontDescription("monospace bold 12"))
        self._activity_box.pack_start(lbl_title, False, False, 4)

        sep = Gtk.Separator()
        self._activity_box.pack_start(sep, False, False, 4)

        # Stats grid
        stats = [
            ("Active Flows", str(flows), "gp-accent"),
            ("Bandwidth", self._fmt_bytes(total_bytes), "gp-accent"),
            ("DNS Queries", str(dns_queries), "gp-accent"),
        ]

        for label_text, value_text, val_css in stats:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl_key = self.make_label(f"{label_text}:", "gp-dim")
            lbl_key.set_size_request(100, -1)
            row.pack_start(lbl_key, False, False, 0)
            lbl_val = self.make_label(value_text, val_css)
            row.pack_start(lbl_val, False, False, 0)
            self._activity_box.pack_start(row, False, False, 2)

        # Top ports
        if top_ports:
            sep2 = Gtk.Separator()
            self._activity_box.pack_start(sep2, False, False, 8)

            lbl_ports_title = self.make_label("TOP PORTS", "gp-accent")
            self._activity_box.pack_start(lbl_ports_title, False, False, 4)

            port_services = {
                "22": "SSH", "53": "DNS", "80": "HTTP", "443": "HTTPS",
                "993": "IMAPS", "587": "SMTP", "8080": "HTTP-ALT",
                "3389": "RDP", "5353": "mDNS", "8443": "HTTPS-ALT",
            }

            for port, count in top_ports:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                service = port_services.get(port, "")
                port_text = f":{port}" + (f" ({service})" if service else "")
                lbl_port = self.make_label(port_text, "gp-text")
                lbl_port.set_size_request(140, -1)
                row.pack_start(lbl_port, False, False, 0)
                lbl_count = self.make_label(f"{count} flows", "gp-dim")
                row.pack_start(lbl_count, False, False, 0)
                self._activity_box.pack_start(row, False, False, 1)

        # No activity message
        if flows == 0 and dns_queries == 0:
            lbl_none = self.make_label("No active connections", "gp-dim")
            lbl_none.set_halign(Gtk.Align.CENTER)
            lbl_none.set_margin_top(10)
            self._activity_box.pack_start(lbl_none, False, False, 0)

        self._activity_box.show_all()

    def _show_activity_error(self, msg):
        for child in self._activity_box.get_children():
            self._activity_box.remove(child)
        lbl = self.make_label(f"Error: {msg}", "gp-danger")
        lbl.set_halign(Gtk.Align.CENTER)
        lbl.set_margin_top(20)
        self._activity_box.pack_start(lbl, False, False, 0)
        self._activity_box.show_all()

    @staticmethod
    def _fmt_bytes(b):
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.1f} GB"

    def _on_destroy(self, *args):
        save_profiles(self._profiles)
        super()._on_destroy(*args)

    def on_theme_changed(self):
        self._rebuild_list()


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = CrewManifestApp()
    app.run()
