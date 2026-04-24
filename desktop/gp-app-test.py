#!/usr/bin/env python3
"""Quick test app to verify GhostPortApp base class works."""
import sys
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

class TestApp(GhostPortApp):
    def __init__(self):
        super().__init__("GhostPort Test App", "test", (600, 400))

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Header
        vbox.pack_start(self.make_header("Test App", "Verifying base class works"), False, False, 0)

        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.get_style_context().add_class("gp-content")

        content.pack_start(self.make_label("Theme color: " + self.colors['accent'], "gp-accent"), False, False, 0)
        content.pack_start(self.make_label("This is normal text", "gp-text"), False, False, 0)
        content.pack_start(self.make_label("This is dim text", "gp-dim"), False, False, 0)
        content.pack_start(self.make_label("This is bright text", "gp-bright"), False, False, 0)
        content.pack_start(self.make_label("DANGER", "gp-danger"), False, False, 0)
        content.pack_start(self.make_label("WARNING", "gp-warning"), False, False, 0)
        content.pack_start(self.make_label("SUCCESS", "gp-success"), False, False, 0)

        # Cards
        for cls in ["gp-card", "gp-card-danger", "gp-card-warning", "gp-card-info"]:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            card.get_style_context().add_class(cls)
            card.pack_start(self.make_label(f"Card class: {cls}"), False, False, 0)
            content.pack_start(card, False, False, 4)

        # Buttons
        btn_box = Gtk.Box(spacing=8)
        btn_box.pack_start(self.make_button("Normal", lambda b: self.set_status("Normal clicked")), False, False, 0)
        btn_box.pack_start(self.make_button("Primary", lambda b: self.set_status("Primary clicked"), "gp-btn-primary"), False, False, 0)
        btn_box.pack_start(self.make_button("Danger", lambda b: self.set_status("Danger clicked"), "gp-btn-danger"), False, False, 0)
        content.pack_start(btn_box, False, False, 8)

        vbox.pack_start(content, True, True, 0)

        # Status bar
        vbox.pack_start(self.make_status_bar("Phase 0 test — base class working"), False, False, 0)

if __name__ == "__main__":
    app = TestApp()
    app.run()
