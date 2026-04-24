# GhostPort Self-Healing Architecture

**Deployed**: 2026-04-09
**Status**: LIVE — all guards active

## Overview

GhostPort runs two independent watchdog systems every 60 seconds that detect configuration drift, service failures, resource exhaustion, and tunnel health issues — then auto-correct them without user intervention.

The philosophy: a privacy router that breaks silently is worse than no privacy router at all. Users assume they're protected. If DNS leaks, a tunnel drops, or a service crashes, the system must fix itself before the user even notices.

---

## Guard Systems

### 1. DNS Integrity Guard (`gp-dns-guard`)

**Location**: `/usr/local/bin/gp-dns-guard`
**Timer**: `ghostport-dns-guard.timer` (60s interval, 30s after boot)
**Purpose**: Ensure DNS configuration always matches the active mode

| Check | Expected State | Auto-Fix |
|-------|---------------|----------|
| Pi-hole upstream (dnsmasq) | `10.66.67.1#53` in tunnel modes, `127.0.0.1#5053` in ISP/ZeroTrust | Rewrites config, reloads Pi-hole |
| Pi-hole upstream (pihole.toml) | Matches dnsmasq config | Calls `gp-dns-upstream` to resync |
| cloudflared service | STOPPED in DoubleHop/ZHop, RUNNING in ISP/ZeroTrust | Stops or starts the service |
| nftables output chain | Contains `dport 53 drop` rules in tunnel modes | Reapplies full nftables profile |
| resolv.conf | No public DNS (8.8.8.8, 1.1.1.1, 9.9.9.9, etc.) | Removes offending lines |
| DNS resolution test | `dig cloudflare.com @127.0.0.1` succeeds | Restarts pihole-FTL, then full stack reset |

**Why this matters**: The DNS leak that prompted this system was caused by `doublehop.nft` having no outbound DNS restrictions in its output chain. The Pi could send DNS queries directly out eth0 to the ISP, bypassing the WireGuard tunnel entirely. Users on ipleak.net saw their ISP's DNS servers despite being in "full tunnel" mode.

### 2. System Health Guard (`gp-health-guard`)

**Location**: `/usr/local/bin/gp-health-guard`
**Timer**: `ghostport-health-guard.timer` (60s interval, 45s after boot)
**Purpose**: Monitor and recover everything that isn't DNS

#### WireGuard Tunnel Health (tunnel modes only)

| Check | Condition | Auto-Fix |
|-------|-----------|----------|
| wg1 (data plane) interface | Must be UP | Re-runs `gp-mode <current> --no-rollback` |
| wg1 handshake age | Must be < 180 seconds | Bounces interface (down/up) to force re-handshake |
| wg0 (control plane) interface | Must be UP | Re-runs `gp-mode <current> --no-rollback` |
| Default route | Must include `wg1` | `ip route replace default dev wg1 metric 10` |

#### Critical Service Recovery

| Service | What happens if it dies | Auto-Fix |
|---------|------------------------|----------|
| pihole-FTL | DNS + DHCP down for all LAN clients | Restart, clear start-limit-hit if needed |
| hostapd | WiFi AP disappears | Restart |
| ghostport | Dashboard and API down | Restart, clear start-limit-hit if needed |
| tailscaled | Remote management lost | Restart (this is the management plane — must never stay down) |

The guard also handles `start-limit-hit` — when systemd gives up restarting a service after too many failures. The guard calls `systemctl reset-failed` and retries.

#### Network Interface Health

| Interface | Check | Auto-Fix |
|-----------|-------|----------|
| wlan0 (LAN AP) | Must be UP | Restarts hostapd |
| eth0 (WAN) | Must be UP in tunnel modes | Cannot fix ISP outage — logs warning for diagnostics |

#### Resource Monitoring

| Resource | Warning | Critical | Auto-Fix |
|----------|---------|----------|----------|
| CPU Temperature | 70°C | 80°C (throttling) | Log warning (hardware limitation) |
| Memory | 80% used | 90% used | Drop kernel caches (`echo 3 > /proc/sys/vm/drop_caches`) |
| Disk Space | 80% used | 90% used | Vacuum journals, delete old logs, clean apt cache |
| Conntrack Table | 80% full | 95% full | Flush AP client entries |
| Journal Size | — | >200M | Vacuum to 100M |

#### Health State API

The guard writes system health to `/etc/phantom/health-state.json` every run:

```json
{
  "timestamp": "2026-04-09T14:52:58-07:00",
  "mode": "doublehop",
  "temp_c": 53,
  "mem_used_pct": 40,
  "disk_used_pct": 58,
  "conntrack_pct": 0,
  "fixes": 0,
  "warnings": 0
}
```

Available via `GET /api/system/health` (authenticated).

---

## Service Restart Policies

| Service | Restart Policy | RestartSec | Notes |
|---------|---------------|------------|-------|
| ghostport | `always` | 5s | Dashboard + API |
| ghostport-discord | `always` | 10s | Discord bot |
| ghostport-reset | `always` | 5s | GPIO button |
| pihole-FTL | `on-failure` | 5s | DNS + DHCP |
| hostapd | `on-failure` | 2s | WiFi AP |
| dnsmasq | `on-failure` | 3s | **Fixed 2026-04-09** (was `Restart=no`) |
| tailscaled | `on-failure` | — | Remote management |

### Node.js Crash Protection

`ghostport-server.js` includes:
- `process.on('uncaughtException')` — logs to activity, saves, exits (systemd restarts)
- `process.on('unhandledRejection')` — logs to activity, continues (non-fatal)

---

## DNS Leak Prevention Architecture

### Firewall Layer (nftables)

In DoubleHop and ZHop modes, the output chain restricts ALL outbound DNS:

```
# Allow DNS to localhost (Pi-hole)
ip daddr 127.0.0.1 udp dport 53 accept
ip daddr 127.0.0.1 tcp dport 53 accept
# Allow DNS to Tailscale MagicDNS
ip daddr 100.100.100.100 udp dport 53 accept
ip daddr 100.100.100.100 tcp dport 53 accept
# Allow DNS through WireGuard tunnel only
oifname "wg1" udp dport 53 accept
oifname "wg1" tcp dport 53 accept
# Block everything else
udp dport 53 drop
tcp dport 53 drop
```

### Service Layer

- cloudflared is stopped in tunnel modes (no DoH to external resolvers)
- Pi-hole upstream points exclusively to tunnel Unbound (10.66.67.1#53)
- dnsmasq has `no-resolv` (never reads /etc/resolv.conf)

### Client Layer

- Prerouting NAT redirects ALL port 53 from LAN to Pi-hole
- QUIC (UDP 443) blocked — forces browser HTTPS fallback
- DoT (TCP/UDP 853) blocked in tunnel modes

### Verification Layer

- gp-dns-guard validates all three layers every 60 seconds
- gp-mode runs `verify_dns()` after every mode switch (3 retries)

---

## Systemd Timers

| Timer | Interval | Service |
|-------|----------|---------|
| ghostport-dns-guard | 60s | DNS integrity checks |
| ghostport-health-guard | 60s | System health checks |
| ghostport-update | ~30min | OTA update check |
| ghostport-auto-update | Weekly (Sun 4AM) | Pi-hole + system package updates |

---

## Logging

All guard activity is logged via syslog:

```bash
# View DNS guard logs
sudo journalctl -t gp-dns-guard --since "1 hour ago"

# View health guard logs
sudo journalctl -t gp-health-guard --since "1 hour ago"

# Only show fixes (skip routine OK logs)
sudo journalctl -t gp-dns-guard -t gp-health-guard | grep FIX
```

Routine "OK" logs are suppressed except every 10 minutes to reduce noise. Fix and warning logs are always written immediately.

---

## Testing

### DNS Guard Self-Healing Test

```bash
# Corrupt the upstream and watch it auto-fix
sudo sed -i 's|^server=10.66.67.1#53|server=8.8.8.8|' /etc/dnsmasq.d/10-ghostport-core.conf
sudo /usr/local/bin/gp-dns-guard
grep '^server=' /etc/dnsmasq.d/10-ghostport-core.conf
# Expected: server=10.66.67.1#53 (auto-corrected)
```

### Health Guard Test

```bash
# Run manually and check state
sudo /usr/local/bin/gp-health-guard
cat /etc/phantom/health-state.json | python3 -m json.tool
```
