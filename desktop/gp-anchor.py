#!/usr/bin/env python3
"""gp-anchor — Anchor: VPN Kill Switch for Phantom OS"""
import sys, os
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo
import json
import re
import time
import subprocess

def _hex_to_rgb(hex_str):
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)

# ── Config ──────────────────────────────────────────────────────────

CONFIG_DIR = os.path.expanduser("~/.config/phantom")
CONFIG_FILE = os.path.join(CONFIG_DIR, "killswitch.json")
MODE_FILE = "/etc/phantom/current-mode"

# Tunnel quality thresholds
LATENCY_THRESHOLDS = {"A": 50, "B": 100, "C": 150, "D": 300}  # ms
LOSS_THRESHOLDS = {"A": 0, "B": 2, "C": 5, "D": 15}  # percent

# Max uptime history samples (1 per 3s refresh = 1200 = 1 hour)
MAX_UPTIME_SAMPLES = 1200

# ── Helpers ─────────────────────────────────────────────────────────

def load_armed_state():
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
            return data.get("armed", False)
    except Exception:
        return False

def save_armed_state(armed):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"armed": armed}, f)
    except Exception:
        pass

def read_current_mode():
    try:
        with open(MODE_FILE) as f:
            return f.read().strip().lower()
    except Exception:
        return "unknown"

def humanize_handshake(text):
    if not text:
        return "Never"
    text = text.strip()
    if text == "0":
        return "Never"
    return text

def format_transfer(text):
    if not text:
        return "No data"
    return text.strip()

def ping_endpoint(host, count=3, timeout=5):
    """Ping a host and return (avg_ms, loss_pct) or (None, 100)."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if result.returncode != 0:
            return None, 100.0

        # Parse loss
        loss_match = re.search(r'(\d+)% packet loss', result.stdout)
        loss = float(loss_match.group(1)) if loss_match else 100.0

        # Parse avg latency from "rtt min/avg/max/mdev = ..."
        rtt_match = re.search(r'rtt.*?=\s*[\d.]+/([\d.]+)/', result.stdout)
        avg_ms = float(rtt_match.group(1)) if rtt_match else None

        return avg_ms, loss
    except Exception:
        return None, 100.0


def grade_quality(latency_ms, loss_pct):
    """Return quality grade A-F based on latency and loss."""
    if latency_ms is None or loss_pct >= 100:
        return "F"

    # Grade by latency
    lat_grade = "F"
    for grade in ("A", "B", "C", "D"):
        if latency_ms <= LATENCY_THRESHOLDS[grade]:
            lat_grade = grade
            break

    # Grade by loss
    loss_grade = "F"
    for grade in ("A", "B", "C", "D"):
        if loss_pct <= LOSS_THRESHOLDS[grade]:
            loss_grade = grade
            break

    # Take the worse of the two
    grades = ["A", "B", "C", "D", "F"]
    return grades[max(grades.index(lat_grade), grades.index(loss_grade))]


# ── Main App ────────────────────────────────────────────────────────

class AnchorApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Anchor?",
         "Anchor is your VPN Kill Switch — a safety net that blocks all internet "
         "traffic if your VPN tunnel drops.\n\n"
         "Without a kill switch, if your VPN suddenly disconnects (bad signal, "
         "tunnel reset, ISP hiccup), your computer happily falls back to the "
         "naked internet connection and your real IP leaks. With Anchor armed, "
         "nothing leaves the device until the tunnel is back up. Safer, but you "
         "lose internet for the duration of the outage."),

        ("The big DISARMED / ARMED toggle",
         "The large button at the top is the master switch. Two states:\n\n"
         "• DISARMED (grey) — normal operation. Traffic flows whether the VPN is "
         "up or not. If the tunnel drops, your real IP is exposed until it reconnects.\n\n"
         "• ARMED (accent color) — kill switch active. If the tunnel handshake is "
         "stale or the interface goes down, nftables blocks all forwarded traffic "
         "until it recovers. Your LAN devices will see internet outages as long as "
         "the tunnel is unhealthy.\n\n"
         "Click the button to toggle. There's a brief confirmation dialog if arming "
         "would immediately interrupt traffic."),

        ("Auto-arm suggestion banner",
         "If tunnel quality degrades (high latency, missed handshakes, packet loss) "
         "a warning banner appears offering to arm the switch for you.\n\n"
         "The banner only shows up when there's a real reason — it doesn't nag. "
         "Click \"Arm Now\" to activate, or ignore it and the banner dismisses "
         "when quality recovers."),

        ("Current Mode",
         "Shows which firewall profile is active (ISP / ZeroTrust / DoubleHop / ZHop).\n\n"
         "The kill switch is most useful in DoubleHop and ZHop — those are the modes "
         "that route your traffic through the WireGuard data plane. In ISP mode "
         "there's no VPN to protect, so arming the switch just blocks everything."),

        ("Tunnel Status cards",
         "Three cards showing the state of each tunnel:\n\n"
         "• wg0 (Control Plane) — always-on link to the fleet server. Used for "
         "heartbeats, bridge messaging, and OTA updates. Low bandwidth.\n\n"
         "• wg1 (Data Plane) — the big tunnel your actual internet traffic flows "
         "through in DoubleHop/ZHop. This is the one the kill switch watches.\n\n"
         "• tailscale0 (Management) — the always-on Tailscale mesh. Used for "
         "remote admin access. Stays up in every mode.\n\n"
         "Each card shows: interface name, current status, peer endpoint, last "
         "handshake time. \"Last handshake\" > 3 minutes is usually a problem."),

        ("Tunnel Quality",
         "Per-tunnel quality score based on latency, packet loss, and handshake "
         "freshness. Ranges from 0 (broken) to 100 (perfect).\n\n"
         "• 80+ — everything's fine, normal to see minor dips.\n"
         "• 50-80 — degraded but usable; check ISP / signal.\n"
         "• < 50 — poor; this is where Auto-arm fires.\n\n"
         "Grades are a rolling 60-second window, so they lag slightly behind reality."),

        ("Uptime sparklines",
         "The little squiggly graphs show each tunnel's up/down state over the last hour.\n\n"
         "Solid line = up. Gaps = down. One or two small gaps = normal handshake "
         "blips. Many gaps = tunnel is flapping; investigate the peer endpoint or "
         "your upstream connection."),

        ("When to arm, when not",
         "ARM IT when:\n"
         "• You're downloading/uploading something sensitive and NEED the VPN\n"
         "• You notice tunnel quality degrading and want to fail safe\n"
         "• You're worried about specific traffic (work, legal, research) leaking\n\n"
         "DON'T ARM IT (or disarm first) when:\n"
         "• You're about to switch modes (mode switches flap the tunnel briefly)\n"
         "• You're troubleshooting connectivity — it's one more moving part\n"
         "• The device is running background updates that can't survive outages\n\n"
         "The safe fallback if you ever get stuck: `sudo gp-mode isp` in a terminal "
         "drops back to passthrough mode, which the kill switch allows through."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    def __init__(self):
        super().__init__("Anchor", "anchor", (750, 650))
        self.armed = False
        self.start_time = time.time()
        # Tunnel quality tracking
        self._tunnel_quality = {
            "wg0": {"latency": None, "loss": 100.0, "grade": "?", "endpoint": None},
            "wg1": {"latency": None, "loss": 100.0, "grade": "?", "endpoint": None},
        }
        # Uptime sparkline history: list of booleans (True=up)
        self._uptime_history = {
            "wg0": [],
            "wg1": [],
            "tailscale0": [],
        }
        self._auto_arm_suggested = False
        self.build_ui()
        self.refresh_all()
        self.poll_start(3, self.refresh_all)
        # Ping tunnels every 10s (separate from UI refresh)
        self.poll_start(10, self._ping_tunnels_tick)

    def build_ui(self):
        self._apply_css(self._build_extra_css())

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        header = self.make_header("ANCHOR", "VPN Kill Switch")
        vbox.pack_start(header, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)

        # ── Kill Switch Section ──
        self.switch_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.switch_frame.set_halign(Gtk.Align.CENTER)
        self.switch_frame.set_valign(Gtk.Align.CENTER)

        self.toggle_btn = Gtk.Button(label="DISARMED")
        self.toggle_btn.get_style_context().add_class("anchor-toggle")
        self.toggle_btn.get_style_context().add_class("gp-toggle-off")
        self.toggle_btn.set_size_request(280, 60)
        self.toggle_btn.connect("clicked", self._on_toggle)
        self.switch_frame.pack_start(self.toggle_btn, False, False, 4)

        self.state_label = self.make_label("Kill switch is disarmed", "anchor-state-disarmed")
        self.state_label.set_halign(Gtk.Align.CENTER)
        self.switch_frame.pack_start(self.state_label, False, False, 0)

        self.switch_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.switch_container.get_style_context().add_class("anchor-disarmed")
        self.switch_container.set_margin_start(40)
        self.switch_container.set_margin_end(40)
        self.switch_container.pack_start(self.switch_frame, True, True, 8)

        content.pack_start(self.switch_container, False, False, 0)

        # Auto-arm suggestion banner (hidden by default)
        self._auto_arm_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._auto_arm_box.get_style_context().add_class("gp-card-warning")
        self._auto_arm_box.set_no_show_all(True)
        self._auto_arm_box.hide()

        lbl_warn = self.make_label("Tunnel quality is poor. Consider arming the kill switch.", "gp-warning")
        self._auto_arm_box.pack_start(lbl_warn, True, True, 4)

        arm_btn = self.make_button("Arm Now", self._on_toggle, "gp-btn-primary")
        self._auto_arm_box.pack_end(arm_btn, False, False, 4)

        content.pack_start(self._auto_arm_box, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(sep, False, False, 4)

        # Mode Display
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_title = self.make_label("Current Mode:", "gp-dim")
        mode_box.pack_start(mode_title, False, False, 0)
        self.mode_label = self.make_label("---", "mode-label")
        mode_box.pack_start(self.mode_label, False, False, 0)
        content.pack_start(mode_box, False, False, 0)

        # ── Tunnel Status Panel ──
        tunnel_title = self.make_label("Tunnel Status", "gp-accent")
        content.pack_start(tunnel_title, False, False, 0)

        tunnel_grid = Gtk.Grid()
        tunnel_grid.set_column_spacing(12)
        tunnel_grid.set_row_spacing(8)
        tunnel_grid.set_column_homogeneous(True)

        self.wg0_card = self._build_tunnel_card("wg0", "Control Plane")
        tunnel_grid.attach(self.wg0_card["box"], 0, 0, 1, 1)

        self.wg1_card = self._build_tunnel_card("wg1", "Data Plane")
        tunnel_grid.attach(self.wg1_card["box"], 1, 0, 1, 1)

        self.ts_card = self._build_tunnel_card("tailscale0", "Management")
        tunnel_grid.attach(self.ts_card["box"], 2, 0, 1, 1)

        content.pack_start(tunnel_grid, False, False, 0)

        # ── Tunnel Quality Section ──
        quality_title = self.make_label("Tunnel Quality", "gp-accent")
        content.pack_start(quality_title, False, False, 4)

        quality_grid = Gtk.Grid()
        quality_grid.set_column_spacing(16)
        quality_grid.set_row_spacing(4)
        quality_grid.set_column_homogeneous(True)

        # wg0 quality card
        self._wg0_quality_card = self._build_quality_card("wg0")
        quality_grid.attach(self._wg0_quality_card["box"], 0, 0, 1, 1)

        # wg1 quality card
        self._wg1_quality_card = self._build_quality_card("wg1")
        quality_grid.attach(self._wg1_quality_card["box"], 1, 0, 1, 1)

        content.pack_start(quality_grid, False, False, 0)

        # ── Uptime Sparklines ──
        sparkline_title = self.make_label("Uptime (last 1h)", "gp-accent")
        content.pack_start(sparkline_title, False, False, 4)

        sparkline_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        for iface in ("wg0", "wg1", "tailscale0"):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = self.make_label(f"{iface}:", "gp-dim")
            lbl.set_size_request(80, -1)
            row.pack_start(lbl, False, False, 0)

            da = Gtk.DrawingArea()
            da.set_size_request(300, 16)
            da._iface = iface
            da.connect("draw", self._draw_sparkline)
            row.pack_start(da, True, True, 0)

            sparkline_box.pack_start(row, False, False, 0)

        self._sparkline_box = sparkline_box
        content.pack_start(sparkline_box, False, False, 0)

        scrolled = self.make_scrolled(content)
        vbox.pack_start(scrolled, True, True, 0)

        # Bottom action bar — just the Help button; the main arm/disarm
        # interaction lives on the big toggle above.
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom_bar.set_margin_start(8)
        bottom_bar.set_margin_end(8)
        bottom_bar.set_margin_top(4)
        bottom_bar.set_margin_bottom(4)
        bottom_bar.pack_end(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)
        vbox.pack_start(bottom_bar, False, False, 0)

        self.status_bar = self.make_status_bar("Initializing...")
        vbox.pack_start(self.status_bar, False, False, 0)

    def _build_tunnel_card(self, iface, description):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("gp-card")

        name_label = Gtk.Label()
        name_label.set_markup(f"<b>{iface}</b>")
        name_label.set_halign(Gtk.Align.START)
        name_label.get_style_context().add_class("gp-accent")
        card.pack_start(name_label, False, False, 0)

        desc_label = self.make_label(description, "gp-dim")
        card.pack_start(desc_label, False, False, 0)

        status_label = self.make_label("--", "gp-dim")
        card.pack_start(status_label, False, False, 2)

        detail1 = self.make_label("", "gp-dim")
        card.pack_start(detail1, False, False, 0)

        detail2 = self.make_label("", "gp-dim")
        card.pack_start(detail2, False, False, 0)

        detail3 = self.make_label("", "gp-dim")
        card.pack_start(detail3, False, False, 0)

        return {
            "box": card,
            "status": status_label,
            "detail1": detail1,
            "detail2": detail2,
            "detail3": detail3,
        }

    def _build_quality_card(self, iface):
        """Build a tunnel quality card with grade, latency, loss."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("gp-card")

        # Grade (big letter)
        grade_lbl = self.make_label("?", "gp-dim")
        grade_lbl.set_halign(Gtk.Align.CENTER)
        card.pack_start(grade_lbl, False, False, 2)

        # Interface name
        name_lbl = self.make_label(iface, "gp-accent")
        name_lbl.set_halign(Gtk.Align.CENTER)
        card.pack_start(name_lbl, False, False, 0)

        # Latency
        lat_lbl = self.make_label("Latency: --", "gp-dim")
        card.pack_start(lat_lbl, False, False, 0)

        # Loss
        loss_lbl = self.make_label("Loss: --", "gp-dim")
        card.pack_start(loss_lbl, False, False, 0)

        return {
            "box": card,
            "grade": grade_lbl,
            "latency": lat_lbl,
            "loss": loss_lbl,
        }

    # ── Sparkline Drawing ────────────────────────────────────────────

    def _draw_sparkline(self, widget, cr):
        """Draw uptime sparkline for a tunnel interface."""
        c = self.colors
        alloc = widget.get_allocation()
        w = alloc.width
        h = alloc.height
        iface = widget._iface
        history = self._uptime_history.get(iface, [])

        r, g, b = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0

        if not history:
            cr.set_source_rgba(r, g, b, 0.15)
            cr.rectangle(0, 2, w, h - 4)
            cr.fill()
            cr.set_source_rgba(r, g, b, 0.4)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(9)
            cr.move_to(4, h - 4)
            cr.show_text("No data yet")
            return

        n = len(history)
        bar_w = max(1, w / max(n, 1))

        for i, up in enumerate(history):
            x = i * bar_w
            if up:
                cr.set_source_rgba(r, g, b, 0.7)
            else:
                dr, dg, db = _hex_to_rgb(self.colors["danger"]); cr.set_source_rgba(dr, dg, db, 0.6)
            cr.rectangle(x, 2, max(1, bar_w - 0.5), h - 4)
            cr.fill()

    # ── Toggle Logic ────────────────────────────────────────────────

    def _on_toggle(self, btn):
        if self.armed:
            self.run_async(self._disarm_killswitch, self._on_toggle_done)
        else:
            self.run_async(self._arm_killswitch, self._on_toggle_done)

    def _arm_killswitch(self):
        # Killswitch chain lives in `inet filter` and hooks forward at priority -10,
        # so it runs before the main forward chain (priority 0). policy=drop with
        # explicit oif/iif wg1 accepts means LAN→eth0 leaks are dropped at the
        # earliest hook. Tunnel-mode profiles pre-install this chain; arm/disarm
        # tears it down and rebuilds idempotently.
        self.run_sudo(["nft", "delete", "chain", "inet", "filter", "killswitch"], timeout=5)
        out, err, rc = self.run_sudo([
            "nft", "add", "chain", "inet", "filter", "killswitch",
            "{ type filter hook forward priority -10; policy drop; }"
        ], timeout=5)
        if rc != 0:
            return ("error", f"Failed to create chain: {err}")
        out, err, rc = self.run_sudo([
            "nft", "add", "rule", "inet", "filter", "killswitch",
            "oifname", "wg1", "accept"
        ], timeout=5)
        if rc != 0:
            return ("error", f"Failed to add outbound rule: {err}")
        out, err, rc = self.run_sudo([
            "nft", "add", "rule", "inet", "filter", "killswitch",
            "iifname", "wg1", "ct", "state", "established,related", "accept"
        ], timeout=5)
        if rc != 0:
            return ("error", f"Failed to add inbound rule: {err}")
        save_armed_state(True)
        return ("armed", "Kill switch armed")

    def _disarm_killswitch(self):
        out, err, rc = self.run_sudo([
            "nft", "delete", "chain", "inet", "filter", "killswitch"
        ], timeout=5)
        if rc != 0 and "No such" not in err:
            return ("error", f"Failed to remove chain: {err}")
        save_armed_state(False)
        return ("disarmed", "Kill switch disarmed")

    def _on_toggle_done(self, result):
        if isinstance(result, Exception):
            self.set_status(f"Error: {result}")
            return
        state, msg = result
        if state == "error":
            self.set_status(msg)
            return
        self.armed = (state == "armed")
        self._update_armed_ui()
        if self.armed:
            self._auto_arm_box.hide()
            self._auto_arm_suggested = False
        self.set_status(msg)

    def _update_armed_ui(self):
        ctx = self.toggle_btn.get_style_context()
        cctx = self.switch_container.get_style_context()

        if self.armed:
            self.toggle_btn.set_label("ARMED")
            ctx.remove_class("gp-toggle-off")
            ctx.add_class("gp-toggle-on")
            self.state_label.set_text("All traffic stops if VPN drops")
            sctx = self.state_label.get_style_context()
            sctx.remove_class("anchor-state-disarmed")
            sctx.add_class("anchor-state-armed")
            cctx.remove_class("anchor-disarmed")
            cctx.add_class("anchor-armed")
        else:
            self.toggle_btn.set_label("DISARMED")
            ctx.remove_class("gp-toggle-on")
            ctx.add_class("gp-toggle-off")
            self.state_label.set_text("Kill switch is disarmed")
            sctx = self.state_label.get_style_context()
            sctx.remove_class("anchor-state-armed")
            sctx.add_class("anchor-state-disarmed")
            cctx.remove_class("anchor-armed")
            cctx.add_class("anchor-disarmed")

    # ── Tunnel Quality Pings ─────────────────────────────────────────

    def _ping_tunnels_tick(self):
        """Ping tunnel endpoints every 10s (background)."""
        self.run_async(self._do_pings, self._on_pings_done)
        return True

    def _do_pings(self):
        """Ping wg0 and wg1 endpoints."""
        results = {}
        for iface in ("wg0", "wg1"):
            ep = self._tunnel_quality[iface].get("endpoint")
            if ep:
                # Extract host from endpoint (host:port)
                host = ep.split(":")[0] if ":" in ep else ep
                lat, loss = ping_endpoint(host, count=3, timeout=5)
                grade = grade_quality(lat, loss)
                results[iface] = {"latency": lat, "loss": loss, "grade": grade}
            else:
                results[iface] = {"latency": None, "loss": 100.0, "grade": "?"}
        return results

    def _on_pings_done(self, results):
        if isinstance(results, Exception):
            return

        for iface in ("wg0", "wg1"):
            if iface in results:
                self._tunnel_quality[iface]["latency"] = results[iface]["latency"]
                self._tunnel_quality[iface]["loss"] = results[iface]["loss"]
                self._tunnel_quality[iface]["grade"] = results[iface]["grade"]

        # Update quality cards
        self._update_quality_card(self._wg0_quality_card, "wg0")
        self._update_quality_card(self._wg1_quality_card, "wg1")

        # Auto-arm suggestion: if wg1 (data plane) quality is D or F
        wg1_grade = self._tunnel_quality["wg1"]["grade"]
        if wg1_grade in ("D", "F") and not self.armed and not self._auto_arm_suggested:
            self._auto_arm_suggested = True
            self._auto_arm_box.show()
        elif wg1_grade in ("A", "B", "C"):
            self._auto_arm_suggested = False
            self._auto_arm_box.hide()

    def _update_quality_card(self, card, iface):
        q = self._tunnel_quality[iface]
        grade = q["grade"]
        lat = q["latency"]
        loss = q["loss"]

        # Grade label
        card["grade"].set_text(grade)
        grade_cls = f"grade-{grade.lower()}" if grade in "ABCDF" else "gp-dim"
        ctx = card["grade"].get_style_context()
        for cls in ["grade-a", "grade-b", "grade-c", "grade-d", "grade-f", "gp-dim"]:
            ctx.remove_class(cls)
        ctx.add_class(grade_cls)

        # Latency
        if lat is not None:
            card["latency"].set_text(f"Latency: {lat:.1f} ms")
        else:
            card["latency"].set_text("Latency: --")

        # Loss
        if loss < 100:
            card["loss"].set_text(f"Loss: {loss:.0f}%")
        else:
            card["loss"].set_text("Loss: --")

    # ── Polling / Refresh ───────────────────────────────────────────

    def refresh_all(self):
        self.run_async(self._fetch_status, self._apply_status)
        return True

    def _fetch_status(self):
        data = {}

        out, err, rc = self.run_sudo(["nft", "list", "chain", "inet", "filter", "killswitch"], timeout=5)
        data["nft_armed"] = (rc == 0)

        data["mode"] = read_current_mode()

        for iface in ("wg0", "wg1", "tailscale0"):
            out, err, rc = self.run_cmd(["ip", "-br", "link", "show", iface], timeout=5)
            if rc == 0 and out:
                parts = out.split()
                data[f"{iface}_up"] = (len(parts) >= 2 and parts[1] == "UP")
            else:
                data[f"{iface}_up"] = False

        out, err, rc = self.run_sudo(["wg", "show", "wg0"], timeout=5)
        data["wg0_details"] = self._parse_wg_show(out) if rc == 0 else None

        out, err, rc = self.run_sudo(["wg", "show", "wg1"], timeout=5)
        data["wg1_details"] = self._parse_wg_show(out) if rc == 0 else None

        out, err, rc = self.run_cmd(["tailscale", "ip", "-4"], timeout=5)
        data["ts_ip"] = out.strip() if rc == 0 and out.strip() else None

        return data

    def _parse_wg_show(self, text):
        if not text:
            return None
        info = {"endpoint": None, "handshake": None, "transfer": None}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("endpoint:"):
                info["endpoint"] = line.split(":", 1)[1].strip()
            elif line.startswith("latest handshake:"):
                info["handshake"] = line.split(":", 1)[1].strip()
            elif line.startswith("transfer:"):
                info["transfer"] = line.split(":", 1)[1].strip()
        return info

    def _apply_status(self, data):
        if isinstance(data, Exception):
            self.set_status(f"Poll error: {data}")
            return

        self.armed = data.get("nft_armed", False)
        save_armed_state(self.armed)
        self._update_armed_ui()

        mode = data.get("mode", "unknown")
        mode_display = {
            "isp": "ISP",
            "zerotrust": "ZeroTrust",
            "doublehop": "DoubleHop",
            "zhop": "ZHop",
        }.get(mode, mode.upper())
        self.mode_label.set_text(mode_display)

        # Store endpoints for ping
        for iface in ("wg0", "wg1"):
            details = data.get(f"{iface}_details")
            if details and details.get("endpoint"):
                self._tunnel_quality[iface]["endpoint"] = details["endpoint"]

        # Update tunnel cards
        self._update_tunnel_card(
            self.wg0_card,
            up=data.get("wg0_up", False),
            details=data.get("wg0_details"),
            show_transfer=False,
        )

        self._update_tunnel_card(
            self.wg1_card,
            up=data.get("wg1_up", False),
            details=data.get("wg1_details"),
            show_transfer=True,
        )

        ts_up = data.get("tailscale0_up", False)
        ts_card = self.ts_card
        if ts_up:
            ts_card["status"].set_text("UP")
            ctx = ts_card["status"].get_style_context()
            ctx.remove_class("tunnel-down")
            ctx.add_class("tunnel-up")
        else:
            ts_card["status"].set_text("DOWN")
            ctx = ts_card["status"].get_style_context()
            ctx.remove_class("tunnel-up")
            ctx.add_class("tunnel-down")

        ts_ip = data.get("ts_ip")
        ts_card["detail1"].set_text(f"IP: {ts_ip}" if ts_ip else "IP: --")
        ts_card["detail2"].set_text("")
        ts_card["detail3"].set_text("")

        # Update uptime history
        for iface in ("wg0", "wg1", "tailscale0"):
            up = data.get(f"{iface}_up", False)
            hist = self._uptime_history[iface]
            hist.append(up)
            if len(hist) > MAX_UPTIME_SAMPLES:
                hist.pop(0)

        # Redraw sparklines
        for child in self._sparkline_box.get_children():
            for widget in child.get_children():
                if isinstance(widget, Gtk.DrawingArea):
                    widget.queue_draw()

        # Status bar
        uptime = int(time.time() - self.start_time)
        mins, secs = divmod(uptime, 60)
        armed_str = "ARMED" if self.armed else "DISARMED"
        wg1_grade = self._tunnel_quality["wg1"]["grade"]
        self.set_status(
            f"Kill switch: {armed_str}  |  wg1 quality: {wg1_grade}  |  Uptime: {mins}m {secs}s"
        )

    def _update_tunnel_card(self, card, up, details, show_transfer=False):
        if up:
            card["status"].set_text("UP")
            ctx = card["status"].get_style_context()
            ctx.remove_class("tunnel-down")
            ctx.add_class("tunnel-up")
        else:
            card["status"].set_text("DOWN")
            ctx = card["status"].get_style_context()
            ctx.remove_class("tunnel-up")
            ctx.add_class("tunnel-down")

        if details:
            ep = details.get("endpoint")
            card["detail1"].set_text(f"Endpoint: {ep}" if ep else "Endpoint: --")

            hs = details.get("handshake")
            card["detail2"].set_text(f"Handshake: {humanize_handshake(hs)}")

            if show_transfer:
                tx = details.get("transfer")
                card["detail3"].set_text(f"Transfer: {format_transfer(tx)}")
            else:
                card["detail3"].set_text("")
        else:
            card["detail1"].set_text("No tunnel configured")
            card["detail2"].set_text("")
            card["detail3"].set_text("")

    def _build_extra_css(self):
        c = self.colors
        return f"""
.anchor-armed {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.12);
    border: 2px solid {c['accent']};
    border-radius: 12px;
    padding: 16px;
}}
.anchor-disarmed {{
    background-color: rgba(128, 128, 128, 0.06);
    border: 2px solid rgba(128, 128, 128, 0.3);
    border-radius: 12px;
    padding: 16px;
}}
.anchor-toggle {{
    font-size: 18px;
    font-weight: bold;
    font-family: monospace;
    padding: 16px 48px;
    min-height: 50px;
    border-radius: 10px;
}}
.anchor-state-armed {{
    color: {c['accent']};
    font-family: monospace;
    font-size: 13px;
    font-weight: bold;
}}
.anchor-state-disarmed {{
    color: rgba(128, 128, 128, 0.7);
    font-family: monospace;
    font-size: 13px;
}}
.tunnel-up {{
    color: {c['success']};
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}}
.tunnel-down {{
    color: {c['danger']};
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}}
.mode-label {{
    color: {c['accent']};
    font-family: monospace;
    font-size: 14px;
    font-weight: bold;
}}
.grade-a {{ color: {c['success']}; font-family: monospace; font-weight: bold; font-size: 18px; }}
.grade-b {{ color: {c['accent']}; font-family: monospace; font-weight: bold; font-size: 18px; }}
.grade-c {{ color: {c['warning']}; font-family: monospace; font-weight: bold; font-size: 18px; }}
.grade-d {{ color: {c['danger']}; font-family: monospace; font-weight: bold; font-size: 18px; }}
.grade-f {{ color: {c['danger']}; font-family: monospace; font-weight: bold; font-size: 18px; }}
"""

    def on_theme_changed(self):
        # Re-apply CSS with new colors
        self._apply_css(self._build_extra_css())
        # Redraw sparklines on theme change
        for child in self._sparkline_box.get_children():
            for widget in child.get_children():
                if isinstance(widget, Gtk.DrawingArea):
                    widget.queue_draw()


# ── Entry Point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    app = AnchorApp()
    app.run()
