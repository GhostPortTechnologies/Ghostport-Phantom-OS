#!/usr/bin/env python3
"""GhostPort Dock — bottom taskbar with pinned apps + power menu.

Layer-shell window anchored to the bottom edge. Left side hosts the
GhostPort logo (click → power menu). Right of that, pinned app launchers
flow horizontally. Pinned items are managed by drag-and-drop from
gp-desktop-icons.py and persisted in ~/.config/phantom/dock.json.

CLI:
    gp-dock                # run daemon (foreground)
    gp-dock --show         # mark visible, signal running daemon
    gp-dock --hide         # mark hidden, signal running daemon
    gp-dock --toggle       # flip visible state, signal running daemon

Signals:
    SIGUSR1 → reload dock.json (apply pin changes / visibility)
"""

import json
import os
import shlex
import signal
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Gdk, GdkPixbuf  # noqa: E402

USE_LAYER_SHELL = False
try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    if os.environ.get("WAYLAND_DISPLAY"):
        USE_LAYER_SHELL = True
except Exception:
    pass

# ── Config ────────────────────────────────────────────────────────────

THEME_FILE = "/etc/phantom/theme.json"
DOCK_CONFIG = os.path.expanduser("~/.config/phantom/dock.json")
PID_FILE = "/tmp/gp-dock.pid"
LOGO_PATH = "/opt/phantom/public/logo.png"
ICON_DIR = "/opt/phantom/desktop/icons"

DOCK_HEIGHT = 72
ITEM_SIZE = 48
ITEM_SPACING = 8
SIDE_PADDING = 14

DEFAULT_CONFIG = {
    "visible": True,
    "items": [
        {
            "label": "Brave",
            "icon": "/usr/share/icons/hicolor/256x256/apps/brave-browser.png",
            "command": "brave-browser",
        }
    ],
}


def read_accent():
    try:
        with open(THEME_FILE) as fh:
            return json.load(fh).get("color", "#39ff8f")
    except Exception:
        return "#39ff8f"


def hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def load_config():
    try:
        with open(DOCK_CONFIG) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        data = dict(DEFAULT_CONFIG)
        save_config(data)
        return data
    data.setdefault("visible", True)
    data.setdefault("items", [])
    return data


def save_config(data):
    os.makedirs(os.path.dirname(DOCK_CONFIG), exist_ok=True)
    with open(DOCK_CONFIG, "w") as fh:
        json.dump(data, fh, indent=2)


def signal_daemon(sig=signal.SIGUSR1):
    try:
        with open(PID_FILE) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, sig)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


# ── Dock window ───────────────────────────────────────────────────────

class Dock(Gtk.Window):
    def __init__(self):
        super().__init__(title="gp-dock")
        self.accent = read_accent()
        self.config = load_config()

        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_resizable(False)

        screen = self.get_screen()
        visual = screen.get_rgba_visual() if screen else None
        if visual:
            self.set_visual(visual)

        if USE_LAYER_SHELL:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, False)
            GtkLayerShell.set_exclusive_zone(self, DOCK_HEIGHT)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        else:
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)

        self._apply_css()
        self._build_ui()

        self.connect("destroy", lambda *_: Gtk.main_quit())

        # PID file + SIGUSR1 reload
        try:
            with open(PID_FILE, "w") as fh:
                fh.write(str(os.getpid()))
        except OSError:
            pass
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._reload)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._shutdown)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._shutdown)

        # Poll for theme changes
        GLib.timeout_add(3000, self._poll_theme)

    def _apply_css(self):
        r, g, b = hex_to_rgb(self.accent)
        css = f"""
        window {{
            background-color: rgba(8, 12, 10, 0.85);
            border-top: 1px solid rgba({r},{g},{b}, 0.45);
        }}
        button.dock-item {{
            background: transparent;
            border: none;
            padding: 6px;
            border-radius: 8px;
            min-width: {ITEM_SIZE}px;
            min-height: {ITEM_SIZE}px;
        }}
        button.dock-item:hover {{
            background: rgba({r},{g},{b}, 0.18);
        }}
        button.dock-item:active {{
            background: rgba({r},{g},{b}, 0.32);
        }}
        button.dock-logo {{
            background: rgba({r},{g},{b}, 0.10);
            border: 1px solid rgba({r},{g},{b}, 0.45);
            border-radius: 10px;
            padding: 4px;
            margin-right: 8px;
            min-width: {ITEM_SIZE}px;
            min-height: {ITEM_SIZE}px;
        }}
        button.dock-logo:hover {{
            background: rgba({r},{g},{b}, 0.22);
        }}
        separator.dock-sep {{
            background: rgba({r},{g},{b}, 0.30);
            min-width: 1px;
            margin: 8px 6px;
        }}
        popover, popover.background {{
            background-color: #0a100c;
            border: 1px solid rgba({r},{g},{b}, 0.55);
            padding: 4px;
        }}
        popover button {{
            background: transparent;
            color: #d6f7e3;
            border: none;
            padding: 8px 16px;
            min-width: 140px;
        }}
        popover button:hover {{
            background: rgba({r},{g},{b}, 0.22);
            color: #ffffff;
        }}
        """.encode()
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=ITEM_SPACING)
        outer.set_margin_start(SIDE_PADDING)
        outer.set_margin_end(SIDE_PADDING)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        self.add(outer)

        # GhostPort logo + power menu
        self.logo_btn = Gtk.Button()
        self.logo_btn.get_style_context().add_class("dock-logo")
        if os.path.isfile(LOGO_PATH):
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(
                    LOGO_PATH, ITEM_SIZE - 8, ITEM_SIZE - 8)
                self.logo_btn.set_image(Gtk.Image.new_from_pixbuf(pix))
            except Exception:
                self.logo_btn.set_label("GP")
        else:
            self.logo_btn.set_label("GP")
        self.logo_btn.set_tooltip_text("GhostPort — system menu")
        self.logo_btn.connect("clicked", self._show_power_menu)
        outer.pack_start(self.logo_btn, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.get_style_context().add_class("dock-sep")
        outer.pack_start(sep, False, False, 0)

        # Pinned items container — rebuilt on reload
        self.items_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=ITEM_SPACING)
        outer.pack_start(self.items_box, False, False, 0)

        self._rebuild_items()
        self._apply_visibility()

    def _rebuild_items(self):
        for child in self.items_box.get_children():
            self.items_box.remove(child)
        for entry in self.config.get("items", []):
            btn = self._make_item_button(entry)
            if btn:
                self.items_box.pack_start(btn, False, False, 0)
        self.items_box.show_all()

    def _make_item_button(self, entry):
        label = entry.get("label", "")
        icon = entry.get("icon", "")
        cmd = entry.get("command", "")
        if not cmd:
            return None
        btn = Gtk.Button()
        btn.get_style_context().add_class("dock-item")
        btn.set_tooltip_text(label or cmd)

        img = None
        if icon and os.path.isfile(icon):
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(
                    icon, ITEM_SIZE - 12, ITEM_SIZE - 12)
                img = Gtk.Image.new_from_pixbuf(pix)
            except Exception:
                img = None
        if img is None:
            # try ICON_DIR fallback
            alt = os.path.join(ICON_DIR, os.path.basename(icon)) if icon else ""
            if alt and os.path.isfile(alt):
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        alt, ITEM_SIZE - 12, ITEM_SIZE - 12)
                    img = Gtk.Image.new_from_pixbuf(pix)
                except Exception:
                    img = None
        if img is not None:
            btn.set_image(img)
        else:
            btn.set_label(label[:2].upper() if label else "?")

        btn.connect("clicked", lambda _b, c=cmd: self._launch(c))
        # Right-click to unpin
        btn.connect("button-press-event",
                    lambda _w, ev, name=label: self._maybe_unpin(ev, name))
        return btn

    def _maybe_unpin(self, event, name):
        if event.button != 3:  # right-click only
            return False
        items = self.config.get("items", [])
        self.config["items"] = [e for e in items if e.get("label") != name]
        save_config(self.config)
        self._rebuild_items()
        return True

    def _launch(self, command):
        try:
            argv = shlex.split(command)
            subprocess.Popen(argv, shell=False,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"gp-dock: launch failed: {exc}\n")

    # ── Power menu ─────────────────────────────────────────────────

    def _show_power_menu(self, _btn):
        popover = Gtk.Popover.new(self.logo_btn)
        popover.set_position(Gtk.PositionType.TOP)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        for label, action in [
            ("🔒  Lock",     self._do_lock),
            ("⏏  Logout",   self._do_logout),
            ("↻  Restart",  self._do_restart),
            ("⏻  Shutdown", self._do_shutdown),
        ]:
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.connect("clicked", lambda _w, a=action, p=popover: (p.popdown(), a()))
            box.pack_start(b, False, False, 0)

        popover.add(box)
        box.show_all()
        popover.popup()

    def _do_lock(self):
        subprocess.Popen(["swaylock", "-f"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _do_logout(self):
        subprocess.Popen(["pkill", "-SIGTERM", "labwc"], start_new_session=True)

    def _do_restart(self):
        subprocess.Popen(["systemctl", "reboot"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _do_shutdown(self):
        subprocess.Popen(["systemctl", "poweroff"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── Reload / visibility ────────────────────────────────────────

    def _reload(self):
        self.config = load_config()
        self._rebuild_items()
        self._apply_visibility()
        return True

    def _apply_visibility(self):
        if self.config.get("visible", True):
            self.show_all()
        else:
            self.hide()

    def _poll_theme(self):
        cur = read_accent()
        if cur != self.accent:
            self.accent = cur
            self._apply_css()
            # gp-theme sed-rewrites the SVG icon files on disk; our pinned
            # pixbufs were cached from the old files, so force a re-read
            # by rebuilding the buttons. Fixes stuck accent on dock icons
            # when the theme changes. Added 2026-04-21.
            self._rebuild_items()
        return True

    def _shutdown(self):
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass
        Gtk.main_quit()
        return False


# ── CLI ───────────────────────────────────────────────────────────────

def cli_set_visible(value):
    cfg = load_config()
    cfg["visible"] = bool(value)
    save_config(cfg)
    if not signal_daemon():
        sys.stderr.write("gp-dock: daemon not running; will apply on next start\n")


def cli_toggle():
    cfg = load_config()
    cfg["visible"] = not cfg.get("visible", True)
    save_config(cfg)
    state = "visible" if cfg["visible"] else "hidden"
    print(f"dock: {state}")
    if not signal_daemon():
        sys.stderr.write("gp-dock: daemon not running; will apply on next start\n")


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("-h", "--help"):
            print(__doc__)
            return
        if arg == "--show":
            cli_set_visible(True)
            return
        if arg == "--hide":
            cli_set_visible(False)
            return
        if arg == "--toggle":
            cli_toggle()
            return
        sys.stderr.write(f"gp-dock: unknown arg: {arg}\n")
        sys.exit(2)

    Dock()
    Gtk.main()


if __name__ == "__main__":
    main()
