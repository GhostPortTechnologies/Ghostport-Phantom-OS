# Crow's Nest — Intrusion Detection Dashboard

GUI frontend to the `gp-ids` intrusion detection engine. Surfaces live alerts, attack trends, and per-country source breakdowns in one desktop app.

## When To Use It
- You want a visual read on what's hitting your network right now
- Something in the waybar IDS counter spiked and you need context
- Post-incident: "show me the last 24h of port scans by country"

## Tabs

| Tab | What's there |
|-----|--------------|
| **Alerts** | Live stream of IDS events — timestamp, severity, source IP, country (via GeoIP), rule hit, destination port. Color-coded by severity. |
| **Trends** | Drop-rate graph, top attacked ports, attack-pattern detection (port scan, brute force, SYN flood, DNS tunnel, C2 beacon, exfil). |

## Controls

- Scroll: mouse wheel or `↑/↓`
- Search/filter: top-right filter box
- Theme follows active GhostPort theme (live-reload via `~/.config/phantom/theme.json`)

## Data Sources

- `/etc/phantom/ids-events.json` — rolling event log written by `gp-ids` (see `gp-ids.md`)
- `/usr/share/GeoIP/GeoLite2-Country-*.csv` — local MaxMind GeoIP DB (no API calls)
- Waybar companion: `gp-bar-ids` — click to open this app

## Troubleshooting

| Symptom | Check |
|---------|-------|
| "No events" with known drops | `journalctl -u ghostport -n 50 \| grep GhostPort-DROP` — is `gp-ids monitor` running? |
| Country column is blank | `ls /usr/share/GeoIP/` — GeoIP CSVs may be missing (non-fatal, IP still shown) |
| App doesn't launch | `python3 /opt/phantom/desktop/gp-crowsnest.py` from a terminal for error output |
| Stale data | App re-reads `ids-events.json` every 5s; confirm the file's mtime is recent |

## Files

- App: `/opt/phantom/desktop/gp-crowsnest.py`
- Icon: `/opt/phantom/desktop/icons/gp-crowsnest.svg`
- Engine (headless): `gp-ids monitor` (see `gp-ids.md`)
