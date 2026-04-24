# GhostPort Disaster Recovery Plan

**Last updated**: 2026-04-09
**Golden image**: ghostport-os-20260409.img.gz (verified 3x, on thumb drive)

---

## Quick Reference — "Everything is broken, now what?"

| Scenario | Fix |
|----------|-----|
| Dashboard down | `sudo systemctl restart ghostport` |
| DNS not working | `sudo systemctl restart pihole-FTL` |
| WiFi AP gone | `sudo systemctl restart hostapd` |
| Can't reach device at all | SSH via Tailscale (see below) |
| Tailscale also dead | Physical access, plug in monitor + keyboard |
| SD card corrupted | Flash backup from thumb drive (see below) |
| Need to rebuild from scratch | Full procedure at bottom of this doc |

---

## 1. SSH Access

### Primary: Tailscale (works from anywhere)
```bash
ssh ghostport-admin@100.93.206.60
```
- Key-based auth only (no passwords)
- Works regardless of which mode the Pi is in
- Works even if WireGuard tunnels are down
- Tailscale is NEVER stopped — it's the emergency management plane

### Secondary: LAN (must be on GhostPort WiFi)
```bash
ssh ghostport-admin@192.168.50.1
```
- Connect to WiFi SSID "Incognito" first
- Same key-based auth

### SSH to EC2 (requires tunnel mode)
```bash
ssh -i ~/.ssh/ghostport-ec2.pem ubuntu@10.66.66.1
```
- Only works when wg0 is up (DoubleHop or ZHop mode)

### SSH Config
- Root login: disabled
- Password auth: disabled
- Max auth tries: 3
- X11 forwarding: disabled
- Authorized keys: `~/.ssh/authorized_keys`

---

## 2. Restoring from Backup

### What you need
- The thumb drive (SanDisk 58GB, label D7D9-0175)
- A computer with an SD card reader (or USB adapter)
- The Pi's SD card

### Backup files on thumb drive
```
ghostport-os-20260409.img.gz.aa  (3.9 GB)
ghostport-os-20260409.img.gz.ab  (3.9 GB)
ghostport-os-20260409.img.gz.ac  (3.0 GB)
```

### Restore procedure

**Option A: From another Linux machine**
```bash
# Insert SD card, find device (usually /dev/sdX or /dev/mmcblkX)
lsblk

# Mount thumb drive
sudo mount /dev/sdY1 /mnt

# Flash the image (REPLACE /dev/sdX with your actual SD card device)
cat /mnt/ghostport-os-20260409.img.gz.* | gunzip | sudo dd of=/dev/sdX bs=4M status=progress

# Sync and eject
sync
sudo eject /dev/sdX
```

**Option B: From a Mac**
```bash
# Find the SD card device
diskutil list

# Unmount (NOT eject) the SD card
diskutil unmountDisk /dev/diskN

# Flash (use rdiskN for faster writes)
cat /Volumes/D7D9-0175/ghostport-os-20260409.img.gz.* | gunzip | sudo dd of=/dev/rdiskN bs=4m

# Eject
diskutil eject /dev/diskN
```

**Option C: From Windows**
- Use WSL2 or install balenaEtcher
- In WSL2: `cat` + `gunzip` + `dd` as in Option A
- With Etcher: first combine and decompress on the thumb drive, then flash the .img

### After flashing
1. Insert SD card into Pi, power on
2. Wait 60-90 seconds for boot
3. Connect to WiFi "Incognito" or SSH via Tailscale
4. Verify: `cat /etc/os-release` should show "Phantom OS 1.0 (Seadevil)"
5. Check mode: `gp-mode status`
6. The self-healing guards will auto-correct any DNS/service issues within 60 seconds

---

## 3. Claude Code — Reinstallation

### Install Claude Code
```bash
# Install via npm (requires Node.js 20+)
npm install -g @anthropic-ai/claude-code

# Or via the official installer
curl -fsSL https://claude.ai/install.sh | bash
```

### Claude Memory Location
All memory files are stored at:
```
/home/ghostport-admin/.claude/projects/-home-ghostport-admin/memory/
```

**This directory is included in the SD card backup.** After restoring from the thumb drive, all memory files will be intact.

### Memory file index
The index is at:
```
/home/ghostport-admin/.claude/projects/-home-ghostport-admin/memory/MEMORY.md
```

### Key memory files
| File | What it contains |
|------|-----------------|
| `MEMORY.md` | Index of all memory files |
| `user_background.md` | Who you are, how you work |
| `user_contact.md` | Contact info, notification prefs |
| `feedback_*.md` | Your corrections and preferences (12 files) |
| `reference_ec2_bridge.md` | EC2 access, bridge API, fleet endpoints |
| `reference_moltbook.md` | Moltbook account details |
| `project_roadmap.md` | Full product roadmap |
| `session_2026_*.md` | Session histories (18 files) |
| `incident_*.md` | Incident reports |

### If memory is lost
If the SD card is unrecoverable and memory files are gone:
1. The thumb drive backup contains them (from 2026-04-09)
2. The GitHub repo at `GhostPortTechnologies/Ghostport-Phantom-OS` may have copies
3. Claude Code will need to rebuild context from the codebase + CLAUDE.md files
4. Key context: read `/home/ghostport-admin/CLAUDE.md` and `/opt/phantom/CLAUDE.md`

### Claude Code config
```
/home/ghostport-admin/.claude/settings.json       — global settings
/home/ghostport-admin/.claude/settings.local.json  — local overrides
```

---

## 4. Full Rebuild from Scratch

If you need to build a completely new GhostPort device:

### Step 1: Flash base OS
1. Download Raspberry Pi OS Lite (64-bit) from raspberrypi.com
2. Flash to SD card with Raspberry Pi Imager
3. Enable SSH in imager settings, set username `ghostport-admin`

### Step 2: Boot and connect
```bash
# Find the Pi on your network
ping ghostport.local

# SSH in
ssh ghostport-admin@<ip>
```

### Step 3: Install GhostPort software
```bash
# Clone the repo
git clone https://github.com/GhostPortTechnologies/Ghostport-Phantom-OS.git /opt/phantom

# Run the image builder
sudo /opt/phantom/scripts/gp-build-image --confirm

# Install Node.js dependencies
cd /opt/phantom && npm install

# Copy scripts to system paths
sudo cp /opt/phantom/scripts/gp-* /usr/local/bin/
sudo chmod 755 /usr/local/bin/gp-*

# Copy nftables profiles
sudo cp /opt/phantom/etc/gpmodes/*.nft /etc/gpmodes/

# Copy systemd services
sudo cp /opt/phantom/systemd/*.service /etc/systemd/system/
sudo cp /opt/phantom/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

### Step 4: Install dependencies not in repo
```bash
# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Pi-hole
curl -sSL https://install.pi-hole.net | bash

# cloudflared (check current install method at developers.cloudflare.com)
```

### Step 5: Configure
```bash
# Set up WireGuard tunnels (need keys from EC2)
sudo cp wg0.conf wg1.conf /etc/wireguard/

# Set up hostapd
sudo cp /opt/phantom/etc/hostapd.conf /etc/hostapd/hostapd.conf

# Provision the device
sudo gp-provision

# Enable services
sudo systemctl enable --now ghostport ghostport-boot ghostport-dns-guard.timer ghostport-health-guard.timer

# Set initial mode
sudo gp-mode isp
```

### Step 6: Install Claude Code
```bash
npm install -g @anthropic-ai/claude-code

# Restore memory from backup thumb drive
cp -r /media/ghostport-admin/D7D9-0175/claude-memory-backup/* \
  /home/ghostport-admin/.claude/projects/-home-ghostport-admin/memory/
```

---

## 5. Service Recovery Commands

```bash
# Full service restart (safe order)
sudo systemctl restart pihole-FTL
sudo systemctl restart hostapd
sudo systemctl restart ghostport
sudo systemctl restart dnsmasq

# Mode reset to safe fallback
sudo gp-mode isp

# WireGuard tunnel restart
sudo gp-mode doublehop --no-rollback

# Force DNS upstream reset
sudo gp-dns-upstream $(cat /etc/phantom/current-mode)

# Check self-healing guard status
sudo journalctl -t gp-dns-guard --since "10 min ago"
sudo journalctl -t gp-health-guard --since "10 min ago"

# View health state
cat /etc/phantom/health-state.json | python3 -m json.tool

# Nuclear option: reboot
sudo reboot
# ghostport-boot.service will restore the saved mode after reboot
```

---

## 6. Critical Files to Never Lose

| File | Why |
|------|-----|
| `/etc/wireguard/wg0.conf` | Control plane tunnel keys |
| `/etc/wireguard/wg1.conf` | Data plane tunnel keys |
| `/etc/phantom/fleet-auth.json` | Fleet auth, bridge tokens, secrets |
| `/etc/phantom/auth.json` | Dashboard passcode + TOTP |
| `/opt/phantom/ssl/` | SSL certificates |
| `~/.ssh/ghostport-ec2.pem` | EC2 SSH key |
| `~/.ssh/authorized_keys` | SSH access keys |
| `~/.claude/projects/-home-ghostport-admin/memory/` | Claude memory |

**All of these are included in the thumb drive backup.**

---

## 7. Contacts

- **Tailnet**: thomasestrada915@ (all devices on same tailnet)
- **EC2 Claude**: Messages via `sudo gp-bridge send text "message"` (requires tunnel mode)
- **Fleet API**: http://10.66.66.1:8080 (via wg0)
- **Public API**: https://api.ghostporttechnologies.com
