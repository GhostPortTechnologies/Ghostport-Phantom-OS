# gp-firewall — Firewall Rule Builder

Interactive TUI for managing GhostPort's nftables firewall rules.

## Usage
```
gp-firewall          # Launch interactive TUI
gp-firewall --help   # Show help
```

## Features
- View all 50+ active firewall rules with explanations
- Color-coded: green=allow, red=block, blue=NAT, amber=log
- Add block rules (IP, port, MAC address)
- Add allow rules (interface, protocol, port)
- Remove custom rules (system rules are protected)
- Bulk IP blocklist import
- Port manager with common security toggles (QUIC, WebRTC, Tor)
- First-launch tutorial explains firewall concepts

## Tables
- **management** — What can reach the Pi (SSH, dashboard, Tailscale)
- **filter** — What AP clients can do (forward traffic)
- **nat** — Network address translation (DNS redirect, masquerade)

## Safety
- System-critical rules cannot be deleted (SSH, dashboard, Tailscale)
- Mode switches reload base ruleset (custom rules don't survive)
- All custom rules are tagged with `gp-custom-*` comments

## Keys
| Key | Action |
|-----|--------|
| 1 | View all rules |
| 2 | Add block rule |
| 3 | Add allow rule |
| 4 | Remove a rule |
| 5 | IP blocklist |
| 6 | Port manager |
| j/k | Scroll rules |
| h | Help |
| q | Quit |
