# GhostPort OS -- Data Classification Policy

**Document ID:** GP-DC-001
**Version:** 1.0
**Date:** 2026-03-24
**Framework:** NIST SP 800-60 / NIST SP 800-171
**Scope:** GhostPort OS (Raspberry Pi 5 device) and GhostPort Fleet Server (AWS EC2)
**Owner:** GhostPort Technologies

---

## 1. Classification Tiers

| Tier | Label | Description | Handling Requirements |
|------|-------|-------------|----------------------|
| **Restricted** | RED | Cryptographic secrets, authentication material, API keys. Compromise leads to full system takeover, financial loss, or customer data breach. | Encryption required at rest and in transit. Access limited to service accounts and root. No logging of values. Rotate on suspected exposure. |
| **Internal** | AMBER | Operational data, device registrations, system configs, logs. Not public but not cryptographic. Compromise enables reconnaissance or service disruption. | Access-controlled (file permissions, authenticated APIs). Encrypted in transit. Backup required. Retain per operational need. |
| **Public** | GREEN | Marketing content, documentation, health endpoints. Designed for public consumption. | No restrictions. Available without authentication. |

---

## 2. Restricted Data (RED)

| # | Data Type | Location | System | Encryption at Rest | Encryption in Transit | Backup | Retention |
|---|-----------|----------|--------|--------------------|-----------------------|--------|-----------|
| R-01 | Stripe API key (sk_live_*) | `/opt/phantom-fleet/stripe.json` | EC2 | Filesystem permissions (mode 0600, owner: ghostport). EBS volume not encrypted by default. | Yes (HTTPS to Stripe API) | AMI snapshot (2026-03-23) | Rotate on exposure; no expiry |
| R-02 | Stripe webhook signing secret (whsec_*) | `/opt/phantom-fleet/stripe.json` | EC2 | Mode 0600, owner: ghostport | Yes (HTTPS inbound via nginx TLS) | AMI snapshot | Rotate on exposure |
| R-03 | WireGuard private key (Pi) | `/etc/wireguard/wg0.conf` | Pi | Filesystem permissions (mode 0600, owner: root) | N/A (never transmitted) | .bak file alongside | Permanent; rotate if compromised |
| R-04 | WireGuard private key (EC2) | `/etc/wireguard/wg0.conf` | EC2 | Filesystem permissions (mode 0600, owner: root) | N/A (never transmitted) | AMI snapshot | Permanent; rotate if compromised |
| R-05 | Admin passcode hash + salt | `/etc/phantom/auth.json` | Pi | Filesystem permissions (mode 0600). Scrypt hash (not reversible). | Yes (HTTPS on port 4201) | .bak file alongside | Regenerated on reset via gp-passcode |
| R-06 | Fleet auth tokens (bridge Bearer token) | `/opt/phantom-fleet/auth.json` | EC2 | Mode 0600, owner: ghostport | Yes (HTTPS via nginx; WireGuard tunnel for internal) | AMI snapshot | Rotate on exposure |
| R-07 | SSL private key (Pi) | `/opt/phantom/ssl/` | Pi | Filesystem permissions | N/A (used locally for TLS termination) | Not backed up externally | Renewed with cert |
| R-08 | SSL private key (EC2 / Let's Encrypt) | `/etc/letsencrypt/live/api.ghostporttechnologies.com/` | EC2 | Filesystem permissions (mode 0600) | N/A (used locally for TLS termination) | AMI snapshot | Auto-renewed every 90 days; expires 2026-06-20 |
| R-09 | WiFi WPA passphrase | `/etc/hostapd/hostapd.conf` | Pi | Filesystem permissions | WPA3/WPA2 over air | .bak file alongside | Permanent; change via hostapd config |
| R-10 | Pi-hole API password | `/etc/phantom/pihole.json` | Pi | Mode 0600, owner: ghostport-admin | Yes (localhost HTTP only, port 80) | .bak file alongside | Regenerated on Pi-hole reinstall |

### Restricted Data Controls
- Files MUST be mode 0600 or 0640, owned by service account or root.
- Values MUST NOT appear in application logs, bridge messages, or error output.
- Stripe keys, webhook secrets, and bridge tokens MUST be rotated immediately upon suspected exposure.
- Sensitive customer/payment data (Stripe tokens, PII) MUST NEVER be stored on the Pi -- all billing logic lives on EC2.

---

## 3. Internal Data (AMBER)

| # | Data Type | Location | System | Encryption at Rest | Encryption in Transit | Backup | Retention |
|---|-----------|----------|--------|--------------------|-----------------------|--------|-----------|
| I-01 | fleet.db (device registrations, tenants, license keys, subscriptions) | `/opt/phantom-fleet/fleet.db` | EC2 | No (plaintext SQLite on EBS) | Yes (WireGuard tunnel for API; HTTPS for activation) | AMI snapshot; daily S3 backup planned | Permanent (customer records) |
| I-02 | Bridge messages (inter-Claude messaging) | In-memory + fleet.db `messages` table | EC2 | No (SQLite) | Yes (HTTPS + Bearer auth) | AMI snapshot | Last 200 messages retained |
| I-03 | auth.json (Pi dashboard auth config) | `/etc/phantom/auth.json` | Pi | No | Yes (HTTPS port 4201) | .bak file | Permanent |
| I-04 | pihole.json (Pi-hole API credentials) | `/etc/phantom/pihole.json` | Pi | No | Localhost only | .bak file | Permanent |
| I-05 | arsenal.json (security tools config) | `/etc/phantom/arsenal.json` | Pi | No | Yes (HTTPS port 4201) | .bak file | Permanent |
| I-06 | family-shield.json (parental control config) | `/etc/phantom/family-shield.json` | Pi | Mode 0600, owner: ghostport-admin | Yes (HTTPS port 4201) | No external backup | Permanent |
| I-07 | nginx configs | `/etc/nginx/sites-available/` | EC2 | No | N/A | AMI snapshot | Permanent |
| I-08 | WireGuard peer configs (public keys, endpoints, allowed IPs) | `/etc/wireguard/wg0.conf` | Both | Mode 0600 (bundled with private key file) | N/A | .bak (Pi), AMI (EC2) | Permanent |
| I-09 | nftables firewall profiles | `/etc/gpmodes/*.nft` | Pi | No | N/A | Git repo at /opt/phantom/ | Permanent |
| I-10 | System logs (journald) | `/var/log/journal/` | Both | No | N/A | No external backup | Default journald rotation |
| I-11 | Pi-hole query logs | Pi-hole FTL database | Pi | No | N/A | No external backup | Pi-hole default (24h detailed, 365d summary) |
| I-12 | Pi-hole gravity.db (blocklists) | `/etc/pihole/gravity.db` | Pi | No | N/A | Rebuildable via `pihole -g` | Rebuilt on update |
| I-13 | Device serial numbers | `/proc/cpuinfo` (Pi), fleet.db (EC2) | Both | No | Yes (WireGuard tunnel during fleet checkin) | AMI snapshot (EC2) | Permanent |
| I-14 | current-mode state | `/etc/phantom/current-mode` | Pi | No | N/A | .bak file | Overwritten on mode switch |
| I-15 | Moltbook API credentials | `~/.config/moltbook/credentials.json` | Pi | Filesystem permissions | Yes (HTTPS to Moltbook API) | No external backup | Permanent |
| I-16 | dnsmasq config | `/etc/dnsmasq.d/` | Pi | No | N/A | .bak files | Permanent |
| I-17 | hostapd config (non-secret fields) | `/etc/hostapd/hostapd.conf` | Pi | No | N/A | .bak file | Permanent |
| I-18 | Tailscale node key / auth state | `/var/lib/tailscale/` | Pi | Filesystem permissions | Yes (Tailscale encrypted tunnel) | No external backup | Managed by tailscaled |
| I-19 | Unbound config | `/etc/unbound/unbound.conf.d/` | EC2 | No | N/A | AMI snapshot | Permanent |
| I-20 | cloudflared config | `/etc/cloudflared/` or systemd unit | Pi | No | Yes (DNS-over-HTTPS to Cloudflare) | .bak file | Permanent |

### Internal Data Controls
- Config files SHOULD be owned by the appropriate service user with restrictive permissions.
- Logs MUST NOT contain Restricted-tier values (API keys, passwords, private keys).
- Database backups (fleet.db) SHOULD be encrypted before offsite transfer (S3).
- Internal data MAY be shared between GhostPort service accounts but MUST NOT be exposed to AP clients or public endpoints.

---

## 4. Public Data (GREEN)

| # | Data Type | Location | System | Encryption at Rest | Encryption in Transit | Backup | Retention |
|---|-----------|----------|--------|--------------------|-----------------------|--------|-----------|
| P-01 | Blog content | blog.ghostporttechnologies.com | External hosting | N/A (hosted platform) | Yes (HTTPS) | Platform managed | Permanent |
| P-02 | Marketing materials | ghostporttechnologies.com | External hosting | N/A | Yes (HTTPS) | Platform managed | Permanent |
| P-03 | API documentation | README, CLAUDE.md in repo | GitHub (private repo) | GitHub encryption | Yes (HTTPS) | Git history | Permanent |
| P-04 | Health check endpoint | `GET /webhooks/health` | EC2 (nginx) | N/A | Yes (HTTPS) | N/A | N/A |
| P-05 | Activation page HTML | `GET /activate/<key>` | EC2 (nginx + fleet API) | N/A | Yes (HTTPS) | AMI snapshot | Permanent |
| P-06 | Stripe checkout redirect | `POST /activate/<key>/checkout` | EC2 | N/A | Yes (HTTPS to Stripe) | N/A | N/A |
| P-07 | Success/error pages | `/activate/success`, `/activate/invalid` | EC2 | N/A | Yes (HTTPS) | AMI snapshot | Permanent |
| P-08 | Moltbook public posts | moltbook.com/@GhostPortOS | External platform | N/A | Yes (HTTPS) | Platform managed | Permanent |
| P-09 | WireGuard public keys | In wg0.conf (public portion), exchanged during setup | Both | N/A (public by design) | N/A | With config files | Permanent |

### Public Data Controls
- Public endpoints MUST NOT leak Internal or Restricted data in responses or error messages.
- License keys in activation URLs are single-use tokens, not secrets -- but SHOULD NOT be enumerable.
- Health endpoints MUST return only status indicators, not system internals.

---

## 5. Data Flow Summary

```
Customer Phone
    |
    | HTTPS (TLS 1.2+)
    v
EC2 nginx (443) --> Fleet API (8080)
    |                    |
    | Stripe HTTPS       | SQLite (fleet.db)
    v                    |
Stripe API          WireGuard tunnel (10.66.66.0/24)
                         |
                         v
                    Pi (10.66.66.2)
                         |
                    +----+----+
                    |         |
               Pi-hole     Dashboard (4200/4201)
                    |         |
                    v         v
               AP clients (192.168.50.x)
```

---

## 6. Review Schedule

| Action | Frequency | Responsible |
|--------|-----------|-------------|
| Review classification assignments | Quarterly | System Owner |
| Audit file permissions on Restricted data | Monthly | System Owner |
| Rotate Stripe API keys | Annually or on exposure | System Owner |
| Verify backup integrity (AMI, .bak files) | Monthly | System Owner |
| Review and purge unnecessary logs | Quarterly | System Owner |

---

*This document aligns with NIST SP 800-60 Vol. 2 (information type categorization) and NIST SP 800-171 r2 (CUI protection controls 3.1, 3.8, 3.13). GhostPort does not process CUI but applies equivalent rigor to cryptographic material and customer billing data.*
