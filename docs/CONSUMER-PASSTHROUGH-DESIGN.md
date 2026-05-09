# Consumer-Device Passthrough — Design Doc

**Ticket:** T-0178 (research) → first impl shipped via T-0177 (commit 487269d, gp-allow + passthrough.json)
**Last verified:** 2026-05-08

The single highest-ROI consumer-readiness move per the 2026-05-08 readiness review. This doc captures the model, defaults, integration points, and the gap analysis for what still needs to ship on top of T-0177.

---

## 1. Problem

Privacy-router defaults (DoubleHop / ZHop / ZeroTrust) apply blanket policies that break common consumer devices:

| Device | Failure mode in tunnel modes |
|---|---|
| PS5 / PS4 (Sony) | DHCPOFFER unicast → ignored; QUIC PSN traffic blocked; NAT type Unknown |
| Xbox / Switch | Similar QUIC + matchmaking issues |
| Smart TVs (LG, Samsung, Vizio, Roku) | DoH (Samsung phones home to `samsungcloudsolution.net` over DoH); video CDN HTTP/3 blocked |
| Apple TV / HomePod | iCloud Private Relay traffic, Apple-managed DoH |
| Amazon Echo / Fire / Ring | Amazon DoH, low-tolerance matchmaking |

The 2026-05-08 PS5 incident burned 8 hours of two Claudes + operator. A real customer would have given up at minute 30.

**Customer-readiness impact estimate:** +8–10 readiness points. Collapses the "your privacy stack broke my console / TV" support burden into a one-time pre-populated allowlist.

---

## 2. Shipped policy model (T-0177)

### Source of truth
`/etc/ghostport/passthrough.json`:
```json
{"devices": [{"mac": "2C:9E:00:85:B6:1E", "label": "PS5"}]}
```

### Generated nft fragment
`/etc/ghostport/custom-rules.nft`:
```
insert rule inet filter forward iifname "wlan0" \
  ether saddr 2C:9E:00:85:B6:1E counter accept comment "passthrough:PS5"
```

`insert rule` lands the accept at the **top** of the forward chain, ahead of every mode-specific drop (DoH-IP, DoT, DNS forcing, kill switch).

### Persistence
`gp-mode`'s `apply_custom_rules` runs the fragment after every mode switch. Allowlist survives mode swaps, reboot (via `gp-mode-boot`), and `sudo gp-mode <same-mode>` reapplies.

### CLI
```
gp-allow add <MAC> <LABEL>     # add device, regenerate fragment, reapply mode
gp-allow remove <MAC>          # remove
gp-allow list                  # show current allowlist
gp-allow regenerate            # rebuild fragment from JSON
```

`/usr/local/bin/gp-allow` is a symlink → `~/.local/bin/gp-allow`. Repo mirror at `scripts/gp-allow`.

---

## 3. Survey of how privacy-router peers handle this

| Project | Approach | Notes |
|---|---|---|
| **AdGuard Home** | Per-client filtering settings (per IP / MAC) | Same model: operator marks a client as "less restricted" |
| **Pi-hole** | Group management (per MAC → group → blocklist) | We already use this for Family Shield (§5) |
| **NextDNS** | Per-device profiles | Cloud-side, not directly relevant to a Pi router |
| **OpenWRT + AdBlock** | DNS-only filtering (no firewall layer per device) | We need both DNS and firewall layer; AdBlock alone wouldn't fix PSN QUIC |
| **Privacy Pi / Pi-VPN** | One-size-fits-all; documents "your console may have issues" | We're trying to do better than this |
| **Eero / Plume** | "Profiles" with risk levels per device | Closer to what we want but vendor-locked |

**Common pattern:** an operator-explicit allowlist with optional vendor-OUI hint. That's what we shipped.

---

## 4. Default OUI list (recommended)

The IEEE OUI database at `/etc/ghostport/oui-extras.json` (39,377 entries, refreshed via T-0161) covers the vendors that account for 80%+ of consumer-device pain. Counts of relevant prefixes already in our DB:

| Vendor (substring match) | OUI count |
|---|---|
| Apple | 1,493 |
| Samsung Electronics | 894 |
| Amazon | 202 |
| LG (LG + LG Electronics) | 287 |
| Sony | 137 |
| Microsoft | 123 |
| Google | 104 |
| Nintendo | 107 |
| Hisense | 41 |
| Roku | 29 |
| Vizio | 16 |

**Recommended hint OUIs** for the dashboard's "looks like a console / TV" auto-suggest (NOT auto-allow — operator must confirm):

```
Sony Interactive (PS4/PS5):    bc:60:a7, 28:0d:fc, 70:9e:29, 2c:9e:00, ...
Nintendo (Switch / classic):   60:1a:c7, 7c:bb:8a, 98:b6:e9, ...
Microsoft (Xbox):              60:45:bd, 7c:1e:52, 98:5f:d3, ...
Apple (TV / HomePod / iPhone): 14:7d:da, 18:34:51, 28:cf:e9, ...
Roku:                          b8:3e:59, c8:3a:6b, dc:3a:5e, ...
Amazon (Echo / Fire / Ring):   34:d2:70, 44:65:0d, 4c:ef:c0, ...
LG (TVs + appliances):         00:e0:91, 38:8c:50, 64:bc:0c, ...
Samsung (TVs / appliances):    50:32:75, 78:bd:bc, e8:50:8b, ...
```

**Decision:** the allowlist is operator-explicit (T-0177 already shipped), and the dashboard suggests devices to mark based on OUI match. We do NOT auto-allow by OUI — that would silently weaken privacy posture for a customer who rejects the suggestion.

---

## 5. Reconciliation with existing per-device systems

### Family Shield (`/etc/ghostport/family-shield.json`)
Currently a flat per-category map (`adult`, `gambling`, `facebook`, `tiktok`, `twitter`, `acr`, `dataBrokers`) — global, not per-device. The Pi-hole group-management plumbing exists but the JSON isn't wired to per-device yet.

**Interaction with passthrough:** if a device is in `passthrough.json`, it bypasses the firewall-layer DoH-IP / DoT blocks (intentional, per T-0180 §9). It still hits Pi-hole's DNS resolver (since Pi-hole serves DHCP), so Family Shield's DNS-layer category blocks would *still apply* to a passthrough device — which is wrong if the device is, say, a parent's iPhone.

**Recommendation (follow-up ticket):** when a device is in passthrough.json, exempt it from Pi-hole group blocks too. Passthrough = full bypass = "I trust this device." Half-bypass surprises operators.

### gp-bulkhead (categorical broker / tracker filtering)
Bulkhead currently applies its blocks at the Pi-hole layer (DNS). Same logic as Family Shield — passthrough devices bypass firewall-level rules but not Pi-hole. Same fix recommended (or document as expected behavior).

---

## 6. UI surface

### Bulkhead "Trusted Devices" tab (NOT yet shipped — T-0177 closure note flags this as future polish)

Mockup:

```
┌─ Bulkhead → Trusted Devices ────────────────────┐
│ Devices listed here bypass all privacy filtering │
│ (DoH-IP, DoT, broker blocks). Use sparingly.     │
│                                                  │
│ MAC                LABEL    REASON               │
│ 2C:9E:00:85:B6:1E  PS5      PSN QUIC traffic    │
│                                                  │
│ [+ Add device]  [- Remove]  [Refresh]            │
│                                                  │
│ Suggestion: 60:1A:C7:** (Nintendo) seen on LAN. │
│             [Mark as Switch]   [Ignore]         │
└──────────────────────────────────────────────────┘
```

### Crew Manifest "Mark as game console" action
Crew Manifest already loads OUI hints via `_load_oui_extras()`. Add a row-action: when OUI is in the gaming/TV vendor set, surface a "Trust this device" button that calls `gp-allow add <mac> <suggested-label>`.

### Dashboard surface
A "Why is this device disconnected?" link on each LAN client (per T-0182) that, if root cause is firewall-block, suggests adding to passthrough.

---

## 7. Constraints honored by the shipped impl

| Constraint | How honored |
|---|---|
| Must not weaken kill-switch chain (priority -10 in tunnel modes) | Killswitch chain is `forward priority -10` (drops non-wg1); passthrough rules `insert` into the main forward chain (`priority 0`). Killswitch still fires first → if wg1 is down, even passthrough devices can't leak. ✓ |
| Must not bypass DNS lock for ZeroTrust/ZHop | DNS lock is in `prerouting` DNAT (priority -100), runs before forward. Passthrough's `forward` rule cannot affect DNS. ✓ |
| Operator must be able to revoke any device | `gp-allow remove <MAC>` regenerates fragment; next mode apply removes the rule. ✓ |
| Customer image (no Tailscale/wg0/SSH) must still work | All logic is Pi-local; no remote-management dependency. Dashboard runs on customer image. ✓ |

---

## 8. Gaps & follow-up tickets

The shipped impl (T-0177) covers the core. Remaining work:

1. **Bulkhead "Trusted Devices" tab UI** — flagged in T-0177 closure note. File as separate impl ticket.
2. **Crew Manifest "Mark as game console" row-action** — surface OUI hints in the existing UI.
3. **Pi-hole group exemption for passthrough devices** — close the Family Shield / gp-bulkhead DNS-layer leak (§5). Without this, a passthrough device is half-trusted, not fully trusted.
4. **Dashboard "Why is this device disconnected?" link** — overlaps with T-0182 (troubleshooting wizard); file there.
5. **Suggested-OUI auto-detect on AP join** — when a new MAC associates and OUI matches a known consumer-device vendor, surface a notification: "Detected new device 'PS5-class' (Sony OUI 2C:9E:00). Trust now?"

Items 1–3, 5 file separately. Item 4 rolls into T-0182.

---

## 9. Follow-up implementation ticket(s)

Filing **one bundled ticket** for items 1–3 + 5 since they share a code surface (Bulkhead + Crew Manifest + gp-allow):

- **Title:** `Trusted Devices UI: Bulkhead tab, Crew Manifest action, Pi-hole exemption, AP-join detect`
- **Type:** feature
- **Priority:** normal
- **Body:** consolidates §8.1, §8.2, §8.3, §8.5 with code-surface mapping.

Item 4 already covered by T-0182.
