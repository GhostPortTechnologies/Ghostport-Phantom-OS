#!/bin/bash
# test_dns_rules.sh
#
# Verifies gp-dns-rules against a sandboxed copy of the dnsmasq drop-in
# directory — does NOT touch the live /etc/dnsmasq.d/ state.
#
# Covered:
#   1. scan_rules returns the seeded rules correctly
#   2. allow flips an uncommented rule to commented
#   3. allow is idempotent
#   4. block flips a commented rule to uncommented + strips the annotation
#   5. case-insensitive domain match
#   6. scan after edits reflects the right blocked/allowed state

set -euo pipefail

SCRIPT=/usr/local/bin/gp-dns-rules
[ -x "$SCRIPT" ] || { echo "FAIL: $SCRIPT not installed"; exit 1; }

SANDBOX=$(mktemp -d)
SANDBOX_MOD=/tmp/gpdr-test-$$.py
trap 'sudo rm -rf "$SANDBOX" "$SANDBOX_MOD"' EXIT

# Build a module copy whose DNSMASQ_DIR points at the sandbox
sudo cp "$SCRIPT" "$SANDBOX_MOD"
sudo sed -i "s|DNSMASQ_DIR = Path(\"/etc/dnsmasq.d\")|DNSMASQ_DIR = Path(\"$SANDBOX\")|" "$SANDBOX_MOD"
sudo chmod 644 "$SANDBOX_MOD"

# Seed fixture
cat >"$SANDBOX/50-test.conf" <<'FIXTURE'
# test fixture
address=/block-me.test/0.0.0.0
address=/also-block.test/0.0.0.0
# address=/pre-allowed.test/0.0.0.0  # allowed by gp-dns-rules 2026-04-01
FIXTURE

echo "=== test_dns_rules ==="

# One consolidated Python block exercises every assertion. Easier to
# read than 7 nested shell calls and avoids bash -c function-scope issues.
sudo python3 <<PY
import sys, importlib.util, json
spec = importlib.util.spec_from_file_location("gpdr", "$SANDBOX_MOD")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

pass_ct = 0
fail_ct = 0
def check(name, cond, detail=""):
    global pass_ct, fail_ct
    if cond:
        print(f"  PASS: {name}")
        pass_ct += 1
    else:
        print(f"  FAIL: {name}  {detail}")
        fail_ct += 1

def read_fixture():
    return open("$SANDBOX/50-test.conf").read()

# 1. scan_rules picks up the 3 seeded rules
rules = m.scan_rules()
check("scan picks up 3 rules", len(rules) == 3, f"got {len(rules)}")

# 2. allow flips uncommented → commented
m.toggle_domain("block-me.test", block=False)
check(
    "allow comments out an active rule",
    "# address=/block-me.test" in read_fixture(),
)

# 3. idempotent: calling allow again leaves only ONE '#' prefix
m.toggle_domain("block-me.test", block=False)
content = read_fixture()
check(
    "allow is idempotent (no double-comment)",
    content.count("# address=/block-me.test") == 1 and "## address" not in content,
)

# 4. block uncomments + strips annotation
m.toggle_domain("pre-allowed.test", block=True)
content = read_fixture()
check(
    "block uncomments disabled rule",
    any(line.startswith("address=/pre-allowed.test/") for line in content.splitlines()),
)
check(
    "block strips the allowed-by annotation",
    "allowed by gp-dns-rules" not in content.split("pre-allowed.test")[1].split("\n")[0],
)

# 5. case-insensitive match
m.toggle_domain("ALSO-BLOCK.TEST", block=False)
check(
    "case-insensitive domain match",
    "# address=/also-block.test" in read_fixture(),
)

# 6. scan_rules reflects accurate state after all edits
final = {r["domain"]: r["blocked"] for r in m.scan_rules()}
check(
    "scan reflects block-me.test allowed",
    final.get("block-me.test") is False,
    f"state: {final}",
)
check(
    "scan reflects also-block.test allowed",
    final.get("also-block.test") is False,
    f"state: {final}",
)
check(
    "scan reflects pre-allowed.test blocked",
    final.get("pre-allowed.test") is True,
    f"state: {final}",
)

print()
print("=== summary ===")
print(f"  pass: {pass_ct}")
print(f"  fail: {fail_ct}")
sys.exit(0 if fail_ct == 0 else 1)
PY
