#!/usr/bin/env python3
"""gp-bulkhead — Bulkhead: Visual Firewall Builder for Phantom OS"""
import sys, os
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import json
import re
import subprocess
import time
import threading

# ── Plain-English Rule Translations ─────────────────────────────────

EXPR_TRANSLATIONS = [
    (r'iifname\s+"wlan0"',           "From WiFi clients"),
    (r'iifname\s+"eth0"',            "From WAN"),
    (r'iifname\s+"wg0"',             "From control tunnel"),
    (r'iifname\s+"wg1"',             "From VPN tunnel"),
    (r'iifname\s+"tailscale0"',      "From Tailscale"),
    (r'iifname\s+"lo"',              "From loopback"),
    (r'oifname\s+"wlan0"',           "Going to WiFi clients"),
    (r'oifname\s+"eth0"',            "Going to WAN"),
    (r'oifname\s+"wg0"',             "Going through control tunnel"),
    (r'oifname\s+"wg1"',             "Going through VPN tunnel"),
    (r'oifname\s+"tailscale0"',      "Going through Tailscale"),
    (r'tcp\s+dport\s+443',           "HTTPS traffic (port 443)"),
    (r'tcp\s+dport\s+80',            "HTTP traffic (port 80)"),
    (r'tcp\s+dport\s+22',            "SSH access (port 22)"),
    (r'tcp\s+dport\s+53',            "DNS over TCP"),
    (r'tcp\s+dport\s+4200',          "GhostPort dashboard (port 4200)"),
    (r'tcp\s+dport\s+4201',          "GhostPort HTTP (port 4201)"),
    (r'tcp\s+dport\s+853',           "DNS-over-TLS (port 853)"),
    (r'tcp\s+dport\s+5053',          "Local DNS proxy (port 5053)"),
    (r'udp\s+dport\s+53',            "DNS queries"),
    (r'udp\s+dport\s+41641',         "Tailscale management"),
    (r'udp\s+dport\s+51820',         "WireGuard tunnel"),
    (r'ct\s+state\s+established,related',  "Replies to your requests"),
    (r'ct\s+state\s+established',    "Established connections"),
    (r'ct\s+state\s+related',        "Related connections"),
    (r'ct\s+state\s+new',            "New connections"),
    (r'ct\s+state\s+invalid',        "Invalid packets"),
    (r'ip\s+saddr\s+192\.168\.50\.0/24',   "From GhostPort clients"),
    (r'ip\s+saddr\s+10\.66\.66\.0/24',     "From control plane"),
    (r'ip\s+saddr\s+10\.66\.67\.0/24',     "From data plane"),
    (r'ip\s+saddr\s+100\.64\.0\.0/10',     "From Tailscale network"),
    (r'ip\s+daddr\s+192\.168\.50\.0/24',   "To GhostPort clients"),
    (r'ip\s+daddr\s+10\.66\.67\.1',        "To VPN DNS server"),
    (r'ip\s+daddr\s+100\.100\.100\.100',   "To Tailscale MagicDNS"),
    (r'ip\s+daddr\s+127\.0\.0\.1',         "To localhost"),
    (r'masquerade',                  "NAT: hide source IP"),
    (r'log\s+prefix\s+"[^"]*DROP[^"]*"',   "Log and drop"),
    (r'log\s+prefix',               "Log packet"),
    (r'counter',                     "Count packets"),
    (r'meta\s+l4proto\s+icmp',       "ICMP (ping)"),
    (r'icmp\s+type\s+echo-request',  "Ping request"),
    (r'fib\s+daddr\s+type\s+local',  "Traffic destined for this device"),
]

ACTION_MAP = {
    "accept": "ALLOWED",
    "drop":   "BLOCKED",
    "reject": "REJECTED",
    "masquerade": "NAT: hide source IP",
    "redirect": "Redirect",
    "log":    "Log only",
    "set":    "Modify packet",
    "return": "Return to caller",
    "jump":   "Jump to chain",
    "goto":   "Goto chain",
}

# Rules containing these patterns are protected from deletion
# Each tuple: (regex_pattern, description)
PROTECTED_RULES = [
    (r'ct\s+state\s+(established|related)',  "connection tracking"),
    (r'(tcp|udp)\s+dport\s+(\{[^}]*\b4200\b[^}]*\}|4200\b)', "GhostPort dashboard"),
    (r'(tcp|udp)\s+dport\s+(\{[^}]*\b22\b[^}]*\}|22\b)',     "SSH access"),
    (r'udp\s+dport\s+(\{[^}]*\b41641\b[^}]*\}|41641\b)',     "Tailscale management"),
    (r'iifname\s+("tailscale0"|\{[^}]*"tailscale0"[^}]*\})', "Tailscale interface"),
    (r'oifname\s+("tailscale0"|\{[^}]*"tailscale0"[^}]*\})', "Tailscale interface"),
    (r'iifname\s+("lo"|\{[^}]*"lo"[^}]*\})',                 "loopback interface"),
]


def translate_rule(expr):
    """Convert an nftables rule expression to plain English."""
    parts = []
    remaining = expr.strip()

    for pattern, desc in EXPR_TRANSLATIONS:
        if re.search(pattern, remaining):
            parts.append(desc)

    # Catch generic port references not covered above
    for m in re.finditer(r'(tcp|udp)\s+dport\s+(\d+)', remaining):
        proto, port = m.group(1).upper(), m.group(2)
        generic = f"{proto} port {port}"
        # Skip if we already matched a named translation for this port
        if not any(f"port {port})" in p or f"port {port})" in p for p in parts):
            if generic not in " ".join(parts):
                parts.append(f"{proto} port {port}")

    # Handle nft set syntax: dport { 53, 67 }
    for m in re.finditer(r'(tcp|udp)\s+dport\s+\{([^}]+)\}', remaining):
        proto = m.group(1).upper()
        ports = [p.strip() for p in m.group(2).split(",")]
        parts.append(f"{proto} ports {', '.join(ports)}")

    # Handle interface sets: iifname { "lo", "wlan0" }
    for m in re.finditer(r'(iifname|oifname)\s+\{([^}]+)\}', remaining):
        direction = "From" if m.group(1) == "iifname" else "To"
        ifaces = [i.strip().strip('"') for i in m.group(2).split(",")]
        parts.append(f"{direction} {'/'.join(ifaces)}")

    # Handle redirect
    m_redir = re.search(r'redirect\s+to\s+:(\d+)', remaining)
    if m_redir:
        parts.append(f"Redirect to port {m_redir.group(1)}")
    elif "redirect" in remaining:
        parts.append("Redirect")

    if not parts:
        # Fallback: return a shortened version of the raw expression
        short = remaining
        # Strip counter / comment noise
        short = re.sub(r'counter\s+packets\s+\d+\s+bytes\s+\d+', 'counter', short)
        short = re.sub(r'comment\s+"[^"]*"', '', short)
        return short.strip() or "(empty rule)"

    return " + ".join(parts)


def detect_action(expr):
    """Extract the terminal action from a rule expression.
    Checks the LAST word/token to find the actual verdict, not substrings
    in log prefixes or comments."""
    # Strip counter stats and comments to find the real action at the end
    clean = re.sub(r'counter\s+packets\s+\d+\s+bytes\s+\d+', '', expr)
    clean = re.sub(r'comment\s+"[^"]*"', '', clean)
    clean = re.sub(r'log\s+prefix\s+"[^"]*"', 'log', clean)
    tokens = clean.strip().split()
    if not tokens:
        return ""
    # Check last token first (where the verdict usually is)
    last = tokens[-1].lower()
    for action in ["accept", "drop", "reject", "masquerade", "return"]:
        if last == action:
            return action
    # Check for redirect (e.g. "redirect to :53")
    for t in tokens:
        if t.lower() == "redirect":
            return "redirect"
    # Check for jump/goto (e.g. "jump chainname")
    for i, t in enumerate(tokens):
        if t.lower() in ("jump", "goto"):
            return t.lower()
    # Check for standalone log (no drop after it)
    if "log" in [t.lower() for t in tokens]:
        return "log"
    # MSS clamping and other set operations
    if "set" in [t.lower() for t in tokens]:
        return "set"
    return ""


def is_protected(chain, expr):
    """Check if a rule is critical infrastructure that shouldn't be deleted."""
    text = f"{chain} {expr}"
    for pattern, _desc in PROTECTED_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


# ── Bulkhead App ─────────────────────────────────────────────────────

class BulkheadApp(GhostPortApp):
    def __init__(self):
        super().__init__("Bulkhead", "bulkhead", (1000, 700))

        # Data
        self.tables = []         # list of (family, table_name)
        self.rules = []          # list of dicts for current display
        self.selected_table = None
        self.rule_count = 0
        self.table_count = 0
        self.search_text = ""    # search/filter text
        self.undo_stack = []     # list of (family, table, chain, rule_expr) for undo
        self._prev_packets = {}  # (table, chain, handle) -> packet count for trend arrows
        self._zero_hit_since = {}  # (table, chain, handle) -> first_seen_time for dead rule detection

        # Extra CSS for color-coded rows
        self._apply_css(extra_css=self._extra_css())

        self.build_ui()
        self.show_all()

        # Initial load
        self.refresh_data()

        # Poll every 10 seconds
        self.poll_start(10, self._poll_refresh)

    def _extra_css(self):
        c = self.colors
        return f"""
.rule-accept {{
    color: {c['success']};
}}
.rule-drop {{
    color: {c['danger']};
}}
.rule-log {{
    color: {c['warning']};
}}
.rule-other {{
    color: {c['text']};
}}
.protected-icon {{
    color: {c['warning']};
    font-size: 14px;
}}
.table-btn {{
    background-color: transparent;
    border: 1px solid rgba({c['r']},{c['g']},{c['b']}, 0.15);
    border-radius: 4px;
    color: {c['text']};
    font-family: monospace;
    font-size: 11px;
    padding: 6px 10px;
    min-height: 28px;
}}
.table-btn:hover {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.1);
    border-color: rgba({c['r']},{c['g']},{c['b']}, 0.4);
}}
.table-btn-active {{
    background-color: rgba({c['r']},{c['g']},{c['b']}, 0.15);
    border: 1px solid {c['accent']};
    border-radius: 4px;
    color: {c['accent']};
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
    padding: 6px 10px;
    min-height: 28px;
}}
.sidebar-title {{
    color: {c['accent']};
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
    padding: 4px 0;
}}
"""

    @staticmethod
    def _fmt_count(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    @staticmethod
    def _fmt_bytes(n):
        if n >= 1_073_741_824:
            return f"{n / 1_073_741_824:.1f}GB"
        if n >= 1_048_576:
            return f"{n / 1_048_576:.1f}MB"
        if n >= 1_024:
            return f"{n / 1_024:.1f}KB"
        return f"{n}B"

    # ── UI Build ─────────────────────────────────────────────────────

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header
        root.pack_start(self.make_header("BULKHEAD", "Firewall Rule Builder"), False, False, 0)

        # Top-level tabs — Firewall (existing nftables editor) + DNS Rules
        # (new allowlist editor for dnsmasq drop-ins). Each tab owns its own
        # content so the firewall sidebar doesn't bleed into the DNS view.
        self.tabs = Gtk.Notebook()
        self.tabs.set_vexpand(True)
        root.pack_start(self.tabs, True, True, 0)

        # Tab 1: Firewall — existing sidebar + rule TreeView layout
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content.set_vexpand(True)
        self.tabs.append_page(content, Gtk.Label(label="Firewall Rules"))

        # Left sidebar — table list
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sidebar.get_style_context().add_class("gp-sidebar")
        sidebar.set_size_request(200, -1)

        sidebar_title = self.make_label("TABLES", "sidebar-title")
        sidebar.pack_start(sidebar_title, False, False, 4)

        sidebar_sep = Gtk.Separator()
        sidebar.pack_start(sidebar_sep, False, False, 2)

        # Scrolled container for table buttons
        self.table_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sidebar_scroll = self.make_scrolled(self.table_box)
        sidebar.pack_start(sidebar_scroll, True, True, 0)

        # "All Tables" button
        all_btn = Gtk.Button(label="All Tables")
        all_btn.get_style_context().add_class("table-btn-active")
        all_btn.connect("clicked", self._on_table_click, None)
        self.table_box.pack_start(all_btn, False, False, 0)
        self._all_btn = all_btn
        self._table_buttons = {}

        content.pack_start(sidebar, False, False, 0)

        # Right area — rules treeview + button bar
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.get_style_context().add_class("gp-content")
        content.pack_start(right, True, True, 0)

        # Search/filter bar
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_box.set_margin_bottom(6)
        search_icon = self.make_label("\U0001f50d", "gp-text")
        search_box.pack_start(search_icon, False, False, 0)
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Filter rules by expression, port, action, chain...")
        self.search_entry.connect("changed", self._on_search_changed)
        search_box.pack_start(self.search_entry, True, True, 0)
        clear_search = self.make_button("Clear", self._on_clear_search, "gp-btn")
        search_box.pack_start(clear_search, False, False, 0)
        right.pack_start(search_box, False, False, 0)

        # TreeView for rules
        # Columns: Protected, Chain, Handle, Expression, Action, Explanation
        # Store: protected(bool), chain(str), handle(str), expr(str),
        #        action(str), explanation(str), action_color(str),
        #        family(str), table(str), handle_int(int), is_protected(bool)
        self.store = Gtk.ListStore(
            str,   # 0: protected icon
            str,   # 1: chain
            str,   # 2: handle
            str,   # 3: expression (raw nft)
            str,   # 4: action display
            str,   # 5: explanation
            str,   # 6: foreground color for action
            str,   # 7: family (hidden)
            str,   # 8: table (hidden)
            int,   # 9: handle int (hidden)
            bool,  # 10: is_protected (hidden)
            str,   # 11: packets display
            str,   # 12: bytes display
            int,   # 13: packets raw (for sorting)
        )

        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        self.tree.set_enable_search(True)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)

        # Column 0: Protected (lock icon)
        col_lock = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
        col_lock.set_min_width(30)
        col_lock.set_max_width(30)
        self.tree.append_column(col_lock)

        # Column 1: Chain
        renderer_chain = Gtk.CellRendererText()
        renderer_chain.set_property("font", "monospace 11")
        col_chain = Gtk.TreeViewColumn("Chain", renderer_chain, text=1)
        col_chain.set_min_width(100)
        col_chain.set_resizable(True)
        col_chain.set_sort_column_id(1)
        self.tree.append_column(col_chain)

        # Column 2: Handle
        renderer_handle = Gtk.CellRendererText()
        renderer_handle.set_property("font", "monospace 11")
        col_handle = Gtk.TreeViewColumn("Handle", renderer_handle, text=2)
        col_handle.set_min_width(60)
        col_handle.set_resizable(True)
        col_handle.set_sort_column_id(9)
        self.tree.append_column(col_handle)

        # Column 3: Expression
        renderer_expr = Gtk.CellRendererText()
        renderer_expr.set_property("font", "monospace 10")
        renderer_expr.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_expr = Gtk.TreeViewColumn("Rule Expression", renderer_expr, text=3)
        col_expr.set_min_width(200)
        col_expr.set_expand(True)
        col_expr.set_resizable(True)
        self.tree.append_column(col_expr)

        # Column 4: Action (color-coded)
        renderer_action = Gtk.CellRendererText()
        renderer_action.set_property("font", "monospace 11")
        renderer_action.set_property("weight", Pango.Weight.BOLD)
        col_action = Gtk.TreeViewColumn("Action", renderer_action, text=4, foreground=6)
        col_action.set_min_width(100)
        col_action.set_resizable(True)
        self.tree.append_column(col_action)

        # Column 5: Explanation
        renderer_explain = Gtk.CellRendererText()
        renderer_explain.set_property("font", "monospace 10")
        renderer_explain.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_explain = Gtk.TreeViewColumn("Plain English", renderer_explain, text=5)
        col_explain.set_min_width(200)
        col_explain.set_expand(True)
        col_explain.set_resizable(True)
        self.tree.append_column(col_explain)

        # Column 6: Packets
        renderer_pkts = Gtk.CellRendererText()
        renderer_pkts.set_property("font", "monospace 10")
        renderer_pkts.set_property("xalign", 1.0)
        col_pkts = Gtk.TreeViewColumn("Packets", renderer_pkts, text=11)
        col_pkts.set_min_width(80)
        col_pkts.set_resizable(True)
        col_pkts.set_sort_column_id(13)
        self.tree.append_column(col_pkts)

        # Column 7: Bytes
        renderer_bytez = Gtk.CellRendererText()
        renderer_bytez.set_property("font", "monospace 10")
        renderer_bytez.set_property("xalign", 1.0)
        col_bytez = Gtk.TreeViewColumn("Bytes", renderer_bytez, text=12)
        col_bytez.set_min_width(80)
        col_bytez.set_resizable(True)
        self.tree.append_column(col_bytez)

        tree_scroll = self.make_scrolled(self.tree)
        right.pack_start(tree_scroll, True, True, 0)

        # Button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_top(8)

        btn_add = self.make_button("+ Add Rule", self._on_add_rule, "gp-btn-primary")
        btn_bar.pack_start(btn_add, False, False, 0)

        btn_delete = self.make_button("Delete Selected", self._on_delete_selected, "gp-btn-danger")
        btn_bar.pack_start(btn_delete, False, False, 0)

        self.btn_undo = self.make_button("Undo Delete", self._on_undo, "gp-btn")
        self.btn_undo.set_sensitive(False)
        btn_bar.pack_start(self.btn_undo, False, False, 0)

        btn_refresh = self.make_button("Refresh", self._on_refresh, "gp-btn")
        btn_bar.pack_start(btn_refresh, False, False, 0)

        btn_export = self.make_button("Export", self._on_export, "gp-btn")
        btn_bar.pack_end(btn_export, False, False, 0)

        btn_help = self.make_button("? Help", self._on_help, "gp-btn")
        btn_help.set_tooltip_text("Guided tour — what everything on this screen means")
        btn_bar.pack_end(btn_help, False, False, 0)

        right.pack_start(btn_bar, False, False, 0)

        # ── Tab 2: DNS Rules — dnsmasq drop-in allowlist editor ──────────
        # Backed by `sudo gp-dns-rules list|allow|block|reload`. Users can
        # toggle any domain that's currently hard-coded as address= in the
        # shipped drop-ins (e.g. browserleaks.com in 30-anti-fingerprint.conf)
        # without editing files directly.
        dns_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        dns_page.set_margin_start(12)
        dns_page.set_margin_end(12)
        dns_page.set_margin_top(8)
        dns_page.set_margin_bottom(8)

        dns_help = self.make_label(
            "DNS-level block rules from /etc/dnsmasq.d/. Type a domain + Add "
            "to block a new one, toggle the Blocked column to flip existing "
            "entries, then Reload DNS to apply.",
            "gp-dim",
        )
        dns_help.set_line_wrap(True)
        dns_help.set_halign(Gtk.Align.START)
        dns_page.pack_start(dns_help, False, False, 0)

        # Add-new-block bar — feeds gp-dns-rules add. User-added entries
        # land in /etc/dnsmasq.d/90-user-blocks.conf (OTA preserves it).
        dns_add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dns_add_label = self.make_label("Add block:", "gp-text")
        dns_add_box.pack_start(dns_add_label, False, False, 0)
        self.dns_add_entry = Gtk.Entry()
        self.dns_add_entry.set_placeholder_text("e.g. example-tracker.com  (no scheme, no path)")
        self.dns_add_entry.connect("activate", self._on_dns_add)  # Enter key submits
        dns_add_box.pack_start(self.dns_add_entry, True, True, 0)
        dns_add_btn = self.make_button("Add", self._on_dns_add, "gp-btn-primary")
        dns_add_btn.set_tooltip_text("Add a new block rule and reload DNS")
        dns_add_box.pack_start(dns_add_btn, False, False, 0)
        dns_page.pack_start(dns_add_box, False, False, 0)

        # DNS filter box
        dns_filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dns_filter_icon = self.make_label("\U0001f50d", "gp-text")
        dns_filter_box.pack_start(dns_filter_icon, False, False, 0)
        self.dns_filter = Gtk.Entry()
        self.dns_filter.set_placeholder_text("Filter by domain…")
        self.dns_filter.connect("changed", self._on_dns_filter_changed)
        dns_filter_box.pack_start(self.dns_filter, True, True, 0)
        dns_page.pack_start(dns_filter_box, False, False, 0)

        # T-0014: row count / cap status. Anti-fingerprint blocklists can
        # grow to 10k+ entries (Path A work in 2acb381 + 66564bd). GTK3
        # TreeView lags hard above ~1k rows on Pi 5. Cap visible rows at
        # 500; backing list (dns_store_all) stays full so the filter can
        # still hit any rule.
        self.dns_status_label = self.make_label("", "gp-dim")
        self.dns_status_label.set_halign(Gtk.Align.START)
        dns_page.pack_start(self.dns_status_label, False, False, 0)

        # DNS rule TreeView — columns: blocked(toggle), domain, target, file
        self.dns_store_all = []  # in-memory full rule set; filter pulls from here
        self.dns_store = Gtk.ListStore(bool, str, str, str)  # blocked, domain, target, file
        self.dns_tree = Gtk.TreeView(model=self.dns_store)
        self.dns_tree.set_headers_visible(True)

        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.set_activatable(True)
        toggle_renderer.connect("toggled", self._on_dns_toggle)
        col_blocked = Gtk.TreeViewColumn("Blocked", toggle_renderer, active=0)
        col_blocked.set_min_width(80)
        self.dns_tree.append_column(col_blocked)

        dns_col_domain_rend = Gtk.CellRendererText()
        dns_col_domain_rend.set_property("font", "monospace 11")
        col_domain = Gtk.TreeViewColumn("Domain", dns_col_domain_rend, text=1)
        col_domain.set_min_width(220)
        col_domain.set_resizable(True)
        col_domain.set_sort_column_id(1)
        self.dns_tree.append_column(col_domain)

        dns_col_target_rend = Gtk.CellRendererText()
        dns_col_target_rend.set_property("font", "monospace 10")
        col_target = Gtk.TreeViewColumn("Resolves to", dns_col_target_rend, text=2)
        col_target.set_min_width(100)
        col_target.set_resizable(True)
        self.dns_tree.append_column(col_target)

        dns_col_file_rend = Gtk.CellRendererText()
        dns_col_file_rend.set_property("font", "monospace 10")
        col_file = Gtk.TreeViewColumn("Source file", dns_col_file_rend, text=3)
        col_file.set_min_width(180)
        col_file.set_resizable(True)
        self.dns_tree.append_column(col_file)

        dns_page.pack_start(self.make_scrolled(self.dns_tree), True, True, 0)

        # DNS button bar
        dns_btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dns_btn_refresh = self.make_button("Refresh list", self._on_dns_refresh, "gp-btn")
        dns_btn_bar.pack_start(dns_btn_refresh, False, False, 0)
        dns_btn_reload = self.make_button("Reload DNS (pihole-FTL)", self._on_dns_reload, "gp-btn-primary")
        dns_btn_reload.set_tooltip_text("Apply pending allow/block changes by restarting pihole-FTL")
        dns_btn_bar.pack_start(dns_btn_reload, False, False, 0)
        dns_page.pack_start(dns_btn_bar, False, False, 0)

        self.tabs.append_page(dns_page, Gtk.Label(label="DNS Rules"))

        # Kick off DNS list load in the background so the tab is populated
        # by the time the user clicks over to it.
        GLib.idle_add(self._load_dns_rules)

        # Status bar (shared — reused across both tabs)
        root.pack_start(self.make_status_bar("Loading firewall rules..."), False, False, 0)

    # ── DNS Rules tab handlers ──────────────────────────────────────────

    def _load_dns_rules(self):
        """Refresh the DNS rule list from `gp-dns-rules list`. Idempotent."""
        try:
            r = subprocess.run(
                ["sudo", "-n", "gp-dns-rules", "list"],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.set_status(f"gp-dns-rules list error: {e}")
            return False
        if r.returncode != 0:
            self.set_status(f"gp-dns-rules list failed: {(r.stderr or r.stdout).strip()[:80]}")
            return False
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            self.set_status("gp-dns-rules returned invalid JSON")
            return False
        self.dns_store_all = data.get("rules", [])
        self._apply_dns_filter()
        return False  # idle_add should not repeat

    # T-0014: Hard cap on visible rows. 500 is well above any realistic
    # interactive scroll; for blocklists in the 10k+ range (anti-fingerprint
    # imports), the user must filter to find specific entries. Backing
    # dns_store_all keeps the full set so toggle/reload work end-to-end.
    DNS_VISIBLE_ROW_CAP = 500

    def _apply_dns_filter(self):
        """Re-render dns_store from dns_store_all applying the current filter text."""
        needle = (self.dns_filter.get_text() if hasattr(self, "dns_filter") else "").lower().strip()
        self.dns_store.clear()
        matched = 0
        shown = 0
        for rule in self.dns_store_all:
            if needle and needle not in rule["domain"].lower():
                continue
            matched += 1
            if shown >= self.DNS_VISIBLE_ROW_CAP:
                continue
            fname = rule["file"].rsplit("/", 1)[-1]
            self.dns_store.append([rule["blocked"], rule["domain"], rule["target"], fname])
            shown += 1
        if hasattr(self, "dns_status_label"):
            total = len(self.dns_store_all)
            if matched <= self.DNS_VISIBLE_ROW_CAP:
                self.dns_status_label.set_text(
                    f"{matched} rule{'' if matched == 1 else 's'}"
                    + (f" (of {total} total)" if needle and matched < total else "")
                )
            else:
                self.dns_status_label.set_text(
                    f"Showing {self.DNS_VISIBLE_ROW_CAP} of {matched} matching"
                    f"{f' (of {total} total)' if needle else ''} — type to filter"
                )

    def _on_dns_filter_changed(self, _entry):
        self._apply_dns_filter()

    def _on_dns_toggle(self, _renderer, path):
        """Click the Blocked cell → flip that domain via gp-dns-rules allow/block."""
        it = self.dns_store.get_iter(path)
        currently_blocked = self.dns_store.get_value(it, 0)
        domain = self.dns_store.get_value(it, 1)
        action = "allow" if currently_blocked else "block"
        try:
            r = subprocess.run(
                ["sudo", "-n", "gp-dns-rules", action, domain],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.set_status(f"gp-dns-rules {action} error: {e}")
            return
        if r.returncode != 0:
            self.set_status(f"{action} failed: {(r.stderr or r.stdout).strip()[:80]}")
            return
        # Update in-memory state + re-render row
        for rule in self.dns_store_all:
            if rule["domain"].lower() == domain.lower():
                rule["blocked"] = not currently_blocked
        self.dns_store.set_value(it, 0, not currently_blocked)
        self.set_status(
            f"{action}ed {domain} — click 'Reload DNS (pihole-FTL)' to apply"
        )

    def _on_dns_refresh(self, _btn):
        self._load_dns_rules()
        self.set_status("DNS rule list refreshed")

    def _on_dns_add(self, _widget):
        """Add-new-block handler. Called by the Add button and by Enter
        on the entry widget (same flow). On success: refreshes the list
        so the new row appears + auto-reloads DNS so the block is live.
        Keeps the entry field populated only on error so the user can fix
        and retry without re-typing.
        """
        domain = (self.dns_add_entry.get_text() or "").strip()
        if not domain:
            self.set_status("Enter a domain to block (e.g. example.com)")
            return
        try:
            r = subprocess.run(
                ["sudo", "-n", "gp-dns-rules", "add", domain],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.set_status(f"gp-dns-rules add error: {e}")
            return
        if r.returncode != 0:
            # CLI prints validation / duplicate errors to stderr; surface them verbatim
            msg = (r.stderr or r.stdout).strip().splitlines()[-1][:160]
            self.set_status(f"Add failed: {msg}")
            return  # leave entry populated so user can edit + retry
        # Success: clear entry, refresh list, auto-reload so the new block
        # is live immediately (different from the toggle flow where reload
        # is explicit — here the user's intent is "block this now").
        self.dns_add_entry.set_text("")
        self._load_dns_rules()
        try:
            rr = subprocess.run(
                ["sudo", "-n", "gp-dns-rules", "reload"],
                capture_output=True, text=True, timeout=30,
            )
            if rr.returncode == 0:
                self.set_status(f"Added {domain} and reloaded DNS — block is live")
            else:
                self.set_status(f"Added {domain} (reload failed — click Reload DNS to retry)")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.set_status(f"Added {domain} (reload skipped — click Reload DNS manually)")

    def _on_dns_reload(self, _btn):
        try:
            r = subprocess.run(
                ["sudo", "-n", "gp-dns-rules", "reload"],
                capture_output=True, text=True, timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.set_status(f"reload error: {e}")
            return
        if r.returncode != 0:
            self.set_status(f"reload failed: {(r.stderr or r.stdout).strip()[:80]}")
            return
        self.set_status("DNS reloaded — changes are live")

    # ── Data Loading ─────────────────────────────────────────────────

    def refresh_data(self):
        """Load nftables ruleset in background."""
        self.set_status("Refreshing firewall rules...")
        self.run_async(self._load_ruleset, self._on_ruleset_loaded)

    def _poll_refresh(self):
        """Periodic refresh callback."""
        self.run_async(self._load_ruleset, self._on_ruleset_loaded)
        return True

    def _load_ruleset(self):
        """Fetch nftables ruleset. Returns (tables, rules) or Exception."""
        # Try JSON first
        stdout, stderr, rc = self.run_sudo(["nft", "-j", "-a", "list", "ruleset"], timeout=10)
        if rc == 0 and stdout:
            try:
                return self._parse_json_ruleset(stdout)
            except Exception:
                pass

        # Fallback to text parsing
        stdout, stderr, rc = self.run_sudo(["nft", "-a", "list", "ruleset"], timeout=10)
        if rc == 0 and stdout:
            return self._parse_text_ruleset(stdout)

        return Exception(f"Failed to load ruleset: {stderr}")

    def _parse_json_ruleset(self, raw):
        """Parse nft -j output into tables and rules."""
        data = json.loads(raw)
        nftables = data.get("nftables", [])

        tables = set()
        rules = []

        for item in nftables:
            if "table" in item:
                t = item["table"]
                tables.add((t.get("family", "inet"), t.get("name", "?")))

            elif "rule" in item:
                r = item["rule"]
                family = r.get("family", "inet")
                table = r.get("table", "?")
                chain = r.get("chain", "?")
                handle = r.get("handle", 0)
                tables.add((family, table))

                # Build expression string from JSON expr array
                expr_parts = []
                action = ""
                for e in r.get("expr", []):
                    if isinstance(e, dict):
                        for key, val in e.items():
                            expr_parts.append(self._json_expr_to_str(key, val))
                            if key in ACTION_MAP:
                                action = key
                    elif isinstance(e, str):
                        expr_parts.append(e)
                        if e in ACTION_MAP:
                            action = e

                expr_str = " ".join(expr_parts)
                explanation = translate_rule(expr_str)
                action_display = ACTION_MAP.get(action, action)
                protected = is_protected(chain, expr_str)

                rules.append({
                    "family": family,
                    "table": table,
                    "chain": chain,
                    "handle": handle,
                    "expr": expr_str,
                    "action": action,
                    "action_display": action_display,
                    "explanation": explanation,
                    "protected": protected,
                })

        return (sorted(tables), rules)

    def _json_expr_to_str(self, key, val):
        """Convert a single JSON nft expression element to a readable string."""
        if key == "match":
            op = val.get("op", "==")
            left = self._json_expr_val(val.get("left", ""))
            right = self._json_expr_val(val.get("right", ""))
            if op == "==":
                return f"{left} {right}"
            return f"{left} {op} {right}"
        elif key == "counter":
            packets = val.get("packets", 0) if isinstance(val, dict) else 0
            bytez = val.get("bytes", 0) if isinstance(val, dict) else 0
            return f"counter packets {packets} bytes {bytez}"
        elif key == "log":
            prefix = val.get("prefix", "") if isinstance(val, dict) else ""
            return f'log prefix "{prefix}"' if prefix else "log"
        elif key == "accept":
            return "accept"
        elif key == "drop":
            return "drop"
        elif key == "reject":
            return "reject"
        elif key == "masquerade":
            return "masquerade"
        elif key == "return":
            return "return"
        elif key == "jump":
            target = val.get("target", "") if isinstance(val, dict) else val
            return f"jump {target}"
        elif key == "goto":
            target = val.get("target", "") if isinstance(val, dict) else val
            return f"goto {target}"
        elif key == "comment":
            return f'comment "{val}"' if isinstance(val, str) else ""
        elif key == "mangle":
            return "mangle"
        elif key == "redirect":
            port = ""
            if isinstance(val, dict) and "port" in val:
                port = f" to :{val['port']}"
            return f"redirect{port}"
        elif key == "dnat":
            addr = ""
            if isinstance(val, dict):
                addr = val.get("addr", "")
                port = val.get("port", "")
                if addr and port:
                    return f"dnat to {addr}:{port}"
                if addr:
                    return f"dnat to {addr}"
            return "dnat"
        elif key == "snat":
            if isinstance(val, dict):
                addr = val.get("addr", "")
                return f"snat to {addr}" if addr else "snat"
            return "snat"
        elif isinstance(val, dict):
            # Generic dict — try to reconstruct
            return key
        elif val is None:
            return key
        else:
            return f"{key} {val}"

    def _json_expr_val(self, val):
        """Recursively turn a JSON expression value into a string."""
        if isinstance(val, str):
            return val
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, dict):
            if "meta" in val:
                mk = val["meta"].get("key", "")
                return f"meta {mk}" if mk else "meta"
            if "payload" in val:
                p = val["payload"]
                proto = p.get("protocol", "")
                field = p.get("field", "")
                return f"{proto} {field}" if proto else field
            if "ct" in val:
                ct = val["ct"]
                return f"ct {ct.get('key', '')}"
            if "prefix" in val:
                addr = val.get("addr", "")
                length = val.get("len", "")
                if isinstance(addr, dict):
                    addr = self._json_expr_val(addr)
                return f"{addr}/{length}" if length else str(addr)
            if "fib" in val:
                return "fib"
            if "set" in val:
                items = val["set"]
                if isinstance(items, list):
                    return ",".join(str(i) for i in items)
                return str(items)
            if "range" in val:
                items = val["range"]
                if isinstance(items, list) and len(items) == 2:
                    return f"{items[0]}-{items[1]}"
                return str(items)
            # Fallback
            for k, v in val.items():
                return f"{k} {self._json_expr_val(v)}"
        if isinstance(val, list):
            return ",".join(self._json_expr_val(v) for v in val)
        return str(val)

    def _parse_text_ruleset(self, raw):
        """Fallback: parse plaintext nft list ruleset output."""
        tables = set()
        rules = []
        current_family = "inet"
        current_table = ""
        current_chain = ""

        for line in raw.splitlines():
            line = line.strip()

            # table inet ghostport {
            m = re.match(r'table\s+(\w+)\s+(\w+)\s*\{', line)
            if m:
                current_family = m.group(1)
                current_table = m.group(2)
                tables.add((current_family, current_table))
                continue

            # chain forward {
            m = re.match(r'chain\s+(\w+)\s*\{', line)
            if m:
                current_chain = m.group(1)
                continue

            # rule lines with handle: ... # handle 42
            m = re.match(r'(.+?)\s*#\s*handle\s+(\d+)', line)
            if m and current_table and current_chain:
                expr_str = m.group(1).strip()
                handle = int(m.group(2))

                # Skip chain policy lines like "type filter hook ..."
                if expr_str.startswith("type "):
                    continue

                action = detect_action(expr_str)
                action_display = ACTION_MAP.get(action, action)
                explanation = translate_rule(expr_str)
                protected = is_protected(current_chain, expr_str)

                rules.append({
                    "family": current_family,
                    "table": current_table,
                    "chain": current_chain,
                    "handle": handle,
                    "expr": expr_str,
                    "action": action,
                    "action_display": action_display,
                    "explanation": explanation,
                    "protected": protected,
                })

        return (sorted(tables), rules)

    def _on_ruleset_loaded(self, result):
        """Callback on main thread after ruleset is loaded."""
        if isinstance(result, Exception):
            self.set_status(f"Error: {result}")
            return

        tables, rules = result
        self.tables = tables
        self.rules = rules
        self.rule_count = len(rules)
        self.table_count = len(tables)

        self._update_sidebar()
        self._update_treeview()
        self.set_status(f"{self.rule_count} rules loaded across {self.table_count} tables")

    # ── Sidebar ──────────────────────────────────────────────────────

    def _update_sidebar(self):
        """Rebuild table buttons in sidebar."""
        # Remove old buttons (except All Tables)
        for child in list(self.table_box.get_children()):
            if child != self._all_btn:
                self.table_box.remove(child)

        self._table_buttons = {}

        for family, name in self.tables:
            label_text = f"{family} {name}"
            # Count rules in this table
            count = sum(1 for r in self.rules if r["family"] == family and r["table"] == name)
            btn = Gtk.Button(label=f"{label_text} ({count})")
            is_active = (self.selected_table == (family, name))
            btn.get_style_context().add_class("table-btn-active" if is_active else "table-btn")
            btn.connect("clicked", self._on_table_click, (family, name))
            self.table_box.pack_start(btn, False, False, 0)
            self._table_buttons[(family, name)] = btn

        self.table_box.show_all()

    def _on_table_click(self, button, table_key):
        """Handle table selection."""
        self.selected_table = table_key

        # Update button styles
        is_all = table_key is None
        for cls in ["table-btn", "table-btn-active"]:
            self._all_btn.get_style_context().remove_class(cls)
        self._all_btn.get_style_context().add_class("table-btn-active" if is_all else "table-btn")

        for key, btn in self._table_buttons.items():
            for cls in ["table-btn", "table-btn-active"]:
                btn.get_style_context().remove_class(cls)
            btn.get_style_context().add_class("table-btn-active" if key == table_key else "table-btn")

        self._update_treeview()

    # ── TreeView ─────────────────────────────────────────────────────

    def _update_treeview(self):
        """Populate the rule list based on current table and search filter."""
        self.store.clear()

        filtered = self.rules
        if self.selected_table:
            family, table = self.selected_table
            filtered = [r for r in self.rules
                        if r["family"] == family and r["table"] == table]

        # Apply search filter
        if self.search_text:
            query = self.search_text
            filtered = [r for r in filtered
                        if query in r["expr"].lower()
                        or query in r["chain"].lower()
                        or query in r["action"].lower()
                        or query in r["action_display"].lower()
                        or query in r["explanation"].lower()]

        new_prev = {}
        total_pkts = 0
        total_bytes_val = 0

        for r in filtered:
            # Color for action column
            action = r["action"]
            if action in ("accept",):
                color = self.colors["success"]
            elif action in ("drop", "reject"):
                color = self.colors["danger"]
            elif action in ("masquerade", "redirect"):
                color = self.colors["info"]
            elif action in ("log",):
                color = self.colors["warning"]
            elif action in ("set",):
                color = self.colors["text"]
            else:
                color = self.colors["text"]

            lock_icon = "\U0001f512" if r["protected"] else ""

            # Extract counter stats from expression
            pkts_raw = 0
            bytes_raw = 0
            m = re.search(r'counter\s+packets\s+(\d+)\s+bytes\s+(\d+)', r["expr"])
            if m:
                pkts_raw = int(m.group(1))
                bytes_raw = int(m.group(2))

            total_pkts += pkts_raw
            total_bytes_val += bytes_raw

            # Trend arrow based on previous packet count
            key = (r["table"], r["chain"], r["handle"])
            prev_pkts = self._prev_packets.get(key)
            trend = ""
            if prev_pkts is not None:
                if pkts_raw > prev_pkts:
                    trend = " \u2191"  # ↑
                elif pkts_raw < prev_pkts:
                    trend = " \u2193"  # ↓
            new_prev[key] = pkts_raw

            # Dead rule flagging: track rules with 0 packets for >24h
            now = time.time()
            explanation = r["explanation"]
            if pkts_raw == 0 and m:  # has counter but 0 packets
                if key not in self._zero_hit_since:
                    self._zero_hit_since[key] = now
                elif now - self._zero_hit_since[key] > 86400:
                    explanation = explanation + " \u26a0 DEAD"
            else:
                # Rule has packets, remove from dead tracking
                self._zero_hit_since.pop(key, None)
            # Cap dict at 1000 entries
            if len(self._zero_hit_since) > 1000:
                oldest = sorted(self._zero_hit_since, key=self._zero_hit_since.get)
                for old_key in oldest[:len(self._zero_hit_since) - 1000]:
                    del self._zero_hit_since[old_key]

            pkts_display = self._fmt_count(pkts_raw) + trend if pkts_raw > 0 or m else ""
            bytes_display = self._fmt_bytes(bytes_raw) if bytes_raw > 0 or m else ""

            self.store.append([
                lock_icon,                      # 0: protected icon
                r["chain"],                     # 1: chain
                str(r["handle"]),               # 2: handle string
                r["expr"],                      # 3: expression
                r["action_display"],            # 4: action display
                explanation,                    # 5: explanation (may have DEAD flag)
                color,                          # 6: action foreground color
                r["family"],                    # 7: family (hidden)
                r["table"],                     # 8: table (hidden)
                r["handle"],                    # 9: handle int (hidden)
                r["protected"],                 # 10: is_protected (hidden)
                pkts_display,                   # 11: packets display
                bytes_display,                  # 12: bytes display
                pkts_raw,                       # 13: packets raw (for sorting)
            ])

        self._prev_packets = new_prev

        # Default sort by packet count descending
        self.store.set_sort_column_id(13, Gtk.SortType.DESCENDING)

        count = len(filtered)
        stats = f" | {self._fmt_count(total_pkts)} pkts, {self._fmt_bytes(total_bytes_val)}" if total_pkts > 0 else ""
        if self.selected_table:
            self.set_status(
                f"{count} rules in {self.selected_table[0]} {self.selected_table[1]} "
                f"({self.rule_count} total across {self.table_count} tables){stats}"
            )
        else:
            self.set_status(f"{self.rule_count} rules loaded across {self.table_count} tables{stats}")

    # ── Search ────────────────────────────────────────────────────────

    def _on_search_changed(self, entry):
        self.search_text = entry.get_text().strip().lower()
        self._update_treeview()

    def _on_clear_search(self, button):
        self.search_entry.set_text("")
        self.search_text = ""
        self._update_treeview()

    # ── Button Handlers ──────────────────────────────────────────────

    def _on_refresh(self, button):
        self.refresh_data()

    def _validate_port(self, port_str):
        """Validate a port number or range. Returns (ok, error_msg)."""
        if not port_str:
            return True, ""
        # Port range: 1000-2000
        if "-" in port_str:
            parts = port_str.split("-", 1)
            if len(parts) != 2:
                return False, "Invalid port range format (use start-end)"
            try:
                lo, hi = int(parts[0]), int(parts[1])
            except ValueError:
                return False, "Port range must be numeric"
            if lo < 1 or hi > 65535:
                return False, "Ports must be 1-65535"
            if lo >= hi:
                return False, "Range start must be less than end"
            return True, ""
        try:
            p = int(port_str)
        except ValueError:
            return False, "Port must be a number (1-65535)"
        if p < 1 or p > 65535:
            return False, f"Port {p} out of range (1-65535)"
        return True, ""

    def _tokenize_rule(self, rule_expr):
        """Split a rule expression respecting quoted strings."""
        tokens = []
        for m in re.finditer(r'"[^"]*"|\S+', rule_expr):
            tokens.append(m.group(0))
        return tokens

    def _on_add_rule(self, button):
        """Show the Add Rule dialog."""
        dialog = Gtk.Dialog(
            title="Add Firewall Rule",
            parent=self,
            modal=True,
            destroy_with_parent=True,
        )
        dialog.set_default_size(500, 400)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Validate & Add", Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)

        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(12)

        row = 0

        # Table/Family selector
        lbl_table = self.make_label("Table:", "gp-text")
        grid.attach(lbl_table, 0, row, 1, 1)
        combo_table = Gtk.ComboBoxText()
        for family, name in self.tables:
            combo_table.append_text(f"{family} {name}")
        if self.selected_table:
            for i, (f, n) in enumerate(self.tables):
                if (f, n) == self.selected_table:
                    combo_table.set_active(i)
                    break
        elif self.tables:
            combo_table.set_active(0)
        grid.attach(combo_table, 1, row, 1, 1)
        row += 1

        # Chain
        lbl_dir = self.make_label("Chain:", "gp-text")
        grid.attach(lbl_dir, 0, row, 1, 1)
        combo_dir = Gtk.ComboBoxText()
        for d in ["input", "forward", "output", "prerouting", "postrouting"]:
            combo_dir.append_text(d)
        combo_dir.set_active(0)
        grid.attach(combo_dir, 1, row, 1, 1)
        row += 1

        # Interface (in/out)
        lbl_iface = self.make_label("Interface:", "gp-text")
        grid.attach(lbl_iface, 0, row, 1, 1)
        iface_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        combo_iface_dir = Gtk.ComboBoxText()
        for d in ["(none)", "iifname (in)", "oifname (out)"]:
            combo_iface_dir.append_text(d)
        combo_iface_dir.set_active(0)
        iface_box.pack_start(combo_iface_dir, False, False, 0)
        combo_iface = Gtk.ComboBoxText()
        for iface in ["wlan0", "eth0", "wg0", "wg1", "tailscale0", "lo"]:
            combo_iface.append_text(iface)
        combo_iface.set_sensitive(False)
        iface_box.pack_start(combo_iface, True, True, 0)
        combo_iface_dir.connect("changed", lambda w: combo_iface.set_sensitive(w.get_active() != 0))
        grid.attach(iface_box, 1, row, 1, 1)
        row += 1

        # Protocol
        lbl_proto = self.make_label("Protocol:", "gp-text")
        grid.attach(lbl_proto, 0, row, 1, 1)
        combo_proto = Gtk.ComboBoxText()
        for p in ["tcp", "udp", "any"]:
            combo_proto.append_text(p)
        combo_proto.set_active(0)
        grid.attach(combo_proto, 1, row, 1, 1)
        row += 1

        # Port
        lbl_port = self.make_label("Port:", "gp-text")
        grid.attach(lbl_port, 0, row, 1, 1)
        entry_port = Gtk.Entry()
        entry_port.set_placeholder_text("e.g. 8080 or 1000-2000")
        grid.attach(entry_port, 1, row, 1, 1)
        row += 1

        # Source IP
        lbl_src = self.make_label("Source IP:", "gp-text")
        grid.attach(lbl_src, 0, row, 1, 1)
        entry_src = Gtk.Entry()
        entry_src.set_placeholder_text("optional, e.g. 192.168.50.0/24")
        grid.attach(entry_src, 1, row, 1, 1)
        row += 1

        # Action
        lbl_action = self.make_label("Action:", "gp-text")
        grid.attach(lbl_action, 0, row, 1, 1)
        combo_action = Gtk.ComboBoxText()
        for a in ["accept", "drop", "reject"]:
            combo_action.append_text(a)
        combo_action.set_active(0)
        grid.attach(combo_action, 1, row, 1, 1)
        row += 1

        content.pack_start(grid, True, True, 0)
        dialog.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            table_text = combo_table.get_active_text()
            chain = combo_dir.get_active_text()
            proto = combo_proto.get_active_text()
            port = entry_port.get_text().strip()
            src = entry_src.get_text().strip()
            action = combo_action.get_active_text()
            iface_dir_idx = combo_iface_dir.get_active()
            iface_name = combo_iface.get_active_text()

            if not table_text:
                self._show_error("Please select a table.")
                dialog.destroy()
                return

            # Validate port
            port_ok, port_err = self._validate_port(port)
            if not port_ok:
                self._show_error(port_err)
                dialog.destroy()
                return

            parts = table_text.split(" ", 1)
            family = parts[0]
            table_name = parts[1] if len(parts) > 1 else parts[0]

            # Build nft rule parts
            rule_parts = []

            # Interface
            if iface_dir_idx == 1 and iface_name:
                rule_parts.append(f'iifname "{iface_name}"')
            elif iface_dir_idx == 2 and iface_name:
                rule_parts.append(f'oifname "{iface_name}"')

            if src:
                rule_parts.append(f"ip saddr {src}")
            if proto != "any" and port:
                rule_parts.append(f"{proto} dport {port}")
            elif port:
                rule_parts.append(f"tcp dport {port}")
            rule_parts.append(action)

            rule_expr = " ".join(rule_parts)

            if not rule_expr.strip() or rule_expr.strip() in ("accept", "drop", "reject"):
                self._show_error("Rule is too broad. Specify at least a port, source IP, or interface.")
                dialog.destroy()
                return

            # Tokenize properly (handles quoted interface names)
            tokens = self._tokenize_rule(rule_expr)

            # Dry-run validate
            cmd = ["nft", "-c", "add", "rule", family, table_name, chain] + tokens
            stdout, stderr, rc = self.run_sudo(cmd, timeout=10)

            if rc != 0:
                self._show_error(f"Validation failed:\n{stderr}")
                dialog.destroy()
                return

            # Actually apply
            cmd = ["nft", "add", "rule", family, table_name, chain] + tokens
            stdout, stderr, rc = self.run_sudo(cmd, timeout=10)

            if rc != 0:
                self._show_error(f"Failed to add rule:\n{stderr}")
            else:
                self.set_status(f"Rule added: {family} {table_name} {chain} {rule_expr}")
                self.refresh_data()

        dialog.destroy()

    def _on_delete_selected(self, button):
        """Delete selected rules (non-protected only)."""
        selection = self.tree.get_selection()
        model, paths = selection.get_selected_rows()

        if not paths:
            self._show_error("No rules selected. Click on rules to select them.")
            return

        # Gather rules to delete
        to_delete = []
        protected_count = 0
        for path in paths:
            it = model.get_iter(path)
            is_prot = model.get_value(it, 10)
            if is_prot:
                protected_count += 1
                continue
            family = model.get_value(it, 7)
            table = model.get_value(it, 8)
            chain = model.get_value(it, 1)
            handle = model.get_value(it, 9)
            expr = model.get_value(it, 3)
            to_delete.append((family, table, chain, handle, expr))

        if not to_delete:
            self._show_error(
                f"All {protected_count} selected rule(s) are protected "
                "(critical infrastructure rules cannot be deleted)."
            )
            return

        # Confirmation dialog
        msg = f"Delete {len(to_delete)} rule(s)?"
        if protected_count:
            msg += f"\n({protected_count} protected rule(s) will be skipped)"
        msg += "\n\nRules to delete:"
        for family, table, chain, handle, expr in to_delete[:5]:
            short_expr = expr[:60] + "..." if len(expr) > 60 else expr
            msg += f"\n  {chain} #{handle}: {short_expr}"
        if len(to_delete) > 5:
            msg += f"\n  ... and {len(to_delete) - 5} more"

        confirm = Gtk.MessageDialog(
            parent=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=msg,
        )
        confirm.set_title("Confirm Delete")
        response = confirm.run()
        confirm.destroy()

        if response != Gtk.ResponseType.OK:
            return

        # Delete rules (in reverse handle order to avoid handle shifting)
        to_delete.sort(key=lambda x: x[3], reverse=True)
        success = 0
        errors = []
        undo_batch = []
        for family, table, chain, handle, expr in to_delete:
            cmd = ["nft", "delete", "rule", family, table, chain, "handle", str(handle)]
            stdout, stderr, rc = self.run_sudo(cmd, timeout=10)
            if rc == 0:
                success += 1
                # Save for undo — strip counter stats since they won't apply to re-add
                clean_expr = re.sub(r'counter\s+packets\s+\d+\s+bytes\s+\d+', 'counter', expr)
                undo_batch.append((family, table, chain, clean_expr))
            else:
                errors.append(f"Handle {handle}: {stderr}")

        if undo_batch:
            self.undo_stack.append(undo_batch)
            if len(self.undo_stack) > 50:
                self.undo_stack = self.undo_stack[-50:]
            self.btn_undo.set_sensitive(True)

        status = f"Deleted {success}/{len(to_delete)} rule(s)"
        if errors:
            status += f" ({len(errors)} failed)"
        self.set_status(status)
        self.refresh_data()

    def _on_undo(self, button):
        """Re-add the last batch of deleted rules."""
        if not self.undo_stack:
            self._show_error("Nothing to undo.")
            return

        batch = self.undo_stack.pop()
        if not self.undo_stack:
            self.btn_undo.set_sensitive(False)

        success = 0
        errors = []
        for family, table, chain, expr in batch:
            tokens = self._tokenize_rule(expr)
            cmd = ["nft", "add", "rule", family, table, chain] + tokens
            stdout, stderr, rc = self.run_sudo(cmd, timeout=10)
            if rc == 0:
                success += 1
            else:
                errors.append(f"{chain}: {stderr}")

        status = f"Undo: restored {success}/{len(batch)} rule(s)"
        if errors:
            status += f" ({len(errors)} failed)"
        self.set_status(status)
        self.refresh_data()

    def _on_export(self, button):
        """Export current ruleset to a file."""
        dialog = Gtk.FileChooserDialog(
            title="Export Firewall Rules",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_current_name("ghostport-rules.nft")
        dialog.set_do_overwrite_confirmation(True)

        # Add filter
        filt = Gtk.FileFilter()
        filt.set_name("nftables rules (*.nft)")
        filt.add_pattern("*.nft")
        dialog.add_filter(filt)

        filt_all = Gtk.FileFilter()
        filt_all.set_name("All files")
        filt_all.add_pattern("*")
        dialog.add_filter(filt_all)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            dialog.destroy()

            # Get the ruleset text
            def _export():
                stdout, stderr, rc = self.run_sudo(["nft", "list", "ruleset"], timeout=15)
                return (stdout, stderr, rc)

            def _on_exported(result):
                if isinstance(result, Exception):
                    self._show_error(f"Export failed: {result}")
                    return
                stdout, stderr, rc = result
                if rc != 0:
                    self._show_error(f"Export failed: {stderr}")
                    return
                try:
                    with open(filepath, "w") as f:
                        f.write(stdout + "\n")
                    self.set_status(f"Exported to {filepath}")
                except Exception as e:
                    self._show_error(f"Write failed: {e}")

            self.run_async(_export, _on_exported)
        else:
            dialog.destroy()

    # ── Helpers ──────────────────────────────────────────────────────

    def _show_error(self, message):
        """Show an error message dialog."""
        dlg = Gtk.MessageDialog(
            parent=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        dlg.set_title("Bulkhead")
        dlg.run()
        dlg.destroy()

    # ── Help Dialog ──────────────────────────────────────────────────
    # Per-region contextual help for non-technical users, added 2026-04-20.
    # Each tuple is (section heading, plain-English body). The ? Help button
    # in the bottom bar opens a modal with these as collapsible expanders.
    # To add a section: append a tuple. To reword: edit here — no other wiring.
    # Companion tutorial: /opt/phantom/docs/tutorials/bulkhead-tutorial.md

    HELP_SECTIONS = [
        ("What is Bulkhead?",
         "Bulkhead shows you every rule your GhostPort firewall is "
         "enforcing right now — and lets you add or remove rules "
         "without editing config files.\n\n"
         "Think of the firewall as a bouncer at the door of your "
         "network. Each rule is a line in the bouncer's notebook: "
         "\"let this in, block that, count how often this happens.\" "
         "Bulkhead is just a friendly view of that notebook."),

        ("TABLES sidebar (left)",
         "The left column lists the firewall's \"tables\" — groupings "
         "of related rules. Most people only have one or two.\n\n"
         "• All Tables — shows every rule in one list (the default).\n"
         "• Individual table buttons — click one to filter the view to "
         "just that table's rules.\n\n"
         "You don't need to understand table names to use Bulkhead. "
         "Just leave it on \"All Tables\" unless you're hunting for "
         "something specific."),

        ("Filter bar (top right)",
         "Type anything — a port number, an IP, a word like \"drop\" "
         "or \"tailscale\" — and the list below narrows down instantly.\n\n"
         "This is the fastest way to answer \"is there a rule about X?\" "
         "The Clear button wipes the filter and shows everything again."),

        ("Rule list columns (the big table)",
         "Each row is one firewall rule. The columns:\n\n"
         "🔒 (lock)  — a protected rule. You can't delete these. They "
         "keep you from locking yourself out of your own device "
         "(Tailscale, SSH, the dashboard on port 4200).\n\n"
         "Chain  — which part of the traffic flow the rule checks "
         "(incoming, outgoing, forwarded). Technical; most users "
         "can ignore it.\n\n"
         "Handle  — the rule's ID number. Used when you want to "
         "delete a specific rule.\n\n"
         "Rule Expression  — the raw firewall syntax. Precise but "
         "cryptic. Read the next column instead.\n\n"
         "Action  — what happens if traffic matches. Color-coded:\n"
         "   ACCEPT (green) = let through\n"
         "   DROP / REJECT (red) = block\n"
         "   LOG (yellow) = just record it\n"
         "   JUMP / GOTO = send to another rule set\n\n"
         "Plain English  — the same rule explained in normal words. "
         "If you only read one column, read this one.\n\n"
         "Packets / Bytes  — how many times the rule has fired since "
         "the last boot. Zero packets after a few hours of use usually "
         "means the rule isn't doing anything — a candidate for cleanup."),

        ("Bottom button bar",
         "+ Add Rule  — opens a form to add a new rule. You pick what "
         "to block or allow (an IP, a port, a protocol) and Bulkhead "
         "writes the firewall syntax for you.\n\n"
         "Delete Selected  — removes the rule you clicked on. "
         "Protected rules (🔒) are refused automatically. Everything "
         "else is fair game — but see \"Undo\" below before panicking.\n\n"
         "Undo Delete  — puts back the last batch of rules you removed. "
         "Up to 50 levels deep — older deletes drop off the stack.\n\n"
         "Refresh  — re-reads the live firewall state. The list "
         "auto-refreshes periodically, but hit this if you just made "
         "a change at the command line.\n\n"
         "Export  — saves a timestamped snapshot of every rule to a "
         "file. Good practice before you try anything experimental."),

        ("Important: mode switches overwrite changes",
         "GhostPort has four firewall modes (ISP, ZeroTrust, DoubleHop, "
         "ZHop). Switching modes reloads that mode's full rule set "
         "from `/etc/gpmodes/`.\n\n"
         "This means: **rules you add here do NOT survive a mode switch.** "
         "If you want a rule to be permanent across mode changes, it "
         "needs to go into the .nft profile on disk. Ask Claude or your "
         "technical helper to make that change.\n\n"
         "For temporary rules (\"block this annoying IP for the next "
         "hour\"), adding via Bulkhead is perfect."),

        ("When something looks wrong",
         "• Lots of DROP rules with high packet counts = something is "
         "actively knocking on your door and being refused. Normal on "
         "the internet, not an emergency.\n\n"
         "• A rule you added doesn't show up = try Refresh. If still "
         "missing, the firewall may have rejected the syntax — check "
         "the status bar at the bottom.\n\n"
         "• The status bar shows an \"nft error\" = your last change "
         "had a typo or a conflict. No harm done; the old ruleset is "
         "still in place. Adjust and try again.\n\n"
         "• The list is empty = the firewall isn't running. Open a "
         "terminal and run `sudo gp-mode status` to see what's going on.\n\n"
         "If you get stuck, the safe reset is: `sudo gp-mode isp` in a "
         "terminal. That reloads the ISP profile and cannot lock you out."),

        ("DNS Rules tab — what it is",
         "Separate from the Firewall tab above. GhostPort ships a curated "
         "list of fingerprinting and tracking domains (Samba TV, Mixpanel, "
         "Hotjar, etc.) hard-blocked at the DNS layer via files in "
         "/etc/dnsmasq.d/. This tab lets you manage that list without "
         "editing files by hand.\n\n"
         "Three controls:\n"
         "• Add block (top input + button) — type a domain, click Add, "
         "it's blocked system-wide. Saves to /etc/dnsmasq.d/90-user-blocks"
         ".conf and reloads DNS immediately.\n"
         "• Blocked toggle (middle column) — flip any existing entry on or "
         "off. Changes stage until you click Reload DNS, so you can batch "
         "several edits into one restart.\n"
         "• Reload DNS (bottom button) — restarts pihole-FTL to apply "
         "pending toggle changes. Not needed after Add — that reloads "
         "automatically.\n\n"
         "Layer note: this tab manages dnsmasq address= rules directly. "
         "The Dashboard's \"Allow / Block Domain\" widget writes to "
         "Pi-hole gravity, a different layer. For privacy-product lists "
         "(anti-fingerprint, data brokers) dnsmasq is the right layer; "
         "for casual ad-block additions, either works."),

        ("DNS Rules — why the sites you WANT to visit might show up here",
         "The shipped anti-fingerprint list blocks most third-party "
         "trackers AND two privacy-test sites (browserleaks.com, "
         "amiunique.org) because those sites use fingerprinting to show "
         "YOU what's detectable. The shipped default leaves those two "
         "ALLOWED so you can self-test.\n\n"
         "If you want a stricter posture (block even the self-test sites), "
         "find them in the list and click Blocked → the toggle flips ON. "
         "Click Reload DNS and they'll be blocked going forward.\n\n"
         "Equivalent CLI: `sudo gp-dns-rules allow <domain>` or "
         "`sudo gp-dns-rules block <domain>` then `sudo gp-dns-rules "
         "reload`. Same tool the GUI uses under the hood."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(
            self.HELP_SECTIONS,
            tutorial_path="/opt/phantom/docs/tutorials/bulkhead-tutorial.md",
        )

    def on_theme_changed(self):
        self._apply_css(extra_css=self._extra_css())


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = BulkheadApp()
    app.run()
