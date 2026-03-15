#!/bin/bash
# ╔═══════════════════════════════════════════════════════════╗
# ║           GhostPort OS — One Shot Installer               ║
# ║           https://github.com/ghostport-os/ghostport-os    ║
# ╚═══════════════════════════════════════════════════════════╝

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
BOLD='\033[1m'

log()     { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*"; exit 1; }
section() { echo -e "\n${BOLD}☠  $*${NC}\n"; }

# Must run as root
[[ $EUID -eq 0 ]] || error "Run this script with sudo: sudo ./install.sh"

# Must be on a Raspberry Pi
grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null || warn "Not detected as Raspberry Pi — continuing anyway."

CURRENT_USER="${SUDO_USER:-$(whoami)}"
USER_HOME=$(eval echo "~$CURRENT_USER")

echo ""
echo "  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠"
echo "       GhostPort OS Installer v1.1"
echo "    Your data never leaves your hands."
echo "  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠"
echo ""

# ── Step 1: System update ────────────────────────────────────
section "Step 1/9 — System Update"
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl wget git nftables wireguard openssl
log "System updated."

# ── Step 2: Node.js ──────────────────────────────────────────
section "Step 2/9 — Node.js"
if ! command -v node &>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y nodejs >/dev/null
  log "Node.js $(node -v) installed."
else
  log "Node.js already installed: $(node -v)"
fi

# ── Step 3: Install gp-mode and tools ────────────────────────
section "Step 3/9 — GhostPort Mode Switcher & Tools"
mkdir -p /etc/gpmodes
cp etc/gpmodes/*.nft /etc/gpmodes/
chmod 644 /etc/gpmodes/*.nft

for script in gp-mode gp-mode-boot gp-dns-switch gp-passcode gp-new; do
  if [ -f "scripts/$script" ]; then
    cp "scripts/$script" "/usr/local/bin/$script"
    chmod 755 "/usr/local/bin/$script"
    log "Installed $script"
  fi
done

mkdir -p /etc/ghostport
echo "isp" > /etc/ghostport/current-mode
log "Mode switcher installed."

# ── Step 4: Dashboard ────────────────────────────────────────
section "Step 4/9 — GhostPort Dashboard"
mkdir -p /opt/ghostport/public
mkdir -p /opt/ghostport/ssl

cp ghostport-server.js /opt/ghostport/
chmod 644 /opt/ghostport/ghostport-server.js

for f in index.html login.html arsenal.js pwa.html manifest.json logo.png; do
  if [ -f "public/$f" ]; then
    cp "public/$f" "/opt/ghostport/public/$f"
  fi
done
chmod 644 /opt/ghostport/public/*

cd /opt/ghostport
npm init -y >/dev/null
npm install express cors >/dev/null
log "Dashboard installed at /opt/ghostport"

# ── Step 5: SSL Certificate ──────────────────────────────────
section "Step 5/9 — SSL Certificate"
if [ ! -f /opt/ghostport/ssl/ghostport.crt ]; then
  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout /opt/ghostport/ssl/ghostport.key \
    -out /opt/ghostport/ssl/ghostport.crt \
    -days 3650 \
    -subj "/CN=ghostport.local" \
    -addext "subjectAltName=DNS:ghostport.local,DNS:localhost,IP:127.0.0.1,IP:192.168.50.1" \
    2>/dev/null
  chmod 640 /opt/ghostport/ssl/ghostport.key
  chown root:$CURRENT_USER /opt/ghostport/ssl/ghostport.key
  log "SSL certificate generated with SANs."
else
  log "SSL certificate already exists — skipping."
fi

# ── Step 6: Sudoers (hardened) ────────────────────────────────
section "Step 6/9 — Permissions"
cat > /etc/sudoers.d/010_ghostport-hardened <<'SUDOERS'
# GhostPort — minimal sudo privileges
Defaults    env_reset
Defaults    secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Mode switching & network tools
ghostport-admin ALL=(ALL) NOPASSWD: /usr/local/bin/gp-mode
ghostport-admin ALL=(ALL) NOPASSWD: /usr/local/bin/gp-mode-boot
ghostport-admin ALL=(ALL) NOPASSWD: /usr/local/bin/gp-dns-switch
ghostport-admin ALL=(ALL) NOPASSWD: /usr/local/bin/gp-passcode
ghostport-admin ALL=(ALL) NOPASSWD: /usr/local/bin/gp-new

# Firewall & tunnels
ghostport-admin ALL=(ALL) NOPASSWD: /usr/sbin/nft
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/wg
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/wg-quick
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/tailscale

# Service management (specific services only)
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart hostapd
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart unbound
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart ghostport
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable lightdm
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable lightdm
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/systemctl set-default *

# DNS & Pi-hole
ghostport-admin ALL=(ALL) NOPASSWD: /usr/local/bin/pihole
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/unbound-control

# Conntrack flush
ghostport-admin ALL=(ALL) NOPASSWD: /usr/sbin/conntrack

# System tools used by repair endpoints
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/resolvectl flush-caches
ghostport-admin ALL=(ALL) NOPASSWD: /usr/sbin/reboot
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/apt-get update
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/apt-get upgrade *
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/cron.d/ghostport-schedules
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/touch /etc/cron.d/ghostport-schedules
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/chmod 644 /etc/cron.d/ghostport-schedules
ghostport-admin ALL=(ALL) NOPASSWD: /usr/bin/sed -i * /etc/cron.d/*
SUDOERS
chmod 440 /etc/sudoers.d/010_ghostport-hardened
log "Hardened sudoers configured for $CURRENT_USER"

# ── Step 7: Systemd services ─────────────────────────────────
section "Step 7/9 — Autostart Services"

systemctl enable nftables >/dev/null 2>&1 || true

for svc in systemd/*.service; do
  if [ -f "$svc" ]; then
    cp "$svc" /etc/systemd/system/
    log "Installed $(basename $svc)"
  fi
done

systemctl daemon-reload
systemctl enable ghostport >/dev/null
systemctl enable ghostport-boot >/dev/null 2>&1 || true
systemctl restart ghostport
log "GhostPort services enabled and started."

# ── Step 8: Desktop Setup ────────────────────────────────────
section "Step 8/9 — Desktop Shortcuts & Wallpaper"

# Icon
ICON_DIR="$USER_HOME/.local/share/icons"
mkdir -p "$ICON_DIR"
if [ -f public/logo.png ]; then
  cp public/logo.png "$ICON_DIR/ghostport.png"
  chown "$CURRENT_USER:$CURRENT_USER" "$ICON_DIR/ghostport.png"
fi

# Wallpaper
if [ -f public/wallpaper.png ]; then
  cp public/wallpaper.png /usr/share/rpd-wallpaper/ghostport.png
  # Update desktop config if pcmanfm is in use
  DESKTOP_CONF="$USER_HOME/.config/pcmanfm/LXDE-pi"
  if [ -d "$DESKTOP_CONF" ]; then
    for conf in "$DESKTOP_CONF"/desktop-items-*.conf; do
      [ -f "$conf" ] && sed -i 's|wallpaper=.*|wallpaper=/usr/share/rpd-wallpaper/ghostport.png|' "$conf"
    done
    log "Wallpaper set."
  fi
fi

# Desktop shortcuts
DESKTOP_DIR="$USER_HOME/Desktop"
APP_DIR="$USER_HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR" "$APP_DIR"

cat > "$DESKTOP_DIR/ghostport.desktop" <<EOF
[Desktop Entry]
Name=GhostPort
Comment=GhostPort Command Deck
Exec=firefox --kiosk http://localhost:4200
Icon=$ICON_DIR/ghostport.png
Type=Application
Categories=Network;
Terminal=false
StartupNotify=true
EOF

cat > "$DESKTOP_DIR/ghostport-webapp.desktop" <<EOF
[Desktop Entry]
Name=GhostPort Web
Comment=GhostPort Command Deck (Browser)
Exec=firefox --new-window http://localhost:4200
Icon=$ICON_DIR/ghostport.png
Type=Application
Categories=Network;
Terminal=false
StartupNotify=true
EOF

chmod 750 "$DESKTOP_DIR"/ghostport*.desktop
cp "$DESKTOP_DIR"/ghostport*.desktop "$APP_DIR/"
chown -R "$CURRENT_USER:$CURRENT_USER" "$DESKTOP_DIR"/ghostport*.desktop "$APP_DIR"/ghostport*.desktop
log "Desktop shortcuts installed."

# ── Step 9: Apply ISP mode ───────────────────────────────────
section "Step 9/9 — Applying Default Mode"
gp-mode isp
log "Default mode: ISP"

# ── Done ─────────────────────────────────────────────────────
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠"
echo ""
echo "  ✓ GhostPort OS installed successfully!"
echo ""
echo "  Dashboard → http://$PI_IP:4200"
echo "  Pi-hole   → http://$PI_IP/admin"
echo ""
echo "  Next steps:"
echo "    sudo gp-new    — Generate device passcode"
echo "    sudo gp-mode status"
echo ""
echo "  ☠  Your data never leaves your hands.  ☠"
echo ""
