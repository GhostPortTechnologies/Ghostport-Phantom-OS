# gp-arsenal — Security Control Panel

## Summary
Interactive terminal UI for toggling security features without a browser. Reads live system state and `/etc/phantom/arsenal.json`. No API dependency — works even if the web UI is down.

## Quick Start
1. Open **Start Menu > PROTECT > Arsenal (Security Panel)**
2. Press `1`-`8` to toggle security features on/off
3. Press `d` for DNS leak test, `u` to update blocklists

## Toggles
| Key | Feature | Description |
|-----|---------|-------------|
| `1` | Kill Switch | Drop all traffic if VPN fails |
| `2` | Kill Switch Auto | Auto-trip on DNS leak detection |
| `3` | Encrypted DNS | DNS-over-HTTPS via cloudflared |
| `4` | MAC Randomization | Random MAC on reboot |
| `5` | QUIC Block | Block QUIC/HTTP3 protocol |
| `6` | Anti-Fingerprint | Block fingerprinting domains |
| `7` | WebRTC Block | Block STUN/TURN IP leaks |
| `8` | Cover Traffic | Generate decoy noise traffic |

## Tools
| Key | Tool |
|-----|------|
| `d` | DNS Leak Test (inline) |
| `u` | Update Pi-hole blocklist (gravity) |
| `r` | Refresh status |
| `q` | Quit |

## How It Works
Checks live system state via `gp-dns-switch status`, `systemctl`, `ip link show`, `nft list ruleset`, and DHCP leases. Toggles modify both the JSON config and the live system (nftables rules, systemd services). Shows WireGuard tunnel status and connected client count.

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-arsenal` | Main script |
| `/etc/phantom/arsenal.json` | Toggle state config |
