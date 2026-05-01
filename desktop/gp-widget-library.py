#!/usr/bin/env python3
"""
GhostPort Widget Library — Full-screen gallery for browsing and placing desktop widgets.
Opens as a proper app window with preview cards, descriptions, and placement controls.
"""

try:
    import cairo
except ImportError:
    cairo = None

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import json
import os
import sys
import signal
import math
import subprocess
import time
import fcntl

# ── Config ───────────────────────────────────────────────────────────
LAYOUT_DIR = os.path.expanduser("~/.config/ghostport")
LAYOUT_FILE = os.path.join(LAYOUT_DIR, "widget-layout.json")
LOCK_FILE = "/tmp/gp-widget-library.lock"
PID_FILE = "/tmp/gp-widget-library.pid"

DEFAULT_LAYOUT = {
    "widgets": {
        "score":   {"enabled": False, "x": 50,  "y": 60,  "width": 250, "height": 300},
        "chamber": {"enabled": False, "x": 320, "y": 60,  "width": 380, "height": 500},
        "mode":    {"enabled": False, "x": 50,  "y": 380, "width": 280, "height": 200},
        "tunnel":  {"enabled": False, "x": 720, "y": 60,  "width": 280, "height": 220},
        "ads":     {"enabled": False, "x": 720, "y": 300, "width": 250, "height": 180},
        "clients": {"enabled": False, "x": 720, "y": 500, "width": 280, "height": 300},
        "theme":   {"enabled": False, "x": 350, "y": 400, "width": 260, "height": 280},
    }
}

# ── Widget Catalog ───────────────────────────────────────────────────

WIDGETS = [
    {
        "id": "score",
        "name": "Privacy Score",
        "icon": "\U0001f6e1\ufe0f",
        "category": "Monitor",
        "description": "Live privacy score with animated arc gauge. Shows your overall security posture with a color-coded breakdown of DNS, firewall, tunnel, and encryption status.",
        "preview_draw": "gauge",
        "size": "250 × 300",
    },
    {
        "id": "chamber",
        "name": "Chamber Chat",
        "icon": "\U0001f4ac",
        "category": "Communicate",
        "description": "Real-time chat feed from the Chamber coordination system. Watch AI instances collaborate, send messages, and monitor activity across rooms.",
        "preview_draw": "chat",
        "size": "380 × 500",
    },
    {
        "id": "mode",
        "name": "Mode Switcher",
        "icon": "\u26a1",
        "category": "Control",
        "description": "Quick-access mode control. See your current privacy mode at a glance and switch between ISP, ZeroTrust, DoubleHop, and Z-HOP with one click.",
        "preview_draw": "mode",
        "size": "280 × 200",
    },
    {
        "id": "tunnel",
        "name": "Tunnel Status",
        "icon": "\U0001f510",
        "category": "Monitor",
        "description": "Live status of your WireGuard tunnels (wg0 control plane, wg1 data plane) and Tailscale management connection. Shows data transfer and exit IP.",
        "preview_draw": "tunnel",
        "size": "280 × 220",
    },
    {
        "id": "ads",
        "name": "Ads Blocked",
        "icon": "\U0001f6ab",
        "category": "Monitor",
        "description": "Pi-hole ad blocking counter. Track how many ads and trackers have been blocked this session and all-time. Watch the number climb in real time.",
        "preview_draw": "ads",
        "size": "250 × 180",
    },
    {
        "id": "clients",
        "name": "Connected Clients",
        "icon": "\U0001f4f1",
        "category": "Monitor",
        "description": "See every device connected to your GhostPort access point. Shows hostname, IP address, and online status for each client on your network.",
        "preview_draw": "clients",
        "size": "280 × 300",
    },
    {
        "id": "theme",
        "name": "Theme Picker",
        "icon": "\U0001f3a8",
        "category": "Control",
        "description": "Change your desktop accent color. Choose from 8 presets, pick a custom color, or enable Rainbow cycling mode.",
        "preview_draw": "theme",
        "size": "260 × 280",
    },
]

# ── CSS ──────────────────────────────────────────────────────────────

CSS = """
.library-window {
    background-color: #06060a;
}

.library-header {
    background-color: rgba(15, 22, 15, 0.98);
    border-bottom: 1px solid rgba(68, 170, 255, 0.15);
    padding: 20px 32px 16px 32px;
}

.library-title {
    color: #44aaff;
    font-family: monospace;
    font-size: 22px;
    font-weight: bold;
}

.library-subtitle {
    color: #194060;
    font-family: monospace;
    font-size: 12px;
}

.library-grid {
    padding: 24px;
}

.widget-card {
    background-color: rgba(15, 22, 15, 0.95);
    border: 1px solid rgba(68, 170, 255, 0.12);
    border-radius: 16px;
    padding: 0;
}

.widget-card:hover {
    border-color: rgba(68, 170, 255, 0.35);
}

.card-preview {
    background-color: rgba(8, 12, 8, 0.9);
    border-radius: 16px 16px 0 0;
    min-height: 140px;
}

.card-body {
    padding: 16px 18px;
}

.card-category {
    color: #44aaff;
    font-family: monospace;
    font-size: 9px;
    font-weight: bold;
    letter-spacing: 2px;
}

.card-name {
    color: #e0ede0;
    font-family: monospace;
    font-size: 15px;
    font-weight: bold;
}

.card-desc {
    color: #708870;
    font-family: monospace;
    font-size: 10px;
}

.card-size {
    color: #506850;
    font-family: monospace;
    font-size: 9px;
}

.card-footer {
    padding: 0 18px 16px 18px;
}

.btn-add {
    background-color: rgba(68, 170, 255, 0.12);
    border: 1px solid rgba(68, 170, 255, 0.4);
    border-radius: 8px;
    color: #44aaff;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    padding: 8px 20px;
    min-height: 36px;
}

.btn-add:hover {
    background-color: rgba(68, 170, 255, 0.22);
    border-color: rgba(68, 170, 255, 0.7);
}

.btn-remove {
    background-color: rgba(255, 68, 68, 0.08);
    border: 1px solid rgba(255, 68, 68, 0.3);
    border-radius: 8px;
    color: #ff6b6b;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    padding: 8px 20px;
    min-height: 36px;
}

.btn-remove:hover {
    background-color: rgba(255, 68, 68, 0.18);
    border-color: rgba(255, 68, 68, 0.6);
}

.active-badge {
    background-color: rgba(68, 170, 255, 0.15);
    border: 1px solid rgba(68, 170, 255, 0.4);
    border-radius: 12px;
    color: #44aaff;
    font-family: monospace;
    font-size: 9px;
    font-weight: bold;
    padding: 3px 10px;
}

.inactive-badge {
    background-color: rgba(96, 120, 96, 0.1);
    border: 1px solid rgba(96, 120, 96, 0.2);
    border-radius: 12px;
    color: #506850;
    font-family: monospace;
    font-size: 9px;
    padding: 3px 10px;
}

.toolbar {
    background-color: rgba(15, 22, 15, 0.98);
    border-top: 1px solid rgba(68, 170, 255, 0.1);
    padding: 12px 32px;
}

.toolbar-label {
    color: #194060;
    font-family: monospace;
    font-size: 11px;
}

.toolbar-count {
    color: #44aaff;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
}

.btn-reset {
    background-color: transparent;
    border: 1px solid rgba(96, 120, 96, 0.3);
    border-radius: 6px;
    color: #194060;
    font-family: monospace;
    font-size: 10px;
    padding: 4px 12px;
}

.btn-reset:hover {
    border-color: rgba(255, 68, 68, 0.4);
    color: #ff6b6b;
}

.btn-close-lib {
    background-color: rgba(68, 170, 255, 0.1);
    border: 1px solid rgba(68, 170, 255, 0.3);
    border-radius: 8px;
    color: #44aaff;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    padding: 6px 20px;
}

.btn-close-lib:hover {
    background-color: rgba(68, 170, 255, 0.2);
    border-color: rgba(68, 170, 255, 0.6);
}

/* Scrollbar */
scrollbar {
    background-color: transparent;
}
scrollbar slider {
    background-color: rgba(68, 170, 255, 0.15);
    border-radius: 4px;
    min-width: 6px;
}
scrollbar slider:hover {
    background-color: rgba(68, 170, 255, 0.3);
}
"""

# ── Layout helpers ───────────────────────────────────────────────────

def load_layout():
    try:
        with open(LAYOUT_FILE, 'r') as f:
            data = json.load(f)
            for wid, wdef in DEFAULT_LAYOUT["widgets"].items():
                if wid not in data.get("widgets", {}):
                    data.setdefault("widgets", {})[wid] = dict(wdef)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_LAYOUT))


def save_layout(layout):
    os.makedirs(LAYOUT_DIR, exist_ok=True)
    tmp = LAYOUT_FILE + ".tmp"
    try:
        with open(tmp, 'w') as f:
            json.dump(layout, f, indent=2)
        os.replace(tmp, LAYOUT_FILE)
    except OSError:
        pass


def notify_engine():
    """Tell the running widget engine to reload layout."""
    try:
        with open("/tmp/gp-widgets.pid", 'r') as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGUSR2)
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        pass


_last_engine_start = 0

def engine_is_alive():
    """Check if widget engine is running."""
    try:
        with open("/tmp/gp-widgets.pid", 'r') as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        return False

def ensure_engine_running():
    """Start widget engine if not already running. Returns True if engine was already up."""
    global _last_engine_start
    if engine_is_alive():
        return True
    now = time.time()
    if now - _last_engine_start < 5:
        return False
    _last_engine_start = now
    subprocess.Popen(
        [os.path.expanduser("~/.local/bin/gp-widgets"), "start"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return False


# ── Preview Drawing ──────────────────────────────────────────────────

def draw_gauge_preview(widget, cr):
    """Draw a mini privacy score gauge."""
    alloc = widget.get_allocation()
    cx, cy = alloc.width / 2, alloc.height / 2 + 10
    radius = min(cx, cy) - 20

    # Background arc
    cr.set_line_width(6)
    cr.set_source_rgba(0.15, 0.25, 0.15, 0.5)
    cr.arc(cx, cy, radius, 0.7 * math.pi, 2.3 * math.pi)
    cr.stroke()

    # Score arc (show 73%)
    cr.set_source_rgba(0.224, 1.0, 0.56, 0.7)
    sweep = 1.6 * math.pi
    cr.set_line_width(6)
    if cairo:
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.arc(cx, cy, radius, 0.7 * math.pi, 0.7 * math.pi + 0.73 * sweep)
    cr.stroke()

    # Score text
    cr.select_font_face("monospace", 0, 1)  # FONT_WEIGHT_BOLD=1
    cr.set_font_size(28)
    cr.set_source_rgba(0.224, 1.0, 0.56, 0.8)
    ext = cr.text_extents("73")
    cr.move_to(cx - ext.width / 2, cy + ext.height / 3)
    cr.show_text("73")


def draw_chat_preview(widget, cr):
    """Draw mini chat messages."""
    alloc = widget.get_allocation()
    cr.select_font_face("monospace", 0, 0)
    cr.set_font_size(9)

    messages = [
        ("#44aaff", "pi-claude", "checking tunnel status..."),
        ("#194060", "ec2-claude", "wg0 up, wg1 up, all clear"),
        ("#44aaff", "desktop-user", "nice work team"),
    ]

    y = 35
    for color, user, text in messages:
        # Username
        r, g, b = int(color[1:3], 16)/255, int(color[3:5], 16)/255, int(color[5:7], 16)/255
        cr.set_source_rgba(r, g, b, 0.7)
        cr.move_to(20, y)
        cr.show_text(user)
        y += 14

        # Message
        cr.set_source_rgba(0.78, 0.84, 0.78, 0.5)
        cr.move_to(20, y)
        cr.show_text(text)
        y += 22


def draw_mode_preview(widget, cr):
    """Draw mode indicator."""
    alloc = widget.get_allocation()
    cx = alloc.width / 2

    # Mode dot
    cr.set_source_rgba(0.224, 1.0, 0.56, 0.7)
    cr.arc(cx - 50, 45, 6, 0, 2 * math.pi)
    cr.fill()

    # Mode name
    cr.select_font_face("monospace", 0, 1)
    cr.set_font_size(16)
    cr.set_source_rgba(0.224, 1.0, 0.56, 0.7)
    cr.move_to(cx - 38, 50)
    cr.show_text("DOUBLEHOP")

    # Mode buttons
    cr.set_font_size(8)
    modes = ["ISP", "ZTRUST", "DHOP", "ZHOP"]
    x_start = 20
    for i, m in enumerate(modes):
        x = x_start + i * 62
        cr.set_source_rgba(0.224, 1.0, 0.56, 0.15 if i != 2 else 0.3)
        cr.rectangle(x, 75, 55, 24)
        cr.fill()
        cr.set_source_rgba(0.224, 1.0, 0.56, 0.4 if i != 2 else 0.8)
        cr.rectangle(x, 75, 55, 24)
        cr.stroke()
        ext = cr.text_extents(m)
        cr.move_to(x + 27 - ext.width/2, 91)
        cr.show_text(m)


def draw_tunnel_preview(widget, cr):
    """Draw tunnel status indicators."""
    cr.select_font_face("monospace", 0, 0)
    cr.set_font_size(10)

    tunnels = [("WG0 Control", True), ("WG1 Data", True), ("Tailscale", True)]
    y = 35
    for name, up in tunnels:
        cr.set_source_rgba(0.78, 0.84, 0.78, 0.5)
        cr.move_to(20, y)
        cr.show_text(name)

        if up:
            cr.set_source_rgba(0.224, 1.0, 0.56, 0.7)
            cr.move_to(180, y)
            cr.show_text("\u25b2 UP")
        else:
            cr.set_source_rgba(1.0, 0.27, 0.27, 0.7)
            cr.move_to(180, y)
            cr.show_text("\u25bc DOWN")
        y += 28


def draw_ads_preview(widget, cr):
    """Draw ad block counter."""
    alloc = widget.get_allocation()
    cx = alloc.width / 2

    cr.select_font_face("monospace", 0, 1)
    cr.set_font_size(32)
    cr.set_source_rgba(0.224, 1.0, 0.56, 0.7)
    ext = cr.text_extents("1,247")
    cr.move_to(cx - ext.width / 2, 65)
    cr.show_text("1,247")

    cr.set_font_size(9)
    cr.set_source_rgba(0.38, 0.47, 0.38, 0.6)
    ext = cr.text_extents("BLOCKED THIS SESSION")
    cr.move_to(cx - ext.width / 2, 85)
    cr.show_text("BLOCKED THIS SESSION")


def draw_clients_preview(widget, cr):
    """Draw client list."""
    cr.select_font_face("monospace", 0, 0)
    cr.set_font_size(9)

    clients = [
        ("\u25cf", "iPhone-14", "192.168.50.101"),
        ("\u25cf", "MacBook-Pro", "192.168.50.102"),
        ("\u25cb", "Galaxy-S24", "192.168.50.105"),
    ]
    y = 35
    for dot, name, ip in clients:
        # Dot
        is_on = dot == "\u25cf"
        cr.set_source_rgba(0.224, 1.0, 0.56, 0.7) if is_on else cr.set_source_rgba(1.0, 0.27, 0.27, 0.5)
        cr.move_to(20, y)
        cr.show_text(dot)
        # Name
        cr.set_source_rgba(0.224, 1.0, 0.56, 0.6)
        cr.move_to(35, y)
        cr.show_text(name)
        # IP
        cr.set_source_rgba(0.38, 0.47, 0.38, 0.5)
        cr.move_to(35, y + 13)
        cr.show_text(ip)
        y += 32


def draw_theme_preview(widget, cr):
    """Draw theme picker preview with 8 preset color swatches."""
    import math
    colors = [
        (0.224, 1.0, 0.56),    # Ghost Green #39ff8f
        (0.0, 0.83, 1.0),     # Cyber Blue #00d4ff
        (1.0, 0.6, 0.27),     # Amber #ff9944
        (0.878, 0.251, 0.984),# Neon Purple #e040fb
        (1.0, 0.267, 0.267),  # Red Alert #ff4444
        (1.0, 0.843, 0.0),    # Gold #ffd700
        (1.0, 1.0, 1.0),      # White #ffffff
    ]
    # Draw 7 color dots in 2 rows (4 + 3)
    for i, (r, g, b) in enumerate(colors):
        row = i // 4
        col = i % 4
        cx = 30 + col * 55
        cy = 35 + row * 30
        cr.set_source_rgba(r, g, b, 0.8)
        cr.arc(cx, cy, 10, 0, 2 * math.pi)
        cr.fill()
    # Rainbow bar below
    bar_y = 85
    bar_w = 220
    bar_h = 16
    bar_x = 20
    for x in range(bar_w):
        hue = x / bar_w
        # Simple HSV to RGB
        h6 = hue * 6
        i_h = int(h6)
        f = h6 - i_h
        if i_h == 0: r, g, b = 1.0, f, 0
        elif i_h == 1: r, g, b = 1.0 - f, 1.0, 0
        elif i_h == 2: r, g, b = 0, 1.0, f
        elif i_h == 3: r, g, b = 0, 1.0 - f, 1.0
        elif i_h == 4: r, g, b = f, 0, 1.0
        else: r, g, b = 1.0, 0, 1.0 - f
        cr.set_source_rgba(r, g, b, 0.6)
        cr.rectangle(bar_x + x, bar_y, 1, bar_h)
        cr.fill()
    # Label
    cr.select_font_face("monospace", 0, 0)
    cr.set_font_size(8)
    cr.set_source_rgba(0.224, 1.0, 0.56, 0.4)
    cr.move_to(bar_x, bar_y + bar_h + 12)
    cr.show_text("RAINBOW")


PREVIEW_DRAWERS = {
    "gauge": draw_gauge_preview,
    "chat": draw_chat_preview,
    "mode": draw_mode_preview,
    "tunnel": draw_tunnel_preview,
    "ads": draw_ads_preview,
    "clients": draw_clients_preview,
    "theme": draw_theme_preview,
}


# ── Library App ──────────────────────────────────────────────────────

class WidgetLibrary:
    def __init__(self):
        self.layout = load_layout()
        self.buttons = {}  # wid -> (add_btn, remove_btn, badge)

        self._setup_css()
        self._build_ui()

    def _setup_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        screen = Gdk.Screen.get_default()
        if screen:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build_ui(self):
        self.window = Gtk.Window(title="GhostPort Widget Library")
        self.window.set_default_size(960, 700)
        self.window.get_style_context().add_class("library-window")
        self.window.connect("destroy", Gtk.main_quit)
        self.window.connect("key-press-event", self._on_key)

        # Transparency
        screen = Gdk.Screen.get_default()
        if screen:
            visual = screen.get_rgba_visual()
            if visual:
                self.window.set_visual(visual)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # ── Header ──
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.get_style_context().add_class("library-header")

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title = Gtk.Label(label="Widget Library")
        title.get_style_context().add_class("library-title")
        title.set_halign(Gtk.Align.START)
        title_row.pack_start(title, True, True, 0)

        close_btn = Gtk.Button(label="Done")
        close_btn.get_style_context().add_class("btn-close-lib")
        close_btn.connect("clicked", lambda b: self.window.close())
        title_row.pack_end(close_btn, False, False, 0)

        header.pack_start(title_row, False, False, 0)

        subtitle = Gtk.Label(label="Browse and place widgets on your desktop. Drag widgets by their title bar to reposition.")
        subtitle.get_style_context().add_class("library-subtitle")
        subtitle.set_halign(Gtk.Align.START)
        header.pack_start(subtitle, False, False, 0)

        main_box.pack_start(header, False, False, 0)

        # ── Scrollable Grid ──
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Use a FlowBox for responsive grid
        self.grid = Gtk.FlowBox()
        self.grid.set_valign(Gtk.Align.START)
        self.grid.set_max_children_per_line(3)
        self.grid.set_min_children_per_line(1)
        self.grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.grid.set_column_spacing(16)
        self.grid.set_row_spacing(16)
        self.grid.set_margin_start(24)
        self.grid.set_margin_end(24)
        self.grid.set_margin_top(24)
        self.grid.set_margin_bottom(24)
        self.grid.set_homogeneous(True)

        for w in WIDGETS:
            card = self._build_card(w)
            self.grid.add(card)

        scroll.add(self.grid)
        main_box.pack_start(scroll, True, True, 0)

        # ── Bottom Toolbar ──
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toolbar.get_style_context().add_class("toolbar")

        active_count = sum(1 for w in WIDGETS if self.layout["widgets"].get(w["id"], {}).get("enabled", False))
        self.count_label = Gtk.Label()
        self.count_label.get_style_context().add_class("toolbar-label")
        self._update_count_label()
        toolbar.pack_start(self.count_label, False, False, 0)

        spacer = Gtk.Box()
        toolbar.pack_start(spacer, True, True, 0)

        reset_btn = Gtk.Button(label="Remove All")
        reset_btn.get_style_context().add_class("btn-reset")
        reset_btn.connect("clicked", self._on_remove_all)
        toolbar.pack_end(reset_btn, False, False, 0)

        main_box.pack_start(toolbar, False, False, 0)

        self.window.add(main_box)

    def _build_card(self, widget_info):
        wid = widget_info["id"]
        is_enabled = self.layout["widgets"].get(wid, {}).get("enabled", False)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.get_style_context().add_class("widget-card")
        card.set_size_request(280, -1)

        # Preview area with Cairo drawing
        preview = Gtk.DrawingArea()
        preview.set_size_request(-1, 140)
        preview.get_style_context().add_class("card-preview")
        drawer_name = widget_info.get("preview_draw", "")
        drawer = PREVIEW_DRAWERS.get(drawer_name)
        if drawer:
            preview.connect("draw", drawer)
        card.pack_start(preview, False, False, 0)

        # Card body
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.get_style_context().add_class("card-body")

        # Category + badge row
        cat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cat = Gtk.Label(label=widget_info["category"].upper())
        cat.get_style_context().add_class("card-category")
        cat.set_halign(Gtk.Align.START)
        cat_row.pack_start(cat, False, False, 0)

        badge = Gtk.Label(label="ACTIVE" if is_enabled else "INACTIVE")
        badge.get_style_context().add_class("active-badge" if is_enabled else "inactive-badge")
        cat_row.pack_end(badge, False, False, 0)
        body.pack_start(cat_row, False, False, 0)

        # Name
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Label(label=widget_info["icon"])
        name_row.pack_start(icon, False, False, 0)
        name = Gtk.Label(label=widget_info["name"])
        name.get_style_context().add_class("card-name")
        name.set_halign(Gtk.Align.START)
        name_row.pack_start(name, True, True, 0)
        body.pack_start(name_row, False, False, 0)

        # Description
        desc = Gtk.Label(label=widget_info["description"])
        desc.get_style_context().add_class("card-desc")
        desc.set_halign(Gtk.Align.START)
        desc.set_line_wrap(True)
        desc.set_line_wrap_mode(Pango.WrapMode.WORD)
        desc.set_max_width_chars(38)
        desc.set_xalign(0)
        body.pack_start(desc, False, False, 0)

        # Size info
        size_lbl = Gtk.Label(label=f"Size: {widget_info['size']}")
        size_lbl.get_style_context().add_class("card-size")
        size_lbl.set_halign(Gtk.Align.START)
        body.pack_start(size_lbl, False, False, 0)

        card.pack_start(body, True, True, 0)

        # Footer with button
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.get_style_context().add_class("card-footer")

        add_btn = Gtk.Button(label="Add to Desktop")
        add_btn.get_style_context().add_class("btn-add")
        add_btn.connect("clicked", self._on_add, wid)

        remove_btn = Gtk.Button(label="Remove")
        remove_btn.get_style_context().add_class("btn-remove")
        remove_btn.connect("clicked", self._on_remove, wid)

        if is_enabled:
            add_btn.hide()
            footer.pack_start(remove_btn, True, True, 0)
            footer.pack_start(add_btn, True, True, 0)
        else:
            remove_btn.hide()
            footer.pack_start(add_btn, True, True, 0)
            footer.pack_start(remove_btn, True, True, 0)

        card.pack_start(footer, False, False, 0)

        self.buttons[wid] = (add_btn, remove_btn, badge)
        return card

    def _on_add(self, btn, wid):
        self.layout["widgets"].setdefault(wid, {})["enabled"] = True
        save_layout(self.layout)
        was_running = ensure_engine_running()
        if was_running:
            notify_engine()
        self._update_card_state(wid, True)
        self._update_count_label()

    def _on_remove(self, btn, wid):
        self.layout["widgets"].setdefault(wid, {})["enabled"] = False
        save_layout(self.layout)
        self._signal_engine_reload()
        self._update_card_state(wid, False)
        self._update_count_label()

    def _signal_engine_reload(self):
        """Send SIGUSR2 to widget engine to reload layout."""
        notify_engine()
        return False

    def _on_remove_all(self, btn):
        for w in WIDGETS:
            self.layout["widgets"].setdefault(w["id"], {})["enabled"] = False
            self._update_card_state(w["id"], False)
        save_layout(self.layout)
        notify_engine()
        self._update_count_label()

    def _update_card_state(self, wid, enabled):
        if wid not in self.buttons:
            return
        add_btn, remove_btn, badge = self.buttons[wid]

        if enabled:
            add_btn.hide()
            remove_btn.show()
            badge.set_text("ACTIVE")
            ctx = badge.get_style_context()
            ctx.remove_class("inactive-badge")
            ctx.add_class("active-badge")
        else:
            remove_btn.hide()
            add_btn.show()
            badge.set_text("INACTIVE")
            ctx = badge.get_style_context()
            ctx.remove_class("active-badge")
            ctx.add_class("inactive-badge")

    def _update_count_label(self):
        active = sum(1 for w in WIDGETS if self.layout["widgets"].get(w["id"], {}).get("enabled", False))
        self.count_label.set_markup(
            f'<span foreground="#44aaff" font_weight="bold">{active}</span>'
            f'<span foreground="#194060"> of {len(WIDGETS)} widgets on desktop</span>'
        )

    def _on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.close()
            return True
        return False

    def run(self):
        self.window.show_all()
        # Fix visibility after show_all
        for wid in self.buttons:
            enabled = self.layout["widgets"].get(wid, {}).get("enabled", False)
            add_btn, remove_btn, badge = self.buttons[wid]
            if enabled:
                add_btn.hide()
                remove_btn.show()
            else:
                remove_btn.hide()
                add_btn.show()
        Gtk.main()


# ── Main ─────────────────────────────────────────────────────────────

def main():
    lock_fd = None
    # Clean stale lock files from crashed instances
    try:
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        os.kill(old_pid, 0)
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        for stale in (LOCK_FILE, PID_FILE):
            try:
                os.unlink(stale)
            except OSError:
                pass

    # Single instance lock
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except (IOError, OSError):
        # Already running — kill old and retry
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, signal.SIGTERM)
        except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
            pass
        time.sleep(0.3)
        if lock_fd:
            lock_fd.close()
        try:
            lock_fd = open(LOCK_FILE, 'w')
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fd.write(str(os.getpid()))
            lock_fd.flush()
        except (IOError, OSError):
            print("[widget-library] Could not acquire lock", file=sys.stderr)
            sys.exit(1)

    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    signal.signal(signal.SIGTERM, lambda s, f: Gtk.main_quit())

    app = WidgetLibrary()
    app.run()

    # Cleanup
    try:
        os.unlink(LOCK_FILE)
        os.unlink(PID_FILE)
    except OSError:
        pass
    if lock_fd:
        lock_fd.close()


if __name__ == "__main__":
    main()
