# Phantom OS — Icon Polish SOP

Standard procedure for redesigning desktop app icons. Read this COMPLETELY before touching any SVG file.

---

## 1. Context

All 15 desktop app icons live in `/opt/phantom/desktop/icons/`. Many still use old generic names from before the pirate rebrand. The icons are simple stroke-art on dark circles — functional but visually flat. The goal is to make each icon more visually appealing with curves, loops, and passthroughs while keeping them recognizable at 64x64px.

---

## 2. Icon-to-App Mapping

The desktop launcher config is in `/opt/phantom/desktop/gp-desktop-icons.py` lines 34-50. The SVG filenames do NOT always match the app names (legacy from rename). Here is the authoritative mapping:

| App Name | SVG File | What It Does | Icon Should Depict |
|----------|----------|--------------|-------------------|
| Crow's Nest | `gp-crowsnest.svg` | IDS dashboard | Ship lookout tower with radar arcs |
| Bulkhead | `gp-bulkhead.svg` | Firewall builder | Reinforced wall/barrier |
| Dragnet | `gp-dragnet.svg` | Packet capture | Fishing net catching data |
| Anchor | `gp-anchor.svg` | VPN kill switch | Ship anchor (hold steady) |
| Aether Box | `gp-strongbox.svg` | Encrypted vault | Locked chest/strongbox |
| Tide Chart | `gp-heatmap.svg` | Bandwidth monitor | Wave/tide patterns |
| Sonar | `gp-watchdog.svg` | Rogue AP scanner | Sonar ping/concentric rings |
| Crew Manifest | `gp-roster.svg` | Client list | Crew roster/people list |
| Atlas | `gp-atlas.svg` | Network topology | Map/compass/network |
| Stonefish | `gp-tripwire.svg` | ARP guard | Hidden spiny fish/trap |
| Seadevil | `gp-seadevil.svg` | MAC randomizer | Ghost/seadevil figure |
| Gangplank | `gp-dock.svg` | USB manager | Plank/dock for connecting |
| Sea Urchin | `gp-vitals.svg` | System health | Spiny urchin/vital signs |
| Logbook | `gp-logbook.svg` | Event log viewer | Open book with entries |
| Quartermaster | `gp-audit.svg` | Security audit | Inspection/checklist/magnifier |

Also in the icons folder but NOT on the desktop launcher (leftovers from rename):
- `gp-sentinel.svg` — duplicate of crowsnest (old name)
- `gp-rampart.svg` — old name for bulkhead
- `gp-wiretap.svg` — old name for dragnet
- `gp-deadbolt.svg` — old name for anchor

These duplicates can be left alone or cleaned up — they are not referenced by anything.

---

## 3. SVG Format Rules

Every icon MUST follow these rules exactly or the theme engine will break:

### 3.1 Dimensions
- `width="128pt" height="128pt" viewBox="0 0 128 128"`
- Design within the 128x128 canvas

### 3.2 Color Format (CRITICAL — theme engine depends on this)
The theme engine in `gp-theme` does literal string replacement on these exact RGB percentage strings. Use ONLY these values:

**Stroke color (accent):**
```
stroke:rgb(100.000000%,60.000000%,26.666667%)
```

**Fill color (background circle):**
```
fill:rgb(3.921569%,2.352941%,2.352941%)
```

**Filled accent elements (dots, circles):**
```
fill:rgb(100.000000%,60.000000%,26.666667%)
```

DO NOT use hex colors, named colors, or different RGB percentage values. The theme engine searches for these exact strings to recolor icons.

### 3.3 Background Circle
Every icon starts with the same dark circle with a faint accent border:
```xml
<path style="fill-rule:nonzero;fill:rgb(3.921569%,2.352941%,2.352941%);fill-opacity:1;stroke-width:2;stroke-linecap:butt;stroke-linejoin:miter;stroke:rgb(100.000000%,60.000000%,26.666667%);stroke-opacity:0.4;stroke-miterlimit:10;" d="M 126 64 C 126 98.242188 98.242188 126 64 126 C 29.757812 126 2 98.242188 2 64 C 2 29.757812 29.757812 2 64 2 C 98.242188 2 126 29.757812 126 64 "/>
```
Copy this line exactly as the first path in every icon.

### 3.4 Opacity for Depth
Use `stroke-opacity` to create visual layers:
- `1.0` — primary structural elements
- `0.7-0.8` — secondary elements (bars, crossbars)
- `0.4-0.5` — supporting details (ropes, shadows, subtle lines)
- `0.3` — faintest background details

### 3.5 Stroke Width Hierarchy
- `4-4.5` — main structural element (mast, wall edge, anchor shaft)
- `2-2.5` — major outlines (basket, frame, hull)
- `1.2-1.5` — detail lines (bars, crossbars, rope)
- `0.8-1.0` — fine detail (textures, faint arcs)

### 3.6 Line Caps
Use `stroke-linecap:round;stroke-linejoin:round` for polished look. The old icons used `butt` — new ones should use `round`.

---

## 4. Design Guidelines

### 4.1 What "Polish" Means
- Replace straight lines with curves (use `Q` quadratic and `C` cubic bezier paths)
- Add decorative loops (small `C` curves along railings, ropes, edges)
- Add depth through layered opacity (foreground elements bright, background dim)
- Add small filled accent elements (dots, circles) as visual anchors
- Make the silhouette recognizable at 64x64px (the rendered size on desktop)

### 4.2 What NOT to Do
- Do NOT change colors — the theme engine handles that
- Do NOT add new color values — only use the exact RGB strings above
- Do NOT make the icon too busy — it renders at 64x64, fine detail disappears
- Do NOT use `<text>` elements — they depend on font availability
- Do NOT use external references (`xlink:href` to other files)
- Do NOT use CSS classes or `<style>` blocks — inline styles only
- Do NOT exceed ~30 path elements per icon — keep SVG lightweight

### 4.3 Curves Reference
```xml
<!-- Straight line -->
<path d="M 40 60 L 88 60" />

<!-- Gentle arc (quadratic bezier — one control point) -->
<path d="M 40 60 Q 64 54 88 60" />

<!-- S-curve (cubic bezier — two control points) -->
<path d="M 40 60 C 50 50 78 70 88 60" />

<!-- Small decorative loop -->
<path d="M 44 38 C 44 34 48 34 48 38" />

<!-- Circle element (filled dot) -->
<circle style="fill:rgb(100.000000%,60.000000%,26.666667%);fill-opacity:1;stroke:none;" cx="75" cy="23" r="2.5"/>
```

---

## 5. Workflow Per Icon

1. **Read the current SVG** — understand what's there
2. **Check the app-to-icon mapping** (Section 2) — know what the icon represents
3. **Sketch the redesign** mentally — identify what curves, loops, and details to add
4. **Write the new SVG** — keep the background circle, redesign the content
5. **Validate the SVG loads:**
   ```bash
   python3 -c "
   from gi.repository import GdkPixbuf
   pb = GdkPixbuf.Pixbuf.new_from_file_at_size('/opt/phantom/desktop/icons/ICON.svg', 64, 64)
   print(f'OK: {pb.get_width()}x{pb.get_height()}')
   "
   ```
6. **Restart desktop icons to see the change:**
   ```bash
   pkill -f gp-desktop-icons.py
   sleep 1
   rm -f /tmp/gp-desktop-icons.lock /tmp/gp-desktop-icons.pid
   export WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0
   nohup python3 /opt/phantom/desktop/gp-desktop-icons.py > /tmp/icons-out.log 2>&1 &
   ```
7. **Get user feedback** before moving to next icon

---

## 6. Validation Checklist

Before declaring an icon done:

- [ ] SVG loads without error at 64x64
- [ ] Background circle is identical to template (Section 3.3)
- [ ] ALL stroke colors use `rgb(100.000000%,60.000000%,26.666667%)` exactly
- [ ] ALL fill colors use `rgb(3.921569%,2.352941%,2.352941%)` or accent `rgb(100.000000%,60.000000%,26.666667%)` exactly
- [ ] No hex colors, no named colors, no other RGB values
- [ ] Uses round line caps (`stroke-linecap:round`)
- [ ] Under 30 path elements
- [ ] Recognizable at 64x64 rendered size
- [ ] Has visual depth (multiple opacity layers)
- [ ] Has curves/loops (not all straight lines)

---

## 7. Icons Remaining

Track progress here:

- [x] `gp-crowsnest.svg` — Crow's Nest (IDS) — DONE
- [ ] `gp-bulkhead.svg` — Bulkhead (Firewall)
- [ ] `gp-dragnet.svg` — Dragnet (Packet Capture)
- [ ] `gp-anchor.svg` — Anchor (Kill Switch)
- [ ] `gp-strongbox.svg` — Aether Box (Vault)
- [ ] `gp-heatmap.svg` — Tide Chart (Bandwidth)
- [ ] `gp-watchdog.svg` — Sonar (Rogue AP Scanner)
- [ ] `gp-roster.svg` — Crew Manifest (Clients)
- [ ] `gp-atlas.svg` — Atlas (Topology)
- [ ] `gp-tripwire.svg` — Stonefish (ARP Guard)
- [ ] `gp-seadevil.svg` — Seadevil (MAC Randomizer)
- [ ] `gp-dock.svg` — Gangplank (USB Manager)
- [ ] `gp-vitals.svg` — Sea Urchin (Health)
- [ ] `gp-logbook.svg` — Logbook (Event Log)
- [ ] `gp-audit.svg` — Quartermaster (Security Audit)

---

## 8. mtime Audit (2026-04-16)

The checkboxes above track *artistic polish*, not just "has the file been edited."
Several icons have been touched in recent sessions for minor fixes (semantic
rework, theme pass, bug hunt) but have not been through a full polish pass.

At audit time **all 15 icon SVGs** had mtimes later than this tracker's last
edit, meaning every icon has been modified at least once since the checkboxes
were written. That makes mtime-based verification unreliable. Use visual
inspection (load each SVG in the desktop grid) to decide whether a `[ ]` entry
is genuinely unpolished or just unchecked.

Known exceptions (touched for reasons other than polish, should stay `[ ]`):
- `gp-bulkhead.svg` — redrawn 2026-04-16 as a warship watertight door (semantic
  match for Firewall). Counts as a completed polish pass.

Do not check off an icon purely on mtime evidence.
