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
import sys, importlib.util, json, types
from pathlib import Path
spec = importlib.util.spec_from_file_location("gpdr", "$SANDBOX_MOD")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# USER_BLOCKS_FILE was pinned at module load from DNSMASQ_DIR. Because we
# sed'd DNSMASQ_DIR on the sandbox copy, USER_BLOCKS_FILE follows — but
# make it explicit for clarity in failures.
m.USER_BLOCKS_FILE = Path("$SANDBOX") / "90-user-blocks.conf"

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

# ── add subcommand ───────────────────────────────────────────────
# Synthesize argparse-style namespace
class NS:
    pass

# 7. invalid domains are rejected
for bad, label in [
    ("bad input", "whitespace"),
    ("http://example.com", "url"),
    ("*.example.com", "wildcard"),
    ("noTLD", "no-dot"),
    ("", "empty"),
]:
    ok, reason = m.validate_domain(bad)
    check(f"validator rejects {label!r}", not ok, f"input={bad!r} ok={ok} reason={reason}")

# 8. valid FQDNs pass validation
for good in ["example.com", "sub.example.com", "a-b.co.uk", "1-2-3.foo.example"]:
    ok, _ = m.validate_domain(good)
    check(f"validator accepts {good!r}", ok)

# 9. add creates the user-blocks file + appends the rule
args = NS(); args.domain = "fresh-add.test"; args.target = "0.0.0.0"
rc = m.cmd_add(args)
check("add succeeds on fresh domain", rc == 0)
check("user-blocks file was created", m.USER_BLOCKS_FILE.exists())
content = m.USER_BLOCKS_FILE.read_text()
check("file has header", "GhostPort User DNS Blocks" in content)
check("file has the new rule", "address=/fresh-add.test/0.0.0.0" in content)
check("rule carries the 'added by' annotation", "added by gp-dns-rules" in content)

# 10. duplicate (in user file) is refused
args2 = NS(); args2.domain = "fresh-add.test"; args2.target = "0.0.0.0"
rc2 = m.cmd_add(args2)
check("add refuses duplicate of a user-file entry", rc2 == 1)

# 11. duplicate (against original fixture) is refused across files
args3 = NS(); args3.domain = "also-block.test"; args3.target = "0.0.0.0"
rc3 = m.cmd_add(args3)
check("add refuses duplicate across different files", rc3 == 1)

# 12. second fresh add appends without corrupting previous
args4 = NS(); args4.domain = "second-add.test"; args4.target = "0.0.0.0"
rc4 = m.cmd_add(args4)
check("second add succeeds", rc4 == 0)
content2 = m.USER_BLOCKS_FILE.read_text()
check("both entries present", (
    "address=/fresh-add.test/0.0.0.0" in content2
    and "address=/second-add.test/0.0.0.0" in content2
))
# Count header lines — must still be exactly one header
header_count = content2.count("GhostPort User DNS Blocks")
check("header appears exactly once", header_count == 1, f"got {header_count}")

# 13. added domain flows through the toggle path cleanly
m.toggle_domain("fresh-add.test", block=False)
content3 = m.USER_BLOCKS_FILE.read_text()
check(
    "allow works on added entry",
    "# address=/fresh-add.test/0.0.0.0" in content3,
)

print()
print("=== summary ===")
print(f"  pass: {pass_ct}")
print(f"  fail: {fail_ct}")
sys.exit(0 if fail_ct == 0 else 1)
PY
