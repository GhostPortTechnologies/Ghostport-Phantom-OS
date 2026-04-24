# gp-killswitch — VPN Kill Switch

## Overview
Monitors the WireGuard VPN tunnel (wg1) and automatically blocks all internet traffic if it drops, preventing accidental ISP exposure. Auto-restores when the tunnel reconnects.

## Usage
```
gp-killswitch              Interactive monitor (checks every 3s)
gp-killswitch status       Show current state
gp-killswitch enable       Arm the kill switch
gp-killswitch disable      Disarm the kill switch
gp-killswitch --help       Help
```

## Interactive Keys
- `[e]` Enable — arm the kill switch
- `[x]` Disable — disarm and remove any active block
- `[t]` Test — briefly block traffic and restore (verifies nftables rules)
- `[h]` Help — how it works, what stays accessible
- `[q]` Quit

## How It Works
1. Checks wg1 interface status and WireGuard handshake age every 3 seconds
2. If tunnel is down (no handshake within 180s, ping to 10.66.67.1 fails):
   - Inserts nftables chain `gp-killswitch` with DROP rules for wlan0->eth0 and wlan0->wg1
   - All AP client internet traffic is blocked
3. When tunnel recovers:
   - Removes the killswitch chain and restores normal forwarding
4. Only active in VPN modes (DoubleHop, ZHop)

## What Stays Working During Block
- Dashboard (port 4200)
- Tailscale (remote management)
- Local DNS (Pi-hole)
- DHCP (device leases)
- SSH access

## Config
- State: `~/.config/phantom/killswitch.json`
- nftables chain: `inet filter gp-killswitch`

## Theme
Sources `/usr/local/lib/gp-theme-colors.sh` — all colors derived from accent.
