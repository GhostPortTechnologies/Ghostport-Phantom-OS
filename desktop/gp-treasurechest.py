#!/usr/bin/env python3
"""GhostPort Treasure Chest — storage for hidden desktop icons and widgets."""

import json
import os
import signal
import sys

sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp  # noqa: E402

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

POSITIONS_FILE = os.path.expanduser("~/.config/phantom/icon-positions.json")
WIDGET_LAYOUT_FILE = os.path.expanduser("~/.config/phantom/widget-layout.json")
ICONS_PID_FILE = "/tmp/gp-desktop-icons.pid"
WIDGETS_PID_FILE = "/tmp/gp-widgets.pid"


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)


def signal_process(pid_file, sig):
    try:
        with open(pid_file) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, sig)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


class TreasureChest(GhostPortApp):
    def __init__(self):
        super().__init__("TREASURE CHEST", "treasurechest", (640, 560))
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        header = self.make_header("TREASURE CHEST", "Stowed icons and widgets")
        root.pack_start(header, False, False, 0)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(4)

        refresh_btn = self.make_button("Refresh", lambda b: self._refresh(), "gp-btn")
        toolbar.pack_start(refresh_btn, False, False, 0)

        restore_all_btn = self.make_button(
            "Restore All", lambda b: self._restore_all(), "gp-btn-primary"
        )
        toolbar.pack_end(restore_all_btn, False, False, 0)

        root.pack_start(toolbar, False, False, 0)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = self.make_scrolled(self.list_box)
        scroll.set_margin_start(12)
        scroll.set_margin_end(12)
        scroll.set_margin_top(4)
        scroll.set_margin_bottom(4)
        root.pack_start(scroll, True, True, 0)

        self.status_bar = self.make_status_bar("Loading…")
        root.pack_start(self.status_bar, False, False, 0)

    def _refresh(self):
        for child in self.list_box.get_children():
            self.list_box.remove(child)

        hidden_icons = self._hidden_desktop_icons()
        hidden_widgets = self._hidden_widgets()

        if not hidden_icons and not hidden_widgets:
            empty = self.make_label(
                "Chest is empty. Drag an icon to the bottom of the screen to stow it.",
                "gp-dim",
            )
            empty.set_margin_top(24)
            empty.set_margin_start(16)
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.add(empty)
            self.list_box.add(row)
            self.list_box.show_all()
            self.set_status("Chest empty")
            return

        if hidden_icons:
            self._add_section("DESKTOP ICONS")
            for name in sorted(hidden_icons):
                self._add_row(name, "icon", lambda _b, n=name: self._restore_icon(n))

        if hidden_widgets:
            self._add_section("WIDGETS")
            for wid in sorted(hidden_widgets):
                self._add_row(wid, "widget", lambda _b, w=wid: self._restore_widget(w))

        self.list_box.show_all()
        total = len(hidden_icons) + len(hidden_widgets)
        self.set_status(f"{total} item(s) stowed")

    def _add_section(self, label):
        lbl = self.make_label(label, "gp-accent")
        lbl.set_margin_top(8)
        lbl.set_margin_start(8)
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.add(lbl)
        self.list_box.add(row)

    def _add_row(self, name, kind, on_restore):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_start(12)
        hbox.set_margin_end(8)
        hbox.set_margin_top(6)
        hbox.set_margin_bottom(6)

        name_lbl = self.make_label(name, "gp-text")
        name_lbl.set_hexpand(True)
        hbox.pack_start(name_lbl, True, True, 0)

        kind_lbl = self.make_label(kind, "gp-dim")
        hbox.pack_start(kind_lbl, False, False, 0)

        restore_btn = self.make_button("Restore", on_restore, "gp-btn")
        hbox.pack_end(restore_btn, False, False, 0)

        row.add(hbox)
        self.list_box.add(row)

    def _hidden_desktop_icons(self):
        positions = load_json(POSITIONS_FILE, {})
        return [name for name, entry in positions.items() if isinstance(entry, dict) and entry.get("hidden")]

    def _hidden_widgets(self):
        layout = load_json(WIDGET_LAYOUT_FILE, {"widgets": {}})
        widgets = layout.get("widgets", {})
        return [wid for wid, cfg in widgets.items() if not cfg.get("enabled", False)]

    def _restore_icon(self, name):
        positions = load_json(POSITIONS_FILE, {})
        if name in positions and isinstance(positions[name], dict):
            positions[name].pop("hidden", None)
            save_json(POSITIONS_FILE, positions)
            signal_process(ICONS_PID_FILE, signal.SIGUSR1)
            self.set_status(f"Restored {name}")
            GLib.timeout_add(500, lambda: (self._refresh(), False)[1])

    def _restore_widget(self, wid):
        layout = load_json(WIDGET_LAYOUT_FILE, {"widgets": {}})
        widgets = layout.setdefault("widgets", {})
        widgets.setdefault(wid, {})["enabled"] = True
        save_json(WIDGET_LAYOUT_FILE, layout)
        signal_process(WIDGETS_PID_FILE, signal.SIGUSR2)
        self.set_status(f"Restored widget {wid}")
        GLib.timeout_add(500, lambda: (self._refresh(), False)[1])

    def _restore_all(self):
        positions = load_json(POSITIONS_FILE, {})
        changed = False
        for name, entry in positions.items():
            if isinstance(entry, dict) and entry.get("hidden"):
                entry.pop("hidden", None)
                changed = True
        if changed:
            save_json(POSITIONS_FILE, positions)
            signal_process(ICONS_PID_FILE, signal.SIGUSR1)

        layout = load_json(WIDGET_LAYOUT_FILE, {"widgets": {}})
        widgets_changed = False
        for wid, cfg in layout.get("widgets", {}).items():
            if not cfg.get("enabled", False):
                cfg["enabled"] = True
                widgets_changed = True
        if widgets_changed:
            save_json(WIDGET_LAYOUT_FILE, layout)
            signal_process(WIDGETS_PID_FILE, signal.SIGUSR2)

        self.set_status("Restored all stowed items")
        self._refresh()


if __name__ == "__main__":
    app = TreasureChest()
    app.run()
