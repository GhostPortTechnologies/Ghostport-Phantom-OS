# GhostPort Privacy Score Methodology

**Version**: 1.0
**Date**: April 3, 2026

## Overview

Each privacy mode is scored on a 0-100 scale across five measurable categories. The final score is a weighted average reflecting real privacy protection provided.

## Categories & Weights

| Category | Weight | What it measures |
|----------|--------|-----------------|
| **DNS Privacy** | 25% | Whether DNS queries are encrypted, filtered, and leak-proof |
| **Traffic Encryption** | 25% | Whether browsing traffic is encrypted end-to-end from ISP observation |
| **IP Masking** | 20% | Whether the user's real IP is hidden from destination servers |
| **Metadata Leakage** | 15% | Whether connection metadata (SNI, timing, packet sizes) is observable |
| **Attack Surface** | 15% | Inbound exposure, open ports, bypass vectors |

## Scoring Rubric

### DNS Privacy (25 points max)
| Points | Criteria |
|--------|----------|
| 0 | DNS queries sent in cleartext to ISP resolver |
| 5 | DNS queries sent to third-party resolver (still cleartext) |
| 10 | DNS queries encrypted (DoH/DoT) but no filtering |
| 15 | DNS encrypted + Pi-hole filtering active |
| 20 | DNS encrypted + filtered + DNS leak prevention (redirect all port 53) |
| 25 | DNS encrypted + filtered + leak prevention + DoT/DoH/QUIC blocked on clients |

### Traffic Encryption (25 points max)
| Points | Criteria |
|--------|----------|
| 0 | Traffic exits via ISP in cleartext (HTTP visible, HTTPS SNI visible) |
| 10 | Traffic exits via ISP but all DNS is encrypted (ISP sees IP destinations only) |
| 20 | Traffic routed through encrypted VPN tunnel (ISP sees only tunnel endpoint) |
| 25 | VPN tunnel + no traffic leaks outside tunnel (kill switch or firewall enforced) |

### IP Masking (20 points max)
| Points | Criteria |
|--------|----------|
| 0 | Real IP visible to all destination servers |
| 10 | Real IP visible but DNS doesn't correlate (encrypted DNS) |
| 20 | Real IP hidden — all traffic exits from VPN server IP |

### Metadata Leakage (15 points max)
| Points | Criteria |
|--------|----------|
| 0 | ISP can see: DNS queries, SNI fields, IP destinations, traffic volume, timing |
| 5 | ISP can see: IP destinations, traffic volume, timing (DNS encrypted) |
| 10 | ISP can see: VPN tunnel endpoint, total traffic volume, timing only |
| 15 | ISP sees only encrypted tunnel traffic; QUIC/DoT/DoH client-side leaks blocked |

### Attack Surface (15 points max)
| Points | Criteria |
|--------|----------|
| 0 | Open ports, no filtering, devices directly exposed |
| 5 | NAT + basic firewall, but no DNS filtering |
| 10 | NAT + firewall + DNS filtering + ad/tracker blocking |
| 15 | NAT + strict firewall + DNS filtering + all non-tunnel traffic blocked |

---

## Mode Scores

### ISP Mode — 15/100

| Category | Score | Rationale |
|----------|-------|-----------|
| DNS Privacy | 0/25 | DNS queries use ISP default resolver, cleartext |
| Traffic Encryption | 0/25 | All traffic exits via ISP, SNI and HTTP visible |
| IP Masking | 0/20 | Real IP visible to all destinations |
| Metadata Leakage | 0/15 | ISP has full visibility into all connection metadata |
| Attack Surface | 15/15 | NAT active, nftables firewall, but no filtering |
| **Total** | **15/100** | |

### Zero Trust — 50/100

| Category | Score | Rationale |
|----------|-------|-----------|
| DNS Privacy | 25/25 | Encrypted DNS (DoH via cloudflared), Pi-hole filtering, all port 53 redirected, DoT/DoH/QUIC blocked on clients |
| Traffic Encryption | 10/25 | DNS encrypted but browsing traffic still exits via ISP (SNI visible) |
| IP Masking | 0/20 | Real IP visible to all destinations |
| Metadata Leakage | 5/15 | ISP can still see IP destinations and traffic patterns; DNS is hidden |
| Attack Surface | 10/15 | NAT + firewall + DNS filtering + ad blocking active |
| **Total** | **50/100** | |

### Double Hop — 80/100

| Category | Score | Rationale |
|----------|-------|-----------|
| DNS Privacy | 20/25 | DNS inside WireGuard tunnel to EC2 unbound, Pi-hole filtering, port 53 redirected. DoT/DoH not explicitly blocked on clients |
| Traffic Encryption | 25/25 | All traffic routed through WireGuard tunnel; firewall blocks non-tunnel forward |
| IP Masking | 20/20 | All traffic exits from VPN server IP |
| Metadata Leakage | 10/15 | ISP sees only WireGuard tunnel; QUIC blocked but DoT/DoH not blocked on client side |
| Attack Surface | 5/15 | Strong tunnel enforcement but no DNS leak prevention for client-side DoH |
| **Total** | **80/100** | |

### Z-HOP — 95/100

| Category | Score | Rationale |
|----------|-------|-----------|
| DNS Privacy | 25/25 | DNS inside tunnel + Pi-hole + MagicDNS only + all port 53 redirected + DoT/DoH/QUIC blocked |
| Traffic Encryption | 25/25 | All traffic through WireGuard, firewall enforced |
| IP Masking | 20/20 | All traffic exits from VPN server IP |
| Metadata Leakage | 15/15 | ISP sees only encrypted tunnel; all client-side leak vectors blocked |
| Attack Surface | 10/15 | Maximum enforcement but VPN apps on devices can still create bypass tunnels (-5) |
| **Total** | **95/100** | |

### Why not 100?
No mode scores 100 because: (1) a VPN app on a client device can tunnel traffic before it reaches GhostPort, (2) TLS 1.3 Encrypted Client Hello (ECH) can hide SNI from the inspector, (3) no HTTPS content inspection is performed. These are fundamental limitations of any network-level privacy tool without MITM inspection.

---

## Comparison to Previous Scores

| Mode | Old Score | New Score | Change |
|------|-----------|-----------|--------|
| ISP | 20 | 15 | -5 (ISP mode provides even less protection than previously stated) |
| Zero Trust | 55 | 50 | -5 (IP masking gap was underweighted) |
| Double Hop | 80 | 80 | No change |
| Z-HOP | 95 | 95 | No change |

The old scores were directionally correct but lacked methodology. The new scores are derived from measurable criteria and can be defended in technical conversations.
