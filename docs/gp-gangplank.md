# Gangplank — USB Drive Manager

GTK desktop app for mounting, unmounting, and browsing USB drives plugged into the Pi.

## Purpose
GhostPort doesn't auto-mount USB by default (security posture — stops drive-by malware and rogue udev rules). Gangplank gives you a vetted, user-facing way to bring a drive online when you need it: plug in → see device appear → click Mount → browse → Unmount → safe to remove. Also used to backup/restore the encrypted Aether Box vault and to sideload files for the blog pipeline.

## When to use
- Backing up / restoring the Aether Box cipher directory to an external drive
- Sideloading content onto the Pi (photos for blog, recovery codes, config snapshots)
- Exporting logs or pcaps from Dragnet captures
- Restoring from a factory snapshot image

## Screenshot
`/opt/phantom/docs/screenshots/gp-gangplank.png` *(TBD — drop PNG at 900×650 to populate)*

## Config + data files
| Path | What |
|------|------|
| `/dev/sd*` | Block devices enumerated via udev |
| `/media/ghostport-admin/<label>/` | Mount points created on first mount |

Filesystem support: ext4, vfat, exfat, ntfs (via `ntfs-3g`). Encrypted volumes (LUKS) require the passphrase — prompt is modal, cleared from memory on close.

Launch: desktop icon, or `python3 /opt/phantom/desktop/gp-gangplank.py`

## Troubleshooting
- **Drive appears but won't mount** → filesystem likely not supported. Check `lsblk -f` for the detected type. Install `exfat-fuse` or `ntfs-3g` as needed.
- **"Device busy" on unmount** → a process still has a file open on the mount. `lsof +D /media/ghostport-admin/<label>/` to find it, close / kill, then unmount.
- **No device shown** → udev event not raised. `sudo dmesg | tail` — look for the plug-in line; if missing, the USB port may be the data-only lower one or the drive isn't getting enough power (the bottom-left USB-A on the Pi 5 is max 1.2A).
- **LUKS prompt rejects correct password** → verify with `sudo cryptsetup open /dev/sdX1 test` on the command line. If that works, Gangplank's GTK dialog may have stripped trailing whitespace — try without.
