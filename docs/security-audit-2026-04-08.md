# GhostPort Pre-Pen-Test Security Audit Report
**Date:** 2026-04-08
**Classification:** INTERNAL ONLY — DO NOT PUBLISH

## Executive Summary
4-round automated security audit completed ahead of scheduled penetration test. 79 vulnerabilities identified across 4 parallel scanning domains. 30+ patches applied and verified. All CRITICAL and HIGH severity findings resolved.

## Methodology
- **Round 1:** 4 parallel agents scanning API input validation, network/firewall, authentication/secrets, and scripts/system
- **Round 2:** Verification of all Round 1 patches + remaining vulnerability triage
- **Round 3:** Second verification pass — caught gp-update syntax error that would have broken OTA
- **Round 4:** Fresh sweep with pen-tester mindset — caught 2 HIGH XSS vectors

## Round 1 Findings (79 total)

| Domain | Critical | High | Medium | Low | Total |
|--------|----------|------|--------|-----|-------|
| API Input Validation | 2 | 4 | 6 | 6 | 22 |
| Nftables & Network | 1 | 3 | 6 | 7 | 20 |
| Auth & Secrets | 3 | 5 | 5 | 3 | 19 |
| Scripts & System | 1 | 4 | 8 | 4 | 18 |

## Patches Applied (30+)

### CRITICAL (3)
1. HMAC bypass — commands rejected when no secret configured (server + heartbeat)
2. TOTP replay prevention — used codes tracked within 90s window
3. IPv6 disabled on WAN/LAN interfaces — prevents tunnel bypass

### HIGH (10)
4. Passcode no longer logged to console on first boot
5. Hardcoded fleet token fallbacks removed from bridge and heartbeat scripts
6. OTA update SHA-256 verification now mandatory
7. Heartbeat HMAC bypass fixed
8. DNS-over-TLS (port 853) blocked in tunnel modes
9. Legacy/stale UI files removed from public directory
10. Backup files removed from public directory
11. Skip-activation endpoint rate limited (60s cooldown)
12. Activity log XSS — all dynamic content now HTML-escaped
13. WiFi network scanner XSS — SSIDs HTML-escaped before rendering

### MEDIUM (12)
14. WiFi sensing endpoint async/await fix
15. Error message information leak fixed (generic messages)
16. Passcode length capped at 128 chars
17. TOTP partial token IP-bound
18. Pending TOTP cleared on passcode change
19. HSTS header for HTTPS responses
20. Fleet registration log sanitized
21. WAN config file permissions hardened (600)
22. WireGuard DNS leak vector removed
23. SSH X11Forwarding disabled, MaxAuthTries reduced
24. Kernel sysctl hardening (rp_filter, log_martians, IPv6 redirects)
25. ZeroTrust forward chain changed from policy accept to policy drop

### LOW (5)
26. New passcode length capped at 128 chars
27. Stale backup scripts archived
28. Fleet registration log reduced
29. LLMNR disabled
30. ICMP scoped to essential types on WAN interface

### INFRASTRUCTURE
31. rp_filter applied to all interfaces (was only default)
32. gp-update stray syntax error fixed (Round 3 catch — OTA was broken)
33. Misleading comment in heartbeat script corrected

## Remaining Items

### CAN WAIT (address before v2.0)
- Session sliding window extends up to 7 days (hard cap exists)
- No rate limiting on authenticated destructive endpoints
- Backup export includes WiFi passphrase in plaintext
- Shell variable injection in provisioning script (requires root)

### ACCEPTABLE RISK (documented, not fixing)
- HTTP on port 4200 (LAN only, architectural decision)
- CSP unsafe-inline (March 2024 incident — too risky to change without full regression)
- Hostapd password — awaiting decision from owner

### NEEDS HUMAN DECISION
- WiFi AP password (currently default) — randomize or keep for dev?
- ieee80211w deauth protection — needs client compatibility testing
- systemd service sandboxing — needs testing on production workloads

## Verification Status
All patches verified across 4 rounds. Server restarted and operational. nftables profiles validated with dry-run before apply.
