#!/usr/bin/env python3
"""gp-atlas — Atlas: Network Topology Diagram for Phantom OS"""
import sys, os
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import json
import re
import math
import subprocess
import time
import cairo

MODE_FILE = "/etc/phantom/current-mode"

def _hex_to_rgb(hex_str):
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
LEASES_CMD = ["sudo", "-n", "gp-leases"]

# ── Data Collection ──────────────────────────────────────────────────

def run_cmd(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""

def get_iface_ip(iface):
    out = run_cmd(["ip", "-4", "addr", "show", iface])
    m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', out)
    return m.group(1) if m else None

def is_iface_up(iface):
    out = run_cmd(["ip", "link", "show", iface])
    return "state UP" in out

def get_clients():
    clients = []
    try:
        r = subprocess.run(LEASES_CMD, capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 4:
                clients.append({
                    "mac": parts[1],
                    "ip": parts[2],
                    "name": parts[3] if parts[3] != "*" else parts[2],
                })
    except Exception:
        pass
    return clients

def read_current_mode():
    try:
        with open(MODE_FILE) as f:
            return f.read().strip().upper()
    except Exception:
        return "UNKNOWN"

def read_iface_bytes():
    """Read RX/TX bytes per interface from /proc/net/dev."""
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

def read_iface_errors():
    """Read RX/TX errors per interface from /proc/net/dev."""
    result = {}
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, data = line.split(":", 1)
                iface = iface.strip()
                fields = data.split()
                if len(fields) >= 12:
                    # fields[2]=rx_errors, fields[10]=tx_errors
                    result[iface] = (int(fields[2]), int(fields[10]))
    except Exception:
        pass
    return result

def get_conntrack_per_iface():
    """Get active conntrack flow count per interface by matching IPs to known subnets."""
    counts = {"wlan0": 0, "wg0": 0, "wg1": 0, "eth0": 0}
    try:
        result = subprocess.run(
            ["sudo", "conntrack", "-L"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return counts
        lines = result.stdout.strip().split("\n")[:2000]
        for line in lines:
            if not line.strip():
                continue
            # Extract src and dst IPs
            src_match = re.search(r'src=(\d+\.\d+\.\d+\.\d+)', line)
            dst_match = re.search(r'dst=(\d+\.\d+\.\d+\.\d+)', line)
            if not src_match and not dst_match:
                continue
            classified = False
            for ip_str in (src_match.group(1) if src_match else "", dst_match.group(1) if dst_match else ""):
                if not ip_str:
                    continue
                if ip_str.startswith("192.168.50."):
                    counts["wlan0"] += 1
                    classified = True
                    break
                elif ip_str.startswith("10.66.66."):
                    counts["wg0"] += 1
                    classified = True
                    break
                elif ip_str.startswith("10.66.67."):
                    counts["wg1"] += 1
                    classified = True
                    break
            if not classified:
                counts["eth0"] += 1
    except Exception:
        pass
    return counts


# ── Node Layout ──────────────────────────────────────────────────────

class Node:
    def __init__(self, name, label, x, y, radius=28, up=True, ip=None, node_type="normal"):
        self.name = name
        self.label = label
        self.x = x
        self.y = y
        self.radius = radius
        self.up = up
        self.ip = ip
        self.node_type = node_type  # "internet", "router", "iface", "tunnel", "client"
        self.rx_bps = 0
        self.tx_bps = 0
        self.rx_errors = 0
        self.tx_errors = 0
        self.flows = 0


class Edge:
    def __init__(self, n1, n2, up=True, throughput=0):
        self.n1 = n1
        self.n2 = n2
        self.up = up
        self.throughput = throughput  # bytes/sec for line thickness


# ── Main App ─────────────────────────────────────────────────────────

class AtlasApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Atlas?",
         "Atlas is a live map of your network. Every device and tunnel is drawn "
         "as a node; the lines between them show how traffic is routed right now.\n\n"
         "Watch it long enough and you'll see exactly how data flows out of your "
         "LAN, through the firewall, over the VPN tunnels, and onto the public "
         "internet. It updates in real time as mode switches happen or tunnels "
         "come up and down."),

        ("The canvas (main area)",
         "Nodes you'll see, depending on mode:\n\n"
         "• LAN clients — each connected phone/laptop/TV from the DHCP lease table.\n"
         "• wlan0 — your GhostPort access point (192.168.50.1).\n"
         "• eth0 — your WAN uplink to the ISP.\n"
         "• wg0 — the WireGuard control-plane tunnel (fleet / bridge).\n"
         "• wg1 — the WireGuard data-plane tunnel (your internet traffic relay).\n"
         "• tailscale0 — the always-on Tailscale mesh.\n\n"
         "Lines between nodes show routes. Line thickness = relative traffic volume "
         "(thicker = more bytes per second right now). Hover any node to see its IP, "
         "interface state, and current send/receive rate."),

        ("Reading the picture per mode",
         "ISP mode: LAN → wlan0 → eth0 → internet. Clean straight line.\n\n"
         "ZeroTrust: same path as ISP but DNS is locked to cloudflared on 127.0.0.1. "
         "Visually identical, DNS note appears under wlan0.\n\n"
         "DoubleHop: LAN → wlan0 → wg1 → internet (via data-plane relay). eth0 still "
         "visible for the encrypted tunnel traffic. wg0 is ALSO up (fleet control).\n\n"
         "ZHop: same as DoubleHop + DNS locked. Both tunnels up, strict DNS.\n\n"
         "If you expected a tunnel to be up and it's not drawn, something's wrong — "
         "check Anchor for tunnel status."),

        ("Traffic rates and errors",
         "Each interface node shows live rx/tx bytes per second. A few things to watch:\n\n"
         "• One client consuming most of the LAN bandwidth — look at the thick line "
         "and identify the device. Easy way to catch \"who's downloading stuff right now\".\n\n"
         "• Errors counter — if an interface shows nonzero errors, physical-layer "
         "problem (bad cable, weak WiFi signal, MTU mismatch). Investigate.\n\n"
         "• Conntrack count per interface — how many active connections. Spikes on "
         "eth0 often mean torrent/P2P activity; spikes on wg1 = VPN-backed traffic."),

        ("When a node goes red",
         "Red/alert state on a node means one of:\n"
         "• Interface is DOWN (expected if the mode doesn't use it).\n"
         "• High error rate (physical issue).\n"
         "• Tunnel handshake is stale (>3 min since last keepalive).\n\n"
         "Red isn't always bad — wg1 red in ISP mode is correct, because ISP mode "
         "doesn't use the data tunnel. Atlas just shows you the truth; interpretation "
         "depends on what mode you intended to be in."),

        ("Gotchas",
         "• No clickable nodes — Atlas is read-only. To interact with a tunnel or "
         "interface, use the other apps (Anchor, Bulkhead, Crew Manifest).\n\n"
         "• LAN clients only appear after DHCP — a device that pinned a static IP "
         "without DHCP won't show up. Crew Manifest is more thorough for that case.\n\n"
         "• The map auto-refreshes every few seconds. If it looks stuck, switch to "
         "another tab and back, or relaunch."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    def __init__(self):
        super().__init__("ATLAS", "atlas", (900, 650))
        self.nodes = []
        self.edges = []
        self.current_mode = "UNKNOWN"
        self._prev_bytes = {}  # iface -> (rx, tx, time)
        self._iface_rates = {}  # iface -> (rx_bps, tx_bps)
        self._hover_node = None  # Node under cursor or None
        self._conntrack_per_iface = {}  # iface -> flow count
        self._pulse_phase = 0  # Animation phase 0.0-1.0
        self.build_ui()
        self.refresh_topology()
        self.poll_start(10, self.refresh_topology)
        # Animation timer for traffic pulse (every 100ms)
        self._anim_timer = GLib.timeout_add(100, self._animate_tick)
        self._timers.append(self._anim_timer)

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header
        header = self.make_header("ATLAS", "Network Topology")
        root.pack_start(header, False, False, 0)

        # Drawing area with mouse tracking
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_events(
            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.canvas.connect("draw", self.on_draw)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("leave-notify-event", self._on_leave)
        root.pack_start(self.canvas, True, True, 0)

        # Help button row — read-only app so nothing else here
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(8)
        btn_bar.set_margin_end(8)
        btn_bar.set_margin_top(2)
        btn_bar.set_margin_bottom(2)
        btn_bar.pack_end(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)
        root.pack_start(btn_bar, False, False, 0)

        # Status bar
        self.status_bar = self.make_status_bar("Scanning network...")
        root.pack_start(self.status_bar, False, False, 0)

    def _animate_tick(self):
        """Advance animation phase and redraw."""
        self._pulse_phase = (self._pulse_phase + 0.05) % 1.0
        self.canvas.queue_draw()
        return True

    def refresh_topology(self):
        self.run_async(self._collect_data, self._update_ui)
        return True

    def _collect_data(self):
        data = {}
        data["mode"] = read_current_mode()
        data["eth0_ip"] = get_iface_ip("eth0")
        data["eth0_up"] = is_iface_up("eth0")
        data["wlan0_ip"] = get_iface_ip("wlan0") or "192.168.50.1"
        data["wlan0_up"] = is_iface_up("wlan0")
        data["wg0_up"] = is_iface_up("wg0")
        data["wg0_ip"] = get_iface_ip("wg0")
        data["wg1_up"] = is_iface_up("wg1")
        data["wg1_ip"] = get_iface_ip("wg1")
        data["ts0_up"] = is_iface_up("tailscale0")
        data["ts0_ip"] = get_iface_ip("tailscale0")
        data["clients"] = get_clients()

        # Traffic rates
        current_bytes = read_iface_bytes()
        now = time.time()
        rates = {}
        for iface in ("eth0", "wlan0", "wg0", "wg1", "tailscale0"):
            if iface in current_bytes and iface in self._prev_bytes:
                prx, ptx, pt = self._prev_bytes[iface]
                rx, tx = current_bytes[iface]
                dt = now - pt
                if dt > 0:
                    rates[iface] = (max(0, (rx - prx) / dt), max(0, (tx - ptx) / dt))
            if iface in current_bytes:
                self._prev_bytes[iface] = (current_bytes[iface][0], current_bytes[iface][1], now)
        data["rates"] = rates

        # Interface errors
        data["errors"] = read_iface_errors()

        # Conntrack count per interface
        data["conntrack_per_iface"] = get_conntrack_per_iface()

        return data

    def _update_ui(self, data):
        if isinstance(data, Exception):
            self.set_status(f"Error: {data}")
            return
        self.current_mode = data["mode"]
        self._iface_rates = data.get("rates", {})
        self._conntrack_per_iface = data.get("conntrack_per_iface", {})
        self._build_graph(data)
        self.canvas.queue_draw()
        n_clients = len(data["clients"])
        tunnels = sum(1 for k in ["wg0_up", "wg1_up", "ts0_up"] if data.get(k))
        total_flows = sum(self._conntrack_per_iface.values())
        self.set_status(
            f"Mode: {data['mode']}  |  Clients: {n_clients}  |  "
            f"Tunnels: {tunnels}  |  Flows: {total_flows}  |  "
            f"Updated: {time.strftime('%H:%M:%S')}"
        )

    def _build_graph(self, data):
        self.nodes = []
        self.edges = []

        alloc = self.canvas.get_allocation()
        w = max(alloc.width, 800)
        h = max(alloc.height, 550)
        cx = w / 2

        y_internet = 50
        y_eth0 = 140
        y_router = 260
        y_branches = 380
        y_clients = 490

        rates = data.get("rates", {})
        errors = data.get("errors", {})
        conntrack = data.get("conntrack_per_iface", {})

        # Internet node
        inet = Node("internet", "INTERNET", cx, y_internet, 30, True, None, "internet")
        self.nodes.append(inet)

        # eth0
        eth0_label = f"eth0\n{data['eth0_ip'] or 'no IP'}"
        eth0 = Node("eth0", eth0_label, cx, y_eth0, 24, data["eth0_up"], data["eth0_ip"], "iface")
        if "eth0" in rates:
            eth0.rx_bps, eth0.tx_bps = rates["eth0"]
        if "eth0" in errors:
            eth0.rx_errors, eth0.tx_errors = errors["eth0"]
        eth0.flows = conntrack.get("eth0", 0)
        self.nodes.append(eth0)

        eth0_rate = eth0.rx_bps + eth0.tx_bps
        self.edges.append(Edge(inet, eth0, data["eth0_up"], eth0_rate))

        # Router
        router_label = f"GhostPort\n{self.current_mode}"
        router = Node("router", router_label, cx, y_router, 42, True, None, "router")
        router.flows = sum(conntrack.values())
        self.nodes.append(router)
        self.edges.append(Edge(eth0, router, data["eth0_up"], eth0_rate))

        # Branches
        branches = []
        branches.append(("wlan0", f"wlan0 AP\n{data['wlan0_ip']}", data["wlan0_up"], data["wlan0_ip"], "iface", data.get("clients", [])))

        if data["wg0_up"]:
            branches.append(("wg0", f"wg0\n{data['wg0_ip'] or 'control'}", True, data["wg0_ip"], "tunnel", []))
        if data["wg1_up"]:
            branches.append(("wg1", f"wg1\n{data['wg1_ip'] or 'data'}", True, data["wg1_ip"], "tunnel", []))
        if data["ts0_up"]:
            branches.append(("ts0", f"tailscale0\n{data['ts0_ip'] or ''}", True, data["ts0_ip"], "tunnel", []))

        n_branches = len(branches)
        if n_branches > 0:
            spacing = min(180, (w - 160) / max(n_branches, 1))
            start_x = cx - (n_branches - 1) * spacing / 2

            for i, (name, label, up, ip, ntype, clients) in enumerate(branches):
                bx = start_x + i * spacing
                bnode = Node(name, label, bx, y_branches, 24, up, ip, ntype)

                iface_key = name if name != "ts0" else "tailscale0"
                if iface_key in rates:
                    bnode.rx_bps, bnode.tx_bps = rates[iface_key]
                if iface_key in errors:
                    bnode.rx_errors, bnode.tx_errors = errors[iface_key]
                bnode.flows = conntrack.get(name, 0)

                self.nodes.append(bnode)
                edge_rate = bnode.rx_bps + bnode.tx_bps
                self.edges.append(Edge(router, bnode, up, edge_rate))

                if name == "wlan0" and clients:
                    max_show = min(len(clients), 8)
                    client_spacing = min(60, (spacing * 1.5) / max(max_show, 1))
                    cstart = bx - (max_show - 1) * client_spacing / 2
                    for j, cl in enumerate(clients[:max_show]):
                        clx = cstart + j * client_spacing
                        cl_label = cl["name"][:12]
                        cnode = Node(f"client_{j}", cl_label, clx, y_clients, 14, True, cl["ip"], "client")
                        self.nodes.append(cnode)
                        self.edges.append(Edge(bnode, cnode, True, 0))

                    if len(clients) > max_show:
                        extra = Node("more", f"+{len(clients) - max_show} more", bx, y_clients + 35, 12, True, None, "client")
                        self.nodes.append(extra)

    # ── Mouse Hover ──────────────────────────────────────────────────

    def _on_motion(self, widget, event):
        """Track hover position and find nearest node."""
        mx, my = event.x, event.y
        self._hover_node = None
        for node in self.nodes:
            dx = mx - node.x
            dy = my - node.y
            if dx * dx + dy * dy <= (node.radius + 5) ** 2:
                self._hover_node = node
                break
        widget.queue_draw()

    def _on_leave(self, widget, event):
        self._hover_node = None
        widget.queue_draw()

    # ── Drawing ──────────────────────────────────────────────────────

    def on_draw(self, widget, cr):
        c = self.colors
        alloc = widget.get_allocation()
        w, h = alloc.width, alloc.height

        # Background
        bg_r = c['r'] * 4 / 100 / 255
        bg_g = c['g'] * 4 / 100 / 255
        bg_b = c['b'] * 4 / 100 / 255
        cr.set_source_rgb(bg_r, bg_g, bg_b)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        ar, ag, ab = c['r'] / 255, c['g'] / 255, c['b'] / 255

        # Draw edges with animated traffic pulses
        for edge in self.edges:
            self._draw_edge(cr, edge, ar, ag, ab)

        # Draw nodes
        for node in self.nodes:
            self._draw_node(cr, node, ar, ag, ab)

        # Draw hover tooltip
        if self._hover_node:
            self._draw_tooltip(cr, self._hover_node, ar, ag, ab)

    def _draw_edge(self, cr, edge, ar, ag, ab):
        n1, n2 = edge.n1, edge.n2
        x1, y1 = n1.x, n1.y + n1.radius
        x2, y2 = n2.x, n2.y - n2.radius

        # Line width proportional to throughput (min 1.5, max 6)
        if edge.throughput > 0:
            thickness = min(6, max(1.5, 1.5 + math.log1p(edge.throughput / 1024) * 0.5))
        else:
            thickness = 1.5

        if edge.up:
            cr.set_source_rgba(ar, ag, ab, 0.5)
            cr.set_line_width(thickness)
        else:
            dr, dg, db = _hex_to_rgb(self.colors["danger"]); cr.set_source_rgba(dr, dg, db, 0.4)
            cr.set_line_width(1.5)
            cr.set_dash([6, 4])

        cr.move_to(x1, y1)
        cr.line_to(x2, y2)
        cr.stroke()
        cr.set_dash([])

        # Animated traffic pulse dot (only if traffic > 0 and link up)
        if edge.up and edge.throughput > 100:
            # Dot travels from n1 to n2
            t = self._pulse_phase
            px = x1 + (x2 - x1) * t
            py = y1 + (y2 - y1) * t
            dot_size = min(4, max(2, thickness * 0.6))
            cr.set_source_rgba(ar, ag, ab, 0.9)
            cr.arc(px, py, dot_size, 0, 2 * math.pi)
            cr.fill()

    def _draw_node(self, cr, node, ar, ag, ab):
        x, y, r = node.x, node.y, node.radius

        if node.node_type == "router":
            cr.set_source_rgba(ar, ag, ab, 0.2)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(ar, ag, ab, 0.9)
            cr.set_line_width(2.5)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgba(ar, ag, ab, 1.0)
            self._draw_centered_text(cr, x, y, node.label, 10, True)
            # Flow count badge
            if node.flows > 0:
                self._draw_flow_badge(cr, x, y, r, node.flows, ar, ag, ab)

        elif node.node_type == "internet":
            ir, ig, ib = _hex_to_rgb(self.colors["info"])
            cr.set_source_rgba(ir, ig, ib, 0.15)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(ir, ig, ib, 0.7)
            cr.set_line_width(1.5)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgba(ir, ig, ib, 1.0)
            self._draw_centered_text(cr, x, y, "WAN", 11, True)

        elif node.node_type == "tunnel":
            cr.set_source_rgba(ar, ag, ab, 0.1)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()
            if node.up:
                cr.set_source_rgba(ar, ag, ab, 0.7)
            else:
                dr, dg, db = _hex_to_rgb(self.colors["danger"]); cr.set_source_rgba(dr, dg, db, 0.5)
            cr.set_line_width(1.5)
            cr.set_dash([4, 3])
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.stroke()
            cr.set_dash([])
            cr.set_source_rgba(ar, ag, ab, 0.9)
            self._draw_centered_text(cr, x, y, node.label, 8, False)
            # Flow count badge
            if node.flows > 0:
                self._draw_flow_badge(cr, x, y, r, node.flows, ar, ag, ab)

        elif node.node_type == "client":
            cr.set_source_rgba(ar, ag, ab, 0.4)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(ar, ag, ab, 0.8)
            self._draw_centered_text(cr, x, y + r + 10, node.label, 7, False)

        else:
            # Normal interface
            cr.set_source_rgba(ar, ag, ab, 0.12)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()
            if node.up:
                cr.set_source_rgba(ar, ag, ab, 0.7)
            else:
                dr, dg, db = _hex_to_rgb(self.colors["danger"]); cr.set_source_rgba(dr, dg, db, 0.5)
            cr.set_line_width(1.5)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgba(ar, ag, ab, 0.9)
            self._draw_centered_text(cr, x, y, node.label, 8, False)
            # Flow count badge
            if node.flows > 0:
                self._draw_flow_badge(cr, x, y, r, node.flows, ar, ag, ab)

        # Highlight hovered node
        if self._hover_node is node:
            cr.set_source_rgba(1, 1, 1, 0.15)
            cr.arc(x, y, r + 3, 0, 2 * math.pi)
            cr.stroke()

    def _draw_flow_badge(self, cr, x, y, r, flows, ar, ag, ab):
        """Draw a flow count badge below a node."""
        badge_text = f"{flows} flows"
        cr.set_font_size(8)
        ext = cr.text_extents(badge_text)
        bx = x - ext.width / 2 - 4
        by = y + r + 6
        cr.set_source_rgba(ar, ag, ab, 0.15)
        cr.rectangle(bx, by, ext.width + 8, 14)
        cr.fill()
        cr.set_source_rgba(ar, ag, ab, 0.7)
        cr.rectangle(bx, by, ext.width + 8, 14)
        cr.stroke()
        cr.move_to(bx + 4, by + 11)
        cr.show_text(badge_text)

    @staticmethod
    def _get_iface_uptime(iface):
        """Get interface uptime string. Returns 'UP (Xh Ym)' or 'DOWN'."""
        try:
            with open(f"/sys/class/net/{iface}/operstate") as f:
                state = f.read().strip()
        except Exception:
            return None
        if state != "up":
            return "DOWN"
        # Use system uptime as proxy for interface uptime
        try:
            with open("/proc/uptime") as f:
                uptime_secs = float(f.read().split()[0])
            hours = int(uptime_secs // 3600)
            mins = int((uptime_secs % 3600) // 60)
            if hours > 0:
                return f"UP ({hours}h {mins}m)"
            else:
                return f"UP ({mins}m)"
        except Exception:
            return "UP"

    def _draw_tooltip(self, cr, node, ar, ag, ab):
        """Draw a tooltip box near the hovered node."""
        lines = []
        if node.ip:
            lines.append(f"IP: {node.ip}")
        if node.rx_bps > 0 or node.tx_bps > 0:
            lines.append(f"RX: {self._fmt_rate(node.rx_bps)}  TX: {self._fmt_rate(node.tx_bps)}")
        if node.rx_errors > 0 or node.tx_errors > 0:
            lines.append(f"Errors: RX={node.rx_errors} TX={node.tx_errors}")
        if node.flows > 0:
            lines.append(f"Active flows: {node.flows}")
        # Interface uptime
        iface_name = node.name
        if iface_name == "ts0":
            iface_name = "tailscale0"
        if node.node_type in ("iface", "tunnel"):
            uptime_str = self._get_iface_uptime(iface_name)
            if uptime_str:
                lines.append(f"Uptime: {uptime_str}")
            elif node.up:
                lines.append("Status: UP")
            else:
                lines.append("Status: DOWN")
        elif node.up:
            lines.append("Status: UP")
        else:
            lines.append("Status: DOWN")

        if not lines:
            return

        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9)

        # Measure text
        max_w = 0
        line_h = 13
        for line in lines:
            ext = cr.text_extents(line)
            if ext.width > max_w:
                max_w = ext.width

        pad = 6
        tip_w = max_w + pad * 2
        tip_h = len(lines) * line_h + pad * 2
        tip_x = node.x + node.radius + 8
        tip_y = node.y - tip_h / 2

        # Background
        bgr, bgg, bgb = _hex_to_rgb(self.colors.get("bg", "#0a0a0a")); cr.set_source_rgba(bgr, bgg, bgb, 0.92)
        cr.rectangle(tip_x, tip_y, tip_w, tip_h)
        cr.fill()

        # Border
        cr.set_source_rgba(ar, ag, ab, 0.5)
        cr.set_line_width(1)
        cr.rectangle(tip_x, tip_y, tip_w, tip_h)
        cr.stroke()

        # Text
        cr.set_source_rgba(ar, ag, ab, 0.9)
        for i, line in enumerate(lines):
            cr.move_to(tip_x + pad, tip_y + pad + (i + 1) * line_h - 2)
            cr.show_text(line)

    def _draw_centered_text(self, cr, x, y, text, font_size, bold):
        cr.select_font_face("monospace", 0, 1 if bold else 0)
        cr.set_font_size(font_size)

        lines = text.split("\n")
        line_height = font_size + 3
        total_h = len(lines) * line_height
        start_y = y - total_h / 2 + font_size / 2

        for i, line in enumerate(lines):
            extents = cr.text_extents(line)
            tx = x - extents.width / 2
            ty = start_y + i * line_height
            cr.move_to(tx, ty)
            cr.show_text(line)

    @staticmethod
    def _fmt_rate(bps):
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        else:
            return f"{bps / (1024 * 1024):.1f} MB/s"

    def on_theme_changed(self):
        self.canvas.queue_draw()


if __name__ == "__main__":
    app = AtlasApp()
    app.run()
