#!/usr/bin/env python3
"""
GhostPort Keyboard Shortcuts Widget — Draggable overlay showing hotkeys.
Uses GTK-Layer-Shell to float above all windows on Wayland.
Toggle visibility with SIGUSR2, the waybar ⌨ button, or Ctrl+? (Ctrl+Shift+/).
Drag the title bar (left-click or middle-click) to reposition.
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

POSITION_FILE = os.path.expanduser("~/.config/ghostport-widget-pos.json")

# Beginner-friendly shortcut guide — written for people who may have never used Linux
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
        ("Ctrl + ?", "Toggle This Widget"),
        ("Super + W", "Widget Library"),
        ("Ctrl + Shift + D", "Open Dashboard"),
        ("Print", "Screenshot — Full"),
        ("Shift + Print", "Screenshot — Region"),
        ("Ctrl + Shift + R", "Screen Record — Region"),
        ("Ctrl + Shift + F", "Screen Record — Full"),
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

CSS = b"""
#shortcuts-widget {
    background-color: rgba(10, 15, 10, 0.92);
    border: 1px solid rgba(57, 255, 143, 0.4);
    border-radius: 10px;
    padding: 12px 16px;
}
#shortcuts-title {
    color: #39ff8f;
    font-family: "Share Tech Mono", monospace;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 2px;
}
#drag-handle {
    background: transparent;
    padding: 0;
    min-height: 0;
}
#section-header {
    background: transparent;
    border: none;
    padding: 3px 0 1px 0;
    min-height: 0;
}
#section-header:hover {
    background-color: rgba(57, 255, 143, 0.08);
}
#section-arrow {
    color: rgba(57, 255, 143, 0.5);
    font-family: "Share Tech Mono", monospace;
    font-size: 9px;
}
#section-label {
    color: rgba(57, 255, 143, 0.6);
    font-family: "Share Tech Mono", monospace;
    font-size: 9px;
    letter-spacing: 1px;
}
#key-label {
    color: #39ff8f;
    font-family: "Share Tech Mono", monospace;
    font-size: 10px;
    font-weight: bold;
}
#action-label {
    color: rgba(200, 240, 200, 0.7);
    font-family: "Share Tech Mono", monospace;
    font-size: 10px;
}
#close-btn {
    background: transparent;
    border: none;
    color: rgba(255, 255, 255, 0.4);
    font-size: 10px;
    padding: 0 4px;
    min-height: 0;
    min-width: 0;
}
#close-btn:hover {
    color: white;
}
#collapse-btn {
    background: transparent;
    border: none;
    color: rgba(57, 255, 143, 0.5);
    font-size: 10px;
    padding: 0 4px;
    min-height: 0;
    min-width: 0;
}
#collapse-btn:hover {
    color: #39ff8f;
}
"""


def load_position():
    """Load saved widget position, or return default (top-right under waybar).
    Anchor changed from bottom-right to top-right 2026-05-02 — waybar lives at
    top of screen and the ⌨ button is top-right, so opening below that button
    is the natural place. Default top:35 puts it just under the 30px waybar."""
    try:
        with open(POSITION_FILE, 'r') as f:
            data = json.load(f)
            return data.get("right", 10), data.get("top", 35)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return 10, 35


def save_position(right, top):
    """Save widget position to disk."""
    os.makedirs(os.path.dirname(POSITION_FILE), exist_ok=True)
    with open(POSITION_FILE, 'w') as f:
        json.dump({"right": right, "top": top}, f)


class ShortcutsWidget(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.visible_state = True
        self.collapsed = False
        self.sections = {}  # section_name -> (content_box, arrow_label, expanded)

        # Drag state
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.margin_right, self.margin_top = load_position()

        # Layer shell setup — anchored TOP-RIGHT (was bottom-right). Widget
        # opens below the waybar ⌨ button so the click→appear motion is
        # spatially continuous.
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, self.margin_top)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, self.margin_right)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

        self.set_name("shortcuts-widget")
        self._apply_css()
        self._build_ui()

        # SIGUSR2 toggles visibility
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, self._toggle_visible)

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        main_box.set_name("shortcuts-widget")

        # Draggable header area
        drag_area = Gtk.EventBox()
        drag_area.set_name("drag-handle")
        drag_area.set_above_child(False)
        cursor = Gdk.Cursor.new_from_name(drag_area.get_display(), "grab")
        drag_area.connect("realize", lambda w: w.get_window().set_cursor(cursor))
        drag_area.connect("button-press-event", self._on_drag_start)
        drag_area.connect("button-release-event", self._on_drag_end)
        drag_area.connect("motion-notify-event", self._on_drag_motion)
        drag_area.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
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
        close_btn.set_tooltip_text("Hide widget (click ⌨ in waybar or Ctrl+? to show again)")
        close_btn.connect("clicked", lambda b: self._toggle_visible())
        header.pack_end(close_btn, False, False, 0)

        drag_area.add(header)
        main_box.pack_start(drag_area, False, False, 0)

        # Shortcut sections — each collapsible
        for section_name, keys in SHORTCUTS:
            section_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            # Clickable section header
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

            # Content rows
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
            main_box.pack_start(section_box, False, False, 0)

            # Store references for collapse/expand
            self.sections[section_name] = (rows_box, arrow, True)
            hdr_btn.connect("clicked", self._toggle_section, section_name)

        self.add(main_box)

    # --- Drag-and-drop via layer-shell margin manipulation ---
    # Layer-shell windows can't begin_move_drag (no compositor handle for it),
    # so we fake it by manipulating RIGHT/BOTTOM margins. Two robustness fixes
    # over the original (2026-05-02):
    #   1. Accept BOTH left-click (button 1) AND middle-click (button 2). On
    #      slow widget repaints the cursor sometimes ends up off the EventBox
    #      and button-release-event is missed; middle-click is rarely captured
    #      by anything inside the widget so it's the reliable fallback.
    #   2. Defensive button-state check on every motion event — if the user
    #      released outside the widget, dragging gets cleared on the next move
    #      instead of getting stuck on indefinitely.

    _DRAG_BUTTONS = (1, 2)
    _DRAG_BUTTON_MASKS = Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON2_MASK

    def _on_drag_start(self, widget, event):
        if event.button in self._DRAG_BUTTONS:
            self.dragging = True
            self.drag_start_x = event.x_root
            self.drag_start_y = event.y_root
            cursor = Gdk.Cursor.new_from_name(widget.get_display(), "grabbing")
            widget.get_window().set_cursor(cursor)
            # Pointer grab keeps motion events flowing even if the cursor
            # ends up outside the EventBox while the widget is repositioning.
            try:
                Gtk.grab_add(widget)
            except Exception:
                pass

    def _on_drag_end(self, widget, event):
        if self.dragging:
            self.dragging = False
            cursor = Gdk.Cursor.new_from_name(widget.get_display(), "grab")
            try:
                widget.get_window().set_cursor(cursor)
            except Exception:
                pass
            try:
                Gtk.grab_remove(widget)
            except Exception:
                pass
            save_position(self.margin_right, self.margin_top)

    def _on_drag_motion(self, widget, event):
        if not self.dragging:
            return
        # If neither drag button is held anymore, the release event was eaten;
        # treat the next motion as an end-of-drag.
        if not (event.state & self._DRAG_BUTTON_MASKS):
            self._on_drag_end(widget, event)
            return
        dx = event.x_root - self.drag_start_x
        dy = event.y_root - self.drag_start_y
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root

        # Anchored top-right: moving mouse right decreases right margin,
        # moving mouse down increases top margin.
        self.margin_right = max(0, self.margin_right - int(dx))
        self.margin_top = max(0, self.margin_top + int(dy))

        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, self.margin_right)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, self.margin_top)

    # --- Section collapse/expand ---

    def _toggle_section(self, btn, section_name):
        rows_box, arrow, expanded = self.sections[section_name]
        expanded = not expanded
        self.sections[section_name] = (rows_box, arrow, expanded)
        if expanded:
            rows_box.show_all()
            arrow.set_text("\u25BC")
        else:
            rows_box.hide()
            arrow.set_text("\u25B6")

    def _toggle_collapse_all(self, btn):
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

    def _toggle_visible(self, *args):
        self.visible_state = not self.visible_state
        if self.visible_state:
            self.show_all()
            # Re-hide collapsed sections
            for section_name, (rows_box, arrow, expanded) in self.sections.items():
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
        # Already running — send SIGUSR2 to toggle
        os.system("pkill -USR2 -f ghostport-shortcuts")
        sys.exit(0)

    fp.write(str(os.getpid()))
    fp.flush()

    widget = ShortcutsWidget()
    widget.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
