#!/usr/bin/env python3
"""
GhostPort Desktop Widget Engine
Lightweight GTK3 + GtkLayerShell widget system for Phantom OS.
Single process manages all widget windows with drag, persist, and live data.
"""

# cairo MUST be imported before gi to register the foreign struct converter
try:
    import cairo
except ImportError:
    cairo = None

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

# GtkLayerShell is optional — fall back to regular GTK windows (SSH, X11, etc.)
USE_LAYER_SHELL = False
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    # Only use layer shell if we're on a real Wayland session
    display = Gdk.Display.get_default()
    if display and display.__class__.__name__ == 'GdkWaylandDisplay':
        USE_LAYER_SHELL = True
except (ValueError, ImportError):
    pass
import json
import signal
import os
import sys
import math
import threading
import urllib.request
import urllib.error
import ssl
import time
import subprocess
import fcntl

# ── Config ───────────────────────────────────────────────────────────
LAYOUT_DIR = os.path.expanduser("~/.config/phantom")
LAYOUT_FILE = os.path.join(LAYOUT_DIR, "widget-layout.json")
LOCK_FILE = "/tmp/gp-widgets.lock"
PID_FILE = "/tmp/gp-widgets.pid"
TOKEN_FILE = "/run/ghostport/widget-token"
STATUS_CACHE = "/tmp/gp-bar-status.json"
API_BASE = "https://localhost:4200"
CHAMBER_BASE = "http://127.0.0.1:4242"
THEME_FILE = "/etc/phantom/theme.json"
# Split to survive gp-theme's sed s/#39ff8f/#new/g — never gets matched
ACCENT_DEFAULT = "#" + "39" + "ff" + "8f"

# SSL context for localhost self-signed cert only — all callers target 127.0.0.1/localhost
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ── Theme Color Utilities ────────────────────────────────────────────

def hex_to_rgb(hex_color):
    """Convert #rrggbb to (r, g, b) normalized 0.0-1.0 floats."""
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

def hex_to_rgb255(hex_color):
    """Convert #rrggbb to (r, g, b) as 0-255 ints."""
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def read_theme_color():
    """Read accent color from theme.json."""
    try:
        with open(THEME_FILE) as f:
            data = json.load(f)
            color = data.get("color", ACCENT_DEFAULT)
            if isinstance(color, str) and len(color) == 7 and color[0] == '#' and all(c in '0123456789abcdefABCDEF' for c in color[1:]):
                return color
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return ACCENT_DEFAULT

def _derive_colors(hex_color):
    """Derive a full palette from a single accent hex color."""
    r, g, b = hex_to_rgb255(hex_color)
    clamp = lambda v: max(0, min(255, int(v)))
    fmt = lambda rv, gv, bv: f"#{clamp(rv):02x}{clamp(gv):02x}{clamp(bv):02x}"
    return {
        "accent":   hex_color,
        "r": r, "g": g, "b": b,
        "dim":      fmt(r*40//100, g*40//100, b*40//100),
        "text":     fmt(min(r*22//100+200, 255), min(g*22//100+200, 255), min(b*22//100+200, 255)),
        "bg":       fmt(max(r*4//100, 6), max(g*4//100, 6), max(b*4//100, 6)),
        "bg2":      fmt(max(r*7//100, 10), max(g*7//100, 10), max(b*7//100, 10)),
        "border":   fmt(r*10//100, g*10//100, b*10//100),
        "text_dim": fmt(r*29//100, g*29//100, b*29//100),
    }


def generate_css(hex_color):
    """Generate CSS dynamically from a single accent color — no hardcoded hex values."""
    c = _derive_colors(hex_color)
    r, g, b = c["r"], c["g"], c["b"]
    br, bg_, bb = hex_to_rgb255(c["bg"])
    b2r, b2g, b2b = hex_to_rgb255(c["bg2"])
    dr, dg, db = hex_to_rgb255(c["dim"])
    return f"""
/* Global widget styling — generated from {c["accent"]} */
.widget-window {{
    background-color: rgba({br}, {bg_}, {bb}, 0.92);
    border: 1px solid rgba({r}, {g}, {b}, 0.3);
    border-radius: 12px;
    color: {c["text"]};
}}
.widget-titlebar {{
    background-color: rgba({b2r}, {b2g}, {b2b}, 0.95);
    border-bottom: 1px solid rgba({r}, {g}, {b}, 0.15);
    border-radius: 12px 12px 0 0;
    padding: 4px 10px;
    min-height: 24px;
}}
.widget-title {{
    color: {c["accent"]};
    font-family: monospace; font-size: 11px; font-weight: bold;
}}
.widget-close {{
    background: transparent;
    border: 1px solid rgba({r}, {g}, {b}, 0.2);
    border-radius: 6px;
    color: {c["accent"]};
    min-width: 22px; min-height: 22px; padding: 0; font-size: 13px;
}}
.widget-close:hover {{
    background-color: rgba({r}, {g}, {b}, 0.15);
    border-color: rgba({r}, {g}, {b}, 0.5);
}}
.widget-body {{ padding: 10px; }}
.body-text {{
    color: {c["text"]}; font-family: monospace; font-size: 11px;
}}
.accent-text {{
    color: {c["accent"]}; font-family: monospace; font-size: 11px;
}}
.dim-text {{
    color: {c["dim"]}; font-family: monospace; font-size: 10px;
}}
.big-number {{
    color: {c["accent"]}; font-family: monospace; font-size: 48px; font-weight: bold;
}}
.big-number-orange {{
    color: #ff9f39; font-family: monospace; font-size: 48px; font-weight: bold;
}}
.big-number-red {{
    color: #ff4444; font-family: monospace; font-size: 48px; font-weight: bold;
}}
.score-label {{
    color: {c["dim"]}; font-family: monospace; font-size: 10px;
}}
.breakdown-name {{
    color: {c["text"]}; font-family: monospace; font-size: 10px;
}}
.breakdown-value-pos {{
    color: {c["accent"]}; font-family: monospace; font-size: 10px; font-weight: bold;
}}
.breakdown-value-neg {{
    color: #ff4444; font-family: monospace; font-size: 10px; font-weight: bold;
}}
/* Mode widget */
.mode-indicator {{ font-size: 12px; }}
.mode-name {{
    color: {c["accent"]}; font-family: monospace; font-size: 18px; font-weight: bold;
}}
.mode-desc {{
    color: {c["dim"]}; font-family: monospace; font-size: 10px;
}}
.mode-btn {{
    background-color: rgba({b2r}, {b2g}, {b2b}, 0.9);
    border: 1px solid rgba({r}, {g}, {b}, 0.3);
    border-radius: 6px;
    color: {c["text"]}; font-family: monospace; font-size: 10px;
    padding: 4px 8px; min-height: 28px;
}}
.mode-btn:hover {{
    background-color: rgba({r}, {g}, {b}, 0.1);
    border-color: rgba({r}, {g}, {b}, 0.6);
    color: {c["accent"]};
}}
.mode-btn-active {{
    background-color: rgba({r}, {g}, {b}, 0.15);
    border: 1px solid rgba({r}, {g}, {b}, 0.6);
    border-radius: 6px;
    color: {c["accent"]}; font-family: monospace; font-size: 10px; font-weight: bold;
    padding: 4px 8px; min-height: 28px;
}}
/* Tunnel widget */
.tunnel-up {{
    color: {c["accent"]}; font-family: monospace; font-size: 11px; font-weight: bold;
}}
.tunnel-down {{
    color: #ff4444; font-family: monospace; font-size: 11px; font-weight: bold;
}}
.tunnel-label {{
    color: {c["text"]}; font-family: monospace; font-size: 11px;
}}
.tunnel-data {{
    color: {c["dim"]}; font-family: monospace; font-size: 10px;
}}
/* Ads widget */
.ads-big {{
    color: {c["accent"]}; font-family: monospace; font-size: 42px; font-weight: bold;
}}
.ads-label {{
    color: {c["dim"]}; font-family: monospace; font-size: 10px;
}}
.ads-alltime {{
    color: {c["text"]}; font-family: monospace; font-size: 12px;
}}
/* Clients widget */
.client-hostname {{
    color: {c["accent"]}; font-family: monospace; font-size: 11px;
}}
.client-ip {{
    color: {c["dim"]}; font-family: monospace; font-size: 10px;
}}
.client-dot-online {{ color: {c["accent"]}; font-size: 8px; }}
.client-dot-offline {{ color: #ff4444; font-size: 8px; }}
/* Chamber widget */
.chamber-messages {{
    background-color: rgba(5, 10, 5, 0.6);
    border: 1px solid rgba({r}, {g}, {b}, 0.1);
    border-radius: 6px;
}}
.chamber-msg-user {{
    color: {c["accent"]}; font-family: monospace; font-size: 10px; font-weight: bold;
}}
.chamber-msg-time {{
    color: #506850; font-family: monospace; font-size: 9px;
}}
.chamber-msg-text {{
    color: {c["text"]}; font-family: monospace; font-size: 10px;
}}
.chamber-input {{
    background-color: rgba({br}, {bg_}, {bb}, 0.9);
    border: 1px solid rgba({r}, {g}, {b}, 0.3);
    border-radius: 6px;
    color: {c["accent"]}; font-family: monospace; font-size: 11px;
    padding: 6px 8px; min-height: 30px;
}}
.chamber-input:focus {{
    border-color: rgba({r}, {g}, {b}, 0.7);
}}
.chamber-send {{
    background-color: rgba({r}, {g}, {b}, 0.15);
    border: 1px solid rgba({r}, {g}, {b}, 0.4);
    border-radius: 6px;
    color: {c["accent"]}; font-family: monospace; font-size: 11px; font-weight: bold;
    min-width: 50px; padding: 4px 10px;
}}
.chamber-send:hover {{
    background-color: rgba({r}, {g}, {b}, 0.25);
    border-color: rgba({r}, {g}, {b}, 0.7);
}}
/* Library widget */
.library-item {{
    background-color: rgba(15, 25, 15, 0.8);
    border: 1px solid rgba({r}, {g}, {b}, 0.1);
    border-radius: 8px; padding: 8px 10px;
}}
.library-item:hover {{ border-color: rgba({r}, {g}, {b}, 0.3); }}
.library-icon {{ font-size: 22px; }}
.library-name {{
    color: {c["accent"]}; font-family: monospace; font-size: 12px; font-weight: bold;
}}
.library-desc {{
    color: {c["dim"]}; font-family: monospace; font-size: 10px;
}}
.library-reset {{
    background-color: rgba(255, 68, 68, 0.1);
    border: 1px solid rgba(255, 68, 68, 0.3);
    border-radius: 6px; color: #ff4444;
    font-family: monospace; font-size: 10px; padding: 6px 12px;
}}
.library-reset:hover {{
    background-color: rgba(255, 68, 68, 0.2);
    border-color: rgba(255, 68, 68, 0.6);
}}
/* Scrollbar */
scrollbar {{ background-color: transparent; }}
scrollbar slider {{
    background-color: rgba({r}, {g}, {b}, 0.2);
    border-radius: 4px; min-width: 6px; min-height: 20px;
}}
scrollbar slider:hover {{ background-color: rgba({r}, {g}, {b}, 0.35); }}
/* Switch */
switch {{
    background-color: rgba(30, 40, 30, 0.9);
    border: 1px solid rgba({r}, {g}, {b}, 0.2);
    border-radius: 12px; min-width: 40px; min-height: 20px;
}}
switch:checked {{
    background-color: rgba({r}, {g}, {b}, 0.3);
    border-color: rgba({r}, {g}, {b}, 0.6);
}}
switch slider {{
    background-color: {c["dim"]};
    border-radius: 10px; min-width: 18px; min-height: 18px;
}}
switch:checked slider {{ background-color: {c["accent"]}; }}
/* Loading */
.loading-text {{
    color: {c["dim"]}; font-family: monospace; font-size: 11px; font-style: italic;
}}
"""


# ── Default Layout ───────────────────────────────────────────────────
DEFAULT_LAYOUT = {
    "widgets": {
        "score":   {"enabled": False, "x": 50,  "y": 60,  "width": 250, "height": 300},
        "chamber": {"enabled": False, "x": 320, "y": 60,  "width": 380, "height": 500},
        "mode":    {"enabled": False, "x": 50,  "y": 380, "width": 280, "height": 200},
        "tunnel":  {"enabled": False, "x": 720, "y": 60,  "width": 280, "height": 220},
        "ads":     {"enabled": False, "x": 720, "y": 300, "width": 250, "height": 180},
        "clients": {"enabled": False, "x": 720, "y": 500, "width": 280, "height": 300},
        "theme":   {"enabled": False, "x": 350, "y": 400, "width": 260, "height": 280},
    }
}

# ── CSS Theme Template ───────────────────────────────────────────────
CSS_TEMPLATE = """
/* Global widget styling */
.widget-window {
    background-color: rgba(10, 6, 6, 0.92);
    border: 1px solid rgba(255, 153, 68, 0.3);
    border-radius: 12px;
    color: #ffe9d6;
}

.widget-titlebar {
    background-color: rgba(17, 10, 10, 0.95);
    border-bottom: 1px solid rgba(255, 153, 68, 0.15);
    border-radius: 12px 12px 0 0;
    padding: 4px 10px;
    min-height: 24px;
}

.widget-title {
    color: #ff9944;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
}

.widget-close {
    background: transparent;
    border: 1px solid rgba(255, 153, 68, 0.2);
    border-radius: 6px;
    color: #ff9944;
    min-width: 22px;
    min-height: 22px;
    padding: 0;
    font-size: 13px;
}

.widget-close:hover {
    background-color: rgba(255, 153, 68, 0.15);
    border-color: rgba(255, 153, 68, 0.5);
}

.widget-body {
    padding: 10px;
}

.body-text {
    color: #ffe9d6;
    font-family: monospace;
    font-size: 11px;
}

.accent-text {
    color: #ff9944;
    font-family: monospace;
    font-size: 11px;
}

.dim-text {
    color: #603a19;
    font-family: monospace;
    font-size: 10px;
}

.big-number {
    color: #ff9944;
    font-family: monospace;
    font-size: 48px;
    font-weight: bold;
}

.big-number-orange {
    color: #ff9f39;
    font-family: monospace;
    font-size: 48px;
    font-weight: bold;
}

.big-number-red {
    color: #ff4444;
    font-family: monospace;
    font-size: 48px;
    font-weight: bold;
}

.score-label {
    color: #603a19;
    font-family: monospace;
    font-size: 10px;
}

.breakdown-name {
    color: #ffe9d6;
    font-family: monospace;
    font-size: 10px;
}

.breakdown-value-pos {
    color: #ff9944;
    font-family: monospace;
    font-size: 10px;
    font-weight: bold;
}

.breakdown-value-neg {
    color: #ff4444;
    font-family: monospace;
    font-size: 10px;
    font-weight: bold;
}

/* Mode widget */
.mode-indicator {
    font-size: 12px;
}

.mode-name {
    color: #ff9944;
    font-family: monospace;
    font-size: 18px;
    font-weight: bold;
}

.mode-desc {
    color: #603a19;
    font-family: monospace;
    font-size: 10px;
}

.mode-btn {
    background-color: rgba(17, 10, 10, 0.9);
    border: 1px solid rgba(255, 153, 68, 0.3);
    border-radius: 6px;
    color: #ffe9d6;
    font-family: monospace;
    font-size: 10px;
    padding: 4px 8px;
    min-height: 28px;
}

.mode-btn:hover {
    background-color: rgba(255, 153, 68, 0.1);
    border-color: rgba(255, 153, 68, 0.6);
    color: #ff9944;
}

.mode-btn-active {
    background-color: rgba(255, 153, 68, 0.15);
    border: 1px solid rgba(255, 153, 68, 0.6);
    border-radius: 6px;
    color: #ff9944;
    font-family: monospace;
    font-size: 10px;
    font-weight: bold;
    padding: 4px 8px;
    min-height: 28px;
}

/* Tunnel widget */
.tunnel-up {
    color: #ff9944;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
}

.tunnel-down {
    color: #ff4444;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
}

.tunnel-label {
    color: #ffe9d6;
    font-family: monospace;
    font-size: 11px;
}

.tunnel-data {
    color: #603a19;
    font-family: monospace;
    font-size: 10px;
}

/* Ads widget */
.ads-big {
    color: #ff9944;
    font-family: monospace;
    font-size: 42px;
    font-weight: bold;
}

.ads-label {
    color: #603a19;
    font-family: monospace;
    font-size: 10px;
}

.ads-alltime {
    color: #ffe9d6;
    font-family: monospace;
    font-size: 12px;
}

/* Clients widget */
.client-hostname {
    color: #ff9944;
    font-family: monospace;
    font-size: 11px;
}

.client-ip {
    color: #603a19;
    font-family: monospace;
    font-size: 10px;
}

.client-dot-online {
    color: #ff9944;
    font-size: 8px;
}

.client-dot-offline {
    color: #ff4444;
    font-size: 8px;
}

/* Chamber widget */
.chamber-messages {
    background-color: rgba(5, 10, 5, 0.6);
    border: 1px solid rgba(255, 153, 68, 0.1);
    border-radius: 6px;
}

.chamber-msg-user {
    color: #ff9944;
    font-family: monospace;
    font-size: 10px;
    font-weight: bold;
}

.chamber-msg-time {
    color: #506850;
    font-family: monospace;
    font-size: 9px;
}

.chamber-msg-text {
    color: #ffe9d6;
    font-family: monospace;
    font-size: 10px;
}

.chamber-input {
    background-color: rgba(10, 6, 6, 0.9);
    border: 1px solid rgba(255, 153, 68, 0.3);
    border-radius: 6px;
    color: #ff9944;
    font-family: monospace;
    font-size: 11px;
    padding: 6px 8px;
    min-height: 30px;
}

.chamber-input:focus {
    border-color: rgba(255, 153, 68, 0.7);
}

.chamber-send {
    background-color: rgba(255, 153, 68, 0.15);
    border: 1px solid rgba(255, 153, 68, 0.4);
    border-radius: 6px;
    color: #ff9944;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    min-width: 50px;
    padding: 4px 10px;
}

.chamber-send:hover {
    background-color: rgba(255, 153, 68, 0.25);
    border-color: rgba(255, 153, 68, 0.7);
}

/* Library widget */
.library-item {
    background-color: rgba(15, 25, 15, 0.8);
    border: 1px solid rgba(255, 153, 68, 0.1);
    border-radius: 8px;
    padding: 8px 10px;
}

.library-item:hover {
    border-color: rgba(255, 153, 68, 0.3);
}

.library-icon {
    font-size: 22px;
}

.library-name {
    color: #ff9944;
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}

.library-desc {
    color: #603a19;
    font-family: monospace;
    font-size: 10px;
}

.library-reset {
    background-color: rgba(255, 68, 68, 0.1);
    border: 1px solid rgba(255, 68, 68, 0.3);
    border-radius: 6px;
    color: #ff4444;
    font-family: monospace;
    font-size: 10px;
    padding: 6px 12px;
}

.library-reset:hover {
    background-color: rgba(255, 68, 68, 0.2);
    border-color: rgba(255, 68, 68, 0.6);
}

/* Scrollbar theming */
scrollbar {
    background-color: transparent;
}

scrollbar slider {
    background-color: rgba(255, 153, 68, 0.2);
    border-radius: 4px;
    min-width: 6px;
    min-height: 20px;
}

scrollbar slider:hover {
    background-color: rgba(255, 153, 68, 0.35);
}

/* Switch styling */
switch {
    background-color: rgba(30, 40, 30, 0.9);
    border: 1px solid rgba(255, 153, 68, 0.2);
    border-radius: 12px;
    min-width: 40px;
    min-height: 20px;
}

switch:checked {
    background-color: rgba(255, 153, 68, 0.3);
    border-color: rgba(255, 153, 68, 0.6);
}

switch slider {
    background-color: #603a19;
    border-radius: 10px;
    min-width: 18px;
    min-height: 18px;
}

switch:checked slider {
    background-color: #ff9944;
}

/* Loading state */
.loading-text {
    color: #603a19;
    font-family: monospace;
    font-size: 11px;
    font-style: italic;
}
"""

# ── Utility Functions ────────────────────────────────────────────────

def load_layout():
    """Load widget layout from disk or return defaults."""
    try:
        with open(LAYOUT_FILE) as f:
            data = json.load(f)
            # Merge with defaults for any missing widgets
            for wid, wdef in DEFAULT_LAYOUT["widgets"].items():
                if wid not in data.get("widgets", {}):
                    data.setdefault("widgets", {})[wid] = dict(wdef)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_LAYOUT))


def save_layout(layout):
    """Persist layout to disk."""
    os.makedirs(LAYOUT_DIR, exist_ok=True)
    tmp = LAYOUT_FILE + ".tmp"
    try:
        with open(tmp, 'w') as f:
            json.dump(layout, f, indent=2)
        os.replace(tmp, LAYOUT_FILE)
    except OSError as e:
        print(f"[widgets] Failed to save layout: {e}", file=sys.stderr)


def get_token():
    """Read API bearer token."""
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return ""


def http_get(url, timeout=5):
    """Fetch URL, returns parsed JSON or None."""
    try:
        ctx = SSL_CTX if url.startswith("https") else None
        req = urllib.request.Request(url)
        token = get_token()
        if token and "localhost:4200" in url:
            req.add_header("Authorization", f"Bearer {token}")
        # nosemgrep: dynamic-urllib-use-detected - url is localhost:4200 dashboard API
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def http_post_json(url, data, timeout=5):
    """POST JSON, returns parsed response or None."""
    try:
        ctx = SSL_CTX if url.startswith("https") else None
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header("Content-Type", "application/json")
        token = get_token()
        if token and "localhost:4200" in url:
            req.add_header("Authorization", f"Bearer {token}")
        # nosemgrep: dynamic-urllib-use-detected - url is localhost:4200 dashboard API
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def read_status_cache():
    """Read cached status from bar fetch script."""
    try:
        with open(STATUS_CACHE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def threaded_fetch(url, callback, post_data=None, widget=None):
    """Run HTTP fetch in a thread, schedule callback on GTK main thread.
    If widget is provided, skip callback if widget is no longer alive."""
    def _worker():
        if post_data is not None:
            result = http_post_json(url, post_data)
        else:
            result = http_get(url)
        def _safe_callback(result):
            if widget and not widget.alive:
                return False
            return callback(result)
        GLib.idle_add(_safe_callback, result)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ── Base Widget Window ───────────────────────────────────────────────

class WidgetWindow:
    """Base class for all desktop widgets."""

    WIDGET_ID = "base"
    TITLE = "Widget"
    DEFAULT_W = 280
    DEFAULT_H = 200

    def __init__(self, engine):
        self.engine = engine
        self.layout = engine.layout["widgets"].get(self.WIDGET_ID, {})
        self.dragging = False
        self._drag_anchor_x = 0
        self._drag_anchor_y = 0
        self.pos_x = self.layout.get("x", 50)
        self.pos_y = self.layout.get("y", 50)
        self.timer_id = None
        self._build_window()

    def _build_window(self):
        self.window = Gtk.Window()
        w = self.layout.get("width", self.DEFAULT_W)
        h = self.layout.get("height", self.DEFAULT_H)
        self.window.set_default_size(w, h)

        if USE_LAYER_SHELL:
            # Wayland layer shell — OVERLAY layer so widgets sit on top of
            # the desktop-icons canvas (which is ALSO on BACKGROUND) and
            # stay visible above normal app windows. Matches the behavior
            # of ghostport-shortcuts.py. Changed from BACKGROUND 2026-04-21
            # after widgets were invisible (hidden behind icons overlay).
            GtkLayerShell.init_for_window(self.window)
            GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_exclusive_zone(self.window, -1)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.BOTTOM, False)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.RIGHT, False)
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.TOP, self.pos_y)
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.LEFT, self.pos_x)
            GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.ON_DEMAND)
        else:
            # Fallback: regular borderless GTK window (SSH, X11, etc.)
            self.window.set_decorated(False)
            self.window.set_skip_taskbar_hint(True)
            self.window.set_skip_pager_hint(True)
            self.window.set_keep_below(True)
            self.window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
            self.window.move(self.pos_x, self.pos_y)

        # Visual setup
        screen = Gdk.Screen.get_default()
        if screen:
            visual = screen.get_rgba_visual()
            if visual:
                self.window.set_visual(visual)
        self.window.set_app_paintable(True)

        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.get_style_context().add_class("widget-window")

        # Title bar (drag handle)
        titlebar = Gtk.EventBox()
        titlebar.get_style_context().add_class("widget-titlebar")
        titlebar.connect("button-press-event", self._on_title_press)
        titlebar.connect("button-release-event", self._on_title_release)
        titlebar.connect("motion-notify-event", self._on_title_motion)
        titlebar.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )

        title_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_lbl = Gtk.Label(label=self.TITLE)
        title_lbl.get_style_context().add_class("widget-title")
        title_lbl.set_halign(Gtk.Align.START)
        title_hbox.pack_start(title_lbl, True, True, 0)

        close_btn = Gtk.Button(label="\u00d7")
        close_btn.get_style_context().add_class("widget-close")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.connect("clicked", self._on_close)
        title_hbox.pack_end(close_btn, False, False, 0)

        titlebar.add(title_hbox)
        main_box.pack_start(titlebar, False, False, 0)

        # Body
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.body.get_style_context().add_class("widget-body")
        self.build_body(self.body)
        main_box.pack_start(self.body, True, True, 0)

        self.window.add(main_box)
        self.window.connect("destroy", self._on_destroy)

    def build_body(self, container):
        """Override in subclasses to build widget content."""
        lbl = Gtk.Label(label="Loading...")
        lbl.get_style_context().add_class("loading-text")
        container.pack_start(lbl, True, True, 0)

    def show(self):
        self.window.show_all()
        self.start_polling()

    def hide(self):
        self.stop_polling()
        self.window.hide()

    def start_polling(self):
        """Override to start data polling."""
        pass

    def stop_polling(self):
        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    @property
    def alive(self):
        """Check if widget window is still valid (not destroyed/hidden)."""
        return self.window and self.window.get_visible()

    def _on_close(self, btn):
        self.hide()
        self.engine.set_widget_enabled(self.WIDGET_ID, False)

    def _on_destroy(self, w):
        self.stop_polling()

    # Anchor-based drag, same pattern as gp-desktop-icons.py. The previous
    # incremental dx/dy approach drifted when the compositor coalesced
    # motion events on wlroots. With an anchor we recompute the absolute
    # position each motion event — stable, no accumulation.
    def _on_title_press(self, widget, event):
        if event.button == 1:
            self.dragging = True
            self._drag_anchor_x = event.x_root - self.pos_x
            self._drag_anchor_y = event.y_root - self.pos_y

    def _on_title_release(self, widget, event):
        if self.dragging:
            self.dragging = False
            self._save_position()

    def _on_title_motion(self, widget, event):
        if not self.dragging:
            return
        new_x = max(0, int(event.x_root - self._drag_anchor_x))
        new_y = max(0, int(event.y_root - self._drag_anchor_y))
        if new_x == self.pos_x and new_y == self.pos_y:
            return
        self.pos_x = new_x
        self.pos_y = new_y
        if USE_LAYER_SHELL:
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.LEFT, self.pos_x)
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.TOP, self.pos_y)
        else:
            self.window.move(self.pos_x, self.pos_y)

    def _save_position(self):
        wl = self.engine.layout["widgets"].setdefault(self.WIDGET_ID, {})
        wl["x"] = self.pos_x
        wl["y"] = self.pos_y
        self.engine.save()

    def set_keyboard_interactive(self, interactive):
        if USE_LAYER_SHELL:
            mode = GtkLayerShell.KeyboardMode.ON_DEMAND if interactive else GtkLayerShell.KeyboardMode.NONE
            GtkLayerShell.set_keyboard_mode(self.window, mode)


# ── Score Widget ─────────────────────────────────────────────────────

class ScoreWidget(WidgetWindow):
    WIDGET_ID = "score"
    TITLE = "Privacy Score"
    DEFAULT_W = 250
    DEFAULT_H = 300

    def build_body(self, container):
        self.score_value = 0
        self.breakdown = []

        # Cairo drawing area for arc gauge
        self.gauge = Gtk.DrawingArea()
        self.gauge.set_size_request(150, 150)
        self.gauge.connect("draw", self._draw_gauge)
        container.pack_start(self.gauge, False, False, 4)

        # Score number below gauge
        self.score_lbl = Gtk.Label(label="--")
        self.score_lbl.get_style_context().add_class("big-number")
        container.pack_start(self.score_lbl, False, False, 0)

        score_sub = Gtk.Label(label="PRIVACY SCORE")
        score_sub.get_style_context().add_class("score-label")
        container.pack_start(score_sub, False, False, 2)

        # Separator
        sep = Gtk.Separator()
        container.pack_start(sep, False, False, 6)

        # Breakdown list
        self.breakdown_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        container.pack_start(self.breakdown_box, True, True, 0)

    def _draw_gauge(self, widget, cr):
        alloc = widget.get_allocation()
        cx = alloc.width / 2
        cy = alloc.height / 2
        radius = min(cx, cy) - 10
        line_width = 8

        # Background arc
        cr.set_line_width(line_width)
        cr.set_source_rgba(0.2, 0.3, 0.2, 0.4)
        cr.arc(cx, cy, radius, 0.7 * math.pi, 2.3 * math.pi)
        cr.stroke()

        # Score arc
        score = max(0, min(100, self.score_value))
        if score >= 70:
            ar, ag, ab = self.engine.accent_rgb
            cr.set_source_rgba(ar, ag, ab, 0.9)  # accent color
        elif score >= 40:
            cr.set_source_rgba(1.0, 0.624, 0.224, 0.9)  # orange
        else:
            cr.set_source_rgba(1.0, 0.267, 0.267, 0.9)  # red

        sweep = 1.6 * math.pi  # total arc span
        score_angle = 0.7 * math.pi + (score / 100.0) * sweep
        cr.set_line_width(line_width)
        # LINE_CAP_ROUND = 1 in cairo enums
        if cairo:
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
        else:
            cr.set_line_cap(1)
        cr.arc(cx, cy, radius, 0.7 * math.pi, score_angle)
        cr.stroke()

    def start_polling(self):
        self._fetch_score()
        self.timer_id = GLib.timeout_add_seconds(10, self._fetch_score)

    def _fetch_score(self):
        data = read_status_cache()
        if data:
            self._update_score(data)
        else:
            threaded_fetch(f"{API_BASE}/api/status", self._update_score)
        return True  # keep timer alive

    def _update_score(self, data):
        if not data:
            return
        self.score_value = data.get("securityScore", 0)
        self.breakdown = data.get("scoreBreakdown", [])

        # Update label
        self.score_lbl.set_text(str(self.score_value))
        # Update label color class
        ctx = self.score_lbl.get_style_context()
        for cls in ["big-number", "big-number-orange", "big-number-red"]:
            ctx.remove_class(cls)
        if self.score_value >= 70:
            ctx.add_class("big-number")
        elif self.score_value >= 40:
            ctx.add_class("big-number-orange")
        else:
            ctx.add_class("big-number-red")

        # Redraw gauge
        self.gauge.queue_draw()

        # Update breakdown
        for child in self.breakdown_box.get_children():
            child.destroy()

        for item in self.breakdown:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            name = item.get("name", "?")
            label_text = item.get("label", "")
            val = item.get("value", 0)

            display = name
            if label_text:
                display = f"{name} ({label_text})"

            name_lbl = Gtk.Label(label=display)
            name_lbl.get_style_context().add_class("breakdown-name")
            name_lbl.set_halign(Gtk.Align.START)
            name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(name_lbl, True, True, 0)

            val_str = f"+{val}" if val >= 0 else str(val)
            val_lbl = Gtk.Label(label=val_str)
            cls = "breakdown-value-pos" if val >= 0 else "breakdown-value-neg"
            val_lbl.get_style_context().add_class(cls)
            val_lbl.set_halign(Gtk.Align.END)
            row.pack_end(val_lbl, False, False, 0)

            self.breakdown_box.pack_start(row, False, False, 0)

        self.breakdown_box.show_all()


# ── Mode Widget ──────────────────────────────────────────────────────

MODES = [
    ("isp",       "ISP",        "Normal internet passthrough"),
    ("zerotrust", "Zero Trust", "DNS locked, no VPN"),
    ("doublehop", "DoubleHop",  "WireGuard tunnel active"),
    ("zhop",      "Z-HOP",     "Max privacy, DNS + tunnel"),
]

class ModeWidget(WidgetWindow):
    WIDGET_ID = "mode"
    TITLE = "Mode"
    DEFAULT_W = 280
    DEFAULT_H = 200

    def build_body(self, container):
        self.current_mode = ""

        # Current mode display
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.indicator = Gtk.Label(label="\u25cf")
        self.indicator.get_style_context().add_class("mode-indicator")
        info_box.pack_start(self.indicator, False, False, 0)

        self.mode_lbl = Gtk.Label(label="Loading...")
        self.mode_lbl.get_style_context().add_class("mode-name")
        self.mode_lbl.set_halign(Gtk.Align.START)
        info_box.pack_start(self.mode_lbl, True, True, 0)
        container.pack_start(info_box, False, False, 4)

        self.desc_lbl = Gtk.Label(label="")
        self.desc_lbl.get_style_context().add_class("mode-desc")
        self.desc_lbl.set_halign(Gtk.Align.START)
        container.pack_start(self.desc_lbl, False, False, 0)

        sep = Gtk.Separator()
        container.pack_start(sep, False, False, 8)

        # Mode switch buttons
        btn_grid = Gtk.Grid()
        btn_grid.set_column_spacing(6)
        btn_grid.set_row_spacing(6)
        btn_grid.set_column_homogeneous(True)
        self.mode_buttons = {}
        for i, (mid, mname, mdesc) in enumerate(MODES):
            btn = Gtk.Button(label=mname)
            btn.get_style_context().add_class("mode-btn")
            btn.connect("clicked", self._on_mode_click, mid)
            btn_grid.attach(btn, i % 2, i // 2, 1, 1)
            self.mode_buttons[mid] = btn
        container.pack_start(btn_grid, False, False, 0)

    def start_polling(self):
        self._fetch_mode()
        self.timer_id = GLib.timeout_add_seconds(5, self._fetch_mode)

    def _fetch_mode(self):
        data = read_status_cache()
        if data:
            self._update_mode(data)
        else:
            threaded_fetch(f"{API_BASE}/api/status", self._update_mode)
        return True

    def _update_mode(self, data):
        if not data:
            return
        mode = data.get("activeMode", "unknown")
        self.current_mode = mode

        # Find display info
        display_name = mode
        desc = ""
        for mid, mname, mdesc in MODES:
            if mid == mode:
                display_name = mname
                desc = mdesc
                break

        self.mode_lbl.set_text(display_name.upper())
        self.desc_lbl.set_text(desc)

        # Color the indicator with accent color
        self.indicator.set_markup(f'<span foreground="{self.engine.accent_hex}">\u25cf</span>')

        # Update button styles
        for mid, btn in self.mode_buttons.items():
            ctx = btn.get_style_context()
            ctx.remove_class("mode-btn")
            ctx.remove_class("mode-btn-active")
            ctx.add_class("mode-btn-active" if mid == mode else "mode-btn")

    def _on_mode_click(self, btn, mode_id):
        if mode_id == self.current_mode:
            return
        # Set all buttons insensitive briefly
        for b in self.mode_buttons.values():
            b.set_sensitive(False)

        def _restore(result):
            if not self.alive:
                return False
            for b in self.mode_buttons.values():
                b.set_sensitive(True)
            if result:
                self._fetch_mode()
                # Non-ISP modes have 60s rollback timer — auto-confirm
                if mode_id != "isp":
                    GLib.timeout_add_seconds(2, self._auto_confirm)

        threaded_fetch(
            f"{API_BASE}/api/mode",
            _restore,
            post_data={"mode": mode_id},
            widget=self
        )

    def _auto_confirm(self):
        """Confirm mode switch to cancel the 60s rollback timer."""
        threaded_fetch(f"{API_BASE}/api/mode/confirm", lambda r: None, widget=self)
        return False  # one-shot timer


# ── Tunnel Widget ────────────────────────────────────────────────────

class TunnelWidget(WidgetWindow):
    WIDGET_ID = "tunnel"
    TITLE = "Tunnels"
    DEFAULT_W = 280
    DEFAULT_H = 220

    def build_body(self, container):
        self.rows = {}
        for tid, tname in [("wg0", "WG0 Control"), ("wg1", "WG1 Data"), ("tailscale", "Tailscale")]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            name_lbl = Gtk.Label(label=tname)
            name_lbl.get_style_context().add_class("tunnel-label")
            name_lbl.set_halign(Gtk.Align.START)
            name_lbl.set_size_request(110, -1)
            row.pack_start(name_lbl, False, False, 0)

            status_lbl = Gtk.Label(label="...")
            status_lbl.get_style_context().add_class("tunnel-down")
            status_lbl.set_halign(Gtk.Align.END)
            row.pack_end(status_lbl, False, False, 0)

            container.pack_start(row, False, False, 4)
            self.rows[tid] = status_lbl

        sep = Gtk.Separator()
        container.pack_start(sep, False, False, 8)

        # Data transferred
        self.data_lbl = Gtk.Label(label="")
        self.data_lbl.get_style_context().add_class("tunnel-data")
        self.data_lbl.set_halign(Gtk.Align.START)
        self.data_lbl.set_line_wrap(True)
        container.pack_start(self.data_lbl, False, False, 0)

        # Exit IP
        self.ip_lbl = Gtk.Label(label="")
        self.ip_lbl.get_style_context().add_class("dim-text")
        self.ip_lbl.set_halign(Gtk.Align.START)
        container.pack_start(self.ip_lbl, False, False, 4)

    def start_polling(self):
        self._fetch_tunnels()
        self.timer_id = GLib.timeout_add_seconds(10, self._fetch_tunnels)

    def _fetch_tunnels(self):
        data = read_status_cache()
        if data:
            self._update_tunnels(data)
        else:
            threaded_fetch(f"{API_BASE}/api/status", self._update_tunnels)
        return True

    def _update_tunnels(self, data):
        if not data:
            return
        tunnels = data.get("tunnels", {})
        for tid, lbl in self.rows.items():
            status = tunnels.get(tid, "down")
            is_up = status == "up"
            lbl.set_text("\u25b2 UP" if is_up else "\u25bc DOWN")
            ctx = lbl.get_style_context()
            ctx.remove_class("tunnel-up")
            ctx.remove_class("tunnel-down")
            ctx.add_class("tunnel-up" if is_up else "tunnel-down")

        ip = data.get("ip", "")
        if ip:
            self.ip_lbl.set_text(f"Exit IP: {ip}")
        else:
            self.ip_lbl.set_text("")

        # Try to get WG transfer data from a thread
        def _get_wg():
            try:
                import subprocess
                out = subprocess.check_output(
                    ["sudo", "wg", "show", "all", "transfer"],
                    timeout=3, stderr=subprocess.DEVNULL
                ).decode().strip()
                return out
            except Exception:
                return None

        def _show_wg(result):
            if not result:
                self.data_lbl.set_text("")
                return
            lines = []
            for line in result.strip().split('\n'):
                parts = line.split('\t')
                if len(parts) >= 3:
                    iface = parts[0] if len(parts) > 3 else "wg"
                    rx = int(parts[-2]) if parts[-2].isdigit() else 0
                    tx = int(parts[-1]) if parts[-1].isdigit() else 0
                    rx_mb = rx / (1024*1024)
                    tx_mb = tx / (1024*1024)
                    lines.append(f"\u2193{rx_mb:.1f}MB \u2191{tx_mb:.1f}MB")
            self.data_lbl.set_text("  ".join(lines) if lines else "")

        t = threading.Thread(target=lambda: GLib.idle_add(_show_wg, _get_wg()), daemon=True)
        t.start()


# ── Ads Widget ───────────────────────────────────────────────────────

class AdsWidget(WidgetWindow):
    WIDGET_ID = "ads"
    TITLE = "Ads Blocked"
    DEFAULT_W = 250
    DEFAULT_H = 180

    def build_body(self, container):
        container.set_valign(Gtk.Align.CENTER)

        self.session_lbl = Gtk.Label(label="0")
        self.session_lbl.get_style_context().add_class("ads-big")
        container.pack_start(self.session_lbl, False, False, 0)

        sub = Gtk.Label(label="BLOCKED THIS SESSION")
        sub.get_style_context().add_class("ads-label")
        container.pack_start(sub, False, False, 2)

        sep = Gtk.Separator()
        container.pack_start(sep, False, False, 8)

        self.alltime_lbl = Gtk.Label(label="All-time: --")
        self.alltime_lbl.get_style_context().add_class("ads-alltime")
        container.pack_start(self.alltime_lbl, False, False, 0)

    def start_polling(self):
        self._fetch_ads()
        self.timer_id = GLib.timeout_add_seconds(15, self._fetch_ads)

    def _fetch_ads(self):
        data = read_status_cache()
        if data:
            self._update_ads(data)
        else:
            threaded_fetch(f"{API_BASE}/api/status", self._update_ads)
        return True

    def _update_ads(self, data):
        if not data:
            return
        session = data.get("adsBlocked", 0)
        alltime = data.get("adsBlockedAllTime", 0)
        self.session_lbl.set_text(f"{session:,}")
        self.alltime_lbl.set_text(f"All-time: {alltime:,}")


# ── Clients Widget ───────────────────────────────────────────────────

class ClientsWidget(WidgetWindow):
    WIDGET_ID = "clients"
    TITLE = "Connected Clients"
    DEFAULT_W = 280
    DEFAULT_H = 300

    def build_body(self, container):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.client_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll.add(self.client_box)
        container.pack_start(scroll, True, True, 0)

        self.count_lbl = Gtk.Label(label="0 devices")
        self.count_lbl.get_style_context().add_class("dim-text")
        self.count_lbl.set_halign(Gtk.Align.START)
        container.pack_start(self.count_lbl, False, False, 4)

    def start_polling(self):
        self._fetch_clients()
        self.timer_id = GLib.timeout_add_seconds(15, self._fetch_clients)

    def _fetch_clients(self):
        threaded_fetch(f"{API_BASE}/api/arsenal/clients", self._update_clients)
        return True

    def _update_clients(self, data):
        # Clear existing
        for child in self.client_box.get_children():
            child.destroy()

        clients = []
        if isinstance(data, list):
            clients = data
        elif isinstance(data, dict):
            clients = data.get("clients", data.get("devices", []))

        if not clients:
            # Try dnsmasq leases as fallback
            self._load_leases()
            return

        for client in clients:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            dot = Gtk.Label(label="\u25cf")
            online = client.get("online", client.get("active", True))
            dot.get_style_context().add_class("client-dot-online" if online else "client-dot-offline")
            row.pack_start(dot, False, False, 0)

            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            hostname = client.get("hostname", client.get("name", "Unknown"))
            h_lbl = Gtk.Label(label=hostname)
            h_lbl.get_style_context().add_class("client-hostname")
            h_lbl.set_halign(Gtk.Align.START)
            h_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            info.pack_start(h_lbl, False, False, 0)

            ip = client.get("ip", client.get("address", ""))
            if ip:
                ip_lbl = Gtk.Label(label=ip)
                ip_lbl.get_style_context().add_class("client-ip")
                ip_lbl.set_halign(Gtk.Align.START)
                info.pack_start(ip_lbl, False, False, 0)

            row.pack_start(info, True, True, 0)
            self.client_box.pack_start(row, False, False, 2)

        self.count_lbl.set_text(f"{len(clients)} device{'s' if len(clients) != 1 else ''}")
        self.client_box.show_all()

    def _load_leases(self):
        """Fallback: parse DHCP leases via canonical gp-leases helper."""
        def _read():
            clients = []
            try:
                import subprocess
                r = subprocess.run(["sudo", "-n", "gp-leases"], capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        clients.append({
                            "ip": parts[2],
                            "hostname": parts[3] if parts[3] != "*" else parts[1][:8],
                            "online": True,
                        })
            except Exception:
                pass
            return clients

        def _show(clients):
            if clients:
                # Render directly instead of calling _update_clients to avoid
                # infinite recursion (_update_clients -> _load_leases -> _show -> _update_clients)
                for child in self.client_box.get_children():
                    child.destroy()
                for client in clients:
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    dot = Gtk.Label(label="\u25cf")
                    dot.get_style_context().add_class("client-dot-online")
                    row.pack_start(dot, False, False, 0)
                    info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    h_lbl = Gtk.Label(label=client.get("hostname", "Unknown"))
                    h_lbl.get_style_context().add_class("client-hostname")
                    h_lbl.set_halign(Gtk.Align.START)
                    h_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                    info.pack_start(h_lbl, False, False, 0)
                    ip = client.get("ip", "")
                    if ip:
                        ip_lbl = Gtk.Label(label=ip)
                        ip_lbl.get_style_context().add_class("client-ip")
                        ip_lbl.set_halign(Gtk.Align.START)
                        info.pack_start(ip_lbl, False, False, 0)
                    row.pack_start(info, True, True, 0)
                    self.client_box.pack_start(row, False, False, 2)
                self.count_lbl.set_text(f"{len(clients)} device{'s' if len(clients) != 1 else ''}")
                self.client_box.show_all()

        t = threading.Thread(target=lambda: GLib.idle_add(_show, _read()), daemon=True)
        t.start()


# ── Chamber Widget ───────────────────────────────────────────────────

class ChamberWidget(WidgetWindow):
    WIDGET_ID = "chamber"
    TITLE = "Chamber"
    DEFAULT_W = 380
    DEFAULT_H = 500

    def build_body(self, container):
        self.session_token = None
        self.last_msg_id = None

        # Message area (scrollable)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("chamber-messages")
        scroll.set_vexpand(True)
        self.msg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.msg_box.set_margin_start(6)
        self.msg_box.set_margin_end(6)
        self.msg_box.set_margin_top(6)
        self.msg_box.set_margin_bottom(6)
        scroll.add(self.msg_box)
        self.scroll = scroll
        container.pack_start(scroll, True, True, 0)

        # Input row
        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        input_row.set_margin_top(6)

        self.entry = Gtk.Entry()
        self.entry.get_style_context().add_class("chamber-input")
        self.entry.set_placeholder_text("Type a message...")
        self.entry.connect("activate", self._on_send)
        self.entry.connect("focus-in-event", self._on_focus_in)
        self.entry.connect("focus-out-event", self._on_focus_out)
        input_row.pack_start(self.entry, True, True, 0)

        send_btn = Gtk.Button(label="\u27a4")
        send_btn.get_style_context().add_class("chamber-send")
        send_btn.connect("clicked", self._on_send)
        input_row.pack_end(send_btn, False, False, 0)

        container.pack_start(input_row, False, False, 0)

    def _on_focus_in(self, widget, event):
        self.set_keyboard_interactive(True)

    def _on_focus_out(self, widget, event):
        self.set_keyboard_interactive(False)

    def show(self):
        super().show()
        # Auto-login
        self._do_login()

    def _do_login(self):
        def _login():
            result = http_post_json(f"{CHAMBER_BASE}/api/login", {
                "username": "desktop-user",
                "role": "human"
            })
            if result and result.get("token"):
                GLib.idle_add(lambda: setattr(self, 'session_token', result["token"]))

        t = threading.Thread(target=_login, daemon=True)
        t.start()

    def start_polling(self):
        self._fetch_messages()
        self.timer_id = GLib.timeout_add_seconds(3, self._fetch_messages)

    def _fetch_messages(self):
        threaded_fetch(f"{CHAMBER_BASE}/api/messages?room=general", self._update_messages)
        return True

    def _update_messages(self, data):
        if not data:
            return
        messages = data.get("messages", [])
        if not messages:
            return

        # Check if we have new messages
        newest_id = messages[-1].get("id", "")
        if newest_id == self.last_msg_id:
            return
        self.last_msg_id = newest_id

        # Clear and rebuild
        for child in self.msg_box.get_children():
            child.destroy()

        for msg in messages[-50:]:  # last 50 messages
            msg_widget = self._build_message(msg)
            self.msg_box.pack_start(msg_widget, False, False, 0)

        self.msg_box.show_all()

        # Scroll to bottom after layout completes
        def _scroll_after(widget, alloc):
            adj = self.scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            widget.disconnect_by_func(_scroll_after)
        self.msg_box.connect("size-allocate", _scroll_after)

    def _build_message(self, msg):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)

        # Header: username + timestamp
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        user_lbl = Gtk.Label(label=msg.get("username", "?"))
        user_lbl.get_style_context().add_class("chamber-msg-user")
        user_lbl.set_halign(Gtk.Align.START)
        header.pack_start(user_lbl, False, False, 0)

        ts = msg.get("ts", 0)
        if ts:
            try:
                dt = time.strftime("%H:%M", time.localtime(ts / 1000))
            except (ValueError, OSError):
                dt = ""
            if dt:
                time_lbl = Gtk.Label(label=dt)
                time_lbl.get_style_context().add_class("chamber-msg-time")
                time_lbl.set_halign(Gtk.Align.START)
                header.pack_start(time_lbl, False, False, 0)

        box.pack_start(header, False, False, 0)

        # Message text
        text_lbl = Gtk.Label(label=msg.get("text", ""))
        text_lbl.get_style_context().add_class("chamber-msg-text")
        text_lbl.set_halign(Gtk.Align.START)
        text_lbl.set_line_wrap(True)
        text_lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text_lbl.set_max_width_chars(45)
        text_lbl.set_xalign(0)
        box.pack_start(text_lbl, False, False, 0)

        return box

    def _scroll_bottom(self):
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _on_send(self, widget):
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")

        data = {
            "username": "desktop-user",
            "text": text,
            "room": "general",
        }
        if self.session_token:
            data["token"] = self.session_token

        def _after(result):
            # Refresh messages after sending
            self._fetch_messages()

        threaded_fetch(f"{CHAMBER_BASE}/api/messages", _after, post_data=data)


# ── Theme Picker Widget ──────────────────────────────────────────────

# NOTE: Ghost Green hex is split to prevent gp-theme sed from corrupting it
_GG = "#" + "39" + "ff" + "8f"  # Ghost Green — must survive sed #ff9944→#new

# Theme presets — 8 official colors
THEME_PRESETS = [
    (_GG,       "Ghost Green"),
    ("#00d4ff", "Cyber Blue"),
    ("#ffbf00", "Amber"),
    ("#e040fb", "Neon Purple"),
    ("#ff4444", "Red Alert"),
    ("#ffd700", "Gold"),
    ("#ffffff", "White"),
    # Rainbow is handled via RGB cycle toggle, not a static preset
]


def _hsl_to_hex(h, s, l):
    """Convert HSL (h=0-360, s/l=0-1) to #rrggbb hex string."""
    h = h % 360
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:    r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return "#{:02x}{:02x}{:02x}".format(
        int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


class ThemeWidget(WidgetWindow):
    WIDGET_ID = "theme"
    TITLE = "Theme"
    DEFAULT_W = 260
    DEFAULT_H = 280

    def build_body(self, container):
        container.set_margin_start(8)
        container.set_margin_end(8)
        container.set_margin_bottom(8)

        # Current color label + hex display
        self.current_lbl = Gtk.Label()
        self.current_lbl.get_style_context().add_class("dim-text")
        self.current_lbl.set_halign(Gtk.Align.START)
        container.pack_start(self.current_lbl, False, False, 2)

        # ── PRESETS section label ──
        pack_label = Gtk.Label()
        pack_label.set_markup(
            '<span font_family="monospace" font_size="x-small" '
            'letter_spacing="2048" foreground="#603a19">PRESETS</span>')
        pack_label.set_halign(Gtk.Align.START)
        container.pack_start(pack_label, False, False, 2)

        # Preset grid (4x2 with dot + name)
        pack_grid = Gtk.Grid()
        pack_grid.set_column_spacing(4)
        pack_grid.set_row_spacing(4)
        pack_grid.set_column_homogeneous(True)

        self.pack_buttons = []
        for i, (hex_color, name) in enumerate(THEME_PRESETS):
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text(name)

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            hbox.set_halign(Gtk.Align.CENTER)

            # Color dot
            dot = Gtk.DrawingArea()
            dot.set_size_request(12, 12)
            dot_color = hex_color
            dot.connect("draw", self._draw_dot, dot_color)
            hbox.pack_start(dot, False, False, 0)

            # Preset name
            lbl = Gtk.Label(label=name)
            lbl.set_halign(Gtk.Align.START)
            hbox.pack_start(lbl, False, False, 0)

            btn.add(hbox)

            btn_css = Gtk.CssProvider()
            btn.get_style_context().add_provider(btn_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            btn.connect("clicked", self._on_color_pick, hex_color, name)

            col = i % 4
            row = i // 4
            pack_grid.attach(btn, col, row, 1, 1)
            self.pack_buttons.append((btn, hex_color, btn_css, name))

        container.pack_start(pack_grid, False, False, 0)

        # ── Custom color + RGB cycle row ──
        custom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        custom_row.set_halign(Gtk.Align.CENTER)

        # GTK color chooser button
        self.color_btn = Gtk.ColorButton()
        self.color_btn.set_size_request(28, 28)
        self.color_btn.set_title("Custom Color")
        try:
            self.color_btn.set_use_alpha(False)
        except Exception:
            pass  # deprecated in newer GTK, not critical
        current_rgba = Gdk.RGBA()
        current_rgba.parse(self.engine.accent_hex)
        self.color_btn.set_rgba(current_rgba)
        self.color_btn.connect("color-set", self._on_custom_color)
        color_btn_css = Gtk.CssProvider()
        color_btn_css.load_from_data(b"""
            colorbutton button {
                border-radius: 50%;
                min-width: 24px; min-height: 24px;
                padding: 0;
            }
        """)
        self.color_btn.get_style_context().add_provider(
            color_btn_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        custom_row.pack_start(self.color_btn, False, False, 0)

        # Hex label
        self.hex_lbl = Gtk.Label()
        self.hex_lbl.get_style_context().add_class("accent-text")
        custom_row.pack_start(self.hex_lbl, False, False, 0)

        # RGB Cycle button (disabled — too CPU-heavy for Pi hardware)
        self.rgb_btn = Gtk.Button()
        self.rgb_btn.set_tooltip_text("RGB Cycle (disabled — too CPU-intensive for Pi)")
        self.rgb_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.rgb_btn.set_size_request(28, 28)
        self.rgb_btn.set_sensitive(False)
        self.rgb_btn.set_opacity(0.35)

        # Rainbow dot via DrawingArea
        self.rgb_dot = Gtk.DrawingArea()
        self.rgb_dot.set_size_request(20, 20)
        self._rainbow_hue = 0.0
        self.rgb_dot.connect("draw", self._draw_rainbow_dot)
        self.rgb_btn.add(self.rgb_dot)

        rgb_css = Gtk.CssProvider()
        rgb_css.load_from_data(b"""
            button {
                background: transparent;
                border: 2px solid rgba(255,255,255,0.2);
                border-radius: 50%;
                min-width: 24px; min-height: 24px;
                padding: 0;
            }
            button:hover {
                border-color: rgba(255,255,255,0.6);
            }
        """)
        self.rgb_btn.get_style_context().add_provider(
            rgb_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.rgb_btn.connect("clicked", self._on_toggle_rainbow)
        self._rgb_css = rgb_css
        custom_row.pack_start(self.rgb_btn, False, False, 0)

        container.pack_start(custom_row, False, False, 2)

        self._refresh_current()

    def _draw_dot(self, widget, cr, hex_color):
        """Draw a colored circle for theme pack buttons."""
        alloc = widget.get_allocation()
        cx, cy = alloc.width / 2, alloc.height / 2
        radius = min(cx, cy) - 1
        r, g, b = hex_to_rgb(hex_color)
        cr.set_source_rgb(r, g, b)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()

    def _draw_rainbow_dot(self, widget, cr):
        """Draw a rainbow gradient circle for the RGB button."""
        alloc = widget.get_allocation()
        cx, cy = alloc.width / 2, alloc.height / 2
        radius = min(cx, cy) - 1
        # Draw 6 color segments
        for i in range(6):
            angle_start = (i / 6) * 2 * math.pi - math.pi / 2
            angle_end = ((i + 1) / 6) * 2 * math.pi - math.pi / 2
            r, g, b = hex_to_rgb(_hsl_to_hex(i * 60, 1.0, 0.5))
            cr.set_source_rgb(r, g, b)
            cr.move_to(cx, cy)
            cr.arc(cx, cy, radius, angle_start, angle_end)
            cr.close_path()
            cr.fill()

    def _refresh_current(self):
        current = self.engine.accent_hex
        dim = _derive_colors(current)["dim"]
        name = "Custom"

        # Check presets
        for hex_color, preset_name in THEME_PRESETS:
            if hex_color.lower() == current.lower():
                name = preset_name
                break

        self.current_lbl.set_markup(
            f'<span font_family="monospace" font_size="small" foreground="{dim}">'
            f'Active: </span>'
            f'<span font_family="monospace" font_size="small" foreground="{current}" '
            f'font_weight="bold">{name}</span>')

        self.hex_lbl.set_markup(
            f'<span font_family="monospace" font_size="small" foreground="{current}" '
            f'font_weight="bold">{current.upper()}</span>')

        # Update pack button styles
        for btn, hex_color, btn_css, pname in self.pack_buttons:
            r, g, b = hex_to_rgb255(hex_color)
            is_active = hex_color.lower() == current.lower()
            border = "rgba(255,255,255,0.8)" if is_active else "rgba(128,128,128,0.2)"
            bg = f"rgba({r},{g},{b},0.12)" if is_active else "transparent"
            text_color = f"#{r:02x}{g:02x}{b:02x}" if is_active else dim
            btn_css.load_from_data(f"""
                button {{
                    background-color: {bg};
                    border: 1px solid {border};
                    border-radius: 6px;
                    color: {text_color};
                    font-family: monospace;
                    font-size: 9px;
                    padding: 2px 4px;
                    min-height: 24px;
                }}
                button:hover {{
                    background-color: rgba({r},{g},{b},0.15);
                    border-color: rgba({r},{g},{b},0.6);
                    color: #{r:02x}{g:02x}{b:02x};
                }}
            """.encode())

        # Update color chooser button
        rgba = Gdk.RGBA()
        rgba.parse(current)
        self.color_btn.set_rgba(rgba)

    def _on_color_pick(self, btn, hex_color, name):
        """Apply a preset color via gp-theme CLI."""
        self._apply_theme_async(hex_color)

    def _on_custom_color(self, color_button):
        """Handle custom color chooser."""
        rgba = color_button.get_rgba()
        hex_color = "#{:02x}{:02x}{:02x}".format(
            int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))
        self._apply_theme_async(hex_color)

    def _apply_theme_async(self, hex_color):
        """Apply theme in background thread via gp-theme CLI."""
        def do_apply():
            try:
                bare = hex_color.lstrip("#")
                gp_theme = os.path.expanduser("~/.local/bin/gp-theme")
                result = subprocess.run(
                    [gp_theme, bare],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode != 0:
                    print(f"[theme] gp-theme failed: {result.stderr}", file=sys.stderr)
                else:
                    print(f"[theme] Applied {hex_color} via gp-theme", file=sys.stderr)
            except Exception as e:
                print(f"[theme] Error running gp-theme: {e}", file=sys.stderr)
            # Update engine state immediately so UI reflects the change
            def _sync_engine():
                new_color = read_theme_color()
                self.engine.accent_hex = new_color
                self.engine.accent_rgb = hex_to_rgb(new_color)
                self.engine._setup_css()
                for wid, widget in self.engine.widgets.items():
                    if hasattr(widget, 'gauge'):
                        widget.gauge.queue_draw()
                self._refresh_current()
            GLib.idle_add(_sync_engine)
        threading.Thread(target=do_apply, daemon=True).start()

    def _on_toggle_rainbow(self, _btn):
        """Rainbow/RGB cycle is disabled on Pi — too CPU-heavy for embedded hardware.
        Show a tooltip explanation instead."""
        pass

    def start_polling(self):
        self._check_theme()
        self.timer_id = GLib.timeout_add_seconds(3, self._check_theme)

    def stop_polling(self):
        super().stop_polling()

    def _check_theme(self):
        self._refresh_current()
        return True


# ── Widget Engine ────────────────────────────────────────────────────

WIDGET_CLASSES = {
    "score": ScoreWidget,
    "ads": AdsWidget,
    "theme": ThemeWidget,
}
# Trimmed 2026-04-21 — removed mode/tunnel/clients/chamber (redundant with
# waybar + right-click menu + Anchor + Crew Manifest). Classes kept in
# this file as dead code; can be re-added to the dict if ever wanted.


class WidgetEngine:
    """Main engine that manages all widget instances."""

    def __init__(self):
        self.layout = load_layout()
        self.widgets = {}
        # Theme color state
        self.accent_hex = read_theme_color()
        self.accent_rgb = hex_to_rgb(self.accent_hex)
        self.css_provider = None
        self._setup_css()
        self._create_widgets()
        self._show_enabled()
        # Poll theme.json for color changes every 3 seconds
        self._theme_timer = GLib.timeout_add_seconds(3, self._poll_theme)

    def _setup_css(self):
        if self.css_provider:
            # Remove old provider before adding new one
            screen = Gdk.Screen.get_default()
            if screen:
                Gtk.StyleContext.remove_provider_for_screen(screen, self.css_provider)
        self.css_provider = Gtk.CssProvider()
        css_text = generate_css(self.accent_hex)
        self.css_provider.load_from_data(css_text.encode())
        screen = Gdk.Screen.get_default()
        if screen is None:
            print("[widgets] No display available, skipping CSS setup", file=sys.stderr)
            return
        Gtk.StyleContext.add_provider_for_screen(
            screen, self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _poll_theme(self):
        """Check theme.json for color changes and apply dynamically."""
        new_color = read_theme_color()
        if new_color != self.accent_hex:
            print(f"[theme] Color changed: {self.accent_hex} -> {new_color}", file=sys.stderr)
            self.accent_hex = new_color
            self.accent_rgb = hex_to_rgb(new_color)
            # Reload CSS with new color
            self._setup_css()
            # Redraw all Cairo-based widgets
            for wid, widget in self.widgets.items():
                if hasattr(widget, 'gauge'):
                    widget.gauge.queue_draw()
                # Re-trigger data update to refresh markup colors
                if hasattr(widget, '_fetch_mode'):
                    widget._fetch_mode()
                if hasattr(widget, '_fetch_score'):
                    widget._fetch_score()
        return True  # keep timer alive

    def _create_widgets(self):
        """No-op: widgets are created lazily on enable."""
        pass

    def _show_enabled(self):
        """Instantiate and show every widget whose layout entry is enabled."""
        for wid in WIDGET_CLASSES:
            cfg = self.layout["widgets"].get(wid, {})
            if cfg.get("enabled", False):
                self._spawn(wid)

    def _spawn(self, wid):
        """Create and show a widget instance. No-op if already present."""
        if wid in self.widgets:
            return self.widgets[wid]
        cls = WIDGET_CLASSES.get(wid)
        if cls is None:
            return None
        widget = cls(self)
        self.widgets[wid] = widget
        widget.show()
        return widget

    def _kill(self, wid):
        """Destroy a widget instance and remove it from the engine. No-op if absent."""
        widget = self.widgets.pop(wid, None)
        if widget is None:
            return
        try:
            widget.stop_polling()
        except Exception:
            pass
        try:
            widget.window.destroy()
        except Exception:
            pass

    def set_widget_enabled(self, wid, enabled):
        """Enable or disable a widget — authoritative single-source state change."""
        self.layout["widgets"].setdefault(wid, {})["enabled"] = enabled
        self.save()
        if enabled:
            self._spawn(wid)
        else:
            self._kill(wid)

    def reload_layout(self):
        """Reload layout from disk (called via SIGUSR2 from Library app).

        Reconciles engine state against disk state without diffing: every widget
        whose disk entry is enabled must exist and be shown; every other widget
        must not exist. This is deliberately idempotent — rapid toggles and
        coalesced signals cannot leave stale phantoms.
        """
        self.layout = load_layout()

        for wid in WIDGET_CLASSES:
            cfg = self.layout["widgets"].get(wid, {})
            enabled = cfg.get("enabled", False)

            if enabled:
                widget = self._spawn(wid)
                if widget is None:
                    continue
                widget.pos_x = cfg.get("x", 50)
                widget.pos_y = cfg.get("y", 50)
                if USE_LAYER_SHELL:
                    GtkLayerShell.set_margin(widget.window, GtkLayerShell.Edge.LEFT, widget.pos_x)
                    GtkLayerShell.set_margin(widget.window, GtkLayerShell.Edge.TOP, widget.pos_y)
                else:
                    widget.window.move(widget.pos_x, widget.pos_y)
            else:
                self._kill(wid)

        return True  # keep GLib signal handler alive

    def save(self):
        save_layout(self.layout)

    def shutdown(self):
        """Clean shutdown of all widgets."""
        if self._theme_timer:
            GLib.source_remove(self._theme_timer)
            self._theme_timer = None
        for widget in self.widgets.values():
            widget.stop_polling()
            widget.window.destroy()
        Gtk.main_quit()


# ── Main ─────────────────────────────────────────────────────────────

def acquire_lock():
    """Ensure single instance via lock file."""
    try:
        fd = open(LOCK_FILE, 'w')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        print("[widgets] Another instance is already running.", file=sys.stderr)
        sys.exit(1)


def write_pid():
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def main():
    lock_fd = acquire_lock()
    write_pid()

    engine = WidgetEngine()

    # SIGUSR2 = reload layout from disk (sent by Widget Library app)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, engine.reload_layout)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, engine.shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, engine.shutdown)

    try:
        Gtk.main()
    finally:
        try:
            os.unlink(LOCK_FILE)
            os.unlink(PID_FILE)
        except OSError:
            pass
        if lock_fd:
            lock_fd.close()


if __name__ == "__main__":
    main()
