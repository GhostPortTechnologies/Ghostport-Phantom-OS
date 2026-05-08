# Quartermaster — Cross-Mode Findings Matrix

**Source ticket:** T-0144. Filed during 17-app review when operator asked whether the AP-hardening findings (WPA3/MFP) were mode-specific or universal. Answer: AP findings are mode-independent, but several others (VPN Tunnel UP, Encrypted DNS, Kill Switch) score differently per mode.

This matrix documents which findings change with which mode so future scoring tweaks have ground truth.

## How to fill rows (operator workflow)

For each of the 4 modes:
1. `sudo gp-mode <mode>` — wait for the 60s rollback timer; `sudo gp-mode confirm` if you want it sticky for the scan.
2. Open Quartermaster from the desktop. Click **Scan**.
3. Note the score, write each check's PASS/FAIL into the matrix below.
4. Switch to the next mode.

ISP mode is the safe-fallback (no rollback timer) and is the right last stop.

## Matrix template

Fill in PASS/FAIL per cell. Score row at the bottom. As of 2026-05-07 only the ZeroTrust column is captured (live during ticket close).

| Check | Weight | ISP | ZeroTrust ✓ | DoubleHop | ZHop |
|---|---|---|---|---|---|
| Firewall Active | 15 | ___ | PASS | ___ | ___ |
| SSH Key-Only Auth | 10 | ___ | PASS | ___ | ___ |
| Encrypted DNS | 10 | ___ | PASS | ___ | ___ |
| VPN Tunnel UP | 10 | N/A | **FAIL** (wg1 down by design) | PASS | PASS |
| Pi-hole Running | 10 | ___ | PASS | ___ | ___ |
| Auto-Updates Enabled | 5 | ___ | PASS | ___ | ___ |
| Custom Passcode Set | 5 | ___ | PASS | ___ | ___ |
| Disk Encryption | 5 | ___ | FAIL (SD root, no LUKS) | ___ | ___ |
| DNS Resolver Health | 5 | ___ | PASS | ___ | ___ |
| Interface Error Rate | 5 | ___ | PASS | ___ | ___ |
| Intrusion Detection | 5 | ___ | PASS | ___ | ___ |
| Kill Switch Armed | 5 | ___ | FAIL (Anchor disarmed) | ___ | ___ |
| AP Encryption: WPA3 | 5 | PASS | **PASS (T-0143)** | PASS | PASS |
| AP MFP (802.11w) | 3 | PASS | **PASS (T-0143)** | PASS | PASS |
| AP WPS Disabled | 2 | ___ | PASS | ___ | ___ |
| **Total Score** | **100** | ___ | **86 (estimated)** | ___ | ___ |

## Mode-dependent findings (expected behavior)

These checks SHOULD score differently per mode:

- **VPN Tunnel UP** — FAIL in ISP/ZeroTrust by design (wg1 is down in those modes); PASS in DoubleHop/ZHop. The 10-point penalty in ZeroTrust is a known scoring quirk — ZeroTrust is a non-tunneled mode, the check probably shouldn't apply. Worth a follow-up scoring tweak.
- **Encrypted DNS** — different upstream per mode: ISP/ZeroTrust use cloudflared at 127.0.0.1:5053; DoubleHop/ZHop use the data-plane Unbound at 10.66.67.1:53. Both should pass when their respective resolver is healthy.
- **Kill Switch Armed** — operator-controlled; orthogonal to mode. Can fire in any mode.

## Mode-independent findings (should always score the same)

These should score IDENTICALLY across all 4 modes — if a row diverges, it's a bug:

- Firewall Active (nft running)
- SSH Key-Only Auth
- Pi-hole Running
- Auto-Updates Enabled
- Custom Passcode Set
- Disk Encryption (hardware fact, not mode)
- Interface Error Rate
- Intrusion Detection
- AP Encryption / MFP / WPS (hostapd-side, mode-independent)

## Action items uncovered while filling this matrix

(Operator: log new tickets here as each mode reveals issues.)

- [ ] If VPN Tunnel UP scoring penalizes ZeroTrust unfairly → file ticket to make it conditional on mode (skip in ISP/ZeroTrust)
- [ ] If Encrypted DNS check fails in DoubleHop because Unbound has a hiccup → that's not a Quartermaster bug, that's a real outage worth investigating

## Closing this ticket

Mark T-0144 done once all 4 columns are filled. Until then, keep the file in `/opt/ghostport/docs/` as the working canvas.
