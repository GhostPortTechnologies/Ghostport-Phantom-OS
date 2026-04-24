# gp-arp-guard — ARP Poisoning Detector

Real-time ARP table monitor that detects spoofing attacks on your network.

## Usage
```
gp-arp-guard         # Live monitor (auto-refreshes every 3s)
gp-arp-guard scan    # One-shot scan and report
gp-arp-guard --help  # Help
```

## Detections
- **Gateway MAC change** — Someone impersonating your router (ARP poisoning)
- **Duplicate MACs** — Two IPs sharing one MAC (spoof indicator)
- **ARP table flood** — Abnormally large table (>50 entries)
- **Incomplete entries** — Many unresolved entries (network scan indicator)

## How It Works
Saves your gateway's MAC on first scan. If it ever changes, alerts immediately.
Scans the kernel ARP cache (`ip neigh`) every 3 seconds in live mode.

## Keys
| Key | Action |
|-----|--------|
| q | Quit |
| h | Help (explains ARP attacks) |
| r | Force refresh |
