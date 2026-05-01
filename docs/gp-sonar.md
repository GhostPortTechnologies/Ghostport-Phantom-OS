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

## Future Work (deferred research)

Tracked under AI-TICKET-SOP. Not on the active queue; split into individual tickets if/when prioritized.

**T-0022 — Behavior gaps** (low): encounter log persistence, scan history viewer, deferred wishlist items already expected by users.

**T-0033 — Research-grade capabilities** (low):
- **Signal triangulation** — wlan0 + monitor-radio RSSI delta as a coarse "in this room? down the street?" hint. Depends on T-0028 (USB monitor radio).
- **PMKID extraction + offline crack** — capture PMKID from neighboring AP beacons and dictionary-test. *Ethically gated:* own-AP allow-list only; hard toggle. Depends on T-0028.
- **Threat-report PDF** — formal incident report (BSSIDs, signal-over-time, IE diffs, signature hits, timestamps) for hand-off to law enforcement / researchers. weasyprint or reportlab.
- **Wardriving overlay** — laptop-only timestamp + GPS log of AP sightings, cross-referenced against WiGLE (T-0030). Niche.
- **STIX/TAXII federation** — auto-publish detected evil-twin / attack-tool sightings as IOCs to a community feed. *Privacy-gated:* opt-in, anonymized BSSID hashing, aggregate-only.

See `gp-tickets show T-0022` / `gp-tickets show T-0033` for full design notes.
