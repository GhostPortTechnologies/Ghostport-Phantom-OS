# End-User Troubleshooting Wizard — Design Doc

**Ticket:** T-0182 (research)
**Last verified:** 2026-05-08

The 2026-05-08 PS5 incident was 8 hours of two Claudes + the operator chasing a single device. A non-technical customer would have given up at minute 30 and asked for a refund. We need a wizard that lets a customer self-serve common consumer-device connectivity failures.

---

## 1. Survey of how peers handle this

| Vendor | Approach | What we'd steal |
|---|---|---|
| **Eero** | Per-device "Connection Health" tile + auto-suggested actions | The card-per-device UX; a "Why?" link on each unhappy device |
| **Firewalla** | Live event log per device + AI-generated narrative ("PS5 lost DNS for 32s at 14:02") | The narrative summarization — turn nft counters + journal lines into English |
| **Asus AiMesh** | Stepwise wizard with "next" / "skip" buttons, plus a "Run automatic fix" CTA | Stepwise structure; the auto-fix CTA model |
| **Plume** | Operator-approval gate before applying any change — surfaces "This is what we'd change. Approve?" | The diff-preview model before any auto-fix |
| **Pi-hole + AdGuard Home** | None — they show a query log but no wizard | Anti-pattern: don't make customers read DNS logs |

**Synthesis:** Eero's per-device card + Plume's diff-preview + Firewalla's narrative summarization. Asus's wizard structure for first-time users.

---

## 2. Minimum decision tree (80% of consumer-device pain)

```
[Device offline / "internet not working" reported]
        │
        ▼
1. Is the device associated to wlan0?
   ├─ Yes → continue
   └─ No  → "Move closer to the router / re-join WiFi" (out of scope; document only)
        │
        ▼
2. Did the device get a DHCP lease?
   ├─ Yes → continue
   └─ No  → 2a. Was DHCP discover seen but no offer?
            ├─ Yes → "DHCP unicast/broadcast bug" — apply T-0176 broadcast fix
            └─ No  → "Pi-hole DHCP service down" → restart pihole-FTL
        │
        ▼
3. Is the device's traffic egressing the tunnel? (conntrack + counters)
   ├─ Yes → continue
   └─ No  → 3a. Is wg1 up? Is the killswitch chain present?
            ├─ wg1 down → "Tunnel down — switch to ISP mode for connectivity"
            └─ killswitch dropping device → "Add device to Trusted Devices" (T-0177)
        │
        ▼
4. Is DNS resolving for the device?
   ├─ Yes → continue
   └─ No  → 4a. Pi-hole query log shows blocks?
            ├─ Yes → "Family Shield / Bulkhead category blocked the query" → operator review
            └─ No  → "DNS path broken" → check cloudflared / wg1 unbound
        │
        ▼
5. Is NAT type usable for game consoles? (STUN test from Pi against PSN)
   ├─ Type 2/3 (cone) → ✓
   ├─ Type Unknown   → "Enable Gaming mode" (T-0179, miniupnpd on relay)
        │
        ▼
[All checks pass] → "Device looks healthy. If issue persists, file a ticket."
```

This tree solves the majority of common cases. The 20% it misses (DNS poisoning, ISP-side outage, hardware fault) escalates to operator + Chamber chat.

---

## 3. UI surface

### Decision: embed in dashboard, NOT standalone /troubleshoot

The dashboard is already auth-gated and the customer's mental home for the device. A standalone `/troubleshoot` would split the customer's attention.

**Layout:** new "Devices" tab in dashboard sidebar with a per-device card. Clicking a card opens a modal with:
- Device summary (MAC, label, vendor OUI, current IP, last seen)
- Health status pill: ✓ Healthy / ⚠ Issue / ✗ Offline
- "Run troubleshooter" button → starts the §2 decision tree
- "Recent fixes" log: every action applied to this device, with timestamp

### Chamber AI chat as escalation

If the wizard's decision tree exits at step 5 with "issue persists", surface a "Open Chamber chat with this context" CTA that opens Chamber pre-loaded with the device's diagnostic dump (mac, mode, recent fixes, failing check). Operator (the customer themselves, or the support handle) can chat with Chamber AI for a 1:1 walkthrough.

### Per-step UI

Each decision-tree step renders as a card:
```
┌─ Step 2 of 5: Did your PS5 get an IP address? ─────┐
│ Checking…                                          │
│                                                    │
│ Result: ✗ No DHCP lease found in the last 5 min   │
│                                                    │
│ We saw your PS5 ask for an IP, but our router    │
│ didn't reply. This is usually a setting we can    │
│ fix automatically.                                │
│                                                    │
│ [ Apply automatic fix ]  [ Skip ]  [ Get help ]   │
└────────────────────────────────────────────────────┘
```

**Plain-English copy.** No nft / DHCP-discover / conntrack jargon at the customer surface. Every check has a customer-friendly phrasing.

---

## 4. Auto-fix vs operator-approval gating

Three categories:

**Tier A — Apply automatically** (low risk, easily reversible, well-understood):
- Add a known-good vendor OUI device to passthrough.json (with explicit "Trust this device for full internet" prompt)
- Restart pihole-FTL (read-only impact while restarting; sub-second)
- Re-issue DHCP for the specific device (forces re-association)

**Tier B — Operator confirms** (touches firewall / mode / global state):
- Switch tunnel mode (would affect ALL devices, not just this one)
- Enable Gaming mode (changes relay tier, fingerprint posture changes)
- Disable Family Shield category for a device (privacy / parenting policy)

**Tier C — Operator-only** (out of scope for customer):
- Edit nft profiles
- Change DNS upstream
- Change Tailscale state

Customer image: tier A fixes are exposed via "Apply automatic fix" buttons; tier B require the operator passcode (customer types it once per session); tier C is not surfaced at all (only via SSH, which customer image doesn't have).

---

## 5. Fix history surface

Every fix applied is logged to `/etc/ghostport/activity.json` (existing schema per `reference_activity_log_schema.md`):
```json
{"ts": 1778307706, "type": "wizard_fix", "message": "Trusted device 2C:9E:00:85:B6:1E (PS5) — added to passthrough", "detail": {"applied_by": "wizard", "step": 3, "device_mac": "2C:9E:00:85:B6:1E"}}
```

Logbook (existing UI) reads this file. Wizard's "Recent fixes" tab is a filtered view of activity.json where `type=wizard_fix` and `detail.device_mac=<this device>`.

**Customer-visible:** human-readable timestamp + plain-English message ("Trusted PS5 — full internet"). Detail blob is collapsed by default; expand for the technical context.

---

## 6. Mockup flows

### Flow A — "My PS5 won't connect"

```
Step 1: Is your PS5 turned on and connected to GhostPort WiFi?
        [ Yes, it's connected ]  [ No, help me connect it ]
   ↓ Yes
Step 2: Checking PS5 IP address…
        ✗ Your PS5 didn't get an IP. This usually means a DHCP setting.
        [ Apply automatic fix ]
   ↓ Apply
        ✓ Fixed. Restart your PS5's network connection.
        [ I restarted, check again ]
   ↓ Recheck
Step 3: Checking internet path…
        ✓ Your PS5 is reaching the internet through GhostPort.
Step 4: Checking DNS…
        ✓ DNS is working.
Step 5: Checking NAT type for PSN…
        ⚠ NAT Type: Unknown. PSN matchmaking may fail.
        [ Enable Gaming mode ] (operator confirms; opens info modal)
   ↓ Confirm
        ✓ Gaming mode enabled. Restart your PS5 to retest.
```

### Flow B — "My TV won't load YouTube"

```
Step 1: Is the TV connected to WiFi?  [ Yes ]
Step 2: ✓ TV has IP 192.168.4.123 (LG OUI 64:BC:0C)
Step 3: ✓ Internet path OK
Step 4: ✗ DNS shows 'youtube.com' was BLOCKED.
        Reason: Family Shield (acr=true) is blocking ACR-tracked services.
        [ Allow YouTube on this device ] (operator confirms)
        [ Disable acr globally ] (operator confirms)
        [ Cancel ]
```

### Flow C — "I can't sign into PSN"

```
Step 1: ✓ PS5 connected
Step 2: ✓ Has IP
Step 3: ⚠ Some PSN traffic blocked.
        Reason: ZHop mode strict-DNS rules block PSN's DoH attempts.
        [ Mark PS5 as Trusted ] (Tier A — auto-apply with confirmation)
   ↓ Apply
        ✓ PS5 trusted. Sign in again.
Step 4: ✓ DNS resolves PSN domains
Step 5: ⚠ NAT Unknown — see Flow A step 5
```

These three flows cover ~70% of consumer support cases observed in the 2026-05-08 incident pattern.

---

## 7. Backend mapping

Wizard checks map to existing tools:

| Step | Backend |
|---|---|
| 1. Associated? | `iw dev wlan0 station dump` + filter by MAC |
| 2. DHCP lease? | `gp-leases | grep <mac>` |
| 3. Tunnel egress? | `conntrack -L` filtered by client IP + nft counter delta |
| 4. DNS resolving? | Pi-hole `pihole -q <domain> --client <mac>` + check query log |
| 5. NAT type? | Pi-side STUN probe (small Python helper, ~30 lines) |

All callable from the existing `ghostport-server.js` Express API. Add new routes:
- `GET /api/device/<mac>/health` — runs steps 1–5, returns JSON
- `POST /api/device/<mac>/fix/<step>` — applies tier-A/B fix
- `GET /api/device/<mac>/history` — filtered activity.json

---

## 8. Constraints honored

| Constraint | How |
|---|---|
| Must work on customer image (no Tailscale/wg0/SSH/bridge) | All logic Pi-local; uses existing dashboard auth. ✓ |
| Customer must self-serve common issues | Tier A fixes are one-button. ✓ |
| Must respect Family Shield | Tier B gate for parental-controls overrides; passcode required. ✓ |
| Privacy-first (data stays on Pi) | All diagnostics local; no external telemetry. Optional "share with support" toggle. ✓ |

---

## 9. Implementation phases (suggested)

**Phase 1 — Backend + JSON API**
- Add `/api/device/<mac>/health` route
- Implement steps 1–4 (5 needs T-0179 miniupnpd to be useful)
- Test via curl

**Phase 2 — Wizard UI**
- Devices tab in dashboard
- Modal with step cards
- Tier A "Apply" buttons
- Activity log filter

**Phase 3 — Tier B passcode gating**
- Re-prompt operator passcode for Tier B fixes
- Audit log every Tier B action

**Phase 4 — Chamber chat handoff**
- "Open Chamber with context" CTA
- Pre-loaded diagnostic dump in first message

Phases 1–2 unlock 60%+ of the value. 3–4 are polish.

---

## 10. Follow-up implementation tickets

Filing **3 tickets** matching the phases:

1. `Wizard backend: device-health API + steps 1–4 implementations` (Phase 1)
2. `Wizard UI: Devices tab + step modal + tier-A apply buttons` (Phase 2 — depends on 1)
3. `Wizard polish: tier-B passcode gate + Chamber handoff` (Phases 3–4 — depends on 2)

NAT-type step (5) lives in T-0186 (gaming-mode toggle); cross-link there.
