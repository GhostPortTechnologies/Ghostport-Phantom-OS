# Phantom OS — Scope Discipline SOP

**Rule: Fix the bug the user reported. Do not expand scope without a "yes".**

This is the most common failure mode in AI sessions on this project. User reports problem X; AI fixes X plus adds features Y, Z, and a tutorial — and the user now has to review four changes instead of one.

---

## 1. The One-Problem Rule

When the user reports a problem:

1. **Diagnose the reported problem only**
2. **Propose the minimum fix**
3. **Ship ONLY that fix**
4. If you notice adjacent issues while working — list them, don't fix them silently

Example from today (what NOT to do):

> User: "Aether Box won't let me upload files"
>
> ❌ AI: Fixed upload button + added Export button + added Shred checkbox + added tutorial + added Help button + added first-run modal.
>
> ✅ AI: The button is disabled because the vault isn't initialized yet. Click UNLOCK → set password, then upload works. I noticed there's no Export, no tutorial, no Shred option — want me to add any of those separately?

---

## 2. When Scope Expansion IS Warranted

Only these cases justify going beyond the reported problem:

| Case | Action |
|------|--------|
| The root cause *is* a missing feature (not a bug) | Propose the feature, wait for "yes" |
| The "fix" creates a new bug elsewhere | Fix both, call out the cascade in your response |
| Security fix requires a paired change (e.g., CSRF + cookie flag) | Ship together, explain why paired |
| **Shared-bug sweep: same wrong path / wrong API / stale constant in many files** | Grep the whole tree, fix every call site in one pass. See §2.1. |
| User explicitly says "and while you're in there…" | Do only what they asked |

Otherwise: propose, don't implement.

### 2.1 The Shared-Bug Sweep (ADDED 2026-04-21)

Scope-discipline stops you from adding features. It does **not** stop you from fixing the same bug in every place it's copy-pasted. If you discover that a hardcoded filesystem path, API endpoint, env var name, or magic constant is wrong, the fix is not "patch the one the user saw" — it's *grep the tree and patch them all*.

Without the sweep, the user reports it app-by-app as they open each UI. Each report costs a round-trip. Fix once, sweep always.

**Required sweep targets** when you find a wrong path/name/constant:
```bash
grep -rln "<token>" \
    /opt/phantom/ \
    ~/.local/bin/ \
    /usr/local/bin/gp-* \
    2>/dev/null | grep -v "__pycache__\|\.bak\|\.pyc"
```

**Tell-tale signs you're in a shared-bug situation** (not a feature creep):
- The same literal string appears in 3+ files.
- It's a path owned by a daemon that isn't GhostPort (Pi-hole, hostapd, systemd).
- Earlier sessions have moved or renamed it (git blame shows churn).
- The failing call is a silent read that returns empty instead of throwing.

When sweeping, still respect §1 for *everything else*: don't rename variables you noticed, don't reformat, don't add features. Just fix the one class of wrongness across all its occurrences. Historical case: 2026-04-21, `dnsmasq.leases` → `dhcp.leases` migration needed across 8 files (server, 3 GTK apps, 5 TUI scripts). One user-reported symptom, system-wide fix.

See `ai-dev-guide.md` §5 "External Daemon State — Canonical Helpers" for the single-helper pattern that keeps these sweeps from ever being needed again.

---

## 3. Proposals Beat Implementations

When you see an adjacent improvement, respond in this shape:

```
Fixed: <the one thing>
Also noticed: <list 1-3 items you did NOT change>
Want me to address any of those?
```

The user then steers. This is the ONLY way to stay aligned in long sessions.

---

## 4. Common Scope-Creep Traps

### 4.1 "Add one more button" compounding
Each small addition looks harmless. After five of them, the app has changed architecturally and the user didn't agree to that.

### 4.2 Tutorials / first-run modals
Never add these unless asked. They're user-visible and opinionated.

### 4.3 Comments explaining your fix
Don't. The commit message is the place for rationale. See CLAUDE.md — "Default to writing no comments."

### 4.4 "While I was in there, I renamed…"
Renaming variables, reformatting blocks, reshuffling imports — all introduce noise in diffs and review burden. Don't touch what you're not fixing.

### 4.5 Feature flags for your uncertainty
If you're not sure the user wants X, don't add X behind a flag. Ask.

### 4.6 Adding dependencies
A new Python package, a new apt install — never silently. Always mention it as a separate line item.

---

## 5. The "Is This In Scope?" Test

Before making an edit, answer three questions:

1. Did the user name this thing, or describe it in their last message?
2. Is it the smallest change that resolves what they named?
3. If I stop after this change, is the reported problem fixed?

If any answer is no, you're out of scope. Stop and propose.

---

## 6. Bundling Allowed Exceptions

These are small enough to bundle without re-asking:

- Typo in adjacent code you touched anyway
- Syntax error you introduced (fix before reporting)
- Import you need for your fix
- Adding a `--help` guard when touching a TUI script (per project policy)

Everything else: ask.

---

## 7. End-of-Response Format

Keep the pattern:

```
What I changed: <one line>
What I didn't: <related things you noticed but left alone>
Next: <what the user should verify or decide>
```

Terse. The user reads the diff for details.
