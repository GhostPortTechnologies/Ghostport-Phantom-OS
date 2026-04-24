# gp-activity — Activity Log Viewer

## Summary
Interactive TUI that displays device event history from `/etc/phantom/activity.json`. Color-coded by event type with pagination and filtering.

## Quick Start
1. Open **Start Menu > MONITOR > Activity Log**
2. Events are shown newest-first, 20 per page
3. Press `f` to filter by type (mode/error/dns/all), `n`/`p` to page

## Keys
| Key | Action |
|-----|--------|
| `n` | Next page |
| `p` | Previous page |
| `f` | Filter by type |
| `r` | Refresh |
| `q` | Quit |

## How It Works
Reads `/etc/phantom/activity.json` which is populated by the GhostPort API server. Events are sorted newest-first with color-coding: green=normal, cyan=mode changes, amber=warnings, red=errors. Supports both list and dict JSON formats.

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-activity` | Main script |
| `/etc/phantom/activity.json` | Event log data |
