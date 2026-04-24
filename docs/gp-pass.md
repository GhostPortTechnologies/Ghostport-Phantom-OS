# gp-pass — Password Manager

GPG-encrypted password store using 'pass' (standard Unix password manager).

## Usage
```
gp-pass              Interactive TUI
gp-pass list         List stored entries
gp-pass get <name>   Copy password to clipboard (10s auto-clear)
gp-pass add <name>   Add new password
gp-pass gen <name>   Generate random password
gp-pass rm <name>    Remove entry
gp-pass search <q>   Search by name
gp-pass export       Export encrypted backup
```

## First Time
Creates a GPG key automatically on first run. Each password is an individual GPG-encrypted file in ~/.password-store/.

## Security
- AES-256 via GPG
- Clipboard auto-clears after 10 seconds
- No plaintext on disk
- Compatible with standard 'pass' ecosystem
