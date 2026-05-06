#!/usr/bin/env python3
"""Phantom OS Desktop — single full-screen layer-shell canvas.

Renders all desktop icons (apps, widget tiles, ~/Desktop files) as sprites
on one Gtk.DrawingArea inside one BACKGROUND-layer window. Drag updates an
in-memory position and calls queue_draw(); GTK pipes that through the
Wayland frame-callback so motion stays vsync-aligned and smooth.

Compatibility contracts preserved from the previous one-window-per-icon
implementation:
  - icon-positions.json schema (name → {x, y, hidden?})
  - widget-layout.json schema
  - SIGUSR1 reloads positions
  - Dock pin via ~/.config/phantom/dock.json + signal to /tmp/gp-dock.pid
  - PID file at /tmp/gp-desktop-icons.pid + flock at /tmp/gp-desktop-icons.lock
"""

import cairo
import fcntl
import json
import math
import os
import shlex
import signal
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio, Pango, PangoCairo  # noqa: E402

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
ICON_DIR = "/opt/phantom/desktop/icons"
DESKTOP_DIR = os.path.expanduser("~/Desktop")
POSITIONS_FILE = os.path.expanduser("~/.config/phantom/icon-positions.json")
WIDGET_LAYOUT_FILE = os.path.expanduser("~/.config/phantom/widget-layout.json")
STATUS_CACHE = "/tmp/gp-bar-status.json"

# Each entry is (label, icon, command, subtitle).
# The subtitle is rendered as small-print under the pirate name on the icon grid
# and MUST match the parenthetical in menu.xml / _build_desktop_menu so the
# desktop and right-click menu speak the same vocabulary. Added 2026-04-20;
# replaced an earlier section-header experiment (user wanted per-icon captions).
DESKTOP_APPS = [
    ("Crow's Nest",    "gp-crowsnest.svg",   "python3 /opt/phantom/desktop/gp-crowsnest.py",    "Intrusion Detection"),
    ("Bulkhead",       "gp-bulkhead.svg",    "python3 /opt/phantom/desktop/gp-bulkhead.py",     "Firewall"),
    ("Dragnet",        "gp-dragnet.svg",     "python3 /opt/phantom/desktop/gp-dragnet.py",      "Packet Capture"),
    ("Anchor",         "gp-anchor.svg",      "python3 /opt/phantom/desktop/gp-anchor.py",       "Kill Switch"),
    ("Aether Box",     "gp-strongbox.svg",   "python3 /opt/phantom/desktop/gp-aetherbox.py",    "Encrypted Vault"),
    ("Tide Chart",     "gp-heatmap.svg",     "python3 /opt/phantom/desktop/gp-tidechart.py",    "Bandwidth Heatmap"),
    ("Sonar",          "gp-watchdog.svg",    "python3 /opt/phantom/desktop/gp-sonar.py",        "Rogue AP Scanner"),
    ("Crew Manifest",  "gp-roster.svg",      "python3 /opt/phantom/desktop/gp-crewmanifest.py", "Connected Clients"),
    ("Atlas",          "gp-atlas.svg",       "python3 /opt/phantom/desktop/gp-atlas.py",        "Network Topology"),
    ("Stonefish",      "gp-tripwire.svg",    "python3 /opt/phantom/desktop/gp-stonefish.py",    "ARP Guard"),
    ("Seadevil",        "gp-seadevil.svg",     "python3 /opt/phantom/desktop/gp-seadevil.py",      "MAC Randomizer"),
    ("Gangplank",      "gp-dock.svg",        "python3 /opt/phantom/desktop/gp-gangplank.py",    "USB Manager"),
    ("Sea Urchin",     "gp-vitals.svg",      "python3 /opt/phantom/desktop/gp-seaurchin.py",    "System Health"),
    ("Logbook",        "gp-logbook.svg",     "python3 /opt/phantom/desktop/gp-logbook.py",      "Event Log"),
    ("Quartermaster",  "gp-audit.svg",       "python3 /opt/phantom/desktop/gp-quartermaster.py", "Security Scan"),
    ("Dashboard",      "gp-dashboard.png",   "brave-browser --app=https://localhost:4201",         "Web UI"),
    ("Brave",          "gp-brave.png",       "brave-browser",                                      "Web Browser"),
    ("Widget Library", "gp-widget-library.svg", "python3 /opt/phantom/desktop/gp-widget-library.py", "Desktop Widgets"),
    ("Chamber",        "gp-chamber.svg",     "brave-browser --app=http://127.0.0.1:4242",          "AI Coordination"),
]

WIDGET_TILES = [
    ("score",   "Score"),
    ("mode",    "Mode"),
    ("tunnel",  "Tunnels"),
    ("ads",     "Ads Blocked"),
    ("clients", "Clients"),
]

ICON_SIZE = 64
CELL_WIDTH = 110
CELL_HEIGHT = 100
GRID_COLS = 5
GRID_PADDING_X = 40
GRID_PADDING_Y = 50
CELL_SPACING_X = 10
CELL_SPACING_Y = 8
SNAP_CELL_W = CELL_WIDTH + CELL_SPACING_X
SNAP_CELL_H = CELL_HEIGHT + CELL_SPACING_Y
DRAG_THRESHOLD = 5

# Bottom dock — height of the strip we treat as a "pin to dock" target
DOCK_HEIGHT = 72
DOCK_CONFIG = os.path.expanduser("~/.config/phantom/dock.json")
DOCK_PID_FILE = "/tmp/gp-dock.pid"


# ── Helpers ───────────────────────────────────────────────────────────

def read_theme():
    try:
        with open(THEME_FILE) as f:
            return json.load(f).get("color", "#39ff8f")
    except Exception:
        return "#39ff8f"


def hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def load_positions():
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_positions(positions):
    os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
    except OSError as e:
        print(f"Save positions error: {e}", file=sys.stderr)


def default_position(idx):
    col = idx % GRID_COLS
    row = idx // GRID_COLS
    x = GRID_PADDING_X + col * SNAP_CELL_W
    y = GRID_PADDING_Y + row * SNAP_CELL_H
    return x, y


def read_status_cache():
    try:
        with open(STATUS_CACHE) as f:
            return json.load(f)
    except Exception:
        return None


def load_widget_layout():
    try:
        with open(WIDGET_LAYOUT_FILE) as f:
            data = json.load(f)
            return data.get("widgets", {})
    except Exception:
        return {}


def load_icon_pixbuf(filename, size=ICON_SIZE):
    if not filename:
        return None
    path = os.path.join(ICON_DIR, filename)
    if not os.path.isfile(path):
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except Exception:
        return None


def load_file_icon_pixbuf(filepath, size=ICON_SIZE):
    """Load a file icon from the system icon theme."""
    try:
        gfile = Gio.File.new_for_path(filepath)
        info = gfile.query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None)
        gicon = info.get_icon()
        theme = Gtk.IconTheme.get_default()
        icon_info = theme.lookup_by_gicon(gicon, size, Gtk.IconLookupFlags.FORCE_SIZE)
        if icon_info:
            return icon_info.load_icon()
    except Exception:
        pass
    return None


# ── Sprite — one drawable item on the canvas ──────────────────────────

class Sprite:
    """A draggable item rendered on the desktop canvas."""

    def __init__(self, name, kind, label, x, y, command="",
                 icon_file="", widget_id="", subtitle=""):
        self.name = name        # canonical key (e.g. "Crow's Nest", "widget:score")
        self.kind = kind        # "app" | "widget" | "file"
        self.label = label      # display label
        self.subtitle = subtitle  # small-print role description under label
        self.x = x
        self.y = y
        self.w = CELL_WIDTH
        self.h = CELL_HEIGHT
        self.command = command
        self.icon_file = icon_file
        self.widget_id = widget_id
        self.pixbuf = None      # GdkPixbuf for app/file
        self.icon_mtime = 0     # for SVG hot-reload
        self.hovered = False
        self.dragging = False
        self.drag_started = False

    def contains(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2


# ── DesktopCanvas — single full-screen window with all sprites ───────

class DesktopCanvas:
    """Full-screen background-layer canvas hosting all desktop sprites."""

    def __init__(self):
        self.accent = read_theme()
        self.positions = load_positions()
        self.sprites = []                    # all sprites in z-order (last = top)
        self.status_data = read_status_cache() or {}
        self._desktop_files = []

        # Drag state
        self.dragged = None                  # Sprite currently being dragged
        self._anchor_x = 0                   # offset cursor→sprite origin (screen)
        self._anchor_y = 0
        self._press_x_root = 0
        self._press_y_root = 0
        self._drag_origin_x = 0
        self._drag_origin_y = 0
        self._max_x = 0
        self._max_y = 0

        # Cached screen geometry
        self.screen_w = 1920
        self.screen_h = 1080
        self._refresh_screen_size()

        # Build window
        self.win = Gtk.Window(title="gp-desktop")
        self.win.set_decorated(False)
        self.win.set_app_paintable(True)
        screen = self.win.get_screen()
        visual = screen.get_rgba_visual() if screen else None
        if visual:
            self.win.set_visual(visual)

        if USE_LAYER_SHELL:
            GtkLayerShell.init_for_window(self.win)
            GtkLayerShell.set_layer(self.win, GtkLayerShell.Layer.BACKGROUND)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self.win, edge, True)
            GtkLayerShell.set_exclusive_zone(self.win, -1)
            GtkLayerShell.set_keyboard_mode(self.win, GtkLayerShell.KeyboardMode.NONE)
        else:
            self.win.set_keep_below(True)
            self.win.set_skip_taskbar_hint(True)
            self.win.set_skip_pager_hint(True)
            self.win.set_default_size(self.screen_w, self.screen_h)

        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.area.connect("draw", self._on_draw)
        self.area.connect("button-press-event", self._on_press)
        self.area.connect("button-release-event", self._on_release)
        self.area.connect("motion-notify-event", self._on_motion)
        self.area.connect("leave-notify-event", self._on_leave)
        self.win.add(self.area)

        # Build initial sprite set
        self._rebuild_app_sprites()
        self._rebuild_widget_sprites()
        self._rebuild_file_sprites()
        self._clamp_all_to_screen()

        # Polling
        GLib.timeout_add(3000, self._poll)
        GLib.timeout_add(5000, self._poll_widget_data)

        # Signals
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._reload)

    # ── screen geometry ────────────────────────────────────────────

    def _refresh_screen_size(self):
        screen = Gdk.Screen.get_default()
        if not screen:
            return
        mon = screen.get_primary_monitor()
        geom = screen.get_monitor_geometry(mon)
        self.screen_w = geom.width
        self.screen_h = geom.height

    def _clamp_xy(self, x, y):
        max_x = max(0, self.screen_w - CELL_WIDTH)
        max_y = max(0, self.screen_h - CELL_HEIGHT - DOCK_HEIGHT)
        return max(0, min(max_x, x)), max(0, min(max_y, y))

    def _clamp_all_to_screen(self):
        """Pull any saved sprite positions back into visible area."""
        changed = False
        for sp in self.sprites:
            cx, cy = self._clamp_xy(sp.x, sp.y)
            if cx != sp.x or cy != sp.y:
                sp.x, sp.y = cx, cy
                self.positions[sp.name] = {"x": cx, "y": cy}
                changed = True
        if changed:
            save_positions(self.positions)

    # ── sprite construction ────────────────────────────────────────

    def _make_app_sprite(self, idx, name, icon_file, command, subtitle=""):
        saved = self.positions.get(name)
        if isinstance(saved, dict):
            x = saved.get("x", default_position(idx)[0])
            y = saved.get("y", default_position(idx)[1])
        else:
            x, y = default_position(idx)
        sp = Sprite(name, "app", name, x, y, command=command,
                    icon_file=icon_file, subtitle=subtitle)
        sp.pixbuf = load_icon_pixbuf(icon_file)
        path = os.path.join(ICON_DIR, icon_file)
        try:
            sp.icon_mtime = os.path.getmtime(path)
        except OSError:
            sp.icon_mtime = 0
        return sp

    def _make_widget_sprite(self, idx, wid, label, widget_count):
        pos_key = f"widget:{wid}"
        saved = self.positions.get(pos_key)
        if isinstance(saved, dict):
            x = saved.get("x", 0)
            y = saved.get("y", 0)
        else:
            col = 4 + (widget_count // 8)
            row = widget_count % 8
            x = GRID_PADDING_X + col * SNAP_CELL_W
            y = GRID_PADDING_Y + row * SNAP_CELL_H
        return Sprite(pos_key, "widget", label, x, y, widget_id=wid)

    def _make_file_sprite(self, file_idx, filename):
        filepath = os.path.join(DESKTOP_DIR, filename)
        file_key = f"file:{filename}"
        saved = self.positions.get(file_key)
        if isinstance(saved, dict):
            x, y = saved.get("x", 0), saved.get("y", 0)
        else:
            x, y = default_position(len(DESKTOP_APPS) + len(WIDGET_TILES) + file_idx)
        display = filename[:-8] if filename.endswith(".desktop") else filename
        sp = Sprite(file_key, "file", display, x, y,
                    command=f"xdg-open {shlex.quote(filepath)}")
        sp.pixbuf = load_file_icon_pixbuf(filepath)
        return sp

    def _rebuild_app_sprites(self):
        # Drop existing app sprites; rebuild from DESKTOP_APPS (skipping hidden)
        self.sprites = [s for s in self.sprites if s.kind != "app"]
        for idx, entry in enumerate(DESKTOP_APPS):
            name, icon_file, command = entry[0], entry[1], entry[2]
            subtitle = entry[3] if len(entry) > 3 else ""
            saved = self.positions.get(name)
            if isinstance(saved, dict) and saved.get("hidden"):
                continue
            self.sprites.append(
                self._make_app_sprite(idx, name, icon_file, command, subtitle))

    def _rebuild_widget_sprites(self):
        self.sprites = [s for s in self.sprites if s.kind != "widget"]
        layout = load_widget_layout()
        widget_count = 0
        for idx, (wid, label) in enumerate(WIDGET_TILES):
            if not layout.get(wid, {}).get("enabled", False):
                continue
            self.sprites.append(self._make_widget_sprite(idx, wid, label, widget_count))
            widget_count += 1

    def _rebuild_file_sprites(self):
        self.sprites = [s for s in self.sprites if s.kind != "file"]
        os.makedirs(DESKTOP_DIR, exist_ok=True)
        try:
            files = sorted(f for f in os.listdir(DESKTOP_DIR) if not f.startswith("."))
        except OSError:
            files = []
        self._desktop_files = files
        for idx, filename in enumerate(files):
            self.sprites.append(self._make_file_sprite(idx, filename))

    # ── hit-test ───────────────────────────────────────────────────

    def _hit(self, x, y):
        # iterate top-down (last drawn = topmost)
        for sp in reversed(self.sprites):
            if sp.contains(x, y):
                return sp
        return None

    # ── input handlers ─────────────────────────────────────────────

    def _on_press(self, _w, event):
        if event.button == 3:
            # Right-click: context menu (sprite-specific or desktop-wide)
            sp = self._hit(int(event.x), int(event.y))
            if sp is None:
                self._show_desktop_menu(event)
            else:
                self._show_sprite_menu(sp, event)
            return True
        if event.button == 2:
            # Middle-click: trigger labwc's client-list menu via W-F12 keybind.
            # T-0012: subprocess.run with timeout instead of fire-and-forget
            # Popen. wtype emits the chord and exits in ms; 5s catches the
            # case where the compositor's input device is unreachable.
            try:
                subprocess.run(
                    ["wtype", "-M", "logo", "-k", "F12", "-m", "logo"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            return True
        if event.button != 1:
            return False
        sp = self._hit(int(event.x), int(event.y))
        if sp is None:
            return False
        self.dragged = sp
        sp.dragging = True
        sp.drag_started = False
        self._anchor_x = event.x_root - sp.x
        self._anchor_y = event.y_root - sp.y
        self._press_x_root = event.x_root
        self._press_y_root = event.y_root
        self._drag_origin_x = sp.x
        self._drag_origin_y = sp.y
        self._refresh_screen_size()
        # Bring sprite to top so it draws over neighbors during drag
        self.sprites.remove(sp)
        self.sprites.append(sp)
        self.area.queue_draw()
        return True

    def _on_motion(self, _w, event):
        # Hover updates (when not dragging)
        if self.dragged is None:
            new_hover = self._hit(int(event.x), int(event.y))
            changed = False
            for sp in self.sprites:
                want = sp is new_hover
                if sp.hovered != want:
                    sp.hovered = want
                    changed = True
            if changed:
                self.area.queue_draw()
            return False

        sp = self.dragged
        dx = event.x_root - self._press_x_root
        dy = event.y_root - self._press_y_root
        if not sp.drag_started:
            if abs(dx) <= DRAG_THRESHOLD and abs(dy) <= DRAG_THRESHOLD:
                return True
            sp.drag_started = True

        new_x, new_y = self._clamp_xy(int(event.x_root - self._anchor_x),
                                      int(event.y_root - self._anchor_y))
        if new_x != sp.x or new_y != sp.y:
            sp.x = new_x
            sp.y = new_y
            self.area.queue_draw()
        return True

    def _on_release(self, _w, event):
        if event.button != 1 or self.dragged is None:
            return False
        sp = self.dragged
        was_drag = sp.drag_started
        sp.dragging = False
        sp.drag_started = False
        self.dragged = None

        if not was_drag:
            self._launch(sp)
            self.area.queue_draw()
            return True

        cx, cy = sp.center()

        # Pin to dock — keep on desktop, snap back to origin
        if cy >= self.screen_h - DOCK_HEIGHT:
            if self._pin_to_dock(sp):
                sp.x, sp.y = self._drag_origin_x, self._drag_origin_y
                self.area.queue_draw()
                return True

        # Snap to grid + collision avoidance
        fx, fy = self._snap_and_resolve(sp.x, sp.y, sp.name)
        sp.x, sp.y = fx, fy
        self.positions[sp.name] = {"x": fx, "y": fy}
        save_positions(self.positions)
        self.area.queue_draw()
        return True

    def _on_leave(self, _w, _event):
        if self.dragged is not None:
            return False
        changed = False
        for sp in self.sprites:
            if sp.hovered:
                sp.hovered = False
                changed = True
        if changed:
            self.area.queue_draw()
        return False

    # ── right-click context menus ──────────────────────────────────

    def _is_pinned_to_dock(self, label):
        try:
            with open(DOCK_CONFIG) as fh:
                cfg = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        return any(it.get("label") == label for it in cfg.get("items", []))

    def _unpin_from_dock(self, label):
        try:
            with open(DOCK_CONFIG) as fh:
                cfg = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        items = cfg.get("items", [])
        new_items = [it for it in items if it.get("label") != label]
        if len(new_items) == len(items):
            return False
        cfg["items"] = new_items
        os.makedirs(os.path.dirname(DOCK_CONFIG), exist_ok=True)
        with open(DOCK_CONFIG, "w") as fh:
            json.dump(cfg, fh, indent=2)
        try:
            with open(DOCK_PID_FILE) as fh:
                os.kill(int(fh.read().strip()), signal.SIGUSR1)
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            pass
        return True

    def _show_sprite_menu(self, sp, event):
        menu = Gtk.Menu()
        # Open
        if sp.command:
            mi = Gtk.MenuItem(label="Open")
            mi.connect("activate", lambda _m: self._launch(sp))
            menu.append(mi)
            menu.append(Gtk.SeparatorMenuItem())

        # Pin / Unpin (apps only)
        if sp.kind == "app":
            if self._is_pinned_to_dock(sp.name):
                mi = Gtk.MenuItem(label="Unpin from Dock")
                mi.connect("activate", lambda _m: self._unpin_from_dock(sp.name))
            else:
                mi = Gtk.MenuItem(label="Pin to Dock")
                mi.connect("activate", lambda _m: self._pin_to_dock(sp))
            menu.append(mi)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _show_desktop_menu(self, event):
        """Right-click on empty desktop — native GTK context menu at cursor.

        Mirrors ~/.config/labwc/menu.xml. Uses a real Gtk.Menu (not fuzzel)
        so it behaves like a standard OS right-click menu.
        """
        menu = self._build_desktop_menu()
        menu.show_all()
        menu.popup_at_pointer(event)
        self._desktop_menu_ref = menu  # keep alive until dismissed

    def _build_desktop_menu(self):
        # Source of truth for the right-click menu tree. Mirrors menu.xml but
        # is what's actually served (the icons overlay covers labwc's root, so
        # menu.xml never reaches the pointer). Keep both in sync when editing.
        # Privacy Tools / Monitoring / Terminal Tools split added 2026-04-20
        # when the old fuzzel-based gp-menu was replaced — TUIs moved into a
        # clearly-labeled Terminal Tools bucket to end the GTK-vs-TUI duplication.
        FOOT = "xfce4-terminal -x "
        HOME = os.path.expanduser("~")
        DESK = "/opt/phantom/desktop"
        tree = [
            ("Quick Settings", [f"{HOME}/.local/bin/gp-quick-settings"]),
            ("Switch Mode",    [f"{HOME}/.local/bin/gp-mode-menu"]),
            ("Dashboard",      ["brave-browser", "--app=https://localhost:4201"]),
            ("---",            None),
            ("Privacy Tools", [
                ("Bulkhead (Firewall)",           ["python3", f"{DESK}/gp-bulkhead.py"]),
                ("Anchor (Kill Switch)",          ["python3", f"{DESK}/gp-anchor.py"]),
                ("Stonefish (ARP Guard)",         ["python3", f"{DESK}/gp-stonefish.py"]),
                ("Seadevil (MAC Randomizer)",      ["python3", f"{DESK}/gp-seadevil.py"]),
                ("Aether Box (Encrypted Vault)",  ["python3", f"{DESK}/gp-aetherbox.py"]),
                ("Quartermaster (Security Scan)", ["python3", f"{DESK}/gp-quartermaster.py"]),
            ]),
            ("Monitoring", [
                ("Crow's Nest (Intrusion Detection)", ["python3", f"{DESK}/gp-crowsnest.py"]),
                ("Dragnet (Packet Capture)",          ["python3", f"{DESK}/gp-dragnet.py"]),
                ("Sonar (Rogue AP Scanner)",          ["python3", f"{DESK}/gp-sonar.py"]),
                ("Atlas (Network Topology)",          ["python3", f"{DESK}/gp-atlas.py"]),
                ("Tide Chart (Bandwidth Heatmap)",    ["python3", f"{DESK}/gp-tidechart.py"]),
                ("Crew Manifest (Connected Clients)", ["python3", f"{DESK}/gp-crewmanifest.py"]),
                ("Logbook (Event Log)",               ["python3", f"{DESK}/gp-logbook.py"]),
                ("Sea Urchin (System Health)",        ["python3", f"{DESK}/gp-seaurchin.py"]),
                ("Gangplank (USB Manager)",           ["python3", f"{DESK}/gp-gangplank.py"]),
            ]),
            ("Terminal Tools", [
                ("Clipboard Privacy",  FOOT + f"{HOME}/.local/bin/gp-clipboard"),
                ("Privacy Report",     FOOT + f"{HOME}/.local/bin/gp-privacy-report"),
                ("Privacy Digest",     FOOT + f"{HOME}/.local/bin/gp-digest --full"),
            ]),
            ("Network", [
                ("WiFi WAN Settings",   ["bash", "-c", "xfce4-terminal -x bash -c\"sudo gp-wan status; echo; echo 'Commands: gp-wan scan | gp-wan connect SSID pass'; bash\""]),
                ("Pi-hole Admin",       ["brave-browser", "--app=http://localhost/admin"]),
                ("Mode Status",         ["bash", "-c", "xfce4-terminal -x bash -c\"sudo gp-mode status; bash\""]),
            ]),
            ("System", [
                ("About This System",   FOOT + f"{HOME}/.local/bin/gp-about"),
                ("System Update",       FOOT + f"{HOME}/.local/bin/gp-updater"),
                ("Help & Shortcuts",    FOOT + f"{HOME}/.local/bin/gp-help"),
                ("Preflight Check",     ["bash", "-c", "xfce4-terminal -x bash -c\"sudo gp-preflight; echo; read -p 'Press Enter to close...'\""]),
                ("System Monitor",      FOOT + "htop"),
                ("Server Logs",         ["bash", "-c", "xfce4-terminal -x bash -c\"sudo journalctl -u ghostport -f\""]),
            ]),
            ("Desktop", [
                ("Widget Library",      [f"{HOME}/.local/bin/gp-widgets", "library"]),
                ("Theme Picker",        ["bash", "-c", f"xfce4-terminal -x {HOME}/.local/bin/gp-theme menu"]),
                ("Pirate Cursors",      ["bash", "-c", f"{HOME}/.local/bin/gp-cursor pirate && notify-send 'Phantom OS' 'Pirate cursors ON' && labwc --reconfigure"]),
                ("Default Cursors",     ["bash", "-c", f"{HOME}/.local/bin/gp-cursor default && notify-send 'Phantom OS' 'Default cursors restored' && labwc --reconfigure"]),
                ("Chamber Chat",        ["brave-browser", "--app=http://127.0.0.1:4242"]),
                ("Start Menu",          [f"{HOME}/.local/bin/gp-menu"]),
            ]),
            ("---",            None),
            ("Terminal",       ["xfce4-terminal"]),
            ("Files",          ["thunar"]),
            ("Browser",        ["brave-browser"]),
            ("Code Editor",    ["geany"]),
            ("Screenshot",     ["bash", "-c", f"mkdir -p {HOME}/Screenshots && grim -g \"$(slurp)\" {HOME}/Screenshots/$(date +%Y%m%d_%H%M%S).png"]),
            ("---",            None),
            ("Development", [
                ("Open Server Code",    ["geany", "/opt/phantom/ghostport-server.js"]),
                ("Git Log",             ["bash", "-c", "xfce4-terminal -x bash -c\"cd /opt/phantom && git log --oneline -20; bash\""]),
                ("Restart Server",      ["bash", "-c", "xfce4-terminal -x bash -c\"sudo systemctl restart ghostport; echo Server restarted; sleep 2\""]),
            ]),
            ("Power", [
                ("Lock Screen",         ["swaylock"]),
                ("Reboot",              ["bash", "-c", "xfce4-terminal -x bash -c\"echo 'Rebooting in 3 seconds...'; sleep 3; sudo reboot\""]),
                ("Shutdown",            ["bash", "-c", "xfce4-terminal -x bash -c\"echo 'Shutting down in 3 seconds...'; sleep 3; sudo poweroff\""]),
            ]),
        ]
        return self._build_menu_from_tree(tree)

    def _build_menu_from_tree(self, tree):
        menu = Gtk.Menu()
        for label, payload in tree:
            if label == "---":
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            if isinstance(payload, list) and payload and isinstance(payload[0], tuple):
                # submenu: list of (label, argv-or-shellstring) tuples
                sub = Gtk.Menu()
                for sub_label, sub_cmd in payload:
                    smi = Gtk.MenuItem(label=sub_label)
                    smi.connect("activate", self._make_menu_launcher(sub_cmd))
                    sub.append(smi)
                mi.set_submenu(sub)
            else:
                mi.connect("activate", self._make_menu_launcher(payload))
            menu.append(mi)
        return menu

    def _make_menu_launcher(self, cmd):
        if isinstance(cmd, str):
            argv = shlex.split(cmd)
        else:
            argv = list(cmd)
        def _run(_mi):
            # T-0012: long-running app launcher. Cannot wrap in
            # subprocess.run with timeout — the launched app is meant to
            # outlive this function call (Brave, terminals, etc.).
            # start_new_session=True puts the child in its own process
            # group so it's reaped by init when the user closes it,
            # not by us — that's the orphan-prevention mechanism.
            try:
                subprocess.Popen(argv, shell=False,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except (OSError, ValueError) as exc:
                print(f"Menu launch error ({argv!r}): {exc}", file=sys.stderr)
        return _run

    # ── click action ───────────────────────────────────────────────

    def _launch(self, sp):
        if not sp.command:
            return
        # T-0012: long-running app launcher (icon click). Same rationale
        # as _make_menu_launcher — start_new_session=True is the
        # orphan-prevention path; a timeout on Popen would kill the app
        # the user is trying to use.
        try:
            argv = shlex.split(sp.command)
            subprocess.Popen(argv, shell=False,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except (OSError, ValueError) as e:
            print(f"Launch error: {e}", file=sys.stderr)

    # ── grid snap / collision ──────────────────────────────────────

    def _snap_to_grid(self, x, y):
        col = max(0, round((x - GRID_PADDING_X) / SNAP_CELL_W))
        row = max(0, round((y - GRID_PADDING_Y) / SNAP_CELL_H))
        return GRID_PADDING_X + col * SNAP_CELL_W, GRID_PADDING_Y + row * SNAP_CELL_H

    def _snap_and_resolve(self, x, y, exclude_name):
        sx, sy = self._snap_to_grid(x, y)
        sx, sy = self._clamp_xy(sx, sy)
        occupied = {(s.x, s.y) for s in self.sprites if s.name != exclude_name}
        if (sx, sy) not in occupied:
            return sx, sy
        # Spiral outward for nearest free cell
        base_col = round((sx - GRID_PADDING_X) / SNAP_CELL_W)
        base_row = round((sy - GRID_PADDING_Y) / SNAP_CELL_H)
        for radius in range(1, 30):
            for dc in range(-radius, radius + 1):
                for dr in range(-radius, radius + 1):
                    if abs(dc) != radius and abs(dr) != radius:
                        continue
                    cx = GRID_PADDING_X + (base_col + dc) * SNAP_CELL_W
                    cy = GRID_PADDING_Y + (base_row + dr) * SNAP_CELL_H
                    if cx < 0 or cy < 0:
                        continue
                    cx, cy = self._clamp_xy(cx, cy)
                    if (cx, cy) not in occupied:
                        return cx, cy
        return sx, sy

    # ── dock zone ──────────────────────────────────────────────────

    def _pin_to_dock(self, sp):
        if sp.kind != "app":
            return False
        app = next((a for a in DESKTOP_APPS if a[0] == sp.name), None)
        if not app:
            return False
        label, icon_file, cmd = app[0], app[1], app[2]
        try:
            with open(DOCK_CONFIG) as fh:
                cfg = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            cfg = {"visible": True, "items": []}
        items = cfg.setdefault("items", [])
        if any(it.get("label") == label for it in items):
            return False
        items.append({
            "label": label,
            "icon": os.path.join(ICON_DIR, icon_file),
            "command": cmd,
        })
        os.makedirs(os.path.dirname(DOCK_CONFIG), exist_ok=True)
        with open(DOCK_CONFIG, "w") as fh:
            json.dump(cfg, fh, indent=2)
        try:
            with open(DOCK_PID_FILE) as fh:
                os.kill(int(fh.read().strip()), signal.SIGUSR1)
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            pass
        return True

    # ── polling ────────────────────────────────────────────────────

    def _poll(self):
        # Theme
        new_accent = read_theme()
        theme_changed = (new_accent != self.accent)
        if theme_changed:
            self.accent = new_accent

        # Icon SVG mtime — reload pixbuf if file changed on disk
        sprites_changed = False
        for sp in self.sprites:
            if sp.kind != "app" or not sp.icon_file:
                continue
            path = os.path.join(ICON_DIR, sp.icon_file)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime != sp.icon_mtime:
                sp.icon_mtime = mtime
                sp.pixbuf = load_icon_pixbuf(sp.icon_file)
                sprites_changed = True

        # Widget layout — rebuild widget sprites if enable/disable changed
        layout = load_widget_layout()
        current = {wid for wid, _ in WIDGET_TILES
                   if layout.get(wid, {}).get("enabled", False)}
        existing = {sp.widget_id for sp in self.sprites if sp.kind == "widget"}
        if current != existing:
            self._rebuild_widget_sprites()
            self._clamp_all_to_screen()
            sprites_changed = True

        # Desktop files
        try:
            current_files = sorted(f for f in os.listdir(DESKTOP_DIR)
                                   if not f.startswith("."))
        except OSError:
            current_files = []
        if current_files != self._desktop_files:
            self._rebuild_file_sprites()
            self._clamp_all_to_screen()
            sprites_changed = True

        if theme_changed or sprites_changed:
            self.area.queue_draw()
        return True

    def _poll_widget_data(self):
        data = read_status_cache()
        if data and data != self.status_data:
            self.status_data = data
            if any(s.kind == "widget" for s in self.sprites):
                self.area.queue_draw()
        return True

    def _reload(self):
        """SIGUSR1 — reload positions/layout from disk."""
        self.positions = load_positions()
        self._rebuild_app_sprites()
        self._rebuild_widget_sprites()
        self._rebuild_file_sprites()
        self._clamp_all_to_screen()
        self.area.queue_draw()
        return True

    # ── drawing ────────────────────────────────────────────────────

    def _on_draw(self, _w, cr):
        # Transparent canvas
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        for sp in self.sprites:
            self._draw_sprite(cr, sp)

        return False

    def _draw_sprite(self, cr, sp):
        r, g, b = hex_to_rgb(self.accent)
        rf, gf, bf = r / 255, g / 255, b / 255

        # Hover/drag glow background
        if sp.hovered or sp.dragging:
            if sp.drag_started:
                bg_alpha, border_alpha, border_w = 0.28, 0.85, 2.0
            elif sp.dragging:
                bg_alpha, border_alpha, border_w = 0.18, 0.0, 0.0
            else:
                bg_alpha, border_alpha, border_w = 0.10, 0.0, 0.0
            self._rounded_rect(cr, sp.x + 4, sp.y + 4, sp.w - 8, sp.h - 8, 8)
            cr.set_source_rgba(rf, gf, bf, bg_alpha)
            if border_alpha > 0:
                cr.fill_preserve()
                cr.set_source_rgba(rf, gf, bf, border_alpha)
                cr.set_line_width(border_w)
                cr.stroke()
            else:
                cr.fill()

        # Reduce opacity slightly while actively dragging
        cr.save()
        if sp.drag_started:
            cr.push_group()

        if sp.kind == "widget":
            self._draw_widget_tile(cr, sp, rf, gf, bf)
        else:
            self._draw_app_tile(cr, sp, rf, gf, bf)

        if sp.drag_started:
            cr.pop_group_to_source()
            cr.paint_with_alpha(0.85)
        cr.restore()

    def _draw_app_tile(self, cr, sp, rf, gf, bf):
        # Icon — center horizontally, top portion of the cell
        if sp.pixbuf is not None:
            iw = sp.pixbuf.get_width()
            ih = sp.pixbuf.get_height()
            ix = sp.x + (sp.w - iw) // 2
            iy = sp.y + 4
            Gdk.cairo_set_source_pixbuf(cr, sp.pixbuf, ix, iy)
            cr.paint()

        # Label — bold accent
        label_layout = PangoCairo.create_layout(cr)
        label_layout.set_text(sp.label, -1)
        label_layout.set_font_description(Pango.FontDescription("monospace Bold 9"))
        label_layout.set_width(Pango.SCALE * (sp.w - 4))
        label_layout.set_alignment(Pango.Alignment.CENTER)
        label_layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        label_layout.set_ellipsize(Pango.EllipsizeMode.END)
        _, label_ext = label_layout.get_pixel_extents()

        # Subtitle — smaller, dimmer, single line
        sub_layout = None
        sub_h = 0
        subtitle = getattr(sp, "subtitle", "") or ""
        if subtitle:
            sub_layout = PangoCairo.create_layout(cr)
            sub_layout.set_text(subtitle, -1)
            sub_layout.set_font_description(Pango.FontDescription("Sans 7"))
            sub_layout.set_width(Pango.SCALE * (sp.w - 4))
            sub_layout.set_alignment(Pango.Alignment.CENTER)
            sub_layout.set_ellipsize(Pango.EllipsizeMode.END)
            _, sub_ext = sub_layout.get_pixel_extents()
            sub_h = sub_ext.height + 1

        # Stack label + subtitle, bottom-anchored
        total_h = label_ext.height + sub_h
        bottom_pad = 4
        label_y = sp.y + sp.h - bottom_pad - total_h

        # Label shadow + foreground
        cr.set_source_rgba(0, 0, 0, 0.85)
        cr.move_to(sp.x + 2 + 1, label_y + 1)
        PangoCairo.show_layout(cr, label_layout)
        cr.set_source_rgba(rf, gf, bf, 1.0)
        cr.move_to(sp.x + 2, label_y)
        PangoCairo.show_layout(cr, label_layout)

        # Subtitle shadow + foreground (dimmer)
        if sub_layout is not None:
            sub_y = label_y + label_ext.height + 1
            cr.set_source_rgba(0, 0, 0, 0.75)
            cr.move_to(sp.x + 2 + 1, sub_y + 1)
            PangoCairo.show_layout(cr, sub_layout)
            cr.set_source_rgba(rf, gf, bf, 0.65)
            cr.move_to(sp.x + 2, sub_y)
            PangoCairo.show_layout(cr, sub_layout)

    def _draw_widget_tile(self, cr, sp, rf, gf, bf):
        # Tile background — always visible (more prominent than apps)
        if sp.drag_started:
            bg_alpha, border_alpha, border_w = 0.22, 0.85, 2.0
        elif sp.hovered or sp.dragging:
            bg_alpha, border_alpha, border_w = 0.12, 0.35, 1.5
        else:
            bg_alpha, border_alpha, border_w = 0.06, 0.20, 1.5
        pad = 4
        self._rounded_rect(cr, sp.x + pad, sp.y + pad,
                           sp.w - pad * 2, sp.h - pad * 2, 8)
        cr.set_source_rgba(rf, gf, bf, bg_alpha)
        cr.fill_preserve()
        cr.set_source_rgba(rf, gf, bf, border_alpha)
        cr.set_line_width(border_w)
        cr.stroke()

        # Widget content
        draw_fn = getattr(self, f"_w_{sp.widget_id}", None)
        if callable(draw_fn):
            draw_fn(cr, sp, rf, gf, bf)  # pylint: disable=not-callable  # callable() narrows; pylint can't follow

        # Label at bottom
        cr.set_source_rgba(rf, gf, bf, 0.8)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(9)
        ext = cr.text_extents(sp.label)
        cr.move_to(sp.x + sp.w / 2 - ext.width / 2, sp.y + sp.h - 10)
        cr.show_text(sp.label)

    # ── widget content draw funcs ──────────────────────────────────

    def _w_score(self, cr, sp, rf, gf, bf):
        cx = sp.x + sp.w / 2
        cy = sp.y + 38
        radius = 22
        score = self.status_data.get("securityScore", 0)
        cr.set_line_width(4)
        cr.set_source_rgba(rf, gf, bf, 0.15)
        cr.arc(cx, cy, radius, 0.7 * math.pi, 2.3 * math.pi)
        cr.stroke()
        if score > 0:
            frac = max(0, min(100, score)) / 100.0
            end_angle = 0.7 * math.pi + frac * 1.6 * math.pi
            if score >= 70:
                cr.set_source_rgba(rf, gf, bf, 0.9)
            elif score >= 40:
                cr.set_source_rgba(1.0, 0.8, 0.2, 0.9)
            else:
                cr.set_source_rgba(1.0, 0.3, 0.2, 0.9)
            cr.arc(cx, cy, radius, 0.7 * math.pi, end_angle)
            cr.stroke()
        cr.set_source_rgba(rf, gf, bf, 1.0)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(18)
        txt = str(score)
        ext = cr.text_extents(txt)
        cr.move_to(cx - ext.width / 2, cy + 7)
        cr.show_text(txt)

    def _w_mode(self, cr, sp, rf, gf, bf):
        cx = sp.x + sp.w / 2
        mode = self.status_data.get("activeMode", "---")
        display = mode.upper() if mode else "---"
        cr.set_source_rgba(rf, gf, bf, 0.9)
        cr.arc(cx, sp.y + 28, 4, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(rf, gf, bf, 1.0)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(12)
        ext = cr.text_extents(display)
        cr.move_to(cx - ext.width / 2, sp.y + 52)
        cr.show_text(display)

    def _w_tunnel(self, cr, sp, rf, gf, bf):
        tunnels = self.status_data.get("tunnels", {})
        items = [
            ("WG0", tunnels.get("wg0", "?")),
            ("WG1", tunnels.get("wg1", "?")),
            ("TS",  tunnels.get("tailscale", "?")),
        ]
        cx = sp.x + sp.w / 2
        for i, (name, status) in enumerate(items):
            y = sp.y + 22 + i * 18
            is_up = status == "up"
            if is_up:
                cr.set_source_rgba(rf, gf, bf, 0.9)
            else:
                cr.set_source_rgba(1.0, 0.3, 0.2, 0.7)
            cr.arc(cx - 22, y, 3, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(rf, gf, bf, 0.8)
            cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(11)
            cr.move_to(cx - 12, y + 4)
            cr.show_text(f"{name} {status.upper()}")

    def _w_ads(self, cr, sp, rf, gf, bf):
        cx = sp.x + sp.w / 2
        ads = self.status_data.get("adsBlocked", 0)
        alltime = self.status_data.get("adsBlockedAllTime", 0)
        cr.set_source_rgba(rf, gf, bf, 1.0)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(20)
        txt = f"{ads:,}"
        ext = cr.text_extents(txt)
        cr.move_to(cx - ext.width / 2, sp.y + 42)
        cr.show_text(txt)
        cr.set_source_rgba(rf, gf, bf, 0.5)
        cr.set_font_size(8)
        atxt = f"{alltime:,} total"
        ext2 = cr.text_extents(atxt)
        cr.move_to(cx - ext2.width / 2, sp.y + 56)
        cr.show_text(atxt)

    def _w_clients(self, cr, sp, rf, gf, bf):
        cx = sp.x + sp.w / 2
        uptime = self.status_data.get("uptime", "--")
        cr.set_source_rgba(rf, gf, bf, 1.0)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(14)
        ext = cr.text_extents(uptime)
        cr.move_to(cx - ext.width / 2, sp.y + 44)
        cr.show_text(uptime)
        cr.set_source_rgba(rf, gf, bf, 0.5)
        cr.set_font_size(8)
        lbl = "UPTIME"
        ext2 = cr.text_extents(lbl)
        cr.move_to(cx - ext2.width / 2, sp.y + 56)
        cr.show_text(lbl)

    # ── shapes ─────────────────────────────────────────────────────

    def _rounded_rect(self, cr, x, y, w, h, radius):
        cr.new_path()
        cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
        cr.arc(x + w - radius, y + radius, radius, 1.5 * math.pi, 0)
        cr.arc(x + w - radius, y + h - radius, radius, 0, 0.5 * math.pi)
        cr.arc(x + radius, y + h - radius, radius, 0.5 * math.pi, math.pi)
        cr.close_path()

    # ── lifecycle ──────────────────────────────────────────────────

    def show(self):
        self.win.show_all()


# ── Main ──────────────────────────────────────────────────────────────

def main():
    lock_path = "/tmp/gp-desktop-icons.lock"
    pid_path = "/tmp/gp-desktop-icons.pid"

    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Lock held — signal existing daemon to reload, then exit
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            os.kill(pid, signal.SIGUSR1)
            return
        except (ProcessLookupError, ValueError, FileNotFoundError):
            # Stale lock — clean up and try again
            try:
                os.unlink(lock_path)
                os.unlink(pid_path)
            except OSError:
                pass
            try:
                lock_fd = open(lock_path, "w")
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
        except PermissionError:
            return

    with open(pid_path, "w") as pf:
        pf.write(str(os.getpid()))

    def cleanup(*_):
        try:
            os.unlink(pid_path)
        except OSError:
            pass
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            os.unlink(lock_path)
        except OSError:
            pass
        Gtk.main_quit()

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    os.makedirs(DESKTOP_DIR, exist_ok=True)
    canvas = DesktopCanvas()
    canvas.show()
    Gtk.main()

    try:
        os.unlink(pid_path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
