# THEME-ENGINE-SOP

How the Phantom OS theme system works, the booby-traps it contains, and the
patterns every desktop component must follow to repaint correctly.

Origin: 2026-05-06, after the Widget Library was reverted to a stale 930-line
version by `gp-theme`'s template-copy loop, and the dock's right-side App
Drawer button stuck on a previous accent color.

---

## 1. The engine

`/home/ghostport-admin/.local/bin/gp-theme` is the **only** correct way to
change the Phantom OS accent color. It does several things in one shot:

- Validates the requested hex color
- Derives the full palette (dim / bright / text / bg / border / etc.)
- Patches every static-color config from templates in
  `~/.config/phantom/theme-defaults/`:
  - `~/.config/waybar/style.css`
  - `~/.config/foot/foot.ini`
  - `~/.config/labwc/themerc-override`
  - `~/.config/swaylock/config`
  - `~/.config/gtklock/style.css`
  - `~/.config/geany/colorschemes/ghostport.conf`
  - `~/.local/bin/gp-menu`, `gp-mode-menu`, `gp-window-switcher`
  - `~/.config/phantom/desktop/ghostport-shortcuts.py` (legacy slot, see §3)
- Sed-replaces accent + bg colors in every `*.svg` in
  `/opt/phantom/desktop/icons/`
- Optionally installs the LightDM greeter GTK theme (root, sudo-only)
- Writes `/etc/phantom/theme.json`
- Reloads waybar (`pkill -SIGUSR2 waybar`), labwc (`labwc --reconfigure`),
  mako (`makoctl reload`)

`gp-theme` runs without sudo for the user-owned bulk; only the LightDM
greeter step needs root and is skipped with a hint when not available.

The Widget Library calls `gp-theme <hex>` directly via subprocess. Do not
write `/etc/phantom/theme.json` from app code without also calling the
engine — Python apps will repaint via polling, but waybar/foot/labwc/SVGs
will not.

---

## 2. The template-copy bomb

The `apply_all` function in `gp-theme` contains a loop:

```bash
for pyfile in <files>; do
    cp "$TEMPLATE_DIR/$pyfile" "$target"
    sed -i ... "$target"
done
```

**Every file in this loop is destroyed and rewritten from the template on
every theme change.** This is intentional for static-color files, but lethal
for theme-aware files (those that subscribe to `theme.json` polling via
`gp_app_base.read_theme_color()` or equivalent).

If a theme-aware file ends up in this loop:
- The template version (often months stale) overwrites the live rewrite
- Sed then corrupts any literal hex codes in the code (e.g., the
  `THEME_PRESETS` swatch list in the Widget Library)

**Rule: a desktop script lives in exactly one lane.**

| Lane | Where colors come from | gp-theme loop? | Template in theme-defaults? |
|------|------------------------|----------------|------------------------------|
| Theme-aware | `gp_app_base.read_theme_color()` polling | NEVER | NEVER |
| Static-color | Hardcoded `#39ff8f` literals | YES | YES |

When making a script theme-aware:
1. Remove it from the gp-theme loop
2. **Delete its template** at `~/.config/phantom/theme-defaults/<file>.py`
3. Verify with the live-fire test (§5)

---

## 3. Sentinel-file pattern for toggleable widgets

For overlays that can be toggled on/off across reboots (e.g. the keyboard
shortcuts overlay), use a sentinel file that the autostart line checks:

```bash
# In ~/.config/labwc/autostart:
sleep 1 && [ ! -f ~/.config/ghostport/<widget>-disabled ] \
        && python3 /opt/ghostport/desktop/<widget>.py &
```

The toggle UI:
- **ON**: `os.unlink(<sentinel>)` (ignore FileNotFoundError); spawn the
  process if not already running
- **OFF**: `open(<sentinel>, "w").close()`; `pkill -f <widget>.py`

State survives reboot, shutdown, and unexpected power loss because the
sentinel file is on persistent SD, and the autostart re-evaluates it on
every login.

Live example: `~/.config/ghostport/shortcuts-disabled` for the shortcuts
overlay; the toggle lives in the Widget Library WIDGETS section.

---

## 4. The fixed-icon caching gotcha

Theme-aware GTK apps with **hardcoded icon buttons** (logo, app drawer,
power, etc.) load their pixbufs once at `_build_ui` time. When a theme
change rewrites the SVG file on disk, those cached pixbufs do not refresh
automatically.

Symptom: theme switches to blue, but the dock's right-side App Drawer
button stays purple from the previous theme.

The dock-style fix:

```python
def _poll_theme(self):
    cur = read_accent()
    if cur != self.accent:
        self.accent = cur
        self._apply_css()
        self._rebuild_items()       # rebuilds user-pinned items
        self._reload_fixed_icons()  # rebuilds hardcoded buttons too
    return True

def _reload_fixed_icons(self):
    for path, button in [(LOGO_PATH, self.logo_btn),
                         (os.path.join(ICON_DIR, "gp-appdrawer.svg"),
                          self.appdrawer_btn)]:
        if os.path.isfile(path):
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(path, SIZE, SIZE)
                button.set_image(Gtk.Image.new_from_pixbuf(pix))
            except Exception:
                pass
```

Audit check for any GTK app with hardcoded buttons: confirm theme change
forces a pixbuf reload of every button that points at a file under
`/opt/phantom/desktop/icons/`.

---

## 5. Live-fire test for theme propagation

Mandatory after any change to `gp-theme` or any theme-aware file:

```bash
BEFORE=$(sha256sum /opt/ghostport/desktop/<file>.py | awk '{print $1}')
gp-theme 00d4ff   # any non-default
AFTER=$(sha256sum /opt/ghostport/desktop/<file>.py | awk '{print $1}')
[[ "$BEFORE" == "$AFTER" ]] && echo "PASS: theme-aware file untouched" \
                            || echo "FAIL: file was rewritten"
gp-theme reset
```

If the file should be theme-aware but is being rewritten, it's still in the
gp-theme loop or has a template. Both must be removed.

---

## 6. Where things live (theme-related)

| Path | What |
|------|------|
| `/etc/phantom/theme.json` | Current accent, polled by all apps |
| `/usr/local/bin` *(none)* | (theme engine is in user's local bin) |
| `~/.local/bin/gp-theme` | The bash engine |
| `~/.config/phantom/theme-defaults/` | Static-color templates |
| `/opt/phantom/desktop/icons/*.svg` | Re-colored on every theme change |
| `~/.config/ghostport/<widget>-disabled` | Sentinel for toggleable widgets |
| `~/.config/labwc/autostart` | Boot-time guards for sentinel lookups |

---

## 7. When to read this SOP

- Adding a new desktop GTK app: pick theme-aware vs static-color upfront
- Modifying `gp-theme` (adding to / removing from any sed/copy loop)
- Reports of "theme stuck on previous color" or "won't repaint"
- Adding a new always-on overlay (use the sentinel pattern)
- Touching the Widget Library or any other theme-control surface
