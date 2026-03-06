
# ☠ GhostPort OS

> **Your data never leaves your hands.**

GhostPort OS is an open-source privacy router firmware built on Raspberry Pi 5. It gives everyday people military-grade network privacy through a beautiful web dashboard — no technical knowledge required.

**Blow NordVPN out of the water. Own your privacy.**

---

## ✦ What is GhostPort?

GhostPort turns a Raspberry Pi 5 into a full privacy router with four switchable modes, Pi-hole ad blocking, WireGuard VPN routing, and forced DNS lockdown — all controllable from a browser or mobile app.

Unlike VPN services, **your traffic never passes through a third-party server**. You own the hardware. You own the network. You own the data.

---

## ✦ The Four Modes

| Mode | Name | What it does |
|------|------|-------------|
| ⚓ | **ISP** — Open Waters | Clean passthrough. Full speed, no filtering. |
| 👻 | **Zero Trust** — Ghost Cloak | Pi-hole DNS blocking + forced DNS, blocks DoH/DoT. |
| 💀 | **Double Hop** — Dead Man's Route | All traffic tunneled through WireGuard VPN. |
| 🏴‍☠️ | **Z-HOP** — Davy Jones | WireGuard + strict DNS lockdown. Maximum stealth. |

Switch modes instantly from the web dashboard or with a single command:

```bash
sudo gp-mode zhop
```

---

## ✦ Features

- 🔒 **4 privacy modes** — switch in seconds from any browser
- 🚫 **Pi-hole integration** — blocks ads, trackers, and malware domains network-wide
- 🛡 **DoH/DoT blocking** — phones cannot bypass DNS filtering
- ⚡ **WireGuard VPN** — fastest VPN protocol available
- 🌐 **Web dashboard** — gorgeous pirate-themed command deck on port 4200
- 📱 **Mobile app** — iOS & Android (coming soon)
- 🔁 **Auto-starts on boot** — fully managed by systemd
- 🧱 **nftables firewall** — modern, fast, battle-tested
- 🔑 **Always-on remote access** — SSH & VNC never locked out regardless of mode

---

## ✦ Hardware Requirements

| Component | Spec | Required |
|-----------|------|----------|
| Raspberry Pi 5 | 4GB or 8GB RAM | ✅ Required |
| MicroSD Card | 32GB+ Class 10 / A2 | ✅ Required |
| USB-C Power Supply | 27W official Pi 5 adapter | ✅ Required |
| USB Ethernet Adapter | USB 3.0 gigabit | ✅ Required |
| PCIe Ethernet HAT | Pi 5 compatible | ⭐ Recommended |
| Case with cooling | Active cooling | ⭐ Recommended |

> 💡 **Want a pre-built kit?** Buy everything you need in one box at [ghostport.io](https://ghostport.io)

---

## ✦ Quick Install

```bash
# 1. Clone the repo
git clone https://github.com/ghostport-os/ghostport-os.git
cd ghostport-os

# 2. Run the installer
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

The installer handles everything: dependencies, Pi-hole, nft profiles, dashboard, and systemd services.

For a full step-by-step guide see [docs/INSTALL.md](docs/INSTALL.md)

---

## ✦ Repository Structure

```
ghostport-os/
├── modes/                  # nft firewall profiles
│   ├── common.nft          # shared variables & management rules
│   ├── isp.nft             # ISP passthrough mode
│   ├── zerotrust.nft       # Pi-hole + DNS lockdown mode
│   ├── doublehop.nft       # WireGuard VPN mode
│   └── zhop.nft            # WireGuard + DNS lockdown mode
├── scripts/
│   ├── gp-mode             # main mode switcher script
│   └── install.sh          # one-shot installer
├── dashboard/
│   ├── server/
│   │   └── ghostport-server.js   # Node.js API server
│   └── public/
│       └── index.html            # web UI
├── docs/
│   ├── INSTALL.md          # full installation guide
│   ├── MODES.md            # mode documentation
│   ├── API.md              # API reference
│   └── TROUBLESHOOTING.md  # common issues
├── LICENSE                 # GhostPort Business Source License
└── README.md
```

---

## ✦ API

The GhostPort API runs on port 4200. Full documentation at [docs/API.md](docs/API.md)

```bash
# Get current status
curl http://YOUR_PI_IP:4200/api/status

# Switch to Z-HOP mode
curl -X POST http://YOUR_PI_IP:4200/api/mode \
  -H "Content-Type: application/json" \
  -d '{"mode":"zhop"}'
```

---

## ✦ Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a pull request.

- 🐛 **Bug reports** — open an issue
- 💡 **Feature requests** — open an issue with the `enhancement` label
- 🔧 **Pull requests** — fork, branch, and submit

---

## ✦ License

GhostPort OS is licensed under the **GhostPort Business Source License 1.0**.

**You are free to:**
- View, study, and learn from the source code
- Build and run GhostPort OS for personal use
- Contribute improvements back to this repository

**You are NOT permitted to:**
- Sell, resell, or commercially distribute GhostPort OS or derivatives
- Offer GhostPort OS as a hosted or managed service
- Remove or modify license headers or attribution

> After 4 years from each release date, that version's code converts to the MIT License.

See [LICENSE](LICENSE) for full terms.

---

## ✦ Support

- 📖 **Docs** — [ghostport.io/docs](https://ghostport.io/docs)
- 💬 **Discord** — [discord.gg/ghostport](https://discord.gg/ghostport)
- 🛒 **Buy a Kit** — [ghostport.io](https://ghostport.io)
- 📧 **Email** — support@ghostport.io

---

<div align="center">

☠ &nbsp; **GhostPort OS** &nbsp; ☠

*Your data never leaves your hands.*

</div>
