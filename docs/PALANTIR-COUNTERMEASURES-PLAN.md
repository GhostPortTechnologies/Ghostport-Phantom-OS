# Palantir Countermeasures — Integration Plan

**Purpose:** five anti-data-broker features integrated into existing apps (not new apps) per FEATURE-INTEGRATION-SOP.md.

**Scope:** closes surveillance vectors at the router layer that no consumer privacy router currently addresses as named features. Each item targets a concrete data-broker vector.

## Decision record — why integrate, not build new

Every feature has a *closest-match* existing surface. Creating a new app for each doubles the maintenance burden and fragments the mental model. The inventory round (2026-04-24) confirmed:
- **Arsenal** is the central toggle hub. Every on/off security feature goes there.
- **Family Shield** auto-renders categories from a schema — adding a category = zero new UI code.
- **Lookout** is the baseline daemon; **Crow's Nest** is the alert surface. Together they own the "baseline + detect + surface" pipeline.
- **Bulkhead** is a rule editor, not a toggle hub. It doesn't manage Arsenal state. Don't put toggles there.
- **Dreadnought** in the dashboard is the exact visual+state pattern for sticky mode-level toggles.

## Feature → Target map

| # | Feature | Target | API / file | Rationale |
|---|---------|--------|------------|-----------|
| 1 | Enemy List widget | `public/index.html` + new backend endpoint | `GET /api/enemies` in ghostport-server.js + new stat-box after line 1983 | Dashboard is the first place the user looks; data is in Pi-hole query log already |
| 2 | ACR (smart-TV surveillance) blocker | Family Shield category | Add `"acr"` key to `FAMILY_SHIELD_LISTS` at ghostport-server.js:3586; default `false` in readFamilyShieldConfig | Family Shield auto-binds category→blocklist URLs→Pi-hole groups. Zero UI code. |
| 3 | TCP/IP fingerprint scrub | Arsenal toggle | `tcpScrub` key in arsenal.json + POST `/api/arsenal/tcpscrub` + nftables mangle-egress rules | Follows QUIC/WebRTC pattern exactly (ghostport-server.js:3207) |
| 4 | Per-device outbound rate anomaly | Lookout (collect) + Crow's Nest (surface) | `get_device_rates()` in gp-lookout + new `alert_class: "rate_anomaly"` in `/etc/phantom/ids-events.json` | Baseline is Lookout's job; alerts are Crow's Nest's job. Clean separation. |
| 5 | Ghost Mode (WG exit-IP rotation) | Arsenal toggle + dashboard mode card | `ghostMode` in arsenal.json + POST `/api/arsenal/ghostmode` + cron-driven `wg syncconf` + new `gm-row` div cloning `dn-row` pattern | Sticky toggle under DoubleHop/ZHop cards. Visually mirrors Dreadnought. |

## Data-broker / surveillance-partner blocklists (canonical seed lists)

### ACR (Automated Content Recognition) partners
Smart TVs and streaming devices capture screen content and phone home. This list covers the companies who buy that data.
- Samba TV (samba.tv, samba-ai.com)
- Inscape (inscape-ai.com, inscape.tv)
- TVision (tvisioninsights.com)
- Vizio Inscape (tvinteractive.tv, tvmetrix.com)
- Nielsen Gracenote (gracenote.com, nielsen.com)
- Roku ACR (scribe.logs.roku.com)
- LG ACR (alphonso.tv)

### Data brokers (Enemy List tile)
Companies whose business model is aggregating American consumer data:
- Acxiom (acxiom.com, liveramp.com)
- LiveRamp (liveramp.com, rampid.liveramp.com)
- Experian (experian.com, rum.experian.com)
- LexisNexis (lexisnexis.com, risk.lexisnexis.com)
- Epsilon (epsilon.com)
- TransUnion (transunion.com)
- Equifax (equifax.com)
- Oracle Data Cloud (bluekai.com, addthis.com)
- Palantir Foundry (palantir.com, foundry.palantir.com)
- Outlogic / X-Mode (x-mode.com, outlogic.com)
- Babel Street (babelstreet.com)
- Venntel (venntel.com)

The blocklist file itself gets shipped as `etc/blocklists/acr.txt` and `etc/blocklists/data-brokers.txt` in the repo, deployed to `/etc/phantom/blocklists/` at install time.

## Risk notes

1. **False positives on ACR.** Some Smart TV features (recommendations, content discovery) break when ACR is blocked. Mitigate: ship `acr` off by default, with a clear UI warning.
2. **Ghost Mode requires EC2 multi-IP.** If the fleet is currently single-IP (likely), Ghost Mode toggle is *visible but disabled* until EC2 side is ready. Clear error text: "Requires ≥2 fleet relay IPs. Contact support@ghostporttechnologies.com."
3. **Rate anomaly baseline requires 7 days of data.** New device (fresh boot) sees "insufficient baseline — collecting" for the first week. Not an error; a warm-up message.
4. **TCP scrub may break some VoIP and gaming traffic.** Known issue with aggressive TCP option stripping. Mitigate: scrub is OFF by default; enable warning states "may affect VoIP calls and online gaming."

## Execution order

1. Plan doc ← (this file, now)
2. FEATURE-INTEGRATION-SOP.md ← codifies the decision pattern
3. Feature 1: Enemy List (lightest — pure UI + one backend endpoint)
4. Feature 3: TCP scrub (self-contained nftables + Arsenal pattern)
5. Feature 2: ACR category (blocklist file + Family Shield schema)
6. Feature 4: Rate anomaly (Lookout daemon change + Crow's Nest ingest)
7. Feature 5: Ghost Mode (Arsenal toggle + mode-card UI + cron rotator)
8. Feature docs × 5 (per FEATURE-DOCS-SOP)
9. Roadmap update + memory update
10. Full gauntlet (bash -n, nft -c, gp-qa, live test)
11. Commit by concern
12. Push

## Commit batching

Per SECRET-SAFETY-SOP §11.3 commit structure:
1. `docs:` plan + SOP
2. `feat(dashboard):` Enemy List
3. `feat(arsenal):` TCP scrub
4. `feat(family-shield):` ACR category
5. `feat(lookout+crowsnest):` rate anomaly
6. `feat(arsenal+mode):` Ghost Mode
7. `docs:` feature docs
8. `roadmap:` mark Palantir countermeasures [x]

Each batch is independently revertible.
