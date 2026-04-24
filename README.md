
# Phantom OS

> **Your data never leaves your hands.**

Phantom OS turns a Raspberry Pi 5 into a full privacy router with four switchable modes, Pi-hole ad blocking, WireGuard VPN routing, and forced DNS lockdown — all controllable from a browser or mobile app.

Unlike VPN services, **your traffic never passes through a third-party server**. You own the hardware. You own the network. You own the data.

> **License Notice:** Phantom OS is **source available**, not open source. The source code is publicly viewable and free to use for personal builds. Commercial use, resale, and redistribution as a competing product or service are not permitted under the Elastic License 2.0. See [LICENSE](LICENSE) for full terms.

---

## The Four Modes

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

## Features

- 4 privacy modes — switch in seconds from any browser
- Pi-hole integration — blocks ads, trackers, and malware domains network-wide
- DoH/DoT blocking — phones cannot bypass DNS filtering
- WireGuard VPN — fastest VPN protocol available
- Web dashboard — pirate-themed command deck on port 4200
- Mobile app — iOS & Android (coming soon)
- Auto-starts on boot — fully managed by systemd
- nftables firewall — modern, fast, battle-tested
- Always-on remote access — SSH & VNC never locked out regardless of mode

---

## Hardware Requirements

| Component | Spec | Required |
|-----------|------|----------|
| Raspberry Pi 5 | 4GB or 8GB RAM | Required |
| MicroSD Card | 32GB+ Class 10 / A2 | Required |
| USB-C Power Supply | 27W official Pi 5 adapter | Required |
| USB Ethernet Adapter | USB 3.0 gigabit | Required |
| PCIe Ethernet HAT | Pi 5 compatible | Recommended |
| Case with cooling | Active cooling | Recommended |

> Want a pre-built kit? Buy everything you need in one box at [ghostporttechnologies.com](https://ghostporttechnologies.com)

---

## Quick Install

```bash
# 1. Clone the repo
git clone https://github.com/GhostPortTechnologies/Ghostport-Phantom-OS.git
cd Ghostport-Phantom-OS

# 2. Run the installer
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

The installer handles everything: dependencies, Pi-hole, nft profiles, dashboard, and systemd services.

---

## Repository Structure

```
Ghostport-Phantom-OS/
├── etc/gpmodes/            # nftables firewall profiles
│   ├── common.nft          # shared variables & management rules
│   ├── isp.nft             # ISP passthrough mode
│   ├── zerotrust.nft       # Pi-hole + DNS lockdown mode
│   ├── doublehop.nft       # WireGuard VPN mode
│   └── zhop.nft            # WireGuard + DNS lockdown mode
├── scripts/                # system scripts installed to /usr/local/bin/gp-*
├── systemd/                # systemd unit files
├── ghostport-server.js     # Node.js Express API server (port 4200)
├── public/                 # dashboard SPA (vanilla JS)
├── desktop/                # GTK3 desktop apps (labwc/Wayland)
├── daemons/                # background daemons (watchdogs, guards)
├── docs/                   # engineer docs, SOPs, per-app references
├── compliance/             # compliance docs
├── SECURITY.md             # threat model + vulnerability reporting
├── CONTRIBUTING.md         # how to contribute
├── LICENSE                 # Elastic License 2.0
└── README.md
```

---

## API

The Phantom OS API runs on port 4200. See `ghostport-server.js` for all endpoints — key ones documented inline with JSDoc.

```bash
# Get current status
curl http://YOUR_PI_IP:4200/api/status

# Switch to Z-HOP mode
curl -X POST http://YOUR_PI_IP:4200/api/mode \
  -H "Content-Type: application/json" \
  -d '{"mode":"zhop"}'
```

---

## Built With

Phantom OS is built on top of these outstanding projects:

| Project | License | What it does |
|---------|---------|-------------|
| [Pi-hole](https://pi-hole.net) | EUPL-1.2 | Network-wide DNS ad blocking |
| [WireGuard](https://wireguard.com) | GPL-2.0 | Fast, modern VPN tunneling |
| [nftables](https://netfilter.org/projects/nftables/) | GPL-2.0 | Linux firewall and packet filtering |
| [Node.js](https://nodejs.org) | MIT | Dashboard API server runtime |
| [Raspberry Pi OS](https://raspberrypi.com/software/) | Various | Underlying operating system |

GhostPort Technologies does not claim ownership of any of the above projects. All trademarks belong to their respective owners.

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a pull request.

- Bug reports — open an issue
- Feature requests — open an issue with the `enhancement` label
- Pull requests — fork, branch, and submit

---

## License

Phantom OS is licensed under the **Elastic License 2.0 (ELv2)**.

**You are free to:**
- View and study the source code
- Build and run Phantom OS for personal use on your own hardware
- Contribute improvements back to this repository

**You are NOT permitted to:**
- Sell, resell, or commercially distribute Phantom OS or derivatives
- Offer Phantom OS as a hosted or managed service to third parties
- Remove or alter licensing or copyright notices

This is **source available** software, not open source. The distinction matters — see [elastic.co/licensing/elastic-license](https://www.elastic.co/licensing/elastic-license) for full terms.

For commercial licensing inquiries contact: licensing@ghostporttechnologies.com

---

## Support

- Docs — [docs/ in this repository](./docs)
- Discord — [discord.gg/ghostport](https://discord.gg/ghostport)
- Blog — [blog.ghostporttechnologies.com](https://blog.ghostporttechnologies.com)
- Buy a Kit — [www.ghostporttechnologies.com](https://www.ghostporttechnologies.com)
- Email — support@ghostporttechnologies.com

---

<div align="center">

**Phantom OS**

*Your data never leaves your hands.*

</div>
