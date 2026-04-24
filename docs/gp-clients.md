# gp-clients — Connected Clients Viewer

## Summary
Shows all devices connected to the GhostPort WiFi AP. Displays hostname, IP, MAC, lease expiry, ARP status, and detects randomized MACs.

## Quick Start
1. Open **Start Menu > MONITOR > Connected Clients**
2. Online devices shown with green dot, stale with red
3. Randomized MACs are flagged with a warning

## Flags
| Flag | Description |
|------|-------------|
| `--json` | JSON output (for scripting) |
| `--watch` | Auto-refresh every 5 seconds |
| `--count` | Print device count only |

## How It Works
Reads DHCP leases from `/var/lib/misc/dnsmasq.leases` and cross-references with the kernel ARP table (`ip neigh show dev wlan0`). ARP states REACHABLE/STALE/DELAY = online; all others = offline. Randomized MACs are detected by checking the second hex digit (2/6/a/e = locally administered = randomized).

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-clients` | Main script |
| `/var/lib/misc/dnsmasq.leases` | DHCP lease data |
