# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GhostPort is a Raspberry Pi 5 privacy router with a web command deck. It manages network privacy modes by applying nftables firewall profiles, routing traffic through WireGuard/Tailscale tunnels, and filtering DNS via Pi-hole. Licensed under Elastic License 2.0 (source available, not open source).

## Running & Restarting

```bash
sudo systemctl restart ghostport        # Restart Node.js server (port 4200 HTTPS)
sudo systemctl status ghostport          # Check server status
sudo journalctl -u ghostport -f          # Tail server logs
sudo systemctl restart ghostport-discord # Restart Discord bot
sudo gp-mode status                      # Check current firewall mode
sudo gp-mode isp|zerotrust|doublehop|zhop  # Switch mode from CLI
sudo gp-mode confirm                     # Confirm mode (cancel rollback timer)
sudo gp-passcode show                    # View device passcode
sudo gp-passcode reset                   # Generate new passcode & restart service
sudo gp-dns-switch on|off|status         # Toggle encrypted DNS (cloudflared DoH)
```

No build step, no test framework, no linter. Edit files directly and restart the service. Bash scripts must pass `shellcheck`. nftables rules must be tested on Pi OS Bookworm 64-bit. JavaScript follows standard ES6+ conventions.

The server runs as the `ghostport-admin` user. Passwordless sudo is configured (via `/etc/sudoers.d/`) for `gp-mode`, `gp-dns-switch`, `gp-passcode`, and other system commands the server invokes via `child_process.exec`.

## Development Workflow

The repo lives at `/opt/ghostport/` but live system files are in their standard Linux locations. To make changes:

1. Edit the live file (e.g., `/usr/local/bin/gp-mode` or `/opt/ghostport/ghostport-server.js`)
2. Test by restarting the relevant service
3. Copy the changed file into the repo subdirectory before committing (see "Live System vs Repository" below)

## Architecture

**Web server** (`ghostport-server.js`): Express 5 over HTTPS on `0.0.0.0:4200`. Serves static files from `public/` and exposes a REST API. Shell commands run via `child_process.exec` with 15s timeout. The server process runs as `ghostport-admin` but calls `sudo gp-mode` for privileged operations.

**Authentication**: Passcode-based auth using scrypt hashing. Passcode auto-generated on first boot and stored in `/etc/ghostport/auth.json`. Sessions are cookie-based (`gp_session`) with 24h TTL, rate-limited to 5 attempts with 60s lockout. All API routes except `/login`, `/api/auth/*` require a valid session (enforced by `requireAuth` middleware).

**Frontend** (`public/index.html`): Single-file vanilla HTML/CSS/JS app (~1200 lines). No framework, no build. Polls `GET /api/status` every 5 seconds. Cyberpunk terminal aesthetic (neon green on dark, Cinzel + Share Tech Mono fonts, scanline animations). Login page is `public/login.html` (passcode entry).

**Arsenal** (`public/arsenal.js`): Separate JS file for the security tools panel. Manages kill switch, encrypted DNS toggle, MAC randomization, blocklist updates, DNS leak testing, connected clients display, and mode scheduling. State persisted in `/etc/ghostport/arsenal.json`.

**PWA** (`public/pwa.html`, `public/manifest.json`): Progressive web app wrapper for mobile home-screen install.

**Mode switcher** (`/usr/local/bin/gp-mode`): Bash script that atomically applies nftables profiles from `/etc/gpmodes/`. Each mode switch: backs up current ruleset → validates new profile with `nft -c -f` → applies → verifies masquerade → flushes conntrack → schedules 60s auto-rollback timer.

**DNS switch** (`/usr/local/bin/gp-dns-switch`): Toggles Unbound between cleartext and encrypted DNS (cloudflared DoH on `127.0.0.1@5053`). Auto-rolls back if DNS resolution fails after the switch.

**Passcode manager** (`/usr/local/bin/gp-passcode`): View or reset the device passcode. Reset generates a new passcode via Node.js crypto, writes to auth.json, and restarts the service (invalidating all sessions).

**Rollback safety system**: When a mode is switched (via API or CLI), a 60s countdown starts. If not confirmed within 60s, the previous mode is automatically restored. Both the server (Node.js timer) and CLI (background shell process) maintain independent rollback timers. API endpoints: `POST /api/mode/confirm` and `POST /api/mode/rollback`.

**Boot persistence**: Mode saved to `/etc/ghostport/current-mode`. On boot, `ghostport-boot.service` runs `gp-mode-boot` to reapply the saved mode before the main server starts.

**Discord bot** (`discord-bot/ghostport-discord-bot.py`): Python bot that monitors a trouble-tickets forum and posts AI triage responses using the Anthropic API. Runs as `ghostport-discord.service`. Requires a Python venv (`discord-bot/venv/`) and env vars from `discord-bot/.env`.

## Network Interfaces

| Interface | Role | Notes |
|-----------|------|-------|
| `eth0` | WAN uplink | Wired to upstream router |
| `wlan0` | LAN (WiFi AP) | hostapd, 5GHz 802.11ax, SSID: GhostPortRouter |
| `wg0` | WireGuard tunnel | Only UP in doublehop/zhop modes |
| `tailscale0` | Tailscale management | Always on, never stopped by mode switches |

## Network Modes (nftables profiles in `/etc/gpmodes/`)

| Mode | Traffic path | DNS | Key constraint |
|------|-------------|-----|----------------|
| `isp` | LAN→eth0 | Default | No filtering |
| `zerotrust` | LAN→eth0 | Locked (DoT/DoH blocked) | DNS leak prevention |
| `doublehop` | LAN→wg0 | Pi-hole (forced via prerouting) | Requires wg0 UP |
| `zhop` | LAN→wg0 | Pi-hole + MagicDNS only | Most restrictive |

Tailscale is **never stopped** in any mode — it's the always-on management plane. `common.nft` allows port 4200, UDP 41641, and tailscale0 interface in all modes.

ISP mode does **not** trigger the rollback timer (it's the safe fallback). All other modes start a 60s rollback countdown.

## API Endpoints

Auth (no session required):
- `POST /api/auth/login` — `{ passcode }` — returns session cookie
- `POST /api/auth/logout` — clear session
- `GET /api/auth/check` — verify session validity
- `POST /api/auth/change-passcode` — `{ current, newPasscode }`

Core (session required):
- `GET /api/status` — mode, tunnel states, public IP, uptime, ads blocked, rollback state
- `POST /api/mode` — `{ mode }` — triggers 60s rollback timer
- `POST /api/mode/confirm` — accept current mode, cancel rollback
- `POST /api/mode/rollback` — immediate revert to previous mode
- `GET /api/pihole` — Pi-hole ad-blocking statistics
- `GET /api/wg` — WireGuard peer info
- `POST /api/tailscale` — `{ action: "start"|"stop" }`
- `POST /api/hostapd/restart` — restart WiFi access point

Diagnostics & repair:
- `GET /api/diagnostics` — system health checks (internet, DNS, gateway, services, disk)
- `POST /api/repair/dns` — restart DNS stack
- `POST /api/repair/wireguard` — restart WireGuard
- `POST /api/repair/firewall` — reapply current mode's nftables profile
- `POST /api/repair/reboot` — system reboot
- `POST /api/ticket` — `{ description, contact? }` — sends to Discord/Slack webhook

Arsenal (security tools):
- `GET /api/arsenal/status` — kill switch, encrypted DNS, MAC randomization state
- `GET /api/arsenal/clients` — connected AP clients
- `POST /api/arsenal/dnstest` — DNS leak test
- `POST /api/arsenal/blocklist` — `{ freq: "daily"|"weekly" }` — set Pi-hole gravity update schedule
- `POST /api/arsenal/blocklist/update` — trigger `pihole updateGravity` immediately
- `POST /api/arsenal/blocklist/domain` — `{ domain, action: "deny"|"allow"|"remove-deny"|"remove-allow" }` — manage Pi-hole custom lists
- `POST /api/arsenal/encrypteddns` — toggle cloudflared DoH
- `POST /api/arsenal/killswitch` — `{ enabled }` — toggle VPN kill switch (monitors wg0, drops all forward if down)
- `POST /api/arsenal/killswitch/auto` — `{ enabled }` — auto-trip kill switch on DNS leak detection
- `POST /api/arsenal/macrandom` — `{ enabled }` — toggle MAC randomization (effective on reboot)
- `POST /api/arsenal/schedules` — `{ time: "HH:MM", days: [0-6], mode }` — create cron-based mode schedule
- `DELETE /api/arsenal/schedules/:id` — delete schedule

## Live System vs Repository

The repo mirrors live system files into subdirectories for version control — they are **not auto-synced**:
- `scripts/` → `/usr/local/bin/gp-*` (gp-mode, gp-mode-boot, gp-dns-switch, gp-passcode, gp-new, install.sh)
- `etc/gpmodes/` → `/etc/gpmodes/`
- `systemd/` → `/etc/systemd/system/ghostport*.service`
- `ghostport-server.js` → `/opt/ghostport/ghostport-server.js`
- `public/` → `/opt/ghostport/public/`

After editing live files, manually copy them into the repo dirs before committing.

Example config files live in `etc/` (e.g., `arsenal.json.example`, `discord-bot.env.example`, `support.json.example`) — real configs with secrets stay on the live system only.

**Installer**: `scripts/install.sh` — one-shot setup script that installs all dependencies, Pi-hole, nft profiles, dashboard, and systemd services on a fresh Pi.

## Development Conventions

- Backup files (`.bak`) are kept alongside configs — don't delete them
- The user manages the device remotely via Tailscale and locally via WiFi AP — zero connectivity disruption during mode switches is critical
- conntrack is flushed after every mode switch to force AP clients through the new path
- nftables profiles are validated (`nft -c -f`) before applying to prevent lockouts
- Arsenal state is persisted in `/etc/ghostport/arsenal.json`
- Auth state in `/etc/ghostport/auth.json` (mode 600)
- Pi-hole API config in `/etc/ghostport/pihole.json`
- Secrets (`.env`, `support.json`, SSL certs) are excluded from git via `.gitignore`
