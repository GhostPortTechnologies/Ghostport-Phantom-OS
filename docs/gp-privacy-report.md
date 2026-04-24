# gp-privacy-report — Privacy Report

## Summary
Full privacy report pulling live data from the GhostPort API. Shows security score, tunnel status, blocking stats, and top blocked domains in one terminal view.

## Quick Start
1. Open **Start Menu > MONITOR > Threat Report**
2. Report generates automatically from live API data
3. Press Enter to close

## Sections
- **Mode & Score** — Current privacy mode, security score out of 100
- **Score Breakdown** — Per-category scoring with bar chart
- **Tunnels** — WireGuard data (wg1), control (wg0), Tailscale status
- **Blocking Stats** — Ads blocked (session + all-time), DNS queries, firewall drops, auth failures
- **Top Blocked Domains** — Top 10 most-blocked domains in last 24h

## How It Works
Fetches `/api/status` and `/api/threat/summary` from localhost:4200 using a widget token from `/run/ghostport/widget-token`. Requires the GhostPort API server to be running.

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-privacy-report` | Main script |
| `/run/ghostport/widget-token` | API auth token |
