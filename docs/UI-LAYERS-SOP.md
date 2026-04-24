# Phantom OS — UI Layers SOP

Phantom OS has **four parallel UI surfaces**. AIs confuse them every session. This document is the map.

Read this before adding, changing, or debugging anything that renders on screen.

---

## 1. The Four Layers

| # | Surface | Owner File | What It Is |
|---|---------|-----------|------------|
| 1 | Desktop icon grid | `/opt/phantom/desktop/gp-desktop-icons.py` | The individually-movable app icons you see on the desktop. Defined in `DESKTOP_APPS`. Each entry: (label, SVG filename, launch command). |
| 2 | Floating widgets | `/opt/phantom/desktop/gp-widgets.py` + `~/.config/phantom/widget-layout.json` | Always-on-top draggable GTK panels (Score, Mode, Tunnel, Theme, etc.). Layout persists in JSON. Enable/disable via Widget Library. |
| 3 | Widget Library gallery | `/opt/phantom/desktop/gp-widget-library.py` | The "App Store" style picker showing all available widgets as cards. Clicking a card toggles enabled state for layer 2. |
| 4 | Right-click menu | `~/.config/labwc/menu.xml` | The labwc root menu. Each entry runs a shell command. |

These layers are **independent**. Enabling a widget in #3 does not add it to #1. Adding an icon to #1 does not affect #4. They must be wired separately.

---

## 2. Decision Tree: "Where Should This Live?"

```
User wants to launch <thing>
│
├─ Is <thing> an always-visible status panel? → Widget (layers 2+3)
│   ├─ Add widget class to gp-widgets.py
│   ├─ Add entry to DEFAULT_LAYOUT in gp-widgets.py (enabled: False)
│   ├─ Add card to WIDGET_DEFINITIONS in gp-widget-library.py
│   └─ Add preview_draw function if needed
│
├─ Is <thing> a GUI app the user opens on demand? → Desktop icon (layer 1)
│   ├─ Add entry to DESKTOP_APPS in gp-desktop-icons.py
│   ├─ Create /opt/phantom/desktop/icons/<name>.svg (follow ICON-POLISH-SOP)
│   └─ Launch command points to the .py/.sh file
│
├─ Is <thing> a TUI script (text in a terminal)? → Right-click menu (layer 4)
│   └─ menu.xml entry MUST wrap with `foot -e` (see §4)
│
└─ Is <thing> a web URL? → Right-click menu (layer 4)
    └─ menu.xml entry uses `chromium --app=URL`
```

---

## 3. Anti-Patterns (Things That Keep Happening)

### 3.1 Reinventing a widget that already exists
**Before** adding a new UI control, grep for its purpose:
```bash
grep -rn "Theme\|picker" /opt/phantom/desktop/ ~/.local/bin/ | grep -vi "__pycache__"
```
Today's bug: fuzzel theme picker was built while `ThemeWidget` already lived in `gp-widgets.py` and `gp-widget-library.py`.

### 3.2 Right-click menu item runs a TUI without `foot -e`
```xml
<!-- WRONG — silently launches with no terminal, appears to "do nothing" -->
<action name="Execute"><command>/path/to/gp-theme menu</command></action>

<!-- RIGHT -->
<action name="Execute"><command>foot -e /path/to/gp-theme menu</command></action>
```

### 3.3 Adding a widget but never surfacing it
Widgets added to `gp-widgets.py` WITHOUT a matching entry in `gp-widget-library.py` are invisible — the user has no way to enable them except editing JSON.

### 3.4 Adding a desktop icon without the SVG
`gp-desktop-icons.py` logs "icon missing" and skips the entry. Always create the SVG first.

### 3.5 Floating widget enabled by default
Never ship a new widget with `"enabled": true`. The user decides via the Widget Library. Anything else clutters their desktop on first boot.

### 3.6 Binding desktop-background mouse actions in rc.xml
`gp-desktop-icons.py` is a full-screen GtkLayerShell surface on the `BACKGROUND` layer anchored to all edges. It captures every pointer event on the "desktop" — labwc's `Root`/`Desktop` mouse context **NEVER FIRES** as long as this app is running.

**Symptom:** you add a `<mousebind button="…" action="Press">` inside `<context name="Root">` in `rc.xml`, reload labwc, and nothing happens. Right-click menu may still work because `gp-desktop-icons.py` handles button 3 itself and forwards to `_show_desktop_menu`.

**Workaround pattern (used for middle-click → window list, 2026-04-20):**
1. Add a keybind in `rc.xml` that invokes the target action:
   ```xml
   <keybind key="W-F12">
     <action name="ShowMenu" menu="client-list-combined-menu" />
   </keybind>
   ```
2. In `gp-desktop-icons.py::_on_press`, synthesize the chord via `wtype`:
   ```python
   if event.button == 2:  # middle
       subprocess.Popen(["wtype", "-M", "logo", "-k", "F12", "-m", "logo"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
       return True
   ```
Keybinds bypass surface input routing, so `W-F12` reaches labwc regardless of which layer-shell surface has the pointer. Empty `<mousebind>` blocks (no child `<action>`) also invalidate the parent context — never leave one "for later."

---

## 4. Right-Click Menu Wrapper Rules

| Thing being launched | Wrapper pattern |
|----------------------|-----------------|
| GTK3 desktop app (`.py` with Gtk.main()) | Bare: `python3 /opt/phantom/desktop/gp-foo.py` |
| TUI bash script (curses, ANSI, `read`) | `foot -e /home/ghostport-admin/.local/bin/gp-foo` |
| TUI that needs sudo wait-for-enter | `foot -e bash -c "sudo gp-foo; read -p 'Enter to close'"` |
| Web URL (dashboard, admin panel) | `chromium --app=http://URL` |
| Fuzzel popup | Bare: `/home/ghostport-admin/.local/bin/gp-something` |

After changing `menu.xml`, run:
```bash
labwc --reconfigure
```

---

## 5. Layer Signals / Reload

| Layer | Reload Mechanism | Notes |
|-------|-----------------|-------|
| Desktop icons | `pkill -f gp-desktop-icons.py && nohup python3 /opt/phantom/desktop/gp-desktop-icons.py &` | Rebuilds entire grid from `DESKTOP_APPS` |
| Widget engine | `kill -USR2 $(cat /tmp/gp-widgets.pid)` | Reloads `widget-layout.json` without restart |
| Widget Library | Restart — `gp-widgets library` | Gallery re-reads layout on launch |
| Right-click menu | `labwc --reconfigure` | Picks up `menu.xml` changes instantly |

`labwc --reconfigure` does NOT propagate to child processes (waybar, widgets, etc.). Signal those separately — see ai-dev-guide §5 "Theme Reload Architecture."

---

## 6. Pre-Ship Checklist (UI Changes)

- [ ] Feature exists on exactly ONE layer (not duplicated in two)
- [ ] If layer 1: SVG icon exists, launches correctly
- [ ] If layer 2: registered in `gp-widgets.py` AND `gp-widget-library.py`
- [ ] If layer 4: correct wrapper (`foot -e` vs bare vs `chromium --app`)
- [ ] Default state = `enabled: False` for any new widget
- [ ] Tested from user's actual entry point (not just by running script directly)
- [ ] `labwc --reconfigure` and/or engine SIGUSR2 fired after change
- [ ] If binding a desktop-background pointer event: routed through `gp-desktop-icons.py` (NOT rc.xml Root context — see §3.6)
