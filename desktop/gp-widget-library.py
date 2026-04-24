#!/usr/bin/env python3
"""GhostPort Widget Library — rebuilt 2026-04-21.

Window (960x700) that lets the user toggle floating desktop widgets AND
interact with each widget's engine inline via a per-widget detail view.

Theme-aware from launch: reads /etc/phantom/theme.json, polls every 3s,
re-applies CSS on change. No Cairo preview drawings — just styled cards
with the widget emoji, name, category, description, and a toggle button.
Detail views let the user use the widget's functionality without adding
it to the desktop first.

Interface contract with gp-widgets.py engine (unchanged from pre-nuke):
  - widget-layout.json schema: {"widgets": {<id>: {enabled, x, y, width, height}}}
  - /tmp/gp-widgets.pid carries the engine PID
  - SIGUSR2 to that PID tells the engine to re-read the layout
  - ~/.local/bin/gp-widgets start launches the engine when it isn't running
"""

import datetime
import fcntl
import json
import os
import signal
import subprocess
import sys
import time

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

# ── Config ───────────────────────────────────────────────────────────

LAYOUT_DIR = os.path.expanduser("~/.config/phantom")
LAYOUT_FILE = os.path.join(LAYOUT_DIR, "widget-layout.json")
LOCK_FILE = "/tmp/gp-widget-library.lock"
PID_FILE = "/tmp/gp-widget-library.pid"
ENGINE_PID_FILE = "/tmp/gp-widgets.pid"
ENGINE_LAUNCHER = os.path.expanduser("~/.local/bin/gp-widgets")
THEME_FILE = "/etc/phantom/theme.json"
ADS_TALLY_FILE = "/etc/phantom/ads-tally.json"
DEFAULT_ACCENT_HEX = "39ff8f"

DEFAULT_LAYOUT = {
    "widgets": {
        "score": {"enabled": False, "x": 100,  "y": 100, "width": 250, "height": 300},
        "ads":   {"enabled": False, "x": 400,  "y": 100, "width": 250, "height": 180},
        "theme": {"enabled": False, "x": 700,  "y": 100, "width": 260, "height": 280},
    }
}

# Only the 3 widgets gp-widgets.py::WIDGET_CLASSES still registers.
WIDGETS = [
    {
        "id": "score",
        "name": "Privacy Score",
        "emoji": "\U0001f6e1\ufe0f",        # 🛡️
        "category": "Monitor",
        "description": "Live privacy score with animated arc gauge. At a "
                       "glance view of your DNS, firewall, tunnel, and "
                       "encryption posture, color-coded 0-100.",
        "size": "250 x 300",
    },
    {
        "id": "ads",
        "name": "Ads Blocked",
        "emoji": "\U0001f6ab",               # 🚫
        "category": "Monitor",
        "description": "Pi-hole ad and tracker block counter. Watch the "
                       "number tick up as junk requests from your devices "
                       "get refused.",
        "size": "250 x 180",
    },
    {
        "id": "theme",
        "name": "Theme Picker",
        "emoji": "\U0001f3a8",               # 🎨
        "category": "Control",
        "description": "Pick your GhostPort accent color from eight presets, "
                       "dial in a custom hex, or toggle rainbow cycle mode. "
                       "The rest of the OS recolors within seconds.",
        "size": "260 x 280",
    },
]

THEME_PRESETS = [
    ("#39ff8f", "Ghost Green"),
    ("#00d4ff", "Cyber Blue"),
    ("#ffbf00", "Amber"),
    ("#e040fb", "Neon Purple"),
    ("#ff4444", "Red Alert"),
    ("#ffd700", "Gold"),
    ("#ffffff", "White"),
]


# ── Theme helpers ────────────────────────────────────────────────────

def _accent_hex():
    try:
        with open(THEME_FILE) as f:
            c = str(json.load(f).get("color", "")).lstrip("#").lower()
        if len(c) == 6 and all(ch in "0123456789abcdef" for ch in c):
            return c
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return DEFAULT_ACCENT_HEX


def _dim_hex_from(accent_hex):
    r = int(accent_hex[0:2], 16)
    g = int(accent_hex[2:4], 16)
    b = int(accent_hex[4:6], 16)
    return f"{int(r * 0.35):02x}{int(g * 0.35):02x}{int(b * 0.35):02x}"


def _accent_rgb_str(accent_hex):
    r = int(accent_hex[0:2], 16)
    g = int(accent_hex[2:4], 16)
    b = int(accent_hex[4:6], 16)
    return f"{r}, {g}, {b}"


CSS_TEMPLATE = """
.library-window { background-color: #06060a; }

.library-header {
    background-color: rgba(12, 16, 12, 0.98);
    padding: 14px 24px 10px 24px;
    border-bottom: 1px solid rgba(__ACCENT_RGB__, 0.15);
}
.library-title {
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 20px;
    font-weight: bold;
    letter-spacing: 2px;
}
.library-subtitle {
    color: #__ACCENT_DIM_HEX__;
    font-family: monospace;
    font-size: 11px;
}

.status-banner {
    background-color: rgba(__ACCENT_RGB__, 0.06);
    border-bottom: 1px solid rgba(__ACCENT_RGB__, 0.12);
    padding: 8px 24px;
}
.status-count {
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}

.card-grid { padding: 24px; }

.widget-card {
    background-color: rgba(15, 20, 15, 0.95);
    border: 1px solid rgba(__ACCENT_RGB__, 0.18);
    border-radius: 14px;
    padding: 18px 20px;
}
.widget-card:hover, .widget-card:focus {
    border-color: rgba(__ACCENT_RGB__, 0.55);
    background-color: rgba(20, 26, 20, 0.98);
}

.card-emoji { font-size: 42px; color: #__ACCENT_HEX__; }
.card-name { color: #e0ede0; font-family: monospace; font-size: 15px; font-weight: bold; }
.card-category { color: #__ACCENT_HEX__; font-family: monospace; font-size: 9px; font-weight: bold; letter-spacing: 2px; }
.card-desc { color: #a0b0a0; font-family: sans-serif; font-size: 11px; }
.card-size { color: #__ACCENT_DIM_HEX__; font-family: monospace; font-size: 9px; letter-spacing: 1px; }

.btn-add, .btn-add:focus {
    background-color: rgba(__ACCENT_RGB__, 0.14);
    border: 1px solid rgba(__ACCENT_RGB__, 0.5);
    border-radius: 8px;
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    padding: 8px 14px;
    min-height: 32px;
}
.btn-add:hover {
    background-color: rgba(__ACCENT_RGB__, 0.24);
    border-color: rgba(__ACCENT_RGB__, 0.8);
}

.btn-remove, .btn-remove:focus {
    background-color: rgba(255, 85, 85, 0.10);
    border: 1px solid rgba(255, 85, 85, 0.45);
    border-radius: 8px;
    color: #ff7a7a;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    padding: 8px 14px;
    min-height: 32px;
}
.btn-remove:hover {
    background-color: rgba(255, 85, 85, 0.22);
    border-color: rgba(255, 85, 85, 0.75);
}

.badge-on {
    background-color: rgba(__ACCENT_RGB__, 0.18);
    border: 1px solid rgba(__ACCENT_RGB__, 0.5);
    border-radius: 10px;
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 9px;
    font-weight: bold;
    padding: 2px 10px;
    letter-spacing: 1px;
}
.badge-off {
    background-color: rgba(100, 110, 100, 0.12);
    border: 1px solid rgba(100, 110, 100, 0.25);
    border-radius: 10px;
    color: #8a998a;
    font-family: monospace;
    font-size: 9px;
    padding: 2px 10px;
    letter-spacing: 1px;
}

.toolbar {
    background-color: rgba(12, 16, 12, 0.98);
    border-top: 1px solid rgba(__ACCENT_RGB__, 0.12);
    padding: 10px 24px;
}

.btn-neutral, .btn-neutral:focus {
    background-color: transparent;
    border: 1px solid rgba(__ACCENT_RGB__, 0.3);
    border-radius: 6px;
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 10px;
    padding: 5px 12px;
}
.btn-neutral:hover {
    border-color: rgba(__ACCENT_RGB__, 0.7);
    background-color: rgba(__ACCENT_RGB__, 0.08);
}

scrollbar { background-color: transparent; }
scrollbar slider {
    background-color: rgba(__ACCENT_RGB__, 0.2);
    border-radius: 4px;
    min-width: 6px;
}
scrollbar slider:hover { background-color: rgba(__ACCENT_RGB__, 0.4); }

/* Detail-view styles */
.detail-header {
    background-color: rgba(12, 16, 12, 0.98);
    border-bottom: 1px solid rgba(__ACCENT_RGB__, 0.15);
    padding: 12px 24px;
}
.detail-title {
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 18px;
    font-weight: bold;
    letter-spacing: 2px;
}
.detail-body { padding: 24px; }
.big-number {
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 48px;
    font-weight: bold;
}
.big-number-sub {
    color: #__ACCENT_DIM_HEX__;
    font-family: monospace;
    font-size: 13px;
    letter-spacing: 1px;
}
.preset-swatch {
    background-color: rgba(20, 24, 20, 0.95);
    border: 1px solid rgba(__ACCENT_RGB__, 0.25);
    border-radius: 10px;
    padding: 10px 14px;
    min-height: 44px;
}
.preset-swatch:hover {
    border-color: rgba(__ACCENT_RGB__, 0.6);
    background-color: rgba(25, 32, 25, 0.98);
}
.preset-name { color: #e0ede0; font-family: monospace; font-size: 11px; }
.current-hex {
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 16px;
    font-weight: bold;
    letter-spacing: 2px;
}
.section-head {
    color: #__ACCENT_DIM_HEX__;
    font-family: monospace;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 2px;
}
.stat-row { padding: 6px 0; }
.stat-key { color: #a0b0a0; font-family: monospace; font-size: 11px; }
.stat-val {
    color: #__ACCENT_HEX__;
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}
"""


def themed_css(accent_hex):
    return (CSS_TEMPLATE
            .replace("__ACCENT_HEX__", accent_hex)
            .replace("__ACCENT_DIM_HEX__", _dim_hex_from(accent_hex))
            .replace("__ACCENT_RGB__", _accent_rgb_str(accent_hex)))


# ── Layout persistence ───────────────────────────────────────────────

def load_layout():
    try:
        with open(LAYOUT_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = json.loads(json.dumps(DEFAULT_LAYOUT))
    data.setdefault("widgets", {})
    for w in WIDGETS:
        defaults = DEFAULT_LAYOUT["widgets"][w["id"]]
        slot = data["widgets"].setdefault(w["id"], {})
        for k, v in defaults.items():
            slot.setdefault(k, v)
    return data


def save_layout(layout):
    os.makedirs(LAYOUT_DIR, exist_ok=True)
    tmp = LAYOUT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(layout, f, indent=2)
    os.replace(tmp, LAYOUT_FILE)


# ── Engine coordination ──────────────────────────────────────────────

def engine_alive():
    try:
        with open(ENGINE_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        return False


def notify_engine():
    try:
        with open(ENGINE_PID_FILE) as f:
            os.kill(int(f.read().strip()), signal.SIGUSR2)
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        pass


_last_start = 0.0


def ensure_engine_running():
    global _last_start
    if engine_alive():
        return True
    now = time.time()
    if now - _last_start < 5:
        return False
    _last_start = now
    try:
        subprocess.Popen(
            [ENGINE_LAUNCHER, "start"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
    return False


# ── Help dialog ──────────────────────────────────────────────────────

HELP_SECTIONS = [
    ("What are widgets?",
     "Widgets are small, always-visible panels that sit directly on your "
     "desktop. They show live information at a glance — your privacy score, "
     "ads blocked, current theme — without needing to open any app."),
    ("How do I add one?",
     "Click the Add to Desktop button on any card below. The widget appears "
     "in the top-left corner of your screen. Drag it by its title bar to "
     "wherever you want."),
    ("How do the Open buttons work?",
     "The Open → button on each card switches to that widget's interactive "
     "view INSIDE this window — you can use the widget without adding it to "
     "the desktop. Great for one-off interactions (like changing theme)."),
    ("How do I move a widget on the desktop?",
     "Click and hold the widget's top title bar, then drag it anywhere. "
     "Position saves automatically. If a widget ends up off-screen, use "
     "Reset Positions at the bottom of this window."),
    ("How do I remove one?",
     "Click Remove on the card, or close its title-bar X on the widget "
     "itself. Either way the widget disappears."),
    ("Keyboard shortcuts",
     "Tab / Shift+Tab — move between cards.\n"
     "Enter or Space — toggle the focused card's widget.\n"
     "Esc — go back from a detail view, or close the window.\n"
     "Ctrl+Q — close from anywhere.\n"
     "Super + W — reopen this Widget Library."),
    ("Why only three widgets?",
     "We trimmed the catalog to the three that do something unique. Mode, "
     "tunnel status, connected-clients, and chamber were all duplicated by "
     "the top bar, right-click menu, or dedicated apps."),
]


# ── Library window ───────────────────────────────────────────────────

class Library:
    def __init__(self):
        self.layout = load_layout()
        self.accent = _accent_hex()
        self.cards = {}
        self._css_provider = None

        self.window = Gtk.Window(title="GhostPort Widget Library")
        self.window.set_default_size(960, 700)
        self.window.set_resizable(True)
        self.window.connect("destroy", self._on_destroy)
        self.window.connect("key-press-event", self._on_key)
        self.window.get_style_context().add_class("library-window")

        self._apply_css()
        self._build_ui()
        GLib.timeout_add_seconds(3, self._poll_theme)
        self.window.show_all()

    # ── CSS ─────────────────────────────────────────────────────────

    def _apply_css(self):
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(themed_css(self.accent).encode())
        screen = Gdk.Screen.get_default()
        if screen:
            Gtk.StyleContext.add_provider_for_screen(
                screen, self._css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _poll_theme(self):
        current = _accent_hex()
        if current != self.accent:
            self.accent = current
            self._css_provider.load_from_data(themed_css(current).encode())
            self._update_status()
        return True

    # ── UI build ────────────────────────────────────────────────────

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(root)

        self._header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._header.get_style_context().add_class("library-header")
        title = Gtk.Label(label="WIDGET LIBRARY")
        title.set_halign(Gtk.Align.START)
        title.get_style_context().add_class("library-title")
        self._header.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(
            label="Toggle the floating panels you want on your desktop.")
        subtitle.set_halign(Gtk.Align.START)
        subtitle.get_style_context().add_class("library-subtitle")
        self._header.pack_start(subtitle, False, False, 0)
        root.pack_start(self._header, False, False, 0)

        self._status_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._status_banner.get_style_context().add_class("status-banner")
        self.count_label = Gtk.Label()
        self.count_label.set_halign(Gtk.Align.START)
        self.count_label.get_style_context().add_class("status-count")
        self._status_banner.pack_start(self.count_label, True, True, 0)
        help_btn = Gtk.Button(label="? Help")
        help_btn.get_style_context().add_class("btn-neutral")
        help_btn.set_tooltip_text("Explain how widgets work (keyboard shortcuts too)")
        help_btn.connect("clicked", self._on_help)
        self._status_banner.pack_end(help_btn, False, False, 0)
        root.pack_start(self._status_banner, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(220)
        self.stack.set_vexpand(True)
        root.pack_start(self.stack, True, True, 0)

        cards_scroll = Gtk.ScrolledWindow()
        cards_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        grid_box.get_style_context().add_class("card-grid")
        grid_box.set_halign(Gtk.Align.CENTER)
        grid_box.set_valign(Gtk.Align.START)
        cards_scroll.add(grid_box)
        for w in WIDGETS:
            grid_box.pack_start(self._build_card(w), False, False, 0)
        self.stack.add_named(cards_scroll, "cards")

        for w in WIDGETS:
            placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.stack.add_named(placeholder, f"detail-{w['id']}")

        self._toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._toolbar.get_style_context().add_class("toolbar")
        reset_btn = Gtk.Button(label="Reset Positions")
        reset_btn.get_style_context().add_class("btn-neutral")
        reset_btn.set_tooltip_text(
            "Move every widget back to its default spot. "
            "Useful if one has drifted off-screen.")
        reset_btn.connect("clicked", self._on_reset_positions)
        self._toolbar.pack_start(reset_btn, False, False, 0)
        close_btn = Gtk.Button(label="Close")
        close_btn.get_style_context().add_class("btn-neutral")
        close_btn.set_tooltip_text("Close this window (Esc)")
        close_btn.connect("clicked", lambda _b: self.window.close())
        self._toolbar.pack_end(close_btn, False, False, 0)
        root.pack_start(self._toolbar, False, False, 0)

        self._update_status()

    def _show_cards(self):
        self.stack.set_visible_child_name("cards")
        self._header.show()
        self._status_banner.show()
        self._toolbar.show()

    def _show_detail(self, wid):
        page_name = f"detail-{wid}"
        page = self.stack.get_child_by_name(page_name)
        if page is None:
            return
        self.stack.remove(page)
        fresh = self._build_detail(wid)
        self.stack.add_named(fresh, page_name)
        fresh.show_all()
        self.stack.set_visible_child_name(page_name)
        self._header.hide()
        self._status_banner.hide()
        self._toolbar.hide()

    def _build_card(self, w):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("widget-card")
        card.set_size_request(280, -1)
        card.set_can_focus(True)
        card.connect("key-press-event", self._on_card_key, w["id"])

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        emoji = Gtk.Label(label=w["emoji"])
        emoji.get_style_context().add_class("card-emoji")
        emoji.set_halign(Gtk.Align.START)
        head.pack_start(emoji, False, False, 0)
        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_box.set_valign(Gtk.Align.CENTER)
        name_lbl = Gtk.Label(label=w["name"])
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.get_style_context().add_class("card-name")
        name_box.pack_start(name_lbl, False, False, 0)
        cat_lbl = Gtk.Label(label=w["category"].upper())
        cat_lbl.set_halign(Gtk.Align.START)
        cat_lbl.get_style_context().add_class("card-category")
        name_box.pack_start(cat_lbl, False, False, 0)
        head.pack_start(name_box, True, True, 0)
        card.pack_start(head, False, False, 0)

        desc = Gtk.Label(label=w["description"])
        desc.set_xalign(0.0)
        desc.set_line_wrap(True)
        desc.set_max_width_chars(34)
        desc.get_style_context().add_class("card-desc")
        desc.set_tooltip_text(w["description"])
        card.pack_start(desc, False, False, 2)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        size_lbl = Gtk.Label(label=w["size"])
        size_lbl.set_halign(Gtk.Align.START)
        size_lbl.get_style_context().add_class("card-size")
        meta.pack_start(size_lbl, True, True, 0)
        badge = Gtk.Label()
        badge.set_halign(Gtk.Align.END)
        meta.pack_end(badge, False, False, 0)
        card.pack_start(meta, False, False, 0)

        open_btn = Gtk.Button(label="Open \u2192")
        open_btn.get_style_context().add_class("btn-neutral")
        open_btn.set_tooltip_text(
            f"Open {w['name']} right here — use it inside the library "
            "without adding it to the desktop.")
        open_btn.connect("clicked", lambda _b, wid=w["id"]: self._show_detail(wid))
        card.pack_start(open_btn, False, False, 2)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_btn = Gtk.Button(label="+ Add to Desktop")
        add_btn.get_style_context().add_class("btn-add")
        add_btn.set_tooltip_text(
            f"Show the {w['name']} widget on the desktop. "
            "Drag its title bar to reposition.")
        add_btn.connect("clicked", self._on_add, w["id"])
        btn_row.pack_start(add_btn, True, True, 0)
        rem_btn = Gtk.Button(label="Remove")
        rem_btn.get_style_context().add_class("btn-remove")
        rem_btn.set_tooltip_text(
            f"Hide the {w['name']} widget. Its position is remembered.")
        rem_btn.connect("clicked", self._on_remove, w["id"])
        rem_btn.set_no_show_all(True)
        btn_row.pack_start(rem_btn, True, True, 0)
        card.pack_start(btn_row, False, False, 4)

        self.cards[w["id"]] = (card, add_btn, rem_btn, badge)
        self._refresh_card(w["id"])
        return card

    # ── State ───────────────────────────────────────────────────────

    def _is_enabled(self, wid):
        return bool(self.layout["widgets"].get(wid, {}).get("enabled", False))

    def _refresh_card(self, wid):
        if wid not in self.cards:
            return
        _card, add_btn, rem_btn, badge = self.cards[wid]
        if self._is_enabled(wid):
            add_btn.hide()
            rem_btn.show()
            badge.set_text("ON DESKTOP")
            ctx = badge.get_style_context()
            ctx.remove_class("badge-off")
            ctx.add_class("badge-on")
        else:
            rem_btn.hide()
            add_btn.show()
            badge.set_text("OFF")
            ctx = badge.get_style_context()
            ctx.remove_class("badge-on")
            ctx.add_class("badge-off")

    def _update_status(self):
        active = sum(1 for w in WIDGETS if self._is_enabled(w["id"]))
        total = len(WIDGETS)
        self.count_label.set_markup(
            f'<span font_family="monospace" font_weight="bold">{active}</span>'
            f' of {total} widgets on desktop')

    # ── Actions ─────────────────────────────────────────────────────

    def _toggle(self, wid, enabled):
        self.layout["widgets"].setdefault(wid, {})["enabled"] = enabled
        save_layout(self.layout)
        was_running = ensure_engine_running()
        if was_running:
            notify_engine()
        self._refresh_card(wid)
        self._update_status()

    def _on_add(self, _btn, wid):
        self._toggle(wid, True)

    def _on_remove(self, _btn, wid):
        self._toggle(wid, False)

    def _on_reset_positions(self, _btn):
        for w in WIDGETS:
            wid = w["id"]
            defaults = DEFAULT_LAYOUT["widgets"][wid]
            slot = self.layout["widgets"].setdefault(wid, {})
            slot["x"] = defaults["x"]
            slot["y"] = defaults["y"]
        save_layout(self.layout)
        if ensure_engine_running():
            notify_engine()

    def _on_help(self, _btn):
        dlg = Gtk.Dialog(title="Widget Library — Help",
                         transient_for=self.window, modal=True)
        dlg.set_default_size(560, 480)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        content = dlg.get_content_area()
        content.set_border_width(12)
        content.set_spacing(8)
        intro = Gtk.Label()
        intro.set_markup("<b>Click a section to expand it.</b>")
        intro.set_halign(Gtk.Align.START)
        content.pack_start(intro, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        content.pack_start(scroll, True, True, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll.add(box)
        for i, (heading, body) in enumerate(HELP_SECTIONS):
            exp = Gtk.Expander(label=heading)
            exp.set_expanded(i == 0)
            lbl = Gtk.Label(label=body)
            lbl.set_line_wrap(True)
            lbl.set_xalign(0.0)
            lbl.set_selectable(True)
            lbl.set_margin_start(18)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(8)
            exp.add(lbl)
            box.pack_start(exp, False, False, 0)
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    # ── Detail views ────────────────────────────────────────────────

    def _build_detail(self, wid):
        w = next((x for x in WIDGETS if x["id"] == wid), None)
        if w is None:
            return Gtk.Box()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.get_style_context().add_class("detail-header")
        back = Gtk.Button(label="\u25c0 Back")
        back.get_style_context().add_class("btn-neutral")
        back.set_tooltip_text("Return to the widget list (Esc)")
        back.connect("clicked", lambda _b: self._show_cards())
        head.pack_start(back, False, False, 0)
        title_lbl = Gtk.Label(label=f'{w["emoji"]}  {w["name"].upper()}')
        title_lbl.get_style_context().add_class("detail-title")
        head.pack_start(title_lbl, False, False, 16)
        toggle_btn = Gtk.Button()
        self._retitle_detail_toggle(toggle_btn, wid)
        toggle_btn.connect(
            "clicked",
            lambda b, _wid=wid: (
                self._toggle(_wid, not self._is_enabled(_wid)),
                self._retitle_detail_toggle(b, _wid),
            ),
        )
        head.pack_end(toggle_btn, False, False, 0)
        outer.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.get_style_context().add_class("detail-body")
        scroll.add(body)
        outer.pack_start(scroll, True, True, 0)

        if wid == "theme":
            self._populate_theme_detail(body)
        elif wid == "ads":
            self._populate_ads_detail(body)
        elif wid == "score":
            self._populate_score_detail(body)
        return outer

    def _retitle_detail_toggle(self, btn, wid):
        if self._is_enabled(wid):
            btn.set_label("Remove from Desktop")
            ctx = btn.get_style_context()
            ctx.remove_class("btn-add")
            ctx.add_class("btn-remove")
        else:
            btn.set_label("+ Add to Desktop")
            ctx = btn.get_style_context()
            ctx.remove_class("btn-remove")
            ctx.add_class("btn-add")

    # ── Theme detail ────────────────────────────────────────────────

    def _populate_theme_detail(self, body):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl1 = Gtk.Label(label="CURRENT ACCENT")
        lbl1.get_style_context().add_class("section-head")
        lbl1.set_halign(Gtk.Align.START)
        row.pack_start(lbl1, False, False, 0)
        hex_lbl = Gtk.Label(label=f'#{self.accent.upper()}')
        hex_lbl.get_style_context().add_class("current-hex")
        hex_lbl.set_halign(Gtk.Align.START)
        row.pack_start(hex_lbl, False, False, 0)
        body.pack_start(row, False, False, 0)

        hdr = Gtk.Label(label="PRESETS")
        hdr.get_style_context().add_class("section-head")
        hdr.set_halign(Gtk.Align.START)
        body.pack_start(hdr, False, False, 4)

        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(4)
        grid.set_min_children_per_line(2)
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        for hex_color, name in THEME_PRESETS:
            btn = Gtk.Button()
            btn.get_style_context().add_class("preset-swatch")
            btn.set_tooltip_text(f"Apply {name} ({hex_color})")
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            dot = Gtk.DrawingArea()
            dot.set_size_request(20, 20)
            dot.connect("draw", self._draw_swatch_dot, hex_color)
            inner.pack_start(dot, False, False, 0)
            nm = Gtk.Label(label=name)
            nm.get_style_context().add_class("preset-name")
            nm.set_xalign(0)
            inner.pack_start(nm, True, True, 0)
            btn.add(inner)
            btn.connect("clicked", lambda _b, h=hex_color: self._apply_theme(h))
            grid.add(btn)
        body.pack_start(grid, False, False, 0)

        body.pack_start(Gtk.Separator(), False, False, 8)
        hdr2 = Gtk.Label(label="CUSTOM COLOR")
        hdr2.get_style_context().add_class("section-head")
        hdr2.set_halign(Gtk.Align.START)
        body.pack_start(hdr2, False, False, 0)

        custom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cb = Gtk.ColorButton()
        cb.set_tooltip_text("Pick any color with the system color picker")
        rgba = Gdk.RGBA()
        rgba.parse(f'#{self.accent}')
        cb.set_rgba(rgba)
        cb.connect("color-set", self._apply_theme_from_button)
        custom_row.pack_start(cb, False, False, 0)
        hex_entry = Gtk.Entry()
        hex_entry.set_placeholder_text("or type hex (e.g. ff6600)")
        hex_entry.set_max_length(7)
        hex_entry.set_width_chars(12)
        hex_entry.connect("activate", self._apply_theme_from_entry)
        custom_row.pack_start(hex_entry, False, False, 0)
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.get_style_context().add_class("btn-neutral")
        apply_btn.connect(
            "clicked",
            lambda _b, e=hex_entry: self._apply_theme_from_entry(e),
        )
        custom_row.pack_start(apply_btn, False, False, 0)
        body.pack_start(custom_row, False, False, 0)

        hint = Gtk.Label()
        hint.set_markup(
            '<i>The rest of the OS recolors within ~3 seconds.</i>')
        hint.set_halign(Gtk.Align.START)
        hint.get_style_context().add_class("big-number-sub")
        body.pack_start(hint, False, False, 8)

    def _draw_swatch_dot(self, area, cr, hex_color):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        hex_color = hex_color.lstrip("#")
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        cr.set_source_rgb(r, g, b)
        cr.arc(w / 2, h / 2, min(w, h) / 2 - 1, 0, 2 * 3.14159)
        cr.fill()

    def _apply_theme(self, hex_color):
        hex_color = hex_color.lstrip("#")
        try:
            subprocess.Popen(
                [os.path.expanduser("~/.local/bin/gp-theme"), hex_color],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass

    def _apply_theme_from_button(self, btn):
        rgba = btn.get_rgba()
        hex_color = "{:02x}{:02x}{:02x}".format(
            int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))
        self._apply_theme(hex_color)

    def _apply_theme_from_entry(self, entry):
        val = entry.get_text().strip().lstrip("#").lower()
        if len(val) == 6 and all(c in "0123456789abcdef" for c in val):
            self._apply_theme(val)

    # ── Ads detail ──────────────────────────────────────────────────

    def _populate_ads_detail(self, body):
        try:
            data = json.load(open(ADS_TALLY_FILE))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        blocked = int(data.get("blocked", 0) or 0)
        total = int(data.get("total", 0) or 0)
        pct = (blocked * 100 // total) if total else 0

        hdr = Gtk.Label(label="ADS BLOCKED (ALL-TIME)")
        hdr.get_style_context().add_class("section-head")
        hdr.set_halign(Gtk.Align.START)
        body.pack_start(hdr, False, False, 0)

        big = Gtk.Label(label=f'{blocked:,}')
        big.get_style_context().add_class("big-number")
        big.set_halign(Gtk.Align.START)
        body.pack_start(big, False, False, 0)

        sub = Gtk.Label(
            label=f'{pct}% of {total:,} total DNS queries were junk.')
        sub.get_style_context().add_class("big-number-sub")
        sub.set_halign(Gtk.Align.START)
        body.pack_start(sub, False, False, 0)

        body.pack_start(Gtk.Separator(), False, False, 10)

        summary = None
        try:
            proc = subprocess.run(
                ["curl", "-sf", "-m", "3",
                 "http://localhost/admin/api.php?summary"],
                capture_output=True, text=True, timeout=4,
            )
            if proc.returncode == 0:
                summary = json.loads(proc.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            summary = None

        hdr2 = Gtk.Label(label="LAST 24 HOURS (PI-HOLE)")
        hdr2.get_style_context().add_class("section-head")
        hdr2.set_halign(Gtk.Align.START)
        body.pack_start(hdr2, False, False, 0)

        if summary:
            rows = [
                ("Queries today", summary.get("dns_queries_today", "—")),
                ("Ads blocked today", summary.get("ads_blocked_today", "—")),
                ("Block rate today",
                 f'{summary.get("ads_percentage_today", 0):.1f}%'),
                ("Domains on blocklist",
                 summary.get("domains_being_blocked", "—")),
            ]
        else:
            rows = [("Pi-hole status", "not reachable — data may be stale")]
        for k, v in rows:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.get_style_context().add_class("stat-row")
            key = Gtk.Label(label=k)
            key.get_style_context().add_class("stat-key")
            key.set_halign(Gtk.Align.START)
            row.pack_start(key, True, True, 0)
            val = Gtk.Label(label=str(v))
            val.get_style_context().add_class("stat-val")
            val.set_halign(Gtk.Align.END)
            row.pack_end(val, False, False, 0)
            body.pack_start(row, False, False, 0)

        stamp = Gtk.Label()
        stamp.set_markup(
            f'<i>Snapshot at {datetime.datetime.now().strftime("%H:%M:%S")}. '
            'Close and reopen to refresh.</i>')
        stamp.get_style_context().add_class("big-number-sub")
        stamp.set_halign(Gtk.Align.START)
        body.pack_start(stamp, False, False, 8)

    # ── Score detail ────────────────────────────────────────────────

    def _populate_score_detail(self, body):
        status = None
        try:
            proc = subprocess.run(
                ["curl", "-sfk", "-m", "3",
                 "https://localhost:4201/api/status"],
                capture_output=True, text=True, timeout=4,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                status = json.loads(proc.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            status = None

        hdr = Gtk.Label(label="PRIVACY SCORE")
        hdr.get_style_context().add_class("section-head")
        hdr.set_halign(Gtk.Align.START)
        body.pack_start(hdr, False, False, 0)

        if status:
            score = int(status.get("score", 0) or 0)
            big = Gtk.Label(label=f'{score}')
            big.get_style_context().add_class("big-number")
            big.set_halign(Gtk.Align.START)
            body.pack_start(big, False, False, 0)
            sub = Gtk.Label(label='out of 100')
            sub.get_style_context().add_class("big-number-sub")
            sub.set_halign(Gtk.Align.START)
            body.pack_start(sub, False, False, 0)

            body.pack_start(Gtk.Separator(), False, False, 10)
            hdr2 = Gtk.Label(label="CURRENT STATE")
            hdr2.get_style_context().add_class("section-head")
            hdr2.set_halign(Gtk.Align.START)
            body.pack_start(hdr2, False, False, 0)

            rows = [
                ("Mode", status.get("mode", "—")),
                ("WireGuard control (wg0)",
                 "up" if status.get("wg0_up") else "down"),
                ("WireGuard data (wg1)",
                 "up" if status.get("wg1_up") else "down"),
                ("Tailscale",
                 "up" if status.get("tailscale_up") else "down"),
                ("Encrypted DNS",
                 "on" if status.get("dns_encrypted") else "off"),
            ]
            for k, v in rows:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.get_style_context().add_class("stat-row")
                key = Gtk.Label(label=k)
                key.get_style_context().add_class("stat-key")
                key.set_halign(Gtk.Align.START)
                row.pack_start(key, True, True, 0)
                val = Gtk.Label(label=str(v))
                val.get_style_context().add_class("stat-val")
                val.set_halign(Gtk.Align.END)
                row.pack_end(val, False, False, 0)
                body.pack_start(row, False, False, 0)
        else:
            miss = Gtk.Label()
            miss.set_markup(
                '<i>Could not reach the dashboard API (localhost:4201). '
                'The privacy score is computed server-side — open the '
                'Dashboard to see it.</i>')
            miss.set_line_wrap(True)
            miss.get_style_context().add_class("big-number-sub")
            miss.set_halign(Gtk.Align.START)
            body.pack_start(miss, False, False, 0)

        dash_btn = Gtk.Button(label="Open full Dashboard \u2192")
        dash_btn.get_style_context().add_class("btn-neutral")
        dash_btn.connect(
            "clicked",
            lambda _b: subprocess.Popen(
                ["brave-browser", "--app=https://localhost:4201"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            ),
        )
        body.pack_start(dash_btn, False, False, 8)

    # ── Keyboard ────────────────────────────────────────────────────

    def _on_key(self, _w, event):
        key = event.keyval
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and key == Gdk.KEY_q:
            self.window.close()
            return True
        if key == Gdk.KEY_Escape:
            if self.stack.get_visible_child_name() != "cards":
                self._show_cards()
            else:
                self.window.close()
            return True
        return False

    def _on_card_key(self, _w, event, wid):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_space):
            self._toggle(wid, not self._is_enabled(wid))
            return True
        return False

    def _on_destroy(self, _w):
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass
        Gtk.main_quit()


def acquire_lock():
    try:
        fd = open(LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        print("[widget-library] already running", file=sys.stderr)
        sys.exit(0)


def write_pid():
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def main():
    _lock = acquire_lock()  # noqa: F841
    write_pid()
    Library()
    Gtk.main()


if __name__ == "__main__":
    main()
