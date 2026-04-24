# Bulkhead — Your Firewall, Without the Jargon

A step-by-step tutorial for using the **Firewall Builder** (nicknamed *Bulkhead*) on your GhostPort device. **No prior networking knowledge assumed.**

---

## 1. What Is a Firewall, in One Paragraph?

Imagine your network is a building. The firewall is the bouncer at the front door. Every packet of internet traffic that tries to come in or go out has to walk past the bouncer. The bouncer has a notebook with rules like *"let anything from the living-room laptop through"* or *"never let traffic from that suspicious country reach the printer."* Bulkhead is the app that lets you read that notebook, and add or remove lines from it.

You don't need to know what "nftables," "chains," or "packet filtering" are to use it. Everything important is also shown in plain English.

---

## 2. When Would I Use Bulkhead?

- **Curiosity** — "What is my firewall actually doing right now?"
- **Blocking something annoying** — An app on your phone keeps phoning home. You want to shut it up.
- **Allowing something** — A game console won't connect, and you think the firewall is blocking it.
- **Investigating** — Your internet feels slow; you want to see if a rule is dropping traffic.
- **Spring cleaning** — Remove rules that haven't fired in weeks. (Bulkhead flags these for you.)
- **Backups** — Save a snapshot of the current ruleset before you experiment.

---

## 3. Opening Bulkhead

Three ways, pick whichever is handy:

1. **Desktop icon** — Click the *Bulkhead* icon (looks like a reinforced wall / watertight door).
2. **Right-click the desktop** → **Privacy Tools** → **Firewall Builder**.
3. **From a terminal** — type `gp-firewall` for the TUI version, or `python3 /opt/phantom/desktop/gp-bulkhead.py` for the graphical version this tutorial is about.

It takes about 2 seconds to load the current firewall rules.

---

## 4. What You See on Screen

When Bulkhead opens, the window has four regions:

```
┌──────────────────────────────────────────────────────┐
│  BULKHEAD  |  Firewall Rule Builder                  │  ← Header
├──────────┬───────────────────────────────────────────┤
│          │  🔍 Filter rules…              [Clear]    │  ← Filter bar
│ TABLES   ├───────────────────────────────────────────┤
│          │  🔒  Chain   Handle  Expression  Action … │
│ All      │  🔒  input   15      tcp port 22 accept … │  ← Rule list
│ mgmt     │      forw    33      ip saddr 1.2... drop │
│ nat      │      …                                    │
│          │                                           │
├──────────┼───────────────────────────────────────────┤
│          │ [+ Add] [Del] [Undo] [Refresh] [?Help] [Export] │
├──────────┴───────────────────────────────────────────┤
│  Status: 42 rules loaded                             │  ← Status bar
└──────────────────────────────────────────────────────┘
```

You can click **? Help** in the bottom right at any time for an in-app version of this guide.

### The TABLES sidebar (left)

The firewall is organized into "tables" — think of them as notebooks, one per subject. You'll usually see:

- **management** — rules that keep your own access working (SSH, dashboard, Tailscale)
- **filter** — the main accept/drop rules
- **nat** — network address translation (how your devices share one internet connection)

**For a first look, just click "All Tables" at the top** — it shows every rule in one list. You can filter later once you know what you're hunting for.

### The Filter bar (top right)

Type anything: a port number (`22`), an IP address, an interface (`wlan0`), or an action like `drop`. The list narrows down instantly. Hit **Clear** to show everything again.

### The Rule list (the big table)

This is the heart of the screen. Each row is one firewall rule. The columns, in order:

| Column | What it means |
|---|---|
| 🔒 | **Protected**. You can't delete this rule. These are the ones that keep you from accidentally locking yourself out of your own device (Tailscale, SSH, dashboard). |
| **Chain** | Which part of the traffic flow this rule watches (incoming, outgoing, forwarded). Technical — usually fine to ignore. |
| **Handle** | The rule's ID number inside nftables. Used internally when deleting. |
| **Rule Expression** | The raw firewall syntax. Precise but cryptic. Skip to the next column. |
| **Action** | What happens to matching traffic, color-coded: <br>• <span style="color:green">**ACCEPT**</span> = let through <br>• <span style="color:red">**DROP / REJECT**</span> = block <br>• <span style="color:orange">**LOG**</span> = just record it <br>• **JUMP / GOTO** = hand off to another rule set |
| **Plain English** | The same rule, translated. **If you only read one column, read this one.** |
| **Packets** | How many times this rule has fired since the last reboot. |
| **Bytes** | Total data volume the rule has seen. |

#### Reading the numbers

- **High packets on a DROP rule** = something on the internet keeps trying to reach you, and the firewall keeps refusing. This is normal. The internet is a loud place. It's not an emergency unless the numbers are growing by thousands per minute *and* from a single source.
- **Zero packets after days of use** = the rule isn't doing anything. Candidate for deletion (unless it's protecting against something that hasn't happened yet, like a specific attack).
- **Packets on an ACCEPT rule** = that traffic flow is alive and working.

### The button bar (bottom)

From left to right:

- **+ Add Rule** — opens a small form to create a new rule. See §5.
- **Delete Selected** — removes the highlighted rule. Protected rules (🔒) are rejected automatically. See §6.
- **Undo Delete** — restores the most recent deletion. Only one step back, so don't binge-delete.
- **Refresh** — re-reads live firewall state. Usually not needed (the list auto-refreshes), but useful if you made a change at the command line.
- **? Help** — opens the in-app help guide.
- **Export** — saves a timestamped snapshot of every rule to a file in `~/Downloads/` (or your configured location). Good habit before experiments.

### The status bar (very bottom)

Shows what Bulkhead is doing: "Loading…", "42 rules loaded", "Rule added successfully", or — if something went wrong — "nft error: …". **Watch this line after every Add or Delete.**

---

## 5. Your First Task: Block an Annoying IP

Say you keep seeing hits from the IP address `203.0.113.42` and you want to tell your firewall *"never talk to that address again."*

1. Click **+ Add Rule**.
2. In the dialog that appears, pick a template (Block IP / Allow Port / Rate-limit). Choose **Block IP**.
3. Enter `203.0.113.42` in the address field.
4. Click **Preview** — Bulkhead shows you the rule in both raw syntax and plain English. Make sure it reads the way you expected.
5. Click **Apply**.
6. Watch the status bar at the bottom. If it says "Rule added", you're done. If it shows an error, the rule wasn't applied — read the error, adjust, try again.
7. Back on the main screen, type `203.0.113.42` in the filter bar to confirm your new rule is there.

The new rule will start counting packets the moment it's applied.

---

## 6. Deleting a Rule

1. **Click the rule** in the big table to select it. (You can Ctrl-click to select multiple.)
2. Click **Delete Selected**.
3. A confirmation appears — read it, then click **Confirm**.
4. If the rule had a 🔒 icon, Bulkhead refuses and tells you why. That's not a bug — it's saving you from losing remote access to your own device.
5. If you change your mind immediately, click **Undo Delete**.

### What's always protected?

You cannot delete rules that:
- Allow port **4200** (the dashboard)
- Allow port **22** (SSH)
- Allow UDP port **41641** (Tailscale, your always-on remote access)
- Reference the `tailscale0` or `lo` (loopback) interfaces

These protections exist because deleting them in a pique of spring-cleaning is a common way people brick their own network remotely.

---

## 7. Exporting (Backing Up) Your Rules

Before any experiment — even a small one — hit **Export**. Bulkhead writes a timestamped file containing every rule. If something goes sideways later, a technical helper can restore from that file.

Exports go to `~/Downloads/bulkhead-export-YYYYMMDD-HHMMSS.nft` by default.

---

## 8. The One Thing to Know About Mode Switches

GhostPort has four firewall profiles — **ISP, ZeroTrust, DoubleHop, ZHop**. Each is a complete set of rules stored in a file on disk.

**Switching modes completely reloads the profile from disk.** That means:

> **Rules you add in Bulkhead do NOT survive a mode switch.**

If you want a rule to be permanent across mode changes, the rule needs to go *into* the profile file itself (in `/etc/gpmodes/`). That requires a technical helper — ask Claude or whoever maintains your device.

For **temporary** rules (*"block this IP for the rest of the week," "let this console talk while I'm gaming tonight"*), adding in Bulkhead is perfect. They'll apply immediately and stick around until either you delete them or you switch modes.

---

## 9. Troubleshooting

| Symptom | What's probably going on | What to do |
|---|---|---|
| My new rule isn't in the list | Syntax was rejected, or auto-refresh hasn't run yet | Click **Refresh**. Read the status bar. Try again with fewer changes. |
| The list is empty | Firewall isn't loaded | Open a terminal, run `sudo gp-mode status`. If nothing is active, run `sudo gp-mode isp` to load the safe default. |
| "nft error: …" in status bar | Your last change had a typo or conflict | No harm done — the old ruleset is still in place. Fix the typo and try again. |
| A rule I added yesterday is gone | A mode switch happened (auto or manual) | See §8. Add it again, or ask for it to be baked into the profile. |
| I'm locked out of the dashboard | You deleted a protection | Shouldn't be possible (🔒 guard). If it happened anyway: hold the reset button on the Pi for 10 seconds to reset the passcode, log in locally via `foot` + `gp-mode isp`. |
| I don't know which mode I'm in | Just check | `sudo gp-mode status` in a terminal. |

---

## 10. When to Ask for Help

Bulkhead is safe for exploration. You cannot break your device by clicking around or adding rules — the worst you can do is block yourself out of something temporarily, and **`sudo gp-mode isp`** is always a valid reset.

Reach out to a technical helper if:

- You want a rule to survive mode switches (needs profile edit on disk).
- You see thousands of DROP-rule hits from a single IP — someone is *really* trying to reach you.
- The rule list is empty *and* `gp-mode status` shows an active mode.
- The status bar repeatedly shows "nft error" on rules that look correct.

---

## 11. Quick Reference

- **Open Bulkhead:** desktop icon, or right-click desktop → Privacy Tools → Firewall Builder.
- **In-app help:** click **? Help** in the bottom button bar.
- **Safe reset:** `sudo gp-mode isp` in a terminal. Always works.
- **Tutorial (this doc):** `/opt/phantom/docs/tutorials/bulkhead-tutorial.md`.
- **Developer notes:** `/opt/phantom/docs/gp-bulkhead.md`.

Relax — and feel free to click around. The firewall isn't going anywhere.
