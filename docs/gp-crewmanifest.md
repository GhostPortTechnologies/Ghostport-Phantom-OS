# Crew Manifest — Connected Device Manager

GTK desktop app showing every device connected to the GhostPort access point: hostname, IP, MAC, vendor, online status, and activity.

## Purpose
The "who's on my network right now" view. Pulls current DHCP leases and the live ARP table, cross-references with the device profile database, and shows per-device classification (laptop, phone, TV, etc.), first-seen timestamps, and recent traffic.

## When to use
- Unknown device connects → confirm whether it's yours or a stranger
- Parental controls / Family Shield setup → identify the device to apply rules to
- Guest access hygiene → see who hasn't left after the party
- Troubleshooting: "which device is using the WiFi?" when bandwidth spikes

## Screenshot
`/opt/phantom/docs/screenshots/gp-crewmanifest.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/var/lib/misc/dnsmasq.leases` | Active DHCP leases (hostname + MAC + IP + expiry) |
| `/etc/phantom/device-profiles.json` | Custom labels, vendor overrides, per-device notes |

Device classification uses MAC OUI lookup combined with heuristics (hostname patterns, observed ports). Overrides you set in the app persist to `device-profiles.json`.

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-crewmanifest.py`

## Troubleshooting
- **Device shows "unknown vendor"** → OUI not in bundled database; the device may be using MAC randomization (common on iOS 14+ and Android 10+).
- **Hostname shows as `*` or IP only** → device didn't send a hostname in its DHCP request. Rename it in the app (stored in device-profiles.json).
- **Device missing entirely** → it used a static IP (didn't take a DHCP lease). Check `ip neigh show` for ARP entries outside the DHCP pool.
- **Lease expired but device still online** → dnsmasq lease refresh hiccup. Restart dnsmasq (`sudo systemctl restart dnsmasq`) to force re-lease.
