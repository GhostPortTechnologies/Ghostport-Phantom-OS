# Phantom OS — Ask Not Assume SOP

**Rule: When there are two reasonable implementations, ask. Don't pick.**

The hidden cost of guessing right 80% of the time is the 20% where you have to rip out your choice and the user's trust.

---

## 1. When to Use AskUserQuestion

Use the tool any time you face a fork where:

- Two implementations are both defensible
- The "right" answer depends on taste, workflow, or a constraint you don't know
- Reversal cost > 10 minutes of work
- The user has expressed opinions on similar trade-offs before (ask to confirm, don't assume)

Common forks on this project:

| Fork | Typical Options |
|------|-----------------|
| New feature placement | Desktop app / Widget / TUI / Menu item |
| UI style | Fuzzel popup / GTK window / Terminal |
| Data lifetime | Permanent (config) / Session-only (/tmp) / Memory-only |
| Default state | Enabled on boot / Off by default |
| Error surface | Silent log / Status bar / Modal / Notification |
| Scope of change | This app only / Paired with related app / System-wide |

---

## 2. When NOT to Ask

Asking on every keystroke is worse than guessing. Do not ask when:

- The user already answered the question upstream in the same message
- One option is objectively wrong (insecure, breaks a documented rule)
- The decision is reversible in <1 minute (typo, variable rename)
- Project convention already dictates the answer (see CLAUDE.md, SOPs)

Example:

> ❌ "Should I use spaces or tabs?" (convention is in the file)
> ✅ "Should the Theme Picker be a desktop icon or a widget?" (real trade-off)

---

## 3. How to Ask Well

The AskUserQuestion tool gives 2–4 options. Make them:

- **Distinct** — if two options feel similar, collapse them
- **Labeled concretely** — "Desktop icon" not "Option A"
- **Short descriptions** — one sentence each, state the trade-off
- **Recommendation marked** — put your pick first, append "(Recommended)" to the label

Example:

```
Question: "Where should the Theme Picker live?"
Options:
  1. Desktop icon (Recommended) — Standalone app window, own icon on grid, ~60 LOC.
  2. Widget Library entry only — No new code; user enables via gallery card.
  3. Right-click menu popup — Fuzzel-based, no desktop real estate used.
```

---

## 4. Anti-Patterns

### 4.1 Asking after building
"I built X. Did you want Y?" — too late, now you own the rework. Ask BEFORE building.

### 4.2 Asking yes/no on a multi-way decision
"Should I add a button?" collapses real choices into a gate. Offer the alternatives.

### 4.3 Asking about a decision the user already made
Re-read their last 2-3 messages before asking. If the answer is already there, act.

### 4.4 Silent picking with "let me know if you want it different"
This shifts rework onto the user. They asked you to decide BY asking. Don't bounce it back while also implementing.

---

## 5. The 30-Second Decision Framework

Before you type the edit, run this:

1. Is there one obvious answer that respects project conventions?
2. If not, can I list 2-4 concrete alternatives in under a minute?
3. If yes to #2, ask.
4. If no to both, DO minimal research first (grep, read), then return to step 1.

---

## 6. Tracking Previous Decisions

If the user has chosen between similar forks before, their choice is a signal — but memory decays:

- Check `~/.claude/projects/-home-ghostport-admin/memory/feedback_*.md`
- Confirm the precedent still applies ("earlier you preferred X — same here?")
- Don't rigidly apply old choices if the context differs

---

## 7. After the Answer

Once the user picks:

- Build only what they picked — scope discipline applies (see SCOPE-DISCIPLINE-SOP)
- Don't second-guess their choice mid-build
- If implementation reveals the choice was wrong, stop and re-surface — don't silently pivot
