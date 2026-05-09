# Consumer-Device NAT-Type Fix — Design Doc

**Ticket:** T-0179 (research). Cross-Claude — primary impl is EC2-side, this is the Pi-side mirror + EC2 design recommendations for handoff.
**Companion doc (EC2 side):** `/opt/ghostport-fleet/docs/RELAY-UPNP-DESIGN.md` — to be authored from this draft by EC2 Claude.
**Last verified:** 2026-05-08

---

## 1. Problem

PS5 (verified 2026-05-08) shows NAT type **Unknown** through wg1. Without a fix:
- PSN matchmaking fails or sticks at strict NAT
- Party voice degrades
- Multi-player hosting impossible
- Xbox Live + Switch Online have similar (slightly more lenient) failure modes

**Root cause** (per EC2 Claude diagnosis, msg `2888437ab19c`): wg1 relays use plain iptables MASQUERADE → **symmetric NAT** to STUN. PSN expects either a cone NAT (Type 1/2) or working UPnP-IGD. We provide neither.

We have one PS5 customer today; this will appear on every gaming customer the moment we add gaming customers.

---

## 2. Options surveyed

### Option A — miniupnpd-iptables on each wg1 relay *(recommended; EC2 + Pi agreed)*

Standard UPnP-IGD daemon. Listens on the wg1-internal interface, lets the PS5 request port mappings, programs them into iptables.

**Pros:**
- Standard tool, packaged for Amazon Linux 2023 (`miniupnpd` package)
- Per-customer-Pi UPnP — each Pi gets its own daemon view, no kernel mods
- Handles PSN's "auto-port" model out of the box
- Logs every mapping (auditable)

**Cons:**
- One UPnP daemon per relay can only serve one client behind it cleanly. With multiple Pi customers sharing a relay, port collisions are possible.
- See §3 for multi-tenant handling.

### Option B — Static DNAT for PS5 ports

Hardcode PSN's port set (UDP 3074, 3478–3479; TCP 80, 443, 3478–3480, 8080) DNAT'd through the relay → wg1 → Pi → LAN.

**Pros:** Dead simple. No UPnP daemon.
**Cons:** Only one PS5 per relay. Multi-tenant unfriendly. Operator must reconfigure when customers come and go.
**Verdict:** rejected for fleet use; might be a fallback for a customer's own dedicated relay tier.

### Option C — `xt_FULLCONENAT` kernel module

Endpoint-independent NAT (full-cone behavior on Linux conntrack).

**Pros:** Cleanest "full cone" outcome. Solves NAT type without UPnP at all.
**Cons:**
- Kernel module — needs DKMS or pinned kernel on AL2023
- Heavy operational load: kernel update breaks the module
- Maintainability cost vs payoff doesn't justify on a privacy-router relay tier
**Verdict:** rejected unless fleet outgrows miniupnpd's per-host model.

---

## 3. Multi-tenant handling for Option A

When two or more Pis share a wg1 relay, miniupnpd needs port-range partitioning.

### Approach 3.1 — port-range split per Pi
miniupnpd config supports `ext_port_range` per listening interface. With wg1's `/24` carrying multiple Pis, each Pi gets a slice:
```
Pi A (10.66.67.10): UPnP allocates ext-port range 30000–34999
Pi B (10.66.67.11): UPnP allocates ext-port range 35000–39999
```
This requires miniupnpd >= 2.2 (supports range partitioning) — verify AL2023 package version.

### Approach 3.2 — gaming relay tier *(recommended)*
**Run a separate, smaller relay-instance pool dedicated to gaming customers.** Privacy-purist customers stay on the existing `<east-data-plane>` / `<west-data-plane>` pool with no UPnP. Gaming-mode customers route to a `gaming-east-1` / `gaming-west-1` instance pool that runs miniupnpd. (Concrete endpoint IPs intentionally redacted per SECRET-SAFETY-SOP §11.2.3 — see `/etc/wireguard/wg1.conf` on the live device for current values.)

**Rationale:**
- Cleanly separates the security risk surface (UPnP CVE history is non-trivial — see §6)
- Privacy-purists pay for and get a leaner relay
- Gaming customers opt in (explicit signal during activation)
- Per-customer billing / Stripe metering can handle the tier split

**Cost:** running a second EC2 instance pool. Probably 1–2 t3.small instances per region as the gaming tier scales.

**Recommendation:** start with 3.2 (gaming relay tier). Re-evaluate if it stays at <10 customers and a single shared relay would be cheaper.

---

## 4. Port catalog (canonical)

### PSN (PS4 / PS5)
- TCP: 80, 443, 1935, 3478–3480, 5223, 8080
- UDP: 3074, 3478–3479, 3658, 6000–7000, 10070, 49152–65535 (dynamic)

### Xbox Live
- TCP: 53, 80, 443, 3074
- UDP: 53, 88, 500, 3074, 3544, 4500

### Nintendo Switch Online
- UDP: 1–65535 (Nintendo recommends "all UDP" — basically asks for full cone)
- TCP: 80, 443

**Decision:** with miniupnpd in place, the PS5/Xbox/Switch each request their own ports via UPnP-IGD. We don't statically allow these — the daemon programs them on demand. The static fallback set above is for Option B only.

---

## 5. Pi-side changes required

Even though install is EC2-side, the Pi has two responsibilities:

### 5.1 Allow inbound UPnP control traffic to the relay
miniupnpd uses TCP/2828 (control) + multicast SSDP UDP/1900. The PS5 sends these through wg1 to the relay's wg1-internal IP (10.66.67.1).

**Pi change:** ensure `iifname wlan0 oifname wg1 ip daddr 10.66.67.1 udp dport 1900 accept` is present in tunnel modes. SSDP on a privacy network doesn't escape wg1; it terminates at the relay.

### 5.2 "Gaming mode" toggle in dashboard
A per-customer toggle: when on, `gp-mode` selects the gaming relay endpoint (e.g., `gaming-east-1.ghostport.tech`) instead of the privacy relay. State lives in `/etc/ghostport/active-region.json` (existing) extended with a `tier` field:
```json
{"id": "us-east-1", "tier": "gaming", ...}
```
The dashboard surfaces the toggle as: **"Gaming mode (lets game consoles use UPnP for matchmaking)"** with one-line caveat: *"Slightly larger fingerprint surface; only enable if you game."*

### 5.3 Per-device passthrough integration
Customer who toggles gaming mode probably also needs to add their console's MAC to passthrough.json (T-0177). Auto-suggest this when gaming mode is enabled and we see a Sony / Nintendo / Microsoft OUI on the LAN.

### 5.4 Dashboard surfacing of active mappings
Operator should see "PS5 (2C:9E:00:85:B6:1E) — UPnP active, 7 ports mapped, expires 1h22m". Pull from miniupnpd's `upnpc -l` output (relay-side); Pi requests the list from the relay over wg1's control channel (a tiny `gp-bridge` query).

---

## 6. Security analysis

### CVE history of miniupnpd
- Multiple memory-safety bugs over the years (CVE-2014-3985, CVE-2015-6031, CVE-2019-12107)
- Modern versions (>= 2.3.x) are clean since the major SSDP refactor
- AL2023 ships 2.3.x → safe baseline

### Mitigations (mandatory)
1. **Bind miniupnpd ONLY to the wg1-internal interface.** Never to eth0 / public.
2. **ACL: only Pi-LAN MACs can request mappings.** Static ACL keyed on the Pi's wg1 IP.
3. **Outbound rule: each mapped port DNATs to exactly one wg1 peer.** Cross-tenant port aliasing is a hard-no.
4. **Per-customer max ports** (e.g., 64). Prevents one PS5 starving the pool.
5. **Logging:** every mapping logged to CloudWatch with mac + duration + ports for audit.

### Attack surface delta vs status quo
Pre-miniupnpd: relay accepts inbound from a known WG peer only, no other listening daemons.
Post-miniupnpd: relay accepts inbound TCP/2828 + UDP/1900 from wg1-internal only. No new public exposure if §6.1 is honored.

**Privacy fingerprint impact:** UPnP traffic is internal-only. The customer's *outbound* traffic still appears as "from EC2 IP" externally. Zero leak of customer presence to outside observers.

---

## 7. Cross-Claude handoff

### EC2 Claude needs to author `/opt/ghostport-fleet/docs/RELAY-UPNP-DESIGN.md` covering:
1. AL2023 miniupnpd version + install steps
2. Per-relay miniupnpd config template (bind to wg1-internal, ACL, max-ports)
3. Either §3.1 port-range partitioning OR §3.2 gaming tier instance pool — operator decides
4. CloudWatch log shipping for mapping audit trail
5. SOP at `/opt/ghostport-fleet/sops/relay-upnp-add-customer.md` for onboarding a gaming customer

### Pi Claude (this side) ships:
1. Dashboard "Gaming mode" toggle (this doc §5.2)
2. `gp-mode` extension to honor `tier` field in active-region.json (§5.2)
3. Auto-suggest passthrough.json on console-OUI detection in gaming mode (§5.3)
4. Active-mapping surface in dashboard (§5.4) — depends on EC2-side `gp-bridge` query support

### Coordination flow
- This research ticket (T-0179) closes once the EC2-side mirror doc exists and the Pi-side impl ticket is filed
- EC2-side impl ticket filed against the fleet repo
- Pi-side impl ticket filed against this repo (proposed below)

---

## 8. Constraints honored

| Constraint | How |
|---|---|
| Don't regress T-0169 (no PersistentKeepalive on relays; server doesn't initiate) | miniupnpd is reactive — only opens ports on Pi-initiated request. Server-side keepalive stays off. ✓ |
| Per-customer opt-in (privacy purists shouldn't pay for UPnP) | Gaming relay tier (§3.2) — non-gaming customers never hit a UPnP-running relay. ✓ |
| Operator visible in dashboard | §5.4 dashboard surfacing |

---

## 9. Follow-up implementation tickets

### Pi-side (filing now):
- **Title:** `Gaming mode toggle + 'tier' field in active-region.json + UPnP mapping surface`
- **Type:** feature
- **Priority:** normal (high once a gaming customer signs)
- **Body:** §5 items 1–4

### EC2-side (handoff to EC2 Claude):
- **Title:** `Gaming relay tier: deploy miniupnpd-running pool + per-customer onboarding SOP`
- **Type:** feature
- **Priority:** normal
- **Body:** §6 mitigations + §3.2 tier setup + CloudWatch logging
