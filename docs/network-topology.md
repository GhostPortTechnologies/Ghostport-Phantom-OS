# GhostPort Network Topology

**Last updated**: 2026-04-09
**Device**: Raspberry Pi 5, Phantom OS 1.0 (Seadevil)

---

## Physical Layout

```
                    ┌──────────────────────────────────────────┐
                    │            INTERNET                      │
                    └──────┬───────────────┬──────────────┬────┘
                           │               │              │
                    ┌──────┴──────┐ ┌──────┴─────┐ ┌──────┴──────┐
                    │  ISP Gateway │ │ EC2 (wg0)  │ │ EC2 (wg1)   │
                    │ 192.168.0.1  │ │44.214.101.82│ │54.211.104.73│
                    │  Cox Comms   │ │ Control    │ │ Data Plane  │
                    └──────┬──────┘ └──────┬─────┘ └──────┬──────┘
                           │               │              │
                    ┌──────┴───────────────┴──────────────┴────┐
                    │              RASPBERRY PI 5               │
                    │          Phantom OS 1.0 (Seadevil)       │
                    │                                           │
                    │  eth0: 192.168.0.9    (WAN - ISP)        │
                    │  wg0:  10.66.66.2     (Control Plane)    │
                    │  wg1:  10.66.67.2     (Data Plane)       │
                    │  tailscale0: 100.93.206.60 (Management)  │
                    │  wlan0: 192.168.50.1  (LAN AP)           │
                    │  lo:   127.0.0.1      (Loopback)         │
                    └──────────────────┬───────────────────────┘
                                       │ WiFi AP (wlan0)
                                       │ SSID: Incognito
                                       │ 5GHz 802.11ax, 80MHz
                                       │
                    ┌──────────────────┴───────────────────────┐
                    │              LAN CLIENTS                  │
                    │         192.168.50.10 - .100              │
                    │         DHCP, 24hr lease                  │
                    │         DNS → 192.168.50.1 (Pi-hole)     │
                    └──────────────────────────────────────────┘
```

---

## Interfaces

| Interface | IP Address | Role | Always Up? |
|-----------|-----------|------|------------|
| eth0 | 192.168.0.9/24 (DHCP) | WAN uplink to ISP | Yes |
| wlan0 | 192.168.50.1/24 | LAN WiFi access point | Yes |
| tailscale0 | 100.93.206.60/32 | Remote management (Tailnet) | Yes — NEVER stopped |
| wg0 | 10.66.66.2/32 | Control plane tunnel (fleet heartbeat, bridge) | DoubleHop/ZHop only |
| wg1 | 10.66.67.2/32 | Data plane tunnel (internet relay) | DoubleHop/ZHop only |
| lo | 127.0.0.1 | Loopback (Pi-hole, cloudflared) | Yes |

---

## WireGuard Tunnels

### wg0 — Control Plane
- **Endpoint**: 44.214.101.82:51820 (EC2 EIP)
- **Address**: 10.66.66.2/32
- **AllowedIPs**: 10.66.66.0/24 (control subnet only)
- **Purpose**: Fleet heartbeat, bridge messaging, fleet API access
- **EC2 side**: 10.66.66.1 (fleet API on port 8080)

### wg1 — Data Plane
- **Endpoint**: 54.211.104.73:51820 (EC2 EIP)
- **Address**: 10.66.67.2/32
- **AllowedIPs**: 0.0.0.0/0 (full tunnel — all internet traffic)
- **Purpose**: Internet relay for LAN clients in tunnel modes
- **EC2 side**: 10.66.67.1 (Unbound DNS on port 53)

### Tailscale
- **IP**: 100.93.206.60
- **Tailnet**: thomasestrada915@
- **Peers**:
  - EC2: 100.74.199.65 (ip-172-31-71-254)
  - Desktop: 100.65.112.115 (it-testrada, Windows)
  - Gaming PC: 100.112.236.102 (juanye, Windows)
- **CRITICAL**: Tailscale is NEVER stopped in any mode. It is the emergency management plane.

---

## DNS Architecture

```
LAN Client DNS query
        │
        ▼
nftables prerouting (port 53 redirect)
        │
        ▼
   Pi-hole FTL (127.0.0.1:53)
   ├── Blocklists (gravity)
   ├── filter-AAAA (no IPv6 responses)
   └── Upstream:
        │
        ├── ISP/ZeroTrust mode ──→ cloudflared (127.0.0.1:5053)
        │                              └── DoH → 1.1.1.1, 9.9.9.9
        │
        └── DoubleHop/ZHop mode ──→ 10.66.67.1:53 (EC2 Unbound via wg1)
                                        └── Recursive resolution from EC2
```

---

## Traffic Flow by Mode

### ISP Mode
```
LAN Client → wlan0 → NAT (eth0) → ISP Gateway → Internet
DNS: Pi-hole → cloudflared (DoH) → 1.1.1.1/9.9.9.9
```

### ZeroTrust Mode
```
LAN Client → wlan0 → NAT (eth0) → ISP Gateway → Internet
DNS: Pi-hole → cloudflared (DoH) → 1.1.1.1/9.9.9.9
+ DoT (853) blocked, QUIC (UDP 443) blocked, known DoH IPs blocked
```

### DoubleHop Mode
```
LAN Client → wlan0 → NAT (wg1) → EC2 Data Plane → Internet
DNS: Pi-hole → 10.66.67.1:53 (EC2 Unbound via wg1)
+ Output chain blocks DNS on eth0 (leak prevention)
+ cloudflared stopped
```

### ZHop Mode
```
LAN Client → wlan0 → NAT (wg1) → EC2 Data Plane → Internet
DNS: Pi-hole → 10.66.67.1:53 (EC2 Unbound via wg1)
+ Same as DoubleHop + DoT/QUIC blocked
+ cloudflared stopped
```

---

## Listening Services

| Port | Interface | Service | Purpose |
|------|-----------|---------|---------|
| 22 | 192.168.50.1, 100.93.206.60 | sshd | SSH (key-only, no root, no password) |
| 53 | 0.0.0.0 | pihole-FTL | DNS (Pi-hole) |
| 67 | wlan0 | pihole-FTL | DHCP |
| 80 | 192.168.50.1, 127.0.0.1 | pihole-FTL | Pi-hole admin web |
| 443 | 192.168.50.1, 127.0.0.1 | pihole-FTL | Pi-hole admin HTTPS |
| 4200 | 0.0.0.0 | node (ghostport) | Dashboard HTTP |
| 4201 | 0.0.0.0 | node (ghostport) | Dashboard HTTPS |
| 5053 | 127.0.0.1 | cloudflared | DoH proxy (ISP/ZeroTrust only) |

---

## Firewall (nftables)

Profiles in `/etc/gpmodes/`:
- `common.nft` — Base rules (SSH, dashboard, Tailscale, DHCP, ICMP)
- `isp.nft` — Passthrough NAT
- `zerotrust.nft` — DNS-locked passthrough
- `doublehop.nft` — Full tunnel + DNS leak prevention
- `zhop.nft` — Full tunnel + DNS locked + leak prevention

Key rules in all profiles:
- Port 4200/4201 always allowed (dashboard)
- UDP 41641 always allowed (Tailscale)
- tailscale0 always allowed
- SSH from wlan0 + tailscale0 only

---

## EC2 Server

- **Public IPs**: 44.214.101.82 (wg0), 54.211.104.73 (wg1)
- **Domain**: api.ghostporttechnologies.com
- **OS**: Ubuntu 24.04
- **User**: ubuntu (SSH), ghostport (services)
- **SSH**: `ssh -i ~/.ssh/ghostport-ec2.pem ubuntu@10.66.66.1` (via wg0 only)
- **Fleet API**: http://10.66.66.1:8080
- **Services**: ghostport-health, ghostport-watchdog, nginx, unbound
- **Bridge**: POST/GET /messages (HMAC-signed, encrypted)
