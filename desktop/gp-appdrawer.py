#!/usr/bin/env python3
"""GhostPort App Drawer — flat grid of all apps with search + hide/show toggle.

Surfaces:
  - GhostPort tools (DESKTOP_APPS) including ones hidden from the desktop
  - .desktop files from /usr/share/applications and ~/.local/share/applications
  - ~/Desktop/ files

Click an app to launch. Right-click a GhostPort tool to toggle its
visibility on the desktop (mirrors the icon-positions.json hidden flag
read by gp-desktop-icons.py's _rebuild_app_sprites filter).
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf  # noqa: E402

import json
import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gp_app_base import GhostPortApp  # noqa: E402

ICON_DIR = "/opt/ghostport/desktop/icons"
POSITIONS_FILE = os.path.expanduser("~/.config/ghostport/icon-positions.json")
DESKTOP_PID_FILE = "/tmp/gp-desktop-icons.pid"


# Fallback list — used ONLY if importing gp-desktop-icons.py fails.
# Drift here is acceptable; the alternative is App Drawer crashing and
# leaving the customer with no recovery path. Keep this list reasonably
# fresh; the importlib path is preferred.
_FALLBACK_APPS = [
    (
        "Crow's Nest",
        "gp-crowsnest.svg",
        "python3 /opt/phantom/desktop/gp-crowsnest.py",
        "Intrusion Detection",
    ),
    (
        "Bulkhead",
        "gp-bulkhead.svg",
        "python3 /opt/phantom/desktop/gp-bulkhead.py",
        "Firewall",
    ),
    (
        "Dragnet",
        "gp-dragnet.svg",
        "python3 /opt/phantom/desktop/gp-dragnet.py",
        "Packet Capture",
    ),
    (
        "Anchor",
        "gp-anchor.svg",
        "python3 /opt/phantom/desktop/gp-anchor.py",
        "Kill Switch",
    ),
    (
        "Aether Box",
        "gp-strongbox.svg",
        "python3 /opt/phantom/desktop/gp-aetherbox.py",
        "Encrypted Vault",
    ),
    (
        "Tide Chart",
        "gp-heatmap.svg",
        "python3 /opt/phantom/desktop/gp-tidechart.py",
        "Bandwidth Heatmap",
    ),
    (
        "Sonar",
        "gp-watchdog.svg",
        "python3 /opt/phantom/desktop/gp-sonar.py",
        "Rogue AP Scanner",
    ),
    (
        "Crew Manifest",
        "gp-roster.svg",
        "python3 /opt/phantom/desktop/gp-crewmanifest.py",
        "Connected Clients",
    ),
    (
        "Atlas",
        "gp-atlas.svg",
        "python3 /opt/phantom/desktop/gp-atlas.py",
        "Network Topology",
    ),
    (
        "Stonefish",
        "gp-tripwire.svg",
        "python3 /opt/phantom/desktop/gp-stonefish.py",
        "ARP Guard",
    ),
    (
        "Seadevil",
        "gp-seadevil.svg",
        "python3 /opt/phantom/desktop/gp-seadevil.py",
        "MAC Randomizer",
    ),
    (
        "Gangplank",
        "gp-dock.svg",
        "python3 /opt/phantom/desktop/gp-gangplank.py",
        "USB Manager",
    ),
    (
        "Sea Urchin",
        "gp-vitals.svg",
        "python3 /opt/phantom/desktop/gp-seaurchin.py",
        "System Health",
    ),
    (
        "Logbook",
        "gp-logbook.svg",
        "python3 /opt/phantom/desktop/gp-logbook.py",
        "Event Log",
    ),
    (
        "Quartermaster",
        "gp-audit.svg",
        "python3 /opt/phantom/desktop/gp-quartermaster.py",
        "Security Scan",
    ),
    (
        "Dashboard",
        "gp-dashboard.png",
        "brave-browser --app=https://localhost:4201",
        "Web UI",
    ),
    ("Brave", "gp-brave.png", "brave-browser", "Web Browser"),
    (
        "Accessibility",
        "gp-widget-library.svg",
        "python3 /opt/phantom/desktop/gp-widget-library.py",
        "Display & Theme",
    ),
    (
        "Chamber",
        "gp-chamber.svg",
        "brave-browser --user-data-dir=/home/ghostport-admin/.local/share/chamber-brave-profile --app=http://127.0.0.1:4242",
        "AI Coordination",
    ),
]


def _load_ghostport_apps():
    """Pull DESKTOP_APPS from gp-desktop-icons.py via importlib so the
    drawer never drifts from what the desktop renders. If that fails for
    any reason (syntax error, moved file, etc.) fall back to the static
    list above — App Drawer must always open, even partially."""
    import importlib.util

    try:
        path = "/opt/ghostport/desktop/gp-desktop-icons.py"
        spec = importlib.util.spec_from_file_location("_gpdi", path)
        mod = importlib.util.module_from_spec(spec)
        # The module guards main() under __name__ == "__main__"; loading
        # just populates module-level constants like DESKTOP_APPS.
        spec.loader.exec_module(mod)
        return list(mod.DESKTOP_APPS)
    except Exception as exc:  # noqa: BLE001 — we genuinely want the catch-all here
        sys.stderr.write(
            f"gp-appdrawer: failed to import DESKTOP_APPS from "
            f"gp-desktop-icons.py ({exc!r}); using fallback list.\n"
        )
        try:
            subprocess.Popen(
                [
                    "notify-send",
                    "-t",
                    "6000",
                    "App Drawer (degraded)",
                    "GhostPort tools list couldn't load from source. "
                    "Showing fallback list. System apps still discoverable.",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError):
            pass
        return list(_FALLBACK_APPS)


GHOSTPORT_APPS = _load_ghostport_apps()

ICON_PIXEL_SIZE = 56
TILE_WIDTH = 140
TILE_HEIGHT = 110

HELP_SECTIONS = [
    (
        "What it does",
        "App Drawer surfaces every app on this system in one searchable grid:\n"
        "  • GhostPort tools (visible and hidden)\n"
        "  • System .desktop apps from /usr/share/applications and ~/.local\n"
        "  • Files placed in ~/Desktop",
    ),
    (
        "Controls",
        "Click any tile to launch.\n"
        "Right-click a GhostPort tile → toggle Hide from / Show on Desktop.\n"
        "Type in the search box to filter live.",
    ),
    (
        "Hide / Show",
        "Hidden GhostPort tools have their hidden:true flag set in\n"
        "~/.config/ghostport/icon-positions.json. gp-desktop-icons reads that\n"
        "flag and skips them when rendering the desktop grid. Toggle restores.",
    ),
]


def load_positions():
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_positions(positions):
    os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


def is_hidden(positions, name):
    entry = positions.get(name)
    return isinstance(entry, dict) and bool(entry.get("hidden"))


def signal_desktop_reload():
    try:
        with open(DESKTOP_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 10)  # SIGUSR1
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


def parse_desktop_file(path):
    """Minimal .desktop parser. Returns (name, exec, icon, comment) or None."""
    try:
        with open(path, encoding="utf-8") as f:
            data = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    in_section = False
    name = exec_cmd = icon = comment = None
    no_display = False
    hidden = False
    for line in data.splitlines():
        line = line.strip()
        if line.startswith("["):
            if in_section:
                break
            in_section = line == "[Desktop Entry]"
            continue
        if not in_section or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key == "Name" and name is None:
            name = val
        elif key == "Exec" and exec_cmd is None:
            # strip %f, %F, %u, %U, %i, %c, %k field codes
            tokens = [
                t
                for t in shlex.split(val, posix=True)
                if not (t.startswith("%") and len(t) == 2)
            ]
            exec_cmd = tokens
        elif key == "Icon" and icon is None:
            icon = val
        elif key == "Comment" and comment is None:
            comment = val
        elif key == "NoDisplay" and val.lower() == "true":
            no_display = True
        elif key == "Hidden" and val.lower() == "true":
            hidden = True
    if no_display or hidden or not name or not exec_cmd:
        return None
    return name, exec_cmd, icon, comment or ""


def discover_system_apps():
    """Walk standard XDG dirs and collect launchable .desktop entries."""
    seen_paths = set()
    apps = []
    for d in (
        os.path.expanduser("~/.local/share/applications"),
        "/usr/share/applications",
        "/usr/local/share/applications",
    ):
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".desktop"):
                continue
            path = os.path.join(d, fname)
            if fname in seen_paths:
                continue  # later dir wins by being processed second
            seen_paths.add(fname)
            parsed = parse_desktop_file(path)
            if parsed:
                apps.append(parsed)
    return apps


def discover_desktop_files():
    """Files dropped in ~/Desktop — every regular file becomes a launchable tile."""
    home_desktop = os.path.expanduser("~/Desktop")
    if not os.path.isdir(home_desktop):
        return []
    out = []
    for fname in sorted(os.listdir(home_desktop)):
        if fname.startswith("."):
            continue
        path = os.path.join(home_desktop, fname)
        if not os.path.isfile(path):
            continue
        if fname.endswith(".desktop"):
            parsed = parse_desktop_file(path)
            if parsed:
                out.append((parsed[0], parsed[1], parsed[2], parsed[3], fname))
                continue
        # Regular file (text, image, etc.) — open with xdg-open
        out.append((fname, ["xdg-open", path], None, "Desktop file", fname))
    return out


def lookup_icon_pixbuf(icon_name, size=ICON_PIXEL_SIZE):
    """Resolve an icon name to a Pixbuf via theme + absolute-path fallback."""
    if not icon_name:
        return None
    if os.path.isabs(icon_name) and os.path.isfile(icon_name):
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_name, size, size, True)
        except Exception:
            return None
    theme = Gtk.IconTheme.get_default()
    try:
        info = theme.lookup_icon(icon_name, size, 0)
        if info:
            return info.load_icon()
    except Exception:
        pass
    return None


def lookup_ghostport_icon(icon_file):
    path = os.path.join(ICON_DIR, icon_file)
    if not os.path.isfile(path):
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(
            path, ICON_PIXEL_SIZE, ICON_PIXEL_SIZE, True
        )
    except Exception:
        return None


class AppDrawer(GhostPortApp):
    def __init__(self):
        super().__init__("App Drawer", "appdrawer", (920, 640))
        self.positions = load_positions()
        self.tiles = []  # list of (Gtk.FlowBoxChild, name, source)
        self.build_ui()

    def build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header = self.make_header("APP DRAWER", "All apps — search, launch, hide")
        header_box.pack_start(header, True, True, 0)
        help_btn = self.make_help_button(sections=HELP_SECTIONS)
        help_btn.set_margin_end(8)
        help_btn.set_valign(Gtk.Align.CENTER)
        header_box.pack_end(help_btn, False, False, 0)
        outer.pack_start(header_box, False, False, 0)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_row.set_margin_start(12)
        search_row.set_margin_end(12)
        search_row.set_margin_top(8)
        search_row.set_margin_bottom(4)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Type to filter…")
        self.search_entry.connect(
            "search-changed", lambda _e: self.flowbox.invalidate_filter()
        )
        search_row.pack_start(self.search_entry, True, True, 0)
        outer.pack_start(search_row, False, False, 0)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_max_children_per_line(8)
        self.flowbox.set_min_children_per_line(3)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_row_spacing(8)
        self.flowbox.set_column_spacing(8)
        self.flowbox.set_margin_start(12)
        self.flowbox.set_margin_end(12)
        self.flowbox.set_margin_top(4)
        self.flowbox.set_margin_bottom(8)
        self.flowbox.set_filter_func(self._filter_tile)

        scrolled = self.make_scrolled(self.flowbox)
        outer.pack_start(scrolled, True, True, 0)

        outer.pack_start(self.make_status_bar(""), False, False, 0)

        self._populate()

    def _populate(self):
        # 1. GhostPort tools — hidden ones included, marked
        for label, icon_file, command, subtitle in GHOSTPORT_APPS:
            pix = lookup_ghostport_icon(icon_file)
            hidden = is_hidden(self.positions, label)
            child = self._make_tile(
                label,
                subtitle,
                pix,
                command,
                source="ghostport",
                hidden=hidden,
                pos_key=label,
            )
            self.flowbox.add(child)

        # 2. ~/Desktop files (visible + hidden — both are toggleable here)
        for name, exec_cmd, icon, comment, fname in discover_desktop_files():
            pix = lookup_icon_pixbuf(icon) if icon else None
            file_hidden = is_hidden(self.positions, f"file:{fname}")
            child = self._make_tile(
                name,
                comment or "Desktop",
                pix,
                exec_cmd,
                source="desktop",
                hidden=file_hidden,
                pos_key=f"file:{fname}",
            )
            self.flowbox.add(child)

        # 3. System .desktop apps
        for name, exec_cmd, icon, comment in discover_system_apps():
            pix = lookup_icon_pixbuf(icon) if icon else None
            child = self._make_tile(
                name,
                comment or "System",
                pix,
                exec_cmd,
                source="system",
                hidden=False,
                pos_key=None,
            )
            self.flowbox.add(child)

        self.flowbox.show_all()
        self.set_status(f"{len(self.tiles)} apps")

    def _make_tile(
        self, label, subtitle, pixbuf, command, *, source, hidden, pos_key=None
    ):
        child = Gtk.FlowBoxChild()
        child.set_size_request(TILE_WIDTH, TILE_HEIGHT)

        evt = Gtk.EventBox()
        evt.set_above_child(False)
        child.add(evt)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(6)
        box.set_margin_end(6)
        evt.add(box)

        if pixbuf is not None:
            img = Gtk.Image.new_from_pixbuf(pixbuf)
        else:
            img = Gtk.Image.new_from_icon_name(
                "application-x-executable", Gtk.IconSize.DIALOG
            )
        img.set_pixel_size(ICON_PIXEL_SIZE)
        if hidden:
            img.set_opacity(0.35)
        box.pack_start(img, False, False, 0)

        name_label = Gtk.Label(label=label)
        name_label.get_style_context().add_class("gp-text" if not hidden else "gp-dim")
        name_label.set_max_width_chars(16)
        name_label.set_line_wrap(False)
        name_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        box.pack_start(name_label, False, False, 0)

        if hidden:
            badge = Gtk.Label(label="hidden")
            badge.get_style_context().add_class("gp-dim")
            box.pack_start(badge, False, False, 0)
        elif subtitle:
            sub = Gtk.Label(label=subtitle)
            sub.get_style_context().add_class("gp-dim")
            sub.set_max_width_chars(18)
            sub.set_line_wrap(False)
            sub.set_ellipsize(3)
            box.pack_start(sub, False, False, 0)

        evt.connect(
            "button-press-event", self._on_tile_click, label, command, source, pos_key
        )

        # Stash for filter + lookup
        child._gp_label = label.lower()
        child._gp_subtitle = (subtitle or "").lower()
        child._gp_source = source
        child._gp_hidden = hidden
        self.tiles.append((child, label, source))
        return child

    def _on_tile_click(self, _w, event, label, command, source, pos_key):
        if event.button == 1:
            self._launch(command)
            self.close()
            return True
        if event.button == 3:
            if pos_key:
                self._show_tile_menu(event, pos_key)
            return True
        return False

    def _show_tile_menu(self, event, pos_key):
        menu = Gtk.Menu()
        hidden = is_hidden(self.positions, pos_key)
        action_label = "Show on Desktop" if hidden else "Hide from Desktop"
        mi = Gtk.MenuItem(label=action_label)
        mi.connect("activate", lambda _m: self._toggle_hide(pos_key))
        menu.append(mi)
        menu.show_all()
        menu.popup_at_pointer(event)
        self._menu_ref = menu  # keep alive

    def _toggle_hide(self, pos_key):
        self.positions = load_positions()
        entry = self.positions.get(pos_key)
        if not isinstance(entry, dict):
            entry = {"x": 0, "y": 0}
        if entry.get("hidden"):
            entry.pop("hidden", None)
        else:
            entry["hidden"] = True
        self.positions[pos_key] = entry
        save_positions(self.positions)
        signal_desktop_reload()
        # Refresh the visible drawer so the tile reflects new state
        for child in self.flowbox.get_children():
            self.flowbox.remove(child)
        self.tiles = []
        self._populate()

    def _filter_tile(self, child):
        q = self.search_entry.get_text().strip().lower()
        if not q:
            return True
        return q in child._gp_label or q in child._gp_subtitle

    def _launch(self, command):
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        try:
            subprocess.Popen(
                argv,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self.set_status(f"launch error: {exc}")


def main():
    app = AppDrawer()
    app.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
