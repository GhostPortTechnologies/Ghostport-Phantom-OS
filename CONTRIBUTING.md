# Contributing to GhostPort OS

First off — thank you for considering contributing to GhostPort OS! Every contribution helps make privacy more accessible to everyday people. 🏴‍☠️

## How to Contribute

### Reporting Bugs
Open an issue with the `bug` label. Include:
- What happened vs what you expected
- Your Pi model and OS version (`uname -a`)
- Output of `sudo gp-mode status`
- Any relevant error messages

### Suggesting Features
Open an issue with the `enhancement` label. Describe the feature and why it would benefit GhostPort users.

### Submitting Pull Requests
1. Fork the repository
2. Create a branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Test thoroughly on a real Pi
5. Submit a pull request with a clear description

## Code Standards
- Bash scripts must pass `shellcheck`
- nft rules must be tested on Pi OS Bookworm (64-bit)
- JavaScript follows standard ES6+ conventions
- All new features need a corresponding docs update

## Contributor License Agreement
By submitting a pull request, you agree your contribution is licensed under the GhostPort Business Source License 1.0 and grant GhostPort OS maintainers the right to use it in the project.

## Code of Conduct
Be excellent to each other. Privacy is for everyone.

☠ GhostPort OS — Your data never leaves your hands.
