# HTTPS Inspection Feasibility Report

**Date**: April 3, 2026
**Status**: Research Complete — NOT RECOMMENDED for v1

## Executive Summary

Full HTTPS content inspection is **not viable for a consumer router product today**. Three independent blockers each individually kill it:

1. **Android CA problem**: Since Android 7 (2016), user-installed CAs are ignored by apps. Android 14+ made even root-level system CA install impossible (immutable APEX containers). This breaks banking, streaming, messaging, and social media apps.
2. **Certificate pinning**: Banking apps, Signal, WhatsApp, Netflix, and OS services all pin certificates and refuse MITM connections. A constantly-maintained bypass list is untenable.
3. **Zero market precedent**: No consumer router attempts this (Firewalla, Gryphon, Circle, ASUS, Netgear all use DNS/SNI only). Strong signal.

## What GhostPort Should Do Instead (v1)

1. **DNS filtering** via Pi-hole — already implemented, covers 80-90% of use cases
2. **SNI inspection** via nftables — inspect cleartext TLS ClientHello hostname
3. **ECH mitigation** — strip SVCB/HTTPS (type 65) DNS records to prevent ECH negotiation, preserving SNI visibility
4. **Per-device profiles** — different DNS/firewall rules per MAC for child vs. adult devices
5. **QUIC blocking** — already implemented, forces apps to standard TLS where SNI is visible

## Future Path (v2+)

The only viable route to HTTPS inspection would require a **companion app** (lightweight MDM) that:
- Detects device type (iOS/Android/Windows/Mac)
- Installs CA at system level with full trust
- Manages certificate lifecycle and bypass lists
- This is a major product expansion requiring per-platform native apps

## Pi 5 Hardware Assessment: GREEN

The hardware is not the bottleneck:
- AES-256-GCM: 1.8 GB/s (hardware accelerated)
- ChaCha20-Poly1305: 700 MB/s
- 8GB RAM sufficient for proxy + cert cache
- Recommendation: Use Squid or fluxzy (.NET), NOT mitmproxy (Python too slow)

## Legal: YELLOW

- Vicarious consent doctrine allows parental monitoring of minors (good faith required)
- Gray area for other household members — must require per-device opt-in
- FTC has published scrutiny of parental control privacy risks
- GDPR problematic if selling internationally

## ECH Defense: GREEN (Actionable Now)

ECH threatens current SNI inspection but is mitigable:
1. Strip HTTPS/SVCB DNS records in Pi-hole (prevents ECH negotiation)
2. DoH already blocked in ZeroTrust/ZHop
3. Block `use-application-dns.net` canary domain
4. These should be implemented NOW as a proactive defense

## Sources

- Android 14 CA changes: httptoolkit.com/blog/android-14-breaks-system-certificate-installation/
- Fluxzy benchmarks (30-70x faster than mitmproxy): fluxzy.io/resources/blogs/performance-benchmark
- Cisco ECH defense: secure.cisco.com/secure-firewall/docs/encrypted-client-hello-defense-strategies
- Vicarious consent doctrine: winston.com/en/blogs-and-podcasts/privacy-law-corner/
