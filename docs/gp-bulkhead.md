# Bulkhead — Visual Firewall Builder

Point-and-click editor for the nftables firewall that drives every GhostPort mode (ISP / ZeroTrust / DoubleHop / ZHop). Read current rules, see their hit counts, flag dead rules, export a snapshot. For the engine itself see `gp-firewall`.

## When To Use It
- You want to SEE what the firewall is actually doing — chains, rules, hits
- A rule isn't firing and you want live counter proof
- Snapshot / export the current ruleset before a mode switch

## Features

- Live view of all nftables chains and rules — grouped by mode profile
- Per-rule **hit counters** (drawn from `nft list ruleset` counters)
- **Dead-rule flagging** — rules with 0 hits since boot are highlighted for review
- Safe-delete guard — core GhostPort rules (port 4200, tailscale0, UDP 41641) are protected from deletion
- **Export** ruleset to a timestamped file for audit / backup
- Rule-add dialog with template shortcuts (block IP, allow port, rate-limit)

## Safety Rules (enforced by the app)

- You cannot delete rules that break remote management (Tailscale, SSH, dashboard)
- Changes go to the LIVE ruleset immediately — no staging area
- Before apply, the app dry-runs via `nft -c -f` and aborts on syntax error
- Mode switches (`gp-mode ...`) will overwrite any hand-edits — for persistent changes, edit the `.nft` profile in `/etc/gpmodes/`

## Data Sources

- `nft list ruleset` — live kernel state
- `/etc/gpmodes/{common,isp,zerotrust,doublehop,zhop}.nft` — mode profiles on disk
- `/etc/phantom/current-mode` — which profile is active

## Troubleshooting

| Symptom | Check |
|---------|-------|
| "Permission denied" reading rules | App uses passwordless sudo for `nft`; verify `sudoers.d/ghostport-admin` is intact |
| Hit counts stuck at 0 | Counters reset on every `gp-mode` switch or `nft flush`. That's expected — not a bug |
| Rule appears in `/etc/gpmodes/` but not in app | You edited the file but didn't switch mode. Run `sudo gp-mode <current>` to reapply |
| Can't delete a rule | It's protected (see Safety Rules). Edit the profile `.nft` file directly and re-apply the mode |

## Files

- App: `/opt/phantom/desktop/gp-bulkhead.py`
- Icon: `/opt/phantom/desktop/icons/gp-bulkhead.svg`
- Profiles: `/etc/gpmodes/*.nft`
- Mode switch CLI: `sudo gp-mode isp|zerotrust|doublehop|zhop`
