# Phantom OS Migration Plan

**Goal:** rebrand from "GhostPort OS" to "Phantom OS" everywhere — filesystem paths, system user, services, commands, docs, marketing — while keeping the company name "GhostPort Technologies" and the device-line name "GhostPort" intact.

**Status:** planning only. Execution begins per phase after sign-off.

**Rule #1:** every phase has a backup and a rollback. Rollback is one command.
**Rule #2:** nothing in Phase 3+ runs without user at the terminal. Rule #18 (no unilateral service/compositor/system restarts) is absolute.
**Rule #3:** run the gauntlet after every file edit. No exceptions.
**Rule #4 (ADDED 2026-04-22):** batch all file edits across Phases 3 + 4 into ONE coordinated restart at the end. systemd keeps in-memory config until `daemon-reload`, so pure file edits are non-disruptive and the system keeps running on old config until the single restart window. Reduces risk, reduces disruption, makes rollback easier.

---

## 1. Name model (source of truth)

| Thing | Before | After |
|---|---|---|
| Company | GhostPort Technologies | GhostPort Technologies *(unchanged)* |
| Device line (hardware) | GhostPort | GhostPort *(unchanged)* |
| OS product name | GhostPort OS | **Phantom OS** |
| Domain | ghostporttechnologies.com | ghostporttechnologies.com *(unchanged)* |
| Dashboard UI brand | GHOSTPORT | **PHANTOM OS** *(already flipped)* |
| Filesystem root | `/opt/ghostport/` | `/opt/phantom/` |
| Config dir | `/etc/ghostport/` | `/etc/phantom/` |
| System user | `ghostport-admin` | `ghostport-admin` *(retained — zero user-visible surface, Phase 5 deferred 2026-04-22)* |
| Command prefix | `gp-*` | **`gp-*`** *(retained — deeply embedded, pragmatic win)* |
| systemd units | `ghostport*.service` | `phantom*.service` |
| GitHub repo | `GhostPortTechnologies/Ghostport-OS` | `GhostPortTechnologies/Phantom-OS` |
| OS codename (apt) | `phantom` | `phantom` *(already correct)* |

**Why keep `gp-*`:** "gp" reads fine as a neutral command namespace ("GhostPort CLI tools" OR "GhostPort-Phantom tools"). Renaming ~60 commands and ~thousands of references adds days with zero user-visible payoff — the CLI prefix doesn't appear in the dashboard, boot splash, or marketing. Treated as historical, not rebranded.

**Why keep `ghostport-admin` user (Phase 5 deferred):** The Linux username never surfaces in the dashboard, first-boot wizard, Plymouth splash, or any user-facing UI. It only appears in `whoami`, `sudo` logs, process ownership, and systemd `User=` fields — all internal plumbing. Renaming carries real lockout risk (the active login session is the same user being renamed; one mistake = console-only recovery). Zero-benefit, high-risk — user directive 2026-04-22: "as long as it is 0 hinderence to the project." Treated as historical backend identifier, matching the `gp-*` decision above.

---

## 2. Phases

Each phase has: scope, entry criteria, steps, validation, rollback, and risk level. Phase 0 already happened.

### Phase 0 — User-visible brand strings *(DONE 2026-04-22)*
- Dashboard header brand + `<title>` flipped to PHANTOM OS
- README.md title + prose swept
- `~/.local/bin/gp-first-boot`, `gp-menu` banner/comment strings updated
- **Staged (not yet installed):** `/etc/os-release`, `/etc/issue`, Plymouth theme, `gp-preflight` banner
- MAC randomizer renamed Phantom → Seadevil (name collision resolved)
- `/tmp/install_os_rename.sh` — still needs re-staging in Phase 1 to say just "Phantom OS"

### Phase 1 — Full backup + re-staging *(tonight, ~30 min, low risk)*
- Snapshot: `/opt/ghostport` tree, `/etc/ghostport`, `/etc/sudoers.d`, `/etc/systemd/system/ghostport-*.service`, `/etc/os-release`, `/etc/issue`, `/home/ghostport-admin` dotfiles, `~/.local/bin/`, `~/.config/`, Plymouth theme dir
- Generate sha256 manifest
- Re-stage OS-identity files: "GhostPort Phantom OS" → "Phantom OS" in os-release, issue, Plymouth, gp-preflight, README, gp-first-boot, gp-menu
- **Validation:** backup hashes verify, staged files parse clean
- **Rollback:** restore from `~/backups/phantom-migration-<timestamp>/`
- **Risk:** none (read-only work)

### Phase 2 — Engineer docs + code strings *(tonight, ~1.5 hr, low risk)*
- Sweep "GhostPort OS" → "Phantom OS" in:
  - `/opt/ghostport/docs/*.md` (all SOPs, runbooks, tutorials)
  - `/opt/ghostport/CLAUDE.md` (project-level claude guide)
  - `/home/ghostport-admin/CLAUDE.md` (top-level)
  - `ghostport-server.js` log strings + startup banner
  - Desktop Python app `__doc__` strings, comments
  - systemd unit `Description=` fields
- Do **NOT** change paths, unit names, or user references in this phase — strings only
- Run gauntlet after each batch
- **Validation:** grep for remaining "GhostPort OS" — only marketing/brand-intentional mentions should remain
- **Rollback:** per-file from Phase 1 backup
- **Risk:** low — no runtime state changed

### Phase 3 — Filesystem path rename */opt/ghostport → /opt/phantom* *(needs user present, ~2 hr, medium risk)*
- Dual-path strategy:
  1. `cp -a /opt/ghostport /opt/phantom` (duplicate, both exist)
  2. Update every systemd unit's `ExecStart=/opt/ghostport/...` to `/opt/phantom/...`
  3. Update sudoers rules containing `/opt/ghostport/` paths
  4. Update `gp-update` GitHub URL expectation
  5. `systemctl daemon-reload` + restart affected services **one at a time** with user confirmation
  6. Validate everything still runs
  7. After 24h soak: remove `/opt/ghostport`, leave symlink `/opt/ghostport -> /opt/phantom` for 30 days, then delete symlink
- `/etc/ghostport` → `/etc/phantom` same pattern, same caution
- **Validation:** `gp-preflight` green, dashboard reachable, all services active
- **Rollback:** `systemctl stop $svc && mv /etc/systemd/system/backup/... && systemctl daemon-reload && systemctl start $svc`
- **Risk:** medium — one missed path breaks a service. Mitigated by dual-path period.

### Phase 4 — systemd unit rename *(needs user present, ~1 hr, medium risk)*
- 10 units to rename:
  - `ghostport.service` → `phantom.service`
  - `ghostport-boot.service` → `phantom-boot.service`
  - `ghostport-discord.service` → `phantom-discord.service`
  - `ghostport-dns-guard.service` + `.timer` → `phantom-dns-guard.service` + `.timer`
  - `ghostport-health-guard.service` + `.timer` → `phantom-health-guard.service` + `.timer`
  - `ghostport-reset.service` → `phantom-reset.service`
  - `ghostport-sni.service` → `phantom-sni.service`
  - `ghostport-ui.service` → `phantom-ui.service`
  - `ghostport-update.service` + `.timer` → `phantom-update.service` + `.timer`
  - `ghostport-auto-update.service` + `.timer` → `phantom-auto-update.service` + `.timer`
- Dual-alias strategy: new units installed; old units become `Alias=` entries that redirect. daemon-reload. systemctl enable new names.
- Remove old unit files after 24h soak
- **Validation:** all units active under new names; nothing in `journalctl -u ghostport*` after reboot
- **Rollback:** leave old units in place, don't alias-swap until confidence
- **Risk:** medium — boot sequence depends on these. Mitigated by dual-alias.

### Phase 5 — User rename *(DEFERRED INDEFINITELY — 2026-04-22 user directive)*

**Decision:** keep `ghostport-admin` as the Linux system user. Do NOT rename.

**Why deferred:** The username never appears in the dashboard, first-boot wizard, Plymouth splash, or any user-visible UI. It lives in `whoami`, `sudo` logs, process ownership, and systemd `User=` fields — all internal plumbing. Operator verdict: *"as long as it is 0 hindrance to the project"* — it is zero hindrance, and the rename carries real lockout risk (the active session is the same user being renamed). Treated as a historical backend identifier, same posture as the `gp-*` command prefix decision in §1.

**If this ever needs to be reopened** (e.g. for a legal / trademark audit, or the username starts appearing in user-visible places), the original Phase 5 procedure is preserved in the pre-decision backup at `~/backups/phantom-migration-20260422-163424/` — retrieve the old plan from the backup for the steps.

### Phase 6 — GitHub repo rename + OTA compat *(coordinated with EC2 Claude, ~1 day, medium risk)*
- GitHub repo rename: `GhostPortTechnologies/Ghostport-OS` → `GhostPortTechnologies/Phantom-OS`
  - GitHub auto-redirects old URLs but it's fragile under edit
  - Update `gp-update` script: change hardcoded URL
  - Deploy hotfix of `gp-update` to existing fleet FIRST so they pick up the new URL before the old one is redirected
- EC2 Claude updates any references in fleet API / provisioning code
- `api.ghostporttechnologies.com` endpoints don't change (domain stays)
- **Validation:** fresh `gp-update` run on a test device pulls from new URL successfully
- **Rollback:** rename repo back on GitHub (they allow); revert `gp-update`
- **Risk:** medium — field devices running old URL get stuck if redirect fails

### Phase 7 — Marketing rebrand *(EC2 Claude's work, timing TBD)*
- Bridge message to EC2 Claude with the spec:
  - Blog: update all articles referencing "GhostPort OS" → "Phantom OS"
  - Affiliates portal: sales-playbook.html, referral copy
  - Investors page
  - tools.ghostporttechnologies.com (Privacy Exposure Score)
  - demo.ghostporttechnologies.com (when it launches per OS-DEMO-VM-PLAN.md)
  - Content kit: 80 JPG + 80 MP4 fact cards need recapture/re-render
  - Moltbook: retroactive edits if plausible, else future posts only
- Pi-side has zero to do here; we're the blocker on spec, not execution

---

## 3. Timing vs v1.1.0 beta ship

**The elephant.** v1.1.0 ships to friends-beta this week per TOMORROW.md. Full B is ~1-2 weeks.

**Three options:**

**Option X — ship v1.1.0 AS "GhostPort Phantom OS" first, then full B over 2 weeks.** Friends get units tomorrow-ish. Dashboard + README already say Phantom OS. Internal paths unchanged. Then full rename is a v1.2 release. Safe. Marketing-consistent once v1.2 is out.

**Option Y — hold v1.1.0, execute full B, ship as "Phantom OS" in ~2 weeks.** Friends wait. But they get a clean product with no brand-seam.

**Option Z — ship v1.1.0 NOW (as "GhostPort Phantom OS"), execute B in parallel, and release v1.1.1 as "Phantom OS" within 2 weeks. Friends upgrade via `gp-update`.** Hybrid. Risk: friends see a name flip inside the beta period.

**Recommendation: Option X.** Shipping unblocks revenue, friends-beta validates the product as-is, and B is done properly without rush. v1.2 ships as "Phantom OS" and that's the first-impression for the public launch.

---

## 4. What I can/can't do tonight

**Tonight, no user coordination needed:**
- ✅ Phase 1 backup + re-staging (automatic, low-risk)
- ✅ Phase 2 doc + string sweep (mass search/replace + gauntlet)

**Tonight, needs a one-paste from the user:**
- ✅ Install the restaged OS-identity files (sudo)

**Tomorrow or later, user at terminal:**
- ⚠️ Phase 3 path rename (service restarts per rule #18)
- ⚠️ Phase 4 systemd unit rename (service restarts)
- ⚠️ Phase 5 user rename (requires root session, risk of lockout)
- ⚠️ Phase 6 GitHub rename (needs EC2 coordination + OTA hotfix staging)

**Ongoing:**
- 📡 Phase 7 marketing (EC2 Claude works in parallel once spec is received)

---

## 5. Rollback summary

Any phase can be undone. Each phase leaves the previous state backed up. Final safety net: full image snapshot at `/home/ghostport-admin/backups/phantom-migration-<TS>/` before Phase 1 starts. Anything after Phase 1 is recoverable by restoring that snapshot + re-installing root-owned files.

---

## 6. Open questions

1. **Option X vs Y vs Z** — pick one. §3 recommendation is X.
2. **Company name** — does "GhostPort Technologies" also change to something Phantom-themed? (I'm assuming no.)
3. **Domain** — does `ghostporttechnologies.com` also change? (I'm assuming no — massive SEO/legal cost.)
4. **gp-* prefix** — keep as-is per §1 rationale, or also rename to `pm-*`? (I'm recommending keep.)
5. **Device line "GhostPort"** — on the hardware label / packaging / product landing page, still say "GhostPort the hardware runs Phantom OS"? (I'm assuming yes.)
6. **EC2 Claude coordination** — when should he start Phase 7? In parallel with tonight's work, or after we confirm Phase 3-5 landed cleanly?

---

## 7. First next action

Phase 1 (backup + re-staging). No user action needed for that — I can execute it solo. I'll report when done and hand you the updated `sudo bash /tmp/install_os_rename.sh` for the root-owned files.
