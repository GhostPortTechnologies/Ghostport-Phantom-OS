# Stonefish — ARP Spoof Detector

GTK desktop app that watches the LAN ARP table for spoofing, MAC flips, and gateway impersonation.

## Purpose
ARP spoofing lets a local attacker pose as the gateway (or another client) to MITM traffic inside your LAN — still dangerous even with encrypted DNS and HTTPS, because it enables TLS-downgrade and SSL-stripping attacks. Stonefish baselines the ARP table on launch, then alerts whenever a MAC changes IP, an IP changes MAC, or two MACs claim the same IP.

## When to use
- Running GhostPort at an event / coworking / hotel where you don't trust every device on the LAN
- After adding a new client, especially one you didn't provision yourself
- Post-incident: reconstruct what MAC/IP pairs were active when an anomaly happened

## Screenshot
`/opt/phantom/docs/screenshots/gp-stonefish.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/proc/net/arp` | Live ARP table (read every 3–5s interactively; daemon mode every 10–30s) |

Baseline is kept in-memory during the app's lifetime; closing the app resets the baseline. For persistent ARP monitoring, use the TUI companion `gp-arp-guard`.

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-stonefish.py`

## Troubleshooting
- **False alarms on device reconnect** → DHCP renewals and WiFi roams do legitimately flip MAC↔IP bindings. Tune the "suppress-within-X-seconds" guard or whitelist the device in the baseline.
- **Gateway MAC flips** → either your ISP modem replaced its hardware, or someone is spoofing the upstream gateway. Compare with `ip neigh show dev eth0`; if the MAC doesn't match the sticker/label on your modem, investigate.
- **Baseline cleared unexpectedly** → app was closed and reopened. The baseline is session-scoped by design — move to `gp-arp-guard` (TUI) for persistent monitoring.
- **Two MACs on one IP** → most common cause is a duplicate static-IP assignment. Check `/etc/dnsmasq.d/` and any static assignments clients may have set themselves.
