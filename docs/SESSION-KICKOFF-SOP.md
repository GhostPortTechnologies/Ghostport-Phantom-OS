# Phantom OS — Session Kickoff SOP

**Rule: Every session starts with the same three-minute ritual. No exceptions.**

Skipping kickoff is how AIs end up repeating incidents already in memory, or working on a system that's already in a broken state.

---

## 1. The Boot Ritual

Run these in order at the start of every session:

### Step 1 — Read the three SOPs
```
/opt/phantom/docs/OPERATOR-SOP.md
/opt/phantom/docs/ai-dev-guide.md
/opt/phantom/docs/ICON-POLISH-SOP.md
```

Plus the newer ones when relevant to the task:
```
/opt/phantom/docs/UI-LAYERS-SOP.md
/opt/phantom/docs/INVENTORY-BEFORE-BUILD-SOP.md
/opt/phantom/docs/SCOPE-DISCIPLINE-SOP.md
/opt/phantom/docs/ASK-NOT-ASSUME-SOP.md
```

Plus the architecture invariants — required reading before touching firewall, tunnel, AP, or boot paths:
```
/opt/phantom/docs/ARCHITECTURE-INVARIANTS.md
```
One page of load-bearing rules with the "why" and the consequence of violating each. Skim once per session — costs 60 seconds, saves hours.

Read them with the Read tool. Do not summarize from memory.

### Step 2 — Check Chamber
```bash
curl -s http://localhost:4242/api/messages | python3 -m json.tool | tail -80
```
Look for:
- Claims from other AIs on tasks you're about to touch
- Directives posted overnight
- Incident reports or grade postings
- Any message addressed to you or your role

### Step 3 — Preflight check
```bash
gp-preflight
```
Expected outputs:
- All green except known non-blockers
- A red on `nftables-loaded` with green `mode-consistency` is usually a preflight quirk, not a real problem — but flag it
- Any red other than that → investigate before making changes

### Step 4 — Check process baseline
```bash
ps aux | sort -k3 -rn | head -5        # top CPU
ps aux | sort -k4 -rn | head -5        # top memory
```
Note the baseline. If you spawn subprocesses later, compare.

### Step 5 — Check recent git state
```bash
cd /opt/phantom && git log --oneline -10 && git status --short
```
Recent commits tell you what changed yesterday; `git status` tells you what's uncommitted and in flight.

---

## 2. Read-Before-Act on User's First Message

After the ritual, before touching files:

1. Re-read the user's message
2. Identify: is this a new request, a continuation, or a correction of a previous session?
3. Check memory for matching session logs:
   ```
   ~/.claude/projects/-home-ghostport-admin/memory/session_*.md
   ```
4. If continuing, catch up on the last session file for the specific topic

---

## 3. Announce Before You Act

State your plan in ONE sentence before your first tool call. This anchors the user and catches misalignment early. See CLAUDE.md "Text output" section.

Good:
> "Reading gp-aetherbox.py to find why uploads are disabled."

Bad:
> [silent tool calls]
> "Here's what I found after 8 tool calls..."

---

## 4. Skipping Kickoff — The Consequences

Historical incidents where kickoff was skipped:

| Date | What went wrong |
|------|-----------------|
| 2026-03-24 | CSP migration delegated to sub-agents without reading prior incident notes |
| 2026-04-02 | Mixed DoubleHop/ZeroTrust state left by an autonomous session that didn't check mode state |
| 2026-04-16 | Zombie processes from TUI scripts without `--help` guards — fix was already documented |

Every one of these was preventable with the ritual.

---

## 5. Mid-Session Re-Check

After ~30 minutes or anything risky (mode switch, service restart, nftables change), re-run:

```bash
gp-preflight
ps aux | sort -k3 -rn | head -3
curl -s http://localhost:4242/api/messages | python3 -m json.tool | tail -20
```

The goal is catching drift early. Preflight going red mid-session means your last change broke something — revert or investigate immediately, do not continue piling changes.

---

## 6. End-of-Session Ritual

Symmetric to kickoff:

1. Senior review (if squad is active)
2. Post status to Chamber
3. Update memory — new `session_YYYY_MM_DD.md` if significant work was done
4. Run `gp-preflight` one last time
5. Bridge update to EC2 if the other side needs to know

---

## 7. The One-Minute Version

If you're resuming a task within the same day and nothing has changed on the system:

1. Re-read the memory index: `cat ~/.claude/projects/-home-ghostport-admin/memory/MEMORY.md | head -50`
2. `gp-preflight`
3. Chamber `tail -20`

That's enough for short re-entries. Full ritual is for fresh days / new missions.
