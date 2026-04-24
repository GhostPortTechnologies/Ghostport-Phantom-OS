# Phantom OS — Pen Testing Instructions

## Overview

This document covers how to simulate network intrusions at all three severity levels (DANGER, WARNING, INFO) to test the Crow's Nest IDS dashboard and the nftables firewall. It also lists the core features that must be verified across all 15 GTK desktop apps.

---

## Understanding the Architecture

GhostPort's firewall uses nftables with a default-deny policy. Packets dropped by the firewall are logged to the kernel ring buffer with the prefix `GhostPort-DROP`. The Crow's Nest app (`gp-crowsnest.py`) reads `dmesg` every 5 seconds, parses these lines, classifies each drop by severity, and displays them as color-coded alert cards.

**Key interfaces:**
- `eth0` — WAN side (ISP subnet, e.g. 192.168.0.x)
- `wlan0` — LAN side (AP subnet, 192.168.50.x)
- `wg0` — WireGuard control plane (10.66.66.0/24)
- `wg1` — WireGuard data plane (10.66.67.0/24)
- `tailscale0` — Always-on management plane

**What triggers GhostPort-DROP:** Unsolicited inbound packets on eth0 that don't match an established/related connection. WireGuard and Tailscale traffic is explicitly allowed and will NOT trigger drops.

---

## Why EC2 Claude Cannot Simulate Intrusions

- EC2 traffic arrives via WireGuard tunnels (wg0/wg1), which are whitelisted in nftables — no drops triggered
- Sending packets to the Pi's public IP won't work because the Pi sits behind the ISP's NAT router — inbound packets from the internet never reach eth0 directly
- The 632 real drops we observed came from devices on the ISP's local subnet (192.168.0.x), not from the internet

---

## Method 1: Inject Fake Kernel Log Entries (Recommended for UI Testing)

Write directly to `/dev/kmsg` to simulate drops without any actual network traffic. This is instant, safe, and controllable. Crow's Nest parses these identically to real drops.

### DANGER Level (Red Cards)

```bash
# SSH scan attempt
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.99 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=54321 DPT=22" | sudo tee /dev/kmsg

# Telnet probe
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=10.0.0.5 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=44444 DPT=23" | sudo tee /dev/kmsg

# RDP connection attempt
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.174 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=50000 DPT=3389" | sudo tee /dev/kmsg

# SMB/CIFS connection attempt
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.200 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=49999 DPT=445" | sudo tee /dev/kmsg

# Potential C2 on port 9999
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.174 DST=192.168.50.255 LEN=60 PROTO=UDP SPT=9999 DPT=9999" | sudo tee /dev/kmsg
```

### WARNING Level (Amber Cards)

```bash
# NetBIOS name service
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.50 DST=192.168.0.255 LEN=78 PROTO=UDP SPT=137 DPT=137" | sudo tee /dev/kmsg

# LIFX smart bulb discovery (unauthenticated IoT)
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.174 DST=192.168.0.255 LEN=36 PROTO=UDP SPT=56700 DPT=56700" | sudo tee /dev/kmsg

# DNS query attempt blocked
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=192.168.50.1 LEN=64 PROTO=UDP SPT=1024 DPT=53" | sudo tee /dev/kmsg

# DNS-over-TLS attempt blocked
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.30 DST=1.1.1.1 LEN=44 PROTO=TCP SPT=55555 DPT=853" | sudo tee /dev/kmsg

# HTTP probe on port 8080
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.105 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=33333 DPT=8080" | sudo tee /dev/kmsg

# Unsolicited WAN inbound on high port
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.77 DST=192.168.50.1 LEN=52 PROTO=TCP SPT=12345 DPT=4444" | sudo tee /dev/kmsg
```

### INFO Level (Blue/Dim Cards)

```bash
# IGMP multicast query from router
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=224.0.0.1 LEN=32 PROTO=2" | sudo tee /dev/kmsg

# mDNS service discovery
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.42 DST=224.0.0.251 LEN=64 PROTO=UDP SPT=5353 DPT=5353" | sudo tee /dev/kmsg

# UPnP/SSDP discovery
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=239.255.255.250 LEN=175 PROTO=UDP SPT=1900 DPT=1900" | sudo tee /dev/kmsg

# Spotify Connect peer discovery
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.60 DST=192.168.0.255 LEN=44 PROTO=UDP SPT=57621 DPT=57621" | sudo tee /dev/kmsg

# DHCP request
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=0.0.0.0 DST=255.255.255.255 LEN=300 PROTO=UDP SPT=68 DPT=67" | sudo tee /dev/kmsg

# ICMP ping
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=192.168.50.1 LEN=84 PROTO=ICMP" | sudo tee /dev/kmsg

# Generic broadcast
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.88 DST=192.168.0.255 LEN=60 PROTO=UDP SPT=40000 DPT=40000" | sudo tee /dev/kmsg
```

### Run All At Once (Full Spectrum Test)

```bash
#!/bin/bash
# gp-pentest-simulate — Inject all severity levels into dmesg
# Run this, then open Crow's Nest and wait 5 seconds for the poll

echo "Injecting DANGER events..."
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.99 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=54321 DPT=22" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=10.0.0.5 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=44444 DPT=23" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.174 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=50000 DPT=3389" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.200 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=49999 DPT=445" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.174 DST=192.168.50.255 LEN=60 PROTO=UDP SPT=9999 DPT=9999" | sudo tee /dev/kmsg > /dev/null

echo "Injecting WARNING events..."
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.50 DST=192.168.0.255 LEN=78 PROTO=UDP SPT=137 DPT=137" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.174 DST=192.168.0.255 LEN=36 PROTO=UDP SPT=56700 DPT=56700" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=192.168.50.1 LEN=64 PROTO=UDP SPT=1024 DPT=53" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.105 DST=192.168.50.1 LEN=44 PROTO=TCP SPT=33333 DPT=8080" | sudo tee /dev/kmsg > /dev/null

echo "Injecting INFO events..."
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=224.0.0.1 LEN=32 PROTO=2" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.42 DST=224.0.0.251 LEN=64 PROTO=UDP SPT=5353 DPT=5353" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=239.255.255.250 LEN=175 PROTO=UDP SPT=1900 DPT=1900" | sudo tee /dev/kmsg > /dev/null
echo "<4>GhostPort-DROP IN=eth0 OUT= MAC=00:00:00:00:00:00 SRC=192.168.0.1 DST=192.168.50.1 LEN=84 PROTO=ICMP" | sudo tee /dev/kmsg > /dev/null

echo ""
echo "Done. 13 simulated drops injected."
echo "Open Crow's Nest — it will pick them up within 5 seconds."
echo "Expected: 5 DANGER (red), 4 WARNING (amber), 4 INFO (blue)"
```

---

## Method 2: Real Packet Testing with nmap (Actual Firewall Exercise)

Requires `nmap` installed on a device that can reach eth0. This sends real packets that nftables actually processes and drops.

### From a device on the ISP subnet (192.168.0.x)

```bash
# Get the Pi's eth0 IP
ETH0_IP=$(ssh ghostport-admin@<pi-ip> "ip -4 addr show eth0 | grep inet | awk '{print \$2}' | cut -d/ -f1")

# DANGER: SSH scan
nmap -sS -p 22 $ETH0_IP

# DANGER: SMB + RDP + Telnet
nmap -sS -p 23,445,3389 $ETH0_IP

# WARNING: HTTP probes
nmap -sS -p 80,443,8080 $ETH0_IP

# WARNING: DNS + NetBIOS
nmap -sU -p 53,137,138 $ETH0_IP

# INFO: ICMP ping
ping -c 5 $ETH0_IP

# Full port scan (will generate many drops)
nmap -sS -p 1-1024 $ETH0_IP
```

### From the Pi itself (loopback test via eth0 IP)

```bash
# Get eth0 IP
ETH0_IP=$(ip -4 addr show eth0 | grep inet | awk '{print $2}' | cut -d/ -f1)

# Quick scan — these hit nftables input chain
sudo nmap -sS -p 22,23,445,3389,9999 $ETH0_IP
sudo nmap -sU -p 53,137,5353,1900,56700 $ETH0_IP
```

**Note:** Loopback tests may not trigger GhostPort-DROP depending on nftables chain order. Method 1 (kmsg injection) is more reliable for pure UI testing.

---

## Method 3: Physical Device on ISP Subnet

The most realistic test. Plug a laptop or phone into the same ISP router/switch as the Pi's eth0:

1. Connect to the ISP network (same subnet as Pi's eth0)
2. Find the Pi's eth0 IP: `arp-scan --localnet` or check router's DHCP table
3. Run nmap scans from the laptop targeting that IP
4. Watch Crow's Nest light up with real drops

This is exactly how the original 632-drop discovery happened — real devices on the ISP subnet probing the Pi's eth0.

---

## Core Features to Test — All 15 GTK Desktop Apps

Each app must pass these checks:

### Universal Checks (Every App)

- [ ] Launches from start menu with correct pirate name
- [ ] Launches from desktop icon
- [ ] Window title shows correct app name
- [ ] Theme colors match current accent (change theme, verify within 5s)
- [ ] Single instance — launching twice raises existing window, doesn't spawn duplicate
- [ ] Resize works, content adapts
- [ ] Close button works, process actually exits (no zombie)
- [ ] No crash on empty/missing data (first boot scenario)
- [ ] Status bar shows meaningful info

### Crow's Nest (IDS Dashboard)

- [ ] Displays real GhostPort-DROP entries from dmesg
- [ ] Correct severity classification (red/amber/blue cards)
- [ ] Stats bar updates: total drops, critical count, threat level
- [ ] Threat level transitions: NOMINAL -> LOW -> ELEVATED -> HIGH
- [ ] Timestamp conversion from kernel time to wall clock
- [ ] Protocol display (TCP/UDP/ICMP/IGMP)
- [ ] Source:port -> Dest:port connection line
- [ ] Plain-English threat descriptions
- [ ] Pause/Resume stops/starts polling
- [ ] Clear All empties the display
- [ ] Export Log saves valid JSON via file picker
- [ ] Refresh forces immediate data reload
- [ ] Scrolls smoothly with 200 entries
- [ ] New events appear at top (newest first)

### Bulkhead (Firewall Builder)

- [ ] Lists current nftables rules with plain-English explanations
- [ ] Add Rule dialog works (protocol, port, IP, action)
- [ ] Delete rule with confirmation
- [ ] Protected rules cannot be deleted
- [ ] Rule changes apply immediately via nft
- [ ] Presets panel loads correctly

### Dragnet (Packet Capture)

- [ ] Live capture tab: interface dropdown populates
- [ ] Start/Stop capture with tshark
- [ ] Live packet list updates in real time
- [ ] Filter presets work
- [ ] Save to .pcap file
- [ ] File analysis tab: open existing .pcap
- [ ] Protocol color coding
- [ ] 500 packet display limit respected

### Anchor (Kill Switch)

- [ ] Large ARM/DISARM toggle works
- [ ] Tunnel status cards show wg0/wg1/tailscale0 state
- [ ] Kill switch actually modifies nftables killswitch chain
- [ ] Status polls every 3 seconds
- [ ] Visual feedback on arm/disarm (color change)

### Aether Box (Encrypted Vault)

- [ ] File browser shows vault contents
- [ ] Lock/unlock with password dialog
- [ ] First-time init flow creates vault
- [ ] Add files via file chooser
- [ ] Auto-lock timer works

### Tide Chart (Bandwidth Heatmap)

- [ ] Cairo heatmap renders colored grid
- [ ] Interface selector works
- [ ] Live sampling from /proc/net/dev every 5s
- [ ] Visual upgrade from ASCII — colored rectangles visible

### Sonar (Rogue AP Scanner)

- [ ] Lists detected APs from `iw scan`
- [ ] Identifies our own AP (Incognito)
- [ ] Evil twin detection (same SSID, different BSSID) highlighted red
- [ ] Open/WEP networks highlighted amber
- [ ] Signal strength bars
- [ ] Trust/untrust AP functionality
- [ ] Trusted APs persist in JSON

### Crew Manifest (Connected Clients)

- [ ] Reads DHCP leases from dnsmasq
- [ ] Shows online/offline status via ARP check
- [ ] MAC vendor lookup works
- [ ] Randomized MAC detection (locally-administered bit)
- [ ] Online/offline/total counts in header
- [ ] Polls every 10 seconds

### Atlas (Network Topology)

- [ ] Cairo diagram renders nodes and edges
- [ ] Shows correct interfaces (eth0, wlan0, tunnels if UP)
- [ ] Connected clients appear as nodes
- [ ] Current mode label on router node
- [ ] Green=UP, red=DOWN edge colors
- [ ] Polls every 10 seconds

### Stonefish (ARP Guard)

- [ ] TreeView shows ARP table entries
- [ ] Detects duplicate MACs (SPOOFING alert)
- [ ] Gateway MAC change detection vs saved baseline
- [ ] Save Baseline / Clear Alerts buttons
- [ ] Color-coded threat column
- [ ] Polls every 5 seconds

### Seadevil (MAC Randomizer)

- [ ] Shows interface cards for eth0 and wlan0
- [ ] Current MAC and permanent MAC displayed
- [ ] Randomize button generates locally-administered MAC
- [ ] Restore button reverts to original
- [ ] Original MACs saved to JSON on first run

### Gangplank (USB Drive Manager)

- [ ] Detects USB drives via lsblk JSON parsing
- [ ] Shows model, size, filesystem, mount status
- [ ] Mount/Eject/Open in Files buttons work
- [ ] Safe eject: sync + unmount + power-off
- [ ] Notification on eject
- [ ] Polls for hotplug every 5 seconds
- [ ] No crash when no USB drive present

### Sea Urchin (System Diagnostics)

- [ ] 6 cairo gauge arcs render (CPU, temp, RAM, disk, uptime, load)
- [ ] Color coding: green < 60%, amber < 85%, red >= 85%
- [ ] Temperature warning at 70C
- [ ] Service status panel (ghostport, sni, dns-guard, health-guard)
- [ ] Polls every 5 seconds

### Logbook (Event Log)

- [ ] Reads /etc/phantom/activity.json
- [ ] TreeView with Timestamp/Category/Description columns
- [ ] Sortable columns
- [ ] Category filter dropdown works
- [ ] Text search entry works
- [ ] Handles missing/empty file gracefully
- [ ] 30-second auto-refresh

### Quartermaster (Security Scan)

- [ ] Runs 10 security checks (firewall, SSH, DNS, VPN, Pi-hole, etc.)
- [ ] Big score number with color coding
- [ ] Each check shows PASS/FAIL with description
- [ ] Fix suggestions for failed checks
- [ ] Export report to text file
- [ ] Async scan doesn't freeze UI

---

## Known Issues to Fix During Testing

- Export dialog in Crow's Nest says "Sentinel" (old name) — should say "Crow's Nest"
- Crow's Nest has no filtering/search by IP, port, or severity
- Crow's Nest sends no notification on new critical events
- Crow's Nest Clear only clears the display — dmesg still has the entries, they'll reappear on next poll unless paused

---

## Post-Testing Cleanup

```bash
# Clear injected test entries from kernel ring buffer
sudo dmesg -C

# Verify Crow's Nest shows empty after clearing + refresh
# (open app, click Clear All, then Refresh)
```
