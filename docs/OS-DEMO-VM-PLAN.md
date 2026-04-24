# OS Demo VM — Plan

**Status:** planning only. Not started. No implementation work until this doc is signed off by operator + EC2 Claude.

**Goal:** let a website visitor click "Try Phantom OS" on `ghostporttechnologies.com/demo` and land in an actual running GhostPort desktop in their browser. Interact with it. See it's real.

**Why not a video:** the product is a privacy OS — trust is the feature. A video tells; a live demo proves.

---

## 1. Architecture at a glance

```
  visitor browser
       │
       │  HTTPS
       ▼
  ghostporttechnologies.com/demo  (landing page + "Try It" button)
       │
       │  session token
       ▼
  demo.ghostporttechnologies.com  (KasmVNC gateway — nginx + basic auth)
       │
       │  WebSocket (noVNC-over-WS)
       ▼
  ┌────────────────────────────────────────────────┐
  │  Graviton t4g.medium  (ARM64, KVM acceleration) │
  │                                                  │
  │   ┌────────────────────────────────────────┐    │
  │   │  Phantom OS — demo build (QCOW2)     │    │
  │   │  - labwc + waybar + all GTK apps       │    │
  │   │  - Pi-hole, Dashboard                  │    │
  │   │  - Fleet-register stubbed              │    │
  │   │  - Hostapd/WG tunnels disabled         │    │
  │   │  - Egress whitelisted                  │    │
  │   └────────────────────────────────────────┘    │
  │                                                  │
  │   KasmVNC Community (≤5 concurrent sessions)     │
  │   nginx + Let's Encrypt                          │
  │   session watchdog (20-min timeout + reset)      │
  └────────────────────────────────────────────────┘
```

**Why Graviton (ARM64), not x86_64:** the real Pi is ARM64. Running ARM64-native on Graviton means the demo image is ~99% the same binaries as the real Pi image. x86 port would mean maintaining a parallel build and finding Pi-only-built packages bite at demo time. Graviton is priced similar to x86 Nitro on small sizes; no penalty.

---

## 2. Demo-mode image — the patch list

The real OS image won't boot cleanly as a cloud VM. A **demo fork** of the golden image (built from the same `gp-golden-strip` output, then further stripped) needs these changes:

| Area | Change | Reason |
|---|---|---|
| Fleet registration | Stub `gp-heartbeat`, `gp-new`, `gp-provision` to no-op | Demo VM must NOT phone home to real fleet |
| Stripe activation | Pre-seed `/etc/phantom/fleet-auth.json` with mock activated state | Skip activation wall |
| Hostapd | Mask `hostapd.service` | No wireless card in VM; AP mode impossible |
| WireGuard tunnels | Mask `wg-quick@wg0` + `wg-quick@wg1` | No demo-peer secrets in the image |
| Tailscale | Mask `tailscaled.service` | No tailnet join from a public demo |
| SSH | Leave installed but bind to 127.0.0.1 only | No external SSH exposure |
| Bridge | Mask `gp-bridge` endpoints | Isolation from prod EC2 bridge |
| Mode switching | Allow all four modes; stub DNS upstream writes | User should see the mode UI respond, but not actually reconfigure nft rules outside the VM's scope |
| Dashboard | Disable passcode (pre-authed session) OR pre-seed passcode with fixed demo value | Visitor shouldn't be blocked at login screen |
| Waybar | Add persistent banner module: **"DEMO MODE — STATE RESETS EACH SESSION"** | Transparency; prevents "is this real?" confusion |
| Hostnames / MACs | Hardcode `GhostPort-Demo` + synthetic client list in Crew Manifest | Empty screens are bad demos |
| Auto-update | Mask `ghostport-auto-update.timer` | No apt during demo |
| First-boot wizard | Pre-complete so visitor doesn't see it | Or: expose it as an explicit "replay wizard" button |
| Reset button | Hide the GPIO reset service (no GPIO in VM) | — |
| Blog pipeline / EC2 scripts | Remove `/usr/local/bin/gp-blog-*`, `/usr/local/bin/gp-bridge` | Not relevant to desktop demo, removes credentials surface |

All of this lives in a `gp-demo-strip` script modeled after `gp-golden-strip`. Runs over a mounted QCOW2 image.

---

## 3. Egress whitelist (critical — the privacy-router irony)

The demo VM must NOT have unfiltered internet — otherwise a visitor turns it into a crypto miner or an open proxy. Since GhostPort's whole deal is filtering anyway, we keep filtering enabled and whitelist:

- apt mirrors (deb.debian.org, security.debian.org, raspbian.org)
- Pi-hole blocklist sources (specific URLs, pinned)
- Marketing-domain endpoints the demo UI itself loads (self-referential)
- NTP pool (time.cloudflare.com)
- Nothing else. No general outbound internet.

Enforced at the KVM host level via nftables on the bridge interface, not inside the VM (visitor may have root in the VM — can't trust in-VM filtering).

---

## 4. Session management

**KasmVNC Community Edition** handles:
- User authentication (generated token per visitor)
- WebSocket streaming (noVNC-based)
- Recording (optional — disabled by default for privacy)
- 5 concurrent sessions on free tier (fine for beta)

**Wrapper service (systemd + bash or Python)** handles:
- Queue when 5 sessions are in use ("3 people ahead of you — ~6 min wait")
- 20-minute hard session timeout
- 5-minute idle timeout
- Session-end hook: revert QCOW2 to clean snapshot
- Rate limit by IP (max 3 sessions / IP / hour)

**Reset mechanism:** QCOW2 overlay + snapshot revert. The base image is read-only; each session gets a thin writable overlay that's discarded on disconnect. Reset = delete overlay, create new one. ~2-5 seconds.

---

## 5. Phased build

**Phase 1 — Image (3-4 days)**
- [ ] Fork golden-image output as `ghostport-demo-v1.qcow2`
- [ ] Write `gp-demo-strip` (mirrors `gp-golden-strip` structure — §6 strip routines + §7 verify)
- [ ] Boot in local QEMU, verify desktop comes up
- [ ] Verify dashboard accessible at port 4200 from host
- [ ] Verify all GTK apps launch without errors (hostapd/WG masking won't cause UI crashes)

**Phase 2 — Host + orchestrator (2-3 days)**
- [ ] Provision Graviton t4g.medium in same AWS region as existing EC2 infra
- [ ] Install KVM + libvirt + KasmVNC Community
- [ ] Deploy `ghostport-demo-v1.qcow2` as libvirt domain
- [ ] Configure QCOW2 overlay strategy + snapshot revert script
- [ ] Write session-watchdog (timeout, idle, reset)
- [ ] nftables egress whitelist on the bridge interface

**Phase 3 — Gateway + TLS (1 day)**
- [ ] Provision `demo.ghostporttechnologies.com` DNS + Let's Encrypt cert (same pipeline as api.)
- [ ] nginx reverse-proxy → KasmVNC
- [ ] CAPTCHA on the entry endpoint
- [ ] Rate limit by IP

**Phase 4 — Landing page (½ day)**
- [ ] Update `ghostporttechnologies.com/demo` with "Try Phantom OS" button
- [ ] Queue-status indicator (poll demo.ghostport.../queue.json)
- [ ] Pre-session "here's what you'll see" walkthrough

**Phase 5 — Hardening + launch (2-3 days)**
- [ ] Fuzz / abuse testing — hostile visitor tries to break out
- [ ] CloudWatch alarms: CPU, RAM, session count, nftables drop rate
- [ ] Budget alarm: $75/mo hard cap
- [ ] Recording disabled-by-default verified
- [ ] Privacy policy update ("demo sessions are ephemeral, no PII collected")

**Total:** ~2 weeks focused work. Realistically 3-4 weeks with part-time attention.

---

## 6. Cost model

| Item | Monthly |
|---|---|
| Graviton t4g.medium (always-on) | $24 |
| EBS gp3 50GB | $4 |
| Bandwidth (~100 GB demo traffic) | $9 |
| Let's Encrypt + DNS | $0 |
| KasmVNC Community | $0 |
| **Base** | **~$37/mo** |

Traffic surge scenario (product-launch viral moment):
- c7g.2xlarge scale-up (autoscaling group) — adds ~$200/mo while active
- Budget alarm + manual scale-down stops runaway costs

---

## 7. Risk + mitigation

| Risk | Mitigation |
|---|---|
| Hostile user gets root, pivots | Egress whitelist at host nftables; no SSH exposure; 20-min hard timeout |
| Visitor burns through all 5 sessions with a bot | Rate limit per IP (3/hr); CAPTCHA on entry |
| Demo discovers a real OS bug | Good — use that signal. Watchdog logs panic → we fix before beta ships |
| Cost runaway during viral moment | Hard AWS budget alarm ($75/mo); manual pause switch on demo.ghostport... |
| "Feature X doesn't work" confusion (WG, hostapd) | In-OS tooltips: "This feature runs on real hardware — demo shows the UI only" |
| Visitor thinks it's really on their network | Banner makes it unambiguous; on-screen walkthrough on session start |
| KasmVNC Community tier sessions insufficient | Fallback: Apache Guacamole (unlimited, more setup); or upgrade to Kasm Workspaces paid |

---

## 8. Open questions (waiting on operator / EC2 Claude)

1. **Graviton instance — same AWS account as prod EC2 or isolated sub-account?** Isolated is safer (demo abuse can't touch prod); same account is simpler.
2. **Concurrency target at beta launch** — 5 is the free-tier ceiling. If we expect >5 on day 1, start on Guacamole or paid Kasm.
3. **Can the demo show Dreadnought Mode?** Probably yes — unbound works in the VM, no Pi-specific bits.
4. **Privacy Exposure Score tool** (already at tools.ghostport...) — embed in the demo or link out?
5. **Recording on / off** — Kasm can record sessions for debugging. Off by default; only enable for our own debugging with explicit consent.
6. **Sequencing vs v1.1.0 beta** — ship v1.1.0 to friends first (no demo needed), then build demo for public beta? Or demo first to reach public faster?

---

## 9. What this is NOT

- It's not a full cloud-SaaS version of GhostPort. The product is still the hardware appliance. Demo is marketing + trust.
- It's not meant to replicate WiFi AP, real VPN, or fleet phone-home. Those features are disabled and tooltip-explained.
- It's not an always-on public playground (we don't want that traffic pattern). Session-limited, time-boxed.

---

## 10. Next steps when we start

1. Operator + EC2 Claude sign off on this plan.
2. Answer the 6 open questions in §8.
3. Phase 1 kickoff: fork golden image, write `gp-demo-strip`.
4. Reassess after Phase 1 — if the local-QEMU test goes cleanly, proceed. If there are unforeseen port issues, rescope.

---

*Filed under Phase 6 — Distribution & Growth in ROADMAP.md.*
