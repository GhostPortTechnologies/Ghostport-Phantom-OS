# Dragnet — Packet Capture & Analysis

GUI packet capture tool. Captures live traffic with tshark, analyses saved pcap files, and shows a live flow / protocol breakdown during capture. The TUI equivalent is `gp-capture` (see `gp-capture.md`).

## When To Use It
- Diagnose a stuck connection or weird DNS behaviour
- See what a specific client is actually sending (SNI inspector pairs well)
- Open and analyse a `.pcap` you captured elsewhere

## Features

- Interface picker: eth0 (WAN), wlan0 (LAN AP), wg0 / wg1 (tunnels), tailscale0
- BPF filter presets (HTTP/S, DNS, SSH, DHCP, ARP, ICMP, WireGuard, SYN scans) + custom filters
- **Live stats sidebar during capture** — packets/sec, bytes/sec, protocol pie chart
- **Flow inspector** — groups packets into conntrack-style flows for readability
- Post-capture analysis: protocol breakdown, top talkers, DNS query list
- Saves to `~/captures/dragnet_<timestamp>.pcap`
- 100 MB per-file cap (set in source; edit `MAX_PCAP_SIZE_KB` if you need more)

## Safety

- Capture requires passwordless sudo for tshark (already configured)
- pcap files contain ALL traffic on the selected interface — treat them as sensitive
- The 100 MB cap exists to prevent a runaway capture filling the SD card

## Dependencies

- `tshark` — install with `sudo apt install tshark`
- The app detects missing tshark on launch and shows an install prompt instead of crashing

## Troubleshooting

| Symptom | Check |
|---------|-------|
| "tshark not found" | `which tshark` — run `sudo apt install tshark`. The app will re-enable capture after install. |
| Interface list is empty | `ip -br addr` — is the interface up? wg0/wg1 only appear in DoubleHop / ZHop modes |
| Capture stops immediately | Check free disk: `df -h ~/captures`. App aborts if `<100 MB` free |
| `.pcap` opens blank | You captured with a filter that matched nothing. Re-try with no filter to confirm traffic is flowing |

## Files

- App: `/opt/phantom/desktop/gp-dragnet.py`
- Icon: `/opt/phantom/desktop/icons/gp-dragnet.svg`
- Captures: `~/captures/*.pcap`
- TUI counterpart: `~/.local/bin/gp-capture`
- Related: `gp-bilge` (live flow inspector, TUI)
