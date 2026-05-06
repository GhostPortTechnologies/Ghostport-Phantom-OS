#!/usr/bin/env python3
"""
GhostPort Widget Library — mission control for desktop layout & theme.

Two functions only:
  1. Theme picker — 8 presets + custom hex; writes /etc/phantom/theme.json
     directly (instant in-app preview), then fires `pkexec gp-theme <hex>`
     for full system propagation (waybar / foot / labwc / greeter / SVG icons).
  2. Desktop icons — toggle which app icons appear on the desktop and launch
     them on demand. Uses ~/.config/ghostport/icon-positions.json hidden flag
     and SIGUSR1s gp-desktop-icons to live-reload.
"""

import json
import math
import os
import re
import signal
import subprocess
import sys

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gp_app_base import GhostPortApp, read_theme_color  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────

ICON_DIR = "/opt/phantom/desktop/icons"
ICON_POSITIONS_FILE = os.path.expanduser("~/.config/ghostport/icon-positions.json")
THEME_FILE = "/etc/phantom/theme.json"
DESKTOP_ICONS_PID_FILE = "/tmp/gp-desktop-icons.pid"

# Mirror of DESKTOP_APPS in gp-desktop-icons.py (excluding the library itself).
# (label, icon, command, subtitle).
DESKTOP_APPS = [
    ("Crow's Nest",   "gp-crowsnest.svg",  "python3 /opt/phantom/desktop/gp-crowsnest.py",     "Intrusion Detection"),
    ("Bulkhead",      "gp-bulkhead.svg",   "python3 /opt/phantom/desktop/gp-bulkhead.py",      "Firewall"),
    ("Dragnet",       "gp-dragnet.svg",    "python3 /opt/phantom/desktop/gp-dragnet.py",       "Packet Capture"),
    ("Anchor",        "gp-anchor.svg",     "python3 /opt/phantom/desktop/gp-anchor.py",        "Kill Switch"),
    ("Aether Box",    "gp-strongbox.svg",  "python3 /opt/phantom/desktop/gp-aetherbox.py",     "Encrypted Vault"),
    ("Tide Chart",    "gp-heatmap.svg",    "python3 /opt/phantom/desktop/gp-tidechart.py",     "Bandwidth Heatmap"),
    ("Sonar",         "gp-watchdog.svg",   "python3 /opt/phantom/desktop/gp-sonar.py",         "Rogue AP Scanner"),
    ("Crew Manifest", "gp-roster.svg",     "python3 /opt/phantom/desktop/gp-crewmanifest.py",  "Connected Clients"),
    ("Atlas",         "gp-atlas.svg",      "python3 /opt/phantom/desktop/gp-atlas.py",         "Network Topology"),
    ("Stonefish",     "gp-tripwire.svg",   "python3 /opt/phantom/desktop/gp-stonefish.py",     "ARP Guard"),
    ("Seadevil",      "gp-seadevil.svg",   "python3 /opt/phantom/desktop/gp-seadevil.py",      "MAC Randomizer"),
    ("Gangplank",     "gp-dock.svg",       "python3 /opt/phantom/desktop/gp-gangplank.py",     "USB Manager"),
    ("Sea Urchin",    "gp-vitals.svg",     "python3 /opt/phantom/desktop/gp-seaurchin.py",     "System Health"),
    ("Logbook",       "gp-logbook.svg",    "python3 /opt/phantom/desktop/gp-logbook.py",       "Event Log"),
    ("Quartermaster", "gp-audit.svg",      "python3 /opt/phantom/desktop/gp-quartermaster.py", "Security Scan"),
    ("Dashboard",     "gp-dashboard.png",  "brave-browser --app=https://localhost:4201",       "Web UI"),
    ("Brave",         "gp-brave.png",      "brave-browser",                                     "Web Browser"),
    ("Chamber",       "gp-chamber.svg",    "brave-browser --app=http://127.0.0.1:4242",        "AI Coordination"),
]

# Same eight presets the bash gp-theme menu offers, in the same order.
THEME_PRESETS = [
    ("Ghost Green", "39ff8f"),
    ("Cyber Blue",  "00d4ff"),
    ("Amber",       "ffbf00"),
    ("Neon Purple", "e040fb"),
    ("Red Alert",   "ff4444"),
    ("Gold",        "ffd700"),
    ("White",       "ffffff"),
    ("Rainbow",     "RAINBOW"),
]

HELP_SECTIONS = [
    ("What is the Widget Library?",
     "Mission control for your desktop. Pick an accent color to retheme the OS, "
     "and toggle which app icons live on the desktop for one-click access."),
    ("Theme picker",
     "Click any swatch to apply it instantly. Type a custom 6-digit hex (e.g. 00d4ff) "
     "and press Apply. Rainbow cycles hues over time.\n\n"
     "All apps and widgets repaint within a few seconds. The full system retheme "
     "(waybar, foot, window decorations, SVG icons, login screen) runs through "
     "gp-theme via pkexec — you may see a one-time auth prompt."),
    ("Desktop icons",
     "Each card represents an app. Toggle ON to show its icon on the desktop, "
     "OFF to hide it. Press Launch to open the app immediately without changing "
     "icon visibility."),
    ("Where things live",
     "Theme:           /etc/phantom/theme.json\n"
     "Icon visibility: ~/.config/ghostport/icon-positions.json (hidden flag)\n"
     "Live reload:     SIGUSR1 to /tmp/gp-desktop-icons.pid"),
]


# ── File / signal helpers ────────────────────────────────────────────

def load_icon_positions():
    try:
        with open(ICON_POSITIONS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_icon_positions(data):
    os.makedirs(os.path.dirname(ICON_POSITIONS_FILE), exist_ok=True)
    tmp = ICON_POSITIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, ICON_POSITIONS_FILE)


def signal_desktop_icons():
    try:
        with open(DESKTOP_ICONS_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        pass


def write_theme(value):
    """Write {"color": "#<value>"} to /etc/phantom/theme.json. Falls back to
    pkexec when the file is root-owned. Returns True on success."""
    payload = json.dumps({"color": f"#{value}"}) + "\n"
    try:
        with open(THEME_FILE, "w") as f:
            f.write(payload)
        return True
    except PermissionError:
        try:
            subprocess.run(
                ["pkexec", "tee", THEME_FILE],
                input=payload, text=True, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


def propagate_theme_async(hex_code):
    """Fire-and-forget full system retheme. RAINBOW is in-app only."""
    if hex_code == "RAINBOW":
        return
    try:
        subprocess.Popen(
            ["pkexec", "/usr/local/bin/gp-theme", hex_code],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError):
        pass


def is_valid_hex(s):
    return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", s.strip()))


# ── Library window ───────────────────────────────────────────────────

class WidgetLibrary(GhostPortApp):

    EXTRA_CSS = """
    .wl-section-title {
        font-family: monospace;
        font-size: 13px;
        font-weight: bold;
        letter-spacing: 1px;
    }
    .wl-swatch {
        padding: 4px;
        border-radius: 8px;
    }
    .wl-swatch:hover {
        background-color: rgba(255,255,255,0.04);
    }
    """

    def __init__(self):
        super().__init__("Widget Library", "widget-library", (980, 720))
        self._apply_css(self.EXTRA_CSS)
        self.icon_positions = load_icon_positions()
        self.swatch_areas = []  # list of (hex_code, DrawingArea)
        self.toggles = {}       # label -> Gtk.Switch
        self._build_ui()

    # ── UI construction ──

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header = self.make_header(
            title="Widget Library",
            subtitle="Theme · Desktop Icons",
        )
        header_row.pack_start(header, True, True, 0)
        help_btn = self.make_help_button(sections=HELP_SECTIONS)
        help_btn.set_margin_end(12)
        help_btn.set_margin_top(8)
        help_btn.set_valign(Gtk.Align.CENTER)
        header_row.pack_end(help_btn, False, False, 0)
        outer.pack_start(header_row, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.pack_start(scroll, True, True, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.set_margin_top(16)
        body.set_margin_bottom(16)
        scroll.add(body)

        body.pack_start(self._build_theme_section(), False, False, 0)
        body.pack_start(self._build_icons_section(), True, True, 0)

    def _section_title(self, text):
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        lbl.get_style_context().add_class("wl-section-title")
        lbl.get_style_context().add_class("gp-accent")
        return lbl

    def _section_sub(self, text):
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        lbl.get_style_context().add_class("gp-dim")
        lbl.set_margin_bottom(4)
        return lbl

    # ── Theme section ──

    def _build_theme_section(self):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        section.pack_start(self._section_title("THEME"), False, False, 0)
        section.pack_start(
            self._section_sub("Pick an accent color. Apps and widgets retheme within a few seconds."),
            False, False, 0,
        )

        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(8)
        flow.set_min_children_per_line(4)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_column_spacing(6)
        flow.set_row_spacing(6)
        flow.set_homogeneous(True)
        flow.set_margin_top(6)
        for name, code in THEME_PRESETS:
            flow.add(self._make_swatch(name, code))
        section.pack_start(flow, False, False, 0)

        custom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        custom.set_margin_top(10)

        custom_lbl = Gtk.Label(label="Custom hex:")
        custom_lbl.get_style_context().add_class("gp-dim")
        custom.pack_start(custom_lbl, False, False, 0)

        self.hex_entry = Gtk.Entry()
        self.hex_entry.set_placeholder_text("00d4ff")
        self.hex_entry.set_max_length(7)
        self.hex_entry.set_width_chars(10)
        self.hex_entry.connect("activate", lambda _e: self._apply_custom())
        custom.pack_start(self.hex_entry, False, False, 0)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.get_style_context().add_class("gp-btn-primary")
        apply_btn.connect("clicked", lambda _b: self._apply_custom())
        custom.pack_start(apply_btn, False, False, 0)

        self.current_lbl = Gtk.Label()
        self.current_lbl.set_halign(Gtk.Align.END)
        self.current_lbl.get_style_context().add_class("gp-dim")
        custom.pack_end(self.current_lbl, True, True, 0)
        self._refresh_current_label()

        section.pack_start(custom, False, False, 0)
        return section

    def _make_swatch(self, name, hex_code):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_tooltip_text(f"{name}" if hex_code == "RAINBOW" else f"{name}  #{hex_code}")
        btn.get_style_context().add_class("wl-swatch")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_halign(Gtk.Align.CENTER)

        area = Gtk.DrawingArea()
        area.set_size_request(56, 36)
        area.connect("draw", self._draw_swatch, hex_code)
        box.pack_start(area, False, False, 0)
        self.swatch_areas.append((hex_code, area))

        lbl = Gtk.Label(label=name)
        lbl.set_max_width_chars(12)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.get_style_context().add_class("gp-dim")
        box.pack_start(lbl, False, False, 0)

        btn.add(box)
        btn.connect("clicked", lambda _b, h=hex_code: self._apply_theme(h))
        return btn

    def _draw_swatch(self, area, cr, hex_code):
        alloc = area.get_allocation()
        w, h = alloc.width, alloc.height
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 4

        if hex_code == "RAINBOW":
            steps = 60
            for i in range(steps):
                hue = i / steps
                h6 = hue * 6
                ih = int(h6)
                f = h6 - ih
                if ih == 0:   r, g, b = 1.0, f, 0.0
                elif ih == 1: r, g, b = 1.0 - f, 1.0, 0.0
                elif ih == 2: r, g, b = 0.0, 1.0, f
                elif ih == 3: r, g, b = 0.0, 1.0 - f, 1.0
                elif ih == 4: r, g, b = f, 0.0, 1.0
                else:         r, g, b = 1.0, 0.0, 1.0 - f
                cr.set_source_rgb(r, g, b)
                start = i / steps * 2 * math.pi
                end = (i + 1) / steps * 2 * math.pi + 0.02
                cr.move_to(cx, cy)
                cr.arc(cx, cy, radius, start, end)
                cr.close_path()
                cr.fill()
        else:
            r = int(hex_code[0:2], 16) / 255
            g = int(hex_code[2:4], 16) / 255
            b = int(hex_code[4:6], 16) / 255
            cr.set_source_rgb(r, g, b)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.fill()

        # Selection ring on the active accent
        current = read_theme_color()
        is_current = (
            (hex_code == "RAINBOW" and current.upper() == "RAINBOW")
            or (hex_code != "RAINBOW" and current.lower() == hex_code.lower())
        )
        if is_current:
            cr.set_source_rgb(self.colors["r"] / 255, self.colors["g"] / 255, self.colors["b"] / 255)
            cr.set_line_width(2)
            cr.arc(cx, cy, radius + 2, 0, 2 * math.pi)
            cr.stroke()
        return False

    def _apply_custom(self):
        text = self.hex_entry.get_text().strip()
        if not is_valid_hex(text):
            self.show_error("Invalid hex", "Enter a 6-digit color (e.g. 00d4ff or #00d4ff).")
            return
        self._apply_theme(text.lstrip("#").lower())

    def _apply_theme(self, hex_code):
        if not write_theme(hex_code):
            self.show_error(
                "Theme write failed",
                f"Could not write {THEME_FILE}.",
                f"Try `gp-theme {hex_code}` from a terminal.",
            )
            return
        propagate_theme_async(hex_code)
        self._refresh_current_label()
        for _, area in self.swatch_areas:
            area.queue_draw()

    def _refresh_current_label(self):
        cur = read_theme_color()
        text = "Current: Rainbow" if cur.upper() == "RAINBOW" else f"Current: #{cur}"
        self.current_lbl.set_text(text)

    # ── Icons section ──

    def _build_icons_section(self):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        section.pack_start(self._section_title("DESKTOP ICONS"), False, False, 0)
        section.pack_start(
            self._section_sub("Toggle which icons appear on the desktop. Launch starts the app immediately."),
            False, False, 0,
        )

        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(1)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        flow.set_homogeneous(True)
        flow.set_margin_top(6)

        for label, icon, command, subtitle in DESKTOP_APPS:
            flow.add(self._make_icon_card(label, icon, command, subtitle))
        section.pack_start(flow, True, True, 0)
        return section

    def _make_icon_card(self, label, icon_file, command, subtitle):
        frame = Gtk.Frame()
        frame.get_style_context().add_class("gp-card")
        frame.set_size_request(210, -1)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_start(8)
        inner.set_margin_end(8)
        inner.set_margin_top(10)
        inner.set_margin_bottom(10)
        frame.add(inner)

        icon_path = os.path.join(ICON_DIR, icon_file)
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 56, 56, True)
            img = Gtk.Image.new_from_pixbuf(pix)
        except Exception:
            img = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        img.set_halign(Gtk.Align.CENTER)
        inner.pack_start(img, False, False, 0)

        name_lbl = Gtk.Label(label=label)
        name_lbl.set_halign(Gtk.Align.CENTER)
        name_lbl.get_style_context().add_class("gp-accent")
        inner.pack_start(name_lbl, False, False, 0)

        sub_lbl = Gtk.Label(label=subtitle)
        sub_lbl.set_halign(Gtk.Align.CENTER)
        sub_lbl.get_style_context().add_class("gp-dim")
        sub_lbl.set_max_width_chars(22)
        sub_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        inner.pack_start(sub_lbl, False, False, 0)

        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toggle_row.set_halign(Gtk.Align.CENTER)
        toggle_row.set_margin_top(6)
        sw = Gtk.Switch()
        sw.set_active(self._is_icon_visible(label))
        sw.connect("notify::active", self._on_toggle, label)
        toggle_row.pack_start(sw, False, False, 0)
        on_lbl = Gtk.Label(label="On Desktop")
        on_lbl.get_style_context().add_class("gp-dim")
        toggle_row.pack_start(on_lbl, False, False, 0)
        inner.pack_start(toggle_row, False, False, 0)

        launch_btn = Gtk.Button(label="Launch")
        launch_btn.get_style_context().add_class("gp-btn")
        launch_btn.connect("clicked", lambda _b, c=command: self._launch_app(c))
        inner.pack_start(launch_btn, False, False, 0)

        self.toggles[label] = sw
        return frame

    def _is_icon_visible(self, label):
        entry = self.icon_positions.get(label)
        if isinstance(entry, dict):
            return not entry.get("hidden", False)
        return True

    def _on_toggle(self, switch, _gparam, label):
        active = switch.get_active()
        # Re-read in case gp-desktop-icons updated positions while we were open.
        self.icon_positions = load_icon_positions()
        entry = self.icon_positions.get(label)
        if not isinstance(entry, dict):
            entry = {}
        if active:
            entry.pop("hidden", None)
        else:
            entry["hidden"] = True
        self.icon_positions[label] = entry
        save_icon_positions(self.icon_positions)
        signal_desktop_icons()

    def _launch_app(self, command):
        try:
            subprocess.Popen(
                command, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self.show_error("Launch failed", str(e))

    # ── Theme change hook ──

    def on_theme_changed(self):
        self._refresh_current_label()
        for _, area in self.swatch_areas:
            area.queue_draw()


# ── Main ─────────────────────────────────────────────────────────────

def main():
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        print(__doc__.strip())
        return
    app = WidgetLibrary()
    app.connect("destroy", lambda _w: Gtk.main_quit())
    app.run()


if __name__ == "__main__":
    main()
