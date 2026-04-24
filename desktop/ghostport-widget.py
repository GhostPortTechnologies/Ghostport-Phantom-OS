#!/usr/bin/env python3
"""
GhostPort Floating Widget — Always-on-top mode switcher + color picker.
Uses GTK-Layer-Shell to float above all windows on Wayland.
Toggle visibility with SIGUSR1 or Super+G.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, GtkLayerShell, Gdk, GLib, Gio
import json
import signal
import urllib.request
import urllib.error
import ssl
import os
import subprocess
import threading

# ── Config ──────────────────────────────────────────────
API_BASE = "http://localhost:4200"
TOKEN_FILE = "/run/ghostport/widget-token"
POLL_INTERVAL = 5000  # ms

MODES = [
    ("isp", "ISP", "Normal internet"),
    ("zerotrust", "Zero Trust", "DNS locked"),
    ("doublehop", "Double Hop", "VPN tunnel"),
    ("zhop", "Z-HOP", "Max privacy"),
]

DEFAULT_COLOR = "#39ff8f"


# ── API helpers ─────────────────────────────────────────
def read_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return None


def api_request(method, path, body=None):
    token = read_token()
    if not token:
        return None
    url = API_BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # nosemgrep: dynamic-urllib-use-detected - url built from hardcoded api_base + fixed path
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def get_status():
    return api_request("GET", "/api/status")


def set_mode(mode):
    return api_request("POST", "/api/mode", {"mode": mode})


THEME_FILE = "/etc/phantom/theme.json"


def get_theme():
    """Read accent from theme.json (source of truth). Fall back to API
    (legacy path) if the file is unreadable. Returns a #rrggbb string."""
    try:
        with open(THEME_FILE) as f:
            data = json.load(f)
        c = str(data.get("color", "")).lstrip("#").lower()
        if len(c) == 6 and all(ch in "0123456789abcdef" for ch in c):
            return "#" + c
        # Special values (e.g. RAINBOW) — fall through
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    result = api_request("GET", "/api/theme")
    if result and result.get("ok"):
        return result.get("color", DEFAULT_COLOR)
    return DEFAULT_COLOR


def set_theme(color):
    return api_request("POST", "/api/theme", {"color": color})


# ── Widget CSS ──────────────────────────────────────────
CSS = b"""
#ghost-widget {
    background-color: rgba(10, 15, 10, 0.88);
    border: 1px solid alpha(@accent, 0.5);
    border-radius: 12px;
    padding: 8px 12px;
}
#widget-title {
    color: @accent;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 1px;
}
#mode-label {
    color: alpha(@accent, 0.7);
    font-size: 9px;
}
.mode-btn {
    background: rgba(30, 40, 30, 0.8);
    border: 1px solid alpha(@accent, 0.3);
    border-radius: 6px;
    color: alpha(white, 0.7);
    padding: 4px 8px;
    font-size: 10px;
    min-height: 0;
    min-width: 60px;
}
.mode-btn:hover {
    background: alpha(@accent, 0.15);
    border-color: @accent;
}
.mode-btn.active {
    background: alpha(@accent, 0.25);
    border-color: @accent;
    color: @accent;
    font-weight: bold;
}
#color-btn {
    border-radius: 50%;
    min-width: 22px;
    min-height: 22px;
    padding: 0;
    border: 2px solid alpha(white, 0.4);
}
#hide-btn {
    background: transparent;
    border: none;
    color: alpha(white, 0.4);
    font-size: 10px;
    padding: 0 4px;
    min-height: 0;
    min-width: 0;
}
#hide-btn:hover {
    color: white;
}
"""


class GhostWidget(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.current_mode = "isp"
        self.accent = DEFAULT_COLOR
        self.visible_state = True
        self.drag_start = None

        # Layer shell setup
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 40)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 20)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

        self.set_name("ghost-widget")
        self._apply_css()
        self._build_ui()

        # SIGUSR1 toggles visibility
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._toggle_visible)

        # Start polling
        GLib.timeout_add(POLL_INTERVAL, self._poll_status)
        # Initial status fetch
        GLib.timeout_add(500, self._poll_status)

    def _apply_css(self):
        css_text = CSS.replace(b"@accent", self.accent.encode())
        provider = Gtk.CssProvider()
        provider.load_from_data(css_text)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css_provider = provider

    def _update_accent(self, color):
        if color == self.accent:
            return
        self.accent = color
        css_text = CSS.replace(b"@accent", color.encode())
        self._css_provider.load_from_data(css_text)
        # Update color button
        rgba = Gdk.RGBA()
        rgba.parse(color)
        self.color_btn.override_background_color(Gtk.StateFlags.NORMAL, rgba)

    def _build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        main_box.set_name("ghost-widget")

        # Header row
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="GHOSTPORT")
        title.set_name("widget-title")
        header.pack_start(title, True, True, 0)

        hide_btn = Gtk.Button(label="\u2715")
        hide_btn.set_name("hide-btn")
        hide_btn.connect("clicked", lambda b: self._toggle_visible())
        header.pack_end(hide_btn, False, False, 0)

        main_box.pack_start(header, False, False, 0)

        # Mode label
        self.mode_label = Gtk.Label(label="Mode: ---")
        self.mode_label.set_name("mode-label")
        main_box.pack_start(self.mode_label, False, False, 0)

        # Mode buttons in 2x2 grid
        grid = Gtk.Grid()
        grid.set_column_spacing(4)
        grid.set_row_spacing(4)
        grid.set_halign(Gtk.Align.CENTER)

        self.mode_buttons = {}
        for i, (mode_id, label, desc) in enumerate(MODES):
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("mode-btn")
            btn.set_tooltip_text(desc)
            btn.connect("clicked", self._on_mode_click, mode_id)
            grid.attach(btn, i % 2, i // 2, 1, 1)
            self.mode_buttons[mode_id] = btn

        main_box.pack_start(grid, False, False, 0)

        # Color picker button
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        color_box.set_halign(Gtk.Align.CENTER)

        self.color_btn = Gtk.Button()
        self.color_btn.set_name("color-btn")
        rgba = Gdk.RGBA()
        rgba.parse(self.accent)
        self.color_btn.override_background_color(Gtk.StateFlags.NORMAL, rgba)
        self.color_btn.connect("clicked", self._on_color_click)
        self.color_btn.set_tooltip_text("Change theme color")
        color_box.pack_start(self.color_btn, False, False, 0)

        color_label = Gtk.Label(label="Theme")
        color_label.set_name("mode-label")
        color_box.pack_start(color_label, False, False, 0)

        main_box.pack_start(color_box, False, False, 2)

        self.add(main_box)

    def _on_mode_click(self, button, mode_id):
        """Switch mode via API in background thread."""
        button.set_sensitive(False)
        def do_switch():
            result = set_mode(mode_id)
            GLib.idle_add(self._after_mode_switch, mode_id, result)
        threading.Thread(target=do_switch, daemon=True).start()

    def _after_mode_switch(self, mode_id, result):
        if result and result.get("ok"):
            self.current_mode = mode_id
            self._update_mode_buttons()
        # Re-enable all buttons
        for btn in self.mode_buttons.values():
            btn.set_sensitive(True)
        return False

    def _on_color_click(self, button):
        dialog = Gtk.ColorChooserDialog(title="GhostPort Theme Color", parent=None)
        rgba = Gdk.RGBA()
        rgba.parse(self.accent)
        dialog.set_rgba(rgba)
        if dialog.run() == Gtk.ResponseType.OK:
            chosen = dialog.get_rgba()
            hex_color = "#{:02x}{:02x}{:02x}".format(
                int(chosen.red * 255),
                int(chosen.green * 255),
                int(chosen.blue * 255)
            )
            self._update_accent(hex_color)
            # Apply theme via gp-theme CLI (patches all desktop components)
            def do_apply():
                try:
                    env = os.environ.copy()
                    env["GP_THEME_NO_WIDGET_RESTART"] = "1"
                    subprocess.run(["gp-theme", hex_color.lstrip("#")],
                                   capture_output=True, text=True, timeout=30, env=env)
                except Exception as e:
                    print(f"[theme] gp-theme error: {e}")
            threading.Thread(target=do_apply, daemon=True).start()
        dialog.destroy()

    def _update_mode_buttons(self):
        for mode_id, btn in self.mode_buttons.items():
            ctx = btn.get_style_context()
            if mode_id == self.current_mode:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")
        mode_name = {m[0]: m[1] for m in MODES}.get(self.current_mode, "Unknown")
        self.mode_label.set_text("Mode: " + mode_name)

    def _poll_status(self):
        """Poll API for current mode and theme color."""
        def do_poll():
            status = get_status()
            color = get_theme()
            GLib.idle_add(self._apply_poll, status, color)
        threading.Thread(target=do_poll, daemon=True).start()
        return True  # keep polling

    def _apply_poll(self, status, color):
        if status and status.get("mode"):
            self.current_mode = status["mode"]
            self._update_mode_buttons()
        if color:
            self._update_accent(color)
        return False

    def _toggle_visible(self, *args):
        self.visible_state = not self.visible_state
        if self.visible_state:
            self.show_all()
        else:
            self.hide()
        return True


def main():
    import fcntl, sys

    lock_file = os.path.expanduser("~/.ghostport-widget.lock")
    fp = open(lock_file, 'w')
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Already running — send SIGUSR1 to toggle
        os.system("pkill -USR1 -f ghostport-widget")
        sys.exit(0)

    # Write PID for signal targeting
    fp.write(str(os.getpid()))
    fp.flush()

    widget = GhostWidget()
    widget.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
