# gp-throttle-detect — ISP Throttle Detector

## Summary
Compares internet speed with and without VPN to detect ISP throttling. If VPN speed is significantly faster than direct, your ISP is likely throttling your connection.

## Quick Start
1. Open **Start Menu > MONITOR > ISP Throttle Detector**
2. Tests run automatically (~60 seconds, switches modes)
3. Review verdict: throttling detected, no throttling, or inconclusive

## How It Works
1. Tests speed in current mode (VPN or direct)
2. Automatically switches to the other mode (ISP ↔ DoubleHop)
3. Tests speed again
4. Restores original mode
5. Compares results

## Analysis
| Result | Meaning |
|--------|---------|
| VPN >15% faster | **Throttling detected** — ISP is deprioritizing your traffic |
| ISP >15% faster | **No throttling** — normal VPN overhead (encryption + routing) |
| Within 15% | **Inconclusive** — run again at different times |

## Requirements
- DoubleHop or ZHop mode must be available (WireGuard tunnel configured)
- If only ISP mode is available, runs single-mode test with note to switch modes

## Important Notes
- The test temporarily switches privacy modes. Original mode is always restored.
- Mode confirmation (`gp-mode confirm`) is called automatically.
- ISPs often throttle during peak hours (6-10 PM) — run at different times for best results.
- Uses Cloudflare speed test (2 runs x 10MB each per mode).

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-throttle-detect` | Main script |
