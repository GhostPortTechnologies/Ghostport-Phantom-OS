# Quartermaster — Security Audit Scorecard

GTK desktop app that runs a 10-point security audit against the router and produces a scorecard with pass/fail per check plus historical trending.

## Purpose
A quick self-assessment of security posture. Verifies that the defensive basics are in place: encrypted DNS is on, firewall rules loaded, auth enabled, session cookies flagged, TOTP configured (if enrolled), HMAC verified on the fleet bridge, nftables syntax valid, no rogue listeners on privileged ports, SSH hardened, and kernel up-to-date relative to patch stream.

## When to use
- Before shipping a new config — sanity check nothing regressed
- Monthly routine check — catches drift from user tinkering
- Post-update verification — did the OTA break any invariant?
- Customer escalation — capture the current score to share with support

## Screenshot
`/opt/phantom/docs/screenshots/gp-quartermaster.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/etc/phantom/audit-history.json` | Score history (timestamped, last 30 audits) |
| `/etc/phantom/auth.json` | Read to verify auth + TOTP state (passphrase hash not decrypted, only presence checked) |

Each check is a small function with a pass/fail verdict and a one-line explanation. Total score is out of 100, grade mapped A–F (NIST-compliance alignment; see security-audit-2026-04-08.md).

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-quartermaster.py`

## Troubleshooting
- **Score dropped unexpectedly** → click the failing check row for the specific reason. Common: encrypted DNS turned off (`sudo gp-dns-switch on`), SSH allowed password auth (`/etc/ssh/sshd_config.d/` override), or TOTP enrolled but session cookie weak.
- **Audit won't run** → script expects passwordless sudo on `gp-*`. If installed fresh, re-run `sudo gp-provision`.
- **"HMAC check: skipped"** → fleet bridge not configured (normal on a standalone unit). Not a blocker — just means no fleet pairing.
- **Score flatlines at 85** → the final 15 points are certification-class items (CMMC roadmap, pen-test receipt). Not reachable without the external audit pipeline.
