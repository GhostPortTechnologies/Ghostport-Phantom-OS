# Phantom OS — Field Support Runbook

Customer-facing triage playbook for deployed units. Every procedure here assumes you are not physically next to the device and must drive recovery over Tailscale or by guiding the customer to the hardware reset button.

**Golden rule:** ISP mode is the safe fallback. Whenever triage is uncertain, get the unit to ISP mode first, then diagnose — ISP has no rollback timer and restores flat eth0 passthrough.

---

## 1. Access Paths (in reach order)

| Path | When to use | How |
|------|-------------|-----|
| **Tailscale SSH** | Always try first. Management plane is up in every mode. | `tailscale ssh ghostport-admin@<device-tsname>` (names in fleet registry). If SSH fails but the device is Tailscale-online, tailnet routing is fine — move to path 2. |
| **Tailnet HTTPS** | Dashboard check without shell. | Browser to `https://<device-tsname>:4200` — tailnet is auth-gated. 5xx = server down, 302 to /login = server up. |
| **Public-IP SSH** | Tailscale down, customer network reachable. | Pull device public IP from last heartbeat on EC2 (`/api/fleet/heartbeats`). Requires the Pi's public IP to be in EC2 UFW allowlist (see incident 2026-04-02). |
| **Customer on-site** | All remote paths dark. | Walk customer to the GPIO reset button. See §4. |

**Never ask the customer for their passcode.** Plaintext is not stored — only the scrypt hash. If they've forgotten it, use the 10-second GPIO hold to generate a new one (§4.1).

---

## 2. Triage Decision Tree

Start at the top and walk down. Each node has a concrete command.

### Step 1 — Can we reach the device at all?

```bash
tailscale ping <device-tsname>         # tailnet liveness
```

- **Answer: yes** → Go to Step 2.
- **Answer: no, but device shows "online" on tailnet** → Tailscale daemon ok, something else broke. Go to Step 2 via HTTPS path.
- **Answer: offline on tailnet** → Device has lost internet or crashed. Customer on-site reset (§4).

### Step 2 — Is the dashboard responding?

```bash
tailscale ssh ghostport-admin@<device> -- gp-preflight
```

Read the output row-by-row. Any `✗` maps to a fix below.

| Failing row | Likely cause | Fix |
|-------------|-------------|-----|
| `ghostport-server` | Node server crashed | `sudo systemctl restart ghostport` — check logs with `journalctl -u ghostport -n 50` |
| `pihole-ftl` | Pi-hole FTL dead | `sudo systemctl restart pihole-FTL` |
| `hostapd` | WiFi AP down | `sudo systemctl restart hostapd` — customer will briefly lose WiFi |
| `tailscale` | **Stop. This is the remote lifeline.** | Do NOT restart. If it's down, the unit is already isolated — escalate to engineering. |
| `dashboard-http` | Port 4200 unreachable | Restart ghostport-server, check nftables didn't drop 4200 |
| `dns-resolution` | Upstream DNS broken | See §3.4 |
| `mode-consistency` | Routing/firewall mismatch — *incident class A* | See §3.2 |
| `nftables-loaded` | Firewall not loaded | `sudo systemctl restart ghostport-boot` — boot service re-applies saved mode |
| `wlan0-ap` | AP radio missing | Hardware issue; factory reset won't help |
| `wan-interface` | No eth0 link or no carrier | Customer cable/modem issue |
| `temperature` / `memory` / `disk` | Resource exhaustion | See §3.3 |

### Step 3 — Is the customer locked out of the dashboard?

Symptom: customer reports "login says N attempts remaining" or "locked out".

Server enforces 5 attempts → tiered lockout (1m, 2m, 5m, 15m cumulative). See §3.1.

---

## 3. Known Incident Classes

### 3.1 Dashboard Lockout (HTTP 401 loop)

**Symptom:** customer locked out after failed logins. Any script or browser that retries blindly on 401 will lock them out.

**Rule (OPERATOR-SOP §8 rule #15):** the "N attempts remaining" counter is *remaining*, not *total*. Stop on the first 401 and investigate.

**Recover over Tailscale:**

```bash
sudo rm /etc/phantom/lockout.json
sudo systemctl restart ghostport
```

If the customer also forgot the passcode:

```bash
sudo gp-passcode reset        # prints new random passcode once
```

Never use `gp-passcode show` — it does not exist (rule #14, plaintext was removed).

### 3.2 Mixed Mode State (routing / firewall / mode-file disagree)

**Incident of record:** 2026-04-02. Autonomous session left `/etc/phantom/current-mode=zerotrust` but actual routing went through wg0; `curl ifconfig.me` returned the EC2 EIP. All outbound LAN traffic tunneled incorrectly.

**Detection:**

```bash
gp-mode status                          # what it THINKS
ip route | head -5                      # what it's DOING
wg show                                 # tunnel reality
sudo nft list ruleset | head -3         # firewall reality
```

If any of the four disagree, you are in mixed state.

**Recover (order matters):**

```bash
sudo gp-mode isp                        # clean switch, brings wg0 down, restores eth0 default
# verify
curl -s ifconfig.me                     # should return ISP public IP, not 44.214.101.82
gp-mode status                          # now consistent
```

Only then attempt to re-enter DoubleHop/ZHop if that's what the customer wants. Never hand-edit routes, wg interfaces, or nftables in the field.

### 3.3 CPU Spike / Memory Pressure

**Detection:**

```bash
uptime                                  # 1-min load > 4× cores = overload
ps aux --sort=-pcpu | head -5
```

**Common culprits:**

1. **Parallel `gp-qa --security` runs** (2026-04-17 incident) — not a field issue unless operator is actively debugging. Emergency stop: `pkill -9 gitleaks trivy osemgrep`. See PYTHON-QA-SOP §9.5.
2. **Zombie TUI script from a missing `--help` guard** — `while true` loop pinning a core. Identify with `ps` and kill.
3. **Stale GTK overlay apps after long uptime** (2026-04-16 incident, cursor loss variant) — `pkill -f gp-widgets.py gp-desktop-icons.py gp-crowsnest.py gp-bulkhead.py && rm -f /tmp/gp-widgets.lock /tmp/gp-widgets.pid`, then relaunch if the customer uses the desktop.

### 3.4 DNS Breaks

```bash
dig @127.0.0.1 example.com +short       # Pi-hole local resolver
dig @10.66.67.1 example.com +short      # data-plane Unbound (tunnel modes)
```

| Failure | Fix |
|---------|-----|
| `@127.0.0.1` fails | `sudo systemctl restart pihole-FTL dnsmasq` |
| `@10.66.67.1` fails in DoubleHop/ZHop | wg1 down or EC2 Unbound down — switch to ISP via `sudo gp-mode isp` and escalate |
| Queries succeed but blocking broken | `sudo gp-dns-switch on` / `sudo gp-dns-upstream status` |

### 3.5 Dual-Tunnel Down (DoubleHop / ZHop)

Two tunnels must be up for these modes: wg0 (control, 10.66.66.0/24) and wg1 (data, 10.66.67.0/24).

```bash
wg show wg0 && wg show wg1              # both should have "latest handshake"
```

If either is stale (>3min since handshake):

```bash
sudo gp-mode isp                        # fail to safe
# then engineering triages EC2-side wg endpoints
```

### 3.6 Desktop / Cursor Loss (customer using GUI)

**Symptom:** customer says mouse cursor vanished, clicks don't land. Typical after multi-day uptime.

Over Tailscale (requires `DISPLAY` / `WAYLAND_DISPLAY` env):

```bash
pkill -f 'gp-widgets.py|gp-desktop-icons.py|gp-crowsnest.py|gp-bulkhead.py'
rm -f /tmp/gp-widgets.lock /tmp/gp-widgets.pid
# relaunch as ghostport-admin under the live Wayland session — see incident_2026_04_16_cursor.md
```

---

## 4. Hardware Recovery (GPIO Reset Button)

When every remote path is dark, walk the customer to the reset button.

### 4.1 10-second hold — Passcode Reset

- What it does: generates a fresh random passcode and prints it to the boot log / displays via the reset daemon.
- What it preserves: everything else (mode, pihole config, fleet enrollment, Tailscale).
- When to use: customer lost passcode but device is otherwise healthy.

### 4.2 30-second hold — Factory Reset

- What it does: wipes all `/etc/phantom/*.json` configs, kicks device back to ISP mode.
- What it preserves: **Tailscale enrollment and `fleet-auth.json`** — the unit stays manageable and known to fleet.
- When to use: device is in an unrecoverable state (corrupt config, mode lockup, forgotten passcode + broken dashboard).
- After: customer re-runs first-boot wizard for their own settings; no manufacturing re-provisioning required.

### 4.3 What factory reset does NOT fix

- Hardware failures (AP radio, Ethernet PHY, SD card bit rot)
- Tailscale daemon death (requires OS reinstall)
- Kernel panics visible only on serial console

For these, RMA path (§6).

---

## 5. Customer Log Collection (one-liner)

Ask the customer to run this over SSH, or run it yourself over Tailscale. It generates a single tarball you can pull back for engineering.

```bash
sudo bash -c '
  D=/tmp/gp-diag-$(date +%s)
  mkdir -p "$D"
  gp-preflight > "$D/preflight.txt" 2>&1
  gp-mode status > "$D/mode.txt" 2>&1
  ip route > "$D/routes.txt"
  ip -br addr > "$D/ifaces.txt"
  wg show > "$D/wg.txt" 2>&1
  nft list ruleset > "$D/nft.txt" 2>&1
  tailscale status > "$D/ts.txt" 2>&1
  journalctl -u ghostport -u ghostport-boot -u ghostport-sni -n 500 > "$D/journal.txt"
  dmesg -T | tail -500 > "$D/dmesg.txt"
  tar -czf "$D.tar.gz" -C /tmp "$(basename $D)"
  echo "Diag bundle: $D.tar.gz"
'
```

Pull it back:

```bash
tailscale file cp <device-tsname>:/tmp/gp-diag-*.tar.gz ./
```

No passcodes, API keys, or WireGuard private keys are captured — those live outside the included paths.

---

## 6. Escalation Ladder

| Class | Handle in field | Escalate to engineering |
|-------|----------------|------------------------|
| Passcode reset, service restart, mode stuck → ISP | Yes | Only if restart doesn't clear it |
| Dashboard 5xx that returns on restart | Yes | Only if it recurs within 24h |
| Mixed mode state (incident 2026-04-02) | Recover via `sudo gp-mode isp`, then escalate | Always — autonomous-session root cause needs review |
| Tailscale daemon dead | No | Always — remote lifeline is gone |
| Hardware failure (AP radio / Ethernet / SD) | No | RMA |
| DNS upstream broken in tunnel mode | Fail to ISP, then escalate | Always — EC2-side issue |
| Dual-tunnel down >5min | Fail to ISP, then escalate | Always |

**What engineering needs from you before escalation:**

1. Diag tarball (§5)
2. Device's tailnet name and last-known public IP
3. Time the customer first saw the problem
4. What field steps you already tried

---

## 7. Rules (Do Not Violate)

1. **Never stop `tailscale.service`.** It is the only always-on remote lifeline.
2. **Never hand-edit nftables, routing tables, or WireGuard interfaces** — use `gp-mode` only.
3. **ISP mode is the safe fallback** — no rollback timer. Use it whenever state is uncertain.
4. **The login counter shows *remaining* attempts, not total** — stop on the first 401.
5. **`gp-passcode show` does not exist.** Plaintext passcodes are never stored.
6. **Tailscale and `fleet-auth.json` survive factory reset** — this is intentional; do not "clean" them.
7. **Never apply a dev-image step to a customer unit.** Customer images have rpi-connect / dev keys stripped per GOLDEN-IMAGE-SOP — don't reinstall them.
