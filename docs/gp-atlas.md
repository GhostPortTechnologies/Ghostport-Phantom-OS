# Atlas — Network Topology Map

GTK desktop app that draws your router's current network topology: WAN, LAN, tunnels, and clients in one diagram, updated live.

## Purpose
A picture of where traffic is actually flowing right now. Shows the WAN interface (eth0 or wlan0), the LAN AP (wlan0 when WiFi AP), each connected client with its IP/hostname, the active mode's tunnel state (wg0 control, wg1 data, tailscale0), and traffic flow animation between nodes.

## When to use
- Explaining the architecture to a new user or customer
- Confirming the current mode took effect — does LAN → wg1 actually route as expected?
- Post-mode-switch verification: tunnel up, DNS reachable, clients routed correctly
- Debugging: a client says "no internet" — Atlas shows whether traffic is reaching the WAN node

## Screenshot
`/opt/phantom/docs/screenshots/gp-atlas.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/etc/phantom/current-mode` | Which mode to render (ISP / ZeroTrust / DoubleHop / ZHop) |
| `/var/lib/misc/dnsmasq.leases` | Source of client nodes in the diagram |

Conntrack data drives the flow animation — it samples `/proc/net/nf_conntrack` every few seconds (scoped reads, not full dumps; see ai-dev-guide §2).

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-atlas.py`

## Troubleshooting
- **No clients in the diagram** → LAN side empty (no DHCP leases). Check `cat /var/lib/misc/dnsmasq.leases`; if truly empty, no clients are connected to the AP.
- **Tunnel nodes greyed out in DoubleHop/ZHop** → wg0 or wg1 interface is down. `sudo wg show all` to confirm, then `sudo gp-mode status`.
- **Diagram lags / animation choppy** → conntrack table is huge. Check `sudo conntrack -L | wc -l`; if >50k, consider reducing `net.netfilter.nf_conntrack_max` or restarting conntrackd.
- **Mode label wrong** → stale `current-mode` file. `sudo gp-mode status` to re-read, or `sudo gp-mode isp && sudo gp-mode <your-target>` to reassert.
