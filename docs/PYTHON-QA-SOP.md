# Python Quality + Security Gate SOP

> **TL;DR for AI agents:**
> Before declaring any Python change complete, run `gp-qa <file>` and confirm it exits 0.
> For auth/crypto/network/subprocess/secret/dependency changes, run `gp-qa --security <file>`.
> For release cuts or weekly sweeps, run `gp-qa --paranoid` (adds the redundant-coverage tools).
> **Do NOT run `pyupgrade` unguarded** — it rewrites files in place. The runner handles this safely; invoking it manually is a trap (see §7).

---

## 1. Scope

| Applies to | Does not apply to |
|------------|-------------------|
| `*.py` under `/opt/phantom/desktop/` | Bash (`bash -n`) |
| `*.py` under `/opt/phantom/` (utilities) | Nftables (`nft -c -f`) |
| Any new `.py` created in a session | Node / JS / HTML |

---

## 2. Tool Matrix — Core 12 (default gate)

These run on every `gp-qa` invocation. Each tool is in the suite because it catches a failure class the others miss. No redundancy.

### 2.1 Quality (7)

| # | Tool | Catches | Modifies? | Speed |
|---|------|---------|-----------|-------|
| 1 | **ruff** | Style, imports, py-upgrade suggestions, 700+ rules (replaces flake8+isort+pyupgrade) | Only with `--fix` | Fast |
| 2 | **black** | Formatting (single canonical formatter) | Only without `--check` | Fast |
| 3 | **pylint** | Real bugs (errors-only mode): undefined vars, bad calls, missing attrs | No | Slow |
| 4 | **mypy** | Type errors | No | Slow |
| 5 | **radon** | Cyclomatic complexity + maintainability index | No | Fast |
| 6 | **vulture** | Dead code (unused imports, variables, functions) | No | Fast |
| 7 | **perflint** | Performance anti-patterns (list vs generator, etc.) | No | Medium |

### 2.2 Security (5)

| # | Tool | Catches | Modifies? | Speed |
|---|------|---------|-----------|-------|
| 8 | **bandit** | Python-specific SAST: shell=True, yaml.load, weak crypto | No | Fast |
| 9 | **semgrep** | Pattern-based SAST, covers Python + more | No | Medium |
| 10 | **pip-audit** | CVEs in installed Python deps (PyPI advisory DB) | No | Fast (online) |
| 11 | **trivy** ⚠️ | Filesystem: vulns + secrets + misconfig — **whole-tree scanner (§9.5)** | No | Medium (online first run) |
| 12 | **gitleaks** ⚠️ | Secrets in git history (regex + entropy) — **whole-tree scanner (§9.5)** | No | Fast solo, 100%+ CPU |

### 2.3 What's deliberately NOT in the core

- `flake8`, `isort`, `pyupgrade` — ruff covers all three; running them is duplicate work.
- `safety` — duplicates pip-audit; its free `check` command is deprecated.
- `detect-secrets` — gitleaks + trufflehog do the same job better.
- `trufflehog` — optional verify layer for gitleaks; not needed on every run.
- `sonarcloud`, `snyk` — SaaS, require cloud accounts + CI; not a local-gate fit.

These 8 tools live in the **paranoid mode** (§6) for escalation.

---

## 3. Install

One-time setup:

```bash
# Shared QA venv (owns all pip tools)
python3 -m venv /home/ghostport-admin/.qa-venv
VENV=/home/ghostport-admin/.qa-venv/bin
$VENV/pip install --upgrade pip wheel
$VENV/pip install ruff black pylint mypy radon vulture perflint \
                  bandit semgrep pip-audit

# System binary tools
# trivy
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | \
    sudo gpg --dearmor -o /usr/share/keyrings/trivy.gpg
echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" | \
    sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt update && sudo apt install -y trivy

# gitleaks (arm64)
TMPDIR=$(mktemp -d) && cd "$TMPDIR"
curl -sSLo gitleaks.tar.gz \
  "https://github.com/gitleaks/gitleaks/releases/download/v8.28.0/gitleaks_8.28.0_linux_arm64.tar.gz"
tar xzf gitleaks.tar.gz && sudo mv gitleaks /usr/local/bin/
```

Paranoid-mode extras (§6):

```bash
$VENV/pip install flake8 isort pyupgrade safety detect-secrets
# trufflehog
curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | \
    sudo sh -s -- -b /usr/local/bin
# sonar-scanner + snyk are SaaS; install only if a cloud account exists
```

---

## 4. Canonical Commands

**Do not invoke tools ad-hoc — always run `gp-qa`.** The runner enforces the right flags (especially `pyupgrade --check`) and centralizes output.

```bash
gp-qa                    # core gate on all *.py in CWD
gp-qa file.py            # core gate on one file
gp-qa --security         # core + 5 security tools
gp-qa --security-only    # security tools only
gp-qa --paranoid         # core + security + 8 redundant-coverage tools
                         # ⚠️  never run --security or --paranoid in parallel —
                         #     see §9.5 for squad concurrency rules
gp-qa --summary          # aggregate counts only
gp-qa --full             # same as --security
gp-qa --help             # flags
```

Exit code: **0 = pass, 1 = at least one blocker**. Safe to use in scripts / pre-commit.

Reports written to `/tmp/qa/` (quality) and `/tmp/qa/security/` (security).

---

## 5. Pass / Fail Rules

### 5.1 Blockers (must be 0 before declaring done)

| Tool | Blocker condition |
|------|-------------------|
| pylint | Any error (errors-only mode, so all output blocks) |
| mypy | Any error |
| vulture | Any finding at ≥90% confidence |
| bandit | Any `Severity: High` |
| semgrep | Any `Blocking` finding |
| pip-audit | Any CVE with CVSS ≥ 7.0 |
| trivy | Any `HIGH` or `CRITICAL` |
| gitleaks | Any leak not in `.gitleaksignore` |

### 5.2 Style debt (may ship, but do not ADD)

ruff, black, isort, flake8, perflint non-blocking findings may exist in the codebase. Rule: your change must not INCREASE these counts. Track baseline per §8.

### 5.3 Exemptions

When a finding is a confirmed false positive, add an inline suppression comment with a one-line justification. Format:

```python
subprocess.run(cmd, shell=True)  # nosec B602 — cmd is hardcoded DESKTOP_APPS entry
req = urllib.request.Request(url)  # nosemgrep: dynamic-urllib — url is localhost:4200 API
```

Exemption files (persistent across runs):

- `.gitleaksignore` — secrets that are false positives or test fixtures
- `.semgrepignore` — paths/rules to skip globally
- `.secrets.baseline` — detect-secrets accepted findings
- `# nosec` / `# nosemgrep` — bandit/semgrep inline suppressions

### 5.4 Known-noisy patterns on this project

Pre-approved — already exempt or OK to exempt inline:

| Rule | Where | Why it's OK |
|------|-------|-------------|
| flake8 E402 | Most desktop apps | `sys.path.insert` before `gp_app_base` import |
| bandit B404 / B603 / B607 | Any file using subprocess | GTK apps shell out to `gp-*` scripts |
| pylint E0203 | `_css_provider` hasattr check | Intentional first-run guard |
| semgrep dynamic-urllib | `gp-widgets.py:754,770`, `gp-desktop-icons.py:221` | URL is hardcoded localhost:4200 API |
| trufflehog entropy false-hits | `#39ff8f` theme green | Cosmetic color constant |

---

## 6. Paranoid Mode

Purpose: defense in depth. Catches things a single tool missed because of rule-set differences or DB coverage gaps. Trades runtime for verification.

Adds these 8:

| # | Tool | Overlaps with | Adds value when |
|---|------|---------------|-----------------|
| 13 | flake8 | ruff | Rule set differences at the edges |
| 14 | isort | ruff | Stricter import-ordering enforcement |
| 15 | pyupgrade | ruff UP rules | Proposes upgrades ruff doesn't autofix |
| 16 | safety | pip-audit | Second-opinion CVE DB |
| 17 | detect-secrets | gitleaks | Entropy-based catches that regex misses |
| 18 | trufflehog | gitleaks | Verified-secret pass (live key testing) |
| 19 | sonarcloud | bandit+semgrep | Cross-repo trends, requires SaaS setup |
| 20 | snyk | pip-audit+trivy | License + container, requires SaaS setup |

**When to run:**

- Release cuts
- Weekly scheduled sweep
- Touching crypto, auth, or secret handling
- After an incident posted in Chamber

```bash
gp-qa --paranoid          # all 20 tools
gp-qa --paranoid --full   # same + verbose output
```

SaaS tools (sonarcloud, snyk) are installed but skipped unless configured:

```bash
snyk auth                       # one-time browser login
# SonarCloud: create project at sonarcloud.io, set SONAR_TOKEN env var
```

---

## 7. The pyupgrade Trap (required reading)

`pyupgrade` has **no `--check` mode**. It rewrites files in place by default. `gp-qa` handles this safely by:

1. Copying each target to `/tmp/qa/<name>.preupg` before invocation
2. Running pyupgrade
3. Comparing each file byte-for-byte against the copy
4. Reverting any modifications (logs `reverted proposed changes to: <file>`)
5. The report shows what pyupgrade WOULD do, without having done it

**Do NOT run `pyupgrade` manually on this codebase.** If you need its suggestions, read `/tmp/qa/pyupgrade.log` after `gp-qa`.

If you must run it manually:

```bash
git add -f file.py                    # stage first
pyupgrade --py311-plus file.py        # run
git diff --staged file.py             # review
git restore --staged --worktree file.py  # revert if bad
```

Never run pyupgrade on untracked files.

---

## 8. Reporting Format (what AI agents must include in task summaries)

```
QA Gate: PASS / FAIL
Core quality:   ruff=N  black=N  pylint=N  mypy=N  radon=GRADE  vulture=N  perflint=N
Core security:  bandit=H/M/L  semgrep=N  pip-audit=N  trivy=H/C  gitleaks=N
Paranoid (if run): flake8=N  isort=N  pyupgrade=N  safety=N  detect-secrets=N  trufflehog=N

Blockers fixed:  <list or "none">
Exemptions:      <list or "none">
Style debt:      <delta vs baseline>
```

Terse, numeric, verifiable. No prose explanations of what the tools do — the reader already knows (or reads this SOP).

---

## 9. Workflow by Change Type

| Change | Gate to run | Can ship if |
|--------|-------------|-------------|
| Small edit (one function) | `gp-qa <file>` | Blockers == 0 AND delta style-debt ≤ 0 |
| New script | `gp-qa <file>` | Blockers == 0 AND no new F401/F841 |
| Auth / crypto / network | `gp-qa --security <file>` | All blocker tables clean |
| Dependency add or upgrade | `gp-qa --security` (pip-audit + trivy mandatory) | No new CVE ≥ 7.0 |
| Release cut | `gp-qa --paranoid` | Clean across all 20 tools |
| Weekly sweep | `gp-qa --paranoid` on whole tree | Log deltas to Chamber |

---

## 9.5 Squad Concurrency Rules

Learned the hard way on 2026-04-17: four Claudes running `gp-qa --security` in parallel flattened the Pi (load avg 21.76 on 4 cores, gitleaks alone burning 113% CPU per instance).

**Why:** two tools in the security suite are **whole-tree scanners**, not file scanners:

| Tool | Scan target | Cost per instance |
|------|-------------|-------------------|
| **gitleaks** | `--source .` walks the entire working tree recursively | ~100–115% CPU |
| **trivy fs** | `fs .` same — full recursive filesystem scan | ~60–90% CPU |

A single instance is fine. N instances is ~N× the work because each walks the same tree independently.

### MANDATORY Claim-Before-Security Protocol

**Any Claude about to run `gp-qa --security` or `gp-qa --paranoid` MUST:**

1. **Check Chamber** for an active `CLAIMING: security sweep` message:
   ```bash
   curl -s http://localhost:4242/api/messages | python3 -m json.tool | \
       grep -A2 "security sweep" | tail -20
   ```
2. **If the claim is held by someone else** → do not run `--security`. Run `gp-qa` (quality only) instead — that's parallel-safe.
3. **If no active claim** → post a claim before running:
   ```bash
   curl -s -X POST http://localhost:4242/api/login \
       -H "Content-Type: application/json" \
       -d '{"username":"<your-name>","role":"ai"}'
   curl -s -X POST http://localhost:4242/api/messages \
       -H "Content-Type: application/json" \
       -d '{"username":"<your-name>","text":"CLAIMING: security sweep — <your-name>"}'
   ```
4. **Release the claim when done:**
   ```bash
   curl -s -X POST http://localhost:4242/api/messages \
       -H "Content-Type: application/json" \
       -d '{"username":"<your-name>","text":"RELEASE: security sweep"}'
   ```
5. **Hard stop:** if `uptime` 1-min load > 4× core count (>16 on a 4-core Pi), **do NOT start a new security run** regardless of claim state. Post status to Chamber, let the squad drain.

**Rules when a squad is active (≥2 Claudes working):**

1. **Default mode = `gp-qa` (quality only).** This stays CPU-safe under parallel runs because the quality tools scan only the files passed as arguments.
2. **Only ONE security sweeper at a time** — enforced via the claim protocol above. Violations flatten the Pi (proven 2026-04-17: load hit 21.76, gitleaks ×2 burned 200%+ combined CPU).
3. **Don't run `--paranoid` during active squad work.** Reserve it for release cuts when the squad is idle.
4. **If the system slows down mid-session**, check `ps aux --sort=-pcpu | head -5` — gitleaks / trivy / osemgrep / semgrep-core pinned at 100%+ are the usual suspects.

**Emergency unstick:**
```bash
pkill -9 gitleaks trivy osemgrep
# wait 30s — load avg drops quickly after kill
uptime
```

Squad work (`pylint`, `black`, `mypy` on file lists) is safe to let finish — it's scoped to the passed files and completes in seconds to a couple minutes.

---

## 10. Anti-Patterns

- **Skipping the gate** because the change is "small" → regressions compound silently.
- **Disabling rules globally** to make the gate green → use inline exemptions with justification, never global disables.
- **Running tools individually** outside `gp-qa` → inconsistent flags across agents, untrustworthy reports.
- **Fixing style debt opportunistically** in a bug-fix commit → see SCOPE-DISCIPLINE-SOP. Report counts, don't refactor.
- **Ignoring `pyupgrade` output** → some proposals catch real issues (e.g., `super(Foo, self)` → `super()` clarifies inheritance).
- **Over-exempting** → if you add more than 3 exemptions in one change, stop and reconsider the design.

---

## 11. Historical Baseline (2026-04-17)

First full gate run on `/opt/phantom/desktop/` (25 files, 17,232 LOC).

**Core quality:**

| Tool | Count | Grade |
|------|-------|-------|
| pylint errors | 5 | Blocker: fix 1 real (`draw_fn not callable`), 4 are `hasattr` false positives |
| mypy errors | 0 | Clean |
| vulture ≥80% | 3 | 2 unused imports, 1 unused var |
| radon avg complexity | A (4.06) | Healthy |
| radon MI | 5 files grade C (0.00–7.27) | bulkhead, crowsnest, widgets, desktop-icons, dragnet (all large; acceptable) |
| ruff | 412 auto-fixable + 118 unsafe | Style debt, mostly imports + line length |
| black | 25 files want reformat | Style debt |
| perflint | 586 | Mostly comprehension hints, non-blocking |

**Core security:**

| Tool | Findings |
|------|----------|
| bandit | 1 HIGH — `gp-desktop-icons.py:445` subprocess shell=True (fix pending — shlex.split) |
| semgrep | 6 blocking — 1 same as bandit, 5 dynamic-urllib false positives (exempted) |
| pip-audit | 3 CVEs in QA-venv setuptools 66.1.1 (fix: upgrade setuptools) |
| trivy | 0 HIGH/CRITICAL |
| gitleaks | 0 |

**Paranoid:**

| Tool | Findings |
|------|----------|
| trufflehog | 0 verified / 0 unverified |
| detect-secrets | 0 |
| safety | 1 advisory |
| isort | 75 import-order diffs |
| flake8 | 370 (167 E402 expected) |
| sonarcloud | SaaS SKIP |
| snyk | SaaS SKIP |

**Regression detection rule:** any future change that INCREASES the error-class counts (pylint errors, mypy errors, bandit HIGH, semgrep blocking, new CVEs, gitleaks matches, F401/F841) counts as a regression and blocks the gate.
