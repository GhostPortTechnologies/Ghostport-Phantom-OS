# Seadevil — MAC Address Randomizer

GTK desktop app that randomizes the MAC address on `eth0` (WAN) or `wlan0` (AP / WiFi WAN), with one-click rotation and restore.

## Purpose
The factory-assigned MAC is stable forever and can be used by your ISP, upstream WiFi networks, and anyone on the local segment to fingerprint the GhostPort device across locations. Seadevil generates a random, locally-administered MAC (IEEE-compliant: second-least-significant bit of the first byte set), applies it to the chosen interface, and can restore the original on demand.

## When to use
- Connecting to a new upstream WiFi (hotel / café / airport) — rotate before joining
- Before provisioning a new ISP connection — prevents your old ISP from correlating the new install
- Monthly hygiene on the WAN interface for privacy-conscious users
- Before any RF survey where you don't want your router identifiable

## Screenshot
`/opt/phantom/docs/screenshots/gp-seadevil.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/sys/class/net/<iface>/address` | Current MAC read back for display |

Original MACs are captured once per interface at first launch and kept in the app's state directory; the "Restore original" button writes the captured value back using `ip link set dev <iface> address <mac>`. Operations require root (runs via sudo with passwordless `gp-mac` helper).

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-seadevil.py`

## Troubleshooting
- **"Interface busy" on apply** → the interface is up and active. Seadevil brings it down, applies, brings it back up — if the cycle fails, manually run `sudo ip link set <iface> down && sudo ip link set <iface> up`.
- **Clients disconnect when randomizing wlan0** → expected. The AP briefly drops; clients reassociate within ~5s. Don't rotate wlan0 MAC during a live call.
- **Restore doesn't return to the sticker MAC** → original was captured *after* a prior rotation. Check the interface's current MAC with `cat /sys/class/net/<iface>/address` and manually set it to the factory value printed on the board.
- **`gp-mac` permission denied** → passwordless sudo for `gp-mac` isn't installed. Check `/etc/sudoers.d/ghostport` for an entry like `ghostport-admin ALL=(ALL) NOPASSWD: /usr/local/bin/gp-mac`.
