# Phantom OS — Operator Standard Operating Procedures

## Who This Is For
You — the human operator managing a squad of AI dev instances building and maintaining Phantom OS on a Raspberry Pi 5 privacy router.

---

## 1. Squad Management

### Starting a Session
1. Open a terminal and check system health first:
   ```bash
   gp-preflight            # system health check
   sudo gp-mode status     # current firewall mode
   htop                    # CPU/memory baseline
   ```
2. Check Chamber for any messages from prior sessions:
   ```bash
   chamber                 # or curl http://localhost:4242/api/messages | python3 -m json.tool | tail -80
   ```
3. Brief your lead Claude on goals for the session. Be specific: "Ship 3 tools" beats "make progress."

### Roles That Work
| Role | What They Do | Why It Matters |
|------|-------------|----------------|
| **Senior/Lead** | Reviews code, grades quality, posts directives | Catches structural problems juniors miss |
| **Builder (opus-prime)** | Architecture, complex tools | Owns the hardest tasks |
| **Bug Hunter (testbot)** | Cross-testing, syntax checks | Finds what builders are blind to |
| **Module Dev (wraith)** | Parallel tool builds | Multiplies throughput |

### Claim-Before-Build Protocol
Before anyone starts work, they post to Chamber:
```
CLAIMING: Task #X — gp-toolname
```
If two instances claim the same task, **first claim wins**. This prevented duplicate work in Phase 3.

### When to "Put Someone in Check"
Ask your senior to review when:
- A phase completes (quality gate)
- Someone reports "done" but you haven't verified
- CPU usage spikes unexpectedly (zombie process incident)
- You see conflicting claims in Chamber

---

## 2. Quality Gates

### Every Tool Must Have
- [ ] `--help` early exit guard (BEFORE any `while true` loop)
- [ ] Theme colors sourced from `/usr/local/lib/gp-theme-colors.sh`
- [ ] Zero hardcoded ANSI color codes
- [ ] Start menu entry in `gp-menu`
- [ ] `bash -n` syntax check passes
- [ ] First-run intro screen
- [ ] Dependency auto-install prompt

### MANDATORY: Run the Gauntlet Before Reporting Done

After every script/config/theme edit, Claude must **auto-run the matching gauntlet** and report results as part of the completion message. Do not ask the user to run it. Do not wait to be prompted. This is non-negotiable — the user has delegated QA *into* the work, not *after* it.

| Edited | Required checks |
|---|---|
| Python (`.py`) | `gp-qa <files>` — pylint/mypy/vulture must be 0 |
| Bash script | `bash -n`, `shellcheck`, `--help` guard present, theme-colors sourced |
| GTK CSS | `Gtk.CssProvider().load_from_path()` parse check |
| lightdm / systemd / `.conf` keyfile | `GLib.KeyFile().load_from_file()` (NOT Python configparser — chokes on `%`) |
| nftables profile | `nft -c -f <file>` dry-run — NEVER apply without this |
| SVG icon | `GdkPixbuf.Pixbuf.new_from_file_at_size(path, 64, 64)` loads clean |
| systemd unit | `systemd-analyze verify <unit>` |
| JSON config | `python3 -m json.tool <file>` |

**Also verify service-user read perms** for any asset referenced by a daemon (lightdm, ghostport, etc.): `sudo -u <svcuser> test -r <path>`.

**Concurrency** (per PYTHON-QA-SOP §9.5): default to plain `gp-qa` (quality only). Only the designated security sweeper runs `--security`/`--paranoid`. Check `uptime`; skip if load > 4× cores.

**If a blocker is found:** fix it, re-run the gauntlet, then report. Never hand fix-work back to the user.

**Rule origin:** 2026-04-20 — operator directive after the LightDM greeter theming work. *"If you add the gauntlet as a mandatory process each time you create a script that will stop you from having to do a lot of fixing and I will stop second-guessing you."*

### Senior Review Checklist
Run this after every phase:
```bash
# 1. All scripts exist and parse
for f in ~/.local/bin/gp-*; do bash -n "$f" && echo "OK: $f" || echo "FAIL: $f"; done

# 2. No hardcoded colors
grep -rn '\\033\[' ~/.local/bin/gp-* | grep -v theme-colors

# 3. All have --help guards
for f in ~/.local/bin/gp-*; do grep -q '\-\-help' "$f" || echo "MISSING --help: $f"; done

# 4. Start menu coverage
diff <(ls ~/.local/bin/gp-* | xargs -I{} basename {} | sort) \
     <(grep -oP 'gp-\w+' ~/.local/bin/gp-menu | sort -u)
```

### Grading Scale (What We Used)
- **A**: Zero issues, self-organized, self-reviewed
- **B+**: Minor issues found, good structure
- **B**: Functional but needed cleanup
- **C**: Structural problems, missing integrations

---

## 3. Chamber Communication

### How to Use Chamber
```bash
# Read messages
chamber                              # interactive TUI
curl -s http://localhost:4242/api/messages | python3 -m json.tool

# Post a message
curl -s -X POST http://localhost:4242/api/messages \
  -H "Content-Type: application/json" \
  -d '{"username":"operator","text":"Phase 3 is GO. Check task board."}'
```

### What to Post
- **Task boards** with assignments and sizes (S/M/L/XL)
- **Directives** ("all scripts must source theme-colors.sh")
- **Incident reports** (zombie process, CPU spike, bridge failure)
- **Reviews and grades** (public accountability improves quality)
- **Status updates** for EC2 Claude coordination

### What NOT to Post
- Passwords, API keys, tokens
- Lengthy code dumps (reference file paths instead)
- Duplicate messages (check before posting)

---

## 4. EC2 Bridge Communication

### Current State
The `gp-bridge` CLI defaults to the fleet Tailscale endpoint `http://10.66.66.1:8080`
(from `/etc/phantom/fleet-auth.json`). It does **not** point at localhost —
that line in an earlier revision of this SOP was incorrect. If `fleet-auth.json`
is missing or empty, the CLI falls back to the 10.66.66.1 default; you can override
per-invocation with `--host <url>`.

If `gp-bridge` doesn't reach the EC2 control plane, the usual curl workaround is:
```bash
curl -s -X POST https://api.ghostporttechnologies.com/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"sender":"pi-claude","text":"Status update: Phase 3 complete, 9 tools shipped."}'
```

### When to Bridge
- After completing a phase (summary + grade)
- When EC2 Claude asks questions (check bridge inbox)
- Before/after major infrastructure changes
- When you need EC2-side action (blog deploy, cert renewal)

### Bridge Etiquette
- Always leave a **bootstrap briefing** so a new Claude instance can self-onboard
- Include specific numbers (tools shipped, bugs fixed, grades)
- Flag anything that needs EC2 action vs. info-only

---

## 5. Incident Response

### CPU Spike
```bash
htop                                  # identify the process
ps aux --sort=-pcpu | head -5         # top CPU consumers
uptime                                # confirm load > 4× core count = overload
kill <PID>                            # kill if it's a zombie gp-* script
```

**Root cause patterns (in priority order — check each before killing):**

1. **Parallel `gp-qa --security` runs during a squad session** (2026-04-17 incident). gitleaks and trivy are whole-tree scanners — one instance is fine, four in parallel flatten the Pi (load 21+ on a 4-core). See PYTHON-QA-SOP §9.5. Emergency stop:
   ```bash
   pkill -9 gitleaks trivy osemgrep
   ```
   Load drops in ~30s. Then direct the squad: one designated security sweeper only.

2. **TUI script `while true` loops without `--help` guards**. Scripts called with `--help` spin forever. Add the guard (see feedback_help_guard in memory).

3. **Unreaped zombies (state Z)** — harmless, don't kill the parents (often waybar / gp-desktop-icons, both critical UI). `SIGCHLD` doesn't help; they clear on parent restart.

### IDS Alert
```bash
gp-ids                               # check the dashboard
sudo journalctl -u ghostport -n 50   # recent logs
sudo nft list ruleset | grep -i drop # what's being blocked
```
**Triage**: Most alerts are normal network noise (mDNS, SSDP, ARP). Check source IP against your DHCP range. If it's from 192.168.50.x, it's a client. If from upstream (192.168.0.x), it's your ISP network.

### Mode Switch Failure
```bash
sudo gp-mode status                  # what mode are we in?
sudo gp-mode isp                     # safe fallback (no rollback timer)
sudo nft list ruleset | head -5      # verify nftables loaded
```
**NEVER** leave the system in a mixed mode state. If unsure, go to ISP mode first.

### Keyring/Desktop Annoyances
```bash
# Kill and disable gnome-keyring permanently
pkill -f gnome-keyring
# Already disabled via Hidden=true in ~/.config/autostart/gnome-keyring-*.desktop
```

---

## 6. Daily Workflow

### Morning Startup
1. `gp-preflight` — system health
2. Check Chamber — any overnight messages
3. Check bridge — any EC2 requests
4. `htop` — baseline CPU/memory
5. Set session goals

### During Development
1. Assign tasks via Chamber with clear sizes (S/M/L)
2. Let builders work, check Chamber periodically
3. When a phase completes, request senior review
4. Fix structural issues before moving to next phase
5. Update memory after significant milestones

### End of Session
1. Senior review of all work done
2. Post status to Chamber
3. Send bridge update to EC2 Claude
4. Update memory files (`session_YYYY_MM_DD.md`)
5. Run `gp-preflight` one last time

---

## 7. Memory Management

### What to Save
- Session summaries (what shipped, grades, incidents)
- User feedback that changes future behavior
- External references (where to find things)
- Project context that isn't in code or git

### What NOT to Save
- Code patterns (read the code instead)
- Git history (use git log)
- Passwords or secrets
- Temporary task state

### How to Save
```
~/.claude/projects/-home-ghostport-admin/memory/
├── MEMORY.md              ← index file (keep under 200 lines)
├── session_2026_04_15.md  ← session logs
├── feedback_*.md          ← behavioral corrections
├── project_*.md           ← ongoing project context
└── reference_*.md         ← external system pointers
```

---

## 8. Rules (Learned the Hard Way)

1. **Never touch UI/frontend files** unless explicitly asked (March 24 incident)
2. **Never delegate CSP changes** to sub-agents (March 24 incident)
3. **Never run /init** — it overwrites CLAUDE.md
4. **Never store passwords** in memory files
5. **Always fact-check** before publishing claims
6. **Always add --help guards** before any while loop
7. **Always source theme-colors.sh** — never hardcode ANSI
8. **Always validate nftables** with `nft -c -f` before applying
9. **Never stop Tailscale** — it's your remote lifeline
10. **ISP mode is the safe fallback** — no rollback timer needed
11. **Build first, market second** — never overstate capabilities
12. **Update blog AND demo** after every feature/bug session
13. **`labwc --reconfigure` does NOT restart other apps** — it only reloads labwc's own theme/keybinds. To reload waybar CSS: `pkill -SIGUSR2 waybar`. Never assume a compositor reconfigure propagates to child processes (April 16 bug).
14. **`gp-passcode show` no longer exists.** Plaintext passcodes are not stored — only the scrypt hash. Use `sudo gp-passcode reset` for a new random one or `sudo gp-passcode set` to pick one. CLAUDE.md examples that still show `gp-passcode show` are stale.
15. **Dashboard login has a 5-attempt lockout.** Scripts that blindly retry on HTTP 401 will lock the user out of their own device. Verify auth once, reuse the cookie, stop and investigate on 401. The error message shows "N attempts remaining" — that's *remaining*, not total.
16. **Static files under `/opt/phantom/public/` don't need a service restart.** Express serves them from disk per-request — a browser hard-refresh (Ctrl+Shift+R) is enough. Only restart `ghostport.service` for edits to `ghostport-server.js` or server-side config. Restart invalidates all active sessions — don't do it casually.
17. **Dashboard static files are auth-gated.** `curl` of `/topology.js`, `/app.js`, even `/index.html` returns a 302 to `/login.html` without a session cookie — so "my edit isn't being served" is almost always "you forgot to log in first". See `DASHBOARD-SOP.md` §2 for the auth'd verification flow.
18. **Never log out, shut down, or reboot the Pi without explicit permission.** That includes `sudo reboot`, `sudo shutdown`, `sudo systemctl reboot|poweroff`, `loginctl terminate-user`, killing the compositor, or restarting `lightdm` / `labwc` while the user is logged in. Losing the desktop session drops active terminals, background jobs, SSH agents, and any uncommitted editor state. If a change genuinely needs a reboot or greeter restart to take effect, say so and **ask** — do not do it yourself. Same rule applies to `ghostport.service` restarts that invalidate dashboard sessions (see rule #16) and to any `sudo systemctl restart` of compositor / display-manager / networking units.

---

## 9. Command Quick Reference

```bash
# System
gp-preflight              # health check
sudo gp-mode status       # current mode
sudo gp-mode isp          # safe fallback

# Squad
chamber                   # check messages
gp-bridge                 # EC2 comms (defaults to 10.66.66.1:8080; --host to override)

# Desktop
gp-menu                   # start menu (fuzzel)
gp-theme menu             # change theme
gp-quick-settings         # settings panel

# Monitoring
gp-ids                    # intrusion detection
gp-capture                # packet capture
gp-heatmap                # bandwidth usage
gp-rogue-scan             # evil twin detection

# Security
gp-killswitch             # VPN kill switch
gp-firewall               # nftables builder
gp-vault                  # encrypted storage
gp-pass                   # password manager
gp-tor                    # Tor proxy

# Troubleshooting
htop                      # process monitor
sudo journalctl -u ghostport -f  # server logs
sudo nft list ruleset     # firewall state
ip -br addr               # network interfaces
```

---

## 10. Scaling the Squad

### What Worked (Phase 3 = A-)
- Clear task board with sizes posted to Chamber
- Claim-before-build prevents conflicts
- Bug hunter cross-tests everything
- Senior reviews at phase boundaries
- Progressive quality improvement (B → B+ → A-)

### What to Watch For
- Duplicate claims (first-claim-wins rule)
- Hardcoded colors sneaking back in
- Missing start menu entries
- Scripts without --help guards
- CPU zombies from infinite loops

### Future Growth
- Start menu is at 50+ items — consider subcategories or search
- Hardware abstraction layer (Task #18) needed before porting to non-Pi hardware
- 59 tools still need `/opt/phantom/docs/` documentation (see `docs/DOC-BACKLOG.md` for the prioritised list; previous SOP revision said "5" — outdated)
- `gp-bridge` CLI defaults to the 10.66.66.1 Tailscale endpoint; override with `--host` if needed.
