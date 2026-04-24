#!/usr/bin/env python3
"""Generate SVG icons for all GhostPort desktop apps using cairo."""

import cairo
import math
import os
import json

ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")
THEME_FILE = "/etc/phantom/theme.json"
SIZE = 128

def read_accent():
    try:
        with open(THEME_FILE) as f:
            return json.load(f).get("color", "#39ff8f").lstrip("#")
    except Exception:
        return "39ff8f"

def hex_rgb(h):
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255

def make_icon(name, draw_func, accent):
    """Create a SIZE x SIZE SVG icon."""
    path = os.path.join(ICON_DIR, f"gp-{name}.svg")
    surface = cairo.SVGSurface(path, SIZE, SIZE)
    cr = cairo.Context(surface)
    r, g, b = hex_rgb(accent)

    # Dark circle background
    cr.arc(SIZE / 2, SIZE / 2, SIZE / 2 - 2, 0, 2 * math.pi)
    cr.set_source_rgb(0.04, 0.06, 0.04)
    cr.fill_preserve()
    cr.set_source_rgba(r, g, b, 0.4)
    cr.set_line_width(2)
    cr.stroke()

    # Draw the symbol
    cr.set_source_rgb(r, g, b)
    draw_func(cr, SIZE, r, g, b)

    surface.finish()
    print(f"  {path}")

# ── Icon Symbols ─────────────────────────────────────────────────────

def draw_sentinel(cr, s, r, g, b):
    """Pirate ship crow's nest — lookout platform atop a mast."""
    cx, cy = s / 2, s / 2

    # Mast (vertical pole from bottom to platform)
    cr.set_line_width(4)
    cr.move_to(cx, cy + 38)
    cr.line_to(cx, cy - 8)
    cr.stroke()

    # Platform base (wooden barrel rim — slightly curved trapezoid)
    cr.set_line_width(2.5)
    # Bottom of barrel
    cr.move_to(cx - 22, cy - 4)
    cr.line_to(cx + 22, cy - 4)
    cr.stroke()
    # Barrel sides (tapered inward toward bottom)
    cr.move_to(cx - 22, cy - 4)
    cr.line_to(cx - 18, cy + 12)
    cr.stroke()
    cr.move_to(cx + 22, cy - 4)
    cr.line_to(cx + 18, cy + 12)
    cr.stroke()
    # Barrel bottom
    cr.move_to(cx - 18, cy + 12)
    cr.line_to(cx + 18, cy + 12)
    cr.stroke()
    # Barrel band (horizontal plank across middle)
    cr.set_line_width(1.5)
    cr.move_to(cx - 20, cy + 4)
    cr.line_to(cx + 20, cy + 4)
    cr.stroke()

    # Railing posts (vertical staves around the rim)
    cr.set_line_width(2)
    for x_off in [-22, -11, 0, 11, 22]:
        cr.move_to(cx + x_off, cy - 4)
        cr.line_to(cx + x_off, cy - 22)
        cr.stroke()

    # Top railing (horizontal rail connecting posts)
    cr.set_line_width(2.5)
    cr.move_to(cx - 22, cy - 22)
    cr.line_to(cx + 22, cy - 22)
    cr.stroke()
    # Middle railing
    cr.set_line_width(1.5)
    cr.move_to(cx - 22, cy - 13)
    cr.line_to(cx + 22, cy - 13)
    cr.stroke()

    # Spyglass (telescope resting on railing — lookout's tool)
    cr.set_line_width(2)
    cr.move_to(cx + 6, cy - 22)
    cr.line_to(cx + 20, cy - 34)
    cr.stroke()
    # Spyglass lens (wider end)
    cr.set_line_width(3.5)
    cr.move_to(cx + 19, cy - 33)
    cr.line_to(cx + 24, cy - 37)
    cr.stroke()

    # Rigging lines (ropes from mast top to sides — V shape)
    cr.set_line_width(1)
    cr.set_source_rgba(r, g, b, 0.5)
    cr.move_to(cx, cy - 8)
    cr.line_to(cx - 30, cy + 30)
    cr.stroke()
    cr.move_to(cx, cy - 8)
    cr.line_to(cx + 30, cy + 30)
    cr.stroke()

def draw_rampart(cr, s, r, g, b):
    """Brick wall — firewall."""
    cx, cy = s / 2, s / 2
    cr.set_line_width(2)
    # Battlements
    for i in range(5):
        x = cx - 30 + i * 15
        cr.rectangle(x, cy - 32, 12, 10)
        cr.stroke()
    # Wall rows
    for row in range(3):
        y = cy - 22 + row * 16
        offset = 8 if row % 2 else 0
        for col in range(4):
            x = cx - 30 + offset + col * 18
            w = min(18, cx + 32 - x)
            if x < cx + 30 and w > 2:
                cr.rectangle(x, y, w - 2, 14)
                cr.stroke()

def draw_wiretap(cr, s, r, g, b):
    """Signal waves — packet capture."""
    cx, cy = s / 2, s / 2
    # Center dot
    cr.arc(cx, cy, 5, 0, 2 * math.pi)
    cr.fill()
    # Concentric arcs
    cr.set_line_width(3)
    for radius in [16, 28, 40]:
        cr.arc(cx, cy, radius, -math.pi * 0.4, math.pi * 0.4)
        cr.stroke()

def draw_deadbolt(cr, s, r, g, b):
    """Lock — kill switch."""
    cx, cy = s / 2, s / 2
    # Lock body
    cr.rectangle(cx - 20, cy - 5, 40, 30)
    cr.set_line_width(3)
    cr.stroke()
    # Shackle
    cr.arc(cx, cy - 5, 15, math.pi, 2 * math.pi)
    cr.stroke()
    # Keyhole
    cr.arc(cx, cy + 8, 5, 0, 2 * math.pi)
    cr.fill()
    cr.rectangle(cx - 2, cy + 12, 4, 8)
    cr.fill()

def draw_strongbox(cr, s, r, g, b):
    """Box with lock — vault."""
    cx, cy = s / 2, s / 2
    # Box
    cr.rectangle(cx - 28, cy - 18, 56, 40)
    cr.set_line_width(3)
    cr.stroke()
    # Lid
    cr.move_to(cx - 28, cy - 18)
    cr.line_to(cx - 22, cy - 28)
    cr.line_to(cx + 22, cy - 28)
    cr.line_to(cx + 28, cy - 18)
    cr.stroke()
    # Lock diamond
    cr.save()
    cr.translate(cx, cy + 2)
    cr.rotate(math.pi / 4)
    cr.rectangle(-6, -6, 12, 12)
    cr.fill()
    cr.restore()

def draw_heatmap(cr, s, r, g, b):
    """Grid of squares — heatmap."""
    cx, cy = s / 2, s / 2
    grid = 5
    cell = 10
    gap = 2
    start_x = cx - (grid * (cell + gap)) / 2
    start_y = cy - (grid * (cell + gap)) / 2
    intensities = [
        [0.2, 0.4, 0.6, 0.8, 1.0],
        [0.3, 0.5, 0.9, 0.7, 0.4],
        [0.1, 0.8, 1.0, 0.9, 0.6],
        [0.4, 0.6, 0.7, 0.5, 0.3],
        [0.2, 0.3, 0.4, 0.6, 0.8],
    ]
    for row in range(grid):
        for col in range(grid):
            intensity = intensities[row][col]
            cr.set_source_rgba(r, g, b, intensity)
            x = start_x + col * (cell + gap)
            y = start_y + row * (cell + gap)
            cr.rectangle(x, y, cell, cell)
            cr.fill()

def draw_watchdog(cr, s, r, g, b):
    """Radar sweep — rogue AP scanner."""
    cx, cy = s / 2, s / 2 + 5
    cr.set_line_width(2)
    # Radar circles
    for radius in [12, 24, 36]:
        cr.arc(cx, cy, radius, math.pi, 2 * math.pi)
        cr.stroke()
    # Sweep line
    cr.set_line_width(3)
    cr.move_to(cx, cy)
    angle = -math.pi * 0.3
    cr.line_to(cx + 36 * math.cos(angle), cy + 36 * math.sin(angle))
    cr.stroke()
    # Blip
    cr.arc(cx + 18, cy - 16, 4, 0, 2 * math.pi)
    cr.fill()

def draw_roster(cr, s, r, g, b):
    """People icons — connected clients."""
    cx, cy = s / 2, s / 2
    # Three person silhouettes
    for offset in [-20, 0, 20]:
        x = cx + offset
        cr.arc(x, cy - 12, 7, 0, 2 * math.pi)
        cr.fill()
        cr.arc(x, cy + 10, 12, math.pi, 2 * math.pi)
        cr.fill()

def draw_seadevil(cr, s, r, g, b):
    """Ghost shape — MAC randomizer."""
    cx, cy = s / 2, s / 2
    # Ghost body
    cr.arc(cx, cy - 8, 22, math.pi, 2 * math.pi)
    cr.line_to(cx + 22, cy + 20)
    # Wavy bottom
    for i in range(4):
        x1 = cx + 22 - i * 11
        x2 = cx + 22 - (i + 0.5) * 11
        cr.curve_to(x1, cy + 20, x2, cy + 30, cx + 22 - (i + 1) * 11, cy + 20)
    cr.close_path()
    cr.set_line_width(3)
    cr.stroke()
    # Eyes
    cr.set_source_rgb(r, g, b)
    cr.arc(cx - 8, cy - 6, 4, 0, 2 * math.pi)
    cr.fill()
    cr.arc(cx + 8, cy - 6, 4, 0, 2 * math.pi)
    cr.fill()

def draw_dock(cr, s, r, g, b):
    """USB plug — drive manager."""
    cx, cy = s / 2, s / 2
    # USB body
    cr.rectangle(cx - 10, cy - 25, 20, 40)
    cr.set_line_width(3)
    cr.stroke()
    # Connector
    cr.rectangle(cx - 14, cy + 15, 28, 12)
    cr.stroke()
    # Internal contacts
    cr.rectangle(cx - 5, cy - 18, 4, 12)
    cr.fill()
    cr.rectangle(cx + 2, cy - 18, 4, 12)
    cr.fill()

def draw_atlas(cr, s, r, g, b):
    """Network nodes — topology."""
    cx, cy = s / 2, s / 2
    nodes = [(cx, cy - 25), (cx - 25, cy + 10), (cx + 25, cy + 10), (cx, cy + 30)]
    # Edges
    cr.set_line_width(2)
    for i, (x1, y1) in enumerate(nodes):
        for x2, y2 in nodes[i + 1:]:
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()
    # Nodes
    for x, y in nodes:
        cr.arc(x, y, 7, 0, 2 * math.pi)
        cr.fill()

def draw_tripwire(cr, s, r, g, b):
    """Stonefish (Synanceia) — ARP guard. Stocky craggy ambush predator."""
    cx, cy = s / 2, s / 2 + 6  # shift down for spine room

    # Seafloor — uneven rocky substrate
    cr.set_source_rgba(r, g, b, 0.18)
    cr.set_line_width(1.5)
    cr.move_to(cx - 42, cy + 20)
    cr.line_to(cx - 30, cy + 19)
    cr.line_to(cx - 22, cy + 21)
    cr.line_to(cx - 10, cy + 19)
    cr.line_to(cx + 4, cy + 20)
    cr.line_to(cx + 18, cy + 19)
    cr.line_to(cx + 30, cy + 21)
    cr.line_to(cx + 42, cy + 19)
    cr.stroke()

    # Body — stocky compact shape, nearly as tall as wide, jagged edges
    cr.set_source_rgb(r, g, b)
    cr.set_line_width(2.5)

    # Start at lower jaw (mouth faces up)
    cr.move_to(cx - 24, cy + 10)
    # Steep forehead — almost vertical
    cr.curve_to(cx - 26, cy + 2, cx - 27, cy - 6, cx - 24, cy - 12)
    # Brow ridges (lumpy bumps above eyes)
    cr.line_to(cx - 22, cy - 14)
    cr.line_to(cx - 18, cy - 12)       # saddle dip
    cr.line_to(cx - 14, cy - 14)       # second brow bump
    # Dorsal ridge — irregular bumpy line
    cr.curve_to(cx - 8, cy - 13, cx - 2, cy - 14, cx + 4, cy - 13)
    cr.line_to(cx + 6, cy - 14)        # dorsal bump
    cr.curve_to(cx + 12, cy - 12, cx + 18, cy - 10, cx + 22, cy - 7)
    cr.line_to(cx + 24, cy - 8)        # notch
    cr.curve_to(cx + 28, cy - 4, cx + 30, cy + 2, cx + 30, cy + 6)
    # Rounded paddle tail (NOT forked)
    cr.curve_to(cx + 34, cy + 2, cx + 38, cy - 2, cx + 40, cy - 4)
    cr.curve_to(cx + 42, cy + 2, cx + 42, cy + 10, cx + 40, cy + 14)
    cr.curve_to(cx + 38, cy + 16, cx + 34, cy + 14, cx + 30, cy + 10)
    # Anal fin
    cr.curve_to(cx + 26, cy + 14, cx + 22, cy + 16, cx + 18, cy + 15)
    cr.line_to(cx + 20, cy + 18)
    cr.line_to(cx + 14, cy + 16)
    # Flat belly on substrate
    cr.curve_to(cx + 4, cy + 18, cx - 8, cy + 18, cx - 16, cy + 16)
    # Back to lower jaw
    cr.curve_to(cx - 20, cy + 14, cx - 23, cy + 12, cx - 24, cy + 10)
    cr.close_path()
    cr.stroke_preserve()
    cr.set_source_rgba(r, g, b, 0.15)
    cr.fill()

    # Dorsal fin membrane + 13 spines
    cr.set_source_rgba(r, g, b, 0.12)
    # Low ragged membrane
    cr.move_to(cx - 20, cy - 13)
    spine_positions = []
    for i in range(13):
        sx = cx - 20 + i * 3.8
        base_y = cy - 13 + abs(i - 6) * 0.4
        spine_positions.append((sx, base_y))
        cr.line_to(sx, base_y - 4)
        cr.line_to(sx + 1.9, base_y)
    cr.line_to(cx + 28, cy - 6)
    cr.line_to(cx - 20, cy - 13)
    cr.close_path()
    cr.fill()

    # 13 thick spine needles
    cr.set_source_rgb(r, g, b)
    cr.set_line_width(2)
    for i, (sx, base_y) in enumerate(spine_positions):
        dist = abs(i - 6)
        h = 14 - dist * 1.2
        if h < 6:
            h = 6
        cr.move_to(sx, base_y)
        cr.line_to(sx, base_y - h)
        cr.stroke()

    # Eyes — small, on top of head, in bony ridges
    cr.set_source_rgb(r, g, b)
    cr.arc(cx - 20, cy - 13, 3, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(0.04, 0.06, 0.04)
    cr.arc(cx - 20, cy - 13, 1.5, 0, 2 * math.pi)
    cr.fill()

    # Mouth — large, near-vertical, upturned (signature feature)
    cr.set_source_rgb(r, g, b)
    cr.set_line_width(2.5)
    # Upper jaw
    cr.move_to(cx - 24, cy + 10)
    cr.curve_to(cx - 27, cy + 4, cx - 27, cy - 2, cx - 24, cy - 6)
    cr.stroke()
    # Lower jaw — protruding underbite
    cr.set_line_width(2)
    cr.move_to(cx - 24, cy + 10)
    cr.curve_to(cx - 28, cy + 8, cx - 30, cy + 4, cx - 28, cy)
    cr.stroke()

    # Skin flaps/tassels hanging from jaw (camouflage)
    cr.set_line_width(1.2)
    cr.set_source_rgba(r, g, b, 0.5)
    for tx, ty, tl in [(-26, 6, 5), (-24, 9, 4), (-22, 11, 3), (-28, 3, 4)]:
        cr.move_to(cx + tx, cy + ty)
        cr.curve_to(cx + tx - 2, cy + ty + tl, cx + tx + 1, cy + ty + tl + 1,
                    cx + tx - 1, cy + ty + tl + 2)
        cr.stroke()

    # Pectoral fins — broad fans splayed sideways on seafloor
    cr.set_source_rgb(r, g, b)
    cr.set_line_width(1.5)
    # Left pectoral
    lbx, lby = cx - 14, cy + 12
    l_tips = [(lbx - 18, cy + 14), (lbx - 20, cy + 17), (lbx - 16, cy + 18), (lbx - 10, cy + 18)]
    for rx, ry in l_tips:
        cr.move_to(lbx, lby)
        cr.line_to(rx, ry)
        cr.stroke()
    cr.set_source_rgba(r, g, b, 0.12)
    cr.move_to(lbx, lby)
    for rx, ry in l_tips:
        cr.line_to(rx, ry)
    cr.close_path()
    cr.fill()

    # Right pectoral
    cr.set_source_rgb(r, g, b)
    rbx, rby = cx + 10, cy + 12
    r_tips = [(rbx + 10, cy + 18), (rbx + 16, cy + 18), (rbx + 20, cy + 17), (rbx + 18, cy + 14)]
    for rx, ry in r_tips:
        cr.move_to(rbx, rby)
        cr.line_to(rx, ry)
        cr.stroke()
    cr.set_source_rgba(r, g, b, 0.12)
    cr.move_to(rbx, rby)
    for rx, ry in r_tips:
        cr.line_to(rx, ry)
    cr.close_path()
    cr.fill()

    # Gill slit behind head
    cr.set_source_rgba(r, g, b, 0.4)
    cr.set_line_width(1.5)
    cr.move_to(cx - 10, cy - 4)
    cr.curve_to(cx - 12, cy, cx - 12, cy + 6, cx - 10, cy + 10)
    cr.stroke()

    # Warty nodules all over body
    cr.set_source_rgba(r, g, b, 0.3)
    warts = [
        (-8, -8, 2.5), (0, -9, 2.0), (8, -7, 2.2), (16, -4, 1.8),
        (-4, -2, 2.0), (4, -1, 2.5), (12, 0, 2.0), (20, 2, 1.8),
        (-6, 5, 1.8), (2, 6, 2.2), (10, 5, 1.6), (18, 8, 2.0),
        (-14, 2, 1.6), (6, 10, 1.8), (24, 6, 1.5), (22, -2, 2.0),
    ]
    for dx, dy, sz in warts:
        cr.arc(cx + dx, cy + dy, sz, 0, 2 * math.pi)
        cr.fill()

def draw_vitals(cr, s, r, g, b):
    """Heartbeat line — diagnostics."""
    cx, cy = s / 2, s / 2
    cr.set_line_width(3)
    points = [
        (cx - 35, cy), (cx - 20, cy), (cx - 12, cy - 20),
        (cx - 4, cy + 15), (cx + 4, cy - 25), (cx + 12, cy + 10),
        (cx + 20, cy), (cx + 35, cy)
    ]
    cr.move_to(*points[0])
    for x, y in points[1:]:
        cr.line_to(x, y)
    cr.stroke()

def draw_logbook(cr, s, r, g, b):
    """Stacked lines — event log."""
    cx, cy = s / 2, s / 2
    cr.set_line_width(2)
    # Page outline
    cr.rectangle(cx - 22, cy - 28, 44, 56)
    cr.stroke()
    # Lines
    for i in range(5):
        y = cy - 18 + i * 12
        w = 30 if i % 2 == 0 else 22
        cr.move_to(cx - 14, y)
        cr.line_to(cx - 14 + w, y)
        cr.stroke()

def draw_audit(cr, s, r, g, b):
    """Checkmark in shield — security scan."""
    cx, cy = s / 2, s / 2
    # Shield
    cr.move_to(cx, cy - 30)
    cr.line_to(cx + 28, cy - 18)
    cr.line_to(cx + 28, cy + 8)
    cr.curve_to(cx + 28, cy + 28, cx, cy + 38, cx, cy + 38)
    cr.curve_to(cx, cy + 38, cx - 28, cy + 28, cx - 28, cy + 8)
    cr.line_to(cx - 28, cy - 18)
    cr.close_path()
    cr.set_line_width(3)
    cr.stroke()
    # Checkmark
    cr.set_line_width(4)
    cr.move_to(cx - 12, cy + 2)
    cr.line_to(cx - 2, cy + 14)
    cr.line_to(cx + 16, cy - 10)
    cr.stroke()


# ── Main ─────────────────────────────────────────────────────────────

ICONS = {
    "sentinel": draw_sentinel,
    "rampart": draw_rampart,
    "wiretap": draw_wiretap,
    "deadbolt": draw_deadbolt,
    "strongbox": draw_strongbox,
    "heatmap": draw_heatmap,
    "watchdog": draw_watchdog,
    "roster": draw_roster,
    "seadevil": draw_seadevil,
    "dock": draw_dock,
    "atlas": draw_atlas,
    "tripwire": draw_tripwire,
    "vitals": draw_vitals,
    "logbook": draw_logbook,
    "audit": draw_audit,
}

def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    accent = read_accent()
    print(f"Generating {len(ICONS)} icons with accent #{accent}...")
    for name, draw_func in ICONS.items():
        make_icon(name, draw_func, accent)
    print(f"Done. Icons saved to {ICON_DIR}/")

if __name__ == "__main__":
    main()
