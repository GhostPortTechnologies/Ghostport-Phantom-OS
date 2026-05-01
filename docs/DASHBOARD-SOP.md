# Phantom OS — Dashboard SOP

Standard procedure for working on the web dashboard (Node.js Express server + vanilla JS SPA). Read this in full before editing anything under `/opt/phantom/public/` or the dashboard routes in `/opt/phantom/ghostport-server.js`.

The lessons here were paid for in debugging time — each section is here because a past Claude tripped on it.

---

## 1. Dashboard Layout

| File | Role | Size |
|---|---|---|
| `/opt/phantom/ghostport-server.js` | Express 5 server, API routes, auth middleware | ~bigger, modular |
| `/opt/phantom/public/index.html` | Main SPA — **4,164 lines** of mixed HTML/CSS/inline JS, includes the theme engine, status polling, and SPA bootstrap | fragile, see §4 |
| `/opt/phantom/public/topology.js` | Network topology SVG visualization | ~800 lines |
| `/opt/phantom/public/arsenal.js` | Security tools module | n/a |
| `/opt/phantom/public/bg-effects.js` | Background animation | n/a |
| `/opt/phantom/public/login.html` + `login.js` | Passcode login page | n/a |
| `/opt/phantom/public/sw.js` | Service worker (PWA) | n/a |

**Note (2026-05-01, T-0073):** A previous extraction effort (`v1.4 NIST sprint`) split inline JS out into `app.js` but the matching `<script src>` tag was never added to index.html. The file existed as dead code for months. It has been deleted. The theme engine + status polling + SPA bootstrap all live in the **inline `<script>` block in `index.html`** (starting around line 2530). Don't go looking for them in app.js — it's gone.

Ports:
- **4200** — plain HTTP
- **4201** — HTTPS
- Both redirect unauthenticated requests to `/login.html`.

---

## 2. Auth-Gates Static Files (Major Debugging Gotcha)

**Every static asset is session-gated.** `GET /topology.js`, `/arsenal.js`, even `/index.html` return **302 → `/login.html`** without a valid `gp_session` cookie.

This means **you cannot `curl` a static file to verify your edit is live** until you log in first. If you skip the login step you'll chase a seadevil bug — thinking the server isn't serving the new file when really you're just fetching a 33-byte redirect stub.

### Correct flow for `curl`-based verification

```bash
# 1. Log in, store cookie
PC='GP-XXXX-XXXX-XXXX'   # get from sudo gp-passcode reset if unknown
curl -s -c /tmp/gpck.txt -o /dev/null \
  -H 'Content-Type: application/json' \
  -d "{\"passcode\":\"$PC\"}" \
  http://127.0.0.1:4200/api/auth/login

# 2. Use the cookie for subsequent requests
curl -s -b /tmp/gpck.txt -o /tmp/topology.js http://127.0.0.1:4200/topology.js
diff -q /tmp/topology.js /opt/phantom/public/topology.js
```

### Login has a 5-attempt lockout

Looks like "3 attempts remaining" in the error — that's **remaining**, not total. A script that retries on 401 will lock the user out of their own dashboard. Verify auth **once**, reuse the cookie, and on 401 stop and investigate (wrong passcode, session invalidated by a recent `gp-passcode reset`, or service restart cleared the keystore).

### `/api/status` is intentionally unauthenticated

Status endpoint returns mode/tunnels/IP/score unauth — you can probe this without a session to sanity-check the server is up.

---

## 3. Theming — Every Visual Element Must Use CSS Variables

The dashboard has a live theme picker (color picker + RGB breathing mode). It works by rewriting CSS custom properties on `:root`:

```
--green, --green-dim, --green-glow, --green-20, --green-33, --green-13
--bg, --bg2, --bg3
--border
--text, --text-dim, --text-faint
--red, --amber
```

Theme engine lives in `index.html`'s inline `<script>` block — `applyThemeVars()`, `setColorTheme()`, `hueToTheme()` are defined inline (search by name). RGB breathing mode updates these at ~30 fps via `requestAnimationFrame`. The `THEME_COLORS` palette object and the `packColors` theme-pack swatches are also inline.

### Rules for any new visual component

- **Never hardcode hex or rgba for theme-able colors.** Use `var(--green)`, `var(--text)`, etc.
- **In CSS / `<style>` blocks** — `var(--green)` works everywhere natively.
- **In SVG** — `fill="var(--green)"` as an **attribute does NOT resolve**. You must use either:
  - Inline `style="fill: var(--green)"` on the element, OR
  - A `<style>` block with a class, then put the class on the SVG element.

  The class-based approach is preferred. See `topology.js` (post-2026-04-16 rewrite) for the pattern: one big `<style>` block defining `.topo-node-body`, `.topo-line`, etc., all SVG elements just get `class=` attributes.

- **Canvas components** must poll `getComputedStyle(document.documentElement).getPropertyValue('--green')` on every paint — canvas doesn't cascade from CSS vars.

- **Keep `#00bcd4` (control-plane blue) and `#ff4444`-style hardcoded** when the color is a **semantic differentiator**, not a theme variant (control vs data plane, error state that should stand out regardless of theme). Use judgment.

### Failure mode: silent theme-skip

If your new component uses hardcoded hex:
- It still renders correctly at default green.
- Theme picker rewrites `--green` on `:root` — your component doesn't update.
- User reports "colors don't change with the theme picker" (this exact report triggered the 2026-04-16 topology rewrite).

### Quick audit before shipping a visual change

```bash
# Look for hardcoded #39ff8f (default green) and rgba(57,255,143,...) in the file
grep -nE '#39ff8f|rgba\(57, ?255, ?143' /opt/phantom/public/YOURFILE.js
```

Any match is a bug unless it's inside a semantic-color constant.

---

## 4. `index.html` is Structurally Fragile

4,164 lines of mixed HTML, inline `<style>`, inline `<script>`, and inline event handlers. The March 24 CSP incident happened when a sub-agent was asked to "migrate inline handlers to addEventListener" and broke the dashboard.

### Rules

- **Prefer creating a new external `.js` file** over editing the big inline `<script>` block in index.html (the SPA core, ~lines 2530-4036). New external files load alongside `arsenal.js` / `topology.js` — pattern is in §4 below. Edits to the inline block need extra care.
- **Never delegate CSP or inline-handler changes to a sub-agent.** Do them yourself, verify every change by hand. (From `feedback_csp_failure.md`.)
- **Never touch index.html unless explicitly asked.** From `feedback_no_ui_changes.md` and the user's standing rule. If adding a visual component, put the JS in its own file and add a single `<script src="yourfile.js">` tag — don't bloat the inline block.
- **The `.bak-*` files alongside index.html are intentional rollback points** — leave them alone.

### New `.js` files need a `<script>` tag

The server serves any file under `/public/` — dropping a new `.js` file in there is not enough. You must also add:
```html
<script src="yourfile.js"></script>
```
near the existing script tags (around line 2324). If your code references a global (`window.YourThing`) from the inline script block below, put the tag **above** that block so load order works.

---

## 5. Edit Workflow — Safe Pattern

Pure-JS files have no build step and no test framework. The only guardrails are the ones you add.

```bash
# 1. Back up before edit
cp /opt/phantom/public/topology.js /opt/phantom/public/topology.js.bak-$(date +%Y%m%d-%H%M%S)

# 2. Edit

# 3. Syntax-check
node -c /opt/phantom/public/topology.js

# 4. (optional) Verify server serves the new file — see §2 for auth'd curl

# 5. Tell the user to hard-refresh (Ctrl+Shift+R) — browser cache will bite you otherwise
```

### When does the server need a restart?

| Change | Restart? |
|---|---|
| Any file under `/public/*` (HTML/CSS/JS/images) | **No** — Express serves from disk per-request, browser refresh is enough |
| `ghostport-server.js` (routes, middleware, API) | **Yes** — `sudo systemctl restart ghostport` |
| `/etc/phantom/auth.json` (passcode hash) | Restart handled automatically by `gp-passcode` |
| `/etc/gpmodes/*.nft` | No (applied via `gp-mode`, not server) |

Restarting is not free — it invalidates all active sessions. Don't do it for static file edits.

### SPA cache

The service worker (`sw.js`) aggressively caches assets. Hard-refresh (Ctrl+Shift+R / Cmd+Shift+R) bypasses the cache. A plain refresh can serve stale JS.

---

## 6. Repo Sync (if user asks)

Live files are NOT auto-synced to the repo. If the user explicitly asks you to prep a commit:

```bash
cp /opt/phantom/public/topology.js /opt/phantom/public/     # (already in repo path)
# For scripts / nftables / systemd:
cp /usr/local/bin/gp-* /opt/phantom/scripts/
cp /etc/gpmodes/*.nft  /opt/phantom/etc/gpmodes/
cp /etc/systemd/system/ghostport*.service /opt/phantom/systemd/
```

**Never auto-sync without being asked** — see `feedback_no_repo_sync.md`. The user reviews changes manually.

---

## 7. Quick Sanity Checks

Before declaring a dashboard change complete:

- [ ] `node -c /opt/phantom/public/FILE.js` passes
- [ ] `grep -nE '#39ff8f|rgba\(57, ?255, ?143' FILE.js` returns nothing (or only in semantic-color constants you intended)
- [ ] If you added a new `.js` file: `<script src="...">` tag is in `index.html` in the right load order
- [ ] If you touched an auth-gated static: verified through the browser after a hard-refresh, OR the auth'd `curl` flow above
- [ ] No new inline event handlers added to `index.html` (CSP will reject them)
- [ ] `.bak-<timestamp>` backup exists for any file rewrite

---

## 8. Known Dashboard Quirks (Reference)

- **`/api/status` is unauthenticated** — intentional, for liveness probing.
- **`/api/arsenal/clients` and `/api/system/health` require auth** — the topology map's client list + temp display depend on these, so if the session dies the map quietly stops updating those fields.
- **`visibilitychange` suspends topology polling** — when the tab is hidden, topology.js stops its 3 pollers; resumes on focus. Don't confuse this for a bug.
- **Rainbow theme mode** streams CSS var updates at 30 fps via `requestAnimationFrame` — any per-paint cost you add to visual components multiplies by 30.
