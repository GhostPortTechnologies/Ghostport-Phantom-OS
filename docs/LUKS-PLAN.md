# LUKS Disk Encryption — Planning Doc

**Status:** DECISION REQUIRED — not yet implemented
**Date:** 2026-04-21
**Purpose:** Evaluate the three realistic paths to LUKS-at-rest on GhostPort, with concrete tradeoffs.

---

## 1. The Problem We're Solving

GAP 5 from the 2026-04-20 audit. The microSD card is unencrypted. Physical theft of a shipped unit = attacker has:

- `/etc/wireguard/wg*.conf` — tunnel private keys
- `/etc/phantom/fleet-auth.json` — fleet token + bridge secret (impersonate the device to EC2)
- `/etc/phantom/auth.json` — scrypt hash of dashboard passcode (crack offline at leisure)
- `/etc/phantom/sni-devices.json`, `activity.json` — private user data
- All SSH keys in user home dirs

**Threat model in scope:** opportunistic theft (laptop bag grabbed at a café, stolen package in transit, tossed unit in a trash can that gets rescued), targeted espionage short of nation-state.

**Threat model out of scope:** someone who gets privileged access to a running device. If the device is booted and unlocked, LUKS has already done its job; everything else is the job of sudo/auth/firewall, which we've already hardened (GAP #0).

---

## 2. The Three Realistic Options

### Option A — Passphrase at console

**How:** User enters a passphrase on boot. systemd-cryptsetup prompts on the attached monitor/keyboard.

| Pros | Cons |
|---|---|
| Simple, well-documented, standard Debian setup | Device can't boot unattended — no headless reboot |
| No additional hardware or key storage risk | Power cycle after power outage = dead router until user returns |
| User has direct control | Remote users (Tailscale-only admins) can't unlock — requires physical access |
| Works on any Pi, any SD card | Reboot after OTA update requires the user to be present |

**Fit for GhostPort:** poor for the fleet model. GhostPort is supposed to boot and rejoin the fleet without human involvement after an outage. A passphrase makes the device a brick when the user isn't home.

**Would be viable for:** a "personal router" / "off-grid bunker" product variant where attended boots are fine.

---

### Option B — Keyfile on a removable USB

**How:** LUKS is unlocked by a keyfile stored on a USB stick that stays plugged in (or is plugged in only at boot). systemd-cryptsetup reads the keyfile from `/run/media/...`.

| Pros | Cons |
|---|---|
| Unattended boot when USB is present | User must not lose the USB — losing it = bricked device |
| "Yank the USB, the next reboot is encrypted" — a real security feature | Two-part product (Pi + USB) complicates shipping, returns, packaging |
| Keyfile rotation is easy | USB stick could be stolen with the Pi (defeats the purpose for naive attackers) |
| Works in the field with no console | UX awkward for non-technical users ("don't unplug this little thing") |

**Variants:**
- **"Always plugged" USB:** convenience over security. Attacker who grabs the Pi usually grabs everything plugged into it. Only defends against "Pi alone" theft scenarios.
- **"Plug at boot, remove after" USB:** stronger, but requires user to be present at every boot — same drawback as Option A.

**Fit for GhostPort:** moderate. Reasonable for power users who want extra security. Poor default because of the "don't lose this" liability and the ship-two-items problem.

---

### Option C — TPM-backed key

**How:** Raspberry Pi 5 has native support for an external TPM chip (on the I2C bus). LUKS key is sealed to the TPM using measured boot; on boot, if the kernel and initrd haven't been tampered with, the TPM releases the key automatically and the disk decrypts. If boot is tampered (live-USB attack, kernel replaced), TPM refuses to release the key.

| Pros | Cons |
|---|---|
| Unattended boot, no USB required | Requires TPM chip — added BOM cost (~$5-15), soldering/HAT, hardware complexity |
| Strong against cold-boot / evil-maid attacks | Measured boot setup is fragile — kernel update can "brick" boot until keys are reshipped |
| Best in-class for a shipped consumer product | Pi 5 TPM support is relatively new; limited Debian tooling polish |
| Defeats attackers who clone the SD card and boot it elsewhere | Can't easily migrate TPM-sealed keys to a new unit (support cost for hardware RMA) |

**Fit for GhostPort:** strongest security, hardest engineering. The right answer for a premium product; the wrong answer for a shipping deadline.

**Hardware options on Pi 5:**
- LetsTrust TPM HAT (via SPI or I2C)
- ZymBit HSM (pricier, but includes tamper detection)
- Pi 5's built-in RP1 secure boot (limited — doesn't expose key-release gating the way a real TPM does)

---

## 3. Recommendation

**Short-term (next 2-4 weeks):** ship with **no LUKS** + **clear physical security guidance in the user guide**. Document the threat model honestly: "this device is intended to live in your home. If it leaves your home, treat it like a lost phone — revoke its fleet token immediately." Pair with a dashboard one-click "rotate all keys on this device" command so a stolen unit can be cut out of the fleet remotely.

**Medium-term (Q3):** implement **Option B (keyfile on USB)** as an OPTIONAL feature, guarded behind a setup wizard. Ship the USB with the device. User can ignore the USB and use the unit "clear" (current behavior) or plug it in and get encrypted-at-rest. Document both modes clearly.

**Long-term (v2 hardware or premium SKU):** evaluate **Option C (TPM)**. Would need:
- Board revision to include TPM chip in the main PCB design
- Fleet infrastructure to track per-unit TPM public keys + re-seal keys after firmware updates
- RMA process for TPM-failed units

**Do NOT implement** Option A (passphrase) at all. It doesn't fit the GhostPort fleet model.

---

## 4. Fleet Key Rotation (Useful Regardless of LUKS Decision)

If we can't encrypt at rest yet, we can at least make the keys worth less by making them rotatable. To implement **before** shipping:

1. **`gp-rotate-keys` command** (new): generates fresh WireGuard keys, updates fleet server via bridge, tears down old tunnels, brings up new.
2. **Dashboard button**: "This device was stolen / compromised — rotate all keys now". Calls gp-rotate-keys, flips fleet device status to DISABLED.
3. **Fleet-side**: the fleet API marks the device's token invalidated, requires re-activation via Stripe before the rotated device can re-join.

This turns "compromised SD card" from "permanent fleet impersonation" into "a recoverable incident".

Timeline: 2-3 days of work, no hardware changes.

---

## 5. Decision Checklist

- [ ] Agree on recommendation above (short-term no-LUKS + key rotation, Q3 Option B, v2 Option C)
- [ ] Prioritize `gp-rotate-keys` + dashboard button before shipping
- [ ] Draft user-guide section on physical security expectations
- [ ] Set a Q3 planning milestone to re-evaluate LUKS-on-USB implementation
- [ ] Capture hardware BOM cost of TPM HAT for v2 pricing discussion

Once the top checklist item is agreed, the rest can slot into the existing roadmap.
