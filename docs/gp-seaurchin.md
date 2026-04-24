# Sea Urchin — System Health Dashboard

GTK desktop app showing live CPU, temperature, memory, disk, load, network latency/jitter, and per-service status for GhostPort daemons.

## Purpose
One-screen "is my router healthy?" view. Gauges for hot metrics (CPU temp, load avg, memory pressure), a strip of service dots (ghostport, dnsmasq, hostapd, tailscaled, wireguard, pi-hole, sni-inspector), and per-interface error counters (rx/tx errors, dropped packets) to catch cabling or radio issues early.

## When to use
- First stop when something feels off — before diving into individual tool logs
- Pre-change sanity check: `gp-preflight` is good for scripts; Sea Urchin is good for humans
- Monitoring during a stress event (big download, mode switch, WiFi scan) to watch thermals
- Fleet support call: customer can read the gauges back verbatim, fast triage

## Screenshot
`/opt/phantom/docs/screenshots/gp-seaurchin.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/sys/class/thermal/thermal_zone0/temp` | CPU temperature source |
| `/proc/loadavg`, `/proc/meminfo`, `/proc/net/dev` | Live system stats |
| `systemctl is-active <unit>` | Per-service health checks |

Polling: CPU/temp every 1s, interface stats every 5s, service status every 5s, latency probe every 10s. All with timeouts (see ai-dev-guide §2).

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-seaurchin.py`

## Troubleshooting
- **Temp gauge redlined (>80°C)** → thermal throttling imminent. Check airflow; if in a case, ensure the fan is running (`vcgencmd measure_temp`).
- **Service dot red for `ghostport`** → Node server crashed. `sudo journalctl -u ghostport -n 50` and restart with `sudo systemctl restart ghostport`.
- **Interface errors climbing on eth0** → cable or port issue. Swap cable; run `sudo ethtool eth0` to check link speed & errors.
- **Latency gauge high** → upstream DNS or WAN issue. `gp-preflight` for the automated triage, or `mtr 1.1.1.1` for a hop-by-hop view.
- **Memory pressure >90%** → something is leaking. `ps aux --sort=-rss | head -5` to find the culprit.
