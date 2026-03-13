# GhostPort Security Model

## What GhostPort Protects Against

### Network-level surveillance
- ISP cannot see browsing activity (DoubleHop/ZHop route all traffic through WireGuard)
- DNS queries hidden from ISP (ZeroTrust/ZHop lock DNS to Pi-hole, block DoT/DoH to third parties)
- Real IP hidden from websites and services (they see the VPN exit IP)

### Tracking & telemetry
- Pi-hole blocks ad trackers, analytics beacons, and fingerprinting scripts
- Blocks known telemetry domains (Microsoft, Google, Facebook, etc.)

### Local network attacks
- nftables firewall segments LAN traffic per mode
- Clients on the AP are isolated from the management plane (Tailscale)

### Metadata leakage
- ZHop is the tightest mode — only WireGuard + MagicDNS allowed, everything else dropped
- No DNS leaks in locked modes (DoT/DoH to unauthorized resolvers blocked)

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
- GhostPort is not Tor — no multi-relay onion routing

### Physical access
- If someone has physical access to the Pi, they have the config (WireGuard keys, nftables rules)
- SSL certs stored on-disk unencrypted

### No deep packet inspection
- GhostPort does not inspect packet payloads — it is not a DPI firewall
- Malicious downloads pass through unless the domain is on a Pi-hole blocklist

---

## Summary

GhostPort is strong at **network-layer privacy** — hiding what you do from your ISP and local network observers. It is weak against **application-layer threats** (what apps choose to do with your data) and **endpoint compromise** (malware on the device itself). This is the natural boundary of any router-level privacy solution.
