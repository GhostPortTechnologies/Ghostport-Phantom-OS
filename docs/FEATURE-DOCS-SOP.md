# Phantom OS — Feature Docs SOP

**Rule: Every new feature ships with a plain-English user-facing doc in `~/Documents/GhostPort-Features/`. No exceptions, no deferrals, no "I'll write it later."**

This is the companion to every engineer-facing doc in `/opt/phantom/docs/`. The two live in parallel: the `/opt/phantom/docs/` entry is for people building the OS; the `~/Documents/GhostPort-Features/` entry is for the person *using* the OS (often the same person at a different moment).

Rule origin: 2026-04-20 — operator directive after repeated "what does this do again?" moments when features landed code-complete with zero user-facing writeup.

---

## 1. When This SOP Fires

The full doc requirement fires **only** for genuinely new, user-discoverable capabilities. For variants, polish, and derivative changes a **dev comment in the code is enough** (see §1.5).

Full feature doc required when **any** of these are true:

| Trigger | Example |
|---|---|
| New desktop app / GUI | Shipping `gp-newapp.py` with a start-menu entry |
| New dashboard page / tab | Adding a page to the web UI |
| New CLI tool the user runs | `gp-foobar` added to `~/.local/bin/` |
| New firewall mode | A mode beyond ISP / ZeroTrust / DoubleHop / ZHop |
| New daemon / background service | Anything that phones home, polls, or guards |
| New keybind / mouse binding the user touches | e.g. middle-click → window list |
| Wholly new user-facing capability | A function the user couldn't do before at all |

It does **not** fire for: bug fixes, internal refactors, theme adjustments, doc edits, build-system tweaks, **or enhancements/polish of an already-documented feature** (those get a dev comment instead — §1.5).

If you're unsure: is the user able to do something they fundamentally couldn't do before? Yes → full doc. No → dev comment.

---

## 1.5. Dev-Comment-Is-Enough Rule

For changes that *are* user-observable but don't add a **new capability** — UI polish, menu reorganization, adding help buttons to existing apps, caption/label tweaks, changing how an existing feature is invoked — drop a **dev comment in the script** explaining the WHY, and skip the feature doc.

The comment should be short (2-3 lines max). It needs to answer: *what's the change, and why, so a future engineer understands intent.* No click-by-click user instructions; the existing feature doc (if any) already covers the feature itself.

**Examples that get a dev comment, not a feature doc:**
- Rewriting the right-click menu from a fuzzel popup to a native Gtk.Menu (same menu content, different presentation)
- Adding per-icon captions under the existing desktop icons (same icons, now with small-print function labels)
- Adding a `? Help` button to an existing app that pops contextual text boxes (app's core functionality unchanged)
- Renaming/reorganizing menu submenus (same commands, different grouping)
- Swapping a library under the hood (same UX)

**Placement:** put the comment at the top of the function/method/constant that embodies the change. Not in a separate CHANGELOG-style block. Close to the code.

**Tone:** terse. "This is X because Y" beats a paragraph. CLAUDE.md "default to no comments, only when WHY is non-obvious" still applies — but the dev-comment-is-enough rule means this IS one of those non-obvious WHY cases, worth the 2-3 lines.

Rule origin: 2026-04-20 — operator directive: *"if it's not a feature with unique functionalities you can just drop a dev comment and call it good."*

---

## 2. Where It Lives

```
~/Documents/GhostPort-Features/
├── README.md          ← index + instructions for readers
├── _TEMPLATE.md       ← copy this for every new feature
├── dashboard/         ← web dashboard features
├── desktop/           ← desktop / GUI features
├── modes/             ← firewall modes
├── network/           ← WiFi, tunnels, DNS, routing
├── security/          ← privacy + protection features
└── tools/             ← one-off utilities (vault, lock, etc.)
```

Chosen because Thunar opens to `~` by default, `Documents/` is a visible folder, and the user can find it without knowing a path. Do **not** move these to `/opt/phantom/` — they must stay in the user's home so they show up in the file manager.

One file per feature. Filenames: lowercase, dashes for spaces, match the feature's public name.
Example: `anchor-kill-switch.md`, `double-hop-mode.md`, `middle-click-window-list.md`.

---

## 3. What Goes In The File

Copy `_TEMPLATE.md`. Keep these five sections, in this order, with these exact headings:

1. **What it is** — two or three sentences, no jargon
2. **Why you'd use it** — real-world problem it solves
3. **How to use it** — click-by-click or command-by-command, assume zero prior knowledge
4. **What's happening under the hood** — plain-English mechanics, analogies welcome, no code
5. **Gotchas** — things that look broken but aren't, and things that look fine but will bite

**Tone:** conversational, friendly, like you're explaining to a curious friend. Never condescending, never marketing-speak.

**Length:** aim for 80–200 lines. Under 80 = probably skipping something. Over 200 = probably two features.

**Forbidden:**
- Source code blocks (plain commands are fine — `sudo gp-mode isp` yes, Python yes no)
- Unexplained acronyms (define "DoT" the first time it appears)
- "See the docs for more" punts — if it matters, say it here
- Screenshots that will age poorly (button says "Apply" today, "Confirm" tomorrow — words beat pixels)

---

## 4. Definition of Done

A feature is **not** done until:

- [ ] Code ships and runs
- [ ] Engineer-facing doc exists in `/opt/phantom/docs/` if the feature is architecturally interesting
- [ ] **User-facing doc exists in `~/Documents/GhostPort-Features/<category>/<name>.md`**
- [ ] That doc has all five sections, no TODOs, no placeholders
- [ ] The doc has been read end-to-end once (by you) as a sanity check
- [ ] If a new category was needed, it was added to `README.md`'s directory tree

If the last item is unchecked, you are not done. Don't report done.

---

## 5. Backfilling Existing Features

Don't. This SOP is forward-looking. Writing 50+ retroactive feature docs in one pass produces low-quality filler. Instead:

- When you **touch** an existing feature (fix a bug, change behavior, even look at the code to answer a question), check if its feature doc exists. If not, write one in the same session.
- Treat it like the boy-scout rule: leave the campsite better than you found it, but don't make that your whole job.

The operator can also request targeted backfills ("write the feature doc for DoubleHop"). Those are in-scope when asked.

---

## 6. Relationship To Other SOPs

- **OPERATOR-SOP.md** — "Every Tool Must Have" checklist; feature doc is an additional required item.
- **SCOPE-DISCIPLINE-SOP.md** — writing the doc is NOT scope creep; it's part of the feature. Skipping it *is* a scope violation (incomplete work).
- **UI-LAYERS-SOP.md** — if the feature is a desktop icon, widget, or menu entry, the doc references which layer it lives on.
- **ai-dev-guide.md** §6 — pre-ship checklist should include "user doc exists."

---

## 7. Worked Example

Feature: middle-click on desktop opens a window list (shipped 2026-04-20).

Category: `desktop/`
Filename: `middle-click-window-list.md`

Outline:
- **What it is:** "Click the scroll-wheel button anywhere on the desktop to see every open window, including minimized ones, and jump straight to the one you want."
- **Why you'd use it:** Faster than Alt-Tab when you've lost a window behind others.
- **How to use it:** "Click any empty desktop space with the middle mouse button (the scroll wheel pressed straight down). A menu pops up with every window grouped by workspace. Click a window to bring it forward."
- **Under the hood:** Desktop icons app catches the click, pokes the compositor via a synthetic Super+F12 keypress, compositor opens its built-in window-list menu.
- **Gotchas:** Middle-click over a terminal or browser still pastes — the window list only fires over empty desktop. If you hit an icon by accident, you'll start a drag; press Escape.

That's the whole bar. Five sections. Under 200 lines. A reader who has never used GhostPort knows what to do after reading it.
