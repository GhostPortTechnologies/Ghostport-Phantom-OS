# Phantom OS Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Phantom OS, **please do not open a public GitHub issue.** Coordinated disclosure protects everyone.

**Preferred path:** [GitHub's private vulnerability reporting](https://github.com/GhostPortTechnologies/Ghostport-Phantom-OS/security/advisories/new) — this creates a private advisory only the maintainers can see.

**Alternate paths:**
- Email: `support@ghostporttechnologies.com`

### What to include

- A clear description of the issue and its impact
- Steps to reproduce (or a proof-of-concept if you have one)
- Affected version(s) — check `/etc/os-release` on the device or the v-tag in this repo
- Your GitHub handle or an email so we can acknowledge you in the fix

### What to expect

- **Acknowledgment within 48 hours** (usually faster — one human operates this)
- A timeline to triage and patch, usually 7–30 days depending on severity
- Credit in the security advisory when the fix ships, if you'd like it named
- **Bug bounty**: $50–$500 depending on severity, paid on confirmed fix. See [bug bounty terms on the blog](https://blog.ghostporttechnologies.com).

### What NOT to do

- Do not run scans, fuzzers, or exploit attempts against production Phantom OS devices you don't own
- Do not attempt to access data belonging to other users or other devices in the fleet
- Do not publicly disclose the vulnerability until we've had a chance to fix it (reasonable embargo: 90 days from initial report or sooner if jointly agreed)

---

## What Phantom OS Protects Against

### Network-level surveillance
- ISP cannot see browsing activity (Double Hop / Z-HOP route all traffic through WireGuard)
- DNS queries hidden from ISP (Zero Trust / Z-HOP lock DNS to Pi-hole, block DoT/DoH to third parties)
- Real IP hidden from websites and services (they see the VPN exit IP)

### Tracking & telemetry
- Pi-hole blocks ad trackers, analytics beacons, and fingerprinting scripts
- Blocks known telemetry domains (Microsoft, Google, Facebook, etc.)

### Local network attacks
- nftables firewall segments LAN traffic per mode
- Clients on the AP are isolated from the management plane (Tailscale)

### Metadata leakage
- Z-HOP is the tightest mode — only WireGuard + MagicDNS allowed, everything else dropped
- No DNS leaks in locked modes (DoT/DoH to unauthorized resolvers blocked)

### Optional: Dreadnought Mode (recursive DNS)
- Cuts Cloudflare out of the DNS path by running a local recursive resolver to roots
- Trade-off: plaintext DNS on the wire vs. transport-encrypted DNS to a single provider
- See the in-dashboard warning modal before enabling

---

## Known Limitations

### Application-layer decisions
- Apps that remove or weaken E2E encryption (e.g., platform policy changes) are outside our control
- Users logging into identifying accounts (Google, Facebook, etc.) defeats IP anonymity

### Browser fingerprinting
- Screen resolution, fonts, extensions, WebGL — all still visible to websites
- Pi-hole blocks some fingerprint scripts but not all

### VPN exit node trust
- Traffic is decrypted at the WireGuard exit — that provider can see unencrypted traffic
- If the VPN provider logs, the privacy chain breaks

### No endpoint protection
- Malware on the client device bypasses all network-level protections
- Keyloggers, screen capture, compromised apps — all out of scope for a router

### Traffic correlation
- A well-resourced adversary watching both VPN entry and exit can correlate timing
- Phantom OS is not Tor — no multi-relay onion routing

### Physical access
- If someone has physical access to the Pi, they have the config (WireGuard keys, nftables rules)
- SSL certs stored on-disk unencrypted

### No deep packet inspection
- Phantom OS does not inspect packet payloads — it is not a DPI firewall
- Malicious downloads pass through unless the domain is on a Pi-hole blocklist

---

## Summary

Phantom OS is strong at **network-layer privacy** — hiding what you do from your ISP and local network observers. It is weak against **application-layer threats** (what apps choose to do with your data) and **endpoint compromise** (malware on the device itself). This is the natural boundary of any router-level privacy solution.
