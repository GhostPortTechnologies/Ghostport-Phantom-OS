# GhostPort OS Risk Register

**Aligned to:** NIST SP 800-30 Rev. 1 (Guide for Conducting Risk Assessments)
**Scope:** GhostPort OS (Raspberry Pi 5 privacy router) + EC2 fleet management infrastructure
**Prepared:** 2026-03-24
**Review cycle:** Quarterly
**Classification:** Internal / Confidential

---

## Risk Scoring Matrix

| | Low Impact | Medium Impact | High Impact | Critical Impact |
|---|---|---|---|---|
| **High Likelihood** | Medium | High | Critical | Critical |
| **Medium Likelihood** | Low | Medium | High | Critical |
| **Low Likelihood** | Low | Low | Medium | High |

---

## Category 1: Infrastructure Compromise

### RISK-001 — EC2 Fleet Server Compromise

| Field | Content |
|---|---|
| ID | RISK-001 |
| Asset | EC2 instance (44.214.101.82) — fleet API, SQLite DB, WireGuard server, Stripe integration, nginx |
| Threat | Remote attacker exploits a vulnerability in the Python fleet API, nginx, or OS to gain shell access. The fleet API (~1300 lines) accepts unauthenticated requests on public endpoints (/activate/*, /webhooks/stripe). A code injection, SSRF, or deserialization flaw could yield RCE. |
| Likelihood | Medium |
| Impact | Critical — attacker gains access to fleet.db (all license keys, device serials, subscription data), Stripe API keys (stripe.json), WireGuard private keys, and the Claude bridge auth token. Could push malicious commands to all fleet devices via bridge API. |
| Risk Level | Critical |
| Current Controls | SSH restricted to WireGuard tunnel only (port 22 not public). Fleet API runs as unprivileged `ghostport` user. stripe.json is mode 600. nginx reverse proxy limits public surface to /webhooks/stripe and /activate/*. Ubuntu 24.04 with unattended-upgrades. |
| Residual Risk | High — the fleet API is a custom Python application without formal security audit. SQLite DB and Stripe keys are on the same host. No WAF, no IDS, no file integrity monitoring. |
| Recommended Action | 1. Add rate limiting to all public endpoints via nginx. 2. Run fleet API behind a WAF (AWS WAF or fail2ban at minimum). 3. Implement input validation audit on fleet-api.py. 4. Move Stripe webhook secret verification to nginx layer. 5. Enable CloudWatch or OSSEC for intrusion detection. 6. Consider separating Stripe keys into AWS Secrets Manager. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-002 — Raspberry Pi OS-Level Compromise

| Field | Content |
|---|---|
| ID | RISK-002 |
| Asset | Raspberry Pi 5 running GhostPort OS — Node.js server, nftables firewall, Pi-hole, WireGuard, Tailscale |
| Threat | Attacker on the LAN (connected to WiFi AP) exploits the Express server (port 4200/4201), Pi-hole web interface (port 80), or a local service to gain code execution on the Pi. The Express server executes shell commands via `child_process.exec` with sudo privileges for gp-* commands. |
| Likelihood | Medium |
| Impact | Critical — full control of the router. Attacker could disable firewall, intercept all LAN traffic, exfiltrate DNS queries, pivot to EC2 via WireGuard tunnel, or brick the device. |
| Risk Level | Critical |
| Current Controls | Passcode auth with scrypt hashing and timing-safe comparison. 5-attempt lockout with 60s cooldown. Session cookies are HttpOnly + SameSite=Strict. Security headers (CSP, X-Frame-Options DENY, no-referrer). JSON body limit 10kb. x-powered-by disabled. Passwordless sudo restricted to specific gp-* commands only. |
| Residual Risk | High — `child_process.exec` is used (not `execFile`), which is vulnerable to command injection if any user input reaches the shell string. The server runs on 0.0.0.0 and is reachable by any LAN client. No automated security patching for Node.js dependencies. |
| Recommended Action | 1. Audit all `exec()` calls for command injection — migrate to `execFile()` where possible. 2. Restrict Pi-hole admin interface to localhost or require auth. 3. Add fail2ban for SSH and port 4200. 4. Implement automatic OS security updates. 5. Consider running the Express server in a systemd sandbox (ProtectSystem, NoNewPrivileges). |
| Owner | Firmware Lead |
| Status | Open |

### RISK-003 — Tailscale Management Plane Compromise

| Field | Content |
|---|---|
| ID | RISK-003 |
| Asset | Tailscale (tailscale0 interface) — always-on remote management access to Pi |
| Threat | Attacker compromises the Tailscale account (phishing, credential stuffing) or a Tailscale coordination server vulnerability. Gains SSH and dashboard access to the Pi remotely, bypassing all nftables firewall modes. |
| Likelihood | Low |
| Impact | Critical — remote root-equivalent access to the Pi from anywhere on the internet. Tailscale is explicitly never stopped and is allowed through all firewall profiles. |
| Risk Level | High |
| Current Controls | Tailscale uses WireGuard encryption. Tailscale account presumably uses MFA. SSH access requires key authentication. |
| Residual Risk | Medium — Tailscale is a third-party dependency that provides always-on, unrestricted network access. If the Tailscale control plane is compromised, there is no secondary gate. |
| Recommended Action | 1. Enforce MFA on the Tailscale admin account. 2. Use Tailscale ACLs to restrict which devices can reach the Pi and on which ports. 3. Consider adding SSH key rotation and audit logging. 4. Document a Tailscale compromise response plan (how to cut access from the Pi side). |
| Owner | Infrastructure Lead |
| Status | Open |

---

## Category 2: Data Breach

### RISK-004 — Fleet Database Exfiltration

| Field | Content |
|---|---|
| ID | RISK-004 |
| Asset | /opt/ghostport-fleet/fleet.db (SQLite) — license keys, device serials, subscription status, customer tier data, device checkin history |
| Threat | Attacker gains read access to fleet.db via EC2 compromise (RISK-001), SQL injection in fleet API, or backup exfiltration. Database contains all license keys, device serial numbers (from /proc/cpuinfo), and subscription metadata. |
| Likelihood | Medium |
| Impact | High — exposure of all customer license keys enables subscription fraud and device impersonation. Device serials could be used for targeted attacks. Combined with Stripe customer IDs, enables correlation to payment data. |
| Risk Level | High |
| Current Controls | Database file owned by ghostport user. Fleet API only accessible via WireGuard tunnel (except public activation endpoints). No PII (names, emails, addresses) stored in fleet.db — Stripe holds customer PII separately. |
| Residual Risk | Medium — SQLite has no encryption at rest. No database access logging. Backup strategy unknown. A single compromised query parameter could dump the entire DB. |
| Recommended Action | 1. Implement SQLite encryption at rest (sqlcipher). 2. Add parameterized query audit to fleet-api.py. 3. Implement automated encrypted backups to S3 with versioning. 4. Add database access logging. 5. Minimize data retention — purge stale device checkin records. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-005 — Stripe API Key Exposure

| Field | Content |
|---|---|
| ID | RISK-005 |
| Asset | /opt/ghostport-fleet/stripe.json — Stripe secret key, webhook signing secret |
| Threat | Stripe API keys leaked via EC2 compromise, accidental git commit, log output, or error message disclosure. Live Stripe keys (not test) are in production. |
| Likelihood | Low |
| Impact | Critical — attacker with the Stripe secret key can issue refunds, create charges, access customer payment data (cards, emails, addresses), modify subscriptions, and exfiltrate all customer PII held by Stripe. |
| Risk Level | High |
| Current Controls | stripe.json is mode 600, owned by ghostport user. File excluded from git via .gitignore. Secrets never stored on Pi — only EC2. |
| Residual Risk | Medium — keys are stored as plaintext JSON on disk. No key rotation policy. No monitoring for anomalous Stripe API usage. A single EC2 compromise exposes live payment credentials. |
| Recommended Action | 1. Migrate Stripe keys to AWS Secrets Manager or Parameter Store. 2. Implement restricted Stripe API keys (least privilege). 3. Set up Stripe webhook IP allowlisting. 4. Enable Stripe Radar and anomaly alerts. 5. Establish key rotation schedule (quarterly). 6. Add Stripe API usage monitoring and alerting. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-006 — Claude Bridge Auth Token Exposure

| Field | Content |
|---|---|
| ID | RISK-006 |
| Asset | Bridge API bearer token (hardcoded: ***REVOKED-TOKEN***) — controls Claude-to-Claude messaging |
| Threat | Bridge auth token is stored in memory files, project documentation, and likely in fleet-api.py source code. If leaked, an attacker can send arbitrary commands to both Pi and EC2 Claude instances, read all bridge message history, and impersonate either side. |
| Likelihood | Medium |
| Impact | High — attacker can issue commands via the bridge that Claude instances will execute. Bridge messages include status, config, command, and alert types. Could instruct Pi Claude to modify firewall rules, restart services, or exfiltrate data. |
| Risk Level | High |
| Current Controls | Bearer token auth required for all bridge endpoints. Bridge only reachable via WireGuard tunnel (port 8080 not publicly exposed). |
| Residual Risk | Medium — the token appears in multiple documentation files and memory files that could be committed to git or exposed via context leaks. Single static token with no rotation. No per-message signing or replay protection. |
| Recommended Action | 1. Rotate the bridge token immediately (it appears in this risk register's source material). 2. Remove token from all documentation and memory files. 3. Implement token rotation mechanism. 4. Add message signing (HMAC) to prevent tampering. 5. Add rate limiting to bridge endpoints. 6. Implement message expiry/TTL. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-007 — Pi-side Credential Exposure

| Field | Content |
|---|---|
| ID | RISK-007 |
| Asset | /etc/ghostport/auth.json (passcode hash+salt), /etc/ghostport/pihole.json (Pi-hole password), /etc/ghostport/arsenal.json (feature state), WireGuard private key, SSL certificates |
| Threat | LAN attacker or compromised process reads sensitive config files from the Pi filesystem. auth.json contains the scrypt hash that protects the dashboard. pihole.json contains the Pi-hole admin password in plaintext. |
| Likelihood | Low |
| Impact | High — dashboard takeover, Pi-hole admin access, ability to modify DNS filtering, disable security features. WireGuard key exposure allows tunnel impersonation. |
| Risk Level | Medium |
| Current Controls | auth.json is mode 600. Server runs as ghostport-admin (not root). Secrets excluded from git via .gitignore. Passcode uses scrypt with 32-byte random salt. |
| Residual Risk | Low — file permissions are appropriate. Scrypt is computationally expensive to brute-force. However, pihole.json stores password in cleartext, and the ghostport-admin user has broad sudo access. |
| Recommended Action | 1. Audit all config file permissions (ensure 600 for all sensitive files). 2. Encrypt pihole.json password at rest. 3. Restrict ghostport-admin sudo to exact command paths (no wildcards). 4. Implement WireGuard key rotation schedule. |
| Owner | Firmware Lead |
| Status | Open |

---

## Category 3: Service Disruption

### RISK-008 — WireGuard Tunnel Failure (DoubleHop/ZHop)

| Field | Content |
|---|---|
| ID | RISK-008 |
| Asset | WireGuard tunnel (wg0) — routes all LAN traffic through EC2 in DoubleHop and ZHop modes |
| Threat | WireGuard tunnel drops due to EC2 outage, ISP blocking UDP 51820, NAT traversal failure, or key mismatch. In DoubleHop/ZHop modes, all LAN traffic routes through wg0 — tunnel loss means complete internet outage for all AP clients. |
| Likelihood | Medium |
| Impact | High — total loss of internet connectivity for all devices connected to the GhostPort WiFi AP. Users may not understand the cause or how to recover. |
| Risk Level | High |
| Current Controls | 60-second mode rollback timer auto-reverts to previous mode if not confirmed. Kill switch feature monitors wg0 and drops all forwarding if tunnel goes down (prevents DNS leaks). `gp-mode rollback` CLI command available. ISP mode is always available as safe fallback. Repair endpoints in API (`/api/repair/wireguard`). |
| Residual Risk | Medium — rollback timer only applies during mode switches, not ongoing tunnel failures after confirmation. Kill switch drops traffic (security-correct) but does not auto-recover. No automated tunnel health monitoring with auto-failover. EC2 is a single point of failure. |
| Recommended Action | 1. Implement continuous tunnel health monitoring (periodic ping to 10.66.66.1). 2. Add auto-failover to ISP mode after N consecutive tunnel failures. 3. Add user notification when tunnel degrades. 4. Consider multi-region EC2 failover. 5. Add WireGuard keepalive tuning for NAT traversal reliability. |
| Owner | Firmware Lead |
| Status | Open |

### RISK-009 — DNS Resolution Failure

| Field | Content |
|---|---|
| ID | RISK-009 |
| Asset | DNS resolution chain — dnsmasq -> Pi-hole -> cloudflared (ISP/ZeroTrust) or EC2 unbound (DoubleHop/ZHop) |
| Threat | DNS resolution fails due to Pi-hole crash, dnsmasq misconfiguration, cloudflared failure, or EC2 unbound outage. Multiple DNS components in series creates a fragile chain. Any single component failure breaks name resolution for all AP clients. |
| Likelihood | Medium |
| Impact | High — no DNS means effectively no internet for all connected devices, even if IP connectivity is intact. |
| Risk Level | High |
| Current Controls | gp-dns-switch auto-rolls back if DNS resolution fails after encrypted DNS toggle. Repair endpoint (`/api/repair/dns`) restarts the DNS stack. Pi-hole uses cache (dnsmasq cache set to 10,000 entries). Diagnostics endpoint checks DNS health. |
| Residual Risk | Medium — no automated DNS failover. Multiple components in series (dnsmasq, Pi-hole, upstream resolver) with no health-check loop. cloudflared and unbound are single instances with no redundancy. |
| Recommended Action | 1. Implement DNS health watchdog (periodic resolution test, auto-restart on failure). 2. Add fallback upstream DNS (e.g., if cloudflared fails, fall back to direct DoT). 3. Monitor Pi-hole memory usage (known to OOM on Pi with large blocklists). 4. Add DNS resolution latency to status dashboard. |
| Owner | Firmware Lead |
| Status | Open |

### RISK-010 — Mode Switch Lockout

| Field | Content |
|---|---|
| ID | RISK-010 |
| Asset | nftables firewall profiles (/etc/gpmodes/*.nft) — control all network routing |
| Threat | A malformed nftables rule is applied, locking the admin out of the dashboard (port 4200) and SSH. Or a mode switch partially applies, leaving the firewall in an inconsistent state. conntrack flush during switch briefly drops all established connections. |
| Likelihood | Low |
| Impact | Critical — loss of remote management access. If Tailscale is also blocked (common.nft misconfiguration), the device becomes a brick requiring physical SD card access. |
| Risk Level | Medium |
| Current Controls | nftables profiles validated with `nft -c -f` (dry-run) before applying. common.nft always allows port 4200, UDP 41641 (Tailscale), tailscale0 interface, and SSH. 60-second rollback timer reverts to previous mode. Current ruleset backed up before each switch. ISP mode as safe fallback. Boot service reapplies saved mode (with ISP as default in /etc/nftables.conf). |
| Residual Risk | Low — the multi-layered safety system (dry-run validation, rollback timer, Tailscale always-on, boot fallback) significantly mitigates this risk. Residual risk is from common.nft itself being corrupted, which would affect all modes. |
| Recommended Action | 1. Add integrity check for common.nft (checksum validation before apply). 2. Implement a hardware watchdog timer that reboots to ISP mode if heartbeat stops. 3. Keep a read-only backup of known-good common.nft on a separate partition. 4. Document physical recovery procedure for total lockout. |
| Owner | Firmware Lead |
| Status | Mitigated |

### RISK-011 — EC2 Instance Failure

| Field | Content |
|---|---|
| ID | RISK-011 |
| Asset | EC2 instance — fleet API, WireGuard server, Stripe webhook receiver, DNS resolver, Claude bridge |
| Threat | EC2 instance terminates, crashes, or becomes unreachable due to AWS outage, EBS failure, or misconfiguration. Single instance with no redundancy. |
| Likelihood | Medium |
| Impact | High — all DoubleHop/ZHop users lose internet (WireGuard tunnel down). Fleet API offline means no new activations, no subscription management, missed Stripe webhooks. Claude bridge communication lost. |
| Risk Level | High |
| Current Controls | EBS-backed instance (data survives stop/start). EIP assigned (static public IP survives instance replacement). EBS snapshots (frequency unknown). |
| Residual Risk | High — single instance, single availability zone, no auto-recovery. No load balancer, no auto-scaling group. Stripe webhooks have retry logic but will eventually fail. WireGuard tunnel has no failover. |
| Recommended Action | 1. Configure EC2 auto-recovery (CloudWatch alarm on StatusCheckFailed_System). 2. Implement automated EBS snapshots (daily minimum). 3. Create AMI-based disaster recovery runbook. 4. Consider multi-AZ deployment for the fleet API. 5. Set up Stripe webhook failure alerting. 6. Implement Pi-side graceful degradation when EC2 is unreachable. |
| Owner | Infrastructure Lead |
| Status | Open |

---

## Category 4: Payment and Billing

### RISK-012 — Stripe Webhook Forgery

| Field | Content |
|---|---|
| ID | RISK-012 |
| Asset | Stripe webhook endpoint (https://api.ghostporttechnologies.com/webhooks/stripe) — processes checkout.session.completed, subscription updates, subscription deletions |
| Threat | Attacker sends forged webhook payloads to the public endpoint to fraudulently activate subscriptions, upgrade tiers, or cancel legitimate subscriptions. |
| Likelihood | Medium |
| Impact | High — fraudulent subscription activations (free service), revenue loss from forged cancellations, data integrity corruption in fleet.db. |
| Risk Level | High |
| Current Controls | Stripe webhook signature verification (assumed — standard Stripe SDK practice). HTTPS transport. |
| Residual Risk | Medium — webhook endpoint is publicly accessible. If signature verification has implementation bugs (e.g., not checking the raw body, timing-safe comparison), forgery is possible. No alerting on unusual webhook patterns. |
| Recommended Action | 1. Verify webhook signature validation code uses Stripe SDK's built-in verification with raw body. 2. Implement webhook event idempotency (deduplicate by event ID). 3. Add IP allowlisting for Stripe webhook source IPs. 4. Set up alerting for unusual webhook volume or patterns. 5. Log all webhook events for audit trail. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-013 — Subscription Fraud via License Key Abuse

| Field | Content |
|---|---|
| ID | RISK-013 |
| Asset | License key activation system — GP-XXXX-XXXX-XXXX format keys, activation flow, tier assignment |
| Threat | Attacker brute-forces license keys (format is predictable: GP- + 3 groups of 4 chars from a 30-char alphabet = ~24 billion combinations), reuses a key on multiple devices, or exploits the activation flow to get service without payment. |
| Likelihood | Low |
| Impact | Medium — unauthorized service access, revenue loss. Limited blast radius since Pi has no sensitive data (billing stays on Stripe). |
| Risk Level | Low |
| Current Controls | License keys generated with crypto.randomInt (CSPRNG). Keys tied to device serial on activation (one key per device). Subscription status gated by Stripe payment confirmation via webhook. |
| Residual Risk | Low — keyspace is large enough to resist brute-force. Activation requires Stripe payment. However, rate limiting on the activation endpoint is unknown. |
| Recommended Action | 1. Add rate limiting to /fleet/activate and /activate/*/checkout endpoints. 2. Monitor for activation attempts with invalid keys (potential brute-force). 3. Implement key revocation capability. 4. Add device count limit per license key. |
| Owner | Infrastructure Lead |
| Status | Accepted |

---

## Category 5: Supply Chain

### RISK-014 — Node.js Dependency Compromise

| Field | Content |
|---|---|
| ID | RISK-014 |
| Asset | Node.js runtime and npm packages on the Pi (Express 5 and dependencies) |
| Threat | A compromised npm package (typosquatting, maintainer account takeover, or malicious update) introduces backdoor code into the Express server. The server runs with sudo access to system commands. No lockfile auditing or dependency pinning process documented. |
| Likelihood | Medium |
| Impact | Critical — compromised dependency runs with the same privileges as the Express server, including passwordless sudo for gp-* commands. Could exfiltrate all config files, modify firewall rules, or install persistent backdoors. |
| Risk Level | Critical |
| Current Controls | No build step (direct execution). Express is a well-maintained framework. Server uses a limited set of dependencies (express, crypto, fs, path — mostly Node.js built-ins). |
| Residual Risk | High — no `npm audit` in CI/CD, no automated dependency scanning, no Software Bill of Materials (SBOM). No integrity verification of installed packages. |
| Recommended Action | 1. Run `npm audit` and fix all known vulnerabilities. 2. Pin all dependency versions in package-lock.json. 3. Set up automated dependency scanning (Dependabot, Snyk, or npm audit in cron). 4. Generate and maintain an SBOM. 5. Consider vendoring critical dependencies. 6. Minimize dependency count — audit if all npm packages are necessary. |
| Owner | Firmware Lead |
| Status | Open |

### RISK-015 — Python Dependency Compromise (EC2)

| Field | Content |
|---|---|
| ID | RISK-015 |
| Asset | Python runtime and pip packages on EC2 (fleet-api.py, Stripe SDK, Discord bot dependencies) |
| Threat | Compromised Python package in the fleet API or Discord bot introduces malicious code with access to Stripe keys, fleet database, and WireGuard configuration. |
| Likelihood | Low |
| Impact | Critical — access to Stripe API keys, fleet database, and ability to push commands to all devices via bridge. |
| Risk Level | High |
| Current Controls | Fleet API runs as unprivileged ghostport user. Discord bot runs in a venv. Stripe SDK (v14.4.1) is a well-maintained package. |
| Residual Risk | Medium — no dependency scanning, no pinning audit, no SBOM for the EC2 side. |
| Recommended Action | 1. Pin all Python dependencies with hash verification (pip --require-hashes). 2. Run safety/pip-audit regularly. 3. Minimize installed packages. 4. Consider running fleet API in a container for isolation. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-016 — Raspberry Pi OS Update Regression

| Field | Content |
|---|---|
| ID | RISK-016 |
| Asset | Raspberry Pi OS (Bookworm 64-bit) — kernel, system packages, firmware |
| Threat | An OS update breaks nftables syntax, changes network interface behavior, updates hostapd/dnsmasq in incompatible ways, or introduces kernel regressions that affect WiFi AP or WireGuard performance. GhostPort depends on specific kernel module behavior (nft, conntrack, wireless). |
| Likelihood | Medium |
| Impact | High — broken firewall rules, WiFi AP failure, or WireGuard incompatibility could brick the device for non-technical users. |
| Risk Level | High |
| Current Controls | No automatic OS updates (manual process). nftables profiles tested on specific Pi OS version. Rollback timer prevents persistent bad states from mode switches. |
| Residual Risk | Medium — no controlled update pipeline. No pre-release testing environment. Customers may manually run `apt upgrade` and break their device. |
| Recommended Action | 1. Implement controlled firmware update mechanism (gp-update script with rollback). 2. Pin critical package versions (nftables, hostapd, dnsmasq, wireguard-tools). 3. Disable unattended-upgrades on customer devices. 4. Maintain a test Pi for validating OS updates before pushing to fleet. 5. Document recovery procedure for bricked devices. |
| Owner | Firmware Lead |
| Status | Open |

---

## Category 6: Physical Security

### RISK-017 — Stolen Pi Device

| Field | Content |
|---|---|
| ID | RISK-017 |
| Asset | Physical Raspberry Pi 5 device — SD card contains OS, configs, credentials, WireGuard keys |
| Threat | Device is physically stolen. Attacker extracts the SD card and mounts it on another system. All files are readable including auth.json (passcode hash), WireGuard private key, pihole.json, SSL certificates, and potentially WiFi credentials for the upstream network. |
| Likelihood | Low |
| Impact | High — full access to all on-device secrets. WireGuard key allows tunnel impersonation (connecting to EC2 as this device). WiFi credentials expose the user's home network. auth.json hash could be brute-forced offline. |
| Risk Level | Medium |
| Current Controls | auth.json uses scrypt hashing (expensive to brute-force). No PII or payment data stored on Pi (by design — billing lives on Stripe/EC2). |
| Residual Risk | Medium — SD card is not encrypted. All secrets are readable with physical access. No remote wipe capability. No device deactivation mechanism in fleet API. |
| Recommended Action | 1. Implement SD card encryption (LUKS on the data partition). 2. Add remote device deactivation in fleet API (revoke WireGuard key, mark device as stolen). 3. Store WireGuard private key in tmpfs (regenerated on boot from encrypted seed). 4. Implement device health attestation (fleet API detects cloned device). 5. Document customer procedure for reporting stolen device. |
| Owner | Firmware Lead |
| Status | Open |

### RISK-018 — SD Card Corruption

| Field | Content |
|---|---|
| ID | RISK-018 |
| Asset | MicroSD card — primary storage for OS, configs, and all application data |
| Threat | SD card develops bad sectors due to write wear, power loss during write, or manufacturing defect. Raspberry Pi SD cards are notoriously failure-prone under sustained write loads (logging, DNS cache, Pi-hole databases). |
| Likelihood | High |
| Impact | High — device becomes unbootable or exhibits random failures. Customer loses all configuration. No automated backup mechanism. |
| Risk Level | Critical |
| Current Controls | .bak files maintained alongside configs (same SD card — doesn't help with card failure). Mode state persisted to /etc/ghostport/current-mode. |
| Residual Risk | High — no wear leveling strategy, no read-only root filesystem, no remote backup of device configuration. Pi-hole gravity DB and FTL database generate significant write I/O. |
| Recommended Action | 1. Move high-write-volume files to tmpfs (logs, Pi-hole FTL database, dnsmasq cache). 2. Implement log rotation with aggressive limits. 3. Use industrial-grade SD cards (SLC or pSLC). 4. Implement config backup to EC2 via fleet API heartbeat. 5. Create a device recovery image that auto-provisions from fleet API. 6. Consider overlay filesystem (read-only root with tmpfs overlay). |
| Owner | Firmware Lead |
| Status | Open |

---

## Category 7: Insider Threat

### RISK-019 — Compromised Admin Credentials (ghostport-admin)

| Field | Content |
|---|---|
| ID | RISK-019 |
| Asset | ghostport-admin user account — runs the Express server, has passwordless sudo for gp-* commands, SSH access via Tailscale |
| Threat | Admin SSH key or Tailscale access is compromised. Attacker gains shell access as ghostport-admin, which has passwordless sudo for system commands. Can modify server code, firewall rules, and all configuration files. |
| Likelihood | Low |
| Impact | Critical — full device control, ability to intercept all LAN traffic, modify DNS responses, exfiltrate data, and pivot to EC2 via WireGuard. |
| Risk Level | High |
| Current Controls | SSH key-based authentication (no password auth). Tailscale ACLs (assumed). Passwordless sudo restricted to specific gp-* commands via /etc/sudoers.d/. |
| Residual Risk | Medium — sudo restrictions limit the blast radius but gp-mode can apply arbitrary nftables rules. No audit logging of sudo commands. No session recording. No MFA for SSH. |
| Recommended Action | 1. Implement SSH audit logging (auditd). 2. Add MFA for SSH access (e.g., Tailscale SSH with identity verification). 3. Audit sudoers.d for overly permissive rules. 4. Implement command allowlisting in sudoers (full paths, no wildcards). 5. Set up alerting for unusual SSH login patterns. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-020 — Malicious Claude Bridge Command Injection

| Field | Content |
|---|---|
| ID | RISK-020 |
| Asset | Claude-to-Claude bridge — enables AI instances to send commands between Pi and EC2 |
| Threat | A compromised or hallucinating Claude instance sends destructive commands via the bridge (e.g., "delete fleet.db", "disable firewall", "stop Tailscale"). The bridge accepts arbitrary message types including "command" with no authorization beyond the bearer token. There is no human approval gate for commands. |
| Likelihood | Low |
| Impact | High — automated execution of destructive commands across infrastructure. Bridge messages persist until acknowledged, so a malicious command queue could survive Claude instance restarts. |
| Risk Level | Medium |
| Current Controls | Bearer token authentication. Bridge only reachable via WireGuard tunnel. Message types are validated (status, alert, config, response, query, heartbeat, command, text). |
| Residual Risk | Medium — no command allowlisting, no human-in-the-loop approval, no rate limiting, no anomaly detection. A Claude instance with the bearer token has unrestricted command capability. |
| Recommended Action | 1. Implement command allowlisting (only pre-approved commands can be executed). 2. Add human approval requirement for destructive commands. 3. Implement command audit logging. 4. Add rate limiting per message type. 5. Consider separating read and write tokens. |
| Owner | Infrastructure Lead |
| Status | Open |

---

## Category 8: Network Attacks

### RISK-021 — DNS Bypass / Leak

| Field | Content |
|---|---|
| ID | RISK-021 |
| Asset | DNS filtering chain — Pi-hole blocklists, encrypted DNS, DNS leak prevention |
| Threat | A sophisticated LAN client bypasses Pi-hole DNS filtering by using DNS-over-HTTPS directly to an external resolver (e.g., 8.8.8.8:443), effectively circumventing parental controls (Family Shield) and ad blocking. In ISP mode, there is no DNS filtering at all. |
| Likelihood | Medium |
| Impact | Medium — privacy protection is bypassed. Family Shield parental controls become ineffective. Ad blocking circumvented. Users believe they are protected when they are not. |
| Risk Level | Medium |
| Current Controls | ZeroTrust and ZHop modes block DoT/DoH ports (TCP 853, common DoH endpoints). DNS prerouting rules in DoubleHop/ZHop force all port 53 traffic through Pi-hole. DNS leak test available in Arsenal tools. Kill switch auto-trips on DNS leak detection (optional). |
| Residual Risk | Medium — DoH over port 443 is indistinguishable from HTTPS traffic without deep packet inspection. ZeroTrust blocks known DoH IPs but cannot catch all. ISP mode offers no protection. Hardcoded DNS in client devices (Android Private DNS, Firefox DoH) may bypass. |
| Recommended Action | 1. Implement SNI-based filtering to block known DoH providers. 2. Add a transparent DNS proxy that intercepts all DNS traffic regardless of destination port. 3. Document which modes provide which level of DNS protection. 4. Consider implementing TLS inspection for DoH detection (significant privacy/complexity tradeoff). 5. Add DNS bypass detection alerting. |
| Owner | Firmware Lead |
| Status | Accepted |

### RISK-022 — ARP Spoofing on LAN

| Field | Content |
|---|---|
| ID | RISK-022 |
| Asset | WiFi LAN (wlan0 AP, 192.168.50.0/24) — all connected client devices |
| Threat | A malicious device connected to the GhostPort WiFi AP performs ARP spoofing to intercept traffic from other LAN clients, conduct man-in-the-middle attacks, or impersonate the gateway. |
| Likelihood | Low |
| Impact | High — traffic interception, credential theft, DNS spoofing at the LAN level. Bypasses all upstream protections (Pi-hole, VPN, firewall). |
| Risk Level | Medium |
| Current Controls | WPA3/WPA2 encryption on WiFi AP. Clients are on an isolated /24 subnet. nftables rules control forwarding. |
| Residual Risk | Medium — no ARP inspection, no client isolation (AP clients can communicate with each other), no 802.1X authentication. Any device with the WiFi password can join and attack other clients. |
| Recommended Action | 1. Enable AP client isolation in hostapd (ap_isolate=1) to prevent client-to-client communication. 2. Implement static ARP entries for the gateway. 3. Consider 802.1X for enterprise deployments. 4. Add connected client monitoring with MAC anomaly detection. |
| Owner | Firmware Lead |
| Status | Open |

### RISK-023 — DDoS Against Pi or EC2

| Field | Content |
|---|---|
| ID | RISK-023 |
| Asset | Pi (port 4200/4201 on LAN) and EC2 (public IP 44.214.101.82, ports 443, 51820) |
| Threat | DDoS attack against EC2 public endpoints saturates bandwidth or exhausts resources. On the Pi, a LAN client floods port 4200 with requests, exhausting Node.js event loop or memory. |
| Likelihood | Low (Pi — requires LAN access), Medium (EC2 — public IP) |
| Impact | High — EC2 DDoS takes down fleet API, WireGuard server, and activation flow for all customers. Pi DDoS disables dashboard for the local user. |
| Risk Level | Medium (Pi), High (EC2) |
| Current Controls | Express body parser limited to 10kb. Login rate limiting (5 attempts/60s per IP). EC2 has no DDoS protection service. |
| Residual Risk | High for EC2 — no AWS Shield, no CloudFront, no rate limiting on nginx, static EIP exposed. Medium for Pi — limited to LAN attackers. |
| Recommended Action | 1. Enable AWS Shield Standard (free, auto-enrolled) and verify it is active. 2. Add nginx rate limiting for all public endpoints. 3. Consider CloudFront in front of api.ghostporttechnologies.com. 4. Implement connection limits in nftables on the Pi. 5. Add Express request rate limiting middleware (express-rate-limit). |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-024 — WiFi Deauthentication Attack

| Field | Content |
|---|---|
| ID | RISK-024 |
| Asset | WiFi access point (wlan0, hostapd, 5GHz 802.11ax) |
| Threat | Attacker sends deauthentication frames to disconnect all clients from the GhostPort AP. While 802.11w (PMF) mitigates this on WPA3, older WPA2 clients remain vulnerable. This is a trivial attack with commodity hardware. |
| Likelihood | Medium |
| Impact | Medium — temporary denial of service for WiFi clients. Clients auto-reconnect but experience disruption. Could be used in conjunction with an evil twin attack. |
| Risk Level | Medium |
| Current Controls | 5GHz band (shorter range reduces attack surface). WPA2/WPA3 encryption. |
| Residual Risk | Medium — deauth attacks are trivial to execute and difficult to prevent without 802.11w (Protected Management Frames). |
| Recommended Action | 1. Enable 802.11w (PMF) in hostapd configuration (ieee80211w=2 for required). 2. Use WPA3-SAE where client support allows. 3. Document the limitation for customers using WPA2-only devices. |
| Owner | Firmware Lead |
| Status | Open |

---

## Category 9: Compliance and Legal

### RISK-025 — GDPR Non-Compliance (EU Customers)

| Field | Content |
|---|---|
| ID | RISK-025 |
| Asset | Customer personal data — email addresses (Stripe), device serials, IP addresses (logs), DNS query patterns |
| Threat | If GhostPort sells to EU customers, GDPR applies. Current system may lack: lawful basis documentation, data processing agreements, right to erasure mechanism, data portability, breach notification procedure, and DPO designation. |
| Likelihood | Medium (if selling to EU) |
| Impact | High — GDPR fines up to 4% of annual global turnover or 20M EUR, whichever is higher. Reputational damage. Enforcement actions. |
| Risk Level | High (if EU sales active) |
| Current Controls | Privacy-by-design principles: Pi-hole privacy level set to 3 (anonymous everything). No PII stored on Pi. Stripe handles payment PII. Minimal data collection. |
| Residual Risk | Medium — no formal privacy policy, no data processing agreement with Stripe, no documented retention policy, no right-to-erasure workflow, no breach notification procedure. Fleet.db stores device serials and checkin history (could be considered personal data under GDPR). |
| Recommended Action | 1. Publish a privacy policy on the website. 2. Document lawful basis for each data processing activity. 3. Implement data retention policy and automated purging. 4. Create right-to-erasure workflow (delete customer data from fleet.db and Stripe). 5. Establish breach notification procedure (72-hour window). 6. Execute Data Processing Agreement with Stripe. 7. Determine if a DPO is required based on processing volume. |
| Owner | Legal / Business |
| Status | Open |

### RISK-026 — Encryption Export Compliance

| Field | Content |
|---|---|
| ID | RISK-026 |
| Asset | GhostPort OS product — ships with WireGuard, WPA3, scrypt, TLS, cloudflared DoH |
| Threat | Shipping a product with strong encryption may trigger export control requirements (EAR/BIS in the US, Wassenaar Arrangement internationally). WireGuard and TLS implementations are mass-market exemptions but may require filing. |
| Likelihood | Low |
| Impact | Medium — potential regulatory action, inability to sell in certain markets, fines. |
| Risk Level | Low |
| Current Controls | All encryption components are open-source and widely available (WireGuard, OpenSSL, cloudflared). Mass-market encryption exemption likely applies. |
| Residual Risk | Low — mass-market exemption covers most cases, but BIS self-classification and annual filing may still be required for US companies shipping encryption products. |
| Recommended Action | 1. Confirm mass-market encryption exemption applies (EAR 740.17). 2. File BIS annual self-classification report if required. 3. Maintain list of countries where sale is prohibited (sanctioned nations). 4. Consult export compliance counsel. |
| Owner | Legal / Business |
| Status | Open |

### RISK-027 — Liability for Customer Traffic

| Field | Content |
|---|---|
| ID | RISK-027 |
| Asset | GhostPort as a network appliance routing customer traffic through EC2 |
| Threat | Customer uses GhostPort to route illegal traffic (child exploitation material, copyright infringement, hacking) through the EC2 WireGuard server. GhostPort Technologies could face legal liability as a conduit or be compelled to provide logs. |
| Likelihood | Low |
| Impact | High — legal liability, law enforcement requests, potential seizure of EC2 infrastructure, reputational damage. |
| Risk Level | Medium |
| Current Controls | Pi-hole privacy level 3 (no query logging). No traffic logging on EC2 WireGuard. Terms of service (assumed). |
| Residual Risk | Medium — no formal acceptable use policy, no abuse response procedure, no legal counsel on file for law enforcement requests. EC2 IP could be flagged on abuse lists. |
| Recommended Action | 1. Draft and publish acceptable use policy. 2. Implement abuse@ email and response procedure. 3. Consult legal counsel on liability shield (common carrier vs. service provider). 4. Ensure Terms of Service disclaim liability for customer traffic. 5. Maintain no-logging policy documentation. |
| Owner | Legal / Business |
| Status | Open |

---

## Category 10: Operational

### RISK-028 — SSL/TLS Certificate Expiry

| Field | Content |
|---|---|
| ID | RISK-028 |
| Asset | SSL certificates — Pi self-signed certs (/opt/ghostport/ssl/), EC2 Let's Encrypt cert (api.ghostporttechnologies.com, expires 2026-06-20) |
| Threat | Let's Encrypt certificate expires without renewal, breaking HTTPS for activation pages and Stripe webhooks. Pi self-signed cert expiry breaks HTTPS dashboard access. Certbot renewal failure due to DNS issues, port 80 blocked, or certbot misconfiguration. |
| Likelihood | Medium |
| Impact | High — EC2 cert expiry breaks customer activation flow (Stripe redirects fail on HTTPS errors), webhook delivery fails. Pi cert expiry causes browser warnings that confuse users. |
| Risk Level | High |
| Current Controls | Let's Encrypt auto-renewal via certbot (assumed configured). EC2 cert expires 2026-06-20 (88 days from now). |
| Residual Risk | Medium — certbot renewal can fail silently. No monitoring or alerting for cert expiry. Pi self-signed certs have no automated renewal. |
| Recommended Action | 1. Verify certbot auto-renewal is configured and test with `certbot renew --dry-run`. 2. Set up certificate expiry monitoring (cron job that alerts at 30/14/7 days). 3. Implement Pi self-signed cert auto-renewal script. 4. Add cert expiry check to fleet API health endpoint. 5. Consider adding cert expiry to the dashboard diagnostics. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-029 — Disk Full (EC2)

| Field | Content |
|---|---|
| ID | RISK-029 |
| Asset | EC2 EBS volume — fleet.db, application logs, system logs, nginx logs |
| Threat | Disk fills up from unrotated logs, growing SQLite database, or accumulated temporary files. SQLite transactions fail, fleet API crashes, nginx stops serving requests, system becomes unresponsive. |
| Likelihood | Medium |
| Impact | High — fleet API becomes unavailable, Stripe webhooks fail, new activations blocked, WireGuard may be affected. |
| Risk Level | High |
| Current Controls | Unknown — no documented log rotation or disk monitoring. |
| Residual Risk | High — no disk space alerting, no automated log rotation verification, no database size monitoring. Fleet.db grows with every device checkin. |
| Recommended Action | 1. Configure logrotate for all application logs (fleet API, nginx, unbound). 2. Set up CloudWatch disk space alarm (alert at 80% usage). 3. Implement fleet.db size monitoring and old record purging. 4. Add disk space check to fleet API health endpoint. 5. Set EBS volume auto-expansion or alert threshold. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-030 — Disk Full (Pi / SD Card)

| Field | Content |
|---|---|
| ID | RISK-030 |
| Asset | Pi microSD card — OS, Pi-hole gravity database, FTL database, application logs, systemd journal |
| Threat | SD card fills from Pi-hole FTL database growth, systemd journal accumulation, gravity database updates, or Node.js crash logs. Device becomes unstable or unbootable. |
| Likelihood | High |
| Impact | High — Pi-hole stops functioning, Express server cannot write session/config data, device may become unresponsive. |
| Risk Level | Critical |
| Current Controls | dnsmasq cache in memory (10,000 entries). Pi-hole privacy level 3 (reduces FTL database writes). |
| Residual Risk | High — no journal size limit configured (assumed), no FTL database rotation, no disk space monitoring, no alerting. SD cards are typically 16-32GB with limited headroom. |
| Recommended Action | 1. Configure systemd journal size limit (`SystemMaxUse=100M` in journald.conf). 2. Move Pi-hole FTL database to tmpfs. 3. Add disk space check to /api/diagnostics endpoint. 4. Implement proactive alerting at 85% disk usage. 5. Set up log rotation for all ghostport logs. 6. Schedule periodic `apt clean` and temp file purging. |
| Owner | Firmware Lead |
| Status | Open |

### RISK-031 — Backup Failure / No Recovery Path

| Field | Content |
|---|---|
| ID | RISK-031 |
| Asset | All GhostPort configuration and customer data across Pi and EC2 |
| Threat | No documented or automated backup strategy for either Pi configuration or EC2 fleet data. A catastrophic failure (SD card death, EC2 termination, accidental deletion) results in permanent data loss. |
| Likelihood | Medium |
| Impact | Critical — loss of fleet database means loss of all customer records, subscription links, and device associations. Loss of Pi config means manual reconfiguration. No disaster recovery runbook. |
| Risk Level | Critical |
| Current Controls | .bak files alongside Pi configs (same disk — not a real backup). EBS snapshots exist but frequency/retention unknown. Git repo has copies of scripts and configs (not secrets). |
| Residual Risk | Critical — no offsite backup for fleet.db. No automated Pi config backup. No tested restore procedure. Recovery time objective (RTO) and recovery point objective (RPO) undefined. |
| Recommended Action | 1. Implement automated daily fleet.db backup to S3 (encrypted, versioned, cross-region). 2. Add Pi config backup to EC2 via fleet API heartbeat. 3. Document and test full disaster recovery procedure. 4. Define RTO and RPO targets. 5. Implement automated EBS snapshots with retention policy. 6. Create recovery runbook for both Pi and EC2 scenarios. 7. Test restore procedure quarterly. |
| Owner | Infrastructure Lead |
| Status | Open |

### RISK-032 — Service Configuration Drift

| Field | Content |
|---|---|
| ID | RISK-032 |
| Asset | Live system files across Pi and EC2 — server code, scripts, nftables profiles, systemd units |
| Threat | Live files are edited directly on the system, then manually copied to the git repo. Files drift out of sync — the repo may not reflect reality. Emergency hotfixes applied live but never committed. Multiple admins (human + AI) editing concurrently without coordination. |
| Likelihood | High |
| Impact | Medium — recovery from failure uses outdated repo files. Debugging uses wrong code version. Rollback restores a stale state. Git history becomes unreliable. |
| Risk Level | High |
| Current Controls | Manual copy-before-commit workflow documented. Git repo at /opt/ghostport/. CLAUDE.md documents the sync requirement. |
| Residual Risk | High — no automated sync verification, no drift detection, no pre-commit hook that validates live-vs-repo match. Workflow relies entirely on human/AI discipline. |
| Recommended Action | 1. Implement a `gp-sync-check` script that diffs live files against repo copies. 2. Add a pre-commit hook that warns about unsynced files. 3. Consider making the repo the source of truth with a deploy script (repo -> live, not live -> repo). 4. Run sync-check as a cron job with alerting. |
| Owner | Firmware Lead |
| Status | Open |

---

## Risk Summary Dashboard

| Risk Level | Count | IDs |
|---|---|---|
| **Critical** | 5 | RISK-001, RISK-014, RISK-018, RISK-030, RISK-031 |
| **High** | 14 | RISK-002, RISK-003, RISK-004, RISK-005, RISK-006, RISK-008, RISK-009, RISK-011, RISK-012, RISK-016, RISK-023, RISK-025, RISK-028, RISK-029, RISK-032 |
| **Medium** | 10 | RISK-007, RISK-010, RISK-017, RISK-019, RISK-020, RISK-021, RISK-022, RISK-024, RISK-026, RISK-027 |
| **Low** | 3 | RISK-013, RISK-015 (scored High due to Critical impact), RISK-026 |

### Top 5 Priority Actions

1. **Implement automated backups** (RISK-031) — fleet.db to S3, Pi configs to EC2, tested restore procedure
2. **Audit child_process.exec calls** (RISK-002) — migrate to execFile, prevent command injection
3. **Secure EC2 public surface** (RISK-001) — WAF/rate limiting on nginx, input validation audit on fleet API
4. **SD card resilience** (RISK-018, RISK-030) — tmpfs for high-write files, journal limits, disk monitoring
5. **Dependency scanning** (RISK-014) — npm audit, pin versions, automated vulnerability checks

---

*This risk register should be reviewed and updated quarterly, or immediately following any security incident, architecture change, or new feature deployment.*
