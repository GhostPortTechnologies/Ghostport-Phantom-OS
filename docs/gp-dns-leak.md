# gp-dns-leak — DNS Leak Test

## Summary
5-phase DNS leak test that checks whether your DNS queries are properly protected. Mode-aware — knows what "secure" means for each privacy mode.

## Quick Start
1. Open **Start Menu > MONITOR > DNS Leak Test**
2. Tests run automatically (5 phases, ~10 seconds)
3. Review results — green checkmarks = secure

## Tests
1. **DNS Resolver Check** — Verifies DNS goes through expected upstream (tunnel in DoubleHop/ZHop, cloudflared in ZeroTrust)
2. **Direct DNS Bypass** — Tests if DNS can bypass Pi-hole via direct query to 8.8.8.8 (should be blocked in ZeroTrust/ZHop)
3. **DNS-over-HTTPS** — Checks if cloudflared is running and DoH is active
4. **IPv6 Leak** — Checks for global IPv6 addresses that could leak
5. **WebRTC Leak** — Checks if STUN/TURN ports are blocked in nftables

## How It Works
Uses `dig` for DNS resolution tests, checks nftables rules for port blocks, verifies cloudflared process status, and inspects IPv6 configuration. Results are mode-aware: ISP mode expects direct DNS (by design), tunnel modes expect tunnel DNS, ZeroTrust expects locked DNS.

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-dns-leak` | Main script |
