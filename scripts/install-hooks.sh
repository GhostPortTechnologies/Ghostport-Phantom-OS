#!/bin/bash
# Install the Phantom OS git pre-commit hook for secret scanning.
# Run this once after cloning the repo.
set -e

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/.." && pwd))"
SRC="$REPO_ROOT/scripts/git-hooks/pre-commit"
DEST="$REPO_ROOT/.git/hooks/pre-commit"

if [ ! -f "$SRC" ]; then
    echo "error: source hook missing at $SRC" >&2
    exit 1
fi

# Prefer a symlink so updates to the tracked hook propagate automatically
ln -sf "../../scripts/git-hooks/pre-commit" "$DEST"
chmod +x "$SRC"

echo "✓ pre-commit hook installed: $DEST -> $SRC"
echo ""
if ! command -v gitleaks >/dev/null 2>&1; then
    echo "⚠ gitleaks binary not found on PATH. Install it:"
    echo "    sudo apt install gitleaks        # Debian/Ubuntu/Pi OS"
    echo "    brew install gitleaks            # macOS"
    echo "    https://github.com/gitleaks/gitleaks/releases  # binary download"
    echo ""
    echo "The hook will no-op skip secret scanning until gitleaks is installed."
else
    echo "✓ gitleaks $(gitleaks version 2>&1 | head -1) detected on PATH"
fi

echo ""
echo "To bypass in an emergency (NOT recommended, creates audit trail):"
echo "    git commit --no-verify"
