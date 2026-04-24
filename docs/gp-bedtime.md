# gp-bedtime — Bedtime Torpedo

## Summary
Per-device internet kill schedule. Block specific devices on your network at set times using nftables MAC-based firewall rules. Perfect for parental controls.

## Quick Start
1. Run `gp-bedtime devices` to find the device's MAC address
2. Run `gp-bedtime add aa:bb:cc:dd:ee:ff "Kids iPad" 21:00 07:00`
3. Internet is killed at 9pm, restored at 7am — automatically

## Commands
| Command | Description |
|---------|-------------|
| `gp-bedtime` | Show all rules and status |
| `gp-bedtime add <mac> <name> <bed> <wake> [days]` | Create a rule |
| `gp-bedtime remove <id>` | Delete a rule |
| `gp-bedtime toggle <id>` | Enable/disable a rule |
| `gp-bedtime fire <id>` | Test torpedo (30-second blackout) |
| `gp-bedtime devices` | List connected devices with MACs |
| `gp-bedtime help` | Show help |

## Day Numbers
`0`=Sun, `1`=Mon, `2`=Tue, `3`=Wed, `4`=Thu, `5`=Fri, `6`=Sat. Default: all days.

## Examples
```bash
# Block every night
gp-bedtime add aa:bb:cc:dd:ee:ff "Kids iPad" 21:00 07:00

# Block weeknights only (Mon-Fri)
gp-bedtime add aa:bb:cc:dd:ee:ff "Kids iPad" 21:00 07:00 1,2,3,4,5

# Test a rule for 30 seconds
gp-bedtime fire abc123
```

## How It Works
Creates nftables DROP rules that block all forwarded traffic from a specific MAC address. DNS queries from the device are also blocked. Rules are managed via `/etc/phantom/bedtime.json` and enforced by the GhostPort server's scheduling loop. The `fire` command creates a temporary 30-second block for testing.

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-bedtime` | Main script |
| `/etc/phantom/bedtime.json` | Rule definitions |
