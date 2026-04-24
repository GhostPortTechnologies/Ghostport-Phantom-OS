# Aether Box — Encrypted File Vault

GTK desktop app backed by `gocryptfs` for storing sensitive files encrypted on-device. Replaces the older `gp-vault` TUI.

## Purpose
Hold files you need on the router but don't want readable if the SD card is copied or the device is stolen — passport scans, recovery codes, seed phrases, tax docs. Aether Box uses gocryptfs (AEAD + per-file salt) to encrypt a directory; files look like random blobs on disk and are only readable while the vault is unlocked. Auto-locks after 15 minutes idle.

## When to use
- Storing any single-user secret on the router that isn't a password (use `gp-pass` for passwords)
- Exporting secrets to a USB-stick backup (the cipher directory is portable — copy it, mount on another gocryptfs-capable machine with the same password)
- Pre-travel: load backup recovery codes into the vault, lock, walk away

## Screenshot
`/opt/phantom/docs/screenshots/gp-aetherbox.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `~/.local/share/ghostport-vault/cipher/` | Encrypted storage on disk (gocryptfs ciphertext + gocryptfs.conf) |
| `~/.local/share/ghostport-vault/mnt/` | Mount point (exists only while vault is unlocked) |
| `~/.local/share/ghostport-vault/.intro_seen` | Marker file; skips the first-run walkthrough |

Auto-lock timeout: 15 minutes of UI idle (set in source as `AUTO_LOCK_MINUTES = 15`).

Launch from the desktop icon or: `python3 /opt/phantom/desktop/gp-aetherbox.py`

## Troubleshooting
- **"gocryptfs not found"** → `sudo apt install gocryptfs`. Aether Box detects a missing binary on launch and prompts to install.
- **Forgot password** → There is no recovery. gocryptfs is zero-knowledge; the key is derived from the password. Files are unrecoverable. Delete the cipher dir (`rm -rf ~/.local/share/ghostport-vault/cipher`) and start over with a new vault.
- **"Already mounted" on unlock** → Previous session didn't clean unmount. `fusermount -u ~/.local/share/ghostport-vault/mnt`, then unlock again.
- **"Securely shred original" checkbox is greyed out** → `shred` binary missing. `sudo apt install coreutils` (usually already installed; check `which shred`).
- **Auto-lock fires too fast** — hardcoded 15min, not user-configurable. Edit `AUTO_LOCK_MINUTES` in `/opt/phantom/desktop/gp-aetherbox.py` if you need a longer window. No service restart required; relaunch the app.
- **Vault files visible in file manager** → Vault is unlocked. Lock it (button in app, or close the app — auto-lock on exit). The mount disappears when locked.
