# QUIC + DoH Block — Design Doc

**Ticket:** T-0180 (research) → implementation shipped via T-0175 (commit 487269d)
**Last verified:** 2026-05-08

This doc captures the design decisions behind the targeted DoH-IP block that replaced the original blanket UDP/443 drop. The implementation is live; this is the audit trail + maintenance plan + gap analysis.

---

## 1. Problem & decision

**Original rule (pre-T-0175):**
```
iifname $LAN_IF udp dport 443 counter drop comment "gp-quic-block"
```
Goal: prevent DoH-over-QUIC bypass of Pi-hole. Side effect: dropped *all* HTTP/3 + every legitimate QUIC client (PSN, Switch Online, Xbox Live, modern Chrome video, Apple Private Relay).

**Verified live failure:** 2026-05-08, PS5 could not connect in ZeroTrust because PSN traffic uses QUIC and has no TCP fallback.

**Decision:** drop UDP/443 only to the same set of well-known DoH-resolver IPs we already block on TCP/443. Surgical instead of blanket.

---

## 2. Shipped rule structure

`/etc/gpmodes/zerotrust.nft`, `doublehop.nft`, `zhop.nft`:
```
iifname $LAN_IF ip daddr { 8.8.8.8, 8.8.4.4, 1.1.1.1, 1.0.0.1,
                            9.9.9.9, 208.67.222.222, 208.67.220.220 }
  tcp dport 443 counter drop comment "gp-doh-block"

iifname $LAN_IF ip daddr { 8.8.8.8, 8.8.4.4, 1.1.1.1, 1.0.0.1,
                            9.9.9.9, 208.67.222.222, 208.67.220.220 }
  udp dport 443 counter drop comment "gp-doh-quic-block"
```

The two rules use identical destination sets. Same providers, two transports.

---

## 3. Provider catalog (current)

Shipped IPv4 set (7 destinations, 5 providers):

| Provider | IPv4 | DoT? | DoH? | DoQ? |
|---|---|---|---|---|
| Google Public DNS | 8.8.8.8, 8.8.4.4 | ✓ | ✓ | (planned) |
| Cloudflare | 1.1.1.1, 1.0.0.1 | ✓ | ✓ | ✓ |
| Quad9 | 9.9.9.9 | ✓ | ✓ | ✓ |
| OpenDNS / Cisco | 208.67.222.222, 208.67.220.220 | ✓ | ✓ | — |

**Known gaps from this set** (deliberate or pending):
- AdGuard DNS (94.140.14.14, 94.140.15.15) — not blocked
- NextDNS (45.90.28.0/24, 45.90.30.0/24) — not blocked (anycast subnet)
- Mullvad DNS (194.242.2.x) — not blocked
- ControlD (76.76.2.0/24) — not blocked
- Cleanbrowsing (185.228.168.x) — not blocked
- IPv6 dual entries for all of the above — not blocked

The current 5-provider set covers the resolvers that ship as defaults in Chrome / Firefox / Safari / Edge / iOS / Android. The long tail (AdGuard, NextDNS, etc.) is opt-in and far less likely to be silently chosen by a browser.

**Recommendation:** add the long-tail providers + IPv6 entries in a follow-up ticket. The marginal cost of more set entries in nft is near-zero (set match is O(1)); the marginal benefit is closing the bypass for users who explicitly configure those resolvers.

---

## 4. Alternatives surveyed

### Option A — Blanket UDP/443 drop *(rejected, was the original)*
Sledgehammer. Killed PS5 / Xbox / modern web video.

### Option B — IP-set match *(SHIPPED)*
Targeted. Maintains a known-bad list of resolver IPs.

**Pros:** simple, fast (nft set match is hash-based), low maintenance, predictable.
**Cons:** providers can rotate IPs (rare in practice for anycast roots; common at long-tail providers via `dns.next-dns-id.dns.nextdns.io`).

### Option C — SNI-based block via `ghostport-sni` (Python daemon)
We already run a Python daemon doing passive TLS fingerprinting on AP clients (`/opt/ghostport/sni-inspector.py`).

**Pros:** catches DoH regardless of resolver IP — matches `cloudflare-dns.com`, `dns.google`, `dns.quad9.net` SNI.
**Cons:** TLS 1.3 + ECH (Encrypted Client Hello) breaks SNI inspection. This is a *short-lived* defense — Cloudflare ECH is already deployed for many sites. SNI block ages poorly.

### Option D — Domain-based block at Pi-hole
Add `cloudflare-dns.com`, `dns.google`, etc. to Pi-hole blocklists.

**Pros:** Pi-hole is already in the path.
**Cons:** doesn't help when the client uses raw IP (`1.1.1.1` directly with no DNS lookup at all) — which is exactly the bypass case we're trying to stop.

### Option E — kernel-side DoQ-specific block
There is no QUIC dport other than UDP/443 in practice. Kernel can't easily distinguish DoQ from HTTP/3-video without DPI on the QUIC initial packet.

**Verdict.** Layered defense: **Option B (SHIPPED) + Option D (recommended)**. SNI block (C) is not worth the implementation cost given ECH adoption. DPI (E) is too heavy for a Pi 5.

---

## 5. Apple iCloud Private Relay

iCloud Private Relay uses QUIC (UDP/443) to two relays:
- Ingress: `mask-api.icloud.com` (Akamai/Fastly anycast)
- Egress: third-party CDN (Cloudflare/Fastly)

**This is a privacy plus, not a leak.** It does NOT use a DoH resolver — it sends queries through the same encrypted relay path. The current rule does not match Apple's relay IP ranges (which are large and rotating Akamai/Fastly subnets) so Private Relay is **unaffected** by our DoH block. ✓

If Apple ever publishes a stable IP set for Private Relay, we should explicitly carve them out as `accept` ABOVE the DoH drops to be defensive against future false-positives.

---

## 6. Browser fallback when DoH is blocked

Verified current behavior:
- **Chrome** — falls back to system resolver (Pi-hole). Logs `ERR_NAME_NOT_RESOLVED` only if Pi-hole also fails. ✓
- **Firefox** — `network.trr.mode=2` (default in some regions) attempts Cloudflare DoH, falls back on connection error to system resolver. ✓
- **Safari** — does not use DoH by default unless a configuration profile is installed. ✓
- **iOS/Android Private DNS** — uses TCP/853 (DoT), which the *first* rule of the pair already blocks on the same provider IPs. ✓

No leak when fallback path engages, because Pi-hole is in the system-resolver path and goes through the cloudflared (`127.0.0.1:5053`) or wg1-tunnel (`10.66.67.1:53`) Unbound depending on mode.

---

## 7. Maintenance plan

**Update trigger:** add a new resolver to the set when:
- A major OS / browser version ships with a new default DoH provider
- Operator support tickets mention `<provider> DNS` being requested
- Public DoH-resolver lists (e.g., `https://github.com/curl/curl/wiki/DNS-over-HTTPS`) update

**Update procedure:**
1. Edit the same set in all three mode profiles: `/etc/gpmodes/{zerotrust,doublehop,zhop}.nft`
2. Mirror to `/opt/ghostport/etc/gpmodes/`
3. `nft -c -f` validate each
4. `sudo gp-mode <current-mode>` to reapply
5. Verify with `dig @<new-IP> -p 443 google.com` from an AP client → should time out

**Refresh cadence:** quarterly review at minimum. Annual would miss adoption shifts.

**Automated refresh: deferred.** Auto-pulling a 3rd-party list into firewall rules is a supply-chain risk on a privacy router. Manual review with operator approval is the safer model.

---

## 8. Test plan

**Known-good (must pass):**
- PS5: connect to PSN, run network test → NAT type 2/3 OK, internet "successful"
- Switch: download an OS update (HTTP/3)
- Chrome on a laptop: load a major HTTP/3 site (e.g., `https://www.cloudflare.com/`) → loads in <2s with `h3` shown in DevTools
- iCloud Private Relay enabled iPhone: `mask-api.icloud.com` resolves, sites load through Private Relay

**Known-bad (must be blocked):**
- `dig +udp -p 443 @1.1.1.1 google.com` → timeout
- `curl --doh-url https://1.1.1.1/dns-query https://example.com` → curl reports DNS error
- Firefox `about:networking` → DoH "off" or fallback engaged

Both must be true simultaneously in DoubleHop, ZHop, ZeroTrust.

---

## 9. Coexistence

- **Anti-fingerprint conf:** No `/etc/ghostport/anti-fingerprint.conf` currently exists; the design assumed one but the SNI inspector (`ghostport-sni.service`) does its own fingerprinting on the AP-side. Reconcile this gap with a follow-up ticket if the file is later created.
- **DNS lock:** Independent — DNS forcing happens at higher priority via DNAT in `prerouting` (`udp dport 53 dnat to 127.0.0.1:5053` etc.). DoH-IP block is the second layer.
- **`gp-allow` device passthrough (T-0177):** Trusted devices' MACs are inserted at the TOP of forward chain — they bypass the DoH-IP drop entirely. **Intentional:** if operator explicitly trusts a device, they get unrestricted internet, including DoH. Confirmed correct behavior.

---

## 10. Follow-up implementation ticket

The shipped rule is correct and load-bearing, but the gaps in §3 should be closed. Filing a follow-up ticket separately:

- **Title:** `nft DoH-IP block: extend resolver set to long-tail providers + IPv6`
- **Type:** task
- **Priority:** normal
- **Body:** Add AdGuard, NextDNS, Mullvad, ControlD, Cleanbrowsing IPv4; add IPv6 entries for all current providers; verify each mode profile; live-fire test with `dig` against each provider on TCP/443 and UDP/443.

Closing this research ticket once that follow-up is filed.
