# gp-diagnostics — Diagnostics & Repair Panel

## Summary
Runs 13 health checks with PASS/FAIL/WARN results, then offers 9 one-click repair actions. The system doctor for Phantom OS.

## Quick Start
1. Open **Start Menu > MONITOR > Diagnostics & Repair**
2. Review health check results (green=PASS, red=FAIL, amber=WARN)
3. Press `1`-`9` to run a repair action if something failed

## Health Checks
Internet connectivity, DNS resolution, API server, Pi-hole, WireGuard control (wg0), WireGuard data (wg1), Tailscale, WiFi AP (hostapd), disk space, CPU temperature, memory usage, firewall (nftables), current mode.

## Repair Actions
| Key | Action |
|-----|--------|
| `1` | Restart DNS stack (Pi-hole + dnsmasq) |
| `2` | Restart WireGuard (wg0 + wg1) |
| `3` | Reapply firewall (reload current mode nft) |
| `4` | Repair Tailscale (restart tailscaled) |
| `5` | Flush DNS cache (Pi-hole cache clear) |
| `6` | Flush conntrack (force clients through new path) |
| `7` | Restart WiFi AP (hostapd — clients disconnect briefly) |
| `8` | Restart API server (ghostport.service) |
| `9` | System reboot (5s countdown, Ctrl+C to cancel) |
| `r` | Re-run checks |
| `q` | Quit |

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-diagnostics` | Main script |
