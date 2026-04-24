# gp-rogue-scan — Rogue AP Detector

Scans nearby wireless networks and flags suspicious access points that could be attacking your network or tricking your devices into connecting.

## Usage

```bash
gp-rogue-scan            # Continuous monitoring (scan every 30s)
gp-rogue-scan --once     # Single scan and exit
gp-rogue-scan --waybar   # JSON output for waybar module
gp-rogue-scan --help     # Show help
```

## Threat Levels

| Level | What | Why It Matters |
|-------|------|----------------|
| CRITICAL | Evil twin — same SSID as your AP, different BSSID | Attacker cloned your network name to trick devices into connecting to them |
| HIGH | Open honeypot — unencrypted + known lure SSID | Fake "Free WiFi" designed to intercept traffic |
| MEDIUM | Open network or extremely strong unknown signal | Unencrypted networks expose all traffic; very strong signals suggest a device planted nearby |
| LOW | Known honeypot SSID pattern (but encrypted) | Less dangerous since encryption is present, but still suspicious |

## Keyboard (watch mode)

- `s` — Force immediate scan
- `h` — Show help
- `q` — Quit

## How It Works

Uses `iw dev wlan0 scan` to scan all channels. The radio briefly hops away from your AP channel during scan, causing a <1 second interruption for connected clients.

Compares each found AP against:
1. Your AP's SSID (evil twin detection)
2. Encryption status (open network detection)
3. Signal strength (proximity attack detection)
4. Known honeypot SSID patterns (lure detection)

## Files

- Script: `~/.local/bin/gp-rogue-scan`
- First-run flag: `~/.config/phantom/.rogue-scan-intro`
