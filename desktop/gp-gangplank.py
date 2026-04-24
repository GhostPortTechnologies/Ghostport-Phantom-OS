#!/usr/bin/env python3
"""GANGPLANK — USB Drive Manager for Phantom OS."""

import sys
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

import json
import subprocess
import os


class GangplankApp(GhostPortApp):
    def __init__(self):
        super().__init__("GANGPLANK", "gangplank", (700, 500))
        self._drives = []
        self._selected_drive = None
        self.build_ui()
        self.refresh_drives()
        self.poll_start(5, self._poll_drives)

    def build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Header
        self._header = self.make_header("GANGPLANK", "USB Drive Manager")
        vbox.pack_start(self._header, False, False, 0)

        # Drive count label in header area
        self._count_label = self.make_label("0 USB drives detected", "gp-dim")
        self._count_label.set_margin_start(16)
        self._count_label.set_margin_top(4)
        self._count_label.set_margin_bottom(4)
        vbox.pack_start(self._count_label, False, False, 0)

        # ListBox in scrolled window
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-selected", self._on_row_selected)
        scrolled = self.make_scrolled(self._listbox)
        scrolled.set_vexpand(True)
        vbox.pack_start(scrolled, True, True, 0)

        # Empty state label
        self._empty_label = self.make_label("No USB drives detected", "gp-dim")
        self._empty_label.set_halign(Gtk.Align.CENTER)
        self._empty_label.set_valign(Gtk.Align.CENTER)
        self._empty_label.set_margin_top(40)

        # Button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(12)
        btn_bar.set_margin_end(12)
        btn_bar.set_margin_top(8)
        btn_bar.set_margin_bottom(8)

        self._mount_btn = self.make_button("Mount", self._on_mount, "gp-btn-primary")
        self._eject_btn = self.make_button("Eject", self._on_eject, "gp-btn-danger")
        self._open_btn = self.make_button("Open in Files", self._on_open, "gp-btn")
        self._refresh_btn = self.make_button("Refresh", self._on_refresh, "gp-btn")

        btn_bar.pack_start(self._mount_btn, False, False, 0)
        btn_bar.pack_start(self._eject_btn, False, False, 0)
        btn_bar.pack_start(self._open_btn, False, False, 0)
        btn_bar.pack_end(self._refresh_btn, False, False, 0)

        self._mount_btn.set_sensitive(False)
        self._eject_btn.set_sensitive(False)
        self._open_btn.set_sensitive(False)

        vbox.pack_start(btn_bar, False, False, 0)

        # Status bar
        self._status_bar = self.make_status_bar("Ready")
        vbox.pack_start(self._status_bar, False, False, 0)

    def _get_usb_drives(self):
        """Get USB drives from lsblk."""
        stdout, stderr, rc = self.run_cmd([
            "lsblk", "-Jpo", "NAME,SIZE,MOUNTPOINT,LABEL,FSTYPE,TYPE,MODEL,TRAN"
        ])
        if rc != 0:
            return []
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return []

        drives = []
        for dev in data.get("blockdevices", []):
            if dev.get("tran") == "usb" and dev.get("type") == "disk":
                model = dev.get("model", "USB Drive") or "USB Drive"
                model = model.strip()
                disk_name = dev.get("name", "")
                children = dev.get("children", [])
                if children:
                    for part in children:
                        drives.append({
                            "disk": disk_name,
                            "partition": part.get("name", ""),
                            "model": model,
                            "size": part.get("size", "?"),
                            "fstype": part.get("fstype", "unknown") or "unknown",
                            "label": part.get("label", "") or "",
                            "mountpoint": part.get("mountpoint", "") or "",
                        })
                else:
                    # No partitions, use disk directly
                    drives.append({
                        "disk": disk_name,
                        "partition": disk_name,
                        "model": model,
                        "size": dev.get("size", "?"),
                        "fstype": dev.get("fstype", "unknown") or "unknown",
                        "label": dev.get("label", "") or "",
                        "mountpoint": dev.get("mountpoint", "") or "",
                    })
        return drives

    def _get_disk_usage(self, mountpoint):
        """Get usage percentage for a mounted drive."""
        if not mountpoint:
            return 0.0
        try:
            st = os.statvfs(mountpoint)
            total = st.f_blocks * st.f_frsize
            free = st.f_bfree * st.f_frsize
            if total == 0:
                return 0.0
            return ((total - free) / total) * 100.0
        except Exception:
            return 0.0

    def refresh_drives(self):
        """Refresh the drive list UI."""
        self._drives = self._get_usb_drives()

        # Clear listbox
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        if not self._drives:
            self._count_label.set_text("No USB drives detected")
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.add(self._make_empty_card())
            self._listbox.add(row)
            self._mount_btn.set_sensitive(False)
            self._eject_btn.set_sensitive(False)
            self._open_btn.set_sensitive(False)
        else:
            n = len(self._drives)
            self._count_label.set_text(f"{n} USB drive{'s' if n != 1 else ''} detected")
            for i, drv in enumerate(self._drives):
                row = Gtk.ListBoxRow()
                row._drive_index = i
                row.add(self._make_drive_card(drv))
                self._listbox.add(row)

        self._listbox.show_all()
        self._selected_drive = None

    def _make_empty_card(self):
        """Create a placeholder card when no drives found."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("gp-card")
        card.set_margin_top(20)
        lbl = self.make_label("No USB drives connected", "gp-dim")
        lbl.set_halign(Gtk.Align.CENTER)
        card.pack_start(lbl, False, False, 8)
        hint = self.make_label("Insert a USB drive to manage it", "gp-dim")
        hint.set_halign(Gtk.Align.CENTER)
        card.pack_start(hint, False, False, 4)
        return card

    def _make_drive_card(self, drv):
        """Create a card widget for a USB drive."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("gp-card")

        # Top row: model + size
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        model_lbl = self.make_label(drv["model"], "gp-accent")
        top.pack_start(model_lbl, False, False, 0)

        size_lbl = self.make_label(drv["size"], "gp-text")
        top.pack_start(size_lbl, False, False, 0)

        # Mount status badge
        if drv["mountpoint"]:
            status_lbl = self.make_label("MOUNTED", "gp-success")
        else:
            status_lbl = self.make_label("NOT MOUNTED", "gp-dim")
        top.pack_end(status_lbl, False, False, 0)

        card.pack_start(top, False, False, 0)

        # Details row: partition, filesystem, label
        details = []
        details.append(drv["partition"])
        details.append(drv["fstype"])
        if drv["label"]:
            details.append(f'"{drv["label"]}"')
        if drv["mountpoint"]:
            details.append(f"at {drv['mountpoint']}")
        det_lbl = self.make_label(" | ".join(details), "gp-dim")
        card.pack_start(det_lbl, False, False, 0)

        # Capacity bar (if mounted)
        if drv["mountpoint"]:
            usage = self._get_disk_usage(drv["mountpoint"])
            bar = Gtk.ProgressBar()
            bar.set_fraction(usage / 100.0)
            bar.set_show_text(True)
            bar.set_text(f"{usage:.1f}% used")
            bar.set_margin_top(4)
            # Color the bar via CSS
            if usage >= 85:
                css_class = "gp-danger"
            elif usage >= 60:
                css_class = "gp-warning"
            else:
                css_class = "gp-success"
            c = self.colors
            bar_css = f"""
                progressbar trough {{
                    background-color: {c['bg']};
                    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.2);
                    border-radius: 4px;
                    min-height: 14px;
                }}
                progressbar progress {{
                    background-color: {c['accent']};
                    border-radius: 4px;
                    min-height: 14px;
                }}
                progressbar text {{
                    color: {c['text']};
                    font-family: monospace;
                    font-size: 9px;
                }}
            """
            provider = Gtk.CssProvider()
            provider.load_from_data(bar_css.encode())
            bar.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            card.pack_start(bar, False, False, 0)

        return card

    def _on_row_selected(self, listbox, row):
        """Handle drive selection."""
        if row is None or not hasattr(row, '_drive_index'):
            self._selected_drive = None
            self._mount_btn.set_sensitive(False)
            self._eject_btn.set_sensitive(False)
            self._open_btn.set_sensitive(False)
            return

        idx = row._drive_index
        if idx < len(self._drives):
            self._selected_drive = self._drives[idx]
            mounted = bool(self._selected_drive.get("mountpoint"))
            self._mount_btn.set_sensitive(not mounted)
            self._eject_btn.set_sensitive(True)
            self._open_btn.set_sensitive(mounted)

    def _on_mount(self, btn):
        """Mount the selected drive."""
        if not self._selected_drive:
            return
        part = self._selected_drive["partition"]
        self.set_status(f"Mounting {part}...")

        def do_mount():
            return self.run_cmd(["udisksctl", "mount", "-b", part])

        def on_done(result):
            stdout, stderr, rc = result
            if rc == 0:
                self.set_status(f"Mounted {part}")
            else:
                self.set_status(f"Mount failed: {stderr}")
            self.refresh_drives()

        self.run_async(do_mount, on_done)

    def _on_eject(self, btn):
        """Eject the selected drive."""
        if not self._selected_drive:
            return
        part = self._selected_drive["partition"]
        disk = self._selected_drive["disk"]
        model = self._selected_drive["model"]
        self.set_status(f"Ejecting {part}...")

        def do_eject():
            # Sync first
            subprocess.run(["sync"], timeout=10)
            # Unmount if mounted
            if self._selected_drive.get("mountpoint"):
                stdout, stderr, rc = self.run_cmd(["udisksctl", "unmount", "-b", part])
                if rc != 0:
                    return ("", stderr, rc)
            # Power off the disk
            stdout, stderr, rc = self.run_cmd(["udisksctl", "power-off", "-b", disk])
            return (stdout, stderr, rc)

        def on_done(result):
            stdout, stderr, rc = result
            if rc == 0:
                self.set_status(f"Ejected {model}")
                try:
                    subprocess.Popen(["notify-send", "-a", "Gangplank",
                                      "USB Drive Ejected", f"{model} safely removed"])
                except Exception:
                    pass
            else:
                self.set_status(f"Eject failed: {stderr}")
            self.refresh_drives()

        self.run_async(do_eject, on_done)

    def _on_open(self, btn):
        """Open the mounted drive in Thunar."""
        if not self._selected_drive or not self._selected_drive.get("mountpoint"):
            return
        mp = self._selected_drive["mountpoint"]
        try:
            subprocess.Popen(["thunar", mp])
            self.set_status(f"Opened {mp}")
        except Exception as e:
            self.set_status(f"Failed to open: {e}")

    def _on_refresh(self, btn):
        """Manual refresh."""
        self.refresh_drives()
        self.set_status("Refreshed")

    def _poll_drives(self):
        """Poll for drive changes."""
        new_drives = self._get_usb_drives()
        # Compare by partition names
        old_parts = {d["partition"] for d in self._drives}
        new_parts = {d["partition"] for d in new_drives}
        if old_parts != new_parts:
            self.refresh_drives()
        return True


if __name__ == "__main__":
    app = GangplankApp()
    app.run()
