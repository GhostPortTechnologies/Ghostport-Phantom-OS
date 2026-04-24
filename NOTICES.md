# NOTICES

## Phantom OS — Third Party Notices & Legal Disclosures

Copyright (c) 2026 GhostPort Technologies
All rights reserved.

Phantom OS is an original work of GhostPort Technologies. The Phantom OS
source code, configuration system, mode-switching architecture, web dashboard,
and associated scripts are the exclusive intellectual property of GhostPort
Technologies, protected under applicable copyright law.

GhostPort Technologies makes no claim of ownership over any third-party
software, tools, libraries, or technologies referenced herein. All third-party
software remains the property of its respective authors and is governed
exclusively by its own license terms.

---

## Third Party Software Notices

The following third-party software components are used by or referenced in
Phantom OS. Their inclusion does not imply endorsement by their respective
authors of GhostPort Technologies or its products, nor does it imply any claim
of ownership by GhostPort Technologies over said software.

---

### Pi-hole
- **Project:** Pi-hole Network-wide Ad Blocker
- **Website:** https://pi-hole.net
- **License:** European Union Public License 1.2 (EUPL-1.2)
- **License URL:** https://eupl.eu/1.2/en/
- **Copyright:** Pi-hole LLC and contributors
- **Usage:** Phantom OS optionally integrates with Pi-hole as an independent,
  separately installed DNS filtering service. GhostPort Technologies does not
  distribute, modify, or sublicense Pi-hole software. Users who install Pi-hole
  do so under Pi-hole's own license terms. GhostPort Technologies makes no
  warranties regarding Pi-hole software.

---

### WireGuard
- **Project:** WireGuard — Fast, Modern, Secure VPN Tunnel
- **Website:** https://wireguard.com
- **License:** GNU General Public License v2.0 (GPL-2.0)
- **License URL:** https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
- **Copyright:** Jason A. Donenfeld and contributors
- **Trademark Notice:** "WireGuard" is a registered trademark of Jason A. Donenfeld.
- **Usage:** Phantom OS provides configuration scripts that interact with
  WireGuard as an independently installed system service. GhostPort Technologies
  does not distribute, bundle, or sublicense WireGuard software. WireGuard is
  installed and operated under its own license terms, independent of this
  software. The GPL-2.0 license governing WireGuard is available at the URL
  above and is unaffected by the Elastic License 2.0 governing Phantom OS.

---

### nftables
- **Project:** nftables — Netfilter tables
- **Website:** https://netfilter.org/projects/nftables/
- **License:** GNU General Public License v2.0 (GPL-2.0)
- **License URL:** https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
- **Copyright:** The Netfilter Project and contributors
- **Usage:** Phantom OS includes original nftables rule configuration files
  (.nft profiles) authored by GhostPort Technologies. These configuration files
  are text-based rule definitions and do not constitute a derivative work of
  nftables itself. The nftables software is a standard Linux kernel subsystem
  installed independently via the operating system package manager and governed
  by its own GPL-2.0 license, entirely separate from this software.

---

### Node.js
- **Project:** Node.js JavaScript Runtime
- **Website:** https://nodejs.org
- **License:** MIT License
- **License URL:** https://github.com/nodejs/node/blob/main/LICENSE
- **Copyright:** Node.js contributors
- **Usage:** Phantom OS includes an original Node.js application
  (ghostport-server.js) authored by GhostPort Technologies. Node.js itself is
  installed independently and governed by the MIT License. The MIT License
  governing Node.js is unaffected by the Elastic License 2.0 governing
  Phantom OS.

---

### Express.js
- **Project:** Express — Fast, unopinionated, minimalist web framework for Node.js
- **Website:** https://expressjs.com
- **License:** MIT License
- **License URL:** https://github.com/expressjs/express/blob/master/LICENSE
- **Copyright:** TJ Holowaychuk and contributors
- **Usage:** Used as a dependency of the GhostPort dashboard server application.
  Installed via npm under its own MIT License terms.

---

### Raspberry Pi OS
- **Project:** Raspberry Pi OS (formerly Raspbian)
- **Website:** https://raspberrypi.com/software/
- **License:** Various (Debian-based, see https://www.raspberrypi.com/software/)
- **Copyright:** Raspberry Pi Ltd and respective package authors
- **Trademark Notice:** "Raspberry Pi" is a trademark of Raspberry Pi Ltd.
- **Usage:** Phantom OS is designed to run on Raspberry Pi hardware and
  Raspberry Pi OS. GhostPort Technologies is not affiliated with, endorsed by,
  or sponsored by Raspberry Pi Ltd. The Raspberry Pi trademark is used solely
  for the purpose of hardware compatibility identification.

---

## Scope of Phantom OS License

The Elastic License 2.0 governing Phantom OS applies exclusively to the
original works authored by GhostPort Technologies, including but not limited to:

- The gp-mode mode-switching script and architecture
- The nft firewall profile files (.nft configuration files)
- The GhostPort dashboard web interface (index.html)
- The GhostPort API server (ghostport-server.js)
- The GhostPort installer script (install.sh)
- All associated documentation authored by GhostPort Technologies

The Elastic License 2.0 does not and cannot supersede, modify, or restrict the
licenses governing any third-party software listed in this document. Each
third-party component remains governed solely by its own license.

---

## No Warranty

Phantom OS is provided "as is", without warranty of any kind, express or
implied. GhostPort Technologies makes no warranty that the software will meet
your requirements, operate without interruption, or be free of errors.
GhostPort Technologies shall not be liable for any damages arising from the
use of this software, including but not limited to direct, indirect, incidental,
special, exemplary, or consequential damages.

---

## Export Compliance

Phantom OS incorporates cryptographic software (WireGuard VPN). Users are
responsible for compliance with all applicable export control laws and
regulations in their jurisdiction. GhostPort Technologies makes no
representations regarding the export compliance of this software.

---

## Contact

For licensing inquiries: licensing@ghostporttechnologies.com
For legal notices: legal@ghostporttechnologies.com
For general support: support@ghostporttechnologies.com

GhostPort Technologies
https://ghostporttechnologies.com

---

*This document was last updated: March 2026*
