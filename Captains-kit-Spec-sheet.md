# GhostPort Privacy Router — Full Product Specification
## For Marketing, Sales, and Content Production Teams
### Version 1.3 | April 21, 2026

---

## PRODUCT OVERVIEW

GhostPort is a hardware privacy router built on Raspberry Pi 5 that sits between your home network and your ISP. It encrypts DNS, tunnels traffic through WireGuard VPN, blocks ads and trackers at the network level, and gives users full control over their family's internet privacy — all from a mobile-first web dashboard with a retro hacker aesthetic.

**One sentence:** A plug-and-play privacy router that blocks trackers, encrypts everything, and lets parents control the internet — no app store, no subscription required to use, no cloud dependency.

---

## HARDWARE

| Spec | Detail |
|------|--------|
| Platform | Raspberry Pi 5 |
| CPU | Broadcom BCM2712, quad-core ARM Cortex-A76 @ 2.4GHz |
| RAM | 4GB LPDDR5 |
| Storage | MicroSD (OS + config) |
| WAN Port | Gigabit Ethernet (eth0) |
| WiFi Radio | 5GHz 802.11ax (WiFi 6), Channel 36, 80MHz width |
| Encryption Engine | Hardware AES-256 |
| Power | USB-C, 5V/5A |
| Size | Credit card sized (85mm x 56mm) |

---

## FOUR OPERATING MODES

### 1. ISP Mode — Open Waters
- Direct internet passthrough, no encryption overhead
- Full speed, zero privacy
- Safe fallback mode (always recoverable)
- **Privacy Score: 20/100**

### 2. Zero Trust — Ghost Cloak
- All DNS encrypted via Cloudflare DoH (DNS-over-HTTPS)
- Blocks DNS-over-TLS, DNS-over-QUIC, and known DoH server IPs
- Prevents apps from bypassing Pi-hole with hardcoded DNS
- Forces all DNS through Pi-hole (no exceptions)
- **Privacy Score: 55/100**

### 3. Double Hop — Dead Man's Route
- ALL traffic routed through WireGuard VPN tunnel
- DNS encrypted end-to-end (Pi-hole → EC2 Unbound resolver)
- IPv6 disabled at kernel level (prevents tunnel bypass)
- QUIC blocked (forces inspectable HTTPS)
- **Privacy Score: 80/100**

### 4. Z-HOP — Davy Jones
- WireGuard tunnel + strict DNS lockdown
- DNS ONLY via Pi-hole + Tailscale MagicDNS
- All other DNS paths blocked (output chain drop)
- Maximum privacy, maximum control
- **Privacy Score: 95/100**

**Mode Safety:** Non-ISP modes include a 60-second automatic rollback timer. If you lose connectivity, the router reverts to the previous working mode automatically.

---

## SECURITY FEATURES

### Network-Level Protection
- **Ad & Tracker Blocking**: Pi-hole with 1M+ domains blocked, auto-updating blocklists
- **DNS Encryption**: Cloudflare DoH (DNS-over-HTTPS) via cloudflared
- **VPN Tunnel**: WireGuard AES-256 encryption to private EC2 exit node
- **IPv6 Leak Prevention**: Disabled at kernel level (sysctl) + nftables drop rules
- **QUIC Blocking**: Forces browsers off UDP/443 back to inspectable TCP HTTPS
- **DNS Rebind Protection**: Prevents internal network attacks via malicious DNS responses
- **Kill Switch**: Automatic internet cutoff if VPN tunnel or DNS encryption fails
- **Firewall**: nftables with default-deny input policy, per-mode forwarding rules

### Device-Level Security
- **MAC Privacy Check**: Detects which connected devices use randomized vs real MAC addresses. Flags exposed devices with per-platform instructions to enable Private WiFi Address
- **WPA2/WPA3 Support**: Configurable security protocol (WPA2, WPA2/WPA3 transition, WPA3-only)
- **Management Frame Protection**: ieee80211w enabled in WPA3 modes (prevents deauth attacks)

### Authentication
- **Passcode**: GP-XXXX-XXXX-XXXX format, scrypt-hashed (no plaintext storage)
- **Two-Factor Auth (TOTP)**: Standard authenticator app support (Google Authenticator, Authy, etc.)
- **Backup Codes**: 8 single-use recovery codes (12 characters each, 58+ bits entropy)
- **Brute Force Protection**: 5-attempt lockout with escalating cooldown (1min → 2min → 5min → 15min), persisted across reboots
- **CSRF Protection**: Per-session tokens validated on all state-changing requests
- **Timing-Safe Comparison**: All password and code checks use constant-time comparison (prevents side-channel attacks)
- **Session Security**: HttpOnly, SameSite=Strict, Secure cookies with 24h TTL

### Infrastructure Hardening
- **SSH**: Bound to LAN + Tailscale interfaces only (not WAN-accessible)
- **Pi-hole Admin**: Bound to LAN IP + localhost only (not WAN-accessible)
- **TLS**: Minimum TLS 1.2 with ECDHE+AESGCM cipher suite
- **Sudoers**: Path-restricted (no wildcards, specific files only)
- **mDNS/LLMNR**: Disabled (Avahi masked, systemd-resolved hardened)
- **Tailscale**: Always-on management plane (never stopped, prevents remote lockout)
- **Fleet Commands**: HMAC-SHA256 signed (prevents command injection even if tunnel compromised)

---

## FAMILY SHIELD — PARENTAL CONTROLS

Network-level content filtering that works on EVERY device — no per-device app installation required.

### Content Categories
| Category | What It Blocks |
|----------|---------------|
| Adult | Adult content via Pi-hole blocklists |
| Gambling | Gambling and betting sites |
| Facebook | All Meta/Facebook/Instagram services |
| TikTok | TikTok + ByteDance (IP-based blocking — can't be bypassed with DNS tricks) |
| Twitter/X | Twitter/X platform |

### How It Works
- **Per-device shielding**: Choose which devices get filtered (by IP address)
- **Pi-hole group filtering**: Category blocklists applied at DNS level
- **SNI Inspector**: Three-layer blocking — DNS (Pi-hole) + IP ranges (nftables ASN sets) + TLS ClientHello SNI inspection. No decryption, no MITM. Fail-closed (no SNI = blocked). Auto-starts with Family Shield.
- **IP-based blocking**: For services like TikTok that use hardcoded IPs
- **No VPN bypass**: All DNS is forced through Pi-hole — kids can't use alternative DNS

### Bedtime Torpedo (Per-Device Internet Schedule)
- Cut a device's internet access on a time + day-of-week schedule
- Schedule-based — not app-based, can't be uninstalled
- Test mode to preview schedule without waiting
- Cron-executed at the router level

---

## DASHBOARD & USER INTERFACE

### Design
- Retro hacker terminal aesthetic (green-on-black, monospace fonts)
- Customizable color themes (green, purple, blue, red, cyan, amber, custom RGB, rainbow mode)
- Animated glitch effects, scanlines, and skull branding
- Mobile-first responsive design (375px–430px optimized)

### Progressive Web App (PWA)
- Install to home screen (no app store)
- Offline support with service worker (cache-first static assets)
- Bottom navigation bar (Status / Modes / Tools / Stats)
- Pull-to-refresh
- Mobile onboarding tutorial (3-screen walkthrough)
- Works on iOS and Android

### Dashboard Panels
- **Status Banner**: Current mode, public IP, encryption level
- **Privacy Score Ring**: Animated SVG circle (20–95 based on mode)
- **Tunnel Status**: WireGuard + Tailscale indicators with latency
- **Ads Blocked**: Session + all-time counter with Pi-hole integration
- **Connected Devices**: Live client list with hostname, IP, MAC, and privacy status
- **Activity Log**: Filterable event log (auth, security, mode changes, system events)
- **Bandwidth Monitor**: Real-time per-interface traffic rates (KB/s, MB/s)
- **Network Topology Map**: Interactive SVG diagram — Internet → WAN → Pi → AP → connected devices
- **Privacy Paycheck**: Running tally of ad/tracker requests blocked, translated into estimated dollars saved
- **ISP Throttle Detector**: Runs speed tests with and without VPN to surface ISP throttling
- **Toast Notifications**: Mode changes, tunnel state, DNS block spikes

### Tools Available in Dashboard
- DNS Leak Test (5-test suite: resolver, bypass, DoH, IPv6, WebRTC)
- Speed Test (Cloudflare edge with grading)
- Ping Test
- IP Leak Check
- Security Scan (18-point posture scan with score)
- Blocklist Management (add/remove domains)
- Scheduled Mode Switching (time + day-of-week)
- WiFi Network Config (SSID, password, WPA protocol)
- WireGuard VPN Setup (guided, form, or advanced config paste)
- System Diagnostics (12 health checks + 9 one-click repairs)
- Backup & Restore
- Factory Reset
- OTA Updates

### OS-Native CLI Tools
For users who prefer the terminal — full parity with the dashboard.
- **gp-arsenal** — Terminal UI for security toggles (kill switch, QUIC block, MAC randomization, WebRTC)
- **gp-speedtest** — Cloudflare edge speed test with JSON + quick modes
- **gp-clients** — Connected devices with MAC randomization detection (watch mode, JSON output)
- **gp-diagnostics** — 12 health checks + 9 one-click repairs
- **gp-dns-leak** — 5-test DNS leak suite
- **gp-activity** — Paginated activity log viewer with filtering
- **gp-security-scan** — 18-point security posture scan with score

### Desktop Environment (Phantom OS)
The router ships with a full labwc/Wayland desktop for users who plug in a monitor.
- Waybar privacy bar (mode, privacy score, ads blocked, tunnel, DNS, clients, traffic rate, journal)
- Mission Menu — 30 items across 5 categories (Protect, Monitor, Configure, System, Power)
- Pirate-themed GTK3 tools: gp-anchor, gp-atlas, gp-crowsnest, gp-sonar, gp-crewmanifest, gp-tidechart, gp-logbook, and more
- Plymouth boot splash, custom cursor theme, green-on-black retro terminal aesthetic

---

## FLEET MANAGEMENT

### Device Provisioning
1. Customer receives device with pre-baked fleet token
2. Enters license key (XXXX-XXXX-XXXX-XXXX) on setup screen
3. System auto-generates: device passcode, WiFi password, TOTP secret, backup codes
4. Device registers with fleet server, receives WireGuard config
5. Customer is shown all credentials + QR code for 2FA setup

### Remote Management
- **Tailscale**: Always-on encrypted management tunnel (survives all mode switches)
- **Fleet Checkin**: Device polls fleet server every 60 seconds for commands
- **Remote Commands**: TOTP reset, backup code regeneration, mode switch (all HMAC-signed)
- **Device Activity**: Login attempts, mode changes, security events reported to fleet

### Subscription Integration
- Stripe payment processing (live)
- License key validation
- Subscription tier display in dashboard
- Skip-activation option for local-only use (no subscription required for core features)

---

## NETWORKING SPECS

| Feature | Detail |
|---------|--------|
| WAN | Gigabit Ethernet (DHCP client) |
| LAN | WiFi 6 AP (192.168.50.0/24, up to 254 clients) |
| VPN | WireGuard (AES-256-GCM, ChaCha20-Poly1305) |
| Management | Tailscale (WireGuard-based, NAT traversal) |
| DNS | Pi-hole + dnsmasq + cloudflared (DoH) |
| Firewall | nftables (stateful, per-mode profiles, default-deny input) |
| DHCP | dnsmasq (192.168.50.10–192.168.50.254) |
| IPv6 | Disabled at kernel level (privacy protection) |

---

## WHAT COMPETITORS DON'T HAVE

| Feature | GhostPort | Firewalla | GL.iNet | eero |
|---------|-----------|-----------|---------|------|
| 4 privacy modes with one-tap switching | Yes | No | No | No |
| 60-second automatic rollback on failure | Yes | No | No | No |
| WireGuard + Tailscale dual tunnel | Yes | No | Partial | No |
| Pi-hole integration (1M+ domains) | Yes | Basic | No | No |
| TOTP two-factor on the router itself | Yes | No | No | No |
| Per-device content filtering (no app) | Yes | App required | No | App required |
| MAC randomization detection per device | Yes | No | No | No |
| WPA2/WPA3 switchable from dashboard | Yes | No | Yes | No |
| Open-source hardware (Raspberry Pi) | Yes | No | No | No |
| No cloud dependency for core features | Yes | No | Partial | No |
| HMAC-signed fleet commands | Yes | N/A | N/A | N/A |
| PWA (installable, no app store) | Yes | No | No | No |
| DNS leak auto-detection + kill switch | Yes | No | No | No |
| Retro hacker UI with custom themes | Yes | No | No | No |

---

## TARGET CUSTOMERS

1. **Privacy-conscious families** — Parents who want network-level ad blocking and content filtering without installing apps on every device
2. **Remote workers** — VPN + encrypted DNS for work-from-home security
3. **Tech enthusiasts** — Open-source hardware, hackable, no vendor lock-in
4. **International users** — Journalists, activists, civilians in surveillance-heavy regions (Ukraine deployment in progress)
5. **ISP-skeptics** — Users who've seen their ISP sell browsing data (SJ Res 34) and want to take control back

---

## CONTENT ANGLES FOR MARKETING

### Headlines That Sell
- "Your ISP is selling your browsing history. GhostPort stops that."
- "The privacy router that blocks 1 million trackers before they reach your family."
- "One tap. Full encryption. No app required."
- "Your smart TV contacted 47 servers last night. GhostPort blocked them all."
- "The router your ISP doesn't want you to have."
- "Network-level parental controls that kids can't bypass with a VPN app."
- "WiFi 6. WireGuard VPN. Pi-hole. Two-factor auth. On a device smaller than your hand."

### Pain Points We Solve
1. ISPs selling browsing data (legal since 2017)
2. Smart devices phoning home to ad networks
3. Kids bypassing per-device parental control apps
4. DNS queries leaking in cleartext
5. VPN apps that slow everything down (GhostPort encrypts at the router level)
6. Complex privacy setups that require Linux expertise (GhostPort is plug-and-play)
7. Subscription fatigue (core features work without subscription)

### Proof Points
- **NIST CSF score: 90/100** (up from 65)
- 22-point security audit + 16 additional hardening rounds (255+ issues found, 126+ patched)
- Formal compliance suite: risk register (592 lines), incident response plan, restore runbook, asset inventory, data classification, communication plan
- HMAC-signed fleet commands (military-grade command authentication)
- Scrypt password hashing + timing-safe comparison
- Default-deny firewall (whitelist-only input policy)
- Intrusion detection: rkhunter + chkrootkit weekly scans, AIDE filesystem integrity, fail2ban
- Ukrainian civilian deployment in progress (privacy = survival)
- Open-source hardware (Raspberry Pi — nothing to hide)

---

## TECHNICAL GLOSSARY (for non-technical sales team)

| Term | Plain English |
|------|--------------|
| WireGuard | A VPN tunnel that encrypts all your internet traffic so your ISP can't see what you're doing |
| Pi-hole | Software that blocks ads and trackers for every device on your network — no browser extensions needed |
| DNS | The "phone book" of the internet. GhostPort encrypts it so your ISP can't see which websites you visit |
| DoH | DNS-over-HTTPS — encrypts your DNS lookups inside normal web traffic so they're invisible |
| nftables | The firewall that controls what traffic is allowed in and out of your network |
| TOTP | The 6-digit code from your authenticator app (like Google Authenticator) |
| Tailscale | An always-on encrypted connection that lets you manage your GhostPort from anywhere in the world |
| MAC address | A unique ID burned into every device's network chip. GhostPort detects if yours is exposed |
| WPA3 | The newest WiFi security standard. GhostPort supports it — most consumer routers don't offer the choice |
| Kill switch | Automatically cuts your internet if VPN protection drops, preventing data leaks |

---

---

## SELF-HEALING & MONITORING DAEMONS

The router watches itself — most faults self-correct without user intervention.

| Service | Job |
|---------|-----|
| `ghostport-dns-guard` | 60-second DNS health check with auto-repair drift |
| `ghostport-health-guard` | Watches CPU, RAM, disk, WireGuard, critical services |
| `ghostport-security-loop` | Daily nftables baseline check — flags drift from known-good ruleset |
| `ghostport-alerter` | Multi-channel notification daemon (security + system events) |
| `ghostport-cpu-watchdog` | CPU usage monitor with restart logic |
| `ghostport-desktop-watchdog` | Monitors labwc compositor, restarts on crash |
| `ghostport-noise` | Poisson-distributed cover traffic generator (traffic analysis defense) |
| `ghostport-heartbeat` | 5-minute fleet check-in with subscription verification |

Weekly IDS scans via cron: rkhunter, chkrootkit, AIDE integrity. Hourly log shipping to fleet server.

---

*Generated from live system analysis — Phantom OS v1.5, April 21, 2026*
*All specifications verified against running production code*
