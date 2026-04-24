# Phantom OS — AI Developer Guide

Standard operating procedures for AI agents working on Phantom OS desktop apps and TUI tools. Follow this guide exactly when building, testing, reviewing, or fixing code.

---

## 1. Architecture Overview

Phantom OS runs on a Raspberry Pi 5 (4-core ARM, 8GB RAM). Every resource decision matters. There is no build step, no test framework, no CI/CD — you edit live files and restart services.

### App Types

| Type | Location | Runtime | Example |
|------|----------|---------|---------|
| GTK3 Desktop App | `/opt/phantom/desktop/gp-*.py` | Python3 + GTK3 + GtkLayerShell | gp-bulkhead.py |
| TUI Script | `~/.local/bin/gp-*` | Bash | gp-ids |
| Floating Widget | `/opt/phantom/desktop/gp-widgets.py` | Python3 + GTK3 + GtkLayerShell | Score, Mode, Tunnel |
| System Script | `/usr/local/bin/gp-*` | Bash | gp-mode, gp-dns-switch |
| Web API | `/opt/phantom/ghostport-server.js` | Node.js Express | /api/status |

### Shared Base Class

All GTK apps inherit from `GhostPortApp` in `/opt/phantom/desktop/gp_app_base.py`:
- Theme loading from `/etc/phantom/theme.json` with `derive_colors()`
- CSS generation with accent color derivatives
- `run_async(func, callback)` — thread + GLib.idle_add for safe UI updates
- `run_cmd(cmd, timeout=30)` / `run_sudo(cmd, timeout=30)` — subprocess with timeout
- `poll_start(interval, func)` / `poll_stop(timer_id)` — periodic refresh
- Single-instance via `fcntl.flock` + PID file + SIGUSR1
- `_on_destroy()` cleans up all timers, PID file, lock file

### Naming Convention (Pirate/Ocean Theme)

All app names follow the pirate/nautical theme:
- Crow's Nest (IDS), Bulkhead (Firewall), Dragnet (Packet Capture)
- Anchor (Kill Switch), Aether Box (Vault), Seadevil (MAC Randomizer)
- Stonefish (ARP Guard), Atlas (Network Map), Sonar (Rogue AP Scanner)
- Sea Urchin (Diagnostics), Logbook (Event Log), Quartermaster (Security Scan)
- Crew Manifest (Clients), Tide Chart (Bandwidth), Gangplank (USB Drives)

**Never use old internal names** (Sentinel, Rampart, Wiretap, Deadbolt, etc.) in user-facing strings.

---

## 2. Resource Safety Rules

These are non-negotiable. Violating any of these can brick the Pi or require physical access to recover.

### Subprocess Timeouts

**Every subprocess call MUST have a timeout.** No exceptions.

```python
# Python — ALWAYS use timeout
result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

# Base class methods already enforce this:
stdout, stderr, rc = self.run_cmd(["ip", "link"], timeout=5)
stdout, stderr, rc = self.run_sudo(["nft", "list", "ruleset"], timeout=10)
```

```bash
# Bash — ALWAYS use timeout for external tools
timeout --kill-after=5 15 sudo iw dev wlan0 scan passive
timeout 5 sudo conntrack -L
timeout 30 tshark -i eth0 -c 1000
```

**Known hangers** (tools that can block indefinitely):
- `iw dev wlanX scan` — WiFi driver firmware hang
- `tshark` / `tcpdump` — waits for packets forever
- `nmap` — retransmission timeouts on unresponsive hosts
- `conntrack -L` — kernel lock contention under load

### Memory Limits

| Resource | Limit | Why |
|----------|-------|-----|
| dmesg reads | Pipe through `grep + tail -500` | Kernel buffer can be hundreds of MB |
| Packet captures | `-c 50000` packets, `-a filesize:102400` | tshark has documented EPAN memory leak |
| Capture duration | `-a duration:600` (10 min max) | tshark OOMs in 30-120 min |
| Counter dicts | Cap at 10,000 unique entries | DNS/IP counters grow without bound |
| Event arrays | Rotate every 30 min (bash) or cap at 200-500 entries | Unbounded growth on busy networks |
| pcap file analysis | `-c 50000` on tshark read | Large pcap = large memory |
| Disk writes | Check `os.statvfs()` before capture, min 200MB free | Filling disk = system hang |

### Polling Intervals

| Tool Type | Min Interval | Reason |
|-----------|-------------|--------|
| WiFi scan | 30s | Driver firmware stress, client disruption |
| IDS/flow analysis | 10s | subprocess churn from conntrack/grep chains |
| ARP table | 3-5s interactive, 10-30s daemon | Lightweight but alert fatigue |
| Bandwidth sampling | 5s interactive, 30-60s daemon | /proc/net/dev is free but data files grow |
| Firewall rules | 10s or manual-only | nft list ruleset is expensive on large rulesets |
| Theme check | 3s | Reading one JSON file, negligible |

### Process Cleanup

Every app/script that spawns background processes MUST:

```bash
# Bash: trap ALL exit paths
trap 'kill $(jobs -p) 2>/dev/null; wait' EXIT INT TERM

# Kill specific PIDs in cleanup
cleanup() {
    kill "$LOG_PID" 2>/dev/null || true
    kill "$FLOW_PID" 2>/dev/null || true
    wait "$LOG_PID" 2>/dev/null || true
    wait "$FLOW_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
```

```python
# Python GTK: base class handles timer cleanup via _on_destroy()
# For subprocess.Popen, ALWAYS kill on exit:
def _on_destroy(self, *args):
    if self.capture_proc:
        self.capture_proc.terminate()
        try:
            self.capture_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.capture_proc.kill()
    super()._on_destroy(*args)
```

### Thread Safety

- All threads that update GTK widgets MUST use `GLib.idle_add(callback, data)`
- Use `daemon=True` on all threads so they die with the main process
- The base class `run_async()` handles this correctly — use it

### Protected Infrastructure

Never allow users to delete/modify these via GUI:
- conntrack state rules (established, related)
- Port 4200 (dashboard), Port 22 (SSH), Port 41641 (Tailscale)
- tailscale0 interface rules (management plane)
- loopback interface rules

Use regex matching, not substring matching, for protection checks. Watch for nftables set syntax `{ 4200, 4201 }`.

---

## 3. Bug Hunting Methodology

This is the exact procedure used to find and fix 16 bugs in Bulkhead. Follow it for every app.

### Phase 1: Static Analysis (read the code)

1. Read the entire file top-to-bottom
2. Check for **old name references** (Sentinel, Rampart, Wiretap, etc.)
3. Check every `subprocess` call for timeouts
4. Check every polling interval — is it too fast?
5. Check data structures — can they grow unbounded?
6. Check `_on_destroy` — does it clean up everything?
7. Check error handling — do errors crash the app or fail gracefully?

### Phase 2: Function-Level Testing

Import the module and test every public function with:
- Normal inputs (happy path)
- Edge cases (empty strings, None, zero, negative numbers)
- Boundary values (max ports, long strings, special characters)
- Malformed inputs (wrong types, corrupt data, missing fields)

```python
# Template for testing a function
python3 -c "
import sys; sys.path.insert(0, '/opt/phantom/desktop')
import importlib
mod = importlib.import_module('gp-appname')

# Test each function
result = mod.some_function(test_input)
expected = expected_output
status = 'PASS' if result == expected else 'FAIL'
print(f'{status}: some_function({test_input}) = {result} (expected {expected})')
"
```

### Phase 3: Integration Testing

Test the app against live system data:
- Does it parse actual `nft` output correctly?
- Does it handle real `dmesg` lines?
- Does it survive empty/missing config files?
- Does it work with the current nftables table names (may not be `inet ghostport`)?

### Phase 4: UI Element Verification

Check that all required UI elements exist:
- Correct app name in title bar and headers
- All buttons present (Add, Delete, Export, Refresh, etc.)
- Search/filter functionality
- Status bar with meaningful messages
- Severity color coding
- Scrollable content areas

### Phase 5: Security Testing

- Can users delete protected rules?
- Does the app validate all user inputs?
- Are sudo commands properly scoped?
- Does dry-run validation (`nft -c`) run before actual changes?

### Reporting Format

For each bug found, report:

```
BUG [N]: [Short description]
Severity: CRITICAL / HIGH / MEDIUM / LOW
Location: file.py, function_name(), line ~NNN
What happens: [Describe the failure]
Expected: [What should happen]
Fix: [Suggested fix]
```

Severity guide:
- **CRITICAL**: Can brick the Pi, lock out user, or cause data loss
- **HIGH**: Causes crashes, wrong security decisions, or major UX failures
- **MEDIUM**: Wrong display, missing features, or degraded functionality
- **LOW**: Cosmetic issues, minor UX problems, code quality

---

## 4. Fixing Bugs

### Before You Edit

1. **Read the file first** — understand the full context
2. **Syntax check after every edit**: `python3 -c "import py_compile; py_compile.compile('file.py', doraise=True)"`
3. **Bash syntax check**: `bash -n script.sh`

### Fix Order

1. CRITICAL bugs first (security, lockouts, crashes)
2. Name/branding fixes (wrong app name)
3. Logic fixes (wrong behavior)
4. Missing features
5. Cosmetic/UX improvements

### Verify Fixes

After fixing, re-run the exact test that found the bug:

```python
# Re-run the failing test
result = mod.fixed_function(test_input)
status = 'PASS' if result == expected else 'STILL BROKEN'
print(f'{status}: ...')
```

Then restart the app and verify it launches:

```bash
pkill -f "python3.*gp-appname" 2>/dev/null
sleep 1
setsid python3 /opt/phantom/desktop/gp-appname.py </dev/null &>/dev/null &
sleep 2
pgrep -f gp-appname && echo "running"
```

---

## 5. Common Patterns

### External Daemon State — Canonical Helpers (ADDED 2026-04-21)

Any filesystem path **owned by another daemon** (Pi-hole, hostapd, tailscale, unbound, systemd journal, etc.) is mutable state that can move or change format when that daemon is updated. Never hardcode such paths across multiple files — centralize the read in a single helper with a staleness guard.

**Canonical helpers in this project:**

| Data | Helper | Notes |
|---|---|---|
| DHCP leases | `sudo gp-leases` | Emits dnsmasq-format lines. Auto-discovers `/etc/pihole/dhcp.leases` vs legacy `/var/lib/misc/dnsmasq.leases`. Falls back to `ip neigh show dev wlan0` synthesis when the lease file is stale >4h. `--source` returns `pihole`, `dnsmasq-legacy`, `arp-fallback`, or `none`. `--json` for structured output. |

**Rule:** if you find yourself typing `/var/lib/misc/dnsmasq.leases`, `/etc/pihole/dhcp.leases`, or any `open()` / `cat` / `fs.readFileSync` against a non-GhostPort-owned path, stop and check for a canonical helper first. If no helper exists and the path is referenced in more than one place, create one (bash script in `/usr/local/bin/gp-*`, sudoers entry if it needs root read, documented here).

**When fixing a call site, sweep all of them:**
```bash
grep -rln "<the-hardcoded-path>" /opt/phantom/desktop/ ~/.local/bin/ /opt/phantom/ghostport-server.js 2>/dev/null | grep -v "__pycache__\|\.bak\|\.pyc"
```
Latent "wrong file" bugs surface app-by-app as users notice each UI — fix all call sites in the same session, or the next screen the user opens is broken in the same way.

**Staleness guard pattern (implemented in `gp-leases`):** check `stat -c '%Y'` against `date +%s`; if delta > some threshold, log a warning to stderr and fall back to a live data source (ARP, running processes, etc.). Serving silently-stale data is worse than an error.

**Preflight cross-check pattern:** add a `gp-preflight` check that compares the cached/file-based count to a live-kernel source. Example: `dhcp-leases` check compares lease-file mtime + count vs `ip neigh show dev wlan0` count; flags stale-but-ARP-has-clients as a failure. This is the alarm that prevents repeat incidents.

### nftables Rule Parsing

nft outputs rules in two formats. Always try JSON first, fall back to text:

```python
# JSON (preferred — structured, includes handles)
stdout, _, rc = self.run_sudo(["nft", "-j", "-a", "list", "ruleset"], timeout=10)
if rc == 0:
    data = json.loads(stdout)

# Text fallback
stdout, _, rc = self.run_sudo(["nft", "-a", "list", "ruleset"], timeout=10)
```

Watch for:
- Set syntax: `{ 53, 67 }` — ports/interfaces in curly braces
- Quoted interface names: `iifname "wlan0"` — split() breaks these
- Counter stats: `counter packets 12345 bytes 678900` — strip before comparing
- Log prefixes containing action words: `log prefix "GhostPort-DROP"` is NOT a drop rule

Use `_tokenize_rule()` (regex-based) instead of `split()` for rule expressions:
```python
def _tokenize_rule(self, rule_expr):
    tokens = []
    for m in re.finditer(r'"[^"]*"|\S+', rule_expr):
        tokens.append(m.group(0))
    return tokens
```

### Action Detection

Check the LAST token for the verdict, not substrings anywhere in the expression:
```python
# WRONG — "log prefix 'DROP'" matches "drop"
if "drop" in expr.lower():

# RIGHT — check the actual verdict at the end
clean = re.sub(r'log\s+prefix\s+"[^"]*"', 'log', expr)
tokens = clean.strip().split()
if tokens and tokens[-1].lower() == "drop":
```

### Theme Integration

All GTK apps automatically get themed via the base class. To add app-specific CSS:
```python
def _extra_css(self):
    c = self.colors
    return f"""
    .my-custom-class {{
        color: {c['accent']};
        background-color: rgba({c['r']},{c['g']},{c['b']}, 0.1);
    }}
    """
```

Available color keys: `accent`, `text`, `bg`, `success`, `danger`, `warning`, `info`, `dim`, `r`, `g`, `b`

### Theme Reload Architecture

The theme engine (`~/.local/bin/gp-theme`) is template-based: it copies default configs from `~/.config/phantom/theme-defaults/`, runs `sed` to replace the default green (`#39ff8f`) with derived palette colors, then triggers reloads.

**How each component picks up color changes:**

| Component | Reload Mechanism | Latency |
|-----------|-----------------|---------|
| Waybar (top bar) | `pkill -SIGUSR2 waybar` — reloads CSS from disk | Instant |
| labwc (window decorations) | `labwc --reconfigure` — re-reads themerc-override | Instant |
| GTK3 apps (desktop apps) | Poll `/etc/phantom/theme.json` every 3s via base class | ≤3s |
| Desktop widgets | Poll `/etc/phantom/theme.json` every 3s | ≤3s |
| Foot terminal | Reads config on launch — open a new terminal | Next launch |
| TUI scripts | Source `gp-theme-colors.sh` on launch — run script again | Next launch |
| Fuzzel menus | Script files patched directly by gp-theme | Next invocation |
| SVG icons | Files patched directly by gp-theme | Next render |

**Critical lesson (April 2026 bug):** `labwc --reconfigure` does **NOT** re-run the autostart file. It only reloads labwc's own configs (rc.xml, themerc-override, keybinds). To reload other apps after a theme change, you must signal them directly:
- Waybar: `pkill -SIGUSR2 waybar` (reload CSS, no restart)
- Mako: `makoctl reload` (reload notification styles)
- GTK apps: No signal needed — they poll theme.json

**Do NOT restart waybar with pkill + relaunch** — that risks duplicate bar instances. SIGUSR2 is the correct approach.

**Template files** live in `~/.config/phantom/theme-defaults/`. If a template gets corrupted or overwritten with a non-default color, the sed replacements will silently fail (nothing matches `#39ff8f`). If theme changes stop working, check that templates still contain the default green values.

### Hardcoded-accent hunt (standalone apps and widgets) — ADDED 2026-04-21

Apps derived from `GhostPortApp` get themed via the base class. Standalone GTK apps and widgets (GtkLayerShell overlays, legacy utilities that predate the base class) routinely **hardcode the default accent** and never update on theme change. Three forms to watch for:

1. **CSS hex strings** — `#39ff8f` literal in a CSS string
2. **Cairo RGB floats** — `cr.set_source_rgba(0.224, 1.0, 0.56, α)` (0-1 normalized form of `#39ff8f`)
3. **RGBA in CSS** — `rgba(57, 255, 143, α)` (0-255 form of the same)

Hunt command:
```bash
grep -rln "#39ff8f\|57, *255, *143\|0\.224, *1\.0, *0\.56" \
    /opt/phantom/desktop/ ~/.local/bin/ghostport-* 2>/dev/null \
    | grep -vE "\.pyc|\.bak"
```

A match is only a **real bug** if the file does NOT also read `/etc/phantom/theme.json`:

```bash
for f in <candidates>; do
    reads=$(grep -c "theme\.json\|read_theme\|read_accent" "$f")
    hardcoded=$(grep -c "#39ff8f" "$f")
    echo "$f — reads=$reads hardcoded=$hardcoded"
done
```
`reads=0 hardcoded>0` is a genuine bug. `reads>0 hardcoded>0` usually means the hardcoded value is a fallback (correct).

**Intentional exceptions** — do NOT "fix":
- `gp_app_base.py` — one `#39ff8f` is the fallback in `read_theme()`.
- `gp-widget-library.py` preset swatch list — the "Ghost Green" entry is a user-selectable preset.
- `gp-icon-gen.py` — SVG templates with default green; `gp-theme` sed-recolors them per ICON-POLISH-SOP §3.2.

**Fix pattern** for CSS: add `read_accent_hex()` → `themed_css()` helpers, call in `_apply_css`, add a 3-second `GLib.timeout_add_seconds` poll to re-apply on change (see `ghostport-shortcuts.py` for reference).

**Fix pattern** for Cairo: add module-level `_accent_rgb()` returning an `(r,g,b)` float tuple from theme.json, replace `cr.set_source_rgba(0.224, 1.0, 0.56, α)` with `cr.set_source_rgba(*_accent_rgb(), α)` (see `gp-widget-library.py` for reference).

**Fix pattern** for API-based theme fetches that fall back to default on failure: read theme.json directly BEFORE the API call (see `ghostport-widget.py::get_theme()` for reference). theme.json is the source of truth anyway.

---

## 6. Safety Checklist (Pre-Ship)

Before declaring any app/fix complete:

- [ ] `python3 -c "import py_compile; py_compile.compile('file.py', doraise=True)"` passes
- [ ] App launches without errors
- [ ] No old names in user-facing strings
- [ ] All subprocess calls have timeouts
- [ ] No unbounded data structures
- [ ] Process cleanup in `_on_destroy()`
- [ ] Protected rules can't be deleted
- [ ] User inputs are validated
- [ ] Works with current live nftables tables (not hardcoded table names)
- [ ] Theme colors apply correctly
- [ ] Single-instance works (launching twice raises existing window)
- [ ] `--help` guard on TUI scripts (prevents zombie processes)

---

## 7. Fortinet Reference Architecture

GhostPort's resource management is modeled after FortiGate appliance patterns:

| FortiGate Feature | GhostPort Equivalent |
|---|---|
| Conserve mode (82/88/95% RAM thresholds) | Not yet implemented — future watchdog |
| Session table fixed-size allocation | conntrack max = 262144 (could reduce to 32768) |
| Packet sniffer 10-min timeout + count limit | tshark `-a duration:600 -a filesize:102400` |
| Dedicated monitor radio for rogue AP scanning | Use wlan1 USB dongle when available, passive-only on wlan0 |
| DAI (Dynamic ARP Inspection) at switch level | ARP table polling every 3-5s with gateway MAC tracking |
| Atomic policy changes | `nft -c` dry-run validation before applying rules |
| Circular buffer for bandwidth data | Daily data files with line count rotation |

---

## 8. File Locations Quick Reference

```
/opt/phantom/desktop/
  gp_app_base.py          # Shared GTK base class
  gp-icon-gen.py           # SVG icon generator
  gp-desktop-icons.py      # Desktop icon grid (GtkLayerShell)
  gp-widgets.py            # Floating overlay widgets
  gp-bulkhead.py           # Firewall builder
  gp-crowsnest.py          # IDS dashboard
  gp-dragnet.py            # Packet capture
  gp-anchor.py             # Kill switch
  gp-aetherbox.py          # Encrypted vault
  gp-tidechart.py          # Bandwidth visualization
  gp-sonar.py              # Rogue AP scanner
  gp-crewmanifest.py       # Client list
  gp-seadevil.py            # MAC randomizer
  gp-gangplank.py          # USB manager
  gp-atlas.py              # Network topology
  gp-stonefish.py          # ARP guard
  gp-seaurchin.py          # Diagnostics
  gp-logbook.py            # Event log
  gp-quartermaster.py      # Security scan
  icons/                   # Generated SVG icons

/home/ghostport-admin/.local/bin/
  gp-menu                  # Start menu (fuzzel dmenu)
  gp-ids                   # IDS TUI
  gp-firewall              # Firewall TUI
  gp-capture               # Packet capture TUI
  gp-rogue-scan            # Rogue AP scanner TUI
  gp-arp-guard             # ARP guard TUI
  gp-heatmap               # Bandwidth heatmap TUI
  (50+ more scripts)

/etc/phantom/            # Config files (JSON)
/etc/gpmodes/              # nftables firewall profiles
/opt/phantom/docs/       # Documentation
```
