#!/usr/bin/env python3
"""
GhostPort Aether Box — Encrypted file vault (gocryptfs).
Replaces TUI tool gp-vault.
"""

import sys
import os
import time
import shutil
import subprocess

sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

VAULT_BASE = os.path.expanduser("~/.local/share/ghostport-vault")
CIPHER_DIR = os.path.join(VAULT_BASE, "cipher")
MOUNT_DIR = os.path.join(VAULT_BASE, "mnt")
INTRO_MARKER = os.path.join(VAULT_BASE, ".intro_seen")

AUTO_LOCK_MINUTES = 15

INTRO_TEXT = """AETHER BOX — Encrypted File Vault

WHAT IT DOES
Stores sensitive files encrypted on disk. Without your password,
they look like scrambled blobs — useless to anyone who steals
the device or copies the SD card.

FIRST TIME
1. Click SET PASSWORD
2. Enter a strong password twice
3. Vault is created and unlocked

STORING A FILE (passport, recovery codes, tax docs, seed phrase…)
1. UNLOCK with your password
2. Click + Add File → pick the file
3. CHECK "Securely shred original after encrypting"
4. Click Add — original is overwritten with `shred -u -n 3`, encrypted
   copy goes in vault. Note: on SD/flash storage, wear-leveling can
   preserve fragments at the hardware layer; for the highest assurance,
   create the original directly inside the unlocked vault.

RETRIEVING A FILE
1. UNLOCK
2. Select the file → click Export → save it where you need it
3. Use it, then delete the export and lock the vault

WHAT'S PROTECTED
✓ Pi stolen and powered off — files unreadable
✓ SD card snooped directly — only encrypted blobs visible
✗ While UNLOCKED, files live in ~/.local/share/ghostport-vault/mnt/
✗ Lose your password = lose the files. NO RECOVERY.
  Write the password down somewhere safe (not on the Pi).

AUTO-LOCK
Vault auto-locks after 15 minutes of no activity.

Click the ? button in the toolbar to see this again."""


class AetherBox(GhostPortApp):
    def __init__(self):
        super().__init__("AETHER BOX", "aetherbox", (800, 600))
        self.locked = True
        self.vault_initialized = False
        self.auto_lock_remaining = 0
        self._auto_lock_timer = None
        self._build_ui()
        self._check_state()
        GLib.idle_add(self._maybe_show_intro)

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header
        header = self.make_header("AETHER BOX", "Encrypted Vault")
        root.pack_start(header, False, False, 0)

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(4)

        self.toggle_btn = Gtk.Button(label="UNLOCK")
        self.toggle_btn.get_style_context().add_class("gp-toggle-off")
        self.toggle_btn.connect("clicked", self._on_toggle)
        toolbar.pack_start(self.toggle_btn, False, False, 0)

        self.add_btn = self.make_button("+ Add File", self._on_add_file, "gp-btn-primary")
        self.add_btn.set_sensitive(False)
        toolbar.pack_start(self.add_btn, False, False, 0)

        self.export_btn = self.make_button("Export", self._on_export_file, "gp-btn")
        self.export_btn.set_sensitive(False)
        toolbar.pack_start(self.export_btn, False, False, 0)

        self.delete_btn = self.make_button("Delete", self._on_delete_file, "gp-btn-danger")
        self.delete_btn.set_sensitive(False)
        toolbar.pack_start(self.delete_btn, False, False, 0)

        self.help_btn = self.make_button("?", lambda b: self._show_intro(), "gp-btn")
        toolbar.pack_start(self.help_btn, False, False, 0)

        # Auto-lock label
        self.lock_timer_label = self.make_label("", "gp-dim")
        self.lock_timer_label.set_halign(Gtk.Align.END)
        toolbar.pack_end(self.lock_timer_label, False, False, 0)

        root.pack_start(toolbar, False, False, 0)

        # Install prompt (hidden by default)
        self.install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.install_box.set_margin_start(12)
        self.install_box.set_margin_end(12)
        self.install_box.set_margin_top(40)
        self.install_box.set_halign(Gtk.Align.CENTER)
        self.install_box.set_valign(Gtk.Align.CENTER)

        warn_label = self.make_label("gocryptfs is not installed", "gp-warning")
        self.install_box.pack_start(warn_label, False, False, 0)
        hint_label = self.make_label("Install it with: sudo apt install gocryptfs", "gp-dim")
        self.install_box.pack_start(hint_label, False, False, 0)
        self.install_box.set_no_show_all(True)
        root.pack_start(self.install_box, True, True, 0)

        # File list
        # Model: name, size_str, date_str, full_path
        self.store = Gtk.ListStore(str, str, str, str)
        self.treeview = Gtk.TreeView(model=self.store)
        self.treeview.set_headers_visible(True)

        for i, (title, width) in enumerate([("Name", 350), ("Size", 120), ("Modified", 180)]):
            renderer = Gtk.CellRendererText()
            renderer.set_property("font", "monospace 11")
            col = Gtk.TreeViewColumn(title, renderer, text=i)
            col.set_min_width(width)
            col.set_resizable(True)
            col.set_sort_column_id(i)
            self.treeview.append_column(col)

        self.treeview.get_selection().connect("changed", self._on_selection_changed)

        scrolled = self.make_scrolled(self.treeview)
        scrolled.set_margin_start(12)
        scrolled.set_margin_end(12)
        scrolled.set_margin_top(4)
        scrolled.set_margin_bottom(4)
        self.file_scroll = scrolled
        root.pack_start(scrolled, True, True, 0)

        # Status bar
        self.status_bar = self.make_status_bar("Checking vault status...")
        root.pack_start(self.status_bar, False, False, 0)

    # ── State ─────────────────────────────────────────────────────────

    def _has_gocryptfs(self):
        _, _, rc = self.run_cmd(["which", "gocryptfs"])
        return rc == 0

    def _is_mounted(self):
        _, _, rc = self.run_cmd(["mountpoint", "-q", MOUNT_DIR])
        return rc == 0

    def _is_initialized(self):
        return os.path.isfile(os.path.join(CIPHER_DIR, "gocryptfs.conf"))

    def _check_state(self):
        if not self._has_gocryptfs():
            self.install_box.set_no_show_all(False)
            self.install_box.show_all()
            self.file_scroll.hide()
            self.toggle_btn.set_sensitive(False)
            self.set_status("gocryptfs not installed")
            return

        self.install_box.hide()
        self.file_scroll.show()
        self.toggle_btn.set_sensitive(True)

        # Ensure directories exist
        os.makedirs(CIPHER_DIR, exist_ok=True)
        os.makedirs(MOUNT_DIR, exist_ok=True)

        self.vault_initialized = self._is_initialized()
        mounted = self._is_mounted()

        if mounted:
            self.locked = False
            self._set_unlocked_ui()
            self._refresh_files()
        else:
            self.locked = True
            self._set_locked_ui()

    def _set_locked_ui(self):
        is_first_run = not self.vault_initialized
        self.toggle_btn.set_label("SET PASSWORD" if is_first_run else "UNLOCK")
        ctx = self.toggle_btn.get_style_context()
        ctx.remove_class("gp-toggle-on")
        ctx.add_class("gp-toggle-off")
        self.add_btn.set_sensitive(False)
        self.export_btn.set_sensitive(False)
        self.delete_btn.set_sensitive(False)
        self.store.clear()
        self.lock_timer_label.set_text("")
        if self._auto_lock_timer:
            self.poll_stop(self._auto_lock_timer)
            self._auto_lock_timer = None
        if is_first_run:
            self.set_status("First run — click SET PASSWORD to create your encrypted vault")
        else:
            self.set_status("Vault locked — click UNLOCK to access files")

    def _set_unlocked_ui(self):
        self.toggle_btn.set_label("LOCK")
        ctx = self.toggle_btn.get_style_context()
        ctx.remove_class("gp-toggle-off")
        ctx.add_class("gp-toggle-on")
        self.add_btn.set_sensitive(True)
        self._start_auto_lock_timer()

    def _start_auto_lock_timer(self):
        self.auto_lock_remaining = AUTO_LOCK_MINUTES * 60
        if self._auto_lock_timer:
            self.poll_stop(self._auto_lock_timer)
        self._auto_lock_timer = self.poll_start(1, self._tick_auto_lock)

    def _tick_auto_lock(self):
        self.auto_lock_remaining -= 1
        mins = self.auto_lock_remaining // 60
        secs = self.auto_lock_remaining % 60
        self.lock_timer_label.set_text(f"Auto-lock in {mins}:{secs:02d}")
        if self.auto_lock_remaining <= 0:
            self._do_lock()
            return False
        return True

    # ── Actions ───────────────────────────────────────────────────────

    def _on_toggle(self, btn):
        if self.locked:
            self._prompt_password()
        else:
            self._do_lock()

    def _prompt_password(self):
        """Show password dialog for unlock or init."""
        is_init = not self.vault_initialized
        title = "Initialize Vault" if is_init else "Unlock Vault"

        dialog = Gtk.Dialog(title=title, parent=self, flags=Gtk.DialogFlags.MODAL)
        dialog.set_default_size(350, -1)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)

        if is_init:
            info = self.make_label("Create a password for your encrypted vault.", "gp-text")
            content.pack_start(info, False, False, 0)

        pw_entry = Gtk.Entry()
        pw_entry.set_visibility(False)
        pw_entry.set_placeholder_text("Password")
        pw_entry.set_activates_default(True)
        content.pack_start(pw_entry, False, False, 0)

        if is_init:
            pw_confirm = Gtk.Entry()
            pw_confirm.set_visibility(False)
            pw_confirm.set_placeholder_text("Confirm password")
            pw_confirm.set_activates_default(True)
            content.pack_start(pw_confirm, False, False, 0)

        dialog.show_all()
        response = dialog.run()
        password = pw_entry.get_text()
        confirm = pw_confirm.get_text() if is_init else password
        dialog.destroy()

        if response != Gtk.ResponseType.OK or not password:
            return

        if is_init:
            if password != confirm:
                self.set_status("Passwords do not match")
                return
            self._do_init(password)
        else:
            self._do_unlock(password)

    def _do_init(self, password):
        self.set_status("Initializing vault...")
        os.makedirs(CIPHER_DIR, exist_ok=True)
        os.makedirs(MOUNT_DIR, exist_ok=True)

        def _init():
            proc = subprocess.run(
                ["gocryptfs", "-init", CIPHER_DIR],
                input=password + "\n",
                capture_output=True, text=True, timeout=30
            )
            return proc.returncode, proc.stderr

        def _done(result):
            if isinstance(result, Exception):
                self.set_status(f"Init failed: {result}")
                return
            rc, err = result
            if rc != 0:
                self.set_status(f"Init failed: {err.strip()}")
                return
            self.vault_initialized = True
            self.set_status("Vault initialized. Mounting...")
            self._do_unlock(password)

        self.run_async(_init, _done)

    def _do_unlock(self, password):
        self.set_status("Unlocking vault...")

        def _unlock():
            proc = subprocess.run(
                ["gocryptfs", CIPHER_DIR, MOUNT_DIR],
                input=password + "\n",
                capture_output=True, text=True, timeout=30
            )
            return proc.returncode, proc.stderr

        def _done(result):
            if isinstance(result, Exception):
                self.set_status(f"Unlock failed: {result}")
                return
            rc, err = result
            if rc != 0:
                msg = "Wrong password" if "Password" in err or "password" in err else err.strip()
                self.set_status(f"Unlock failed: {msg}")
                return
            self.locked = False
            self._set_unlocked_ui()
            self._refresh_files()

        self.run_async(_unlock, _done)

    def _do_lock(self):
        self.set_status("Locking vault...")
        stdout, stderr, rc = self.run_cmd(["fusermount", "-u", MOUNT_DIR])
        if rc != 0:
            self.set_status(f"Lock failed: {stderr}")
            return
        self.locked = True
        self._set_locked_ui()

    def _refresh_files(self):
        self.store.clear()
        if not os.path.ismount(MOUNT_DIR):
            self.set_status("Vault not mounted")
            return

        total_size = 0
        count = 0
        try:
            for name in sorted(os.listdir(MOUNT_DIR)):
                path = os.path.join(MOUNT_DIR, name)
                try:
                    stat = os.stat(path)
                    size = stat.st_size
                    mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                    size_str = self._fmt_size(size)
                    total_size += size
                    count += 1
                    self.store.append([name, size_str, mtime, path])
                except OSError:
                    self.store.append([name, "?", "?", path])
                    count += 1
        except OSError as e:
            self.set_status(f"Error reading vault: {e}")
            return

        self.set_status(f"{count} file{'s' if count != 1 else ''} | {self._fmt_size(total_size)} total | Vault unlocked")

    def _fmt_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _on_add_file(self, btn):
        dialog = Gtk.FileChooserDialog(
            title="Add File to Vault",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add", Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)

        shred_check = Gtk.CheckButton(label="Securely shred original after encrypting")
        shred_check.set_active(False)
        shred_check.show()
        dialog.set_extra_widget(shred_check)

        response = dialog.run()
        files = dialog.get_filenames() if response == Gtk.ResponseType.OK else []
        shred_originals = shred_check.get_active()
        dialog.destroy()

        if not files:
            return

        added = 0
        shredded = 0
        for src in files:
            name = os.path.basename(src)
            dest = os.path.join(MOUNT_DIR, name)
            if os.path.exists(dest):
                base, ext = os.path.splitext(name)
                i = 1
                while os.path.exists(dest):
                    dest = os.path.join(MOUNT_DIR, f"{base}_{i}{ext}")
                    i += 1
            try:
                shutil.copy2(src, dest)
                added += 1
                if shred_originals and os.path.getsize(dest) == os.path.getsize(src):
                    _, _, rc = self.run_cmd(["shred", "-u", "-n", "3", src], timeout=60)
                    if rc == 0:
                        shredded += 1
                    else:
                        self.set_status(f"Encrypted {name}, but shred failed")
            except Exception as e:
                self.set_status(f"Failed to add {name}: {e}")

        if added:
            self._refresh_files()
            self._start_auto_lock_timer()
            if shredded:
                self.set_status(f"Added {added} file(s), shredded {shredded} original(s)")

    def _on_delete_file(self, btn):
        sel = self.treeview.get_selection()
        model, tree_iter = sel.get_selected()
        if not tree_iter:
            return

        name = model[tree_iter][0]
        path = model[tree_iter][3]

        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete '{name}' from vault?"
        )
        dialog.format_secondary_text("This cannot be undone.")
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
                self._refresh_files()
            except Exception as e:
                self.set_status(f"Delete failed: {e}")

    def _maybe_show_intro(self):
        if not os.path.exists(INTRO_MARKER):
            self._show_intro()
            try:
                os.makedirs(VAULT_BASE, exist_ok=True)
                open(INTRO_MARKER, "w").close()
            except Exception:
                pass
        return False

    def _show_intro(self):
        dialog = Gtk.Dialog(title="How to use Aether Box", parent=self, flags=Gtk.DialogFlags.MODAL)
        dialog.set_default_size(560, 520)
        dialog.add_button("Got it", Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)

        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_cursor_visible(False)
        textview.set_wrap_mode(Gtk.WrapMode.WORD)
        textview.set_left_margin(8)
        textview.set_right_margin(8)
        textview.set_top_margin(8)
        textview.set_bottom_margin(8)
        textview.override_font(Pango.FontDescription("monospace 10"))
        textview.get_buffer().set_text(INTRO_TEXT)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(textview)
        content.pack_start(scrolled, True, True, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _on_export_file(self, btn):
        sel = self.treeview.get_selection()
        model, tree_iter = sel.get_selected()
        if not tree_iter:
            return

        name = model[tree_iter][0]
        src = model[tree_iter][3]

        dialog = Gtk.FileChooserDialog(
            title="Export File from Vault",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Export", Gtk.ResponseType.OK)
        dialog.set_current_name(name)
        dialog.set_do_overwrite_confirmation(True)

        response = dialog.run()
        dest = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()

        if not dest:
            return

        try:
            shutil.copy2(src, dest)
            self.set_status(f"Exported '{name}' to {dest}")
            self._start_auto_lock_timer()
        except Exception as e:
            self.set_status(f"Export failed: {e}")

    def _on_selection_changed(self, selection):
        model, tree_iter = selection.get_selected()
        has_sel = tree_iter is not None and not self.locked
        self.delete_btn.set_sensitive(has_sel)
        self.export_btn.set_sensitive(has_sel)


if __name__ == "__main__":
    app = AetherBox()
    app.run()
