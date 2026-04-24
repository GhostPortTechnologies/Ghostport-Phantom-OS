# gp-paycheck — Privacy Paycheck

## Summary
Shows the monetary value of your privacy protection. Calculates how much money, data, and time GhostPort has saved you by blocking ads and trackers.

## Quick Start
1. Open **Start Menu > MONITOR > Privacy Paycheck**
2. Review your total savings, ad block stats, data saved, and time recovered
3. Check the fun facts at the bottom

## Calculations
| Metric | Rate | Source |
|--------|------|--------|
| Ad value | $3.50 CPM (per 1,000 impressions) | Industry average |
| Data per ad | 150 KB (trackers + scripts + media) | Measured average |
| Time per ad | 2.5 seconds (DNS + fetch + render) | Measured average |
| Data cost | $10/GB | Mobile data estimate |

## Sections
- **Total Saved** — Combined dollar value
- **Ad Impressions Blocked** — Count, block rate, estimated ad revenue denied
- **Data Saved** — MB/GB of tracker data never downloaded
- **Time Recovered** — Hours/minutes of page load time saved
- **Fun Facts** — TV commercials skipped, songs worth of data, cups of coffee brewed

## How It Works
Reads `/etc/phantom/ads-tally.json` which tracks blocked and total DNS queries (updated by Pi-hole integration). Applies industry-standard rates to estimate real-world value. Theme-aware from `/etc/phantom/theme.json`.

## File Locations
| Path | Purpose |
|------|---------|
| `~/.local/bin/gp-paycheck` | Main script |
| `/etc/phantom/ads-tally.json` | Ad blocking tallies |
