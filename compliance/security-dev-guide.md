# GhostPort — Security Development Guide
**Version:** 1.0 | **Date:** 2026-03-24

---

## Code Security Rules

### Shell Command Execution
- **NEVER** use `exec()` with string interpolation of user input
- **ALWAYS** validate inputs against a whitelist before interpolating into `run()` calls
- **PREFER** `execFile()` with argument arrays over `exec()` with string concatenation
- **ALWAYS** quote file paths in shell commands
- Mode values MUST be validated: `["isp", "zerotrust", "doublehop", "zhop"]`

### Authentication
- Passcode comparison: use `crypto.timingSafeEqual()`, never `===` or `!==`
- Session tokens: `crypto.randomBytes(32)` minimum
- CSRF tokens: required on all POST/PUT/DELETE/PATCH endpoints
- Session absolute expiry: 7 days max, sliding window 24h
- Rate limiting: 5 attempts then lockout (consider exponential backoff)

### Input Validation
- Sanitize all user input before use in commands, configs, or database queries
- WireGuard configs: strip PostUp/PostDown/PreUp/PreDown/SaveConfig
- SSIDs: 1-32 chars, no control characters or newlines
- WiFi passwords: 8-63 printable ASCII
- File paths: reject `..`, null bytes, and symlinks when serving files

### Data Classification
- **Restricted** (Stripe keys, WG private keys, passcode hashes): never log, never expose in API responses, encrypt at rest
- **Internal** (fleet.db, configs, IPs): access-controlled, logged access via auditd
- **Public** (blog, health endpoints): no restrictions

### CSRF Protection
- All state-changing API endpoints require `X-CSRF-Token` header
- Token generated at login, stored in session, returned to client
- Frontend auto-injects via fetch wrapper

### Content Security Policy
- No `unsafe-inline` for scripts (use nonces or external files)
- `unsafe-inline` for styles is acceptable (no XSS vector for CSS)
- No external font/script loads (self-hosted only)
- `frame-ancestors 'none'` (prevent clickjacking)

### Secrets Management
- No hardcoded tokens, keys, or passwords in source code
- Secrets live in `/etc/phantom/` (Pi) or `/opt/phantom-fleet/` (EC2)
- File permissions: 600, owned by service user
- Secrets excluded from git via `.gitignore`
- Rotate after any suspected exposure

### Sudo Rules
- Minimum necessary privilege — no wildcards
- Each command restricted to specific file paths
- New sudo requirements must be added to `010_ghostport-hardened` with exact paths

### Firewall (nftables)
- Always validate with `nft -c -f` before applying
- Never open ports to all interfaces — restrict to specific iifname
- Log dropped packets for anomaly detection
- Mode-specific profiles must maintain: port 4200, UDP 41641, tailscale0, SSH

### Dependency Management
- Run `npm audit` (Pi) and `pip-audit` (EC2) monthly
- No unvetted third-party packages
- Pin dependency versions in package.json

### Logging & Monitoring
- Log all authentication events (success + failure)
- Log all mode switches
- Log all admin actions
- Never log secrets (Stripe keys, passcodes, WG keys)
- Audit logs retained 90 days minimum
