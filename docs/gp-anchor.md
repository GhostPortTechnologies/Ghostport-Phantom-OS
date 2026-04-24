# Anchor — VPN Kill Switch

GTK desktop app that enforces "no traffic outside the tunnel." When armed, Anchor monitors the data-plane WireGuard tunnel (`wg1`) and will drop any traffic that tries to leave via `eth0` directly if the tunnel drops.

## Purpose
A safety net for DoubleHop / ZHop modes. If your VPN tunnel silently fails (peer dies, packet loss spikes, re-key stalls), plain-text traffic normally just falls back to the ISP — leaking your real IP. Anchor arms an nftables hook that blocks that fallback and keeps you offline until the tunnel is healthy again. Also shows live tunnel quality metrics (latency, loss %, uptime sparkline) with letter grades.

## When to use
- Before any travel / public-WiFi session — arm before joining, confirm grade A/B before using the network
- During DoubleHop or ZHop sessions when you'd rather be offline than leaking
- Auditing tunnel health — the grade + sparkline tell you whether `wg1` is actually carrying traffic
- When Waybar's `gp-bar-tunnel` badge drops below grade B, check here for root cause

## Screenshot
`/opt/phantom/docs/screenshots/gp-anchor.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `~/.config/phantom/killswitch.json` | Persisted armed state `{"armed": true|false}` |
| `/etc/phantom/current-mode` | Read-only; Anchor checks current mode before allowing arm |
| Tunnel targets | `wg0` = 44.214.101.82 (control plane), `wg1` = 54.211.104.73 (data plane) |

Thresholds (set in source, not user-configurable):
- Latency grade: A ≤50ms, B ≤100ms, C ≤150ms, D ≤300ms
- Loss grade: A 0%, B ≤2%, C ≤5%, D ≤15%
- Uptime history: 1200 samples = 1 hour at 3s polling

Launch from the desktop icon or: `python3 /opt/phantom/desktop/gp-anchor.py`

## Troubleshooting
- **"Armed but all traffic blocked"** — that's the point. Current firewall mode must route via `wg1` (i.e., DoubleHop or ZHop). In ISP or ZeroTrust mode there is no tunnel to enforce; disarm or switch mode.
- **Latency shows N/A** → `wg1` isn't up. `sudo wg show wg1` — check handshake timestamp. If stale, try `sudo gp-mode <current>` to re-establish.
- **Sparkline is blank** → App just launched; needs one poll cycle (3s) to plot first sample. Blank >10s means polling died — relaunch.
- **Auto-arm suggestion keeps nagging** → App suggests arming when tunnel grade A/B for 10+ minutes. Dismiss with the × on the toast, or arm once and the nag stops.
- **Can't toggle arm/disarm (button greyed)** → `killswitch.json` is unwritable. Check `ls -la ~/.config/phantom/killswitch.json` and fix owner/perms with `chown ghostport-admin:ghostport-admin ~/.config/phantom/killswitch.json`.
