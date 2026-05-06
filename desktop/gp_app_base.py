#!/usr/bin/env python3
"""
GhostPort App Base — Shared foundation for all GhostPort GTK3 desktop apps.

Usage:
    from gp_app_base import GhostPortApp

    class MyApp(GhostPortApp):
        def __init__(self):
            super().__init__("My App", "myapp", (900, 650))
            # Build your UI here
            self.build_ui()

        def build_ui(self):
            ...

    if __name__ == "__main__":
        app = MyApp()
        app.run()
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

import json
import os
import sys
import signal
import fcntl
import threading
import subprocess

# ── Theme ────────────────────────────────────────────────────────────

THEME_FILE = "/etc/phantom/theme.json"

def read_theme_color():
    """Read accent color hex from theme.json, returns 6-char hex string."""
    try:
        with open(THEME_FILE) as f:
            data = json.load(f)
            raw = (data.get("color") or "#39ff8f").lstrip("#").strip()
            if len(raw) == 6 and all(c in "0123456789abcdefABCDEF" for c in raw):
                return raw.lower()
    except Exception:
        pass
    return "39ff8f"

def hex_to_rgb(h):
    """Convert 6-char hex string to (r, g, b) tuple. Falls back to ghost-green on bad input."""
    try:
        if not h or len(h) != 6:
            raise ValueError("expected 6-char hex")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (TypeError, ValueError):
        return 0x39, 0xff, 0x8f

def derive_colors(hex_color):
    """Derive a full color palette from a single accent hex color."""
    r, g, b = hex_to_rgb(hex_color)
    accent = f"#{hex_color}"
    # Dim: 40% brightness
    dim = f"#{r*40//100:02x}{g*40//100:02x}{b*40//100:02x}"
    # Bright: accent + 30% toward white
    br_r = min(255, r + (255 - r) * 30 // 100)
    br_g = min(255, g + (255 - g) * 30 // 100)
    br_b = min(255, b + (255 - b) * 30 // 100)
    bright = f"#{br_r:02x}{br_g:02x}{br_b:02x}"
    # Backgrounds
    bg = f"#{r*4//100:02x}{g*4//100:02x}{b*4//100:02x}"
    bg2 = f"#{r*7//100:02x}{g*7//100:02x}{b*7//100:02x}"
    bg3 = f"#{r*12//100:02x}{g*12//100:02x}{b*12//100:02x}"
    # Text: readable light tone derived from accent
    text = f"#{min(255, r*22//100+200):02x}{min(255, g*22//100+200):02x}{min(255, b*22//100+200):02x}"
    # Accent with alpha variants (for CSS rgba)
    ar, ag, ab = r, g, b
    return {
        "accent": accent,
        "dim": dim,
        "bright": bright,
        "bg": bg,
        "bg2": bg2,
        "bg3": bg3,
        "text": text,
        "danger": "#ff5050",
        "warning": "#ffc832",
        "success": "#50ff78",
        "info": "#50b4ff",
        "r": r, "g": g, "b": b,
    }

# ── CSS Template ─────────────────────────────────────────────────────

def generate_css(colors):
    """Generate the base CSS for all GhostPort apps."""
    c = colors
    return f"""
/* ── GhostPort App Base CSS ── */

window {{
    background-color: {c['bg']};
    color: {c['text']};
}}

.gp-header {{
    background-color: {c['bg2']};
    border-bottom: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.2);
    padding: 8px 16px;
    min-height: 36px;
}}

.gp-header-title {{
    color: {c['accent']};
    font-family: monospace;
    font-size: 16px;
    font-weight: bold;
}}

.gp-header-subtitle {{
    color: {c['dim']};
    font-family: monospace;
    font-size: 11px;
}}

.gp-sidebar {{
    background-color: {c['bg2']};
    border-right: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.15);
    padding: 8px;
}}

.gp-content {{
    padding: 12px;
}}

.gp-status {{
    background-color: {c['bg2']};
    border-top: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.15);
    padding: 4px 12px;
    min-height: 24px;
}}

.gp-status-text {{
    color: {c['dim']};
    font-family: monospace;
    font-size: 10px;
}}

/* Cards */
.gp-card {{
    background-color: {c['bg2']};
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.2);
    border-radius: 8px;
    padding: 10px;
    margin: 4px 0;
}}

.gp-card-danger {{
    background-color: rgba(255, 80, 80, 0.08);
    border: 1px solid rgba(255, 80, 80, 0.4);
    border-radius: 8px;
    padding: 10px;
    margin: 4px 0;
}}

.gp-card-warning {{
    background-color: rgba(255, 200, 50, 0.08);
    border: 1px solid rgba(255, 200, 50, 0.3);
    border-radius: 8px;
    padding: 10px;
    margin: 4px 0;
}}

.gp-card-info {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.06);
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.25);
    border-radius: 8px;
    padding: 10px;
    margin: 4px 0;
}}

/* Labels */
.gp-text {{
    color: {c['text']};
    font-family: monospace;
    font-size: 12px;
}}

.gp-accent {{
    color: {c['accent']};
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}}

.gp-dim {{
    color: {c['dim']};
    font-family: monospace;
    font-size: 11px;
}}

.gp-bright {{
    color: {c['bright']};
    font-family: monospace;
    font-size: 12px;
}}

.gp-danger {{
    color: {c['danger']};
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}}

.gp-warning {{
    color: {c['warning']};
    font-family: monospace;
    font-size: 12px;
}}

.gp-success {{
    color: {c['success']};
    font-family: monospace;
    font-size: 12px;
}}

.gp-big-number {{
    color: {c['accent']};
    font-family: monospace;
    font-size: 48px;
    font-weight: bold;
}}

/* Buttons — `button.` prefix + image/shadow nullification beats the
   system Adwaita theme that paints a light gradient on every button. */
button.gp-btn {{
    background-image: none;
    background-color: {c['bg3']};
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.3);
    border-radius: 6px;
    color: {c['text']};
    font-family: monospace;
    font-size: 11px;
    padding: 6px 14px;
    min-height: 30px;
    box-shadow: none;
    text-shadow: none;
}}

button.gp-btn:hover {{
    background-image: none;
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.18);
    border-color: rgba({c['r']},{c['g']},{c['b']}, 0.7);
    color: {c['accent']};
}}

button.gp-btn:active {{
    background-image: none;
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.30);
}}

button.gp-btn-primary {{
    background-image: none;
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.18);
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.55);
    border-radius: 6px;
    color: {c['accent']};
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    padding: 6px 14px;
    min-height: 30px;
    box-shadow: none;
    text-shadow: none;
}}

button.gp-btn-primary:hover {{
    background-image: none;
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.30);
    border-color: {c['accent']};
}}

button.gp-btn-primary:active {{
    background-image: none;
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.42);
}}

button.gp-btn-danger {{
    background-image: none;
    background-color: rgba(255, 80, 80, 0.12);
    border: 1px solid rgba(255, 80, 80, 0.5);
    border-radius: 6px;
    color: {c['danger']};
    font-family: monospace;
    font-size: 11px;
    padding: 6px 14px;
    min-height: 30px;
    box-shadow: none;
    text-shadow: none;
}}

button.gp-btn-danger:hover {{
    background-image: none;
    background-color: rgba(255, 80, 80, 0.25);
    border-color: rgba(255, 80, 80, 0.8);
}}

/* Toggle button (arm/disarm style) */
button.gp-toggle-off {{
    background-image: none;
    background-color: {c['bg3']};
    border: 2px solid rgba({c['r']},{c['g']},{c['b']}, 0.3);
    border-radius: 8px;
    color: {c['dim']};
    font-family: monospace;
    font-size: 14px;
    font-weight: bold;
    padding: 12px 24px;
    box-shadow: none;
    text-shadow: none;
}}

button.gp-toggle-on {{
    background-image: none;
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.22);
    border: 2px solid {c['accent']};
    border-radius: 8px;
    color: {c['accent']};
    font-family: monospace;
    font-size: 14px;
    font-weight: bold;
    padding: 12px 24px;
    box-shadow: none;
    text-shadow: none;
}}

/* Catch-all: any button without a gp-* class still gets a dark theme
   (covers raw Gtk.Button() instances inside dialogs, popovers, etc.) */
button {{
    background-image: none;
    box-shadow: none;
    text-shadow: none;
}}

/* Notebook tabs */
notebook header {{
    background-color: {c['bg2']};
}}

notebook tab {{
    background-color: {c['bg']};
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.15);
    color: {c['dim']};
    font-family: monospace;
    font-size: 11px;
    padding: 4px 12px;
}}

notebook tab:checked {{
    background-color: {c['bg2']};
    border-bottom: 2px solid {c['accent']};
    color: {c['accent']};
    font-weight: bold;
}}

/* TreeView */
treeview {{
    background-color: {c['bg']};
    color: {c['text']};
    font-family: monospace;
    font-size: 11px;
}}

treeview header button {{
    background-color: {c['bg2']};
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.1);
    color: {c['accent']};
    font-family: monospace;
    font-size: 10px;
    font-weight: bold;
    padding: 4px 8px;
}}

treeview:selected {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.15);
    color: {c['bright']};
}}

/* Text entry */
entry {{
    background-color: {c['bg']};
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.3);
    border-radius: 6px;
    color: {c['accent']};
    font-family: monospace;
    font-size: 11px;
    padding: 6px 8px;
}}

entry:focus {{
    border-color: rgba({c['r']},{c['g']},{c['b']}, 0.7);
}}

/* Scrollbar */
scrollbar slider {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.3);
    border-radius: 4px;
    min-width: 6px;
    min-height: 6px;
}}

scrollbar slider:hover {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.5);
}}

/* Separator */
separator {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.15);
    min-height: 1px;
}}

/* ListBox */
list {{
    background-color: {c['bg']};
}}

list row {{
    padding: 4px 8px;
    border-bottom: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.05);
}}

list row:selected {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.12);
}}

/* Combo box */
combobox button {{
    background-color: {c['bg2']};
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.3);
    border-radius: 6px;
    color: {c['text']};
    font-family: monospace;
    font-size: 11px;
    padding: 4px 8px;
}}
"""


# ── GhostPortApp Base Class ─────────────────────────────────────────

class GhostPortApp(Gtk.Window):
    """Base class for all GhostPort desktop apps."""

    def __init__(self, title, app_id, default_size=(900, 650)):
        super().__init__(title=title)
        self.app_id = app_id
        self.app_title = title
        self._timers = []
        self._lock_fd = None
        self._default_size = default_size

        # Single instance check
        self._acquire_lock()

        # Window setup — honor saved geometry if present, else default
        saved_w, saved_h = self._load_geometry()
        self.set_default_size(saved_w or default_size[0], saved_h or default_size[1])
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self._on_destroy)

        # Wire keyboard shortcuts (Escape=close, Ctrl+Q=quit)
        self.connect("key-press-event", self._on_key_press)

        # Theme
        self.theme_hex = read_theme_color()
        self.colors = derive_colors(self.theme_hex)

        # Apply CSS
        self._apply_css()

        # Theme polling (check for changes every 3s)
        self._theme_timer = GLib.timeout_add_seconds(3, self._check_theme)
        self._timers.append(self._theme_timer)

        # Handle SIGUSR1 to raise window (for single-instance)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._raise_window)

        # Handle SIGTERM/SIGINT so pkill triggers proper cleanup
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._signal_quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._signal_quit)

        # Write PID file
        pid_file = f"/tmp/gp-{self.app_id}.pid"
        try:
            with open(pid_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

    def _acquire_lock(self):
        """Acquire single-instance lock. If already running, signal and exit."""
        lock_path = f"/tmp/gp-{self.app_id}.lock"
        pid_path = f"/tmp/gp-{self.app_id}.pid"
        try:
            self._lock_fd = open(lock_path, "w")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Lock held — check if the owning process is actually alive
            stale = False
            try:
                with open(pid_path) as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # Check if process exists
                # Process alive — signal it to raise window
                os.kill(pid, signal.SIGUSR1)
            except (ProcessLookupError, ValueError, FileNotFoundError):
                # PID is dead or pid file missing — stale lock
                stale = True
            except PermissionError:
                # Process exists but we can't signal it — not stale
                pass
            except Exception:
                stale = True

            if stale:
                # Clean up stale lock and re-acquire
                try:
                    if self._lock_fd:
                        self._lock_fd.close()
                    os.unlink(lock_path)
                except Exception:
                    pass
                try:
                    os.unlink(pid_path)
                except Exception:
                    pass
                try:
                    self._lock_fd = open(lock_path, "w")
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    sys.exit(0)
            else:
                sys.exit(0)

    def _raise_window(self, *_args):
        """Raise window to front (called via SIGUSR1)."""
        self.present()
        return True

    def _signal_quit(self, *_args):
        """Handle SIGTERM/SIGINT — clean up and exit."""
        self._on_destroy()
        return False

    def _apply_css(self, extra_css=""):
        """Apply base CSS + optional app-specific CSS."""
        css = generate_css(self.colors) + "\n" + extra_css
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        screen = Gdk.Screen.get_default()
        if screen:
            # Remove previous provider to avoid duplicate CSS providers
            # pylint: disable=access-member-before-definition
            # hasattr guard handles first-call case; pylint flow analysis misses it.
            if hasattr(self, '_css_provider') and self._css_provider:
                Gtk.StyleContext.remove_provider_for_screen(screen, self._css_provider)
            # pylint: enable=access-member-before-definition
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._css_provider = provider

    def _check_theme(self):
        """Poll theme.json for color changes."""
        try:
            new_hex = read_theme_color()
            if new_hex != self.theme_hex:
                self.theme_hex = new_hex
                self.colors = derive_colors(new_hex)
                self._apply_css()
                self.on_theme_changed()
        except Exception:
            pass
        return True

    def on_theme_changed(self):
        """Override in subclasses to handle theme changes (e.g., redraw cairo)."""
        pass

    def _on_destroy(self, *_args):
        """Clean up on window close."""
        # Save current window geometry before we tear down
        try:
            self._save_geometry()
        except Exception:
            pass
        for timer_id in self._timers:
            GLib.source_remove(timer_id)
        self._timers.clear()
        # Remove PID file
        try:
            os.unlink(f"/tmp/gp-{self.app_id}.pid")
        except Exception:
            pass
        # Release lock
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                os.unlink(f"/tmp/gp-{self.app_id}.lock")
            except Exception:
                pass
        Gtk.main_quit()

    # ── Window Geometry Persistence ─────────────────────────────────

    def _geom_path(self):
        base = os.path.expanduser("~/.config/phantom")
        return os.path.join(base, f"gp-{self.app_id}-geom.json")

    def _load_geometry(self):
        """Return (width, height) from last session, or (None, None) if unavailable."""
        try:
            with open(self._geom_path()) as f:
                data = json.load(f)
            w = int(data.get("width") or 0) or None
            h = int(data.get("height") or 0) or None
            # Sanity clamp: reject absurd sizes from corrupt state
            if w and (w < 300 or w > 4000):
                w = None
            if h and (h < 200 or h > 3000):
                h = None
            return w, h
        except Exception:
            return None, None

    def _save_geometry(self):
        """Persist current window size. Position is skipped on Wayland (unreliable)."""
        try:
            w, h = self.get_size()
        except Exception:
            return
        if w <= 0 or h <= 0:
            return
        path = self._geom_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"width": w, "height": h}, f)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # ── Keyboard Shortcuts ──────────────────────────────────────────

    def _on_key_press(self, widget, event):
        """Global key handler: Escape closes, Ctrl+Q quits."""
        keyval = event.keyval
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        # Escape — close window
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        # Ctrl+Q — quit
        if ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self._signal_quit()
            return True
        return False

    # ── Tooltip & Dialog Helpers ────────────────────────────────────

    def attach_tooltip(self, widget, text):
        """Attach a hover tooltip to any GTK widget."""
        if widget is None or not text:
            return
        try:
            widget.set_tooltip_text(text)
            widget.set_has_tooltip(True)
        except Exception:
            pass

    def show_error(self, title, message, detail=None):
        """Show a themed modal error dialog. Call from main thread only."""
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
            secondary_text=message,
        )
        if detail:
            try:
                area = dlg.get_message_area()
                expander = Gtk.Expander(label="Details")
                detail_label = Gtk.Label(label=str(detail))
                detail_label.set_selectable(True)
                detail_label.set_line_wrap(True)
                detail_label.set_xalign(0)
                expander.add(detail_label)
                area.pack_start(expander, False, False, 6)
                area.show_all()
            except Exception:
                pass
        dlg.run()
        dlg.destroy()

    # ── Help Dialog ─────────────────────────────────────────────────
    # Per-app contextual help. Apps define a HELP_SECTIONS list of
    # (title, body) tuples and call self.show_help_dialog(HELP_SECTIONS)
    # from their Help button handler. First section is auto-expanded;
    # the rest are click-to-expand. Added 2026-04-21 (extracted from the
    # Bulkhead `? Help` pattern once a second app needed it).

    def show_help_dialog(self, sections, title=None, tutorial_path=None):
        """Open a modal with a scrollable column of collapsible help sections.

        sections: list of (heading, body_text) tuples.
        title: dialog window title (defaults to "<AppName> — Help").
        tutorial_path: optional path string shown at the bottom.
        """
        dlg = Gtk.Dialog(
            title=title or f"{self.app_title} — Help",
            transient_for=self,
            modal=True,
        )
        dlg.set_default_size(640, 560)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)

        area = dlg.get_content_area()
        area.set_border_width(12)
        area.set_spacing(8)

        intro = Gtk.Label()
        intro.set_markup(
            "<b>Click a section to expand it.</b> "
            "You can leave this window open while you work."
        )
        intro.set_halign(Gtk.Align.START)
        intro.set_line_wrap(True)
        area.pack_start(intro, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        area.pack_start(scroll, True, True, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll.add(box)

        for idx, (heading, body) in enumerate(sections):
            exp = Gtk.Expander(label=heading)
            exp.set_expanded(idx == 0)
            lbl = Gtk.Label(label=body)
            lbl.set_line_wrap(True)
            lbl.set_xalign(0.0)
            lbl.set_selectable(True)
            lbl.set_margin_start(18)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(8)
            lbl.set_margin_end(8)
            exp.add(lbl)
            box.pack_start(exp, False, False, 0)

        if tutorial_path:
            hint = Gtk.Label()
            hint.set_markup(f"<i>Full walkthrough: {tutorial_path}</i>")
            hint.set_halign(Gtk.Align.START)
            hint.set_line_wrap(True)
            area.pack_start(hint, False, False, 0)

        dlg.show_all()
        dlg.run()
        dlg.destroy()

    def make_help_button(self, callback=None, sections=None, tutorial_path=None):
        """Create a '? Help' button. Either pass a callback, or pass sections
        (and optional tutorial_path) to auto-wire to show_help_dialog."""
        btn = Gtk.Button(label="? Help")
        btn.get_style_context().add_class("gp-btn")
        btn.set_tooltip_text("Guided tour — what everything on this screen means")
        if callback is not None:
            btn.connect("clicked", callback)
        elif sections is not None:
            btn.connect("clicked",
                        lambda _b: self.show_help_dialog(sections, tutorial_path=tutorial_path))
        return btn

    # ── Help Button (legacy — opens generic shortcuts widget) ───────

    def add_help_button(self, container, pack_end=True):
        """Add a small "?" button to a header/toolbar that opens the shortcuts widget."""
        btn = Gtk.Button(label="?")
        btn.get_style_context().add_class("gp-btn")
        btn.set_tooltip_text("Keyboard shortcuts (Esc=close, Ctrl+Q=quit)")

        def _on_help_click(_btn):
            try:
                subprocess.Popen(
                    ["python3", "/opt/phantom/desktop/ghostport-shortcuts.py"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                self.show_error("Help unavailable",
                                "Could not launch the shortcuts widget.",
                                "Check /opt/phantom/desktop/ghostport-shortcuts.py")

        btn.connect("clicked", _on_help_click)
        try:
            if pack_end and hasattr(container, "pack_end"):
                container.pack_end(btn, False, False, 0)
            elif hasattr(container, "pack_start"):
                container.pack_start(btn, False, False, 0)
            else:
                container.add(btn)
        except Exception:
            pass
        return btn

    # ── UI Helpers ───────────────────────────────────────────────────

    def make_header(self, title=None, subtitle=None):
        """Create a styled header bar with title and optional subtitle."""
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header.get_style_context().add_class("gp-header")

        title_label = Gtk.Label(label=title or self.app_title)
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class("gp-header-title")
        header.pack_start(title_label, False, False, 0)

        if subtitle:
            sub_label = Gtk.Label(label=subtitle)
            sub_label.set_halign(Gtk.Align.START)
            sub_label.get_style_context().add_class("gp-header-subtitle")
            header.pack_start(sub_label, False, False, 0)

        return header

    def make_status_bar(self, text="Ready"):
        """Create a bottom status bar."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("gp-status")
        self._status_label = Gtk.Label(label=text)
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.get_style_context().add_class("gp-status-text")
        bar.pack_start(self._status_label, True, True, 0)
        return bar

    def set_status(self, text):
        """Update the status bar text."""
        if hasattr(self, '_status_label'):
            self._status_label.set_text(text)

    def make_scrolled(self, child=None):
        """Create a scrolled window, optionally containing a child widget."""
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        if child:
            sw.add(child)
        return sw

    def make_label(self, text, css_class="gp-text"):
        """Create a styled label."""
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.set_line_wrap(True)
        label.get_style_context().add_class(css_class)
        return label

    def make_button(self, label, callback, css_class="gp-btn"):
        """Create a styled button with a click handler."""
        btn = Gtk.Button(label=label)
        btn.get_style_context().add_class(css_class)
        btn.connect("clicked", callback)
        return btn

    # ── Async Helpers ────────────────────────────────────────────────

    def run_async(self, func, callback=None):
        """Run func in a background thread, call callback(result) on main thread."""
        def _worker():
            try:
                result = func()
            except Exception as e:
                result = e
            if callback:
                GLib.idle_add(callback, result)
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t

    def run_cmd(self, cmd_list, timeout=30):
        """Run a command and return (stdout, stderr, returncode)."""
        try:
            result = subprocess.run(
                cmd_list, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command timed out", 1
        except Exception as e:
            return "", str(e), 1

    def run_sudo(self, cmd_list, timeout=30):
        """Run a command with sudo."""
        return self.run_cmd(["sudo"] + cmd_list, timeout=timeout)

    # ── Polling ──────────────────────────────────────────────────────

    def poll_start(self, interval_seconds, func):
        """Start polling func every interval_seconds. func should return True to continue."""
        timer_id = GLib.timeout_add_seconds(interval_seconds, func)
        self._timers.append(timer_id)
        return timer_id

    def poll_stop(self, timer_id):
        """Stop a specific poll timer."""
        if timer_id in self._timers:
            GLib.source_remove(timer_id)
            self._timers.remove(timer_id)

    # ── Run ──────────────────────────────────────────────────────────

    def run(self):
        """Show the window and start the GTK main loop."""
        self.show_all()
        Gtk.main()
