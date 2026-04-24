# gp-ids — Intrusion Detection System

Lightweight network IDS for Phantom OS. Monitors firewall logs, connection flows, and packet headers to detect threats on your AP network.

## Detection Sources
1. **Firewall logs** — parses `[GhostPort-DROP]` entries from nftables via journald
2. **Connection tracking** — analyzes conntrack flow table every 10s
3. **DNS analysis** — detects excessive DNS queries (tunneling indicator)

## What It Detects
- **Port scans** — 10+ unique ports probed from same IP in 60s
- **Brute force** — repeated connections to same service
- **SYN floods** — TCP connection storms (DoS)
- **ICMP floods** — ping storms
- **DNS tunneling** — excessive unique DNS queries from one host
- **Data exfiltration** — single flows over 100MB
- **C2 beaconing** — connections to known command-and-control ports

## Usage
```
gp-ids                Interactive TUI dashboard
gp-ids monitor        Headless daemon (log + desktop notifications)
gp-ids status         Show detection stats
gp-ids clear          Clear alert history
```

## TUI Controls
- `j/k` — scroll alerts
- `p` — pause/resume
- `c` — clear history
- `e` — export to file
- `d` — show detail + educational context
- `h` — help
- `q` — quit

## Data Files
- Log: `~/.config/phantom/ids/ids.log`
- Events JSON: `/etc/phantom/ids-events.json`
- Waybar module: `gp-bar-ids`
