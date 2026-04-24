# GhostPort EC2 Failover & Resilience Plan

**Version**: 1.0
**Date**: April 3, 2026
**Classification**: Internal — Investor Ready

## Architecture Summary

GhostPort uses a **phone-home** (pull-based) architecture. Customer Pi devices initiate all outbound connections to the EC2 fleet server. The server never initiates contact with any Pi device. This means:

- Pi devices sit behind NAT with zero inbound ports exposed
- A compromised EC2 yields telemetry data, not remote code execution
- All queued commands are HMAC-signed and restricted to a 5-command whitelist
- Pi devices operate fully independently if the server is unreachable

## What depends on EC2

| Function | Dependency | Impact if EC2 is down |
|----------|-----------|----------------------|
| **Day-to-day operation** | None | All privacy modes, ad blocking, DNS encryption, VPN tunneling, and parental controls work without any server contact |
| **Device activation** | Required (one-time) | New devices cannot activate until EC2 returns. Already-activated devices are unaffected |
| **OTA updates** | Required | Devices continue on current firmware. Updates resume when EC2 returns |
| **TOTP recovery** | Required | Users locked out of TOTP cannot reset remotely. Local passcode access still works |
| **Subscription validation** | Periodic check | Cached locally. Devices continue with last-known subscription state |
| **Heartbeat/telemetry** | Periodic (5 min) | Silent failure. Pi logs the miss and retries next cycle |

## Failure Modes & Mitigation

### EC2 instance crash or reboot
- **Detection**: CloudWatch alarm on StatusCheckFailed (configured)
- **Recovery**: Auto-restart via EC2 auto-recovery action
- **RTO**: ~3 minutes
- **Customer impact**: None. Heartbeats silently fail and resume on next cycle

### EC2 instance termination or data loss
- **Detection**: CloudWatch alarm + heartbeat failures from all devices
- **Recovery**: Restore from latest EBS snapshot (automated daily via AWS Data Lifecycle Manager)
- **Data**: Fleet database (SQLite), nginx configs, SSL certs, fleet API code
- **RTO**: ~15 minutes (launch new instance from snapshot + reassociate EIP)
- **Customer impact**: Activation and updates unavailable during recovery. Active devices unaffected

### AWS region outage (us-east-1)
- **Detection**: Global monitoring / inability to reach EIP
- **Recovery**: Launch replacement instance in us-west-2 from cross-region snapshot copy
- **Prerequisites**:
  - [ ] Enable cross-region EBS snapshot copy (weekly)
  - [ ] Document EIP reassociation or DNS failover procedure
  - [ ] Pre-stage AMI in us-west-2
- **RTO**: ~30 minutes
- **Customer impact**: Same as instance termination

### DNS failure (api.ghostporttechnologies.com)
- **Detection**: External uptime monitor (e.g., UptimeRobot)
- **Recovery**: DNS is on Cloudflare — failover to backup IP or restore record
- **Customer impact**: Activation and bridge messaging unavailable. Pi heartbeat uses IP fallback if configured

### EC2 compromise
- **Blast radius**: Attacker can queue HMAC-signed commands (mode switch, reboot, update trigger). Cannot execute arbitrary code. Cannot access Pi local networks. Cannot read Pi traffic.
- **Mitigation already in place**:
  - HMAC command signing (shared secret per device)
  - 5-command whitelist (no shell execution)
  - UFW restricts inbound to ports 22/80/443/51820
  - fail2ban on SSH
  - auditd logging
  - AIDE file integrity monitoring
- **Response**: Rotate HMAC secrets, revoke fleet tokens, push emergency update

## Current State

| Item | Status |
|------|--------|
| EBS snapshots (daily) | Active |
| CloudWatch auto-recovery | Active |
| Cross-region snapshot copy | **NOT YET CONFIGURED** |
| External uptime monitoring | **NOT YET CONFIGURED** |
| Documented runbook for full rebuild | **THIS DOCUMENT** |
| EIP reassociation procedure | Documented below |

## Recovery Runbook: Full EC2 Rebuild

```bash
# 1. Launch new instance from latest snapshot
aws ec2 run-instances \
  --image-id <snapshot-ami> \
  --instance-type t3.micro \
  --key-name Ghostport-vpn \
  --security-group-ids <sg-id> \
  --subnet-id <subnet-id>

# 2. Reassociate Elastic IP
aws ec2 associate-address \
  --instance-id <new-instance-id> \
  --allocation-id <eip-allocation-id>  # EIP: 44.214.101.82

# 3. Verify services
ssh -i Ghostport-vpn.pem ubuntu@44.214.101.82
sudo systemctl status ghostport-health nginx unbound

# 4. Verify WireGuard peers reconnect
sudo wg show

# 5. Test fleet API
curl -s http://localhost:8080/health
```

## Investor Answer

> **"What happens if your cloud goes down?"**
>
> Every GhostPort device operates independently. Ad blocking, DNS encryption, VPN tunneling, and parental controls are 100% local — zero cloud dependency for daily operation. The cloud server handles three things: initial one-time device activation, automatic firmware updates, and remote account recovery. If our server goes down, every active device continues working normally. New activations and updates pause until the server returns. We maintain daily snapshots with a 15-minute recovery time, and all server-to-device commands are cryptographically signed with per-device HMAC keys — so even a compromised server cannot execute arbitrary code on customer networks.
