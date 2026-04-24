# Feature Integration SOP

**Rule above all rules:** when adding a new capability, default to *integrating into the closest existing app*. Creating a new app is the last resort, not the first.

**Rule origin:** 2026-04-24. Operator directive during the Palantir-countermeasures planning round: *"In order to not create a bajillion new apps we are going to do something. I want you to look at each OS app on my desktop and find the one that is most similar and find where these new features could be integrated into them."*

## 1. Why this SOP exists

The OS has 18+ desktop apps, an Arsenal TUI, a dashboard web UI, a Family Shield panel, Lookout daemon, Crow's Nest IDS, Bulkhead firewall editor, and more. Each new feature that spawns its own app:
- Doubles the maintenance burden (bug fixes, theme updates, help dialogs, menu entries, SOP compliance)
- Fragments the user's mental model ("wait, is MAC random in Seadevil or Arsenal?")
- Creates new leak surfaces (each app has its own config file, its own systemd unit, its own CSP story)
- Burns engineering time on boilerplate instead of the actual capability

The counter-rule: one feature, one home. Pick the existing surface that already does 80% of the job and extend it.

## 2. The similarity matrix (pick-your-home decision table)

| New capability shape | Natural home | Why |
|---|---|---|
| On/off security toggle | **Arsenal** (`/etc/phantom/arsenal.json` + `/api/arsenal/*`) | Central toggle hub. Mutex-locked writes. Established endpoint pattern. UI auto-binds. |
| Per-device category block (ads, adult, social, ACR) | **Family Shield** (`FAMILY_SHIELD_LISTS` in ghostport-server.js) | Schema-driven — new category = add key + blocklist URLs. UI renders automatically. Pi-hole group logic is reused. |
| nftables rule editor / custom firewall policy | **Bulkhead** (desktop GTK app) | Owns the nftables rule-editor surface. NOT a toggle hub — don't add on/off switches here. |
| Passive baseline / time-series metric | **Lookout** (`/etc/phantom/baseline.json`) | Owns rolling-window baselining. Add a sample field + collector function. |
| Alert surfacing / IDS event | **Crow's Nest** (reads `/etc/phantom/ids-events.json`) | Owns alert cards + severity classification. Add `alert_class` field; Crow's Nest filters & renders. |
| Dashboard stat tile | **public/index.html stat-box** area (~line 1945+) | Dashboard is the first place the user looks. Pattern: `<div class="stat-box">` + poll `/api/*` every 5–30s. |
| Mode-level behavior toggle (sticky, per-tunnel-mode) | **Mode card UI** (pattern: Dreadnought at index.html:1535–1540) + Arsenal state | Sticky toggles render under their relevant mode card. State lives in Arsenal. |
| Connected-device list / LAN visibility | **Crew Manifest** | Owns the DHCP+ARP+mDNS device enumeration surface. |
| Bandwidth / traffic visualization | **Tide Chart** | Owns per-interface historical byte counters. |
| Device ID / MAC operations | **Seadevil** | Owns the MAC randomization pipeline. Don't fork MAC logic elsewhere. |
| Rogue-AP / external-RF detection | **Sonar** | Owns external WiFi scan + fingerprinting. |
| Intrusion / blocked-packet alerts | **Crow's Nest** | Already tails `dmesg | grep GhostPort-DROP` + ids-events.json. Extend, don't parallel. |
| Packet capture / inspection | **Dragnet** | Owns the tcpdump + pcap viewer surface. |
| Topology / link-diagram | **Atlas** | Owns the network-graph rendering. |
| Security posture / audit score | **Quartermaster** | Owns the posture checklist + scoring. |
| Vault / secret storage | **Aether Box** | Owns at-rest encrypted storage. |
| Event log / audit trail | **Logbook** | Owns the append-only audit stream. |
| Tunnel kill-switch variations | **Anchor** | Owns kill-switch enforcement (and a matching Arsenal toggle). |
| System health (CPU/RAM/temp) | **Sea Urchin** | Owns the vitals surface. |
| ARP spoofing / L2 attacks | **Stonefish** | Owns ARP watch + guard. |
| USB device management | **Gangplank** | Owns USB enumeration + authorization. |

## 3. Inventory-before-build checklist

Before writing a single line of new code, run these greps. Each takes seconds and prevents the "I just built something that already existed" class of bug. (Reference: INVENTORY-BEFORE-BUILD-SOP.md for the general grep checklist.)

```bash
# 1. Does the feature already exist under a different name?
git grep -niE "<feature-keyword>|<synonym>|<related-verb>" -- ':!MEMORY*' ':!*.lock'

# 2. Is there an existing toggle, config key, or schema entry?
grep -i "<feature-keyword>" /etc/phantom/arsenal.json /etc/phantom/family-shield.json 2>/dev/null

# 3. Is there an existing endpoint pattern to copy?
git grep -nE "POST /api/arsenal/|POST /api/family-shield/" ghostport-server.js | head

# 4. Is there an existing desktop app in the same domain?
grep -A1 "^DESKTOP_APPS" desktop/gp-desktop-icons.py | head -30

# 5. Has the operator previously deferred a similar feature?
ls ~/.claude/projects/-home-ghostport-admin/memory/ | grep -iE "<keyword>|defer|future"
```

If step 1 finds prior art: **extend it**. Step 2 finds a related config: **add a key**. Step 3 shows an endpoint pattern: **clone it**. Step 4 shows a domain owner: **that's your target**. Step 5 shows deferred intent: **check the deferral reason before reviving**.

## 4. When new-app IS the right answer

Rare but valid cases:
1. **Entirely new domain** no existing app owns (e.g. the first time we added Bluetooth → would've needed a BLE-focused app).
2. **Workflow that can't live inside an existing app's UI** without breaking it (e.g. a real-time-streaming packet inspector has timing requirements Dragnet's post-capture viewer can't satisfy).
3. **Separation of concerns makes testing impossible** without splitting.

If you think you're in this case, justify in writing — in the feature's integration plan doc — BEFORE building. The review bar for a new app is higher than for an extension.

## 5. Anti-patterns — what NOT to do

1. **"But I want fresh naming."** Not a reason to spawn an app. Use a sub-section heading inside the existing app.
2. **"The existing app's code is messy."** Refactor the existing app as a separate commit, THEN add the feature. Don't use mess as cover to start fresh.
3. **"My feature is special."** Every feature author thinks theirs is special. The similarity matrix in §2 applies.
4. **"The existing app is too big already."** If so, split the existing app first as its own task. Don't spawn a sibling just to avoid confronting the size.
5. **"I'll integrate later, just ship standalone first."** No — "later" never happens. Ship integrated or don't ship.

## 6. Integration-plan doc template

For any non-trivial feature, write a short plan doc BEFORE touching code. Minimum fields:

```markdown
# <Feature Name> — Integration Plan

**Target app:** <name, file path>
**Surface:** <toggle | category | stat-tile | alert-class | baseline-metric | mode-toggle>
**Pattern copied from:** <existing feature in the same app that this follows>
**Storage:** <file path + schema delta>
**API:** <new endpoint, or existing endpoint extended>
**UI placement:** <file:line>
**Risk notes:** <what breaks if this is wrong>
```

One page maximum. Reference this SOP in the doc.

## 7. Post-integration verification

Before declaring done:
1. **Gauntlet** per the language (`bash -n`, `nft -c -f`, `gp-qa`, CSP check on index.html).
2. **Live test** — run the feature on the device, not just a dry run.
3. **Feature doc** per FEATURE-DOCS-SOP — plain-English user-facing doc in `~/Documents/GhostPort-Features/`.
4. **Memory update** if the feature surfaces a new decision pattern worth future sessions knowing.
5. **Roadmap sync** — mark the item `[x]` if it was on the roadmap.

## 8. Known platform constraints (read before building)

Before wiring a new feature, know these one-time facts about the Phantom OS platform. Each one cost real debug time when it surfaced mid-feature; documenting them up front saves the same loop on the next feature.

1. **Pi-hole runs at privacy level 3.** `/api/stats/top_domains` and `/api/queries` return empty. Use nftables counters (OBSERVABILITY-PATTERNS-SOP §1.1) for per-broker / per-domain aggregation.
2. **Arsenal toggle state lives in `/etc/phantom/arsenal.json`** and must be writable by the `ghostport-admin` user. If it ends up root-owned, toggles silently fail. Fix: `sudo chown ghostport-admin:ghostport-admin` + ship a startup ownership sweep.
3. **nftables `ether_addr` sets don't support `flags interval`** — MAC addresses are point values. Use `type ether_addr;` alone.
4. **`net.netfilter.nf_conntrack_acct` must be 1** for per-flow byte counters. Shipped via `/etc/sysctl.d/99-phantom-conntrack-acct.conf`.
5. **Dreadnought applies in ISP / Zero Trust only; Ghost Mode in DoubleHop / ZHop only.** They're mirror-image mode-level toggles — clone the other's row in `public/index.html` when building similar.
6. **DHCP leases live in two places**: `/var/lib/misc/dnsmasq.leases` (stock dnsmasq) and `/etc/pihole/dhcp.leases` (Pi-hole DHCP mode). Read both when resolving IP→hostname.
7. **`getaddrinfo` doesn't honor `socket.setdefaulttimeout` reliably on Linux.** Use `subprocess.run(["dig", ...])` with `+time=N +tries=1` for any DNS resolution under a deadline.

See OBSERVABILITY-PATTERNS-SOP.md §4 for the debug-time discovery that each of these caused.

## 9. Related docs

- `OBSERVABILITY-PATTERNS-SOP.md` — three data-source patterns (nftables counters / conntrack / DNS log) + when to use each. **Read this before any metric / counter / anomaly feature.**
- `INVENTORY-BEFORE-BUILD-SOP.md` — general grep checklist (this SOP builds on it for the app-integration case)
- `SCOPE-DISCIPLINE-SOP.md` — "fix the reported problem, not the whole app"
- `FEATURE-DOCS-SOP.md` — user-facing doc requirements per feature
- `UI-LAYERS-SOP.md` — desktop icons / widgets / library / right-click menu — what lives where
- `OPERATOR-SOP.md` §8 — rules learned the hard way (mode handling, force-push discipline, etc.)
