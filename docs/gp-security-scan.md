# gp-security-scan — Security Threat Scan

## Summary
Comprehensive 20-point security posture check with scoring. Tests network security, DNS, authentication, and system hardening. Grades your setup from STRONG to WEAK.

## Quick Start
1. Open **Start Menu > MONITOR > Security Scan**
2. Scan runs automatically (~15 seconds)
3. Review score and address any FAIL items

## Categories & Checks

**Network Security (7 checks):** Firewall active, default-deny forward policy, Tailscale up, SSH restricted, IPv6 disabled, QUIC blocked, WebRTC STUN blocked.

**DNS Security (3 checks):** Pi-hole running, DNS resolving, encrypted DNS (DoH) active.

**Authentication (3 checks):** Auth file permissions (600), passcode hashed (scrypt), CSRF protection active.

**System Hardening (7 checks):** Disk <80%, CPU <70C, no world-readable secrets, backup files exist, boot mode persistence, health guard timer, DNS guard timer, auto-update timer.

## Grading
| Score | Grade |
|-------|-------|
| >= 80% | STRONG |
| >= 60% | MODERATE |
| < 60% | WEAK |

## How It Works
Runs each check as a shell command and awards points. Results are saved to `/etc/phantom/security-scan.json` for historical tracking. Issues are listed at the end with actionable labels.

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-security-scan` | Main script |
| `/etc/phantom/security-scan.json` | Scan results |
