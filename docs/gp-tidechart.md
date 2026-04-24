# Tide Chart — Bandwidth Heatmap with Anomaly Detection

GTK desktop app that visualises per-interface bandwidth as a calendar heatmap (hour-of-day × day) with automatic anomaly flagging. Replaces the older `gp-heatmap` TUI for the desktop flow.

## Purpose
Show bandwidth trends over time in one glance. Each cell is one hour on one interface, coloured by throughput. Anomalies — cells where bandwidth exceeds the rolling baseline's 90th percentile by ≥1.5× — are outlined in the theme's warning color. Interfaces can be compared side-by-side (e.g. `eth0` vs `wg1` to spot VPN bypass).

## When to use
- Weekly review: catch unexpected overnight spikes (updates pushing, bots, rogue device)
- After deploying a new device: confirm it behaves vs baseline
- Debugging slow internet: is it always slow at this hour, or is today an outlier?
- Post-incident timeline: pair with Logbook + Crow's Nest to see traffic around an alert

## Screenshot
`/opt/phantom/docs/screenshots/gp-tidechart.png` *(TBD — drop PNG at 1050×750 to populate)*

## Config + data files
| Path | What |
|------|------|
| `~/.config/phantom/tidechart-history.json` | Rolling sample history (format differs from TUI heatmap; do not share) |
| `/etc/phantom/retention.json` | Shared config: `{"days": N}`, clamped 1–60 (default 30) |

Sampling: every 5 seconds (POLL_INTERVAL). Interfaces monitored: `eth0`, `wlan0`, `wg0`, `wg1` (wg interfaces only populate data while in DoubleHop/ZHop modes).

Launch from the desktop icon or: `python3 /opt/phantom/desktop/gp-tidechart.py`

## Troubleshooting
- **Heatmap is blank / all grey** → First launch: needs ~1 hour to populate enough samples for the first cell. Check file mtime: `stat ~/.config/phantom/tidechart-history.json` — should update every 5s.
- **Anomaly outlines never appear** → Baseline forms from the rolling retention window. Until you have ≥7 days of data per hour-of-day bucket, the p90 baseline is too noisy to flag anything reliably. This is by design.
- **`wg0` / `wg1` rows are empty** → Expected in ISP / ZeroTrust mode (tunnels down). Data starts flowing when you switch to DoubleHop / ZHop.
- **Heatmap shows old data after a mode switch** → Samples persist across modes; app does NOT wipe on mode change. That's intentional (so you can see pre-switch traffic). Use the time selector to focus on post-switch.
- **Retention changed but history didn't shrink** → Tide Chart trims on the next 5s poll, not immediately. If `retention.json` says 7 days but you still see 30, wait one poll cycle.
- **File grew past 500 KB** → Built-in cap; samples beyond the retention window are dropped per poll. If it keeps growing, check for a write bug: `ls -la ~/.config/phantom/tidechart-history.json` → if ≥1 MB, truncate with `echo '{}' > ~/.config/phantom/tidechart-history.json` and relaunch.
