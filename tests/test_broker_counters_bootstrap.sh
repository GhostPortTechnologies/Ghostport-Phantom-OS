#!/bin/bash
# test_broker_counters_bootstrap.sh
#
# Verifies that gp-broker-counters populates nftables sets from the shipped
# bootstrap_ips list when live DNS resolution fails (fresh-boot / offline
# scenario). Protects against the "Enemy List shows zero forever on Day 1"
# failure mode documented in the honest-assessment issue #11.

set -euo pipefail

SCRIPT=/usr/local/bin/gp-broker-counters
BLOCKLIST=/etc/phantom/blocklists/data-brokers.json
TMPMOD=/tmp/gpbc-test-$$.py

[ -x "$SCRIPT" ] || { echo "FAIL: $SCRIPT not installed"; exit 1; }
[ -f "$BLOCKLIST" ] || { echo "FAIL: $BLOCKLIST not present"; exit 1; }

trap 'sudo rm -f "$TMPMOD"' EXIT
sudo cp "$SCRIPT" "$TMPMOD"
sudo chmod 644 "$TMPMOD"

echo "=== test_broker_counters_bootstrap ==="

# 1. Verify bootstrap_ips present in the blocklist
echo
python3 <<PY
import json, sys
d = json.load(open("$BLOCKLIST"))
bs = d.get("bootstrap_ips", {})
if not bs:
    print("  FAIL: bootstrap_ips missing from data-brokers.json")
    sys.exit(1)
total = sum(len(v) for v in bs.values())
brokers = len(bs)
print(f"  bootstrap_ips: {brokers} brokers, {total} IPs  (expected ≥20 brokers, ≥100 IPs)")
assert brokers >= 20, "too few brokers in bootstrap"
assert total >= 100, "too few total IPs in bootstrap"
print("  PASS: bootstrap_ips populated")
PY

# 2. Verify fallback path triggers when resolver returns empty
echo
sudo python3 <<PY
import sys, importlib.util
spec = importlib.util.spec_from_file_location("gpbc", "$TMPMOD")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Mock: simulate DNS completely down
m.resolve_domain = lambda domain, timeout=1.5: []

brokers = m.load_brokers()
result = m.resolve_broker_ips(brokers)

total_ips = sum(len(v) for v in result.values())
brokers_covered = len(result)

print(f"  simulated offline resolve → {brokers_covered} brokers, {total_ips} IPs")
assert brokers_covered >= 20, f"fallback covered only {brokers_covered} brokers"
assert total_ips >= 100, f"fallback populated only {total_ips} IPs"

# Sanity: known-good entry present
acx = result.get("Acxiom", [])
assert acx, "Acxiom should populate from bootstrap"
assert all("." in ip and ip.count(".") == 3 for ip in acx), "bootstrap IPs must be valid IPv4"

print("  PASS: fallback populates when live resolution fails")
PY

echo
echo "=== summary: bootstrap fallback verified ==="
