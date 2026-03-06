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

echo ""
echo "  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠"
echo "       GhostPort OS Installer v1.0"
echo "    Your data never leaves your hands."
echo "  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠  ☠"
echo ""

# ── Step 1: System update ────────────────────────────────────
section "Step 1/7 — System Update"
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl wget git nftables wireguard
log "System updated."

# ── Step 2: Node.js ──────────────────────────────────────────
section "Step 2/7 — Node.js"
if ! command -v node &>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y nodejs >/dev/null
  log "Node.js $(node -v) installed."
else
  log "Node.js already installed: $(node -v)"
fi

# ── Step 3: Install gp-mode ──────────────────────────────────
section "Step 3/7 — GhostPort Mode Switcher"
mkdir -p /etc/gpmodes
cp modes/*.nft /etc/gpmodes/
cp scripts/gp-mode /usr/local/bin/gp-mode
chmod +x /usr/local/bin/gp-mode
echo "isp" > /tmp/gp-current-mode
log "gp-mode installed."

# ── Step 4: Dashboard ────────────────────────────────────────
section "Step 4/7 — GhostPort Dashboard"
mkdir -p /opt/ghostport/public
cp dashboard/server/ghostport-server.js /opt/ghostport/
cp dashboard/public/index.html /opt/ghostport/public/

cd /opt/ghostport
npm init -y >/dev/null
npm install express cors >/dev/null
log "Dashboard installed at /opt/ghostport"

# ── Step 5: Sudoers ──────────────────────────────────────────
section "Step 5/7 — Permissions"
CURRENT_USER="${SUDO_USER:-$(whoami)}"
echo "$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/local/bin/gp-mode, /usr/sbin/nft, /usr/bin/wg" \
  > /etc/sudoers.d/ghostport
chmod 440 /etc/sudoers.d/ghostport
log "Sudoers configured for $CURRENT_USER"

# ── Step 6: Systemd services ─────────────────────────────────
section "Step 6/7 — Autostart Services"

# Enable nftables
systemctl enable nftables >/dev/null 2>&1 || true

# GhostPort dashboard service
cat > /etc/systemd/system/ghostport.service <<EOF
[Unit]
Description=GhostPort Command Deck
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=/opt/ghostport
ExecStart=/usr/bin/node /opt/ghostport/ghostport-server.js
Restart=always
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ghostport >/dev/null
systemctl restart ghostport
log "GhostPort service enabled and started."

# ── Step 7: Apply ISP mode ───────────────────────────────────
section "Step 7/7 — Applying Default Mode"
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
echo "  Commands:"
echo "    sudo gp-mode isp"
echo "    sudo gp-mode zerotrust"
echo "    sudo gp-mode doublehop"
echo "    sudo gp-mode zhop"
echo "    sudo gp-mode status"
echo ""
echo "  ☠  Your data never leaves your hands.  ☠"
echo ""
