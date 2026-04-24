# gp-capture -- Packet Capture Tool

Interactive TUI for capturing, filtering, and analyzing network traffic using tshark.

## Usage

```bash
gp-capture          # Interactive TUI
gp-capture --help   # Show help
```

## Features

- Interface selection (eth0, wlan0, wg0, wg1, tailscale0)
- 10 preset BPF filters (HTTP/HTTPS, DNS, SSH, DHCP, ARP, ICMP, WireGuard, Non-DNS, SYN Scans)
- Custom BPF filter expressions
- Live packet display with protocol highlighting
- Automatic .pcap file saving to ~/captures/
- Post-capture analysis: protocol breakdown, top talkers, DNS queries
- Saved capture management (view, analyze, delete)
- First-launch tutorial

## Keyboard

| Key | Action |
|-----|--------|
| 1 | Select interface |
| 2 | Select filter |
| 3 | Start capture |
| 4 | View saved captures |
| c | Stop running capture |
| h | Help |
| q | Quit |

## Dependencies

- `tshark` (Wireshark CLI) -- install with `sudo apt install tshark`

## Files

- Script: `~/.local/bin/gp-capture`
- Captures: `~/captures/*.pcap`
- First-run flag: `~/.config/phantom/.capture-intro`
