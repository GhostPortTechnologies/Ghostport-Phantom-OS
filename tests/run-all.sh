#!/bin/bash
# run-all.sh — convenience runner for the full regression suite.
#
# Runs every test_*.sh in /opt/ghostport/tests/, reports pass/fail per file,
# exits non-zero if any failed. Use before pushing, before OTA cuts, before
# customer ships.

set -u

DIR="$(dirname "$(readlink -f "$0")")"
cd "$DIR"

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }

pass=0
fail=0
failures=()

echo "=== Phantom OS regression suite ==="
echo

for t in test_*.sh; do
    [ -x "$t" ] || chmod +x "$t" 2>/dev/null
    printf "  %-50s " "$t"
    if sudo "$DIR/$t" >/tmp/gp-run-all-$$.log 2>&1; then
        echo "$(green PASS)"
        pass=$((pass + 1))
    else
        echo "$(red FAIL)"
        fail=$((fail + 1))
        failures+=("$t")
    fi
done
rm -f /tmp/gp-run-all-$$.log

echo
echo "=== summary ==="
echo "  passed: $pass"
echo "  failed: $fail"

if [ "$fail" -gt 0 ]; then
    echo
    echo "Failures:"
    for f in "${failures[@]}"; do
        echo "  - $f  (rerun with: sudo $DIR/$f)"
    done
    exit 1
fi
exit 0
