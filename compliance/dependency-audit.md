# GhostPort Dependency Audit
**Date:** 2026-03-24

## Pi (Node.js)
```
npm audit: 0 vulnerabilities found
```
**Status: CLEAN**

## EC2 (Python)
```
pip-audit findings:
  urllib3 2.0.7 — 5 CVEs (CVE-2024-37891, CVE-2025-50181, CVE-2025-66418, CVE-2025-66471, CVE-2026-21441)
  wheel 0.42.0 — 1 CVE (CVE-2026-24049)
```

**Risk Assessment:** LOW — ACCEPTED
- urllib3 is an OS-managed package (installed by apt, cannot pip upgrade without `--break-system-packages`)
- The fleet API (`fleet-api.py`) uses Python's built-in `http.server`, not `requests` or `urllib3` directly
- urllib3 is an indirect dependency via other system packages
- `unattended-upgrades` is active on EC2 and will patch these when Ubuntu releases an update
- wheel CVE only affects package installation, not runtime

**Action:** Monitor via `pip-audit` monthly. Will be patched by Ubuntu security updates automatically.

## External Services
| Service | Version/Status | Risk |
|---|---|---|
| Stripe Python SDK | 14.4.1 | Current, no known CVEs |
| Express.js | 5.x | Current, no known CVEs |
| Let's Encrypt (certbot) | Auto-renew active | Low |
| Tailscale | Managed by Tailscale | Low |
