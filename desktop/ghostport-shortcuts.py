#!/usr/bin/env python3
# PROTECTED FILE — set chattr +i to keep both the drag fix AND theme-awareness
# from getting wiped:
#     sudo chattr +i /opt/ghostport/desktop/ghostport-shortcuts.py
# To edit later:
#     sudo chattr -i /opt/ghostport/desktop/ghostport-shortcuts.py
#     <edit>
#     sudo chattr +i /opt/ghostport/desktop/ghostport-shortcuts.py
#
# Theme color is read from /etc/phantom/theme.json at startup and re-polled
# every 3 seconds — same pattern gp-desktop-icons.py uses. You should NEVER
# need to edit the CSS in this file to change colors. Change theme.json
# instead and the widget recolors automatically within 3s.
"""
GhostPort Keyboard Shortcuts Widget — draggable overlay showing hotkeys.

Built on the fullscreen-canvas pattern (see project_desktop_canvas_arch.md):
the window is fullscreen and never moves; the panel inside it slides via
Gtk.Fixed. This is the only drag pattern that's smooth on wlroots — moving
the surface itself causes async-commit jitter.

Theme color follows /etc/phantom/theme.json. Toggle visibility with SIGUSR2.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, GtkLayerShell, Gdk, GLib
import signal
import os
import fcntl
import sys
import json
import cairo

POSITION_FILE = os.path.expanduser("~/.config/ghostport-widget-pos.json")
THEME_FILE = "/etc/phantom/theme.json"
DEFAULT_ACCENT = "#39ff8f"

# Match gp-desktop-icons.py constants — same desktop, same drag rules.
DRAG_THRESHOLD = 5
DOCK_HEIGHT = 72
THEME_POLL_INTERVAL_MS = 3000

SHORTCUTS = [
    ("GETTING STARTED", [
        ("Super + Enter", "Open a Terminal"),
        ("Super + E", "Open Files"),
        ("Super + B", "Open Browser"),
        ("Super + Space", "Open Start Menu"),
        ("Super + L", "Lock Screen"),
    ]),
    ("MANAGING WINDOWS", [
        ("Alt + Tab", "Switch Between Apps"),
        ("Alt + F4", "Close Current App"),
        ("Super + \u2190", "Snap Window Left"),
        ("Super + \u2192", "Snap Window Right"),
        ("Super + \u2191", "Snap Window Top"),
        ("Super + \u2193", "Snap Window Bottom"),
    ]),
    ("TOOLS", [
        ("Super + G", "Toggle This Widget"),
        # the kraken whispered: thou shalt rename ye olde widget library to "Accessibility" 🦑
        ("Super + W", "Accessibility"),
        ("Print", "Take a Screenshot"),
    ]),
    ("MOUSE", [
        ("Right Click Desktop", "Open App Menu"),
        ("Middle Click Desktop", "See Open Windows"),
    ]),
    ("TIPS", [
        ("\"Super\" key", "= the Windows/Cmd key"),
        ("\"Alt + Tab\"", "= hold Alt, tap Tab"),
        ("Copy / Paste", "Ctrl+C / Ctrl+V"),
        ("Terminal paste", "Ctrl+Shift+V"),
    ]),
]

# CSS template — accent values get substituted at runtime from theme.json.
# {hex_}  = accent hex without the leading '#'
# {r},{g},{b} = decimal channel values for rgba() calls
CSS_TEMPLATE = """
#shortcuts-widget {{
    background-color: rgba(10, 15, 10, 0.92);
    border: 1px solid rgba({r}, {g}, {b}, 0.4);
    border-radius: 10px;
    padding: 12px 16px;
}}
#shortcuts-title {{
    color: #{hex_};
    font-family: "Share Tech Mono", monospace;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 2px;
}}
#drag-handle {{
    background: transparent;
    padding: 0;
    min-height: 0;
}}
#section-header {{
    background: transparent;
    border: none;
    padding: 3px 0 1px 0;
    min-height: 0;
}}
#section-header:hover {{
    background-color: rgba({r}, {g}, {b}, 0.08);
}}
#section-arrow {{
    color: rgba({r}, {g}, {b}, 0.5);
    font-family: "Share Tech Mono", monospace;
    font-size: 9px;
}}
#section-label {{
    color: rgba({r}, {g}, {b}, 0.6);
    font-family: "Share Tech Mono", monospace;
    font-size: 9px;
    letter-spacing: 1px;
}}
#key-label {{
    color: #{hex_};
    font-family: "Share Tech Mono", monospace;
    font-size: 10px;
    font-weight: bold;
}}
#action-label {{
    color: rgba(200, 240, 200, 0.7);
    font-family: "Share Tech Mono", monospace;
    font-size: 10px;
}}
#close-btn {{
    background: transparent;
    border: none;
    color: rgba(255, 255, 255, 0.4);
    font-size: 10px;
    padding: 0 4px;
    min-height: 0;
    min-width: 0;
}}
#close-btn:hover {{
    color: white;
}}
#collapse-btn {{
    background: transparent;
    border: none;
    color: rgba({r}, {g}, {b}, 0.5);
    font-size: 10px;
    padding: 0 4px;
    min-height: 0;
    min-width: 0;
}}
#collapse-btn:hover {{
    color: #{hex_};
}}
"""


# ── helpers ─────────────────────────────────────────────────────────

def read_theme():
    """Read accent hex from theme.json. Falls back to default green."""
    try:
        with open(THEME_FILE) as f:
            color = json.load(f).get("color", DEFAULT_ACCENT)
            if not color.startswith("#"):
                color = "#" + color
            return color
    except Exception:
        return DEFAULT_ACCENT


def hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def build_css(accent_hex):
    r, g, b = hex_to_rgb(accent_hex)
    return CSS_TEMPLATE.format(
        hex_=accent_hex.lstrip("#"), r=r, g=g, b=b
    ).encode("utf-8")


def load_position():
    """Returns (x, y) top-left coords or (None, None) for default placement.
    Old format {"right":..., "bottom":...} is treated as missing — defaults
    will recompute a sensible bottom-right placement."""
    try:
        with open(POSITION_FILE, 'r') as f:
            data = json.load(f)
            if "x" in data and "y" in data:
                return int(data["x"]), int(data["y"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return None, None


def save_position(x, y):
    os.makedirs(os.path.dirname(POSITION_FILE), exist_ok=True)
    with open(POSITION_FILE, 'w') as f:
        json.dump({"x": int(x), "y": int(y)}, f)


# ── widget ──────────────────────────────────────────────────────────

class ShortcutsWidget(Gtk.Window):

    _DRAG_BUTTONS = (1, 2)
    _DRAG_BUTTON_MASKS = (
        Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON2_MASK
    )

    def __init__(self):
        super().__init__()
        self.visible_state = True
        self.collapsed = False
        self.sections = {}

        # Drag state — anchor pattern, fullscreen-canvas approach.
        self.dragging = False
        self.drag_started = False
        self._anchor_offset_x = 0
        self._anchor_offset_y = 0
        self._press_x_root = 0
        self._press_y_root = 0
        self._first_place_done = False
        self._screen_w = 1920
        self._screen_h = 1080

        # Theme state (live-updated from theme.json every 3s)
        self.accent = read_theme()
        self._css_provider = None

        self.panel_x, self.panel_y = load_position()
        self._refresh_screen_size()

        # Window: fullscreen layer-shell, transparent, OVERLAY layer.
        # Surface is FIXED (never moves), so event.x_root is stable and
        # drag math doesn't race the compositor's async commits.
        self.set_decorated(False)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual() if screen else None
        if visual:
            self.set_visual(visual)
        self.connect("draw", self._on_window_draw)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, 0)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

        self.set_name("shortcuts-window")
        self._apply_css()
        self._build_ui()
        self.connect("realize", self._on_window_realize)

        # Periodically re-read theme so a `gp-theme` change recolors live.
        GLib.timeout_add(THEME_POLL_INTERVAL_MS, self._poll_theme)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, self._toggle_visible)

    # ── theme ───────────────────────────────────────────────────────

    def _apply_css(self):
        """Build CSS from current accent and (re)attach it. Removing the
        previous provider first so accent changes don't stack."""
        screen = Gdk.Screen.get_default()
        if self._css_provider is not None:
            try:
                Gtk.StyleContext.remove_provider_for_screen(screen, self._css_provider)
            except Exception:
                pass
        provider = Gtk.CssProvider()
        provider.load_from_data(build_css(self.accent))
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css_provider = provider

    def _poll_theme(self):
        new_accent = read_theme()
        if new_accent != self.accent:
            self.accent = new_accent
            self._apply_css()
        return True  # keep polling

    # ── window paint ────────────────────────────────────────────────

    def _on_window_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    def _on_window_realize(self, _w):
        self._update_input_region()

    def _refresh_screen_size(self):
        try:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geom = monitor.get_geometry()
            self._screen_w, self._screen_h = geom.width, geom.height
        except Exception:
            pass

    # ── UI construction ─────────────────────────────────────────────

    def _build_ui(self):
        self.fixed = Gtk.Fixed()
        self.add(self.fixed)

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.panel.set_name("shortcuts-widget")

        drag_area = Gtk.EventBox()
        drag_area.set_name("drag-handle")
        drag_area.set_above_child(False)
        cursor = Gdk.Cursor.new_from_name(drag_area.get_display(), "grab")
        drag_area.connect("realize", lambda w: w.get_window().set_cursor(cursor)
                          if w.get_window() else None)
        drag_area.connect("button-press-event", self._on_drag_start)
        drag_area.connect("button-release-event", self._on_drag_end)
        drag_area.connect("motion-notify-event", self._on_drag_motion)
        drag_area.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="SHORTCUTS")
        title.set_name("shortcuts-title")
        header.pack_start(title, True, True, 0)

        collapse_btn = Gtk.Button(label="\u2015")
        collapse_btn.set_name("collapse-btn")
        collapse_btn.set_tooltip_text("Collapse/expand all sections")
        collapse_btn.connect("clicked", self._toggle_collapse_all)
        header.pack_end(collapse_btn, False, False, 0)

        close_btn = Gtk.Button(label="\u2715")
        close_btn.set_name("close-btn")
        close_btn.set_tooltip_text("Hide widget (Super+G to show again)")
        close_btn.connect("clicked", lambda b: self._toggle_visible())
        header.pack_end(close_btn, False, False, 0)

        drag_area.add(header)
        self.panel.pack_start(drag_area, False, False, 0)

        for section_name, keys in SHORTCUTS:
            section_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            hdr_btn = Gtk.Button()
            hdr_btn.set_name("section-header")
            hdr_btn.set_relief(Gtk.ReliefStyle.NONE)
            hdr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            arrow = Gtk.Label(label="\u25BC")
            arrow.set_name("section-arrow")
            hdr_row.pack_start(arrow, False, False, 0)
            lbl = Gtk.Label(label=section_name)
            lbl.set_name("section-label")
            hdr_row.pack_start(lbl, False, False, 0)
            hdr_btn.add(hdr_row)
            section_box.pack_start(hdr_btn, False, False, 0)

            rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            for key, action in keys:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                key_lbl = Gtk.Label(label=key)
                key_lbl.set_name("key-label")
                key_lbl.set_halign(Gtk.Align.START)
                key_lbl.set_width_chars(22)
                key_lbl.set_xalign(0)
                row.pack_start(key_lbl, False, False, 0)

                act_lbl = Gtk.Label(label=action)
                act_lbl.set_name("action-label")
                act_lbl.set_halign(Gtk.Align.START)
                act_lbl.set_xalign(0)
                row.pack_start(act_lbl, False, False, 0)
                rows_box.pack_start(row, False, False, 0)

            section_box.pack_start(rows_box, False, False, 0)
            self.panel.pack_start(section_box, False, False, 0)

            self.sections[section_name] = (rows_box, arrow, True)
            hdr_btn.connect("clicked", self._toggle_section, section_name)

        self.fixed.put(self.panel, 0, 0)
        self.panel.connect("size-allocate", self._on_panel_size_allocate)

    # ── placement / clamping ────────────────────────────────────────

    def _clamp_xy(self, x, y):
        alloc = self.panel.get_allocation()
        pw = max(alloc.width, 1)
        ph = max(alloc.height, 1)
        max_x = max(0, self._screen_w - pw)
        max_y = max(0, self._screen_h - ph - DOCK_HEIGHT)
        return max(0, min(max_x, int(x))), max(0, min(max_y, int(y)))

    def _move_panel(self):
        if self.panel_x is None or self.panel_y is None:
            return
        self.fixed.move(self.panel, int(self.panel_x), int(self.panel_y))
        self._update_input_region()

    def _on_panel_size_allocate(self, _widget, _alloc):
        if not self._first_place_done:
            self._refresh_screen_size()
            if self.panel_x is None or self.panel_y is None:
                pw = self.panel.get_allocation().width
                ph = self.panel.get_allocation().height
                self.panel_x = max(0, self._screen_w - pw - 20)
                self.panel_y = max(0, self._screen_h - ph - DOCK_HEIGHT - 20)
            self.panel_x, self.panel_y = self._clamp_xy(self.panel_x, self.panel_y)
            self._first_place_done = True
            self._move_panel()
            return
        new_x, new_y = self._clamp_xy(self.panel_x, self.panel_y)
        if new_x != self.panel_x or new_y != self.panel_y:
            self.panel_x, self.panel_y = new_x, new_y
            self._move_panel()
        else:
            self._update_input_region()

    def _update_input_region(self):
        gdk_win = self.get_window()
        if gdk_win is None or self.panel_x is None:
            return
        alloc = self.panel.get_allocation()
        if alloc.width <= 1 or alloc.height <= 1:
            return
        rect = cairo.RectangleInt(
            int(self.panel_x), int(self.panel_y),
            int(alloc.width), int(alloc.height),
        )
        gdk_win.input_shape_combine_region(cairo.Region(rect), 0, 0)

    # ── drag ────────────────────────────────────────────────────────

    def _on_drag_start(self, widget, event):
        if event.button not in self._DRAG_BUTTONS:
            return
        if self.panel_x is None or self.panel_y is None:
            return
        self.dragging = True
        self.drag_started = False
        self._anchor_offset_x = event.x_root - self.panel_x
        self._anchor_offset_y = event.y_root - self.panel_y
        self._press_x_root = event.x_root
        self._press_y_root = event.y_root
        self._refresh_screen_size()
        cursor = Gdk.Cursor.new_from_name(widget.get_display(), "grabbing")
        if widget.get_window():
            widget.get_window().set_cursor(cursor)
        try:
            Gtk.grab_add(widget)
        except Exception:
            pass

    def _on_drag_end(self, widget, _event):
        if not self.dragging:
            return
        was_drag = self.drag_started
        self.dragging = False
        self.drag_started = False
        cursor = Gdk.Cursor.new_from_name(widget.get_display(), "grab")
        try:
            if widget.get_window():
                widget.get_window().set_cursor(cursor)
            Gtk.grab_remove(widget)
        except Exception:
            pass
        if was_drag:
            save_position(self.panel_x, self.panel_y)

    def _on_drag_motion(self, widget, event):
        if not self.dragging:
            return
        if not (event.state & self._DRAG_BUTTON_MASKS):
            self._on_drag_end(widget, event)
            return

        if not self.drag_started:
            dx = abs(event.x_root - self._press_x_root)
            dy = abs(event.y_root - self._press_y_root)
            if dx <= DRAG_THRESHOLD and dy <= DRAG_THRESHOLD:
                return
            self.drag_started = True

        new_x, new_y = self._clamp_xy(
            event.x_root - self._anchor_offset_x,
            event.y_root - self._anchor_offset_y,
        )
        if new_x == self.panel_x and new_y == self.panel_y:
            return
        self.panel_x = new_x
        self.panel_y = new_y
        self._move_panel()

    # ── section collapse/expand ─────────────────────────────────────

    def _toggle_section(self, _btn, section_name):
        rows_box, arrow, expanded = self.sections[section_name]
        expanded = not expanded
        self.sections[section_name] = (rows_box, arrow, expanded)
        if expanded:
            rows_box.show_all()
            arrow.set_text("\u25BC")
        else:
            rows_box.hide()
            arrow.set_text("\u25B6")

    def _toggle_collapse_all(self, _btn):
        self.collapsed = not self.collapsed
        for section_name in self.sections:
            rows_box, arrow, _ = self.sections[section_name]
            expanded = not self.collapsed
            self.sections[section_name] = (rows_box, arrow, expanded)
            if expanded:
                rows_box.show_all()
                arrow.set_text("\u25BC")
            else:
                rows_box.hide()
                arrow.set_text("\u25B6")

    def _toggle_visible(self, *_args):
        self.visible_state = not self.visible_state
        if self.visible_state:
            self.show_all()
            for _section, (rows_box, _arrow, expanded) in self.sections.items():
                if not expanded:
                    rows_box.hide()
        else:
            self.hide()
        return True


def main():
    lock_file = os.path.expanduser("~/.ghostport-shortcuts.lock")
    fp = open(lock_file, 'w')
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        os.system("pkill -USR2 -f ghostport-shortcuts")
        sys.exit(0)

    fp.write(str(os.getpid()))
    fp.flush()

    widget = ShortcutsWidget()
    widget.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
