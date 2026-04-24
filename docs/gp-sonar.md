# Sonar — Rogue AP / Evil Twin Scanner

GTK desktop app that scans the surrounding 2.4 / 5 GHz airspace and flags access points impersonating your GhostPort SSID or neighboring networks.

## Purpose
An "evil twin" is a malicious AP broadcasting a copy of your legitimate SSID to lure devices into connecting. Sonar watches for SSID duplicates, BSSID anomalies (same name / different MAC), signal-strength outliers, and unexpected encryption changes (e.g., your WPA3 network suddenly also advertising as open).

## When to use
- After a move to a new location — baseline the RF neighborhood
- If clients are reporting random disconnects or DNS oddities (evil twin can hijack DNS mid-association)
- On a schedule when the router lives in a public / semi-public space (coffee shop, coworking, hotel)
- Post-incident: correlate a suspected MITM with unknown BSSIDs that appeared around that time

## Screenshot
`/opt/phantom/docs/screenshots/gp-sonar.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/etc/hostapd/hostapd.conf` | Source of truth for your own SSID — Sonar reads this to know what to compare scans against |

Passive scan — does not probe or actively associate. Runs `iw dev wlan0 scan passive` internally with timeouts (see ai-dev-guide §2 for rationale).

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-sonar.py`

## Troubleshooting
- **"Scan failed" / empty list** → wlan0 is currently the upstream WAN (`gp-wan status`). When WiFi WAN is active, wlan0 can't scan simultaneously. Switch to wired WAN or use a secondary USB wlan dongle.
- **"hostapd.conf unreadable"** → file permissions changed; `sudo chmod 644 /etc/hostapd/hostapd.conf` (safe — it contains the AP passphrase only visible to root already).
- **Slow scans** → 2.4+5 GHz passive scan takes 30–45s. Don't close the app mid-scan; let it finish.
- **Neighbor AP with same SSID but different BSSID** → usually a repeater or the neighbor genuinely chose the same SSID. Sonar flags it regardless; review MAC OUI to decide.
