# Phantom OS — Inventory Before Build SOP

**Rule: Before building anything, prove it doesn't already exist.**

**Corollary (2026-04-24): Rule also applies BEFORE PROPOSING anything, not just before coding it.** Proposing features that already ship wastes user trust, not just AI time. Before suggesting a feature wave, grep the repo for each candidate and mark the ones that already exist. The 2026-04-24 proposal round embarrassingly pitched WebRTC STUN block and QUIC toggle as "net-new" when both were shipped months ago (Arsenal #5 + #7, all three non-ISP `.nft` profiles, in the privacy score). Two-minute grep would have caught it.

Most of the waste in this project is AIs reinventing features that ship with the OS. This SOP is the grep checklist that prevents it.

---

## 1. The Five-Second Check

Before ANY new feature, run these searches. If any match exists, read it first.

```bash
# 1. Desktop apps (GUI, launch from desktop grid)
ls /opt/phantom/desktop/gp-*.py

# 2. TUI scripts (terminal tools)
ls ~/.local/bin/gp-* | grep -v '\.'

# 3. System scripts (sudo tooling)
ls /usr/local/bin/gp-*

# 4. Widgets (floating panels)
grep -n "class.*Widget\|WIDGET_ID" /opt/phantom/desktop/gp-widgets.py

# 5. Widget library cards
grep -n '"id":' /opt/phantom/desktop/gp-widget-library.py

# 6. Right-click menu items
grep -oE 'label="[^"]+"' ~/.config/labwc/menu.xml
```

---

## 2. Keyword Grep by Concept

When the user says "build me X", grep for the domain word across the right directories:

```bash
# Example: user asks for a "Theme Picker"
grep -rn -i "theme\|palette\|color.pick" \
    /opt/phantom/desktop/ \
    ~/.local/bin/ \
    ~/.config/labwc/menu.xml \
    2>/dev/null | grep -v __pycache__ | head -30
```

Common domain → file hints:

| User says... | Search for |
|--------------|-----------|
| "file manager", "files" | `Bulkhead`, `gp-bulkhead` |
| "firewall" | `gp-firewall`, `gp-bulkhead` (NOT Bulkhead the app) |
| "IDS", "intrusion" | `gp-ids`, `Crow's Nest`, `gp-crowsnest` |
| "packet", "capture" | `gp-capture`, `Dragnet`, `gp-dragnet` |
| "VPN kill", "killswitch" | `gp-killswitch`, `Anchor`, `gp-anchor` |
| "vault", "encrypted" | `gp-vault`, `Aether Box`, `gp-aetherbox` |
| "theme", "color" | `gp-theme`, `ThemeWidget`, `gp-widget-library` |
| "bandwidth", "usage" | `gp-heatmap`, `Tide Chart`, `gp-tidechart` |
| "MAC", "randomize" | `gp-mac`, `Seadevil`, `gp-seadevil` |

See `project_pirate_names.md` in memory for the full old→new mapping.

---

## 3. Decision Rules

After the grep:

| Grep result | Action |
|-------------|--------|
| Exact match exists | **Use it.** Do not build a competitor. If the existing one has gaps, enhance it — same file, same class. |
| Partial match (similar but different) | Read both. Ask the user which: enhance existing, or add new alongside? |
| No match | You may build. First confirm scope with user. |

**Never** create a second implementation of an existing feature on a different layer (e.g., fuzzel picker competing with a floating widget). See UI-LAYERS-SOP.

---

## 4. When Enhancing an Existing File

1. Read the ENTIRE file first, not just the function you're changing
2. Check git log for recent changes: `cd /opt/phantom && git log --oneline -20 <file>`
3. Check Chamber for anyone else claiming this file
4. Check memory for feedback tied to this file
5. Preserve existing conventions (naming, comment style, CSS classes)

---

## 5. Anti-Patterns

- **Assuming absence from naming** — The app for "firewall" isn't named `gp-firewall.py` (that's the TUI). It's `gp-bulkhead.py`. Always grep, never guess from name.
- **Searching `/opt/phantom/` only** — User-local scripts are in `~/.local/bin/`. System scripts are in `/usr/local/bin/`. Miss either and you'll miss half the fleet.
- **Skipping the widget layer** — Theme picker, client list, ads counter all exist as widgets first, apps second. Always grep `gp-widgets.py` and `gp-widget-library.py`.
- **Trusting memory over code** — Memory says X exists; code may have moved/renamed it. Verify with grep before recommending.

---

## 6. The One-Liner

Paste this to prove you did the inventory check before building:

```bash
echo "=== INVENTORY CHECK: $KEYWORD ===" && \
grep -rn -i "$KEYWORD" /opt/phantom/desktop/ ~/.local/bin/ /usr/local/bin/ ~/.config/labwc/menu.xml 2>/dev/null \
    | grep -v __pycache__ | grep -v ".pyc:" | head -40
```

If the output is empty or only shows matches inside unrelated files, you have permission to build. Otherwise, read the matches first.
