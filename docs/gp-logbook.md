# Logbook — Event Log Viewer

GTK desktop app that reads GhostPort's activity log and presents a filterable, categorized event timeline.

## Purpose
Every GhostPort daemon writes significant events (mode switches, auth attempts, rule changes, tunnel up/down, health alerts) to a central activity log. Logbook surfaces them in a single view with category filters, time-range selection, and event correlation — so "what happened on the router at 3am last night?" is a one-minute question instead of a 20-minute journalctl spelunk.

## When to use
- Morning triage: skim overnight events for anything that needs follow-up
- Incident reconstruction: select the relevant time window, filter by category, export
- Verifying a scheduled change happened (OTA update ran, auto-healing fired, mode reasserted)
- Audit prep: the log is the user-facing record of what the router did

## Screenshot
`/opt/phantom/docs/screenshots/gp-logbook.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/etc/phantom/activity.json` | Primary activity log (rolling, capped at ~2000 events) |

Categories: MODE, AUTH, FIREWALL, TUNNEL, DNS, HEALTH, UPDATE, SECURITY, USER. Each event has timestamp, category, severity (info/warn/critical), source daemon, and a human-readable message.

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-logbook.py`

## Troubleshooting
- **Logbook empty** → activity.json missing or unreadable. `ls -la /etc/phantom/activity.json` — if absent, daemons haven't written yet (fresh boot). If permission-denied, `sudo chmod 644 /etc/phantom/activity.json`.
- **Events older than ~7 days are gone** → by design; log rotates at ~2000 entries to cap disk use. For longer retention, enable journald persistence (`sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald`).
- **Timestamps in wrong timezone** → host timezone is UTC by default on provisioning. Set it: `sudo timedatectl set-timezone America/Los_Angeles` (or your TZ).
- **Critical event I don't understand** → the `source daemon` column names the component. Tail its journal: `sudo journalctl -u ghostport-<daemon> -f` for live follow-up.
