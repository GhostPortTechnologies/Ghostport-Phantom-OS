# GhostPort OS — Incident Response Playbook
**Version:** 1.0
**Last Updated:** 2026-03-24
**Owner:** GhostPort Technologies

---

## 1. Roles & Contacts

| Role | Who | Contact | Responsibility |
|---|---|---|---|
| Incident Commander | Thomas Estrada | support@ghostporttechnologies.com / @ghostporttech | Final authority on response decisions |
| Pi Claude | AI Agent (Pi 5) | Claude bridge | Pi-side diagnostics, code fixes, firewall changes |
| EC2 Claude | AI Agent (EC2) | Claude bridge | Fleet API fixes, log analysis, EC2 remediation |
| UptimeRobot | Automated | Alerts to email | Uptime monitoring for api.ghostporttechnologies.com |

## 2. Severity Levels

| Level | Definition | Response Time | Examples |
|---|---|---|---|
| **SEV-1 Critical** | Service down, data breach, active exploitation | Immediate (< 15 min) | EC2 compromised, fleet.db exfiltrated, Stripe keys leaked |
| **SEV-2 High** | Partial outage, security control bypassed | < 1 hour | WireGuard tunnel down, auth bypass, DNS leak |
| **SEV-3 Medium** | Degraded service, non-critical vulnerability found | < 4 hours | Disk at 85%, cert expiring in 14 days, fail2ban trigger |
| **SEV-4 Low** | Minor issue, informational | < 24 hours | Log anomaly, failed login attempt, config drift |

## 3. Detection Sources

| Source | What It Detects | Alert Method |
|---|---|---|
| UptimeRobot | api.ghostporttechnologies.com down | Email |
| check-cert.sh (daily cron) | SSL cert expiry < 14 days, disk > 85% | Bridge message |
| fail2ban | SSH brute force, repeated auth failures | journald log |
| AIDE (daily cron) | File integrity changes on critical paths | /var/log/aide/aide-check.log |
| auditd | Privilege escalation, config file access, sudo changes | /var/log/audit/audit.log |
| nftables logging | Dropped packets, firewall violations | journald |
| Bridge alerts | Cross-system alerts from either Claude | Bridge messages |

## 4. Incident Scenarios & Runbooks

---

### SCENARIO A: EC2 Server Compromised

**Indicators:** Unauthorized SSH sessions, modified fleet-api.py, unexpected processes, auditd alerts on privilege escalation.

**Containment (< 15 min):**
1. SSH to EC2: `ssh -i ~/Downloads/Ghostport-vpn.pem ubuntu@10.66.66.1`
2. Check active sessions: `who` and `last -20`
3. Check running processes: `ps aux | grep -v "^\(root\|ubuntu\|ghostport\|www-data\|systemd\)"`
4. If confirmed: kill unauthorized sessions: `sudo pkill -u <user>`
5. Block all inbound except your WG IP: `sudo ufw default deny incoming`
6. Stop fleet API: `sudo systemctl stop ghostport-health`

**Eradication:**
1. Check file modifications: `sudo aide --check`
2. Review audit log: `sudo ausearch -k ghostport-secrets -ts recent`
3. Check crontabs: `sudo crontab -l; ls /etc/cron.d/`
4. Check for backdoors: `sudo find / -name "*.sh" -newer /opt/ghostport-fleet/fleet-api.py -ls 2>/dev/null`
5. If rootkit suspected: boot from AMI snapshot `ghostport-fleet-2026-03-23-stable`

**Recovery:**
1. Spin new EC2 from clean AMI snapshot
2. Restore fleet.db from S3 backup (when available) or from the AMI
3. Rotate ALL credentials: Stripe keys, fleet auth token, bridge token
4. Update WireGuard keys (new keypair for server)
5. Update DNS if EIP changes
6. Verify: `curl https://api.ghostporttechnologies.com/webhooks/health`

**Post-Incident:**
1. Determine entry point (SSH? application exploit? AWS credential?)
2. Update firewall rules if needed
3. Take new AMI snapshot
4. Document in incident log

---

### SCENARIO B: Pi Device Compromised

**Indicators:** Unauthorized process, modified server.js, unexpected outbound connections, AIDE alert.

**Containment:**
1. Via Tailscale SSH: `ssh ghostport-admin@<tailscale-ip>`
2. Switch to ISP mode (safe fallback): `sudo gp-mode isp`
3. Check processes: `ps aux | grep -v "^\(root\|ghostport\|pihole\|www-data\|systemd\|dnsmasq\)"`
4. Check recent file changes: `sudo aide --check`
5. If confirmed: disconnect WAN cable (physical) or block outbound: `sudo nft add rule inet filter output drop`

**Eradication:**
1. Review audit log: `sudo ausearch -ts recent`
2. Check for modified binaries: `debsums -c 2>/dev/null | head -20`
3. Verify gp-* scripts: `sha256sum /usr/local/bin/gp-*`
4. Check sudoers: `sudo cat /etc/sudoers.d/010_ghostport-hardened`

**Recovery:**
1. Flash fresh SD card from golden image (when available)
2. Restore configs from git repo: `cd /opt/ghostport && git checkout .`
3. Re-register with fleet: device will auto-register on next checkin
4. Rotate passcode: `sudo gp-passcode reset`
5. Rotate WiFi password via dashboard

---

### SCENARIO C: Stripe Keys Leaked

**Indicators:** Unexpected charges, Stripe dashboard alerts, keys found in logs/code.

**Response (Immediate):**
1. Log into Stripe Dashboard → Developers → API Keys
2. Roll the API key (creates new, old continues working for 24h)
3. Roll the webhook signing secret
4. Update EC2: edit `/opt/ghostport-fleet/stripe.json` with new values
5. Restart fleet API: `sudo systemctl restart ghostport-health`
6. Verify webhook: send test event from Stripe Dashboard
7. Check Stripe event log for unauthorized activity
8. If unauthorized charges found: contact Stripe support immediately

---

### SCENARIO D: WireGuard Tunnel Down

**Indicators:** Bridge messages failing, EC2 unreachable from Pi, UptimeRobot alert (if monitoring internal endpoint).

**Diagnosis:**
1. Check mode: `sudo gp-mode status` (must be doublehop or zhop)
2. Check WG interface: `sudo wg show wg0`
3. Ping EC2: `ping -c3 10.66.66.1`
4. Check endpoint reachability: `ping -c3 44.214.101.82`
5. Check EC2 side (via Tailscale if available, or AWS console)

**Resolution:**
1. If Pi-side: `sudo systemctl restart wg-quick@wg0`
2. If EC2-side: SSH via public IP (if UFW allows) or AWS Session Manager
3. If config drift: restore from backup WG config
4. Verify: `ping -c3 10.66.66.1 && curl -s http://10.66.66.1:8080/health`

---

### SCENARIO E: DDoS / Resource Exhaustion

**Indicators:** High CPU/memory, slow responses, UptimeRobot down alert, rate limiter triggering.

**Response:**
1. Check load: `top -bn1 | head -20`
2. Check connections: `ss -s` and `ss -tn state established | wc -l`
3. If EC2: check nginx access log: `sudo tail -100 /var/log/nginx/access.log | awk '{print $1}' | sort | uniq -c | sort -rn | head`
4. Block offending IPs: `sudo ufw deny from <IP>`
5. If sustained: enable AWS WAF or CloudFront (requires setup)
6. If Pi LAN-side: identify rogue client via `sudo conntrack -L | awk '{print $4}' | sort | uniq -c | sort -rn`

---

### SCENARIO F: SSL Certificate Expiry

**Indicators:** check-cert.sh bridge alert (14 days), browser warnings.

**Response:**
1. SSH to EC2
2. Renew: `sudo certbot renew`
3. Verify: `sudo openssl x509 -in /etc/letsencrypt/live/api.ghostporttechnologies.com/fullchain.pem -noout -dates`
4. Reload nginx: `sudo systemctl reload nginx`
5. If certbot fails: check DNS resolution, check port 80 is open, check rate limits

---

## 5. Communication Protocol

| Severity | Notification | Method |
|---|---|---|
| SEV-1 | Immediate | Email + bridge alert to both Claudes |
| SEV-2 | Within 1 hour | Bridge alert + email if human action needed |
| SEV-3 | Within 4 hours | Bridge message, fix autonomously if possible |
| SEV-4 | Daily digest | Log only, review in next session |

## 6. Post-Incident Checklist

- [ ] Root cause identified and documented
- [ ] Affected credentials rotated
- [ ] Patches applied to prevent recurrence
- [ ] AIDE database re-initialized (after authorized changes)
- [ ] AMI/backup snapshot taken
- [ ] Risk register updated with new entry
- [ ] Blog updated if publicly relevant
- [ ] Lessons learned documented

## 7. Evidence Preservation

When investigating an incident:
1. **Do NOT reboot** until logs are captured
2. Capture audit log: `sudo ausearch -ts boot > /tmp/audit-dump-$(date +%s).log`
3. Capture system state: `ps aux > /tmp/ps-$(date +%s).log; ss -tlnp > /tmp/ss-$(date +%s).log`
4. Capture network state: `sudo nft list ruleset > /tmp/nft-$(date +%s).log`
5. Copy logs to a separate location before remediation
6. Preserve original files (copy, don't move) before fixing
