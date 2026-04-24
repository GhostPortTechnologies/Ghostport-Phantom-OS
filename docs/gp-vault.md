# gp-vault — Encrypted File Vault

## Summary

gp-vault creates an AES-256-GCM encrypted folder on your GhostPort device using gocryptfs. Files placed inside are encrypted at rest — if someone removes your SD card or USB drive, they cannot read your files without the password.

## Quick Start

1. Open **Start Menu > PROTECT > Encrypted Vault** (or run `gp-vault` in terminal)
2. Press `i` to initialize a new vault, choose SD card or USB storage, set a password
3. Press `u` to unlock, `k` to lock, `o` to open in file manager

## Commands

| Command | Description |
|---------|-------------|
| `gp-vault` | Interactive TUI menu |
| `gp-vault init` | Create a new vault |
| `gp-vault unlock` | Unlock (mount) the vault |
| `gp-vault lock` | Lock (unmount) the vault |
| `gp-vault status` | Show vault state, file count, size |
| `gp-vault change` | Change vault password |
| `gp-vault help` | Show help text |

## Interactive Mode Keys

| Key | Action |
|-----|--------|
| `i` | Initialize new vault |
| `u` | Unlock vault |
| `k` | Lock vault |
| `o` | Open vault folder in Thunar |
| `s` | Settings (timeout, password) |
| `h` | Help |
| `q` | Quit |

## Settings

### Auto-Lock Timeout
How long the vault stays open after no file access. Default: 900 seconds (15 minutes). Set to 0 to disable auto-lock.

Recommended values:
- 300 (5 min) — high security
- 900 (15 min) — balanced (default)
- 1800 (30 min) — convenience
- 0 — never auto-lock (not recommended)

### Storage Location
- **SD card** (`~/.vault-encrypted`) — convenient, always available. Minor SD card wear concern with heavy write loads.
- **USB drive** (`/media/ghostport-admin/usb/.vault-encrypted`) — recommended for larger vaults or heavy use. Remove the drive to physically disconnect your encrypted data.

## How It Works

gp-vault uses **gocryptfs**, a mature encrypted filesystem overlay:

1. **Init**: Creates an encrypted directory with a master key derived from your password using scrypt (memory-hard KDF). A `gocryptfs.conf` file stores the encrypted master key.

2. **Unlock**: gocryptfs mounts the encrypted directory as a FUSE filesystem. Files appear as normal files at `~/Vault`. Under the hood, every file name and content is encrypted with AES-256-GCM.

3. **Lock**: The FUSE mount is unmounted. The `~/Vault` directory becomes empty. Only encrypted blobs remain on disk.

4. **At rest**: File names are encrypted (appear as random base64 strings). File contents are encrypted in 4KB blocks. Directory structure is flattened. Without your password, the data is indistinguishable from random noise.

### Before and After

```
UNLOCKED (~/Vault):
  taxes-2025.pdf
  passwords.txt
  family-photos/
    vacation.jpg

LOCKED (~/.vault-encrypted):
  gocryptfs.conf
  gocryptfs.diriv
  ZB3x9Kq7mNwPvL2...
  8fHjR4nTpWxY1kQ...
  aM5cV7bDhF9sX3...
```

### Encryption Details

- **Algorithm**: AES-256-GCM (authenticated encryption)
- **Key derivation**: scrypt with high memory parameters
- **File names**: AES-SIV encrypted, base64-encoded
- **File content**: Encrypted in 4KB blocks with per-block nonce
- **Integrity**: GCM authentication tag on every block (tamper detection)

## File Locations

| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-vault` | Main script |
| `~/.vault-encrypted/` | Encrypted data (default, SD card) |
| `~/Vault/` | Mount point (files visible when unlocked) |
| `~/.config/phantom/vault.json` | Settings (timeout, paths) |
| `~/.config/phantom/.vault-intro` | First-launch flag |

## FAQ

**Q: What if I forget my password?**
Your files are permanently lost. There is no recovery mechanism. This is a security feature, not a bug. Write your password down and store it somewhere safe.

**Q: Can I back up the vault?**
Yes. Copy the entire `~/.vault-encrypted/` directory. The backup is encrypted — you need the password to access it on any machine with gocryptfs installed.

**Q: Does this wear out my SD card?**
Encrypted writes are slightly larger than plaintext (GCM overhead + block alignment). For normal use this is negligible. For heavy use (large file transfers, databases), use a USB drive instead.

**Q: Can I use the vault over the network?**
The vault is local to the GhostPort device. You can copy files into/out of `~/Vault` via SCP/SFTP when it's unlocked.

**Q: What happens if power is lost while unlocked?**
The FUSE mount disappears. No data is corrupted — gocryptfs uses atomic writes. Run `gp-vault unlock` after reboot.

## Troubleshooting

**"Failed to lock — files may be in use"**
Close all applications accessing files in `~/Vault`. If stuck, force unmount with: `fusermount -uz ~/Vault`

**"Unlock failed — wrong password or corrupt vault"**
Double-check your password. If the `gocryptfs.conf` file is corrupted, the vault cannot be recovered.

**"No USB drive found"**
Plug in a USB drive and wait a few seconds. Check with `lsblk` to verify it appears as `/dev/sda1`.
