# CHANGELOG.md — Append Only
[2026-04-13] [DOCS] Manager system initialized — CLAUDE.md constitution established
[2026-04-14] [DOCS] ROADMAP.md rewritten to reflect actual system state — 7 phases, 70+ items marked [x], old stale roadmap replaced
[2026-04-14] [DOCS] TASKS.md populated with real queue (9 items) + 4 blocked items requiring the operator
[2026-04-14] [DOCS] HUMAN_TASKS.md populated — 5 decisions pending from the operator
[2026-04-14] [FEAT] Real-time traffic monitoring — backend (/api/tools/bandwidth/rate), frontend panel, OS waybar module (gp-bar-traffic)
[2026-04-14] [FIX] Health guard bash octal bug — fixed (Opus-Prime-II)
[2026-04-14] [FIX] Change-passcode TOTP drop — fixed (Opus-Prime-II)
[2026-04-14] [FEAT] OS Native Tools Sprint — 7 tools built: gp-arsenal, gp-speedtest, gp-clients, gp-diagnostics, gp-dns-leak, gp-activity, gp-security-scan
[2026-04-14] [FEAT] All 7 OS tools integrated into start menu (6 new entries in MONITOR section)
[2026-04-14] [FEAT] gp-bar-traffic waybar module — live network throughput in privacy bar
[2026-04-14] [FEAT] Merged gp-speedtest (Cloudflare edge, --json/--quick) and gp-clients (--json/--watch/--count, MAC randomization detection)
[2026-04-14] [FEAT] Network topology map (topology.js, 732 lines) integrated into Command Deck dashboard
[2026-04-14] [FIX] cloudflared DoH — restart resilience (RestartSec=5, StartLimitBurst=10)
[2026-04-14] [FIX] SSH boot bind failure — systemd drop-in (After=network-online.target tailscaled.service)
[2026-04-14] [FIX] Hardcoded eth0 in gp-health-guard + gp-preflight — now uses dynamic wan_if()
[2026-04-14] [REFACTOR] All OS tools updated to blue (#00bfff) theme
[2026-04-20] [FEAT] Native right-click context menu — fuzzel popup replaced with Gtk.Menu in gp-desktop-icons.py. Nested submenus: Privacy Tools / Monitoring / Terminal Tools. Fixes silent click-eat when fuzzel was broken.
[2026-04-20] [FEAT] Per-icon captions on the desktop grid — DESKTOP_APPS is 4-tuple with subtitle, renders as Sans 7 under the pirate name.
[2026-04-20] [FEAT] Bulkhead ? Help button + 6-section contextual help dialog + 206-line non-technical bulkhead-tutorial.md.
[2026-04-20] [FIX] gp-menu + 4 sibling scripts — fuzzel 1.8.2 flag incompat (--background→--background-color, removed --anchor/--x-margin/--y-margin).
[2026-04-20] [FIX] gp-privacy-report — missing --help guard added (was returning rc=1 with color-code spill).
[2026-04-20] [DOCS] OPERATOR-SOP §2 (mandatory gauntlet after every edit) + §8 rule #18 (no logout/shutdown/reboot/compositor restart without explicit permission).
[2026-04-20] [DOCS] FEATURE-DOCS-SOP — new SOP for plain-English user docs; §1.5 "dev-comment-is-enough" carve-out for polish changes.
[2026-04-20] [DOCS] LightDM greeter theming block added to gp-theme (recolors /usr/share/themes/GhostPort/gtk-3.0/gtk.css on theme change).
[2026-04-21] [SECURITY] GAP #0 sudo hardening — blanket /etc/sudoers.d/ghostport NOPASSWD:ALL replaced with 11 scoped rule blocks in 010_ghostport-hardened. Phase A+B landed live with zero auditd denials across 15 apps + 20 command patterns. Rollback is a single mv.
[2026-04-21] [SECURITY] Tightened /usr/bin/tee /etc/phantom/* wildcard to specific files (device-profiles.json, ids-trends.json). Prevents ghostport-admin from overwriting auth.json / fleet-auth.json.
[2026-04-21] [FEAT] show_help_dialog + make_help_button helpers in gp_app_base.py. 9 desktop apps now use the shared pattern: Bulkhead, Crow's Nest, Anchor, Dragnet, Sonar, Atlas, Quartermaster, Stonefish, Phantom. HELP_SECTIONS is class-level list of (heading, body) tuples.
[2026-04-21] [DOCS] GOLDEN-IMAGE-SOP §6-7 — session-accumulated dev state strip routines (SSH keys, fleet tokens, sudoers backups, AI artifacts, Chamber history, runtime state files) and extended verification checklist.
[2026-04-21] [DOCS] SUDO-HARDENING-PROPOSAL.md, SUDO-TIGHTEN-TEE.md, LUKS-PLAN.md, COMMIT-PLAN.md.
[2026-04-21] [FEAT] gp-golden-strip helper — automates §6 strip on a mounted target with dry-run + validate subcommands.
[2026-04-21] [FEAT] gp-commit-prep helper — interactive walkthrough of the 10-commit backlog for git staging.
[2026-04-21] [FIX] Pi fleet_server repointed to https://api.ghostporttechnologies.com (from http://10.66.66.1:8080 which is refused from wg0 side).
