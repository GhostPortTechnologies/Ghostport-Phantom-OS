# gp-mac — MAC Address Randomizer

View, randomize, or restore MAC addresses on network interfaces.

## Usage
```
gp-mac                  # Interactive UI
gp-mac show             # Show all MACs
gp-mac random <iface>   # Randomize interface MAC
gp-mac restore <iface>  # Restore original MAC
gp-mac --help
```

## Features
- Shows current vs original MAC for all interfaces
- Detects randomized/locally-administered MACs
- Generates proper locally-administered unicast addresses
- Saves originals for reliable restore
- Interface details (IP, MTU, OUI, state)
- Warns before touching AP interface (wlan0)

## Keys (interactive)
| Key | Action |
|-----|--------|
| r | Randomize an interface |
| o | Restore original MAC |
| s | Show interface details |
| h | Help (explains MAC tracking) |
| q | Quit |
