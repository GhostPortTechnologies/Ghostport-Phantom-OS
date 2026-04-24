#!/usr/bin/env python3
"""
GhostPort Command Deck — Standalone dashboard app (no browser chrome).
Uses WebKit2GTK to render the dashboard in a native window.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, GLib, Gdk, GdkPixbuf

DASHBOARD_URL = "https://localhost:4200"
APP_TITLE = "GhostPort Command Deck"


class GhostPortDeck(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_TITLE)
        self.set_default_size(1200, 800)

        # Set app_id for labwc window rule matching
        self.set_wmclass("ghostport-deck", "ghostport-deck")

        # WebKit context with persistent cookies
        ctx = WebKit2.WebContext.get_default()
        cm = ctx.get_cookie_manager()
        cm.set_persistent_storage(
            GLib.get_user_data_dir() + "/ghostport-deck-cookies",
            WebKit2.CookiePersistentStorage.SQLITE
        )

        # WebKit settings
        settings = WebKit2.Settings()
        settings.set_property("enable-developer-extras", False)
        settings.set_property("enable-javascript", True)
        settings.set_property("hardware-acceleration-policy",
                              WebKit2.HardwareAccelerationPolicy.ALWAYS)

        # WebView
        self.webview = WebKit2.WebView.new_with_context(ctx)
        self.webview.set_settings(settings)

        # Accept self-signed SSL cert
        self.webview.connect("load-failed-with-tls-errors", self._on_tls_error)

        # Handle new window requests (open in same view)
        self.webview.connect("decide-policy", self._on_decide_policy)

        self.add(self.webview)
        self.webview.load_uri(DASHBOARD_URL)
        self.connect("destroy", Gtk.main_quit)

        # Window icon
        icon_path = "/home/ghostport-admin/.local/share/icons/ghostport.png"
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 64, 64, True)
            self.set_icon(pixbuf)
        except Exception:
            pass

        # Keyboard shortcuts
        self.connect("key-press-event", self._on_key_press)
        self._fullscreen = False

    def _on_key_press(self, widget, event):
        key = Gdk.keyval_name(event.keyval)
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if key == "F11":
            if self._fullscreen:
                self.unfullscreen()
            else:
                self.fullscreen()
            self._fullscreen = not self._fullscreen
            return True
        if ctrl and key in ("r", "R"):
            self.webview.reload()
            return True
        if ctrl and key in ("q", "Q"):
            Gtk.main_quit()
            return True
        return False

    def _on_tls_error(self, webview, failing_uri, certificate, errors):
        """Accept self-signed cert for localhost."""
        if "localhost" in failing_uri or "127.0.0.1" in failing_uri:
            ctx = webview.get_context()
            ctx.allow_tls_certificate_for_host(certificate, "localhost")
            ctx.allow_tls_certificate_for_host(certificate, "127.0.0.1")
            webview.load_uri(failing_uri)
            return True
        return False

    def _on_decide_policy(self, webview, decision, decision_type):
        """Keep navigation in the same window."""
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            nav = decision.get_navigation_action()
            req = nav.get_request()
            uri = req.get_uri()
            # Only allow localhost navigation
            if "localhost" in uri or "127.0.0.1" in uri:
                decision.use()
            else:
                decision.ignore()
            return True
        return False


def main():
    # Prevent multiple instances
    import fcntl, sys, os
    lock_file = os.path.expanduser("~/.ghostport-deck.lock")
    fp = open(lock_file, 'w')
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("GhostPort Dashboard is already running.")
        sys.exit(0)

    win = GhostPortDeck()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
