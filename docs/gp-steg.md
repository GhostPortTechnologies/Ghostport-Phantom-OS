# gp-steg — Steganography Tool

Hide secret messages and files inside ordinary-looking images using steghide.

## Usage
```
gp-steg              Interactive menu
gp-steg hide         Hide data in an image (guided)
gp-steg extract      Extract hidden data from image
gp-steg info <file>  Check file for hidden data
```

## How It Works
- steghide embeds data into least-significant bits of cover files
- AES-128 encryption with passphrase protection
- Supported formats: JPEG, BMP, WAV, AU
- A 1MB JPEG can hide ~10-50KB of secret data

## Dependencies
- `steghide` (installed via apt)
