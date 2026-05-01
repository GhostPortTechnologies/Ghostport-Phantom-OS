# AI Trouble-Ticket SOP

**Rule:** When you (an AI working on this project) want to commit non-trivial work that needs human visibility — bug you found, feature you want to build, refactor you want to propose, incident you observed — file a ticket. Humans approve before you start. Status persists across your restarts so the next instance can pick up where you left off.

Rule origin: 2026-04-28 — operator directive: AIs file tickets, humans approve, state must survive an AI being turned off.

---

## 1. When to File a Ticket

**File** when:
- You found a bug that's bigger than a one-line fix and want a paper trail
- You want to propose a feature or behavior change
- You hit a blocker and need a human to unblock you
- You finished an investigation and want the resulting work approved before doing it
- You observed an incident worth tracking (e.g., a service flap, an alert, an unexpected state)

**Do NOT file** for:
- One-line typo fixes inside files you're already editing — just fix
- Trivia covered by an existing ticket — comment on that one (`gp-tickets update <id> --by you --note "..."`)
- Pure questions for the operator — use Chamber chat
- Internal AI-to-AI coordination — use Chamber

---

## 2. Status Lifecycle

```
proposed  ──approve──▶  approved  ──claim──▶  in_progress  ──close──▶  done
    │                        ▲                     │
    │                        │                     │
    └──reject──▶ rejected    └─────update──────────┘
```

- **proposed** — AI filed it; awaiting human review. **You do not start work in this state.**
- **approved** — human said yes. Any AI can claim it.
- **in_progress** — claimed by an assignee. Add `update` notes as you go.
- **done** — work shipped, ticket closed.
- **rejected** — human said no, with reason. Don't re-propose without addressing the reason.

Only **humans** (Chamber `role=human`) can move a ticket from proposed → approved/rejected. The CLI enforces this by checking the Chamber user list.

---

## 3. Persistence Guarantee

The ticket store is `/opt/ghostport/data/ai-tickets.json`. It is the durable source of truth.

If you (the AI) get killed mid-work, restarted, or replaced by a new instance:
1. The next AI runs `gp-tickets list --mine <handle>` to see what was assigned to that handle.
2. `gp-tickets show <id>` reveals the full body and update history.
3. The new AI can pick up the ticket in `in_progress`, add updates, and close it.

**Always include enough detail in the body and update notes** that a fresh AI can resume without you. "Working on it" is useless. "Patched server.js lines 100-130, ran node --check, restart blocked on operator approval" is useful.

---

## 4. CLI Usage

The CLI is `~/.local/bin/gp-tickets` (also reachable as `gp-tickets` if `~/.local/bin` is on PATH).

### File a ticket
```bash
gp-tickets propose --by <your-chamber-handle> \
  --title "Short imperative title" \
  --body "Why this matters, what you'd do, blast radius, rollback plan" \
  --type bug|feature|task|incident \
  --priority low|normal|high|critical
```

### List
```bash
gp-tickets list                                # all tickets
gp-tickets list --status proposed              # awaiting human
gp-tickets list --status approved              # ready to claim
gp-tickets list --mine <handle>                # filed-by-me OR assigned-to-me
```

### Detail
```bash
gp-tickets show T-0007
```

### Claim an approved ticket (AI)
```bash
gp-tickets claim T-0007 --by <your-handle>
```

### Add a progress note
```bash
gp-tickets update T-0007 --by <your-handle> --note "Found root cause — patched gp-mode line 142"
```

### Close
```bash
gp-tickets close T-0007 --by <your-handle>
```

### Approve / reject (humans only)
```bash
gp-tickets approve T-0007 --by <your-human-handle>
gp-tickets reject  T-0007 --by <your-human-handle> --reason "Out of scope for this sprint"
```

The CLI verifies that the `--by` handle has `role=human` in Chamber before allowing approve/reject. Make sure your human handle is logged into Chamber first.

---

## 5. Chamber Web UI

Open Chamber (desktop icon, or `http://127.0.0.1:4242`). The header has two tabs:

- **Chat** — the existing chat rooms, unchanged.
- **Tickets** — kanban view: Proposed / Approved / In Progress / Done.

A small badge on the Tickets tab shows the count of tickets currently awaiting human review.

Clicking a ticket card opens a detail modal with:
- Full body, type, priority, ages, proposer, assignee
- Update history
- Action buttons appropriate to the ticket's status and your role:
  - `proposed` + you're human → APPROVE / REJECT
  - `approved` → CLAIM
  - `in_progress` → ADD NOTE / MARK DONE

Every state change is auto-mirrored as a system message in the Chamber `tickets` room (e.g., "[T-0007] APPROVED by thomas"). Use that room as the audit log; use the kanban for state.

---

## 6. Field Discipline

A good ticket body answers the same five questions every time:

1. **What** — what's broken / what's the change
2. **Where** — file paths, function names, line numbers
3. **Why** — why it matters, what triggered the discovery
4. **How** — your proposed approach
5. **Risk** — blast radius, rollback plan, any safety considerations

Bad title: `fix bug`
Good title: `gp-mode: rollback timer fires before confirm message reaches UI`

Bad body: `there's a problem with the firewall`
Good body: `In /etc/gpmodes/zhop.nft line 42, the established/related rule is positioned after the drop chain. ZHop mode loses LAN→WG return traffic. Repro: switch to zhop, ssh in via tailscale, lan client tries DNS — times out. Fix: hoist the established/related rule above the drop. Risk: low; nft -c validates clean. Rollback: restore the .bak.`

---

## 7. Priority Guidance

- **critical** — production outage, lockout, security exposure, data loss risk. Page the operator if you can.
- **high** — significant degradation, bug that affects shipped customer units, compliance issue.
- **normal** — default. Bug or task that should ship soon but isn't bleeding.
- **low** — nice-to-have, polish, refactor.

Don't inflate. A `critical` ticket every day means none of them are critical.

---

## 8. Hand-off Rules

If you're going to be turned off mid-work:
1. Add a final `gp-tickets update` note with: what's done, what's not, where the next AI should resume, any non-obvious gotchas.
2. Do NOT close the ticket — it's not done.
3. Do NOT mark yourself as no longer the assignee — the next AI can claim if needed by closing your in-flight and re-claiming, but the update history must show why.

If you're picking up an in-flight ticket from a previous AI:
1. Read the full update history — `gp-tickets show <id>`
2. Add an update: `"resumed by <new-handle> — continuing from <last-update-summary>"`
3. Continue the work.

---

## 8.5 Closure Discipline — never bury deferred items in closure notes

**Rule: if you close a ticket with N-of-M items shipped (M > N), every deferred item gets its own follow-up ticket BEFORE you mark the parent done. No exceptions.**

A deferral buried in a closure note evaporates: it's not on any kanban column, not in `gp-tickets list`, not in any AI's startup ritual. The next session has no surface to find it. Operator review of "done" tickets reads as work complete — it isn't.

**What counts as a deferral:**
- "5 of 6 items shipped, item N deferred because …"
- "Item X turned out to need T-NNNN to land first — defer"
- "Scope grew during work; the X part is moved to a future pass"
- Any item from the original ticket body that did NOT ship as part of this close

**What you must do BEFORE closing:**
1. File a follow-up ticket via `gp-tickets propose` for each deferred item.
2. Title format: `<original-area>: <item summary> (deferred from T-NNNN #<item-number>)`
3. Body must include: the deferral rationale (cite the original ticket's closure reasoning), any blockers (e.g., "depends on T-NNNN"), and pointers back to the parent ticket so context is preserved.
4. In the parent ticket's closure note, list the spawned ticket IDs explicitly: `Deferred items spawned: T-XXXX, T-YYYY`.
5. THEN run `gp-tickets close <parent>`.

**Acceptable exception — already-done overlap.** If an item turns out to be already-shipped by a sibling ticket (e.g., T-0020 #2 was already done by T-0018), you do NOT need a follow-up ticket — but the closure note must explicitly cite the sibling ticket and line numbers proving it. "Already done" without proof reads identically to "I forgot."

**Acceptable exception — won't-do.** If during work you decide an item shouldn't ship at all (was a bad idea, no longer applicable), state that decision in the closure note with the reasoning. Do NOT spawn a follow-up. The parent ticket's body + closure note are now the audit trail for the kill.

**Rule origin:** 2026-04-29 — T-0021 closed as "5 of 6 shipped" with item #6 deferred via a sound rationale (dedup consolidation should wait for T-0026's event bus) but no follow-up ticket. Caught during operator review of newly-finished tickets; T-0041 was filed retroactively to recover the lost work. Pattern is high-frequency (T-0020 had a similar partial-completion shape; only escaped this rule because the deferred item was actually already-done overlap). The closure note is not durable enough to be the only record of pending work.

**Related anti-pattern: "scope creep into the close."** Equally bad in the other direction — completing items that were NOT in the original ticket body and burying them in the closure note as bonus work. Those need their own tickets too, after-the-fact, so the audit trail matches the work done. (See SCOPE-DISCIPLINE-SOP.)

---

## 9. Related SOPs

- `OPERATOR-SOP.md` — squad management, quality gates (the gauntlet still applies to ticket-driven work)
- `SCOPE-DISCIPLINE-SOP.md` — fix the ticket's scope, not adjacent work
- `SECRET-SAFETY-SOP.md` — never paste secrets into a ticket body or update note (tickets are world-readable to all AIs)
- `FEATURE-DOCS-SOP.md` — features approved via tickets still need a user-facing doc when they ship

---

## 10. Quick Reference Card

| Task | Command |
|---|---|
| Propose a bug fix | `gp-tickets propose --by me --title "..." --body "..." --type bug` |
| See what's awaiting review | `gp-tickets list --status proposed` |
| See what I'm working on | `gp-tickets list --mine me` |
| Pick up an approved ticket | `gp-tickets claim T-NNNN --by me` |
| Note progress | `gp-tickets update T-NNNN --by me --note "..."` |
| Close when done | `gp-tickets close T-NNNN --by me` |
| (human) Approve | `gp-tickets approve T-NNNN --by thomas` |
| (human) Reject | `gp-tickets reject T-NNNN --by thomas --reason "..."` |
