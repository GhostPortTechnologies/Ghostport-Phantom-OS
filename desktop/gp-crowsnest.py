#!/usr/bin/env python3
"""gp-crowsnest — Crow's Nest: Intrusion Detection Dashboard for Phantom OS"""
import sys, os, re, json, time, math, ipaddress, csv
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo

def _hex_to_rgb(hex_str):
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)

# ── Constants ─────────────────────────────────────────────────────────

IDS_EVENTS_FILE = "/etc/phantom/ids-events.json"
IDS_TRENDS_FILE = "/etc/phantom/ids-trends.json"
MAX_ENTRIES = 200
TRENDS_MAX_HOURS = 24
TRENDS_MAX_SIZE = 50 * 1024  # 50KB cap

GEOIP_DB = "/usr/share/GeoIP/GeoLite2-Country-Blocks-IPv4.csv"
GEOIP_LOCATIONS = "/usr/share/GeoIP/GeoLite2-Country-Locations-en.csv"

# ── Threat Classification ─────────────────────────────────────────────

def classify_drop(src, dst, proto, spt, dpt, in_iface):
    """Return (description, severity) for a firewall drop event."""
    dpt_int = int(dpt) if dpt and dpt.isdigit() else 0
    spt_int = int(spt) if spt and spt.isdigit() else 0
    proto_upper = (proto or "").upper()

    if proto_upper == "2" or proto_upper == "IGMP":
        if dst.startswith("224.0.0."):
            return "IGMP multicast query from router", "info"
        return "IGMP multicast group management", "info"
    if dpt_int == 5353 or dst == "224.0.0.251":
        return "mDNS service discovery", "info"
    if dpt_int == 1900 or dst == "239.255.255.250":
        return "UPnP/SSDP discovery", "info"
    if dpt_int in (137, 138) or spt_int in (137, 138):
        return "NetBIOS name service", "warning"
    if dpt_int == 57621 or spt_int == 57621:
        return "Spotify Connect peer discovery", "info"
    if dpt_int == 56700 or spt_int == 56700:
        return "LIFX smart bulb discovery (unauthenticated IoT)", "warning"
    if dpt_int == 9999:
        return "Service discovery probe (potential C2)", "danger"
    if dpt_int == 67 or dpt_int == 68:
        return "DHCP request/response", "info"
    if dpt_int == 53:
        return "DNS query attempt (blocked by firewall)", "warning"
    if dpt_int == 853:
        return "DNS-over-TLS attempt (blocked)", "warning"
    if dpt_int == 22:
        return "SSH connection attempt", "danger"
    if dpt_int == 23:
        return "Telnet connection attempt (potential scan)", "danger"
    if dpt_int == 3389:
        return "RDP connection attempt", "danger"
    if dpt_int in (445, 139):
        return "SMB/CIFS connection attempt", "danger"
    if dpt_int in (80, 443, 8080, 8443):
        return f"HTTP/HTTPS probe on port {dpt_int}", "warning"
    if dst.startswith("224.") or dst.startswith("239."):
        return "Multicast traffic", "info"
    if dst.endswith(".255") or dst == "255.255.255.255":
        return "Broadcast packet", "info"
    if proto_upper in ("ICMP", "1"):
        return "ICMP ping/probe", "info"
    if dpt_int > 1024 and in_iface == "eth0":
        return f"Unsolicited inbound on port {dpt_int} (WAN)", "warning"
    port_info = f" on port {dpt_int}" if dpt_int else ""
    return f"Unsolicited packet dropped{port_info}", "info"


# ── Drop Line Parser ──────────────────────────────────────────────────

DROP_RE = re.compile(
    r'^\[?\s*[\d.]+\]?\s*\[?GhostPort-DROP\]?\s*'
    r'IN=(\S*)\s+OUT=(\S*)\s+'
    r'.*?SRC=(\S+)\s+DST=(\S+)\s+'
    r'.*?PROTO=(\S+)'
    r'(?:.*?SPT=(\d+))?'
    r'(?:.*?DPT=(\d+))?'
)

TIMESTAMP_RE = re.compile(r'^\[\s*([\d.]+)\]')


def parse_drop_line(line):
    """Parse a dmesg GhostPort-DROP line into a dict, or None."""
    m = DROP_RE.search(line)
    if not m:
        return None

    in_iface = m.group(1) or ""
    out_iface = m.group(2) or ""
    src = m.group(3) or ""
    dst = m.group(4) or ""
    proto = m.group(5) or ""
    spt = m.group(6) or ""
    dpt = m.group(7) or ""

    ts_match = TIMESTAMP_RE.search(line)
    kernel_ts = float(ts_match.group(1)) if ts_match else 0.0

    description, severity = classify_drop(src, dst, proto, spt, dpt, in_iface)

    return {
        "ts": kernel_ts,
        "src": src,
        "dst": dst,
        "proto": proto,
        "spt": spt,
        "dpt": dpt,
        "in": in_iface,
        "out": out_iface,
        "desc": description,
        "severity": severity,
    }


def fetch_drops():
    """Read dmesg for GhostPort-DROP lines."""
    # Module-level: can't use base class methods, timeout enforced manually
    import subprocess
    try:
        result = subprocess.run(
            ["bash", "-c", "(dmesg 2>/dev/null || sudo dmesg 2>/dev/null) | grep 'GhostPort-DROP' | tail -500"],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        return []

    events = []
    for line in result.stdout.splitlines():
        if "GhostPort-DROP" not in line:
            continue
        parsed = parse_drop_line(line)
        if parsed:
            events.append(parsed)
    return events


def load_ids_events():
    """Load persisted IDS events from JSON file."""
    try:
        with open(IDS_EVENTS_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
    except Exception:
        pass
    return []


def format_timestamp(ts_value):
    """Convert a timestamp to human-readable time."""
    if ts_value is None:
        return "--:--:--"
    if isinstance(ts_value, str):
        try:
            return ts_value.split(" ", 1)[1] if " " in ts_value else ts_value
        except Exception:
            return ts_value
    if not isinstance(ts_value, (int, float)):
        return "--:--:--"
    if ts_value <= 0:
        return "--:--:--"
    if ts_value > 1_000_000_000:
        return time.strftime("%H:%M:%S", time.localtime(ts_value))
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
    except Exception:
        return f"T+{ts_value:.0f}s"
    real_time = time.time() - (uptime - ts_value)
    return time.strftime("%H:%M:%S", time.localtime(real_time))


def format_proto(proto_str):
    """Convert protocol number/name to display string."""
    proto_map = {
        "6": "TCP", "17": "UDP", "1": "ICMP", "2": "IGMP",
        "TCP": "TCP", "UDP": "UDP", "ICMP": "ICMP", "IGMP": "IGMP",
    }
    return proto_map.get(proto_str.upper(), proto_str.upper()) if proto_str else "???"


# ── GeoIP Lookup ─────────────────────────────────────────────────────

def _load_geoip():
    """Load MaxMind GeoLite2 CSV for IP-to-country lookup.
    Returns (sorted_networks, geoname_to_country) or (None, None) if files missing."""
    if not os.path.exists(GEOIP_DB) or not os.path.exists(GEOIP_LOCATIONS):
        return None, None
    try:
        # Load locations: geoname_id -> country_iso_code
        geoname_map = {}
        with open(GEOIP_LOCATIONS, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                gid = row.get("geoname_id", "")
                code = row.get("country_iso_code", "")
                if gid and code:
                    geoname_map[gid] = code

        # Load blocks: network CIDR -> geoname_id
        # Store as sorted list of (network_int, broadcast_int, country_code)
        networks = []
        with open(GEOIP_DB, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cidr = row.get("network", "")
                gid = row.get("geoname_id", "") or row.get("registered_country_geoname_id", "")
                if not cidr or not gid:
                    continue
                code = geoname_map.get(gid, "")
                if not code:
                    continue
                try:
                    net = ipaddress.IPv4Network(cidr, strict=False)
                    networks.append((int(net.network_address), int(net.broadcast_address), code))
                except (ValueError, ipaddress.AddressValueError):
                    continue
        networks.sort(key=lambda x: x[0])
        return networks, geoname_map
    except Exception:
        return None, None


def _lookup_country(geoip_networks, ip_str):
    """Binary search for IP in sorted CIDR list. Returns country code or '??'."""
    if geoip_networks is None:
        return "??"
    try:
        ip_int = int(ipaddress.IPv4Address(ip_str))
    except (ValueError, ipaddress.AddressValueError):
        return "??"
    # Binary search: find rightmost network where network_start <= ip_int
    lo, hi = 0, len(geoip_networks) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if geoip_networks[mid][0] <= ip_int:
            lo = mid + 1
        else:
            hi = mid - 1
    # hi is the index of the last network whose start <= ip_int
    if hi >= 0 and geoip_networks[hi][0] <= ip_int <= geoip_networks[hi][1]:
        return geoip_networks[hi][2]
    return "??"


# Module-level GeoIP data (loaded once at import time)
_GEOIP_NETWORKS, _GEOIP_LOCATIONS = _load_geoip()


# ── Attack Pattern Detection ─────────────────────────────────────────

def detect_attack_patterns(drops):
    """Detect attack patterns from drop events. Returns list of pattern dicts."""
    patterns = []
    now = time.time()

    # Convert all timestamps to epoch for analysis
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        boot_epoch = now - uptime
    except Exception:
        boot_epoch = 0

    def to_epoch(ts):
        if isinstance(ts, str):
            try:
                return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                return 0
        if isinstance(ts, (int, float)):
            if ts > 1_000_000_000:
                return ts
            if boot_epoch > 0:
                return boot_epoch + ts
        return 0

    # Only analyze last 60 minutes
    recent = []
    for d in drops:
        epoch = to_epoch(d.get("ts", 0))
        if epoch > now - 3600:
            recent.append({**d, "_epoch": epoch})

    if not recent:
        return patterns

    # Port scan: >10 distinct dst ports from one source in 60s windows
    src_ports = {}  # src -> set of (dpt, epoch)
    for d in recent:
        if len(src_ports) > 1000:
            break
        src = d.get("src", "")
        dpt = d.get("dpt", "")
        if src and dpt:
            src_ports.setdefault(src, []).append((dpt, d["_epoch"]))

    for src, port_times in src_ports.items():
        if len(port_times) < 10:
            continue
        # Check any 60s window
        port_times.sort(key=lambda x: x[1])
        for i in range(len(port_times)):
            window_ports = set()
            for j in range(i, min(i + 50, len(port_times))):
                if port_times[j][1] - port_times[i][1] <= 60:
                    window_ports.add(port_times[j][0])
            if len(window_ports) >= 10:
                country = _lookup_country(_GEOIP_NETWORKS, src)
                src_lbl = f"{src} ({country})" if country != "??" else src
                patterns.append({
                    "type": "PORT SCAN",
                    "severity": "danger",
                    "desc": f"{src_lbl} probed {len(window_ports)} ports in 60s",
                    "source": src,
                })
                break

    # Brute force: >5 hits on SSH/RDP from same source
    for src, port_times in src_ports.items():
        ssh_hits = sum(1 for p, _ in port_times if p in ("22", "3389"))
        if ssh_hits >= 5:
            country = _lookup_country(_GEOIP_NETWORKS, src)
            src_lbl = f"{src} ({country})" if country != "??" else src
            patterns.append({
                "type": "BRUTE FORCE",
                "severity": "danger",
                "desc": f"{src_lbl}: {ssh_hits} SSH/RDP attempts",
                "source": src,
            })

    # SYN flood: >50 drops from one source in 60s (any port)
    for src, port_times in src_ports.items():
        port_times.sort(key=lambda x: x[1])
        for i in range(len(port_times)):
            count = 0
            for j in range(i, min(i + 100, len(port_times))):
                if port_times[j][1] - port_times[i][1] <= 60:
                    count += 1
            if count >= 50:
                country = _lookup_country(_GEOIP_NETWORKS, src)
                src_lbl = f"{src} ({country})" if country != "??" else src
                patterns.append({
                    "type": "SYN FLOOD",
                    "severity": "danger",
                    "desc": f"{src_lbl}: {count} drops in 60s window",
                    "source": src,
                })
                break

    # Dedup patterns by (type, source)
    seen = set()
    unique = []
    for p in patterns:
        key = (p["type"], p.get("source", ""))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique[:20]  # Cap


# ── Trends Data ──────────────────────────────────────────────────────

def load_trends():
    """Load trends data from JSON."""
    try:
        with open(IDS_TRENDS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"hourly": {}, "top_ports": {}, "top_sources": {}}


def save_trends(data):
    """Save trends data, capped at TRENDS_MAX_SIZE."""
    # Module-level: can't use base class methods, timeout enforced manually
    import subprocess
    try:
        raw = json.dumps(data)
        if len(raw) > TRENDS_MAX_SIZE:
            # Prune oldest hourly entries
            hourly = data.get("hourly", {})
            keys = sorted(hourly.keys())
            while len(raw) > TRENDS_MAX_SIZE and keys:
                del hourly[keys.pop(0)]
                raw = json.dumps(data)
        subprocess.run(
            ["sudo", "tee", IDS_TRENDS_FILE],
            input=raw, capture_output=True, text=True, timeout=5
        )
    except Exception:
        pass


def update_trends(drops):
    """Update rolling trends from current drops."""
    trends = load_trends()
    now = time.time()
    hour_key = time.strftime("%Y-%m-%d-%H", time.localtime(now))

    # Count drops by severity for current hour
    danger_count = sum(1 for d in drops if d.get("severity") == "danger")
    warning_count = sum(1 for d in drops if d.get("severity") == "warning")
    info_count = sum(1 for d in drops if d.get("severity") == "info")

    hourly = trends.setdefault("hourly", {})
    hourly[hour_key] = {
        "total": len(drops),
        "danger": danger_count,
        "warning": warning_count,
        "info": info_count,
    }

    # Keep only last 24 hours
    keys = sorted(hourly.keys())
    while len(keys) > TRENDS_MAX_HOURS:
        del hourly[keys.pop(0)]

    # Top ports (all time within window)
    port_counts = {}
    src_counts = {}
    for d in drops:
        dpt = d.get("dpt", "")
        src = d.get("src", "")
        if dpt:
            port_counts[dpt] = port_counts.get(dpt, 0) + 1
        if src:
            src_counts[src] = src_counts.get(src, 0) + 1

    # Cap at 100 entries
    if len(port_counts) > 100:
        port_counts = dict(sorted(port_counts.items(), key=lambda x: -x[1])[:100])
    if len(src_counts) > 100:
        src_counts = dict(sorted(src_counts.items(), key=lambda x: -x[1])[:100])

    trends["top_ports"] = port_counts
    trends["top_sources"] = src_counts

    save_trends(trends)
    return trends


# ── Crow's Nest App ──────────────────────────────────────────────────

class CrowsNestApp(GhostPortApp):

    # Per-region contextual help shown by the ? Help button.
    # Added 2026-04-21 — extracted dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Crow's Nest?",
         "Crow's Nest is the Intrusion Detection dashboard — the lookout up top "
         "watching for anything suspicious hitting your firewall.\n\n"
         "Every time the firewall blocks a packet, Crow's Nest records what was "
         "hit, where it came from, and what it looked like. Patterns across those "
         "events become attack signatures (port scans, brute-force attempts, "
         "malware callbacks). The dashboard gives you a visual summary so you "
         "don't have to read raw kernel logs."),

        ("The top stats bar",
         "Four big numbers across the top:\n\n"
         "• TOTAL DROPS — every packet your firewall has refused since the last "
         "reboot. Usually in the hundreds to thousands per day on the public "
         "internet. Not an alarm; internet noise is constant.\n\n"
         "• CRITICAL — events flagged as high severity (e.g. many attempts from "
         "one IP in a short window). This is the one to pay attention to.\n\n"
         "• THREAT LEVEL — color-coded summary: NOMINAL (green) = routine, "
         "ELEVATED (amber) = uptick, HIGH (red) = active probing worth looking at.\n\n"
         "• PATTERNS — how many distinct attack patterns were recognized. Zero = "
         "quiet day; higher numbers = the system is seeing multiple techniques."),

        ("Alerts tab",
         "The big list under the Alerts tab shows one card per drop event, newest first.\n\n"
         "Each card tells you: source IP, destination port, protocol, and a plain-English "
         "read (\"SSH scan from 45.x.x.x\"). Color coding: red = critical, amber = notable, "
         "dim = routine noise.\n\n"
         "Above the list, a compact \"patterns\" bar shows the top techniques the system "
         "is currently seeing (e.g. \"Port scan: 3 IPs\"). Click a card to see more detail.\n\n"
         "If the list says \"No firewall drops detected\" — good. That means your network "
         "is quiet, or Crow's Nest just started and hasn't loaded yet."),

        ("Trends tab",
         "The Trends tab is the analyst's view — charts and leaderboards instead of a live log.\n\n"
         "• Top chart — hourly drop rate for the last 24 hours. Look for spikes; a sudden "
         "jump around 3am is worth investigating.\n\n"
         "• TOP ATTACKED PORTS — which ports attackers keep knocking on. 22 (SSH), 23 "
         "(Telnet), 3389 (RDP) are the usual suspects.\n\n"
         "• TOP SOURCE IPs — which addresses are hitting you the hardest. One IP with a "
         "huge count = a scanner or botnet node focused on you."),

        ("Bottom buttons",
         "Clear All — wipes the current event buffer. Useful after investigating a spike "
         "so you can see fresh events without noise.\n\n"
         "Pause — freezes the live updates. The dashboard stops auto-refreshing so you "
         "can study a snapshot without it scrolling away. Click again to resume.\n\n"
         "Refresh — force-reload from disk right now (auto-refresh is every 5s anyway).\n\n"
         "Export Log — save the current event buffer to a timestamped file in your "
         "home folder so you can hand it to someone technical or keep a record."),

        ("What's normal, what isn't",
         "NORMAL: hundreds of DROPs per day from random internet IPs hitting common "
         "ports. This is the background radiation of the internet. No action needed.\n\n"
         "WORTH A LOOK: one source IP with thousands of hits in an hour (scanner), or "
         "a THREAT LEVEL jump to HIGH, or a pattern like \"Brute force: SSH\" showing up.\n\n"
         "URGENT: events originating from INSIDE your LAN (192.168.50.x source). That "
         "means a device on your network is behaving badly. Check Crew Manifest to "
         "identify it.\n\n"
         "Remember: a DROP means the firewall REFUSED the packet. High numbers of drops "
         "mean the firewall is doing its job, not that you've been compromised."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    def __init__(self):
        super().__init__("Crow's Nest", "crowsnest", (950, 700))
        self.drops = []
        self.paused = False
        self._last_count = 0
        self._trends = {"hourly": {}, "top_ports": {}, "top_sources": {}}
        self._patterns = []
        self.build_ui()
        self.run_async(self._load_data, self._on_data_loaded)
        self.poll_start(5, self._poll_tick)

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header
        root.pack_start(self.make_header("CROW'S NEST", "Intrusion Detection System"), False, False, 0)

        # Stats bar
        stats_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        stats_bar.get_style_context().add_class("gp-card")
        stats_bar.set_margin_start(8)
        stats_bar.set_margin_end(8)
        stats_bar.set_margin_top(8)

        # Total drops
        drop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        drop_box.set_margin_start(12)
        drop_box.set_margin_end(24)
        self.lbl_total = self.make_label("0", "gp-big-number")
        drop_box.pack_start(self.lbl_total, False, False, 0)
        drop_box.pack_start(self.make_label("TOTAL DROPS", "gp-dim"), False, False, 0)
        stats_bar.pack_start(drop_box, False, False, 0)

        sep1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep1.set_margin_top(6)
        sep1.set_margin_bottom(6)
        stats_bar.pack_start(sep1, False, False, 0)

        # Alerts
        alert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        alert_box.set_margin_start(24)
        alert_box.set_margin_end(24)
        self.lbl_alerts = self.make_label("0", "gp-big-number")
        alert_box.pack_start(self.lbl_alerts, False, False, 0)
        alert_box.pack_start(self.make_label("CRITICAL", "gp-dim"), False, False, 0)
        stats_bar.pack_start(alert_box, False, False, 0)

        sep2 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep2.set_margin_top(6)
        sep2.set_margin_bottom(6)
        stats_bar.pack_start(sep2, False, False, 0)

        # Threat level
        threat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        threat_box.set_margin_start(24)
        threat_box.set_margin_end(24)
        self.lbl_threat = self.make_label("NOMINAL", "gp-success")
        self.lbl_threat.override_font(Pango.FontDescription("monospace bold 28"))
        threat_box.pack_start(self.lbl_threat, False, False, 0)
        threat_box.pack_start(self.make_label("THREAT LEVEL", "gp-dim"), False, False, 0)
        stats_bar.pack_start(threat_box, False, False, 0)

        sep3 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep3.set_margin_top(6)
        sep3.set_margin_bottom(6)
        stats_bar.pack_start(sep3, False, False, 0)

        # Patterns count
        pattern_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pattern_box.set_margin_start(24)
        pattern_box.set_margin_end(12)
        self.lbl_patterns = self.make_label("0", "gp-big-number")
        pattern_box.pack_start(self.lbl_patterns, False, False, 0)
        pattern_box.pack_start(self.make_label("PATTERNS", "gp-dim"), False, False, 0)
        stats_bar.pack_start(pattern_box, False, False, 0)

        root.pack_start(stats_bar, False, False, 0)

        # ── Notebook with Alerts + Trends tabs ──
        self.notebook = Gtk.Notebook()
        self.notebook.set_margin_start(8)
        self.notebook.set_margin_end(8)
        self.notebook.set_margin_top(4)

        # ── Tab 1: Alerts ──
        alerts_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Attack patterns bar (inside alerts tab, above card list)
        self._patterns_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._patterns_box.set_margin_start(4)
        self._patterns_box.set_margin_end(4)
        self._patterns_box.set_margin_top(4)
        alerts_page.pack_start(self._patterns_box, False, False, 0)

        # Alert list
        self.alert_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.lbl_empty = self.make_label(
            "No firewall drops detected. Your network is quiet.", "gp-dim"
        )
        self.lbl_empty.set_halign(Gtk.Align.CENTER)
        self.lbl_empty.set_valign(Gtk.Align.CENTER)
        self.lbl_empty.set_margin_top(40)
        self.alert_list.pack_start(self.lbl_empty, False, False, 0)

        scrolled_alerts = self.make_scrolled(self.alert_list)
        alerts_page.pack_start(scrolled_alerts, True, True, 0)

        self.notebook.append_page(alerts_page, Gtk.Label(label="Alerts"))

        # ── Tab 2: Trends ──
        trends_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Build the scrollable inner container FIRST
        trends_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Hourly drop rate chart (Cairo)
        self._trends_da = Gtk.DrawingArea()
        self._trends_da.set_size_request(-1, 180)
        self._trends_da.connect("draw", self._draw_trends)
        trends_inner.pack_start(self._trends_da, False, False, 4)

        # Top ports + top sources side by side
        top_paned = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_paned.set_margin_start(4)
        top_paned.set_margin_end(4)

        # Top 10 Attacked Ports
        ports_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        ports_box.get_style_context().add_class("gp-card")
        ports_box.pack_start(self.make_label("TOP ATTACKED PORTS", "gp-accent"), False, False, 4)
        self._ports_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        ports_box.pack_start(self._ports_list, True, True, 0)
        top_paned.pack_start(ports_box, True, True, 0)

        # Top 10 Source IPs
        sources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sources_box.get_style_context().add_class("gp-card")
        sources_box.pack_start(self.make_label("TOP SOURCE IPs", "gp-accent"), False, False, 4)
        self._sources_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        sources_box.pack_start(self._sources_list, True, True, 0)
        top_paned.pack_start(sources_box, True, True, 0)

        trends_inner.pack_start(top_paned, True, True, 4)

        # Wrap in scrolled container, pack into page
        scrolled_trends = self.make_scrolled(trends_inner)
        trends_page.pack_start(scrolled_trends, True, True, 0)

        self.notebook.append_page(trends_page, Gtk.Label(label="Trends"))

        root.pack_start(self.notebook, True, True, 0)

        # Bottom button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(8)
        btn_bar.set_margin_end(8)
        btn_bar.set_margin_bottom(4)

        btn_clear = self.make_button("Clear All", self._on_clear, "gp-btn-danger")
        btn_bar.pack_start(btn_clear, False, False, 0)

        self.btn_pause = self.make_button("Pause", self._on_pause, "gp-btn")
        btn_bar.pack_start(self.btn_pause, False, False, 0)

        btn_export = self.make_button("Export Log", self._on_export, "gp-btn-primary")
        btn_bar.pack_end(btn_export, False, False, 0)

        btn_refresh = self.make_button("Refresh", self._on_refresh, "gp-btn")
        btn_bar.pack_end(btn_refresh, False, False, 0)

        btn_help = self.make_help_button(sections=self.HELP_SECTIONS)
        btn_bar.pack_end(btn_help, False, False, 0)

        root.pack_start(btn_bar, False, False, 0)

        # Status bar
        root.pack_start(self.make_status_bar("Initializing..."), False, False, 0)

    # ── Data Loading ──────────────────────────────────────────────────

    def _load_data(self):
        """Background: fetch drops from dmesg + ids-events.json + trends."""
        dmesg_drops = fetch_drops()
        ids_events = load_ids_events()

        for evt in ids_events:
            if not isinstance(evt, dict):
                continue
            normalized = {
                "src": evt.get("source", evt.get("src", "")),
                "dst": evt.get("dst", ""),
                "proto": evt.get("proto", ""),
                "spt": evt.get("spt", ""),
                "dpt": evt.get("dpt", ""),
                "in": evt.get("in", ""),
                "out": evt.get("out", ""),
                "desc": evt.get("detail", evt.get("desc", "")),
                "severity": (evt.get("severity", "info") or "info").lower(),
                "type": evt.get("type", ""),
                "education": evt.get("education", ""),
            }
            if "epoch" in evt:
                normalized["ts"] = float(evt["epoch"])
                normalized["_is_epoch"] = True
            elif "ts" in evt:
                normalized["ts"] = evt["ts"]
                normalized["_is_epoch"] = isinstance(evt["ts"], str)
            else:
                normalized["ts"] = 0.0
            if not normalized["desc"] and normalized["src"]:
                normalized["desc"], normalized["severity"] = classify_drop(
                    normalized["src"], normalized["dst"],
                    normalized["proto"], normalized["spt"],
                    normalized["dpt"], normalized["in"]
                )
            dmesg_drops.append(normalized)

        # Sort by timestamp descending
        try:
            with open("/proc/uptime") as f:
                uptime = float(f.read().split()[0])
            boot_epoch = time.time() - uptime
        except Exception:
            boot_epoch = 0

        def sort_key(e):
            ts = e.get("ts", 0)
            if isinstance(ts, str):
                try:
                    return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    return 0
            if isinstance(ts, (int, float)) and ts > 1_000_000_000:
                return ts
            if isinstance(ts, (int, float)) and boot_epoch > 0:
                return boot_epoch + ts
            return 0

        dmesg_drops.sort(key=sort_key, reverse=True)
        drops = dmesg_drops[:MAX_ENTRIES]

        # Update trends
        trends = update_trends(drops)

        # Detect attack patterns
        patterns = detect_attack_patterns(drops)

        return drops, trends, patterns

    def _on_data_loaded(self, result):
        """Main thread: update UI with loaded data."""
        if isinstance(result, Exception):
            self.set_status(f"Error: {result}")
            return

        drops, trends, patterns = result

        # Notify on new critical events
        old_critical = sum(1 for d in self.drops if d.get("severity") == "danger")
        new_critical = sum(1 for d in drops if d.get("severity") == "danger")
        if new_critical > old_critical:
            diff = new_critical - old_critical
            try:
                import subprocess
                subprocess.run(["notify-send", "-u", "critical",
                    "Crow's Nest", f"{diff} new critical threat(s) detected"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except Exception:
                pass

        self.drops = drops
        self._trends = trends
        self._patterns = patterns
        self._rebuild_list()
        self._rebuild_patterns()
        self._rebuild_top_lists()
        self._trends_da.queue_draw()

    # ── UI Rebuild ────────────────────────────────────────────────────

    def _rebuild_list(self):
        """Rebuild the alert card list from self.drops."""
        for child in self.alert_list.get_children():
            self.alert_list.remove(child)

        total = len(self.drops)
        critical = sum(1 for d in self.drops if d.get("severity") == "danger")
        warnings = sum(1 for d in self.drops if d.get("severity") == "warning")

        self.lbl_total.set_text(str(total))
        self.lbl_alerts.set_text(str(critical))
        self.lbl_patterns.set_text(str(len(self._patterns)))

        if critical >= 10:
            self.lbl_threat.set_text("HIGH")
            self._set_label_class(self.lbl_threat, "gp-danger")
        elif critical >= 3 or warnings >= 15:
            self.lbl_threat.set_text("ELEVATED")
            self._set_label_class(self.lbl_threat, "gp-warning")
        elif total > 0:
            self.lbl_threat.set_text("LOW")
            self._set_label_class(self.lbl_threat, "gp-accent")
        else:
            self.lbl_threat.set_text("NOMINAL")
            self._set_label_class(self.lbl_threat, "gp-success")

        self.lbl_threat.override_font(Pango.FontDescription("monospace bold 28"))

        if total == 0:
            self.lbl_empty = self.make_label(
                "No firewall drops detected. Your network is quiet.", "gp-dim"
            )
            self.lbl_empty.set_halign(Gtk.Align.CENTER)
            self.lbl_empty.set_valign(Gtk.Align.CENTER)
            self.lbl_empty.set_margin_top(40)
            self.alert_list.pack_start(self.lbl_empty, False, False, 0)
        else:
            for drop in self.drops:
                card = self._make_alert_card(drop)
                self.alert_list.pack_start(card, False, False, 0)

        self.alert_list.show_all()

        pause_txt = " [PAUSED]" if self.paused else ""
        self.set_status(f"Monitoring{pause_txt} | {total} drops total | {critical} critical | {warnings} warnings")

    def _rebuild_patterns(self):
        """Rebuild attack patterns display."""
        for child in self._patterns_box.get_children():
            self._patterns_box.remove(child)

        if not self._patterns:
            self._patterns_box.hide()
            return

        self._patterns_box.show()
        for pat in self._patterns[:5]:  # Show max 5
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            sev = pat.get("severity", "warning")
            css = "gp-card-danger" if sev == "danger" else "gp-card-warning"
            card.get_style_context().add_class(css)

            lbl_type = self.make_label(pat.get("type", "PATTERN"), "gp-danger")
            card.pack_start(lbl_type, False, False, 0)

            lbl_desc = self.make_label(pat.get("desc", ""), "gp-text")
            card.pack_start(lbl_desc, True, True, 0)

            self._patterns_box.pack_start(card, False, False, 0)

        self._patterns_box.show_all()

    def _rebuild_top_lists(self):
        """Rebuild top ports and top sources lists."""
        # Top ports
        for child in self._ports_list.get_children():
            self._ports_list.remove(child)

        top_ports = sorted(
            self._trends.get("top_ports", {}).items(),
            key=lambda x: -x[1]
        )[:10]

        for port, count in top_ports:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_start(4)
            row.set_margin_end(4)
            lbl_port = self.make_label(f":{port}", "gp-accent")
            lbl_port.set_size_request(60, -1)
            row.pack_start(lbl_port, False, False, 0)
            lbl_count = self.make_label(str(count), "gp-text")
            row.pack_start(lbl_count, False, False, 0)
            # Bar visualization
            bar = Gtk.DrawingArea()
            bar.set_size_request(100, 12)
            max_count = top_ports[0][1] if top_ports else 1
            bar._bar_ratio = count / max_count if max_count > 0 else 0
            bar.connect("draw", self._draw_bar)
            row.pack_end(bar, True, True, 0)
            self._ports_list.pack_start(row, False, False, 1)

        if not top_ports:
            self._ports_list.pack_start(
                self.make_label("No data yet", "gp-dim"), False, False, 4
            )
        self._ports_list.show_all()

        # Top sources
        for child in self._sources_list.get_children():
            self._sources_list.remove(child)

        top_sources = sorted(
            self._trends.get("top_sources", {}).items(),
            key=lambda x: -x[1]
        )[:10]

        for src, count in top_sources:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_start(4)
            row.set_margin_end(4)
            country = _lookup_country(_GEOIP_NETWORKS, src)
            src_display = f"{src} ({country})" if country != "??" else src
            lbl_src = self.make_label(src_display, "gp-text")
            lbl_src.set_size_request(160, -1)
            row.pack_start(lbl_src, False, False, 0)
            lbl_count = self.make_label(str(count), "gp-dim")
            row.pack_start(lbl_count, False, False, 0)
            bar = Gtk.DrawingArea()
            bar.set_size_request(80, 12)
            max_count = top_sources[0][1] if top_sources else 1
            bar._bar_ratio = count / max_count if max_count > 0 else 0
            bar.connect("draw", self._draw_bar)
            row.pack_end(bar, True, True, 0)
            self._sources_list.pack_start(row, False, False, 1)

        if not top_sources:
            self._sources_list.pack_start(
                self.make_label("No data yet", "gp-dim"), False, False, 4
            )
        self._sources_list.show_all()

    def _draw_bar(self, widget, cr):
        """Draw a small horizontal bar for top-list items."""
        c = self.colors
        alloc = widget.get_allocation()
        w = alloc.width
        h = alloc.height
        ratio = getattr(widget, '_bar_ratio', 0)

        r, g, b = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0
        # Background
        cr.set_source_rgba(r, g, b, 0.1)
        cr.rectangle(0, 2, w, h - 4)
        cr.fill()
        # Filled portion
        cr.set_source_rgba(r, g, b, 0.6)
        cr.rectangle(0, 2, w * ratio, h - 4)
        cr.fill()

    def _draw_trends(self, widget, cr):
        """Draw the hourly drop rate bar chart."""
        c = self.colors
        alloc = widget.get_allocation()
        w = alloc.width
        h = alloc.height
        r, g, b = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0

        hourly = self._trends.get("hourly", {})
        if not hourly:
            cr.set_source_rgba(r, g, b, 0.3)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(12)
            msg = "No trend data yet. Collecting..."
            ext = cr.text_extents(msg)
            cr.move_to(w / 2 - ext.width / 2, h / 2)
            cr.show_text(msg)
            return

        # Get last 24 hours of data, ordered by time
        keys = sorted(hourly.keys())[-24:]
        totals = [hourly[k].get("total", 0) for k in keys]
        dangers = [hourly[k].get("danger", 0) for k in keys]
        max_val = max(max(totals, default=1), 1)

        margin_left = 40
        margin_right = 10
        margin_top = 25
        margin_bottom = 30
        chart_w = w - margin_left - margin_right
        chart_h = h - margin_top - margin_bottom
        bar_w = max(4, chart_w / max(len(keys), 1) - 2)

        # Title
        cr.set_source_rgba(r, g, b, 0.7)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        cr.move_to(margin_left, 15)
        cr.show_text("DROP RATE (last 24h)")

        # Y-axis labels
        cr.set_font_size(9)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        for i in range(5):
            y = margin_top + chart_h * (1 - i / 4)
            val = int(max_val * i / 4)
            cr.set_source_rgba(r, g, b, 0.3)
            cr.move_to(0, y + 3)
            cr.show_text(str(val))
            # Grid line
            cr.set_source_rgba(r, g, b, 0.06)
            cr.move_to(margin_left, y)
            cr.line_to(w - margin_right, y)
            cr.stroke()

        # Bars
        for i, (total, danger) in enumerate(zip(totals, dangers)):
            x = margin_left + i * (chart_w / len(keys))
            bar_h = (total / max_val) * chart_h if max_val > 0 else 0
            danger_h = (danger / max_val) * chart_h if max_val > 0 else 0

            # Total bar (accent)
            cr.set_source_rgba(r, g, b, 0.5)
            cr.rectangle(x + 1, margin_top + chart_h - bar_h, bar_w, bar_h)
            cr.fill()

            # Danger overlay (red)
            if danger > 0:
                dr, dg, db = _hex_to_rgb(self.colors["danger"]); cr.set_source_rgba(dr, dg, db, 0.7)
                cr.rectangle(x + 1, margin_top + chart_h - danger_h, bar_w, danger_h)
                cr.fill()

            # Hour label
            hour_str = keys[i][-2:]  # HH from YYYY-MM-DD-HH
            cr.set_source_rgba(r, g, b, 0.4)
            cr.set_font_size(8)
            ext = cr.text_extents(hour_str)
            cr.move_to(x + bar_w / 2 - ext.width / 2, margin_top + chart_h + 12)
            cr.show_text(hour_str)

    def _make_alert_card(self, drop):
        """Create a styled card widget for a single drop event."""
        severity = drop.get("severity", "info")
        if severity == "danger":
            css_class = "gp-card-danger"
        elif severity == "warning":
            css_class = "gp-card-warning"
        else:
            css_class = "gp-card-info"

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.get_style_context().add_class(css_class)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        ts_text = format_timestamp(drop.get("ts", 0))
        lbl_time = self.make_label(ts_text, "gp-dim")
        top_row.pack_start(lbl_time, False, False, 0)

        proto_text = format_proto(drop.get("proto", ""))
        lbl_proto = self.make_label(proto_text, "gp-accent")
        top_row.pack_start(lbl_proto, False, False, 0)

        if drop.get("in"):
            lbl_iface = self.make_label(f"[{drop['in']}]", "gp-dim")
            top_row.pack_start(lbl_iface, False, False, 0)

        sev_class = {"danger": "gp-danger", "warning": "gp-warning"}.get(severity, "gp-dim")
        sev_text = severity.upper()
        lbl_sev = self.make_label(sev_text, sev_class)
        top_row.pack_end(lbl_sev, False, False, 0)

        card.pack_start(top_row, False, False, 0)

        # Connection line
        src = drop.get("src", "?")
        dst = drop.get("dst", "?")
        spt = drop.get("spt", "")
        dpt = drop.get("dpt", "")
        src_country = _lookup_country(_GEOIP_NETWORKS, src)
        src_base = f"{src}:{spt}" if spt else src
        src_display = f"{src_base} ({src_country})" if src_country != "??" else src_base
        dst_display = f"{dst}:{dpt}" if dpt else dst
        conn_text = f"{src_display}  -->  {dst_display}"
        lbl_conn = self.make_label(conn_text, "gp-text")
        card.pack_start(lbl_conn, False, False, 0)

        # Description
        desc = drop.get("desc", "Unknown event")
        lbl_desc = self.make_label(desc, "gp-bright" if severity == "info" else "gp-text")
        card.pack_start(lbl_desc, False, False, 0)

        return card

    def _set_label_class(self, label, css_class):
        ctx = label.get_style_context()
        for cls in ["gp-danger", "gp-warning", "gp-success", "gp-accent", "gp-dim", "gp-text"]:
            ctx.remove_class(cls)
        ctx.add_class(css_class)

    # ── Theme ────────────────────────────────────────────────────────

    def on_theme_changed(self):
        self._trends_da.queue_draw()
        # Bars in top lists also need redraw — rebuild them
        self._rebuild_top_lists()

    # ── Polling ───────────────────────────────────────────────────────

    def _poll_tick(self):
        if not self.paused:
            self.run_async(self._load_data, self._on_data_loaded)
        return True

    # ── Button Handlers ───────────────────────────────────────────────

    def _on_clear(self, btn):
        self.drops = []
        self._patterns = []
        self._rebuild_list()
        self._rebuild_patterns()
        if not self.paused:
            self._on_pause(btn)
        self.set_status("Cleared and paused. Press Resume to continue monitoring.")

    def _on_pause(self, btn):
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.set_label("Resume")
            self.btn_pause.get_style_context().remove_class("gp-btn")
            self.btn_pause.get_style_context().add_class("gp-btn-primary")
            self.set_status(f"PAUSED | {len(self.drops)} drops displayed")
        else:
            self.btn_pause.set_label("Pause")
            self.btn_pause.get_style_context().remove_class("gp-btn-primary")
            self.btn_pause.get_style_context().add_class("gp-btn")
            self.set_status("Resumed monitoring...")

    def _on_export(self, btn):
        dialog = Gtk.FileChooserDialog(
            title="Export Crow's Nest Log",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_current_name(f"crowsnest-{time.strftime('%Y%m%d-%H%M%S')}.csv")

        # CSV first so it's the default
        csv_filt = Gtk.FileFilter()
        csv_filt.set_name("CSV spreadsheet (*.csv)")
        csv_filt.add_pattern("*.csv")
        dialog.add_filter(csv_filt)
        json_filt = Gtk.FileFilter()
        json_filt.set_name("JSON (*.json)")
        json_filt.add_pattern("*.json")
        dialog.add_filter(json_filt)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            try:
                # Build a display-friendly row for each drop
                export_data = []
                for d in self.drops:
                    entry = dict(d)
                    entry["time"] = format_timestamp(entry.get("ts", 0))
                    entry["protocol"] = format_proto(entry.get("proto", ""))
                    export_data.append(entry)

                if filepath.lower().endswith(".csv"):
                    import csv as _csv
                    # Stable column order — keep common IDS fields first
                    preferred = ["time", "ts", "severity", "type", "source", "dest",
                                 "protocol", "proto", "port", "detail", "education",
                                 "country", "iface"]
                    keys = []
                    seen = set()
                    for col in preferred:
                        for row in export_data:
                            if col in row and col not in seen:
                                keys.append(col); seen.add(col); break
                    for row in export_data:
                        for k in row.keys():
                            if k not in seen:
                                keys.append(k); seen.add(k)
                    with open(filepath, "w", newline="") as f:
                        w = _csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                        w.writeheader()
                        for row in export_data:
                            w.writerow(row)
                else:
                    with open(filepath, "w") as f:
                        json.dump(export_data, f, indent=2)

                self.set_status(f"Exported {len(export_data)} events to {os.path.basename(filepath)}")
            except Exception as e:
                self.set_status(f"Export failed: {e}")
                try:
                    self.show_error("Export failed",
                                    f"Could not write {os.path.basename(filepath)}.",
                                    detail=str(e))
                except Exception:
                    pass
        dialog.destroy()

    def _on_refresh(self, btn):
        self.set_status("Refreshing...")
        self.run_async(self._load_data, self._on_data_loaded)


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = CrowsNestApp()
    app.run()
