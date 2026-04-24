# Phantom OS — Golden Image Prep SOP

Checklist and rules for preparing the shipping SD-card / SSD image that goes
to customers. Developer device ≠ customer device — the dev Pi keeps convenience
features on; the customer image must be stripped.

Treat every bullet as a hard gate before the image is declared shippable.

---

## 1. Remote Access — Privacy Posture

Customers bought a **privacy router**. No always-on cloud-relayed remote access
is permitted in the default image.

### rpi-connect — MUST BE DISABLED

Raspberry Pi Connect (`rpi-connect`) phones home to `connect.raspberrypi.com`
and is signed in to whichever Raspberry Pi ID built the image. Leaving it on
in the shipping image means:

- Every customer device inherits our dev account's trust relationship
- Raspberry Pi Ltd. (UK) sits in the signalling path for any remote session
- A stolen RP-ID password compromises every deployed unit

**Required image-prep steps:**

```bash
# 1. Kill active sessions + stop the daemons
rpi-connect off

# 2. Sign out — unlinks the Pi from the dev's Raspberry Pi account
rpi-connect signout

# 3. Remove the package so customers can't accidentally enable it
sudo apt-get purge -y rpi-connect rpi-connect-lite

# 4. Verify no rpi-connect binaries remain
which rpi-connect rpi-connectd wayvnc
# Expected: empty (wayvnc is OK only if it was used elsewhere)

# 5. Confirm no residual user units
systemctl --user list-unit-files 2>/dev/null | grep rpi-connect
```

A customer who wants Raspberry Pi Connect later can install it themselves.
Default must be *absent*, not *present-but-disabled* (disabled can be re-enabled
by a misclick).

### Tailscale — MUST BE DISABLED + LOGGED OUT

`tailscaled` is used by the operator for management but MUST NOT ship active
on the customer device — our fleet keys would be bundled.

```bash
sudo tailscale logout
sudo tailscale down
sudo systemctl disable --now tailscaled.service
# Reset the node identity so nothing of ours is cached
sudo rm -rf /var/lib/tailscale
```

Customer can re-enable Tailscale if they want it; fresh install, fresh node.

### SSH — Leave enabled, but reset keys

The SSH daemon stays so owners can SSH in over LAN. But host keys must be
regenerated so every shipped device is unique.

```bash
sudo rm -f /etc/ssh/ssh_host_*
sudo systemctl restart ssh
# (ssh-keygen runs on next boot via systemd for missing keys, or:)
sudo dpkg-reconfigure openssh-server
```

---

## 2. Credentials — Wipe, Don't Keep

- `/home/ghostport-admin/.ssh/known_hosts` — delete
- `/home/ghostport-admin/.bash_history` — delete
- `/root/.bash_history` — delete
- `/etc/phantom/fleet-auth.json` — delete (contains dev fleet tokens)
- `/etc/phantom/secrets/*` — delete (SMTP creds, API keys, etc.)
- `/etc/phantom/alerts.json` — reset to empty template (customer fills in)
- Every `.claude/` directory under /home — delete (leaks dev conversation history)

Run `sudo gp-factory-reset` first as a baseline, then manually audit the
paths above.

---

## 3. Provisioning On First Boot

The customer's first boot should trigger `gp-first-boot` which:

1. Regenerates the passcode (`gp-passcode reset`)
2. Generates new SSH host keys
3. Shows a one-time setup screen (SSID, passphrase, etc.)
4. Invokes `gp-provision` if the customer wants fleet enrollment (opt-in)

If any of step 3's screens reference our dev account (email, RP-ID), that's
a bug — fix before ship.

---

## 4. Verification Checklist

Before declaring an image shippable, boot it on a test device and confirm:

- [ ] `rpi-connect status` → command not found OR "not signed in"
- [ ] `tailscale status` → "Tailscale is stopped" OR "Logged out"
- [ ] `/etc/ssh/ssh_host_*` files have been regenerated (check mtimes)
- [ ] `/etc/phantom/fleet-auth.json` is absent or empty
- [ ] `gp-first-boot` wizard runs and completes
- [ ] No `thomasestrada915@` or similar dev identity visible anywhere
- [ ] `history` for ghostport-admin and root is empty
- [ ] Every `/home/*/.claude/` tree is gone

---

## 5. Why This Matters

The device is marketed and sold as a privacy-first router. Shipping with a
pre-authenticated remote access tool + our identity baked in violates the
product premise and, worse, creates a lateral-movement path: compromise
one dev credential → compromise every customer device trusting it. These
are not "nice to have" steps — they're release blockers.

---

## Owner

This SOP applies to **every image build**. The operator performing the
release is responsible for running this checklist. Document any exceptions
in CHANGELOG alongside the reason.

---

## 6. Session-Accumulated Dev State (APPENDED 2026-04-21)

As the dev Pi is used, files accumulate that §4 doesn't list because they
didn't exist when the SOP was written. Add these to the strip routine.

### 6.1 Fleet / bridge secrets (extends §4)

`/etc/phantom/fleet-auth.json` now carries `bridge_secret`, `bridge_token`,
`fleet_token`, and `fleet_server`. All must be absent or empty in the
shipping image; re-issued by `gp-first-boot`. Also strip:

```bash
sudo rm -f /etc/phantom/fleet-auth.json.bak-*
```

### 6.2 SSH keys (dev operator accumulation)

The dev Pi has picked up SSH keys for the EC2 control plane. Never ship:

```bash
sudo rm -f /home/ghostport-admin/.ssh/*.pem \
           /home/ghostport-admin/Downloads/*.pem \
           /home/ghostport-admin/ec2-key.pem \
           /home/ghostport-admin/pi.key \
           /home/ghostport-admin/.ssh/known_hosts
sudo truncate -s 0 /home/ghostport-admin/.ssh/authorized_keys
```

### 6.3 Sudoers backups (new artifact class)

The GAP #0 sudo hardening rollout leaves `.bak-*` and `.disabled.*` files
in `/etc/sudoers.d/`. Sudo ignores them but they're world-readable and
reveal the hardening history. Strip:

```bash
sudo find /etc/sudoers.d -maxdepth 1 \
  \( -name '*.bak-*' -o -name '*.disabled.*' \) -delete
```

### 6.4 AI session artifacts (extends §4 "`.claude/` tree is gone")

```bash
sudo rm -rf /home/ghostport-admin/.claude \
            /home/ghostport-admin/.claude-memory-backups \
            /home/ghostport-admin/.local/share/ghostport-bridge-pending
```

### 6.5 Chamber message history

Chamber is a legitimate ship feature but its message log contains full
AI-squad coordination history. Keep the service, wipe the data:

```bash
sudo rm -rf /home/ghostport-admin/.config/chamber /var/lib/chamber 2>/dev/null
sudo systemctl disable chamber 2>/dev/null
```

### 6.6 Runtime state (activity, statistics, baselines)

These paint a picture of the operator's usage:

```bash
sudo rm -f /etc/phantom/activity.json \
           /etc/phantom/ads-tally.json \
           /etc/phantom/ids-events.json \
           /etc/phantom/ids-trends.json \
           /etc/phantom/health-state.json \
           /etc/phantom/sni-devices.json \
           /etc/phantom/device-profiles.json \
           /etc/phantom/wan.json \
           /etc/phantom/preflight-state.json \
           /etc/phantom/quartermaster-history.json \
           /etc/phantom/stonefish-baseline.json \
           /etc/phantom/lockout.json
```

### 6.7 Dev desktop customization

```bash
sudo rm -f /home/ghostport-admin/.config/phantom/icon-positions.json \
           /home/ghostport-admin/.config/phantom/icon-positions.json.bak-* \
           /home/ghostport-admin/.config/phantom/widget-layout.json
```

### 6.8 Shell history

```bash
sudo truncate -s 0 /home/ghostport-admin/.bash_history /root/.bash_history
sudo rm -f /home/ghostport-admin/.lesshst /home/ghostport-admin/.viminfo
```

---

## 7. Extended Verification (ADDS TO §4)

- [ ] `sudo find /etc/sudoers.d -name '*.bak-*' -o -name '*.disabled.*'` → empty
- [ ] `ls /home/*/.ssh/*.pem /home/*/Downloads/*.pem 2>/dev/null` → empty
- [ ] `ls /home/*/.claude-memory-backups/ 2>/dev/null` → empty
- [ ] `ls /home/*/.local/share/ghostport-bridge-pending/ 2>/dev/null` → empty
- [ ] `cat /etc/phantom/fleet-auth.json` → absent or empty
- [ ] Boot the image once, run `gp-first-boot`, confirm fleet provisioning
      generates NEW bridge_secret/token distinct from the dev values
