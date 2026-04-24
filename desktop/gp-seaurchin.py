#!/usr/bin/env python3
"""SEA URCHIN — System Health Dashboard for Phantom OS."""

import sys
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

import os
import re
import math
import shutil
import cairo

def _hex_to_rgb(hex_str):
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


SERVICES = [
    ("ghostport", "GhostPort Server"),
    ("ghostport-sni", "SNI Inspector"),
    ("ghostport-dns-guard", "DNS Guard"),
    ("ghostport-health-guard", "Health Guard"),
]

IFACE_LIST = ["eth0", "wlan0", "wg0", "wg1", "tailscale0"]


class SeaUrchinApp(GhostPortApp):
    def __init__(self):
        super().__init__("SEA URCHIN", "seaurchin", (800, 700))
        self._prev_cpu = None
        self._prev_iface_stats = {}
        self._iface_labels = {}
        self._proc_labels = []
        # Loading-animation state: each gauge starts in `_loading=True`
        # until _update_all populates real values. _anim_phase advances on
        # a 80ms tick and drives a bouncing arc + dot-chaser on any gauge
        # that's still in loading.
        self._anim_phase = 0
        self.build_ui()
        self._update_all()
        self.poll_start(5, self._poll)
        # Animation tick — ~12 fps is enough for the bouncing arc to feel
        # smooth without burning CPU (tick itself is a handful of µs).
        # Registered with self._timers so the base class cleans it up on
        # _on_destroy alongside the other polling timers.
        anim_id = GLib.timeout_add(80, self._tick_animation)
        self._timers.append(anim_id)

    def build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Header
        vbox.pack_start(self.make_header("SEA URCHIN", "System Health"), False, False, 0)

        # Main scrolled area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        # Gauge grid (2 columns x 4 rows = 8 gauges)
        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(8)
        grid.set_column_homogeneous(True)

        self._gauges = {}
        gauge_defs = [
            ("cpu", "CPU USAGE", "%"),
            ("temp", "CPU TEMP", "C"),
            ("mem", "MEMORY", "%"),
            ("disk", "DISK", "%"),
            ("uptime", "UPTIME", ""),
            ("load", "LOAD AVG", ""),
            ("latency", "LATENCY", "ms"),
            ("conntrack", "CONNECTIONS", ""),
        ]

        for i, (key, title, unit) in enumerate(gauge_defs):
            card = self._make_gauge_card(key, title, unit)
            grid.attach(card, i % 2, i // 2, 1, 1)

        content.pack_start(grid, False, False, 0)

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(sep, False, False, 4)

        # Services section
        svc_title = self.make_label("SERVICES", "gp-accent")
        svc_title.set_margin_top(4)
        content.pack_start(svc_title, False, False, 0)

        self._svc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._svc_labels = {}
        for svc_name, svc_display in SERVICES:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.get_style_context().add_class("gp-card")
            dot = Gtk.Label(label="●")
            dot.get_style_context().add_class("gp-dim")
            row.pack_start(dot, False, False, 0)
            name_lbl = self.make_label(svc_display, "gp-text")
            row.pack_start(name_lbl, True, True, 0)
            status_lbl = self.make_label("checking...", "gp-dim")
            row.pack_end(status_lbl, False, False, 0)
            self._svc_labels[svc_name] = (dot, status_lbl)
            self._svc_box.pack_start(row, False, False, 0)

        content.pack_start(self._svc_box, False, False, 0)

        # Separator
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(sep2, False, False, 4)

        # Interface Health section
        iface_title = self.make_label("INTERFACE HEALTH", "gp-accent")
        iface_title.set_margin_top(4)
        content.pack_start(iface_title, False, False, 0)

        self._iface_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for iface in IFACE_LIST:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.get_style_context().add_class("gp-card")
            name_lbl = self.make_label(f"{iface:12s}", "gp-text")
            row.pack_start(name_lbl, False, False, 0)
            rx_lbl = self.make_label("RX err: --", "gp-dim")
            row.pack_start(rx_lbl, True, True, 0)
            tx_lbl = self.make_label("TX err: --", "gp-dim")
            row.pack_start(tx_lbl, True, True, 0)
            delta_lbl = self.make_label("", "gp-dim")
            row.pack_end(delta_lbl, False, False, 0)
            self._iface_labels[iface] = (name_lbl, rx_lbl, tx_lbl, delta_lbl)
            self._iface_box.pack_start(row, False, False, 0)

        content.pack_start(self._iface_box, False, False, 0)

        # Separator
        sep3 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(sep3, False, False, 4)

        # Top Processes section
        proc_title = self.make_label("TOP PROCESSES", "gp-accent")
        proc_title.set_margin_top(4)
        content.pack_start(proc_title, False, False, 0)

        self._proc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # Header row
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hdr.pack_start(self.make_label(f"{'PID':>7s}  {'%CPU':>5s}  {'%MEM':>5s}  COMMAND", "gp-dim"), True, True, 0)
        self._proc_box.pack_start(hdr, False, False, 0)
        # 5 process rows
        for _ in range(5):
            lbl = self.make_label("", "gp-text")
            self._proc_labels.append(lbl)
            self._proc_box.pack_start(lbl, False, False, 0)

        content.pack_start(self._proc_box, False, False, 0)

        scrolled.add(content)
        vbox.pack_start(scrolled, True, True, 0)

        # Status bar
        self._status_bar = self.make_status_bar("Monitoring system health...")
        vbox.pack_start(self._status_bar, False, False, 0)

    def _make_gauge_card(self, key, title, unit):
        """Create a gauge card with an arc drawing area."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.get_style_context().add_class("gp-card")

        title_lbl = self.make_label(title, "gp-accent")
        title_lbl.set_halign(Gtk.Align.CENTER)
        card.pack_start(title_lbl, False, False, 0)

        # Drawing area for arc gauge
        da = Gtk.DrawingArea()
        da.set_size_request(120, 80)
        da._value = 0.0
        da._unit = unit
        da._display = "0"
        # Loading flag — flipped to False in _update_all once real data
        # populates this gauge. Drives the sweeping-arc animation below.
        da._loading = True
        da.connect("draw", self._draw_gauge)
        card.pack_start(da, False, False, 0)

        # Value label beneath
        val_lbl = self.make_label("--", "gp-text")
        val_lbl.set_halign(Gtk.Align.CENTER)
        card.pack_start(val_lbl, False, False, 0)

        self._gauges[key] = {"da": da, "val_lbl": val_lbl, "title": title}
        return card

    def _draw_gauge(self, da, cr):
        """Draw an arc gauge on a DrawingArea. Switches to a sweeping
        loading animation when the gauge has no real data yet."""
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        cx, cy = w / 2, h - 10
        radius = min(w / 2 - 10, h - 15)
        value = da._value  # 0.0 to 100.0

        c = self.colors
        r, g, b = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0

        # Background arc (always drawn, sits behind everything else)
        cr.set_line_width(8)
        cr.set_source_rgba(r, g, b, 0.15)
        cr.arc(cx, cy, radius, math.pi, 2 * math.pi)
        cr.stroke()

        if getattr(da, "_loading", False):
            # ── Loading animation — bouncing arc segment along the
            # semicircle, plus an animated "..." in the center. Phase is
            # advanced by _tick_animation; this function just paints
            # whichever phase is current.
            phase = self._anim_phase
            # Smooth bounce via sine: 40-tick period = 3.2s round trip
            t = (math.sin(phase * 2 * math.pi / 40) + 1) / 2  # 0..1
            seg_width = math.pi * 0.28  # ~50° segment
            sweep_center = math.pi + t * math.pi  # travels across semicircle
            start_angle = max(math.pi, sweep_center - seg_width / 2)
            end_angle = min(2 * math.pi, sweep_center + seg_width / 2)

            cr.set_line_width(8)
            cr.set_source_rgba(r, g, b, 0.75)
            cr.arc(cx, cy, radius, start_angle, end_angle)
            cr.stroke()

            # Animated ellipsis at the center
            dot_count = (phase // 5) % 4  # 0..3, ticks ~400ms per step
            dots = "." * dot_count
            cr.set_source_rgba(r, g, b, 0.5)
            cr.select_font_face("monospace",
                                cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(18)
            extents = cr.text_extents(dots if dots else ".")
            cr.move_to(cx - extents.width / 2, cy - 5)
            cr.show_text(dots)
            return  # loading view replaces the value view

        # Value arc
        if value > 0:
            # Color based on value
            if value >= 85:
                dr, dg, db = _hex_to_rgb(c["danger"]); cr.set_source_rgba(dr, dg, db, 0.9)
            elif value >= 60:
                wr, wg, wb = _hex_to_rgb(c["warning"]); cr.set_source_rgba(wr, wg, wb, 0.9)
            else:
                sr, sg, sb = _hex_to_rgb(c["success"]); cr.set_source_rgba(sr, sg, sb, 0.9)

            sweep = math.pi * (value / 100.0)
            cr.arc(cx, cy, radius, math.pi, math.pi + sweep)
            cr.stroke()

        # Center text
        cr.set_source_rgba(r, g, b, 0.9)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(18)
        text = da._display
        extents = cr.text_extents(text)
        cr.move_to(cx - extents.width / 2, cy - 5)
        cr.show_text(text)

    def _tick_animation(self):
        """Advance the loading animation phase and redraw any gauge that
        is still in _loading state. Returns True to keep the timer alive.
        Exits early (no queue_draw) when nothing is loading, so the cost
        is ~0 after first successful data poll."""
        self._anim_phase = (self._anim_phase + 1) & 0x3FFFFFFF
        for g in self._gauges.values():
            da = g["da"]
            if getattr(da, "_loading", False):
                da.queue_draw()
        return True

    # ── Data Readers ──────────────────────────────────────────────────

    def _read_cpu_usage(self):
        """Read CPU usage from /proc/stat."""
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            vals = [int(x) for x in parts[1:9]]
            idle = vals[3] + vals[4]
            total = sum(vals)

            if self._prev_cpu is not None:
                prev_idle, prev_total = self._prev_cpu
                d_idle = idle - prev_idle
                d_total = total - prev_total
                if d_total > 0:
                    usage = 100.0 * (1.0 - d_idle / d_total)
                else:
                    usage = 0.0
            else:
                usage = 0.0

            self._prev_cpu = (idle, total)
            return max(0.0, min(100.0, usage))
        except Exception:
            return 0.0

    def _read_temp(self):
        """Read CPU temperature."""
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read().strip()) / 1000.0
        except Exception:
            return 0.0

    def _read_memory(self):
        """Read memory usage from /proc/meminfo."""
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 1)
            avail = info.get("MemAvailable", 0)
            used = total - avail
            pct = (used / total) * 100.0 if total > 0 else 0.0
            used_mb = used / 1024
            total_mb = total / 1024
            return pct, used_mb, total_mb
        except Exception:
            return 0.0, 0, 0

    def _read_disk(self):
        """Read disk usage."""
        try:
            usage = shutil.disk_usage("/")
            pct = (usage.used / usage.total) * 100.0 if usage.total > 0 else 0.0
            used_gb = usage.used / (1024**3)
            total_gb = usage.total / (1024**3)
            return pct, used_gb, total_gb
        except Exception:
            return 0.0, 0, 0

    def _read_uptime(self):
        """Read system uptime."""
        try:
            with open("/proc/uptime") as f:
                secs = float(f.read().split()[0])
            days = int(secs // 86400)
            hours = int((secs % 86400) // 3600)
            mins = int((secs % 3600) // 60)
            if days > 0:
                return f"{days}d {hours}h {mins}m", 0.0
            elif hours > 0:
                return f"{hours}h {mins}m", 0.0
            else:
                return f"{mins}m", 0.0
        except Exception:
            return "?", 0.0

    def _read_load(self):
        """Read load average."""
        try:
            load1, load5, load15 = os.getloadavg()
            try:
                cpus = os.cpu_count() or 4
            except Exception:
                cpus = 4
            pct = (load1 / cpus) * 100.0
            return load1, load5, load15, min(100.0, pct)
        except Exception:
            return 0, 0, 0, 0.0

    def _read_latency(self):
        """Ping default gateway (and tunnel endpoint in tunnel modes).
        Returns (avg_ms, jitter_ms, success).
        In doublehop/zhop, also pings 10.66.67.1 and reports the worse latency."""
        def _ping_host(host):
            """Send 3 pings, return (avg_ms, jitter_ms, success)."""
            stdout, _, rc = self.run_cmd(["ping", "-c", "3", "-W", "2", host], timeout=10)
            if rc != 0:
                return 0.0, 0.0, False
            # Parse all RTT values from "time=X.XX ms" lines
            rtts = [float(m.group(1)) for m in re.finditer(r'time[=<](\d+\.?\d*)', stdout)]
            if not rtts:
                return 0.0, 0.0, False
            avg = sum(rtts) / len(rtts)
            jitter = max(rtts) - min(rtts) if len(rtts) > 1 else 0.0
            return avg, jitter, True

        try:
            # Ping default gateway
            stdout, _, rc = self.run_cmd(["ip", "route", "show", "default"], timeout=3)
            if rc != 0 or not stdout:
                return 0.0, 0.0, False
            m = re.search(r'via\s+(\S+)', stdout)
            if not m:
                return 0.0, 0.0, False
            gw = m.group(1)
            gw_avg, gw_jitter, gw_ok = _ping_host(gw)

            # Check current mode for tunnel awareness
            mode = ""
            try:
                with open("/etc/phantom/current-mode") as f:
                    mode = f.read().strip()
            except Exception:
                pass

            if mode in ("doublehop", "zhop"):
                tun_avg, tun_jitter, tun_ok = _ping_host("10.66.67.1")
                if tun_ok and gw_ok:
                    # Return the worse (higher) latency of the two
                    if tun_avg > gw_avg:
                        return tun_avg, tun_jitter, True
                    return gw_avg, gw_jitter, True
                elif tun_ok:
                    return tun_avg, tun_jitter, True
                elif gw_ok:
                    return gw_avg, gw_jitter, True
                return 0.0, 0.0, False

            return gw_avg, gw_jitter, gw_ok
        except Exception:
            return 0.0, 0.0, False

    def _read_conntrack(self):
        """Read conntrack entry count."""
        try:
            stdout, _, rc = self.run_sudo(["conntrack", "-C"], timeout=5)
            if rc == 0 and stdout:
                return int(stdout.strip())
        except Exception:
            pass
        return 0

    def _read_iface_stats(self):
        """Read interface error/drop counts and packet totals from /proc/net/dev.
        Returns dict: iface -> (rx_err_drops, tx_err_drops, rx_packets, tx_packets, error_rate_pct)."""
        stats = {}
        try:
            with open("/proc/net/dev") as f:
                lines = f.readlines()
            for line in lines[2:]:
                parts = line.split()
                if len(parts) < 17:
                    continue
                iface = parts[0].rstrip(":")
                if iface in IFACE_LIST:
                    # /proc/net/dev columns: bytes packets errs drop fifo frame compressed multicast
                    rx_packets = int(parts[2])
                    rx_errs = int(parts[3])
                    rx_drops = int(parts[4])
                    tx_packets = int(parts[10])
                    tx_errs = int(parts[11])
                    tx_drops = int(parts[12])
                    total_errors = (rx_errs + rx_drops + tx_errs + tx_drops)
                    total_packets = rx_packets + tx_packets
                    error_rate = (total_errors / max(total_packets, 1)) * 100.0
                    stats[iface] = (rx_errs + rx_drops, tx_errs + tx_drops,
                                    rx_packets, tx_packets, error_rate)
        except Exception:
            pass
        return stats

    def _read_top_processes(self):
        """Read top 5 CPU-consuming processes."""
        try:
            stdout, _, rc = self.run_cmd(
                ["ps", "aux", "--sort=-%cpu"], timeout=5
            )
            if rc != 0 or not stdout:
                return []
            lines = stdout.strip().split("\n")
            results = []
            for line in lines[1:6]:  # skip header, take top 5
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    pid = parts[1]
                    cpu = parts[2]
                    mem = parts[3]
                    cmd = parts[10][:35]
                    results.append((pid, cpu, mem, cmd))
            return results
        except Exception:
            return []

    def _check_service(self, name):
        """Check if a systemd service is active."""
        _, _, rc = self.run_cmd(["systemctl", "is-active", "--quiet", name])
        return rc == 0

    # ── Update ────────────────────────────────────────────────────────

    def _update_all(self):
        """Update all gauges, services, interfaces, and processes."""
        # CPU
        cpu = self._read_cpu_usage()
        g = self._gauges["cpu"]
        g["da"]._value = cpu
        g["da"]._display = f"{cpu:.0f}%"
        g["da"]._loading = False
        g["val_lbl"].set_text(f"{cpu:.1f}%")
        g["da"].queue_draw()

        # Temperature
        temp = self._read_temp()
        g = self._gauges["temp"]
        temp_pct = max(0, min(100, (temp - 30) / 60 * 100))
        g["da"]._value = temp_pct
        g["da"]._display = f"{temp:.0f}C"
        g["da"]._loading = False
        warn = " (WARNING)" if temp >= 70 else ""
        g["val_lbl"].set_text(f"{temp:.1f}C{warn}")
        if temp >= 70:
            g["val_lbl"].get_style_context().remove_class("gp-text")
            g["val_lbl"].get_style_context().add_class("gp-danger")
        else:
            g["val_lbl"].get_style_context().remove_class("gp-danger")
            g["val_lbl"].get_style_context().add_class("gp-text")
        g["da"].queue_draw()

        # Memory
        mem_pct, mem_used, mem_total = self._read_memory()
        g = self._gauges["mem"]
        g["da"]._value = mem_pct
        g["da"]._display = f"{mem_pct:.0f}%"
        g["da"]._loading = False
        g["val_lbl"].set_text(f"{mem_used:.0f} / {mem_total:.0f} MB")
        g["da"].queue_draw()

        # Disk
        disk_pct, disk_used, disk_total = self._read_disk()
        g = self._gauges["disk"]
        g["da"]._value = disk_pct
        g["da"]._display = f"{disk_pct:.0f}%"
        g["da"]._loading = False
        g["val_lbl"].set_text(f"{disk_used:.1f} / {disk_total:.1f} GB")
        g["da"].queue_draw()

        # Uptime
        uptime_str, _ = self._read_uptime()
        g = self._gauges["uptime"]
        g["da"]._value = 0.0
        g["da"]._display = uptime_str
        g["da"]._loading = False
        g["val_lbl"].set_text(uptime_str)
        g["da"].queue_draw()

        # Load
        load1, load5, load15, load_pct = self._read_load()
        g = self._gauges["load"]
        g["da"]._value = load_pct
        g["da"]._display = f"{load1:.1f}"
        g["da"]._loading = False
        g["val_lbl"].set_text(f"{load1:.2f} / {load5:.2f} / {load15:.2f}")
        g["da"].queue_draw()

        # Latency — loading when upstream is unreachable (that's genuine
        # "no data flowing", not just a slow reading).
        latency_ms, jitter_ms, lat_ok = self._read_latency()
        g = self._gauges["latency"]
        if lat_ok:
            lat_pct = max(0, min(100, latency_ms / 175.0 * 100.0))  # 150ms ≈ 86% → red
            g["da"]._value = lat_pct
            g["da"]._display = f"{latency_ms:.0f}ms \u00b1{jitter_ms:.0f}ms"
            g["da"]._loading = False
            g["val_lbl"].set_text(f"{latency_ms:.1f} ms \u00b1{jitter_ms:.1f} ms")
        else:
            g["da"]._value = 0.0
            g["da"]._display = ""
            g["da"]._loading = True
            g["val_lbl"].set_text("waiting for upstream...")
        g["da"].queue_draw()

        # Conntrack
        ct_count = self._read_conntrack()
        g = self._gauges["conntrack"]
        ct_pct = max(0, min(100, ct_count / 10000.0 * 100.0))
        g["da"]._value = ct_pct
        g["da"]._display = str(ct_count)
        g["da"]._loading = False
        g["val_lbl"].set_text(f"{ct_count} active flows")
        g["da"].queue_draw()

        # Services
        for svc_name, svc_display in SERVICES:
            dot, status_lbl = self._svc_labels[svc_name]
            active = self._check_service(svc_name)
            if active:
                dot.set_text("●")
                dot.get_style_context().remove_class("gp-dim")
                dot.get_style_context().remove_class("gp-danger")
                dot.get_style_context().add_class("gp-success")
                status_lbl.set_text("active")
                status_lbl.get_style_context().remove_class("gp-danger")
                status_lbl.get_style_context().add_class("gp-success")
            else:
                dot.set_text("●")
                dot.get_style_context().remove_class("gp-dim")
                dot.get_style_context().remove_class("gp-success")
                dot.get_style_context().add_class("gp-danger")
                status_lbl.set_text("inactive")
                status_lbl.get_style_context().remove_class("gp-success")
                status_lbl.get_style_context().add_class("gp-danger")

        # Interface Health
        iface_stats = self._read_iface_stats()
        for iface in IFACE_LIST:
            if iface not in self._iface_labels:
                continue
            name_lbl, rx_lbl, tx_lbl, delta_lbl = self._iface_labels[iface]
            if iface in iface_stats:
                rx_total, tx_total, rx_pkts, tx_pkts, error_rate = iface_stats[iface]
                rate_str = f" ({error_rate:.2f}%)" if error_rate > 0 else ""
                rx_lbl.set_text(f"RX err: {rx_total}{rate_str}")
                tx_lbl.set_text(f"TX err: {tx_total}")
                # Calculate delta from previous poll
                prev = self._prev_iface_stats.get(iface, (0, 0, 0, 0, 0.0))
                delta_rx = rx_total - prev[0]
                delta_tx = tx_total - prev[1]
                total_errs = rx_total + tx_total
                # Color based on error rate: >0.1% is danger, any errors is warning
                if error_rate > 0.1:
                    css_class = "gp-danger"
                elif total_errs > 0:
                    css_class = "gp-warning"
                else:
                    css_class = "gp-success"
                for lbl in (rx_lbl, tx_lbl):
                    for cls in ("gp-dim", "gp-success", "gp-warning", "gp-danger"):
                        lbl.get_style_context().remove_class(cls)
                    lbl.get_style_context().add_class(css_class)
                if delta_rx + delta_tx > 0:
                    delta_lbl.set_text(f"+{delta_rx + delta_tx}")
                    delta_lbl.get_style_context().remove_class("gp-dim")
                    delta_lbl.get_style_context().add_class("gp-danger")
                else:
                    delta_lbl.set_text("")
                    delta_lbl.get_style_context().remove_class("gp-danger")
                    delta_lbl.get_style_context().add_class("gp-dim")
            else:
                rx_lbl.set_text("RX err: --")
                tx_lbl.set_text("TX err: --")
                delta_lbl.set_text("(down)")
                for lbl in (rx_lbl, tx_lbl, delta_lbl):
                    for cls in ("gp-success", "gp-warning", "gp-danger"):
                        lbl.get_style_context().remove_class(cls)
                    lbl.get_style_context().add_class("gp-dim")

        self._prev_iface_stats = iface_stats

        # Top Processes
        procs = self._read_top_processes()
        for i, lbl in enumerate(self._proc_labels):
            if i < len(procs):
                pid, cpu_pct, mem_pct, cmd = procs[i]
                lbl.set_text(f"{pid:>7s}  {cpu_pct:>5s}  {mem_pct:>5s}  {cmd}")
            else:
                lbl.set_text("")

        self.set_status(f"Last updated: {self._now()}")

    def _now(self):
        """Current time string."""
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def _poll(self):
        """Poll callback."""
        self._update_all()
        return True

    def on_theme_changed(self):
        """Redraw gauges on theme change."""
        for g in self._gauges.values():
            g["da"].queue_draw()


if __name__ == "__main__":
    app = SeaUrchinApp()
    app.run()
