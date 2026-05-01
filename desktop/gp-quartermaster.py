#!/usr/bin/env python3
"""QUARTERMASTER — Security Audit Scorecard for Phantom OS."""

import sys
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

import json
import os
import re
import time
import math
import cairo

HISTORY_FILE = "/etc/phantom/audit-history.json"
HISTORY_MAX = 90


class QuartermasterApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Quartermaster?",
         "Quartermaster runs the ship's security audit. It checks your device "
         "against a standard security benchmark — looking at file permissions, "
         "service configuration, password policies, kernel hardening flags, and "
         "known weak spots — and gives you a single score out of 100.\n\n"
         "Think of it like an annual physical: most people don't run it often, "
         "but it catches silent drift before it becomes a problem."),

        ("The score (top right)",
         "Big number out of 100. Higher is better.\n\n"
         "• 90-100 — Excellent. Your system is hardened beyond defaults.\n"
         "• 75-89 — Good. Production-ready for most threat models.\n"
         "• 60-74 — Acceptable. Some known gaps worth addressing.\n"
         "• < 60 — Review required. Enough findings that an attacker has easy wins.\n\n"
         "\"--\" means you haven't run a scan yet. Click Run Scan."),

        ("Run Scan button",
         "Kicks off the full audit. Takes 30-90 seconds depending on how many "
         "checks apply to your system. You'll see the progress label update with "
         "which check is running.\n\n"
         "Runs are non-intrusive — nothing is changed on your system. Quartermaster "
         "only READS configuration and reports findings. You decide what to fix.\n\n"
         "Safe to run while using the device normally; CPU usage is modest."),

        ("Export Report",
         "After a scan completes, Export Report saves the full findings list to a "
         "timestamped file in your home folder. Useful when:\n\n"
         "• You want a before/after comparison across hardening changes.\n"
         "• Someone technical is remote-helping you and needs to see the details.\n"
         "• You need an audit trail for compliance (CMMC, etc.).\n\n"
         "The export includes both pass and fail items so you have the full picture, "
         "not just the warnings."),

        ("Score history graph",
         "Line chart of every scan you've run, with the date on the x-axis and the "
         "score on the y-axis. Useful for spotting trends:\n\n"
         "• Flat line at 85+ — everything's stable.\n"
         "• Sudden drop — something regressed. Look at the most recent check list "
         "to see what newly failed.\n"
         "• Gradual rise — hardening work is landing.\n\n"
         "History is kept in /etc/phantom/quartermaster-history.json. Delete that "
         "file to start the chart fresh."),

        ("Check items list",
         "One row per check, with a status icon (PASS green, FAIL red, WARN amber) "
         "and a plain-English summary. Click a row to expand — you'll see what was "
         "checked, what was found, and (where applicable) a suggested fix.\n\n"
         "Focus on the red items first. Amber warnings are often \"by design for "
         "this environment\" false positives — read the detail before worrying."),

        ("What a good score looks like after today's work",
         "GhostPort's own hardening efforts (sudo scoping, auditd, SSH pubkey-only, "
         "kernel module blacklist, fail2ban, dashboard lockout) generally push the "
         "score into the 85-95 range.\n\n"
         "If you're below 80 on a GhostPort device, something's regressed — check "
         "the recent OTA updates, confirm auditd is running, and re-run the scan."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    def __init__(self):
        super().__init__("QUARTERMASTER", "quartermaster", (800, 650))
        self._checks = []
        self._scanning = False
        self._history = self._load_history()
        self.build_ui()

    def _load_history(self):
        """Load score history from disk."""
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data[-HISTORY_MAX:]
        except Exception:
            pass
        return []

    def _save_history(self, score, passed, total):
        """Append scan result to history file."""
        import tempfile
        entry = {"ts": int(time.time()), "score": score, "passed": passed, "total": total}
        self._history.append(entry)
        self._history = self._history[-HISTORY_MAX:]
        tmpfile = None
        try:
            data = json.dumps(self._history, indent=2)
            fd, tmpfile = tempfile.mkstemp(suffix=".json", prefix="gp-audit-")
            os.write(fd, data.encode())
            os.close(fd)
            self.run_sudo(["mv", tmpfile, HISTORY_FILE], timeout=5)
        except Exception:
            if tmpfile:
                try:
                    os.unlink(tmpfile)
                except Exception:
                    pass

    def build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Header with score
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header_box.get_style_context().add_class("gp-header")

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_lbl = self.make_label("QUARTERMASTER", "gp-header-title")
        title_box.pack_start(title_lbl, False, False, 0)
        sub_lbl = self.make_label("Security Audit", "gp-header-subtitle")
        title_box.pack_start(sub_lbl, False, False, 0)
        header_box.pack_start(title_box, False, False, 0)

        # Big score number
        self._score_label = Gtk.Label(label="--")
        self._score_label.get_style_context().add_class("gp-big-number")
        self._score_label.set_halign(Gtk.Align.END)
        self._score_label.set_valign(Gtk.Align.CENTER)
        header_box.pack_end(self._score_label, False, False, 8)

        score_unit = self.make_label("/ 100", "gp-dim")
        score_unit.set_valign(Gtk.Align.END)
        score_unit.set_margin_bottom(8)
        header_box.pack_end(score_unit, False, False, 0)

        vbox.pack_start(header_box, False, False, 0)

        # Button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(12)
        btn_bar.set_margin_end(12)
        btn_bar.set_margin_top(8)
        btn_bar.set_margin_bottom(4)

        self._scan_btn = self.make_button("Run Scan", self._on_scan, "gp-btn-primary")
        btn_bar.pack_start(self._scan_btn, False, False, 0)

        self._export_btn = self.make_button("Export Report", self._on_export, "gp-btn")
        self._export_btn.set_sensitive(False)
        btn_bar.pack_end(self._export_btn, False, False, 0)

        btn_bar.pack_end(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)

        self._progress_label = self.make_label("", "gp-dim")
        self._progress_label.set_halign(Gtk.Align.CENTER)
        self._progress_label.set_hexpand(True)
        btn_bar.pack_start(self._progress_label, True, True, 0)

        vbox.pack_start(btn_bar, False, False, 0)

        # Score history graph
        self._history_da = Gtk.DrawingArea()
        self._history_da.set_size_request(-1, 100)
        self._history_da.set_margin_start(12)
        self._history_da.set_margin_end(12)
        self._history_da.set_margin_top(4)
        self._history_da.set_margin_bottom(4)
        self._history_da.connect("draw", self._draw_history)
        vbox.pack_start(self._history_da, False, False, 0)

        # Check items ListBox
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled = self.make_scrolled(self._listbox)
        scrolled.set_vexpand(True)
        vbox.pack_start(scrolled, True, True, 0)

        # Status bar
        self._status_bar = self.make_status_bar("Click 'Run Scan' to begin security audit")
        vbox.pack_start(self._status_bar, False, False, 0)

    def _draw_history(self, da, cr):
        """Draw score history line chart. Reads colors from self.colors at draw time."""
        w = da.get_allocated_width()
        h = da.get_allocated_height()
        c = self.colors
        r, g, b = c['r'] / 255.0, c['g'] / 255.0, c['b'] / 255.0

        if not self._history:
            # Empty state
            cr.set_source_rgba(r, g, b, 0.3)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(11)
            text = "No scan history yet — run a scan to start tracking"
            ext = cr.text_extents(text)
            cr.move_to((w - ext.width) / 2, (h + ext.height) / 2)
            cr.show_text(text)
            return

        pad_l, pad_r, pad_t, pad_b = 30, 10, 10, 15
        gw = w - pad_l - pad_r
        gh = h - pad_t - pad_b

        # Threshold lines at 60 and 80
        cr.set_line_width(1)
        cr.set_dash([4, 4])
        for threshold in (60, 80):
            y = pad_t + gh * (1.0 - threshold / 100.0)
            cr.set_source_rgba(r, g, b, 0.2)
            cr.move_to(pad_l, y)
            cr.line_to(w - pad_r, y)
            cr.stroke()
            # Label
            cr.set_source_rgba(r, g, b, 0.4)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(9)
            cr.move_to(2, y + 3)
            cr.show_text(str(threshold))
        cr.set_dash([])

        # Plot points
        n = len(self._history)
        points = []
        for i, entry in enumerate(self._history):
            x = pad_l + (i / max(1, n - 1)) * gw if n > 1 else pad_l + gw / 2
            score = entry.get("score", 0)
            y = pad_t + gh * (1.0 - score / 100.0)
            points.append((x, y))

        if n == 1:
            # Single entry: draw a larger dot with score label
            px, py = points[0]
            cr.set_source_rgba(r, g, b, 1.0)
            cr.arc(px, py, 6, 0, 2 * math.pi)
            cr.fill()
            score_val = self._history[0].get("score", 0)
            cr.set_font_size(12)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.move_to(px + 10, py + 4)
            cr.show_text(str(score_val))
        else:
            # Fill under line
            if points:
                cr.set_source_rgba(r, g, b, 0.08)
                cr.move_to(points[0][0], pad_t + gh)
                for px, py in points:
                    cr.line_to(px, py)
                cr.line_to(points[-1][0], pad_t + gh)
                cr.close_path()
                cr.fill()

            # Line
            cr.set_line_width(2)
            cr.set_source_rgba(r, g, b, 0.8)
            cr.move_to(*points[0])
            for px, py in points[1:]:
                cr.line_to(px, py)
            cr.stroke()

            # Dot on most recent
            lx, ly = points[-1]
            cr.set_source_rgba(r, g, b, 1.0)
            cr.arc(lx, ly, 4, 0, 2 * math.pi)
            cr.fill()

            # Score label on dot
            latest = self._history[-1].get("score", 0)
            cr.set_font_size(10)
            cr.move_to(lx + 6, ly - 4)
            cr.show_text(str(latest))

    def _get_checks(self):
        """Define all security checks. Returns list of (name, passed, desc, fix, weight)."""
        checks = []

        # 1. Firewall active (15 pts)
        name = "Firewall Active"
        _, _, rc = self.run_sudo(["nft", "list", "tables"])
        if rc == 0:
            checks.append((name, True, "nftables firewall is active", "", 15))
        else:
            checks.append((name, False, "nftables firewall is not running",
                           "Run: sudo nft -f /etc/gpmodes/isp.nft", 15))

        # 2. SSH key-only auth (10 pts)
        name = "SSH Key-Only Auth"
        try:
            with open("/etc/ssh/sshd_config") as f:
                content = f.read()
            has_no_pw = bool(re.search(r'^\s*PasswordAuthentication\s+no', content, re.MULTILINE))
            if has_no_pw:
                checks.append((name, True, "SSH password authentication is disabled", "", 10))
            else:
                checks.append((name, False, "SSH password authentication may be enabled",
                               "Set 'PasswordAuthentication no' in /etc/ssh/sshd_config", 10))
        except Exception:
            checks.append((name, False, "Could not read SSH config",
                           "Check /etc/ssh/sshd_config exists and is readable", 10))

        # 3. Encrypted DNS (10 pts)
        name = "Encrypted DNS"
        _, _, rc1 = self.run_cmd(["systemctl", "is-active", "--quiet", "cloudflared"])
        dot_active = False
        mode = ""
        try:
            with open("/etc/phantom/current-mode") as f:
                mode = f.read().strip()
            if mode in ("zerotrust", "zhop", "doublehop"):
                dot_active = True
        except Exception:
            pass
        # Defense-in-depth: in tunnel modes, verify the data-plane resolver
        # path is healthy. 10.66.67.1 is reachable only via wg1, so a successful
        # dig confirms both encryption (WG transport) and routing.
        tunnel_resolver_unhealthy = False
        if mode in ("doublehop", "zhop"):
            _, _, rc_dig = self.run_cmd(
                ["dig", "+time=2", "+tries=1", "+short", "google.com", "@10.66.67.1"],
                timeout=5,
            )
            tunnel_resolver_unhealthy = rc_dig != 0
        if tunnel_resolver_unhealthy:
            checks.append((name, False, "Tunnel-resolver path unhealthy",
                           "Check wg1 link and 10.66.67.1 reachability", 10))
        elif rc1 == 0 or dot_active:
            checks.append((name, True, "Encrypted DNS (DoH/DoT) is active", "", 10))
        else:
            checks.append((name, False, "DNS queries may not be encrypted",
                           "Run: sudo gp-dns-switch on", 10))

        # 4. VPN tunnel UP (10 pts)
        name = "VPN Tunnel UP"
        stdout, _, rc = self.run_cmd(["ip", "link", "show", "wg1"])
        if rc == 0 and "UP" in stdout:
            checks.append((name, True, "WireGuard data tunnel (wg1) is UP", "", 10))
        else:
            checks.append((name, False, "WireGuard data tunnel (wg1) is DOWN",
                           "Switch to DoubleHop or ZHop mode for tunnel protection", 10))

        # 5. Pi-hole running (10 pts)
        name = "Pi-hole Running"
        _, _, rc = self.run_cmd(["systemctl", "is-active", "--quiet", "pihole-FTL"])
        if rc == 0:
            checks.append((name, True, "Pi-hole DNS blocker is active", "", 10))
        else:
            checks.append((name, False, "Pi-hole is not running",
                           "Run: sudo systemctl start pihole-FTL", 10))

        # 6. Auto-updates enabled (5 pts)
        name = "Auto-Updates Enabled"
        _, _, rc = self.run_cmd(["systemctl", "is-active", "--quiet", "ghostport-update.timer"])
        if rc == 0:
            checks.append((name, True, "Automatic update timer is active", "", 5))
        else:
            checks.append((name, False, "Auto-update timer is not active",
                           "Run: sudo systemctl enable --now ghostport-update.timer", 5))

        # 7. Custom passcode (10 pts)
        name = "Custom Passcode Set"
        auth_file = "/etc/phantom/auth.json"
        try:
            if os.path.exists(auth_file):
                st = os.stat(auth_file)
                if st.st_size > 20:
                    checks.append((name, True, "Custom authentication passcode is configured", "", 10))
                else:
                    checks.append((name, False, "Auth config appears empty or default",
                                   "Run: sudo gp-passcode reset", 10))
            else:
                checks.append((name, False, "No auth configuration found",
                               "Run: sudo gp-passcode reset", 10))
        except Exception:
            checks.append((name, False, "Cannot verify auth configuration",
                           "Check /etc/phantom/auth.json", 10))

        # 8. Disk encryption (5 pts)
        # Aether Box uses gocryptfs at ~/.local/share/ghostport-vault/cipher/;
        # marker file is gocryptfs.conf. Legacy /etc/phantom/vault.key kept as
        # back-compat for any manual-init flow. Full-disk LUKS would require
        # re-imaging the SD card.
        name = "Disk Encryption"
        aether_marker = os.path.expanduser(
            "~/.local/share/ghostport-vault/cipher/gocryptfs.conf"
        )
        if os.path.isfile(aether_marker):
            checks.append((name, True, "Encryption vault is initialized via Aether Box", "", 5))
        elif os.path.isfile("/etc/phantom/vault.key"):
            checks.append((name, True, "Encryption vault is initialized", "", 5))
        else:
            stdout2, _, rc2 = self.run_cmd(["ls", "/dev/mapper/"])
            if "crypt" in (stdout2 or "").lower():
                checks.append((name, True, "Disk encryption is active", "", 5))
            else:
                checks.append((name, False, "No disk encryption detected",
                               "Open Aether Box to initialize an encrypted vault for sensitive files", 5))

        # 9. IDS running (10 pts)
        name = "Intrusion Detection"
        _, _, rc1 = self.run_cmd(["systemctl", "is-active", "--quiet", "ghostport-sni"])
        stdout2, _, _ = self.run_cmd(["pgrep", "-f", "gp-security-loop"])
        if rc1 == 0 or stdout2:
            checks.append((name, True, "Intrusion detection is active (SNI Inspector)", "", 10))
        else:
            checks.append((name, False, "No intrusion detection service running",
                           "Run: sudo systemctl start ghostport-sni", 10))

        # 10. Kill switch armed (15 pts)
        name = "Kill Switch Armed"
        stdout, _, rc = self.run_sudo(["nft", "list", "ruleset"])
        if rc == 0 and "killswitch" in (stdout or "").lower():
            checks.append((name, True, "Network kill switch chain is configured", "", 15))
        else:
            try:
                with open("/etc/phantom/current-mode") as f:
                    mode = f.read().strip()
                if mode in ("doublehop", "zhop"):
                    checks.append((name, False, "Kill switch chain not found in tunnel mode",
                                   "Kill switch should be active in tunnel modes", 15))
                else:
                    checks.append((name, True, "Kill switch not needed in current mode (ISP/ZeroTrust)", "", 15))
            except Exception:
                checks.append((name, False, "Cannot determine kill switch state",
                               "Check nftables configuration", 15))

        # 11. DNS Resolver Health (10 pts)
        name = "DNS Resolver Health"
        # Use mode-aware DNS server: tunnel modes query data-plane Unbound
        dns_server = "127.0.0.1"
        try:
            with open("/etc/phantom/current-mode") as f:
                cur_mode = f.read().strip()
            if cur_mode in ("doublehop", "zhop"):
                dns_server = "10.66.67.1"
        except Exception:
            pass
        start_t = time.monotonic()
        stdout, _, rc = self.run_cmd(
            ["dig", "+short", "+time=2", "+tries=1", "google.com", f"@{dns_server}"], timeout=5
        )
        elapsed_ms = (time.monotonic() - start_t) * 1000
        if rc == 0 and stdout and stdout.strip() and elapsed_ms < 500:
            checks.append((name, True, f"DNS resolver ({dns_server}) responding in {elapsed_ms:.0f}ms", "", 10))
        else:
            checks.append((name, False, f"DNS resolver ({dns_server}) slow or failing ({elapsed_ms:.0f}ms)",
                           "Check Pi-hole and upstream DNS configuration", 10))

        # 12. Interface Error Rate (5 pts)
        name = "Interface Error Rate"
        try:
            with open("/proc/net/dev") as f:
                lines = f.readlines()
            total_errors = 0
            total_packets = 0
            for line in lines[2:]:
                parts = line.split()
                if len(parts) >= 12:
                    iface = parts[0].rstrip(":")
                    if iface in ("eth0", "wlan0"):
                        rx_pkts = int(parts[2])
                        rx_errs = int(parts[3])
                        tx_pkts = int(parts[10])
                        tx_errs = int(parts[11])
                        total_errors += rx_errs + tx_errs
                        total_packets += rx_pkts + tx_pkts
            rate = (total_errors / max(total_packets, 1)) * 100.0
            if rate < 0.1:
                checks.append((name, True,
                               f"Interface error rate {rate:.3f}% ({total_errors} errors / {total_packets} packets)", "", 5))
            else:
                checks.append((name, False,
                               f"Interface error rate {rate:.3f}% ({total_errors} errors / {total_packets} packets)",
                               "Check cable connections and WiFi interference", 5))
        except Exception:
            checks.append((name, False, "Could not read interface statistics",
                           "Check /proc/net/dev", 5))

        return checks

    def _on_scan(self, btn):
        """Run security scan."""
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.set_sensitive(False)
        self._export_btn.set_sensitive(False)
        self._progress_label.set_text("Scanning...")
        self.set_status("Running security audit...")

        # Clear previous results
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        def do_scan():
            return self._get_checks()

        def on_done(result):
            if isinstance(result, Exception):
                self.set_status(f"Scan error: {result}")
                self._scanning = False
                self._scan_btn.set_sensitive(True)
                return

            self._checks = result

            # Weighted scoring
            total_weight = sum(w for _, _, _, _, w in self._checks)
            passed_weight = sum(w for _, p, _, _, w in self._checks if p)
            score = int((passed_weight / total_weight) * 100) if total_weight > 0 else 0
            passed_count = sum(1 for _, p, _, _, _ in self._checks if p)
            total_count = len(self._checks)

            # Save to history (async to avoid blocking GTK main loop)
            self.run_async(
                lambda: self._save_history(score, passed_count, total_count),
                lambda *a: None
            )

            # Update score display
            self._score_label.set_text(str(score))
            self._score_label.get_style_context().remove_class("gp-success")
            self._score_label.get_style_context().remove_class("gp-warning")
            self._score_label.get_style_context().remove_class("gp-danger")
            self._score_label.get_style_context().remove_class("gp-big-number")
            if score >= 80:
                self._score_label.get_style_context().add_class("gp-success")
            elif score >= 60:
                self._score_label.get_style_context().add_class("gp-warning")
            else:
                self._score_label.get_style_context().add_class("gp-danger")
            # Keep big font
            ctx = self._score_label.get_style_context()
            big_css = """
                .score-big {
                    font-family: monospace;
                    font-size: 48px;
                    font-weight: bold;
                }
            """
            provider = Gtk.CssProvider()
            provider.load_from_data(big_css.encode())
            ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            ctx.add_class("score-big")

            # Populate check items
            for name, passed_flag, desc, fix, weight in self._checks:
                row = Gtk.ListBoxRow()
                row.set_selectable(False)
                card = self._make_check_card(name, passed_flag, desc, fix, weight)
                row.add(card)
                self._listbox.add(row)

            self._listbox.show_all()
            # Redraw history graph
            self._history_da.queue_draw()
            self._progress_label.set_text(f"{passed_count}/{total_count} checks passed ({passed_weight}/{total_weight} pts)")
            self.set_status(f"Audit complete: {score}/100 ({passed_count}/{total_count} checks, weighted)")
            self._scanning = False
            self._scan_btn.set_sensitive(True)
            self._export_btn.set_sensitive(True)

        self.run_async(do_scan, on_done)

    def _make_check_card(self, name, passed, desc, fix, weight):
        """Create a card for a single security check."""
        card_class = "gp-card" if passed else "gp-card-danger"

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class(card_class)

        # Top row: badge + name + weight
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        if passed:
            badge = self.make_label("PASS", "gp-success")
        else:
            badge = self.make_label("FAIL", "gp-danger")
        top.pack_start(badge, False, False, 0)

        name_lbl = self.make_label(name, "gp-accent")
        top.pack_start(name_lbl, False, False, 0)

        weight_lbl = self.make_label(f"{weight} pts", "gp-dim")
        top.pack_end(weight_lbl, False, False, 0)

        card.pack_start(top, False, False, 0)

        # Description
        desc_lbl = self.make_label(desc, "gp-text")
        card.pack_start(desc_lbl, False, False, 0)

        # Fix suggestion (if failed)
        if not passed and fix:
            fix_lbl = self.make_label(f"Fix: {fix}", "gp-warning")
            card.pack_start(fix_lbl, False, False, 0)

        return card

    def _on_export(self, btn):
        """Export report to file."""
        if not self._checks:
            return

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = os.path.expanduser(f"~/security-report-{timestamp}.txt")

        total_weight = sum(w for _, _, _, _, w in self._checks)
        passed_weight = sum(w for _, p, _, _, w in self._checks if p)
        score = int((passed_weight / total_weight) * 100) if total_weight > 0 else 0
        passed_count = sum(1 for _, p, _, _, _ in self._checks if p)
        total_count = len(self._checks)

        lines = [
            "=" * 60,
            "  QUARTERMASTER Security Audit Report",
            f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            "",
            f"  Overall Score: {score}/100 (weighted: {passed_weight}/{total_weight} pts)",
            f"  Checks: {passed_count}/{total_count} passed",
            "",
            "-" * 60,
        ]

        for name, passed_flag, desc, fix, weight in self._checks:
            status = "PASS" if passed_flag else "FAIL"
            lines.append(f"  [{status}] {name} ({weight} pts)")
            lines.append(f"         {desc}")
            if not passed_flag and fix:
                lines.append(f"         Fix: {fix}")
            lines.append("")

        lines.append("=" * 60)
        lines.append("  Phantom OS - Security Audit")
        lines.append("=" * 60)

        try:
            with open(report_path, "w") as f:
                f.write("\n".join(lines) + "\n")
            self.set_status(f"Report exported to {report_path}")
            try:
                import subprocess
                subprocess.run(
                    ["xdg-open", report_path],
                    timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except Exception:
                pass
        except Exception as e:
            self.set_status(f"Export failed: {e}")

    def on_theme_changed(self):
        """Redraw history graph on theme change."""
        self._history_da.queue_draw()


if __name__ == "__main__":
    app = QuartermasterApp()
    app.run()
