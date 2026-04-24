# Phantom-OS Roadmap
> Source of truth. Updated every session. Never delete entries — mark [x] or [BLOCKED].

---

## Phase 1 — Core OS (Foundation)
- [x] WireGuard VPN integration (dual-tunnel: wg0 control + wg1 data)
- [x] Pi-hole DNS filtering + encrypted DNS (cloudflared DoH)
- [x] Command Deck dashboard (103 API endpoints, vanilla JS SPA)
- [x] AP+STA simultaneous mode (WiFi 6 / MT7921, hostapd 80MHz)
- [x] Four privacy modes: ISP / ZeroTrust / DoubleHop / ZHop
- [x] 60-second rollback timer on mode switches
- [x] Boot persistence (ghostport-boot.service restores saved mode)
- [x] WiFi WAN support (gp-wan: scan, connect, fallback to ethernet)
- [x] Passcode auth (scrypt + TOTP 2FA + backup codes)
- [x] OTA update system (gp-update + gp-auto-update, SHA-256 verified)
- [x] GPIO reset button (10s = reset, 30s = factory reset)
- [x] Fleet provisioning + device registration (gp-provision, gp-new)

---

## Phase 2 — Security & Hardening
- [x] NIST CSF security audit — 95/100
- [x] 311+ bugs found and fixed across 16 audit rounds
- [x] 22-point security audit (ALL CLOSED)
- [x] HMAC-signed fleet commands with timingSafeEqual
- [x] Rate limiting with exponential backoff
- [x] CSRF token protection on all mutating endpoints
- [x] IPv6 disabled (kernel + nftables), DoT/QUIC blocked
- [x] SSH hardened (LAN + Tailscale only, MaxAuthTries=3)
- [x] WebRTC leak prevention (STUN/TURN blocked)
- [x] Traffic noise generator (gp-noise, Poisson-distributed cover traffic)
- [x] Self-healing DNS guard (60s timer, auto-corrects drift)
- [x] System health guard (CPU, memory, disk, WireGuard, services)
- [x] Security scan loop (gp-security-loop, daily nftables baseline check)
- [x] gp-preflight (13-point health check before changes)
- [x] gp-safe-edit (backup + apply + validate + auto-rollback)

---

## Phase 3 — Command Deck (Advanced)
- [x] Pi-hole stats integration in dashboard
- [x] WireGuard peer management UI
- [x] Arsenal security tools panel (kill switch, MAC random, blocklist, DNS test, QUIC block)
- [x] Family Shield parental controls (per-device, per-category, SNI Inspector)
- [x] Simple mode / Advanced mode toggle
- [x] Diagnostics & repair panel (DNS, WireGuard, firewall, Tailscale, reboot)
- [x] Speed test, ping, IP leak, bandwidth tools
- [x] ISP WiFi sensing detection + modem guide
- [x] Security posture score (0-100 real-time)
- [x] Threat intelligence API (/api/threat/summary)
- [x] PWA (installable, offline support)
- [x] Real-time traffic monitoring panel (backend + frontend + waybar module)
- [x] Network topology map (SVG: Internet -> WAN -> Pi -> AP -> devices, 732 lines)
- [ ] One-click VPN server add
- [ ] Multi-region exit node selector

---

## Phase 4 — Phantom OS Desktop
- [x] Privacy-first desktop (labwc/Wayland compositor)
- [x] Waybar privacy bar (8 modules: mode, score, ads, tunnel, DNS, clients, CPU, temp)
- [x] Mission-organized start menu (Protect/Monitor/Configure/System/Power)
- [x] Security notification daemon (gp-notify-daemon via mako)
- [x] Privacy report tool (gp-privacy-report, rich terminal output)
- [x] First-boot wizard (gp-first-boot, 4-step guided setup)
- [x] Custom Plymouth boot splash
- [x] GTK3 GhostPort theme (green-on-black)
- [x] Desktop widget engine (7 draggable GTK3 widgets)
- [x] Pirate cursor theme
- [x] GPU acceleration enabled
- [x] 7 native OS tools (gp-arsenal, gp-speedtest, gp-clients, gp-diagnostics, gp-dns-leak, gp-activity, gp-security-scan)
- [x] Start menu expanded to 30 items across 5 mission categories
- [ ] Visual regression testing (screenshot capture + comparison)

---

## Phase 5 — Infrastructure (EC2 + Fleet)
- [x] AWS dual-instance (t3.micro control + t4g.small data)
- [x] AWS control plane / data plane separation (wg0 + wg1)
- [x] Fleet API (Python/SQLite, devices, licenses, commands)
- [x] Stripe integration LIVE (3 tiers: $5/$10/$15 monthly)
- [x] QR-based activation flow (scan -> Stripe Checkout -> license link)
- [x] Heartbeat system (5-min checkin, subscription verification)
- [x] Claude bridge (Pi <-> EC2 async messaging, HMAC auth)
- [x] nginx + Let's Encrypt HTTPS on api.ghostporttechnologies.com
- [x] Unbound DNS-over-TLS on data plane (10.66.67.1:53)
- [x] Performance tuning (BBR, fq, TCP Fast Open, 16MB buffers)
- [x] CloudWatch monitoring + UptimeRobot
- [ ] Automated NIST CSF scoring pipeline
- [ ] EC2 autonomous security audits on schedule

---

## Phase 6 — Distribution & Growth
- [x] Blog pipeline (gp-blog-pipeline, auto-generate + deploy to EC2)
- [x] Privacy Exposure Score tool (tools.ghostporttechnologies.com)
- [x] Affiliates portal LIVE ($20/sale commission, referral tracking)
- [x] Sales playbook (affiliates.ghostporttechnologies.com/sales-playbook.html)
- [x] Cross-site navigation (5 properties: blog, affiliates, investors, demo, tools)
- [x] Mobile responsive on all web properties
- [x] CMMC compliance blog article (Level 1 met, ~75% toward Level 2)
- [x] User guide (guide.html, 10 sections)
- [x] 12+ blog articles published
- [x] Content kit v2 (80 JPG + 80 MP4 fact cards)
- [x] Discord support bot LIVE
- [x] Bug bounty program announced ($50-$500)
- [x] Batch 1 production ready (10 units, all 5 phases complete)
- [ ] Kickstarter campaign
- [ ] Security Hall of Fame page (responsible disclosure)
- [ ] OS Demo VM (live browser-streamed Phantom OS on demo.ghostporttechnologies.com — ARM64 Graviton + KasmVNC, ~2 weeks, see `docs/OS-DEMO-VM-PLAN.md`)

### Palantir Countermeasures (2026-04-24)

Feature wave targeting the at-home data-broker / surveillance-partner pipeline. Integrated into existing apps per `docs/FEATURE-INTEGRATION-SOP.md` (no new apps spawned).

- [x] Enemy List — named data-broker block counters on the dashboard (nftables counters, privacy-preserving)
- [x] Smart TV Surveillance category (Family Shield) — blocks Samba TV, Inscape, TVision, Nielsen, Roku ACR, LG Alphonso
- [x] Data Brokers category (Family Shield) — blocks Acxiom, LiveRamp, Experian, LexisNexis, Palantir, Outlogic, Venntel, etc.
- [x] TCP/IP fingerprint scrub (Arsenal toggle) — normalizes TTL + MSS so passive observers can't tell iOS from Windows
- [x] Per-device outbound rate anomaly (Lookout+Crow's Nest) — catches silent telemetry activations and compromised-device phone-home
- [x] Ghost Mode (Arsenal + mode card) — rotates WG data-plane exit IP every 4h on DoubleHop/ZHop
- [ ] EC2 fleet multi-IP provisioning (Ghost Mode prerequisite — coordinating with ops)
- [ ] Data-broker opt-out concierge (generate CCPA/GDPR deletion requests on behalf of the user)
- [ ] Bluetooth beacon scanner (detect AirTag-class presence trackers in the home)

### Customer-1 Hardening Pass (2026-04-24)

Post-wave hardening from the honest-assessment review. All shipped + regression-tested.

- [x] #3 ACR first-enable warning modal — prevents silent smart-TV breakage
- [x] #4 Rate anomaly 7-day observe-only window — prevents false-positive noise during baseline warmup
- [x] #8 Node `applyNftRuleset` helper — collapsed 14-line Promise-wrapped spawn duplication
- [x] #11 Broker bootstrap IPs — 119 pre-resolved IPs shipped so first boot counts on minute 1
- [x] #12 Enemy List collapsible (default collapsed) — respects users who don't want the tally on the dashboard
- [x] #14 Ghost Mode rollback on rotation failure — tunnel reverts to last-known-good endpoint if new relay doesn't handshake
- [x] OTA deploy coverage — sysctl, sudoers, blocklists, cron.d, dnsmasq.d now actually deploy
- [x] `gp-dns-rules` CLI + Bulkhead DNS Rules tab — operator + customer can toggle dnsmasq address= rules from GUI or terminal
- [x] Anti-fingerprint config shipped via repo (was dev-only drift)
- [x] Regression suite — 6 test files, 51+ assertions, `tests/run-all.sh` meta-runner
- [ ] #5 TCP scrub extension — window scale, timestamp, SACK-PERM normalization (current covers TTL + MSS)
- [ ] #6 Block MAC hardening — detect MAC rotation on blocked devices + re-block
- [ ] #2 Broker list maintenance pipeline — 22 orgs curated manually, will rot without a refresh process

### Stonefish (ARP Guard) upgrades (2026-04-24)

- [x] Vendor + hostname in device table (OUI lookup + DHCP lease hostname)
- [x] mako critical notifications on SPOOFING / GW CHANGED (5 min per-MAC rate limit)
- [x] One-click Block MAC (right-click → Block; persists across reboot via nftables + systemd restore service)
- [ ] Timeline tab (ARP anomalies over time, like Tide Chart for bandwidth)
- [ ] Trust / whitelist right-click action (suppress alerts for known-good devices)
- [ ] More attack patterns: gratuitous ARP, ARP scans, ARP flood, MAC flapping
- [ ] Waybar module for live Stonefish alert count

---

## Phase 7 — Future (Unscheduled)
- [ ] Multi-region exit node upsell
- [ ] GhostPort Pro (LattePanda / Intel N150 enterprise SKU)
- [ ] Mobile companion app (iOS/Android)
- [ ] GhostPort Pods (WiFi range extenders, GL.iNet OpenWrt bridges)
- [x] Privacy Paycheck (calculate money saved from blocked ads)
- [x] Bedtime Torpedo (per-device internet kill schedule)
- [ ] Snitch Map (real-time tracker destination map)
- [x] ISP Throttle Detector (VPN comparison speed test)
- [ ] Guest Network QR + Auto-Expire
- [ ] Per-device split tunneling
- [ ] Encrypted vault for /etc/phantom/ (gocryptfs)

---

## Known Issues (Fix Queue)
- [x] Health guard bash octal bug — FIXED (integer parsing)
- [x] Change-passcode drops TOTP — FIXED (writeAuth preserves totp field)
- [x] Cloudflared DoH proxy resilience — FIXED (auto-restart + health guard)
- [x] SSH service fails at boot — FIXED (bind ordering + boot race)
- [x] Backup export includes WiFi passphrase — FIXED (wpa_passphrase redacted)
- [ ] Hardcoded eth0 references in some scripts (low priority, gp-wan handles fallback)

---

## Human-Only Items (permanent)
- [H] Physical SD card flashing and hardware testing
- [H] New hardware procurement decisions
- [H] Brand and UI/UX design decisions
- [H] Customer communications and pricing changes
- [H] Legal / compliance review
- [H] Tier naming alignment (Crew/Captain/Admiral vs Basic/Guardian/Covenant)
- [H] Kickstarter go/no-go decision
- [H] AppArmor kernel module activation decision
- [H] Golden SD card image build (needs spare SD)
