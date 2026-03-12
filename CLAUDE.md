# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GhostPort is a Raspberry Pi 5 privacy router with a web command deck. It manages network privacy modes by applying nftables firewall profiles, routing traffic through WireGuard/Tailscale tunnels, and filtering DNS via Pi-hole.

## Running & Restarting

```bash
sudo systemctl restart ghostport        # Restart Node.js server (port 4200 HTTPS)
sudo systemctl status ghostport          # Check server status
sudo journalctl -u ghostport -f          # Tail server logs
sudo gp-mode status                      # Check current firewall mode
sudo gp-mode isp|zerotrust|doublehop|zhop  # Switch mode from CLI
```

No build step, no test framework, no linter. Edit files directly and restart the service.

## Architecture

**Web server** (`ghostport-server.js`): Express 5 over HTTPS on `0.0.0.0:4200`. Serves static files from `public/` and exposes a REST API. Shell commands run via `child_process.exec` with 15s timeout. The server process runs as `ghostport-admin` but calls `sudo gp-mode` for privileged operations.

**Frontend** (`public/index.html`): Single-file vanilla HTML/CSS/JS app. No framework, no build. Polls `GET /api/status` every 5 seconds. Cyberpunk terminal aesthetic (neon green on dark, Cinzel + Share Tech Mono fonts, scanline animations).

**Mode switcher** (`/usr/local/bin/gp-mode`): Bash script that atomically applies nftables profiles from `/etc/gpmodes/`. Each mode switch: backs up current ruleset → validates new profile with `nft -c -f` → applies → verifies masquerade → flushes conntrack → schedules 60s auto-rollback timer.

**Rollback safety system**: When a mode is switched (via API or CLI), a 60s countdown starts. If not confirmed within 60s, the previous mode is automatically restored. Both the server (Node.js timer) and CLI (background shell process) maintain independent rollback timers. API endpoints: `POST /api/mode/confirm` and `POST /api/mode/rollback`.

**Boot persistence**: Mode saved to `/etc/ghostport/current-mode`. On boot, `ghostport-boot.service` runs `gp-mode-boot` to reapply the saved mode before the main server starts.

## Network Modes (nftables profiles in `/etc/gpmodes/`)

| Mode | Traffic path | DNS | Key constraint |
|------|-------------|-----|----------------|
| `isp` | LAN→eth0 | Default | No filtering |
| `zerotrust` | LAN→eth0 | Locked (DoT/DoH blocked) | DNS leak prevention |
| `doublehop` | LAN→wg0 | Pi-hole (forced via prerouting) | Requires wg0 UP |
| `zhop` | LAN→wg0 | Pi-hole + MagicDNS only | Most restrictive |

Tailscale is **never stopped** in any mode — it's the always-on management plane. `common.nft` allows port 4200, UDP 41641, and tailscale0 interface in all modes.

## API Endpoints

- `GET /api/status` — mode, tunnel states, public IP, uptime, ads blocked, rollback state
- `POST /api/mode` — `{ mode: "isp"|"zerotrust"|"doublehop"|"zhop" }` — triggers 60s rollback timer
- `POST /api/mode/confirm` — accept current mode, cancel rollback
- `POST /api/mode/rollback` — immediate revert to previous mode
- `GET /api/pihole` — Pi-hole ad-blocking statistics
- `GET /api/wg` — WireGuard peer info
- `POST /api/tailscale` — `{ action: "start"|"stop" }` — control Tailscale daemon
- `GET /api/diagnostics` — runs 9 system health checks (internet, DNS, gateway, services, disk) and returns pass/fail/warn with fix suggestions
- `POST /api/ticket` — `{ description, contact? }` — sends trouble ticket to Discord/Slack webhook configured in `/etc/ghostport/support.json`

## Key File Locations

- Server: `/opt/ghostport/ghostport-server.js`
- Frontend: `/opt/ghostport/public/index.html`
- Mode CLI: `/usr/local/bin/gp-mode`
- Firewall profiles: `/etc/gpmodes/{common,isp,zerotrust,doublehop,zhop}.nft`
- Mode state: `/etc/ghostport/current-mode`
- Boot script: `/usr/local/bin/gp-mode-boot`
- Services: `/etc/systemd/system/ghostport.service`, `ghostport-boot.service`
- SSL certs: `/opt/ghostport/ssl/`
- Legacy Flask UI: `/usr/local/bin/ghostport-ui.py` (port 5000)

## Development Conventions

- Backup files (`.bak`) are kept alongside configs — don't delete them
- The user manages the device remotely via Tailscale and locally via WiFi AP — zero connectivity disruption during mode switches is critical
- conntrack is flushed after every mode switch to force AP clients through the new path
- nftables profiles are validated (`nft -c -f`) before applying to prevent lockouts
