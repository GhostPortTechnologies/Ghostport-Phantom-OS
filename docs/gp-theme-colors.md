# gp-theme-colors.sh — Shared Color Library

Sourceable bash library that provides theme-aware colors for all TUI apps.

## Usage
```bash
source /usr/local/lib/gp-theme-colors.sh
echo -e "${AC}Accent colored text${RESET}"
gp_header "My Tool"
gp_status "Label:" "value" "$SUCCESS"
gp_bar 75
```

## Colors (all derived from accent)
- `$AC` / `$AC_DIM` / `$AC_BRIGHT` — accent variants
- `$TEXT` / `$TEXT_DIM` / `$TEXT_FAINT` — text variants
- `$SUCCESS` — semantic green (hue 140, blended saturation)
- `$WARNING` — semantic amber (hue 45)
- `$DANGER` — semantic red (hue 0)
- `$INFO` — semantic blue (hue 200)
- `$BOLD` / `$DIM` / `$UNDERLINE` / `$RESET`

## Functions
- `gp_header "title"` — draw themed box header
- `gp_separator` — horizontal line
- `gp_status "label" "value" [color]` — formatted row
- `gp_bar <percent> [width]` — progress bar
- `gp_confirm "prompt"` — y/n dialog (returns 0/1)
- `gp_help_hint` — "[h] Help [q] Quit" footer
- `gp_key_prompt [msg]` — wait for keypress
