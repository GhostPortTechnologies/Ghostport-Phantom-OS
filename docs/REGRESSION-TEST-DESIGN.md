# Regression Test Design

**Ticket:** T-0181 (research) → reference impl shipped at `~/.local/bin/gp-test`
**Last verified:** 2026-05-08

The 2026-05-08 session surfaced six latent bugs in eight hours, every one caught by chasing symptoms rather than by an alert. We need automated tests that exercise load-bearing paths so confidence in correctness comes from a pass/fail signal, not operator memory + customer support tickets.

This doc captures the test substrate, layering, and how `gp-test` complements existing `gp-preflight` and `gp-prepush`.

---

## 1. Test substrate decision

Surveyed:

| Substrate | Verdict |
|---|---|
| pytest harness | overkill for bash-heavy code; we'd port everything |
| Bats (Bash automated testing) | clean assertions, cross-distro, **chosen** for new tests where we want structure |
| Plain bash + exit codes | what we have today — keep for simple smoke checks |
| Containerized client | useful for CI but not for on-device runs |
| Real second Pi as test client | gold-standard but logistically heavy; reserve for fleet-level tests |

**Decision:** plain bash + exit codes for the on-device entry point (`gp-test`); Bats for any new test scenario added later that has multiple assertions per scenario.

---

## 2. Layered test plan

```
┌─ Layer 0: gp-prepush (already shipped, T-0164) ─────────┐
│  Pre-push gauntlet: leak scan, diff direction, manifest │
└─────────────────────────────────────────────────────────┘
┌─ Layer 1: gp-preflight (already shipped) ───────────────┐
│  Health check: services up, mode consistent, temp/mem   │
└─────────────────────────────────────────────────────────┘
┌─ Layer 2: gp-test (NEW, this ticket) ───────────────────┐
│  Regression: change-validating, READ-ONLY by default.   │
│  Verifies invariants across what gp-preflight skips:    │
│  - nft profile validity (-c) for all 4 modes            │
│  - active-region id == wg1 conf endpoint                │
│  - killswitch chain present + has expected counters     │
│  - passthrough.json well-formed; rules generated match  │
│  - DNS path: cloudflared (ISP/ZT) vs wg1 unbound (DH)   │
│  - mode-boot would restore current mode without surprise│
│  Run: gp-test                  (read-only smoke)        │
│       gp-test --disruptive     (mode-cycle, opt-in)     │
└─────────────────────────────────────────────────────────┘
┌─ Layer 3: gp-test --on-fleet (FUTURE) ──────────────────┐
│  Real client browserleaks-style verify. Requires a 2nd  │
│  Pi or a containerized client. Out of scope this round. │
└─────────────────────────────────────────────────────────┘
```

**Layer 2 is the contribution of this ticket.** `gp-test` runs on the live device and is safe to run on production (read-only). `gp-test --disruptive` opt-in cycles modes for full coverage and is for staging / when operator confirms ok.

---

## 3. Trigger model

| When | What runs |
|---|---|
| Before every commit (manual habit) | `gp-prepush` (leaks) + `gp-test` (regressions) |
| Before merge to main (manual) | `gp-test --disruptive` if scope touched mode/firewall/tunnel |
| Nightly (timer, future) | `gp-test` read-only via systemd timer; alert via `gp-bridge` on regression |
| On-demand by AI / operator | `gp-test [check-name]` to focus on one concern |

`gp-test` is intentionally **not** wired into `gp-prepush` automatically — operator decides when to run. Auto-running on every commit is too slow given some checks query the live system.

---

## 4. Reference impl: `gp-test` checks (shipped)

Each check is a function that returns 0 = pass, 1 = fail, 2 = skip-with-reason.

| Check | Layer | What it verifies |
|---|---|---|
| `nft-profiles-valid` | offline | `nft -c -f` for each of 4 mode profiles |
| `active-region-consistent` | live | `active-region.json id` matches `wg1.conf` Endpoint |
| `mode-consistent` | live | `current-mode` file matches what nft list ruleset shows |
| `killswitch-present` | live | killswitch chain exists with priority -10 in tunnel modes |
| `passthrough-json-valid` | live | `passthrough.json` parses; every device has nft rule generated |
| `dns-path-correct` | live | resolved upstream matches mode (cloudflared / wg1 unbound) |
| `mode-boot-no-surprise` | live | `gp-mode-boot --dry-run` would not change anything |
| `pihole-leases-readable` | live | `gp-leases` returns parsable lease file |
| `tailscale-up` | live | tailscale0 has IPv4, peer count > 0 |
| `wg-mtu-1380` | live | wg0 + wg1 MTU = 1380 (per ARCHITECTURE-INVARIANTS §2) |

**Disruptive checks (`--disruptive` flag required):**
| Check | What it does |
|---|---|
| `mode-cycle-isp` | switch to ISP, verify default route, switch back |
| `mode-cycle-doublehop` | switch to DH, verify exit IP via curl, switch back |
| `region-toggle` | gp-region switch + dual-check, restore prior region |

Disruptive checks always restore prior state on completion or `trap` exit.

---

## 5. Failure escalation

`gp-test` exit codes:
- `0` — all pass
- `1` — at least one fail (specific check listed in output)
- `2` — at least one skip (e.g., test marked skip-on-current-mode)
- `3` — internal error (script bug)

**On regression** (exit=1) when run via timer:
1. Log to `/var/log/gp-test.log` with full check output
2. Send `gp-bridge alert` with the failing check name + diff vs last-good baseline (baseline at `/etc/ghostport/gp-test-baseline.json`)
3. Surface in dashboard "System health" panel as red

This mirrors how gp-preflight escalates today — one consistent alert path.

---

## 6. Coexistence with `gp-prepush`

Both are gauntlets but for different concerns:
- **`gp-prepush`** = secret leak prevention. Code-side, scans `git diff`. Doesn't know about the live device.
- **`gp-test`** = behavior regression. Live-device side, asserts invariants are still true.

Run both before a non-trivial commit. They don't overlap; they layer.

---

## 7. Future-work hooks

- **Fleet-level**: a wrapper that ssh-es to each Pi in the fleet and aggregates `gp-test` results. Out of scope until we have >5 production Pis.
- **CI-side**: a GitHub Actions workflow that runs the offline-tier checks on every PR (`nft-profiles-valid`, JSON validity, syntax checks). Currently the public repo has no CI; design doc punts on this until CI substrate is decided.
- **Customer-image survival**: most checks are green-on-green-on-green for a fresh customer image. The dashboard can call `gp-test` as a self-test diagnostic — surfacing a visible "Run health check" button to the customer.

---

## 8. Constraints honored

| Constraint | How |
|---|---|
| Must not require restart of `ghostport.service` or reboot | Read-only checks call APIs / read state; never restart anything. ✓ |
| Must not interrupt customer connectivity if run on production | Default mode is read-only; mode cycling needs `--disruptive` flag. ✓ |
| Must integrate with `gp-prepush` | Both run from operator habit pre-commit; output formats line up. ✓ |

---

## 9. Follow-up implementation ticket

Reference impl is shipping with this ticket (read-only checks only — disruptive checks are stubbed with `--skip` for now). Filing a follow-up to add the disruptive layer + nightly timer + bridge alerting:

- **Title:** `gp-test: add disruptive mode-cycle checks + nightly timer + bridge alert`
- **Type:** feature
- **Priority:** normal
- **Body:** disruptive checks per §4, systemd timer like `ghostport-update.timer`, gp-bridge alert path on regression with baseline comparison
