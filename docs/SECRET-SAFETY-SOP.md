# Secret Safety SOP

**Rule above all rules: a credential must never be echoed, logged, transmitted, or persisted where it wasn't explicitly authorized to be.**

**Rule above the rule above: when a leak or security violation occurs or is suspected — STOP, ALERT the operator before continuing any other work, and begin revocation on the issuing service (not just local cleanup).**

**Rule origin:** 2026-04-23 — operator directive after I leaked the same user's PAT twice in ten minutes, and the initial Pa6wXsV4… bridge token was only noticed after being committed to git for a month. *"Write the SOP and do not fail us again but make sure to alert us of leaks and possible security violations and failed processes."*

This SOP is mandatory reading alongside `OPERATOR-SOP.md` and `ai-dev-guide.md`. It applies to every Claude agent operating on this project, every session.

---

## 1. What counts as a "secret"

Anything that grants access to a system, identity, or data. Treat all of these as radioactive:

- Personal Access Tokens (`ghp_*`, `github_pat_*`, `gho_*`)
- API keys (`sk_live_*`, `sk_test_*`, `pk_live_*`, `whsec_*`, `AIza*`, `xox[bap]-*`, `rk_live_*`)
- Passwords (plaintext, even "temporary" ones)
- HMAC shared secrets, bridge tokens, fleet-auth tokens
- Private keys of any kind (RSA, Ed25519, ECDSA — anything between `BEGIN…PRIVATE KEY` / `END…PRIVATE KEY`)
- WireGuard `PrivateKey = …` lines
- Session cookies, JWTs, refresh tokens
- Backup code lists, TOTP seeds
- Device passcodes (scrypt hashes are slightly less toxic but still treat as secret)
- SSH private keys
- Wi-Fi pre-shared keys (`wpa-psk`, `wpa_passphrase`)
- Stripe webhook secrets
- Database passwords / connection strings with embedded passwords
- Signed URLs with embedded HMAC

If in doubt — **treat it as a secret.** The cost of over-caution is small; the cost of a leak is rotation + incident response + trust damage.

---

## 2. Absolute prohibitions (never do these)

### 2.1 Never echo a secret to stdout or stderr
- `echo $TOKEN` — forbidden
- `set -x` with secrets in the current context — forbidden
- `cat` a file containing secrets — forbidden unless output is immediately consumed and never logged
- Unredacted `curl -v` with auth headers — forbidden

### 2.2 Never embed a secret in a URL
- `https://TOKEN@github.com/...` in a git remote — forbidden (this is what bit us 2026-04-23)
- `https://user:pass@...` anywhere — forbidden
- Query-string tokens in log lines — forbidden
- Any URL that will be echoed by `git remote -v`, `git filter-repo` notices, error messages, or logs — forbidden

### 2.3 Never pass a secret as a command-line argument when a file or env var works
- `curl -H "Authorization: Bearer $TOKEN"` in a shell script where `ps ax` reveals the arg to any user — forbidden (use `--header @file` where the file has `0600` perms, or set env and reference via `-H "Authorization: Bearer $TOKEN"` in a non-exported shell)
- `psql -p PASSWORD` — forbidden; use `PGPASSWORD` env var or `.pgpass` file
- SSH keys as CLI args — forbidden; use `-i /path/to/key`

### 2.4 Never commit a secret, even to a private repo
- Private-repo commits are still accessible to all collaborators, GitHub employees (theoretically), backup snapshots, anyone who clones, anyone who is ever added to the org in the future.
- "I'll remove it in a later commit" — forbidden. Git history is forever unless rewritten + force-pushed (which is a big hammer).
- `.gitignore` is not enough if the file is already tracked. Use `git rm --cached` first, then add to `.gitignore`.

### 2.5 Never store a secret in a file that's not in `.gitignore`
- Every config file containing a secret must have a matching `.gitignore` pattern before creation.
- Prefer `/etc/ghostport/<name>-auth.json` (in repo's `.gitignore`) over repo-local config.
- Prefer `0600` root-owned files over world-readable ones.

### 2.6 Never relay a secret back to the user via bridge message, chat output, or any other transcript-persistent channel
- Bridge messages are signed+encrypted but they still sit in `chamber.json` / bridge inbox as plaintext server-side after decryption.
- Chat output (this transcript) may be retained indefinitely by the user / Anthropic.
- If you need the user to paste a secret to you, design a `read -s` flow where the value lives only in shell env and never passes through an echoed command.

---

## 3. Required patterns (how to do things safely)

### 3.1 Accepting a secret from the user
```bash
read -rsp "Paste <thing> and press Enter: " SECRET && echo
# use $SECRET in commands that DON'T echo it
# when done:
unset SECRET
```
Never ask the user to paste into a message where the chat backend will see and store it.

### 3.2 Git authentication
- **Preferred:** SSH key (`git remote set-url origin git@github.com:org/repo.git`) — no token anywhere, keypair authenticates.
- **Second preferred:** `gh auth login --with-token < tokenfile` — stores token in encrypted credential store, git uses it transparently, URL stays clean.
- **Third:** git credential helper (`git config --global credential.helper store` with a `0600` creds file, or `manager-core` on desktops).
- **Forbidden:** `https://TOKEN@github.com/...` embedded in remote URL.

### 3.3 Third-party API auth
- Put the key in a gitignored config file with `0600` perms.
- Read at runtime via `jq -r '.field' /etc/phantom/foo-auth.json`.
- Fall back to env var (`FOO_KEY`) if config is missing.
- Exit with a clear message if neither is present — never silently post with an empty Bearer header.
- Example: see `compliance/alert-monitor.sh` lines 6-17 for the canonical pattern (ships post-2026-04-23).

### 3.4 Subprocess output that MAY contain URLs/tokens
When running a command that could echo credentials (`git remote -v`, `git filter-repo`, `curl -v`, error-case dumps), either:
- Pre-sanitize the state so there's nothing to echo (e.g., remove tokens from `.git/config` before running), OR
- Pipe stderr through a redactor: `2> >(sed -E 's|https://[^@/]+@|https://[redacted]@|g; s|Bearer [A-Za-z0-9_.-]+|Bearer [redacted]|g' >&2)`, OR
- Capture to a temp file and grep-process before displaying

### 3.5 Token-bearing REST calls
```bash
# BAD — token in command line, visible in ps:
curl -H "Authorization: Bearer $TOKEN" https://...

# GOOD — token via stdin header file with 0600 perms:
umask 077
printf 'Authorization: Bearer %s\n' "$TOKEN" > /tmp/hdr.$$
curl -H @/tmp/hdr.$$ https://...
rm -f /tmp/hdr.$$
```

---

## 4. Proactive alerting — the operator's explicit ask

**When you observe or commit any of the following, STOP your current work and alert the operator BEFORE proceeding.** Do not bury the alert in a long summary; lead with it.

Minimum alert-trigger list:

| Event | Alert format |
|---|---|
| You see a secret in your own tool output | `LEAK DETECTED: <token prefix>… appeared in <command> stderr. Not yet persisted to repo/log. Rotate on <service> before we continue.` |
| You passed a secret in a way that landed in a transcript | `LEAK DETECTED: <what> leaked via <channel>. Blast radius: <where it can be read>. Revoke on <service> immediately.` |
| An operation you're about to run would expose a secret (caught in review, before execution) | `STOP — planned command would echo <secret-type>. Alternate: <safe pattern>. Proceed?` |
| A failed process may have left state in a dangerous place | `FAILED PROCESS: <command> exited <code>. Possible state: <config half-written / permissions changed / secrets in tmp>. Cleanup needed: <steps>.` |
| A `sudo -n` NOPASSWD call fails unexpectedly | `AUTH FAIL: sudo for <command> returned denied. Either my path is wrong OR sudoers is compromised. Check /etc/sudoers.d/ and recent audit log.` |
| A security-relevant file is in an unexpected location or has unexpected perms | `PERM ANOMALY: <file> has mode <mode>, expected <expected>. Could indicate tampering or prior failed install.` |
| A gauntlet check (syntax, shellcheck, py_compile, node --check, visudo, nft -c) returns an error on code you're about to ship | `GAUNTLET FAIL: <tool> rejected <file>: <error>. Not shipping until fixed.` |
| A secret-scan sweep finds a match you didn't expect | `SECRET SCAN HIT: <pattern> found in <file>:<line>. Context: <excerpt>. Rotate + remove + history-rewrite if already committed.` |
| Git commit includes a file you didn't intend | `COMMIT ANOMALY: <file> was staged without explicit add. Check .gitignore and git status before pushing.` |
| A force-push to a shared branch is about to happen | `FORCE PUSH: about to rewrite origin/<branch>. This invalidates anyone else's local clone. Confirm before pushing.` |

**Format discipline:** the alert must appear in your response text, not just in tool output. If the alert is significant, it's the LEAD of your next message — not buried in the middle or end.

---

## 5. Rotation procedure after a leak

A leak has occurred. Follow this sequence — do not deviate:

1. **STOP** the current task. Do not continue writing commits, pushing to remotes, or composing multi-step operations.
2. **CHARACTERIZE** the leak in one message to the operator:
   - What leaked (token prefix only — never echo the full value again)
   - Where it leaked (tool stdout, stderr, bridge message, committed file, transcript)
   - Who can see it (session participants, transcript retention, public/private repo visibility)
   - What the token grants (scopes, access level, blast radius)
3. **REVOKE** on the issuing service side. This is NOT optional and NOT replaceable by local history scrub. Specifically:
   - GitHub PAT → `https://github.com/settings/tokens` → Revoke
   - Stripe key → Stripe Dashboard → API keys → Reveal+Rotate
   - HMAC/bridge token → notify the server-side owner via bridge; wait for revocation ack
   - SSH key → remove from `~/.ssh/authorized_keys` on every target; remove from `github.com/settings/keys`
   - Device passcode → `sudo gp-passcode reset`
4. **VERIFY** revocation. A test request with the old credential must return auth-failure. Local history rewrites confirm nothing until the issuer has actually rejected the credential.
5. **CLEAN UP** locally (in this order, not earlier):
   - Replace the secret in source with a config-file read or placeholder
   - Rewrite git history with `git filter-repo --replace-text` if the secret ever committed
   - Expand `.gitignore` and audit for similar patterns
   - Force-push to any remotes (after explicit operator confirmation)
6. **DOCUMENT** in a `feedback_*.md` memory entry so future sessions learn from the specific failure. Use a name like `feedback_no_<pattern>.md`.
7. **RESUME** original task only after steps 1-6 are complete and the operator has acknowledged the alert.

---

## 6. Known tool behaviors to anticipate

These tools echo credentials or URLs by default. Plan around them.

| Tool | Echo risk | Mitigation |
|---|---|---|
| `git remote -v` | Full URL including embedded token | sed-redact on display; prefer credential helper |
| `git config --get remote.origin.url` | Same | Same |
| `git filter-repo` | Notice on origin removal includes full URL | Pre-remove origin via `git remote remove origin` before running, then re-add AFTER from a clean URL |
| `git clone` in verbose mode | May log token | Use non-verbose; pipe stderr through redactor |
| `curl -v` | Request + response headers including Authorization | Use non-verbose; or `curl --trace-ascii` with post-filter |
| `wget -d` | Debug logging of headers | Same |
| `ssh -v` | Key paths, sometimes host keys | Non-verbose when possible |
| `systemctl status <unit>` | Journal tail may include secret from failed service | Scan output before displaying |
| Error messages from failed HTTP calls | Occasionally include request auth headers | Capture, scan, redact |
| `journalctl -u <unit>` | All of the above accumulated | Grep for known patterns; redact for display |
| `set -x` in shell scripts | Every command including its args | Disable before any secret-touching line |
| `ps aux` | CLI args of running processes | Prefer env vars, stdin, or `@file` for secrets passed to long-running tools |

---

## 7. Failed-process reporting (separate from leaks — same SOP)

A "failed process" is any operation that exits with unexpected status, leaves unexpected state, or partially completes. These are not always security issues but they always need reporting.

**Always report:**
- Non-zero exit codes from anything run with `set -e` (the script will have aborted — don't silently restart)
- Partial writes to config files (half-updated JSON, truncated sudoers)
- Installs that completed in an intermediate state (service installed but not enabled; package installed but not configured)
- Force-pushes that succeeded on part of the ref set (e.g., `main` pushed but tags failed)
- `visudo -cf` rejections (the sudoers drop-in file is broken — don't leave it live)
- `nft -c -f` validation failures (the firewall rules would brick connectivity — don't load them)
- `py_compile`, `bash -n`, `node --check` failures on files you're about to install
- Service restart failures — especially ones that leave the service inactive
- Any interactive prompt that required user input you didn't authorize

**Report format:** one clear sentence naming the command, exit code, and what state the system is in. Never "I'll keep going" — stop, report, wait for direction.

---

## 8. Enforcement

This SOP is enforced by:
- **Pre-action discipline** — before running any command, audit it against §2's prohibitions
- **Post-action observation** — scan your own tool output for the secret patterns in §1 before sending to operator
- **Alerting discipline** — §4's trigger list is a hard checklist
- **Rotation discipline** — §5's six steps are not optional when a leak occurs
- **Memory discipline** — after any incident, write a `feedback_*.md` entry per §5 step 6

If you violate this SOP and the operator notices before you do, you have failed worse than the initial leak — because the whole point of §4 is proactive alerting.

---

## 9. PAT / SSH-key rotation playbook (MANDATORY pattern)

**The one way to rotate a GitHub PAT on this project. No variants. No exceptions.** Rule origin: 2026-04-23 — I leaked the user's PAT three times across two rotations because I kept embedding the token in `https://TOKEN@host/path` URLs. This playbook replaces every previous pattern.

### 9.1 Trigger — when to rotate

- The current PAT has been exposed (leaked in a transcript, logs, screenshot, chat, output of any tool, paste buffer)
- The current PAT's expiration is within 14 days
- The PAT-owning user has changed (account handover, offboarding)
- GitHub emailed you a secret-scanning notice
- Any time you suspect rotation is warranted — cost of rotation is low, cost of compromise is high

### 9.2 Banned patterns (never again)

- **`https://TOKEN@github.com/...` as a git remote URL.** This is the pattern that has leaked every single time. Forbidden.
- `git remote add/set-url origin "https://TOKEN@..."` — the token is in the shell command, visible in bash history + tool output.
- `git config --get remote.origin.url` output containing a token, shown unredacted.
- Asking the user to paste the PAT into a message where the chat backend will see it.
- Running `git filter-repo` / `git filter-branch` while a tokenized URL is set on origin (filter-repo's "Removing origin" notice echoes the full URL to stderr — automatic leak).

### 9.3 The one safe rotation flow

**Step A — revoke the existing token on GitHub first.**
Go to `https://github.com/settings/tokens` (Tokens classic) or `https://github.com/settings/personal-access-tokens` (fine-grained). Click the token → Revoke. This kills the issued credential at the source; no amount of local cleanup matters if the issuer still accepts it.

**Step B — generate a replacement with minimum scopes.**
For this repo: `repo` is sufficient (enables clone, fetch, push on private + public repos). Do NOT tick `workflow`, `admin:*`, `delete_repo`, `gist`, or anything else unless a specific workflow requires it.

**Step C — strip any embedded token from `.git/config` BEFORE doing anything else.**
```
cd /opt/ghostport
git remote set-url origin https://github.com/GhostPortTechnologies/Ghostport-Phantom-OS.git
```
This removes whatever's in `https://…@…` and leaves a clean URL. Do this even if the leaked token is already revoked — it closes the "echoed URL leaks old token" vector for future tools.

**Step D — install new token via `git credential-store` (the ONLY sanctioned persistence path).**
Use the canonical script at `/tmp/rotate-gh-pat.sh` (staged 2026-04-23). It:
- Prompts via `read -rsp … </dev/tty` — token never enters stdin buffer of a piped paste, never in bash history, never echoed
- Stores the new PAT in `~/.git-credentials` with `0600` perms (outside the repo, never logged by routine git commands)
- Sets `git config --global credential.helper store`
- Tests with `git ls-remote` (safe — doesn't echo credentials)

**Step E — verify `.git/config` contains NO token.**
```
grep -E '^\s*url\s*=' /opt/ghostport/.git/config
```
Expected: `url = https://github.com/GhostPortTechnologies/<repo>.git`. No `@` in the URL. If the URL still has `TOKEN@`, rotation failed — start over from Step C.

**Step F — confirm the old token is dead on GitHub.**
A test request with the revoked token must return 401. If revocation step A was skipped or failed, the old leaked value still works — finish the rotation by actually revoking it.

### 9.4 Why git-credential-store, not token-in-URL

| Approach | Token visibility on leak |
|---|---|
| `https://TOKEN@host/path` in `.git/config` | Every `git remote -v`, `git filter-repo` notice, `git clone --mirror` of the bare repo, any error that echoes the remote URL. One accident = instant transcript leak. |
| `gh auth login --with-token` (stores in `~/.config/gh/hosts.yml`) | `gh auth status` shows account but not token; routine git commands don't touch the file. Safer. Requires `gh` installed. |
| `git credential-store` → `~/.git-credentials` | File is `0600`, outside repo, never echoed by routine git commands. Git reads it only when auth is needed. Standard pattern for CI/server use. |
| SSH key (`git@github.com:org/repo.git`) | No token. Server verifies keypair; private key stays local. Strongest option; only downside is setup friction. |

**Default for this project: `git credential-store`.** SSH key is stronger but requires per-machine key setup; `gh` is nice but isn't always installed. `credential-store` is built into git everywhere and works out of the box.

### 9.5 What to do when you see a URL-embedded token in any output

Immediately, before anything else:
1. **Do not run any `git filter-repo` / `filter-branch` / force-push.** Those tools echo the URL on stderr and create a second-order leak.
2. Strip the URL first: `git remote set-url origin <clean URL without @>`.
3. Only then run the rewrite / cleanup. The filter-repo notice will now echo the clean URL, not a token.
4. If the token was already leaked: the damage is done — just rotate (§9.1-9.3) and move on.

### 9.6 Multi-PAT scenarios (stacking leaks)

If rotation #1 also leaked (e.g., tool echoed the new token during setup — exactly what happened 2026-04-23 with `filter-repo`), the new PAT is compromised too. Revoke BOTH the old and the new, then generate a third. **Every leaked PAT must be individually revoked on GitHub** — revoking the first doesn't revoke the second. Treat each as independently issued.

---

## 10. Defensive stack — installed pre-commit + CI guardrails (2026-04-23)

After the triple-leak incident, the following automated defenses were deployed to make this class of failure hard to repeat even under human error or AI-agent error. Every contributor on this repo operates behind these gates.

### 10.1 `.gitleaks.toml` (repo root, tracked)

Machine-readable secret-detection config. Inherits gitleaks' default rule set (AWS, GitHub, Stripe, Slack, private keys, etc.) and adds Phantom-OS-specific rules:

| Rule id | Catches |
|---|---|
| `phantom-bridge-token-known` | The four known-historical leaked token values. Defense-in-depth — ensures they can never reappear even if filter-repo state ever gets out of sync. |
| `phantom-bridge-token-shape` | Generic 40-60-char base64 tokens assigned to `bridge_token`, `fleet_token`, `alert_token`, `AUTH_TOKEN`, etc. |
| `wireguard-private-key` | `PrivateKey = <base64>` assignments in WireGuard configs. |
| `phantom-passcode-plaintext` | Plaintext passcode assignments (product moved to scrypt hash only in March). |
| `tokenized-git-remote-url` | `https://TOKEN@github.com` URLs — the specific pattern that bit us 2026-04-23. |

Allowlist entries cover known-safe content: binary assets, gitignored compliance docs (kept locally), the SOP itself (cites historical tokens in context), placeholder sentinels like `MISSING_BRIDGE_TOKEN` / `${TOKEN}` / `YOUR_*_HERE`.

### 10.2 `scripts/git-hooks/pre-commit` (repo-tracked hook body)

Client-side hook that runs `gitleaks protect --staged` against every proposed commit. Blocks the commit if any `.gitleaks.toml` rule fires. Emits a redacted finding summary and an instruction block pointing to §5 of this SOP.

**Lives at `scripts/git-hooks/pre-commit` in the repo (tracked).** `.git/hooks/pre-commit` is a symlink to it — so updates to the tracked file propagate immediately to the live hook.

### 10.3 `scripts/install-hooks.sh` (one-shot installer for fresh clones)

Run once after `git clone`:
```
./scripts/install-hooks.sh
```
Creates the `.git/hooks/pre-commit → scripts/git-hooks/pre-commit` symlink, validates gitleaks is on PATH, reports readiness. Idempotent.

### 10.4 `.pre-commit-config.yaml` (framework integration)

For contributors using the `pre-commit` framework (`pip install pre-commit` then `pre-commit install`). Declares the same gitleaks hook plus: trailing-whitespace / EOF / large-file / merge-conflict / JSON / YAML / case-conflict / mixed-line-ending checks, `ruff` + `ruff-format` for Python, `shellcheck` for bash. Both the framework path and the plain-hook path end up running gitleaks — redundant-on-purpose.

### 10.5 GitHub-side settings (operator action, one-time per repo)

**Enable in GitHub Settings → Code security and analysis:**
- **Push protection** — rejects `git push` operations containing known secret patterns. Second line of defense after the pre-commit hook.
- **Secret scanning alerts** — continuously scans the committed tree; emails the maintainer when a new secret is detected.
- **Dependabot alerts + security updates** — for npm/apt dep vulns.
- **Code scanning (CodeQL)** — static analysis of JS/Python. Free on public repos.

**Enable in GitHub Settings → Branches (for `main`):**
- Require pull-request review before merging
- Require status checks to pass (pre-commit CI run) before merging
- Require linear history
- Disallow force-push (except for explicit history-rewrite maintenance, authorized per-incident)
- Disallow direct deletions

These are not yet scripted because they're one-click UI settings. Re-verify on every new repo.

### 10.6 `.gitattributes` hygiene (tracked)

Repo-wide defaults: `* text=auto eol=lf`. Binary markers on `*.png`/`*.jpg`/`*.pdf`/`*.gz` etc. `linguist-generated=true` on `*.min.js` + `manifest.json` + `package-lock.json` so GitHub's language-stats bar isn't skewed by vendored/generated files.

### 10.7 How contributions now move through the gates

Proposed commit → **pre-commit hook (gitleaks)** → commit lands locally →
`git push` → **GitHub push protection** → push accepted →
continuous **GitHub secret scanning** → maintainer alerted on any new detection.

A leak now requires **three sequential failures**: the author's local hook, GitHub's server-side push protection, AND the post-commit scanner. Each is independent; each has different rulesets; each has a different owner. That's what the industry-standard stack looks like and that's what we now run.

### 10.8 Bypassing (DO NOT unless genuinely necessary)

Emergency bypass of local hook: `git commit --no-verify`. This is logged (bash history), shows up in `git reflog`, and is immediately visible to anyone reviewing. Use only when the hook is broken (not when it's catching a real finding). Bypass is NOT available for server-side push protection — that's the point.

If a hook bypass happens, the bypasser owes the operator an immediate Chamber / bridge message with the justification. No exceptions.

---

## 11. Related documents

- `feedback_no_tokens_in_urls.md` (memory) — the specific 2026-04-23 incident that triggered this SOP
- `feedback_no_passwords.md` (memory) — earlier rule about passwords in memory
- `feedback_gauntlet_paste_commands.md` (memory) — paste-block validation (applies to secret-bearing installers too)
- `OPERATOR-SOP.md` §8 — rules learned the hard way (includes related sudo/service incidents)
- `GOLDEN-IMAGE-SOP.md` §7 — strip verification (checks for leftover secrets before SD ship)
- `/tmp/rotate-gh-pat.sh` — canonical PAT rotation script (install to `~/.local/bin/gp-rotate-gh-pat` for permanence)
- `/tmp/rotate-bridge.sh` + `/tmp/rotate-bridge.py` — bridge-token rotation (same pattern, different target file)
