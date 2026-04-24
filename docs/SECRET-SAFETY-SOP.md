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
grep -E '^\s*url\s*=' /opt/phantom/.git/config
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

## 11. Pre-public repo sweep playbook (MANDATORY before flipping private → public)

Codified from the 2026-04-23 Phantom OS repo public-flip prep, which surfaced multiple issues the default gitleaks scan missed. Run this playbook on any repo before changing visibility from private to public. **Every bullet is a gate, not an optional check.**

### 11.1 Order of operations

1. **Scrub then scan**, never the reverse. If there's a known historical leak (committed secret), use `git filter-repo --replace-text` FIRST, force-push, then run audits against the rewritten history. Auditing before scrubbing wastes time — you'll re-run every scan.
2. **Strip tokens from `.git/config` URLs before any tool that might echo them.** `git filter-repo` prints the removed origin URL to stderr — if the URL has an embedded PAT, it leaks to the transcript. Pre-strip with `git remote set-url origin <clean>`.
3. **Untrack before you redact.** For files that will never be public-appropriate (internal runbooks, infra docs, compliance-confidential), `git rm --cached` + `.gitignore` is cleaner than rewriting content. Keeps local copy, removes from ship path.
4. **Every automated gate is in place before the flip** — local pre-commit hook, server-side push protection, secret scanning. §10 has the full stack.

### 11.2 The mandatory sweep dimensions

Run each. Don't stop at one "clean" result — every dimension catches different classes of leak.

#### 11.2.1 Automated secret scan
```
gitleaks detect --config .gitleaks.toml --redact   # full history
gitleaks detect --config .gitleaks.toml --no-git --source . --redact   # working tree
```
Clean means: `"no leaks found"` in both. If any hits remain, classify each: true-positive (fix) vs allowlist candidate (add to `.gitleaks.toml [allowlist]`).

**Gitleaks scans HEAD-state of each commit object but doesn't catch strings that appear *only in diffs* (added and later removed in a follow-up commit). Always run the raw-history check below alongside gitleaks — they cover different surfaces.**

```
# Raw-history check — catches strings that lived in diff context even if HEAD is clean now.
# Substitute the list of strings that must never appear anywhere in history.
git log --all -p 2>/dev/null | grep -cE "<token1>|<token2>|<token3>|<token4>"
```
Expected: `0`. Any non-zero count = rewrite history (`git filter-repo --replace-text`) before flipping.

#### 11.2.2 PII in tracked content
Specifically hunt for: real names (first + last), personal emails (not `@<company>`), phone numbers, home addresses, social media handles.

```
# Real name sweep — substitute your operator's name
for name in "$OPERATOR_FIRST_NAME" "$OPERATOR_LAST_NAME"; do
    git ls-files | xargs grep -l "$name" 2>/dev/null
done

# Personal emails (exclude company aliases)
git ls-files | xargs grep -hoE '[a-zA-Z0-9._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' 2>/dev/null \
    | sort -u | grep -vE "^(support|licensing|noreply|no-reply|info|admin|security|legal|privacy|contact|sales|abuse)@<your-domain>"

# Git author history (what email appears in commits?)
git log --all --format='%ae %ce' | tr ' ' '\n' | sort -u
```
Redact to impersonal nouns ("the operator", "the maintainer") OR untrack the file entirely. Keep `@<company>` aliases as public contact channels.

#### 11.2.3 Infrastructure disclosure
Your cloud server IPs, internal subnets, SSH paths, PEM filenames, ISP gateway IPs.

```
# Specific-IP sweep — substitute your real prod IPs
git ls-files | xargs grep -lE "44\.214\.101\.82|54\.211\.104\.73|10\.66\.6[67]\.|192\.168\.50\." 2>/dev/null

# SSH key paths + PEM filenames
git ls-files | xargs grep -hE "ssh -i [^ ]+\.pem|id_rsa|id_ed25519" 2>/dev/null
```

For each hit, decide: replace with DNS name (`api.<domain>`), replace with generic phrasing ("the control-plane endpoint"), or untrack the file. **SSH-PEM-filename references are always untrack.**

#### 11.2.4 Default credentials
Factory-reset scripts, initial-setup defaults, docker-compose example passwords, etc.

```
git ls-files | xargs grep -hnE "wpa_passphrase=|password=|admin:admin|root:root|changeme|default_secret" 2>/dev/null \
    | grep -vE "placeholder|YOUR_|CHANGEME|EXAMPLE|<.*>"
```

Any literal string that the code uses as a default credential in production = classify as a leak. Fix: replace with random generation per-install (see `scripts/gp-factory-reset` for the pattern — 16-char Crockford-alphabet PSK generated via `/dev/urandom`, printed to stdout + persisted to `/boot/firmware/` so the user can retrieve from SD).

#### 11.2.5 Binary + metadata leaks
EXIF on images (GPS, author, device provenance), SVG metadata (title/desc/creator/author elements), PDF metadata, Office doc metadata.

```
# EXIF on tracked images
git ls-files '*.png' '*.jpg' | while read f; do
    # Check symlink vs regular first — git tracks symlinks as 37-byte text, not binary content
    [ "$(git ls-tree HEAD "$f" | awk '{print $1}')" = "100644" ] || continue
    python3 -c "
from PIL import Image
img = Image.open('$f')
if img.getexif(): print('  EXIF: $f —', list(img.getexif().keys()))
" 2>/dev/null
done

# SVG metadata
git ls-files '*.svg' | xargs grep -lE "<(metadata|title|desc|author|creator)" 2>/dev/null
```

Strip via re-save without metadata (Pillow: `Image.new(mode, size).putdata(list(img.getdata())).save()`).

#### 11.2.6 Symlink gotcha
`grep`/`gitleaks` may flag a tracked SYMLINK as containing secrets because they follow the link to the real file on disk. **Git stores only the link text (usually 30-60 bytes), not the target's content.** Verify with:
```
git ls-tree HEAD <path>   # mode 120000 = symlink; 100644 = regular
git cat-file -p HEAD:<path>   # shows what git actually tracks
```
If mode is 120000, the hit is a false positive — the real file's content is NOT in the repo.

#### 11.2.7 npm integrity hash false-positive
`package-lock.json` contains many 80-char base64 strings that look like secrets to naive scanners — they're `"integrity": "sha512-…"` cryptographic content hashes that npm publishes publicly for supply-chain verification. **NOT secrets.** Every public Node.js project has them. Allowlist `package-lock.json` from entropy-based rules.

#### 11.2.8 Internal runbook / infrastructure docs
Even when they don't contain explicit secrets, these reveal operational surface:
- Disaster-recovery playbooks (EC2 restore procedures, IP addresses, PEM keys)
- Compliance docs with full-stack architecture (risk register, asset inventory, incident response)
- Support runbooks (customer-device Tailscale access paths)
- Golden-image build SOPs (reveals what's stripped before ship = what an attacker finds on a stolen image)
- Pen-testing instructions (literal attack surface map)
- Network topology diagrams (full infrastructure reveal)
- Session-specific internal notes (COMMIT-PLAN, TOMORROW, DOC-BACKLOG)
- Sudoers hardening proposals (reveals sudo attack surface evolution)

**Treat as untrack-unless-clearly-public.** See `.gitignore` for the canonical list of internal-only doc paths.

#### 11.2.9 Stale / vestigial files
Tracked content that is no longer current but never got cleaned up:
- `*.bak`, `*.old`, `*.orig`, `*~` backup files
- Binary archives duplicating already-tracked content (a zip of docs that exist unzipped)
- Dev-only smoke test apps (`gp-app-test.py`-style) never meant to ship
- Outdated point-in-time audit snapshots (obsolete by newer audits)
- Redundant legacy prototypes (`public/da-app.js`, `pwa-app.js` from an earlier iteration)

**Delete or untrack.** Anything an honest reviewer would flag as "why is this still here?" deserves investigation.

#### 11.2.10 Filename content
File names themselves can leak. A filename of `ISSUE-14273-customer-data-fix.md` tells the world there was an incident. Scan for names that mention:
- Internal ticket/issue numbers
- Customer identifiers
- "DRAFT", "wip", "notes-for-self"
- Developer first names ("thomas-rules.md", "alice-fixes.md")

#### 11.2.11 Detection-rule hygiene (don't embed what you're detecting)

A `.gitleaks.toml` rule that lists known-leaked tokens as literal regex alternations *publishes those tokens*. The file becomes a secrets list, and every future edit of that file preserves those tokens in the commit diff forever. Even after the tokens are revoked, this creates:

- GitHub push-protection blocks on the public flip (server-side scanner still matches the `ghp_*` / `github_pat_*` shape in the rule itself)
- `git log -p` output that exposes the revoked values to anyone who clones
- A false sense that the rule is defending something, when the shape-based rule already covers all future leaks of the same class

**Rule:** detection rules catch by *shape*, not by *literal value*. If you need a defense-in-depth tripwire against specific known-leaked values, use SHA-256 fingerprints in a custom checker — not the literal string.

```
# DON'T — publishes the token values themselves
regex = '''(ghp_ZZqa2EX...|Pa6wXsV4...)'''

# DO — catches any new token of the same shape
regex = '''(?i)(bridge_token|fleet_token)\s*[:=]\s*["']([A-Za-z0-9+/_-]{40,60})["']'''
```

#### 11.2.12 History-diff scan (gitleaks misses diff-only bleed)

Gitleaks checks each commit's *state at that commit*. A string that was added in commit A and removed in commit B lives in the diffs of both — but at the HEAD of the rewritten rule-file it's gone, so gitleaks reports clean. GitHub's server-side scanner indexes diffs.

```
# Run BEFORE every force-push and BEFORE every visibility flip
git log --all -p 2>/dev/null | grep -cE "<string that must never appear>"
# Expected: 0. Any non-zero = filter-repo rewrite required.
```

If non-zero: write the sensitive values into a gitignored file (never argv, never env), then `git filter-repo --replace-text <file>`, force-push, shred the file. See §5 for the rotation pattern and §11.4 for post-rewrite cleanup.

#### 11.2.13 Pre-commit hook live-fire test

The hook is only defense if it actually blocks. Test it at least once per major config change:

```
# Should BLOCK (PAT)
echo "ghp_$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 36)" > leaktest.txt
git add leaktest.txt && git commit -m test   # must fail with "BLOCKED"
git reset HEAD leaktest.txt && rm leaktest.txt

# Should BLOCK (bare PEM header — gitleaks default rule requires body)
echo "-----BEGIN OPENSSH PRIVATE KEY-----" > keytest.txt
git add keytest.txt && git commit -m test   # must fail with "BLOCKED"
git reset HEAD keytest.txt && rm keytest.txt
```

If either test commits successfully, your hook has a gap. The second test failed on 2026-04-23; fix was adding a `private-key-header-only` rule to `.gitleaks.toml`.

### 11.3 Commit structure for a public-prep pass

One batched pre-public commit is harder to review and harder to bisect. Use discrete commits per concern:

1. `security:` — secrets scrub + `.gitignore` expansion (ship-critical)
2. `cleanup:` — stale/vestigial file removal
3. `privacy:` — PII redaction (name → operator, personal emails → company aliases)
4. `docs:` — brand updates, broken-ref fixes, public-facing polish (SECURITY.md reporting path, CONTRIBUTING.md license claim match)
5. `security:` — defensive stack install (pre-commit hook, `.gitleaks.toml`, `.gitattributes`)

Each commit is independently reviewable and revertible.

### 11.4 Post-flip observability

After flipping public:
1. **Wait 10 minutes** for GitHub's secret-scan sweep of the newly-public repo.
2. Check email for GitHub Security alerts. If anything triggers, rotate that credential on the issuing service + remove/rewrite the commit + re-audit.
3. If using GitHub push protection + branch protection, confirm both are now active on `main`.
4. Monitor the repo's Security tab for incoming vulnerability reports (per `SECURITY.md`).
5. Watch repo analytics for first-week clones and who's cloning — anomalous early traffic (e.g. thousands of clones in the first hour) can indicate automated scrapers testing the freshly-public history for leaked secrets.

### 11.5 Canonical sweep script

The inline commands in §11.2 belong in a runnable script for reproducibility. Recommended path forward: package them as `scripts/audit-pre-public.sh` and include in a future commit. Until then, copy the blocks directly from this SOP.

### 11.6 Rule origin

2026-04-23 Phantom OS public-flip prep. The session's audit surfaced ALL of the following in a repo that had just been "fully swept" by standard tooling:
- Committed bridge auth token in `compliance/alert-monitor.sh` (history + live)
- Three GitHub PATs leaked via tokenized git remote URLs
- Full legal operator name + Law Enforcement Contact role in a compliance doc
- Factory-reset default WiFi passphrase `ghostport` hardcoded
- SSH PEM filename + EC2 public IP in a disaster-recovery runbook
- 7 internal ops runbooks revealing attack surface
- EXIF metadata (benign but unnecessary) on public logo
- 1 development smoke-test app and 1 redundant doc-zip binary
- npm integrity hashes flagged as secrets by naive scans (false positive to document)
- Symlink-to-system-file flagged by grep as binary leak (false positive to document)

**Final-sweep discoveries (added 2026-04-23, after the initial §11 playbook was written):**
- Hardcoded EC2 fleet relay public IPs in `public/topology.js` tooltip strings — infrastructure disclosure (→ §11.2.3)
- Revoked-but-literal token values embedded in `.gitleaks.toml` detection regex — the rule file itself was a secrets list (→ §11.2.11)
- Broken external URLs in `README.md` (`<apex>/docs` 404'd, apex was down) and `SECURITY.md` (broken `/report-vulnerability` path)
- `package.json` license mismatch (`ISC` claim vs `Elastic 2.0` LICENSE file) and missing `"private": true` (accidental `npm publish` risk for both root + desktop sub-app)
- Pre-commit hook gap: gitleaks default private-key rule didn't catch a bare `-----BEGIN OPENSSH PRIVATE KEY-----` line without a body (→ §11.2.13, fixed with `private-key-header-only` custom rule)
- `git log --all -p` still exposed the four revoked tokens in diff context even though gitleaks HEAD scan was clean — required a second `git filter-repo --replace-text` pass (→ §11.2.12)

Each of these was caught by a different sweep angle. **One sweep is not enough.** This playbook codifies the angles so future public-flips don't repeat the multi-round discovery loop of 2026-04-23.

---

## 12. Related documents

- `feedback_no_tokens_in_urls.md` (memory) — the specific 2026-04-23 incident that triggered this SOP
- `feedback_no_passwords.md` (memory) — earlier rule about passwords in memory
- `feedback_gauntlet_paste_commands.md` (memory) — paste-block validation (applies to secret-bearing installers too)
- `OPERATOR-SOP.md` §8 — rules learned the hard way (includes related sudo/service incidents)
- `GOLDEN-IMAGE-SOP.md` §7 — strip verification (checks for leftover secrets before SD ship)
- `/tmp/rotate-gh-pat.sh` — canonical PAT rotation script (install to `~/.local/bin/gp-rotate-gh-pat` for permanence)
- `/tmp/rotate-bridge.sh` + `/tmp/rotate-bridge.py` — bridge-token rotation (same pattern, different target file)
