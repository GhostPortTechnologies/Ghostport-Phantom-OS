# gp-integrity — File Integrity Monitor

Monitors critical system files for unauthorized changes using AIDE.

## Usage
```
gp-integrity             Interactive dashboard
gp-integrity check       Run integrity check
gp-integrity init        Initialize/rebuild AIDE database
gp-integrity status      Show last check results
gp-integrity history     Show check history
```

## Monitored Paths
- `/etc/` — System configuration
- `/usr/local/bin/` — GhostPort scripts
- `/etc/gpmodes/` — Firewall profiles
- `/opt/phantom/` — Server and frontend

## How It Works
- AIDE snapshots file checksums, permissions, metadata
- Checks compare current state against baseline
- Changes flagged via desktop notification + log

## NIST Compliance
Implements SI-7 (Software & Information Integrity) from NIST 800-53.
