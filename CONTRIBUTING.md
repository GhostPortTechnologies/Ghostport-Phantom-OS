# Contributing to Phantom OS

First off — thank you for considering contributing to Phantom OS! Every contribution helps make privacy more accessible to everyday people. 🏴‍☠️

## How to Contribute

### Reporting Bugs
Open an issue with the `bug` label. Include:
- What happened vs what you expected
- Your Pi model and OS version (`uname -a`)
- Output of `sudo gp-mode status`
- Any relevant error messages

### Suggesting Features
Open an issue with the `enhancement` label. Describe the feature and why it would benefit Phantom OS users.

### Submitting Pull Requests
1. Fork the repository
2. Create a branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Test thoroughly on a real Pi
5. Submit a pull request with a clear description

### Reporting Security Issues
**Don't open a public issue for vulnerabilities.** See [SECURITY.md](SECURITY.md) for the private disclosure path.

## Code Standards
- Bash scripts must pass `shellcheck`
- Python scripts must pass `py_compile`; preferred: `ruff check` clean
- nft rules must be tested on Pi OS Bookworm (64-bit)
- JavaScript follows standard ES6+ conventions
- All new features need a corresponding docs update (per `docs/FEATURE-DOCS-SOP.md`)

## Contributor License Agreement
By submitting a pull request, you agree your contribution is licensed under the **Elastic License 2.0** (see [LICENSE](LICENSE)) and grant GhostPort Technologies the right to use it in the project under the same terms.

## Code of Conduct
Be excellent to each other. Privacy is for everyone.

☠ Phantom OS — Your data never leaves your hands.
