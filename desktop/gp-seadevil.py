#!/usr/bin/env python3
"""gp-seadevil — Seadevil: MAC Address Randomizer for Phantom OS"""
import sys, os
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import json
import re
import time
import random
import subprocess

CONFIG_DIR = os.path.expanduser("~/.config/phantom")
ORIGINALS_FILE = os.path.join(CONFIG_DIR, "mac-originals.json")
PHYSICAL_IFACES = ["eth0", "wlan0"]

# ── Helpers ──────────────────────────────────────────────────────────

def run_cmd(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception as e:
        return "", str(e), 1

def get_current_mac(iface):
    out, _, _ = run_cmd(["ip", "link", "show", iface])
    m = re.search(r'link/ether\s+([0-9a-f:]{17})', out)
    return m.group(1) if m else None

def get_permanent_mac(iface):
    out, _, _ = run_cmd(["sudo", "ethtool", "-P", iface])
    m = re.search(r'Permanent address:\s+([0-9a-f:]{17})', out)
    return m.group(1) if m else None

def generate_random_mac():
    """Generate a locally-administered unicast MAC address."""
    # First byte: set bit 1 (locally administered) = 1, bit 0 (unicast) = 0
    # So first byte = (random & 0xFC) | 0x02
    first = (random.randint(0, 255) & 0xFC) | 0x02
    rest = [random.randint(0, 255) for _ in range(5)]
    return ":".join(f"{b:02x}" for b in [first] + rest)

def iface_exists(iface):
    _, _, rc = run_cmd(["ip", "link", "show", iface])
    return rc == 0

def load_originals():
    try:
        with open(ORIGINALS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_originals(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(ORIGINALS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Main App ─────────────────────────────────────────────────────────

class SeadevilApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Seadevil?",
         "Seadevil changes your device's MAC address — the hardware ID your network "
         "card broadcasts every time it connects to a WiFi network or ethernet port.\n\n"
         "Why? Because MAC addresses are effectively a serial number for your device. "
         "Every access point you connect to sees the same MAC, and many retain it "
         "(for the router's client list, for tracking, for advertising joins). "
         "Randomizing the MAC breaks that link — your device looks like a different "
         "one each time, so it can't be followed across networks by its hardware ID."),

        ("Interface cards (the main list)",
         "One card per physical network interface on your device. You'll typically "
         "see:\n\n"
         "• eth0 — wired ethernet port (your WAN uplink to the ISP).\n"
         "• wlan0 — built-in WiFi (either your AP, or your WAN if using WiFi WAN).\n"
         "• wlan1 — USB WiFi dongle if you have one.\n\n"
         "Each card shows two MACs:\n"
         "• Current MAC — what the interface is broadcasting right now.\n"
         "• Permanent — the hardware-burned-in address (what it was originally).\n\n"
         "If Current ≠ Permanent, you're already randomized on that interface."),

        ("Randomize button",
         "Generates a fresh random MAC and applies it to that interface immediately.\n\n"
         "Things that happen when you click:\n"
         "1. The interface is briefly brought down.\n"
         "2. A new MAC is generated (valid format: locally-administered unicast).\n"
         "3. The interface comes back up.\n"
         "4. Existing connections on that interface drop and reconnect.\n\n"
         "If you randomize wlan0 while your phone is connected via GhostPort's AP, "
         "your phone will briefly disconnect and re-associate. Normal, just plan for "
         "the blip."),

        ("Restore Original button",
         "Puts the permanent (factory) MAC back on the interface. Same brief "
         "interruption as Randomize.\n\n"
         "Use this when:\n"
         "• An ISP's DHCP is binding a lease to a specific MAC and you want your "
         "original lease back.\n"
         "• A corporate WiFi requires MAC registration.\n"
         "• You're troubleshooting a connectivity issue and want to rule out the "
         "randomized MAC as the cause."),

        ("When to randomize",
         "✅ GOOD candidates:\n"
         "• Joining any public or hotel WiFi (privacy against venue analytics).\n"
         "• Traveling — different locations shouldn't share a device ID.\n"
         "• Freshly set up device — randomize once and forget.\n"
         "• Concerned about your ISP tracking your hardware.\n\n"
         "❌ SKIP randomization when:\n"
         "• Your ISP or workplace ties services to your original MAC (cable "
         "modems sometimes do this).\n"
         "• A captive portal requires re-registration after every MAC change.\n"
         "• The interface is your GhostPort's AP (wlan0) — randomizing will kick "
         "all connected LAN devices off until they re-associate.\n\n"
         "Randomization isn't sticky across reboots — re-randomize after a "
         "boot if you want a fresh MAC. (For boot-time wlan0 randomization, "
         "the system ships `gp-mac-random.service` which can be enabled via "
         "`sudo systemctl enable --now gp-mac-random.service`.)"),

        ("The privacy claim in plain language",
         "MAC randomization prevents device-level tracking across WiFi networks. "
         "It does NOT hide your internet traffic (that's what the VPN tunnels are "
         "for), and it does NOT prevent the current network from seeing your "
         "activity while you're on it (the access point still sees everything not "
         "encrypted in flight).\n\n"
         "Think of it as removing your name tag. You're still visible; you just "
         "aren't labeled the same every time. Combined with a VPN and encrypted "
         "DNS, it's a solid layer of the privacy stack."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    def __init__(self):
        super().__init__("SEADEVIL", "seadevil", (700, 500))
        self.iface_widgets = {}
        self.originals = load_originals()
        self.last_randomized = None
        self.build_ui()
        self._save_originals_on_first_run()
        self.refresh_all()

    def build_ui(self):
        c = self.colors
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header
        header = self.make_header("SEADEVIL", "MAC Randomizer")
        root.pack_start(header, False, False, 0)

        # Scrollable content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(10)
        content.set_margin_bottom(10)

        for iface in PHYSICAL_IFACES:
            if not iface_exists(iface):
                continue
            card = self._build_iface_card(iface)
            content.pack_start(card, False, False, 0)

        scroll = self.make_scrolled(content)
        root.pack_start(scroll, True, True, 0)

        # Help button row
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(8)
        btn_bar.set_margin_end(8)
        btn_bar.set_margin_top(2)
        btn_bar.set_margin_bottom(2)
        btn_bar.pack_end(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)
        root.pack_start(btn_bar, False, False, 0)

        # Status bar
        self.status_bar = self.make_status_bar("Ready")
        root.pack_start(self.status_bar, False, False, 0)

    def _build_iface_card(self, iface):
        c = self.colors
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("gp-card")

        # Interface name
        name_label = self.make_label(iface.upper(), "gp-accent")
        card.pack_start(name_label, False, False, 0)

        # Current MAC row
        cur_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cur_title = self.make_label("Current MAC:", "gp-dim")
        cur_row.pack_start(cur_title, False, False, 0)
        cur_mac = self.make_label("loading...", "gp-accent")
        cur_row.pack_start(cur_mac, False, False, 0)
        card.pack_start(cur_row, False, False, 0)

        # Permanent MAC row
        perm_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        perm_title = self.make_label("Permanent:  ", "gp-dim")
        perm_row.pack_start(perm_title, False, False, 0)
        perm_mac = self.make_label("loading...", "gp-dim")
        perm_row.pack_start(perm_mac, False, False, 0)
        card.pack_start(perm_row, False, False, 0)

        # Status label
        status_label = self.make_label("", "gp-dim")
        card.pack_start(status_label, False, False, 2)

        # Buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_top(4)

        randomize_btn = self.make_button("Randomize", lambda b, i=iface: self.on_randomize(i), "gp-btn-primary")
        btn_row.pack_start(randomize_btn, False, False, 0)

        restore_btn = self.make_button("Restore Original", lambda b, i=iface: self.on_restore(i), "gp-btn")
        btn_row.pack_start(restore_btn, False, False, 0)

        card.pack_start(btn_row, False, False, 0)

        self.iface_widgets[iface] = {
            "cur_mac": cur_mac,
            "perm_mac": perm_mac,
            "status": status_label,
        }

        return card

    def _save_originals_on_first_run(self):
        """On first run, save the original MACs for each interface."""
        changed = False
        for iface in PHYSICAL_IFACES:
            if iface not in self.originals:
                mac = get_permanent_mac(iface) or get_current_mac(iface)
                if mac:
                    self.originals[iface] = mac
                    changed = True
        if changed:
            save_originals(self.originals)

    def refresh_all(self):
        for iface in PHYSICAL_IFACES:
            if iface in self.iface_widgets:
                self.run_async(lambda i=iface: self._get_macs(i), lambda r, i=iface: self._update_card(i, r))

    def _get_macs(self, iface):
        cur = get_current_mac(iface)
        perm = get_permanent_mac(iface) or self.originals.get(iface, "unknown")
        return cur, perm

    def _update_card(self, iface, result):
        if isinstance(result, Exception):
            return
        cur, perm = result
        w = self.iface_widgets.get(iface)
        if not w:
            return
        w["cur_mac"].set_text(cur or "unavailable")
        w["perm_mac"].set_text(perm or "unknown")

        # Check if MAC has been changed
        original = self.originals.get(iface)
        if cur and original and cur.lower() != original.lower():
            w["status"].set_text("MAC is randomized")
            w["status"].get_style_context().remove_class("gp-dim")
            w["status"].get_style_context().add_class("gp-success")
        elif cur and original and cur.lower() == original.lower():
            w["status"].set_text("MAC is original")
            w["status"].get_style_context().remove_class("gp-success")
            w["status"].get_style_context().add_class("gp-dim")
        else:
            w["status"].set_text("")

    def _iface_is_active_ap(self, iface):
        """True if iface is the running hostapd AP — randomizing kicks every LAN client."""
        if iface != "wlan0":
            return False
        _, _, rc = run_cmd(["systemctl", "is-active", "--quiet", "hostapd"])
        return rc == 0

    def _confirm_ap_randomize(self, iface):
        """Modal confirmation gate when about to disrupt the AP. Returns True to proceed."""
        dialog = Gtk.MessageDialog(
            parent=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Randomize {iface} MAC?",
        )
        dialog.format_secondary_text(
            f"{iface} is the active access point. Changing its MAC will disconnect "
            "every device on your LAN (laptops, phones, smart-home gear) until they "
            "rejoin the WiFi.\n\nTailscale (remote management) is unaffected. "
            "Continue?"
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Randomize anyway", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def on_randomize(self, iface):
        if self._iface_is_active_ap(iface):
            if not self._confirm_ap_randomize(iface):
                self.set_status(f"Randomize cancelled — {iface} is the AP")
                return
        new_mac = generate_random_mac()
        self.set_status(f"Randomizing {iface} to {new_mac}...")

        def do_randomize():
            # Bring down, set MAC, bring up
            _, err1, rc1 = run_cmd(["sudo", "ip", "link", "set", iface, "down"])
            if rc1 != 0:
                return False, f"Failed to bring down {iface}: {err1}"
            _, err2, rc2 = run_cmd(["sudo", "ip", "link", "set", iface, "address", new_mac])
            if rc2 != 0:
                # Try to bring it back up
                run_cmd(["sudo", "ip", "link", "set", iface, "up"])
                return False, f"Failed to set MAC on {iface}: {err2}"
            _, err3, rc3 = run_cmd(["sudo", "ip", "link", "set", iface, "up"])
            if rc3 != 0:
                return False, f"Failed to bring up {iface}: {err3}"
            return True, new_mac

        def on_done(result):
            if isinstance(result, Exception):
                self.set_status(f"Error: {result}")
                return
            success, msg = result
            if success:
                self.last_randomized = time.strftime("%H:%M:%S")
                self.set_status(f"{iface} MAC randomized to {msg}  |  {self.last_randomized}")
                self.refresh_all()
            else:
                self.set_status(f"Failed: {msg}")

        self.run_async(do_randomize, on_done)

    def on_restore(self, iface):
        original = self.originals.get(iface)
        if not original:
            self.set_status(f"No original MAC saved for {iface}")
            return

        self.set_status(f"Restoring {iface} to {original}...")

        def do_restore():
            _, err1, rc1 = run_cmd(["sudo", "ip", "link", "set", iface, "down"])
            if rc1 != 0:
                return False, f"Failed to bring down {iface}: {err1}"
            _, err2, rc2 = run_cmd(["sudo", "ip", "link", "set", iface, "address", original])
            if rc2 != 0:
                run_cmd(["sudo", "ip", "link", "set", iface, "up"])
                return False, f"Failed to restore MAC on {iface}: {err2}"
            _, err3, rc3 = run_cmd(["sudo", "ip", "link", "set", iface, "up"])
            if rc3 != 0:
                return False, f"Failed to bring up {iface}: {err3}"
            return True, original

        def on_done(result):
            if isinstance(result, Exception):
                self.set_status(f"Error: {result}")
                return
            success, msg = result
            if success:
                self.set_status(f"{iface} MAC restored to {msg}")
                self.refresh_all()
            else:
                self.set_status(f"Failed: {msg}")

        self.run_async(do_restore, on_done)


if __name__ == "__main__":
    app = SeadevilApp()
    app.run()
