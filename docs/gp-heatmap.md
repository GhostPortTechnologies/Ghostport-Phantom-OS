# gp-heatmap — Bandwidth Heatmap

## Overview
Visualizes network bandwidth usage across all interfaces as a 24-hour heatmap with live traffic rates. Samples `/proc/net/dev` every 5 seconds and stores hourly averages.

## Usage
```
gp-heatmap              Interactive heatmap (live, updates every 5s)
gp-heatmap summary      One-shot bandwidth summary
gp-heatmap reset        Clear all history data
gp-heatmap --help       Help
```

## Interactive Keys
- `[d]` Details — per-interface stats (IP, MTU, state, total RX/TX)
- `[h]` Help — what interfaces mean, how to read the heatmap
- `[q]` Quit

## Features
- **Live traffic rates**: Real-time download/upload per interface
- **24-hour heatmap**: Block-character visualization, one column per hour, RX and TX rows
- **Per-interface tracking**: eth0 (WAN), wlan0 (AP), wg0 (control), wg1 (data), tailscale0 (mgmt)
- **Today's totals**: Cumulative downloaded/uploaded bytes per interface
- **Intensity legend**: 8 levels from zero to 10+ MB/s, color-coded

## Heatmap Intensity Scale
| Level | Block | Threshold |
|-------|-------|-----------|
| 0 | (space) | No traffic |
| 1-2 | ░ | < 10 KB/s |
| 3-4 | ▒ | < 1 MB/s |
| 5-6 | ▓ | < 10 MB/s |
| 7 | █ | 10+ MB/s |

## Data Storage
- Samples: `~/.config/phantom/heatmap/YYYY-MM-DD.dat`
- Format: `timestamp hour interface delta_rx delta_tx elapsed`
- One file per day, auto-created while running
- Clear with `gp-heatmap reset`

## Theme
Sources `/usr/local/lib/gp-theme-colors.sh` — all colors derived from accent.

## Dependencies
- `/proc/net/dev` (kernel network stats)
- `ip` command (interface detection)
- `awk` (data processing)
