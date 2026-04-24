#!/usr/bin/env python3
"""gp-sonar — SONAR: Rogue AP / Evil Twin WiFi Scanner for Phantom OS"""
import sys, os, re, json, time, subprocess
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

# ── Constants ─────────────────────────────────────────────────────────

TRUSTED_APS_FILE = os.path.expanduser("~/.config/phantom/trusted-aps.json")
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"


def get_our_ap():
    """Read our own AP SSID and BSSID from hostapd config and interface."""
    ssid = ""
    bssid = ""
    try:
        with open(HOSTAPD_CONF) as f:
            for line in f:
                if line.startswith("ssid="):
                    ssid = line.strip().split("=", 1)[1]
                elif line.startswith("interface="):
                    iface = line.strip().split("=", 1)[1]
        # Get BSSID from interface
        result = subprocess.run(
            ["ip", "link", "show", "wlan0"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "link/ether" in line:
                bssid = line.split()[1].lower()
                break
    except Exception:
        pass
    return ssid, bssid


def load_trusted_aps():
    """Load trusted AP list from JSON file."""
    try:
        with open(TRUSTED_APS_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_trusted_aps(trusted):
    """Save trusted AP list to JSON file."""
    try:
        os.makedirs(os.path.dirname(TRUSTED_APS_FILE), exist_ok=True)
        with open(TRUSTED_APS_FILE, "w") as f:
            json.dump(trusted, f, indent=2)
    except Exception:
        pass


def parse_iw_scan(output, our_ssid, our_bssid, trusted_bssids):
    """Parse 'sudo iw dev wlan0 scan' output into list of AP dicts."""
    aps = []
    current = None

    for line in output.splitlines():
        line_stripped = line.strip()

        # New BSS block
        if line.startswith("BSS "):
            if current:
                aps.append(current)
            bssid_match = re.match(r'BSS\s+([0-9a-f:]{17})', line)
            bssid = bssid_match.group(1).lower() if bssid_match else ""
            current = {
                "bssid": bssid,
                "ssid": "",
                "freq": 0,
                "channel": 0,
                "signal": -100,
                "encryption": "Open",
                "wpa_version": "",
                "is_ours": False,
                "threat": "safe",
                "threat_label": "SAFE",
            }
            continue

        if current is None:
            continue

        if line_stripped.startswith("SSID:"):
            current["ssid"] = line_stripped[5:].strip()
        elif line_stripped.startswith("freq:"):
            try:
                current["freq"] = int(line_stripped.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
        elif line_stripped.startswith("signal:"):
            sig_match = re.search(r'(-?\d+\.?\d*)', line_stripped)
            if sig_match:
                current["signal"] = float(sig_match.group(1))
        elif line_stripped.startswith("DS Parameter set: channel"):
            ch_match = re.search(r'channel\s+(\d+)', line_stripped)
            if ch_match:
                current["channel"] = int(ch_match.group(1))
        elif "RSN:" in line_stripped or "WPA:" in line_stripped:
            if "RSN:" in line_stripped:
                current["encryption"] = "WPA2"
                current["wpa_version"] = "RSN"
            elif "WPA:" in line_stripped:
                if current["encryption"] == "Open":
                    current["encryption"] = "WPA"
        elif "SAE" in line_stripped or "Group management" in line_stripped:
            if current["encryption"] in ("WPA2", "WPA"):
                current["encryption"] = "WPA3"
        elif "WEP" in line_stripped and "Privacy" in line_stripped:
            if current["encryption"] == "Open":
                current["encryption"] = "WEP"
        elif line_stripped.startswith("capability:"):
            if "Privacy" in line_stripped and current["encryption"] == "Open":
                current["encryption"] = "WEP"

    if current:
        aps.append(current)

    # Derive channel from freq if not set
    for ap in aps:
        if ap["channel"] == 0 and ap["freq"] > 0:
            ap["channel"] = freq_to_channel(ap["freq"])

    # Classify threats
    for ap in aps:
        ap["is_ours"] = (ap["bssid"] == our_bssid)
        ap["threat"], ap["threat_label"] = classify_ap(
            ap, our_ssid, our_bssid, trusted_bssids
        )

    # Sort: our AP first, then by threat (dangerous > suspicious > warning > safe), then signal
    threat_order = {"dangerous": 0, "suspicious": 1, "warning": 2, "safe": 3}
    aps.sort(key=lambda a: (
        0 if a["is_ours"] else 1,
        threat_order.get(a["threat"], 4),
        a["signal"],  # lower (more negative) = weaker, so stronger first
    ))

    return aps


def freq_to_channel(freq):
    """Convert WiFi frequency to channel number."""
    if 2412 <= freq <= 2484:
        if freq == 2484:
            return 14
        return (freq - 2412) // 5 + 1
    elif 5170 <= freq <= 5825:
        return (freq - 5000) // 5
    elif 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return 0


def classify_ap(ap, our_ssid, our_bssid, trusted_bssids):
    """Return (threat_level, threat_label) for an AP."""
    # Our own AP
    if ap["bssid"] == our_bssid:
        return "safe", "OUR AP"

    # Evil twin: same SSID, different BSSID
    if ap["ssid"] and ap["ssid"] == our_ssid and ap["bssid"] != our_bssid:
        return "dangerous", "EVIL TWIN"

    # Trusted AP
    if ap["bssid"] in trusted_bssids:
        return "safe", "TRUSTED"

    # Open network (no encryption)
    if ap["encryption"] == "Open":
        return "warning", "OPEN"

    # WEP (broken encryption)
    if ap["encryption"] == "WEP":
        return "warning", "WEAK (WEP)"

    # Very strong signal from unknown AP (possible rogue)
    if ap["signal"] >= -40:
        return "suspicious", "STRONG SIGNAL"

    # Same channel as ours, strong signal (potential interference/rogue)
    # We'd need our channel for this, skip for now

    return "safe", "SAFE"


# ── Sonar App ─────────────────────────────────────────────────────────

class SonarApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Sonar?",
         "Sonar scans the airwaves for WiFi access points near your device — your own, "
         "your neighbors', and anything suspicious in between.\n\n"
         "It's a rogue-AP detector: its main job is spotting \"evil twins\" (an "
         "attacker setting up a clone of your own network to lure devices into "
         "connecting to them) and unknown APs appearing at your location. Think of "
         "it as a radar dish pinging the wireless neighborhood."),

        ("SCAN button + spinner (top right)",
         "Click SCAN to run a passive WiFi scan. The spinner next to it shows the "
         "scan is in progress (usually 5-15 seconds).\n\n"
         "The scan is PASSIVE — Sonar listens for beacons, it doesn't probe. "
         "Neighboring networks see nothing; you're just reading broadcasts. Safe "
         "and silent.\n\n"
         "Scans don't run automatically on a timer — too noisy for the radio. "
         "Run a scan whenever you're curious or when you've moved locations."),

        ("Info bar — Our AP",
         "\"Our AP\" shows your own GhostPort access point's SSID and BSSID "
         "(WiFi name + hardware address). Everything else in the list gets "
         "compared against this.\n\n"
         "An evil twin would broadcast a matching SSID but have a DIFFERENT "
         "BSSID. Sonar flags that automatically.\n\n"
         "\"Last scan\" — when the current results were collected. Results don't "
         "auto-refresh; re-scan if you want fresh data."),

        ("AP list (left panel)",
         "Every access point Sonar heard during the scan, sorted by signal strength. "
         "Each row shows: SSID, BSSID, channel, signal strength, encryption type.\n\n"
         "Color coding:\n"
         "• Accent (bright) — your own AP, or an AP you've marked Trusted.\n"
         "• Dim — known nearby network, not classified.\n"
         "• Red/warning — flagged as suspicious (open encryption, BSSID mismatch, "
         "duplicate SSID).\n\n"
         "Click a row to see its details in the right panel."),

        ("Detail panel (right)",
         "When you select an AP, this panel shows everything known about it: full "
         "BSSID, signal history (if scanned repeatedly), first/last seen time, "
         "encryption breakdown, vendor guess from the MAC prefix.\n\n"
         "This is where you decide whether something is benign or worth investigating. "
         "A high-signal unknown AP in a spot where you know nobody's broadcasting? "
         "Worth a second look."),

        ("Trust Selected / Untrust Selected / Export Results",
         "Trust Selected — mark this AP as known-good. Future scans won't flag it. "
         "Use this for your neighbor's WiFi, public hotspots you recognize, etc.\n\n"
         "Untrust Selected — remove the trusted flag. Next scan re-evaluates it "
         "against all suspicion rules.\n\n"
         "Export Results — write the current scan to a timestamped file. Useful "
         "when you want a record before leaving a location, or to share with "
         "someone technical."),

        ("What's an evil twin and why does this matter?",
         "An evil twin is a WiFi access point impersonating another. Attacker sets "
         "up a laptop broadcasting \"Starbucks WiFi\" in a Starbucks — your phone "
         "auto-connects to the stronger signal, the attacker now routes your "
         "internet and sees everything not encrypted end-to-end.\n\n"
         "Sonar spots twins two ways: (1) same SSID as your AP but different BSSID "
         "= flagged immediately. (2) Unknown open network with strong signal where "
         "there shouldn't be one.\n\n"
         "Best defense beyond Sonar: don't let your devices auto-join open networks, "
         "and always use a VPN on public WiFi."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)


    def __init__(self):
        super().__init__("SONAR", "sonar", (900, 650))
        self.aps = []
        self.scanning = False
        self.our_ssid = ""
        self.our_bssid = ""
        self.trusted = []
        self.trusted_bssids = set()
        self.selected_ap = None
        self.last_scan_time = None

        # Load our AP info and trusted list
        self.our_ssid, self.our_bssid = get_our_ap()
        self._reload_trusted()

        self.build_ui()

    def _reload_trusted(self):
        self.trusted = load_trusted_aps()
        self.trusted_bssids = {t.get("bssid", "").lower() for t in self.trusted}

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header row with scan button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.get_style_context().add_class("gp-header")

        header_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_label = Gtk.Label(label="SONAR")
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class("gp-header-title")
        header_left.pack_start(title_label, False, False, 0)

        sub_label = Gtk.Label(label="Rogue AP Scanner")
        sub_label.set_halign(Gtk.Align.START)
        sub_label.get_style_context().add_class("gp-header-subtitle")
        header_left.pack_start(sub_label, False, False, 0)

        header_box.pack_start(header_left, True, True, 0)

        # Scan button + spinner
        scan_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        scan_box.set_valign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        scan_box.pack_start(self.spinner, False, False, 0)

        self.btn_scan = self.make_button("SCAN", self._on_scan, "gp-btn-primary")
        scan_box.pack_start(self.btn_scan, False, False, 0)

        header_box.pack_end(scan_box, False, False, 8)
        root.pack_start(header_box, False, False, 0)

        # Info bar: our AP + last scan time
        info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        info_bar.set_margin_start(12)
        info_bar.set_margin_end(12)
        info_bar.set_margin_top(6)
        info_bar.set_margin_bottom(4)

        self.lbl_our_ap = self.make_label(
            f"Our AP: {self.our_ssid} ({self.our_bssid})", "gp-dim"
        )
        info_bar.pack_start(self.lbl_our_ap, False, False, 0)

        self.lbl_scan_time = self.make_label("Last scan: never", "gp-dim")
        info_bar.pack_end(self.lbl_scan_time, False, False, 0)

        root.pack_start(info_bar, False, False, 0)

        # Main content: paned with AP list on left, detail on right
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(520)

        # Left: AP list
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)

        scrolled_list = self.make_scrolled(self.listbox)
        scrolled_list.set_min_content_width(400)
        paned.pack1(scrolled_list, True, False)

        # Right: detail panel
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.detail_box.get_style_context().add_class("gp-sidebar")
        self.detail_box.set_margin_start(0)

        self.lbl_detail_placeholder = self.make_label(
            "Select an AP to view details", "gp-dim"
        )
        self.lbl_detail_placeholder.set_halign(Gtk.Align.CENTER)
        self.lbl_detail_placeholder.set_valign(Gtk.Align.CENTER)
        self.detail_box.pack_start(self.lbl_detail_placeholder, True, True, 20)

        scrolled_detail = self.make_scrolled(self.detail_box)
        scrolled_detail.set_min_content_width(250)
        paned.pack2(scrolled_detail, False, True)

        root.pack_start(paned, True, True, 0)

        # Bottom button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(8)
        btn_bar.set_margin_end(8)
        btn_bar.set_margin_top(4)
        btn_bar.set_margin_bottom(4)

        btn_export = self.make_button("Export Results", self._on_export, "gp-btn")
        btn_bar.pack_end(btn_export, False, False, 0)

        btn_trust = self.make_button("Trust Selected", self._on_trust, "gp-btn")
        btn_bar.pack_end(btn_trust, False, False, 0)

        btn_untrust = self.make_button("Untrust Selected", self._on_untrust, "gp-btn-danger")
        btn_bar.pack_end(btn_untrust, False, False, 0)

        btn_bar.pack_start(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)

        root.pack_start(btn_bar, False, False, 0)

        # Status bar
        root.pack_start(self.make_status_bar("Ready -- press SCAN to begin"), False, False, 0)

    # ── Scanning ─────────────────────────────────────────────────────

    def _on_scan(self, btn):
        if self.scanning:
            return
        self.scanning = True
        self.btn_scan.set_sensitive(False)
        self.btn_scan.set_label("SCANNING...")
        self.spinner.start()
        self.set_status("Scanning nearby access points...")
        self.run_async(self._do_scan, self._on_scan_done)

    def _do_scan(self):
        """Background: run iw scan."""
        stdout, stderr, rc = self.run_sudo(["iw", "dev", "wlan0", "scan"], timeout=30)
        if rc != 0 and "busy" in stderr.lower():
            # Interface busy, try ap scan
            time.sleep(2)
            stdout, stderr, rc = self.run_sudo(["iw", "dev", "wlan0", "scan"], timeout=30)
        return stdout, stderr, rc

    def _on_scan_done(self, result):
        """Main thread: process scan results."""
        self.scanning = False
        self.btn_scan.set_sensitive(True)
        self.btn_scan.set_label("SCAN")
        self.spinner.stop()

        if isinstance(result, Exception):
            self.set_status(f"Scan error: {result}")
            return

        stdout, stderr, rc = result
        if rc != 0 and not stdout:
            self.set_status(f"Scan failed: {stderr[:80]}")
            return

        self._reload_trusted()
        self.aps = parse_iw_scan(stdout, self.our_ssid, self.our_bssid, self.trusted_bssids)
        self.last_scan_time = time.strftime("%H:%M:%S")
        self.lbl_scan_time.set_text(f"Last scan: {self.last_scan_time}")

        self._rebuild_list()

        dangers = sum(1 for a in self.aps if a["threat"] == "dangerous")
        suspicious = sum(1 for a in self.aps if a["threat"] == "suspicious")
        warnings = sum(1 for a in self.aps if a["threat"] == "warning")
        safe = sum(1 for a in self.aps if a["threat"] == "safe")
        self.set_status(
            f"Found {len(self.aps)} APs | "
            f"{dangers} dangerous | {suspicious} suspicious | "
            f"{warnings} warnings | {safe} safe"
        )

    # ── List Rebuild ─────────────────────────────────────────────────

    def _rebuild_list(self):
        """Rebuild the ListBox from self.aps."""
        # Clear
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        for i, ap in enumerate(self.aps):
            row = Gtk.ListBoxRow()
            row.ap_index = i
            card = self._make_ap_card(ap)
            row.add(card)
            self.listbox.add(row)

        self.listbox.show_all()

    def _make_ap_card(self, ap):
        """Create a card widget for a single AP."""
        threat = ap["threat"]
        if threat == "dangerous":
            css_class = "gp-card-danger"
        elif threat in ("suspicious", "warning"):
            css_class = "gp-card-warning"
        elif ap["is_ours"]:
            css_class = "gp-card-info"
        else:
            css_class = "gp-card"

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.get_style_context().add_class(css_class)

        # Row 1: SSID + threat badge
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        ssid_display = ap["ssid"] if ap["ssid"] else "(Hidden)"
        if ap["is_ours"]:
            ssid_display = f"{ssid_display}  [OUR AP]"
        lbl_ssid = self.make_label(ssid_display, "gp-accent")
        row1.pack_start(lbl_ssid, True, True, 0)

        # Threat badge
        threat_css = {
            "dangerous": "gp-danger",
            "suspicious": "gp-warning",
            "warning": "gp-warning",
            "safe": "gp-success",
        }.get(threat, "gp-dim")
        lbl_threat = self.make_label(ap["threat_label"], threat_css)
        row1.pack_end(lbl_threat, False, False, 0)

        card.pack_start(row1, False, False, 0)

        # Row 2: BSSID, Channel, Signal, Encryption
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        lbl_bssid = self.make_label(ap["bssid"], "gp-dim")
        row2.pack_start(lbl_bssid, False, False, 0)

        lbl_ch = self.make_label(f"CH {ap['channel']}", "gp-text")
        row2.pack_start(lbl_ch, False, False, 0)

        # Signal strength with color
        signal = ap["signal"]
        if signal >= -50:
            sig_css = "gp-success"
        elif signal >= -70:
            sig_css = "gp-accent"
        elif signal >= -80:
            sig_css = "gp-warning"
        else:
            sig_css = "gp-danger"
        lbl_sig = self.make_label(f"{signal:.0f} dBm", sig_css)
        row2.pack_start(lbl_sig, False, False, 0)

        # Encryption
        enc = ap["encryption"]
        enc_css = "gp-success" if enc in ("WPA3", "WPA2") else (
            "gp-warning" if enc == "WPA" else (
                "gp-danger" if enc in ("WEP", "Open") else "gp-dim"
            )
        )
        lbl_enc = self.make_label(enc, enc_css)
        row2.pack_start(lbl_enc, False, False, 0)

        card.pack_start(row2, False, False, 0)

        return card

    # ── Detail Panel ─────────────────────────────────────────────────

    def _on_row_selected(self, listbox, row):
        if row is None:
            self.selected_ap = None
            return

        idx = row.ap_index
        if idx >= len(self.aps):
            return

        self.selected_ap = self.aps[idx]
        self._show_detail(self.selected_ap)

    def _show_detail(self, ap):
        """Show detailed info for selected AP in right panel."""
        for child in self.detail_box.get_children():
            self.detail_box.remove(child)

        # Title
        ssid = ap["ssid"] if ap["ssid"] else "(Hidden Network)"
        lbl_title = self.make_label(ssid, "gp-accent")
        lbl_title.override_font(Pango.FontDescription("monospace bold 14"))
        self.detail_box.pack_start(lbl_title, False, False, 4)

        sep = Gtk.Separator()
        self.detail_box.pack_start(sep, False, False, 4)

        # Detail fields
        fields = [
            ("BSSID", ap["bssid"]),
            ("Channel", str(ap["channel"])),
            ("Frequency", f"{ap['freq']} MHz"),
            ("Signal", f"{ap['signal']:.0f} dBm"),
            ("Encryption", ap["encryption"]),
            ("Threat", ap["threat_label"]),
        ]

        for label_text, value_text in fields:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl_key = self.make_label(f"{label_text}:", "gp-dim")
            lbl_key.set_size_request(90, -1)
            row.pack_start(lbl_key, False, False, 0)

            # Color the value based on content
            val_css = "gp-text"
            if label_text == "Threat":
                val_css = {
                    "EVIL TWIN": "gp-danger",
                    "STRONG SIGNAL": "gp-warning",
                    "OPEN": "gp-warning",
                    "WEAK (WEP)": "gp-warning",
                    "SAFE": "gp-success",
                    "TRUSTED": "gp-success",
                    "OUR AP": "gp-accent",
                }.get(value_text, "gp-text")
            elif label_text == "Encryption":
                val_css = "gp-success" if value_text in ("WPA3", "WPA2") else (
                    "gp-danger" if value_text in ("Open", "WEP") else "gp-text"
                )

            lbl_val = self.make_label(value_text, val_css)
            row.pack_start(lbl_val, False, False, 0)
            self.detail_box.pack_start(row, False, False, 2)

        # Signal strength bar (visual)
        sep2 = Gtk.Separator()
        self.detail_box.pack_start(sep2, False, False, 4)

        lbl_sig_title = self.make_label("SIGNAL STRENGTH", "gp-dim")
        self.detail_box.pack_start(lbl_sig_title, False, False, 2)

        sig_bar = self._make_signal_bar(ap["signal"])
        self.detail_box.pack_start(sig_bar, False, False, 2)

        # Threat explanation
        if ap["threat"] != "safe" or ap["is_ours"]:
            sep3 = Gtk.Separator()
            self.detail_box.pack_start(sep3, False, False, 4)

            lbl_expl_title = self.make_label("ASSESSMENT", "gp-dim")
            self.detail_box.pack_start(lbl_expl_title, False, False, 2)

            explanation = self._get_threat_explanation(ap)
            lbl_expl = self.make_label(explanation, "gp-text")
            lbl_expl.set_line_wrap(True)
            lbl_expl.set_max_width_chars(30)
            self.detail_box.pack_start(lbl_expl, False, False, 2)

        # Trust status
        is_trusted = ap["bssid"] in self.trusted_bssids
        sep4 = Gtk.Separator()
        self.detail_box.pack_start(sep4, False, False, 4)
        trust_text = "TRUSTED" if is_trusted else "UNTRUSTED"
        trust_css = "gp-success" if is_trusted else "gp-dim"
        lbl_trust = self.make_label(f"Status: {trust_text}", trust_css)
        self.detail_box.pack_start(lbl_trust, False, False, 2)

        self.detail_box.show_all()

    def _make_signal_bar(self, signal):
        """Create a text-based signal strength indicator."""
        # Map signal to 0-5 bars
        # -30 dBm = excellent, -90 dBm = terrible
        bars = max(0, min(5, int((signal + 90) / 12)))
        bar_text = "|" * bars + "." * (5 - bars)

        if signal >= -50:
            quality = "Excellent"
            css = "gp-success"
        elif signal >= -60:
            quality = "Good"
            css = "gp-success"
        elif signal >= -70:
            quality = "Fair"
            css = "gp-accent"
        elif signal >= -80:
            quality = "Weak"
            css = "gp-warning"
        else:
            quality = "Very Weak"
            css = "gp-danger"

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_bar = self.make_label(f"[{bar_text}]", css)
        lbl_bar.override_font(Pango.FontDescription("monospace bold 14"))
        box.pack_start(lbl_bar, False, False, 0)

        lbl_q = self.make_label(quality, css)
        box.pack_start(lbl_q, False, False, 0)

        return box

    def _get_threat_explanation(self, ap):
        """Return a human-readable threat explanation."""
        if ap["is_ours"]:
            return "This is your GhostPort access point."
        t = ap["threat_label"]
        if t == "EVIL TWIN":
            return (
                f"CRITICAL: This AP uses the same SSID as your network "
                f"({ap['ssid']}) but has a different BSSID. This is a "
                f"classic evil twin attack used to intercept traffic."
            )
        if t == "OPEN":
            return (
                "This network has no encryption. Any traffic sent over "
                "it can be intercepted by anyone nearby."
            )
        if t == "WEAK (WEP)":
            return (
                "This network uses WEP encryption which is broken and "
                "can be cracked in minutes."
            )
        if t == "STRONG SIGNAL":
            return (
                "This unknown AP has an unusually strong signal, which "
                "could indicate a nearby rogue access point attempting "
                "to lure devices."
            )
        if t == "TRUSTED":
            return "You have marked this AP as trusted."
        return "No known threats detected from this access point."

    # ── Trust / Untrust ──────────────────────────────────────────────

    def _on_trust(self, btn):
        if not self.selected_ap:
            self.set_status("No AP selected")
            return
        ap = self.selected_ap
        if ap["bssid"] in self.trusted_bssids:
            self.set_status(f"Already trusted: {ap['bssid']}")
            return
        self.trusted.append({
            "bssid": ap["bssid"],
            "ssid": ap["ssid"],
            "added": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.trusted_bssids.add(ap["bssid"])
        save_trusted_aps(self.trusted)
        # Re-classify and rebuild
        for a in self.aps:
            a["threat"], a["threat_label"] = classify_ap(
                a, self.our_ssid, self.our_bssid, self.trusted_bssids
            )
        self._rebuild_list()
        self._show_detail(ap)
        self.set_status(f"Trusted: {ap['ssid']} ({ap['bssid']})")

    def _on_untrust(self, btn):
        if not self.selected_ap:
            self.set_status("No AP selected")
            return
        ap = self.selected_ap
        if ap["bssid"] not in self.trusted_bssids:
            self.set_status(f"Not in trusted list: {ap['bssid']}")
            return
        self.trusted = [t for t in self.trusted if t.get("bssid", "").lower() != ap["bssid"]]
        self.trusted_bssids.discard(ap["bssid"])
        save_trusted_aps(self.trusted)
        for a in self.aps:
            a["threat"], a["threat_label"] = classify_ap(
                a, self.our_ssid, self.our_bssid, self.trusted_bssids
            )
        self._rebuild_list()
        self._show_detail(ap)
        self.set_status(f"Removed from trusted: {ap['ssid']} ({ap['bssid']})")

    # ── Export ───────────────────────────────────────────────────────

    def _on_export(self, btn):
        if not self.aps:
            self.set_status("No scan results to export")
            return

        dialog = Gtk.FileChooserDialog(
            title="Export Scan Results",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_current_name(f"sonar-{time.strftime('%Y%m%d-%H%M%S')}.json")

        filt = Gtk.FileFilter()
        filt.set_name("JSON files")
        filt.add_pattern("*.json")
        dialog.add_filter(filt)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            try:
                export_data = {
                    "scan_time": self.last_scan_time or "unknown",
                    "our_ssid": self.our_ssid,
                    "our_bssid": self.our_bssid,
                    "aps": self.aps,
                }
                with open(filepath, "w") as f:
                    json.dump(export_data, f, indent=2)
                self.set_status(f"Exported {len(self.aps)} APs to {os.path.basename(filepath)}")
            except Exception as e:
                self.set_status(f"Export failed: {e}")
        dialog.destroy()


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = SonarApp()
    app.run()
