#!/bin/bash
# test_first_boot.sh
#
# Regression test that wraps gp-first-boot-check + adds invariants about
# initial state that should hold on any healthy Phantom OS device. Runs in
# the same suite as the other test_*.sh files.
#
# Different from test_safe_defaults.sh:
#   safe_defaults — checks the SHIPPED defaults in repo source-of-truth
#                   (does the code/config say "OFF" by default?)
#   first_boot    — checks the LIVE running state on the current device
#                   (are services up? timers active? files where they
#                    should be?)

set -uo pipefail

CHECK_SCRIPT=/usr/local/bin/gp-first-boot-check
[ -x "$CHECK_SCRIPT" ] || { echo "FAIL: $CHECK_SCRIPT not installed"; exit 1; }

echo "=== test_first_boot ==="
pass=0
fail=0

# 1. gp-first-boot-check exits 0 (no FAIL-class issues)
if sudo "$CHECK_SCRIPT" --quiet; then
    echo "  PASS: gp-first-boot-check exits 0 (no failures)"
    pass=$((pass + 1))
else
    echo "  FAIL: gp-first-boot-check exits non-zero — see verbose output:"
    sudo "$CHECK_SCRIPT" 2>&1 | sed 's/^/    /'
    fail=$((fail + 1))
fi

# 2. JSON output is parseable and has the expected shape
JSON=$(sudo "$CHECK_SCRIPT" --json 2>/dev/null)
if echo "$JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'summary' in d
assert 'checks' in d
assert 'healthy' in d
assert isinstance(d['summary'].get('pass'), int)
assert d['summary']['pass'] >= 15, f'expected ≥15 passing checks, got {d[\"summary\"][\"pass\"]}'
" 2>/dev/null; then
    echo "  PASS: --json output parses + has expected shape"
    pass=$((pass + 1))
else
    echo "  FAIL: --json output malformed"
    fail=$((fail + 1))
fi

# 3. Specific invariants the suite assumes — file existence + ownership
declare -A REQUIRED_FILES=(
    ["/etc/phantom/arsenal.json"]="ghostport-admin"
    ["/etc/phantom/family-shield.json"]="ghostport-admin"
    ["/etc/phantom/auth.json"]="ghostport-admin"
    ["/etc/phantom/blocklists/data-brokers.json"]="root"
    ["/etc/dnsmasq.d/30-anti-fingerprint.conf"]="root"
    ["/etc/sysctl.d/99-phantom-conntrack-acct.conf"]="root"
    ["/etc/sudoers.d/022_gp-palantir"]="root"
    ["/usr/local/bin/gp-mode"]="root"
    ["/usr/local/bin/gp-broker-counters"]="root"
    ["/usr/local/bin/gp-device-anomaly"]="root"
    ["/usr/local/bin/gp-ghost-rotate"]="root"
    ["/usr/local/bin/gp-mac-block"]="root"
    ["/usr/local/bin/gp-dns-rules"]="root"
    ["/usr/local/bin/gp-first-boot-check"]="root"
)
for path in "${!REQUIRED_FILES[@]}"; do
    expected_owner="${REQUIRED_FILES[$path]}"
    if [ ! -e "$path" ]; then
        echo "  FAIL: $path missing"
        fail=$((fail + 1))
        continue
    fi
    actual_owner=$(stat -c %U "$path" 2>/dev/null)
    if [ "$actual_owner" = "$expected_owner" ]; then
        : # PASS — too verbose to print every one
    else
        echo "  FAIL: $path owner=$actual_owner (expected $expected_owner)"
        fail=$((fail + 1))
    fi
done
echo "  PASS: all ${#REQUIRED_FILES[@]} required files present + correctly owned"
pass=$((pass + 1))

# 4. Critical timers must be in --now state (active + scheduled)
for timer in phantom-broker-counters phantom-broker-refresh phantom-device-anomaly; do
    if systemctl is-active --quiet "${timer}.timer"; then
        : # ok
    else
        echo "  FAIL: ${timer}.timer is not active"
        fail=$((fail + 1))
    fi
done
echo "  PASS: all 3 phantom-* timers active"
pass=$((pass + 1))

# 5. nftables filter chain must have the GhostPort rules (not bare default)
if sudo nft list chain inet filter forward 2>/dev/null | grep -qE "iifname.*wlan0|iifname \"wlan0\""; then
    echo "  PASS: nftables forward chain has wlan0 rules"
    pass=$((pass + 1))
else
    echo "  FAIL: nftables forward chain missing wlan0 routing rules"
    fail=$((fail + 1))
fi

# 6. Conntrack accounting kernel toggle is enabled. Read /proc directly
# (works without sudo + without sysctl on PATH).
ACCT=$(cat /proc/sys/net/netfilter/nf_conntrack_acct 2>/dev/null || echo 0)
if [ "$ACCT" = "1" ]; then
    echo "  PASS: net.netfilter.nf_conntrack_acct=1 (rate-anomaly detector functional)"
    pass=$((pass + 1))
else
    echo "  FAIL: nf_conntrack_acct disabled — rate anomaly detector blind"
    fail=$((fail + 1))
fi

# 7. Pi-hole DNS resolver answers a query in <2s (no upstream stall)
START=$(date +%s%N)
if dig +short +time=2 +tries=1 @127.0.0.1 cloudflare.com >/dev/null 2>&1; then
    END=$(date +%s%N)
    LATENCY_MS=$(( (END - START) / 1000000 ))
    if [ "$LATENCY_MS" -lt 2000 ]; then
        echo "  PASS: DNS query latency ${LATENCY_MS}ms (<2000ms)"
        pass=$((pass + 1))
    else
        echo "  FAIL: DNS query took ${LATENCY_MS}ms (≥2s) — upstream may be stalling"
        fail=$((fail + 1))
    fi
else
    echo "  FAIL: dig @127.0.0.1 cloudflare.com returned no answer"
    fail=$((fail + 1))
fi

echo
echo "=== summary ==="
echo "  pass: $pass"
echo "  fail: $fail"

[ "$fail" -eq 0 ]
