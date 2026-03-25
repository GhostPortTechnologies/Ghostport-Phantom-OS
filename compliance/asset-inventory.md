# GhostPort OS -- Asset Inventory

**Document ID:** GP-AI-001
**Version:** 1.0
**Date:** 2026-03-24
**Framework:** NIST SP 800-53 (CM-8 Information System Component Inventory)
**Scope:** GhostPort OS (Raspberry Pi 5 device) and GhostPort Fleet Server (AWS EC2)
**Owner:** GhostPort Technologies

---

## 1. Hardware Assets

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| HW-001 | GhostPort Pi 5 | Raspberry Pi 5, arm64 | On-premises (customer site) | GhostPort Technologies | Internal | 5 | Primary privacy router appliance. Runs GhostPort OS (Raspberry Pi OS, Linux 6.12). 8GB RAM. |
| HW-002 | GhostPort Fleet EC2 | AWS EC2 instance | us-east-1 (AWS) | GhostPort Technologies | Internal | 5 | Fleet API server, WireGuard endpoint, Stripe webhook handler. EIP: 44.214.101.82. Ubuntu 24.04. EBS: 14GB (43% used). AMI snapshot: ghostport-fleet-2026-03-23-stable. |
| HW-003 | EC2 EBS Volume | Block storage | us-east-1 (AWS) | GhostPort Technologies | Internal | 4 | 14GB gp3. Hosts OS, fleet.db, configs. Not encrypted at rest (default). |

---

## 2. Network Interfaces

### 2.1 Pi Interfaces

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| NI-001 | eth0 | Ethernet (WAN) | Pi | GhostPort Technologies | Internal | 5 | Upstream internet. Used in all modes. DHCP from upstream router. |
| NI-002 | wlan0 | WiFi AP (LAN) | Pi | GhostPort Technologies | Internal | 5 | hostapd access point. SSID: GhostPortRouter. 5GHz 802.11ax. Subnet: 192.168.50.0/24. |
| NI-003 | wg0 | WireGuard tunnel | Pi | GhostPort Technologies | Restricted | 5 | Tunnel to EC2 (10.66.66.2). Active in DoubleHop and ZHop modes only. Endpoint: 44.214.101.82:51820. |
| NI-004 | tailscale0 | Tailscale overlay | Pi | GhostPort Technologies | Internal | 5 | Always-on management plane. NEVER stopped. Provides remote SSH and dashboard access. UDP 41641. |

### 2.2 EC2 Interfaces

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| NI-005 | ens5 | Ethernet (primary) | EC2 | GhostPort Technologies | Internal | 5 | Public-facing via EIP 44.214.101.82. Ports 22 (WG-only), 443 (public HTTPS), 51820 (WireGuard). |
| NI-006 | wg0 | WireGuard tunnel | EC2 | GhostPort Technologies | Restricted | 5 | Server endpoint 10.66.66.1. Listens on port 51820. Accepts Pi peer (10.66.66.2). |

---

## 3. Services -- Pi

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| SV-001 | ghostport (Node.js) | Web application server | Pi: `/opt/ghostport/ghostport-server.js` | GhostPort Technologies | Internal | 5 | Express 5 API server. Ports 4200 (HTTP), 4201 (HTTPS). Runs as ghostport-admin. ~2640 lines. Systemd: ghostport.service. |
| SV-002 | Pi-hole FTL | DNS sinkhole / DHCP | Pi: pihole-FTL | GhostPort Technologies | Internal | 5 | DNS filtering for AP clients. DHCP range 192.168.50.10-254. Port 53 (DNS), port 80 (web API). Upstream: 127.0.0.1#5053. Family Shield group-based blocking. |
| SV-003 | dnsmasq | DNS/DHCP (Pi-hole component) | Pi: `/etc/dnsmasq.d/` | GhostPort Technologies | Internal | 4 | Managed by Pi-hole FTL. Cache: 10000 entries. |
| SV-004 | hostapd | WiFi access point | Pi: `/etc/hostapd/hostapd.conf` | GhostPort Technologies | Internal | 5 | Creates wlan0 AP. 5GHz, 802.11ax, WPA2/WPA3. |
| SV-005 | wg-quick@wg0 | WireGuard VPN client | Pi: `/etc/wireguard/wg0.conf` | GhostPort Technologies | Restricted | 5 | Tunnel to EC2. Started/stopped by gp-mode. Only active in DoubleHop/ZHop modes. |
| SV-006 | tailscaled | Tailscale daemon | Pi: `/var/lib/tailscale/` | GhostPort Technologies | Internal | 5 | Always-on. Provides remote management. MUST NOT be stopped in any mode. UDP 41641. |
| SV-007 | cloudflared | DNS-over-HTTPS proxy | Pi: port 5053 | GhostPort Technologies | Internal | 4 | Upstream DNS in ISP and ZeroTrust modes. Proxies to Cloudflare DoH. Listens on 127.0.0.1:5053. |
| SV-008 | fail2ban | Intrusion prevention | Pi: `/etc/fail2ban/` | GhostPort Technologies | Internal | 3 | Monitors SSH and dashboard login attempts. |
| SV-009 | nftables | Firewall | Pi: `/etc/gpmodes/*.nft` | GhostPort Technologies | Internal | 5 | Mode-based firewall profiles: common.nft, isp.nft, zerotrust.nft, doublehop.nft, zhop.nft. Applied by gp-mode script. |
| SV-010 | ghostport-boot | Boot-time mode restore | Pi: `/usr/local/bin/gp-mode-boot` | GhostPort Technologies | Internal | 4 | Systemd: ghostport-boot.service. Restores saved mode from /etc/ghostport/current-mode after boot. |
| SV-011 | ghostport-discord | Discord bot | Pi | GhostPort Technologies | Public | 2 | Optional Discord integration. Systemd: ghostport-discord.service. |

---

## 4. Services -- EC2

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| SV-020 | ghostport-health | Fleet API (Python) | EC2: `/opt/ghostport-fleet/fleet-api.py` | GhostPort Technologies | Internal | 5 | Python Flask/FastAPI. ~1300 lines. Port 8080 (internal). Handles fleet registration, licensing, bridge, Stripe webhooks. Runs as ghostport user. Systemd: ghostport-health.service. |
| SV-021 | nginx | Reverse proxy / TLS termination | EC2: `/etc/nginx/` | GhostPort Technologies | Internal | 5 | HTTPS on port 443. Routes: /webhooks/stripe and /activate/* to fleet API. Let's Encrypt cert (expires 2026-06-20). |
| SV-022 | wg-quick@wg0 | WireGuard VPN server | EC2: `/etc/wireguard/wg0.conf` | GhostPort Technologies | Restricted | 5 | Listens on port 51820. Server: 10.66.66.1. Accepts Pi peers. |
| SV-023 | unbound | DNS resolver | EC2: `/etc/unbound/` | GhostPort Technologies | Internal | 4 | DNS-over-TLS upstream to Cloudflare. Serves Pi DNS queries at 10.66.66.1:53 inside WireGuard tunnel. |
| SV-024 | fail2ban | Intrusion prevention | EC2: `/etc/fail2ban/` | GhostPort Technologies | Internal | 3 | Monitors SSH attempts. |
| SV-025 | ghostport-watchdog | WireGuard peer monitor | EC2 | GhostPort Technologies | Internal | 3 | Monitors WireGuard peer connectivity. Systemd: ghostport-watchdog.service. |
| SV-026 | check-cert.sh | SSL/disk monitoring cron | EC2: `/opt/ghostport-fleet/check-cert.sh` | GhostPort Technologies | Internal | 3 | Daily cron (8am UTC). Alerts at 14 days before cert expiry and 85% disk usage. Sends alerts via bridge. |
| SV-027 | certbot | Let's Encrypt renewal | EC2 | GhostPort Technologies | Internal | 3 | Auto-renews SSL cert. Timer-based (systemd or cron). |

---

## 5. Ports and Protocols

| Asset ID | Port | Protocol | Direction | System | Service | Classification | Criticality | Notes |
|----------|------|----------|-----------|--------|---------|----------------|-------------|-------|
| PT-001 | 22 | TCP/SSH | Inbound | Both | sshd | Internal | 5 | Pi: Tailscale only. EC2: WireGuard tunnel only (not publicly exposed). |
| PT-002 | 53 | UDP+TCP/DNS | Inbound | Both | Pi-hole (Pi), unbound (EC2) | Internal | 5 | Pi: serves AP clients on 192.168.50.0/24. EC2: serves Pi via WireGuard at 10.66.66.1. |
| PT-003 | 80 | TCP/HTTP | Inbound | Pi | Pi-hole web API | Internal | 3 | Localhost only. Used by ghostport-server.js for Pi-hole API calls. |
| PT-004 | 443 | TCP/HTTPS | Inbound | EC2 | nginx | Public | 5 | Public-facing. Stripe webhooks, activation pages, health check. TLS 1.2+. |
| PT-005 | 4200 | TCP/HTTP | Inbound | Pi | ghostport | Internal | 5 | Dashboard HTTP. Accessible from AP (192.168.50.x) and Tailscale. |
| PT-006 | 4201 | TCP/HTTPS | Inbound | Pi | ghostport | Internal | 5 | Dashboard HTTPS. Self-signed SSL cert. |
| PT-007 | 5053 | TCP/DNS-over-HTTPS | Loopback | Pi | cloudflared | Internal | 4 | Local DNS proxy. Used as Pi-hole upstream in ISP/ZeroTrust modes. |
| PT-008 | 8080 | TCP/HTTP | Inbound (WG only) | EC2 | ghostport-health | Internal | 5 | Fleet API. Only reachable via WireGuard tunnel (10.66.66.0/24). |
| PT-009 | 51820 | UDP/WireGuard | Inbound | EC2 | wg-quick@wg0 | Restricted | 5 | WireGuard server endpoint. Publicly accessible on EIP. |
| PT-010 | 41641 | UDP/Tailscale | Outbound | Pi | tailscaled | Internal | 5 | Tailscale WireGuard. Always allowed in all nftables profiles (common.nft). |

---

## 6. Software Dependencies

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| SW-001 | Node.js | Runtime | Pi | Open source | Public | 5 | JavaScript runtime for ghostport-server.js. |
| SW-002 | Express 5 | Framework | Pi: `/opt/ghostport/node_modules/` | Open source | Public | 5 | Web framework. Version ^5.2.1. |
| SW-003 | cors | Middleware | Pi: `/opt/ghostport/node_modules/` | Open source | Public | 3 | CORS handling. Version ^2.8.6. |
| SW-004 | Python 3 | Runtime | EC2 | Open source | Public | 5 | Runtime for fleet-api.py. |
| SW-005 | SQLite 3 | Database engine | EC2 | Open source | Public | 5 | Embedded DB for fleet.db and Pi-hole gravity.db. |
| SW-006 | stripe (pip) | Payment SDK | EC2: pip package | Stripe Inc. | Internal | 5 | Stripe Python SDK v14.4.1. Handles checkout sessions and webhook verification. |
| SW-007 | Let's Encrypt / certbot | TLS certificates | EC2 | ISRG | Public | 4 | Free TLS certs for api.ghostporttechnologies.com. 90-day rotation. |
| SW-008 | Raspberry Pi OS | Operating system | Pi | Raspberry Pi Foundation | Public | 5 | Debian-based. Kernel 6.12. arm64. |
| SW-009 | Ubuntu 24.04 | Operating system | EC2 | Canonical | Public | 5 | EC2 server OS. |
| SW-010 | Pi-hole v6 | DNS filtering | Pi | Open source | Public | 5 | FTL engine. Max 64 API sessions. Gravity-based blocklist. |
| SW-011 | WireGuard | VPN | Both | Open source | Public | 5 | Kernel module. Config via wg-quick. |
| SW-012 | Tailscale | Overlay VPN | Pi | Tailscale Inc. | Internal | 5 | Management plane. Proprietary coordination server. |
| SW-013 | nftables | Firewall | Pi | Open source (netfilter) | Public | 5 | Replaces iptables. Mode profiles in /etc/gpmodes/. |
| SW-014 | hostapd | WiFi AP daemon | Pi | Open source | Public | 5 | 802.11ax, WPA2/WPA3. |
| SW-015 | nginx | Web server / proxy | EC2 | Open source | Public | 5 | Reverse proxy for fleet API. TLS termination. |

---

## 7. External Services

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| EX-001 | Stripe | Payment processing | stripe.com | Stripe Inc. | Restricted | 5 | Live mode. Handles subscriptions for 3 tiers ($5/$10/$15 per month). Webhook at /webhooks/stripe. Keys in stripe.json (mode 0600). |
| EX-002 | UptimeRobot | Uptime monitoring | uptimerobot.com | UptimeRobot | Public | 2 | Monitors https://api.ghostporttechnologies.com/webhooks/health. Alerts on downtime. |
| EX-003 | Tailscale Coordination | VPN coordination | login.tailscale.com | Tailscale Inc. | Internal | 5 | Manages Tailscale node keys and ACLs. Loss of service does not break existing connections but prevents new ones. |
| EX-004 | Let's Encrypt | Certificate authority | letsencrypt.org | ISRG | Public | 4 | Issues TLS certs for api.ghostporttechnologies.com. ACME protocol. |
| EX-005 | Cloudflare | DNS resolver | 1.1.1.1, 1.0.0.1 | Cloudflare Inc. | Public | 4 | Upstream for cloudflared (Pi, DoH) and unbound (EC2, DoT). |
| EX-006 | Moltbook | Social platform | moltbook.com | Moltbook | Public | 1 | Marketing presence. Account: @GhostPortOS. API credentials stored locally. |
| EX-007 | GitHub | Source repository | github.com/GhostPortTechnologies/Ghostport-OS | GitHub / Microsoft | Internal | 3 | Private repo. Mirrors live system files. Not auto-synced. |
| EX-008 | Big Cartel | Hardware sales | bigcartel.com | Big Cartel | Public | 2 | Separate from Stripe. Physical device sales only. |
| EX-009 | AWS | Cloud infrastructure | us-east-1 | Amazon | Internal | 5 | EC2 instance, EBS, EIP. IAM role pending for S3 backups. |

---

## 8. Databases

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| DB-001 | fleet.db | SQLite | EC2: `/opt/ghostport-fleet/fleet.db` | GhostPort Technologies | Internal | 5 | Device registrations, license keys, tenant info, subscriptions, bridge messages. Owned by ghostport user. Backup: AMI snapshot + planned S3 daily. |
| DB-002 | gravity.db | SQLite | Pi: `/etc/pihole/gravity.db` | GhostPort Technologies | Internal | 3 | Pi-hole blocklist database. Rebuildable via `pihole -g`. Contains adlist URLs, domain lists, client groups, FamilyShield group (ID 1). |
| DB-003 | pihole-FTL.db | SQLite | Pi: `/etc/pihole/pihole-FTL.db` | GhostPort Technologies | Internal | 3 | Pi-hole query log database. Contains DNS query history for AP clients. 24h detailed retention, 365d summary. |

---

## 9. Scripts and Utilities

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| SC-001 | gp-mode | Bash script | `/usr/local/bin/gp-mode` | GhostPort Technologies | Internal | 5 | Mode switcher (ISP/ZeroTrust/DoubleHop/ZHop). 60s rollback timer for non-ISP modes. Applies nftables profiles, starts/stops wg0, flushes conntrack. |
| SC-002 | gp-mode-boot | Bash script | `/usr/local/bin/gp-mode-boot` | GhostPort Technologies | Internal | 4 | Boot-time mode restore from /etc/ghostport/current-mode. |
| SC-003 | gp-dns-upstream | Bash script | `/usr/local/bin/gp-dns-upstream` | GhostPort Technologies | Internal | 4 | Switches Pi-hole upstream DNS (cloudflared vs EC2 unbound). |
| SC-004 | gp-dns-switch | Bash script | `/usr/local/bin/gp-dns-switch` | GhostPort Technologies | Internal | 4 | Encrypted DNS toggle (on/off/status). |
| SC-005 | gp-passcode | Bash script | `/usr/local/bin/gp-passcode` | GhostPort Technologies | Restricted | 4 | Passcode manager. show/reset commands. Generates scrypt hashes. |
| SC-006 | gp-new | Bash script | `/usr/local/bin/gp-new` | GhostPort Technologies | Internal | 3 | Fleet registration. Generates license key via POST /fleet/licenses. |

---

## 10. DNS Architecture

| Asset ID | Name | Type | Location | Owner | Classification | Criticality | Notes |
|----------|------|------|----------|-------|----------------|-------------|-------|
| DNS-001 | Pi-hole FTL | Authoritative for LAN | Pi: port 53 | GhostPort Technologies | Internal | 5 | Serves all AP client DNS. DHCP-assigned as gateway DNS. Family Shield group-based filtering. |
| DNS-002 | cloudflared | DoH upstream (ISP/ZeroTrust) | Pi: 127.0.0.1:5053 | GhostPort Technologies | Internal | 4 | DNS-over-HTTPS to Cloudflare 1.1.1.1. Used when WireGuard tunnel is down. |
| DNS-003 | unbound | DoT upstream (DoubleHop/ZHop) | EC2: 10.66.66.1:53 | GhostPort Technologies | Internal | 4 | DNS-over-TLS to Cloudflare. Pi-hole forwards here inside WireGuard tunnel. |
| DNS-004 | Tailscale MagicDNS | Overlay DNS | 100.100.100.100 | Tailscale Inc. | Internal | 3 | Allowed in ZHop mode output chain. Resolves Tailscale hostnames. |

---

## 11. Criticality Scale

| Rating | Meaning | Impact of Loss | Recovery Time Objective |
|--------|---------|----------------|------------------------|
| 5 | Critical | Complete service outage or security breach. Remote access lost. Customer data at risk. | Immediate (< 1 hour) |
| 4 | High | Degraded functionality. Some modes unavailable. DNS resolution impaired. | < 4 hours |
| 3 | Medium | Non-essential feature unavailable. Monitoring gaps. | < 24 hours |
| 2 | Low | Marketing/engagement disruption only. No operational impact. | Best effort |
| 1 | Minimal | Informational. No operational dependency. | No urgency |

---

## 12. Backup and Recovery Summary

| System | Backup Method | Frequency | Location | Last Verified |
|--------|--------------|-----------|----------|---------------|
| EC2 (full) | AMI snapshot | Manual (on change) | AWS us-east-1 | 2026-03-23 |
| fleet.db | Planned S3 sync | Daily (pending IAM) | AWS S3 (planned) | Not yet active |
| Pi configs | .bak files alongside | On change | Local filesystem | Ongoing |
| Pi codebase | Git repo | On commit | GitHub (private) | Ongoing |
| Pi-hole gravity.db | Rebuildable | On demand | `pihole -g` | N/A |
| SSL cert (EC2) | Let's Encrypt reissue | 90-day auto-renew | ACME protocol | Expiry: 2026-06-20 |

---

## 13. Review Schedule

| Action | Frequency | Responsible |
|--------|-----------|-------------|
| Full inventory review | Quarterly | System Owner |
| Verify running services match inventory | Monthly | System Owner |
| Update after infrastructure changes | On change | System Owner |
| Validate backup recoverability | Quarterly | System Owner |
| Review external service dependencies | Semi-annually | System Owner |

---

*This inventory complies with NIST SP 800-53 Rev. 5, Control CM-8 (Information System Component Inventory) and supports controls CM-2 (Baseline Configuration), RA-2 (Security Categorization), and CP-9 (Information System Backup).*
