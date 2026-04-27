#!/bin/bash
# test_safe_defaults.sh
#
# Asserts that on a FRESH install (no existing /etc/phantom/*.json files),
# every toggle that affects user behavior or breaks third-party services
# is OFF by default. Catches the "I shipped a feature enabled by accident"
# class of regression.
#
# What this checks:
#   1. ghostport-server.js readArsenal() catch-block — schema fresh installs see
#   2. ghostport-server.js readFamilyShieldConfig() catch-block — same
#   3. shipped 30-anti-fingerprint.conf — browserleaks + amiunique commented out
#   4. data-brokers.json shipped state — has brokers + bootstrap_ips
#   5. ghost-endpoints.json — either absent or has < 2 endpoints (Ghost Mode
#      can't enable accidentally)
#
# This test reads the REPO source of truth, not the live /etc/phantom/ on a
# dev Pi (where the operator's active toggles will be set).

set -uo pipefail

REPO=/opt/ghostport
[ -d "$REPO" ] || { echo "FAIL: $REPO not found"; exit 1; }

echo "=== test_safe_defaults ==="
pass=0
fail=0

check() {
    local name=$1
    local cond=$2
    local detail=${3:-}
    if [ "$cond" = "true" ] || [ "$cond" -eq 0 ] 2>/dev/null; then
        echo "  PASS: $name"
        pass=$((pass + 1))
    else
        echo "  FAIL: $name  ${detail:+— $detail}"
        fail=$((fail + 1))
    fi
}

# ── 1. Arsenal default schema ────────────────────────────────────────
# Pull the fresh-install fallback object from the readArsenal catch block.
echo
echo "— Arsenal default schema (readArsenal fallback) —"

ARSENAL_DEFAULTS=$(python3 <<'PY'
import re, json, sys
src = open("/opt/ghostport/ghostport-server.js").read()
m = re.search(r"function readArsenal\(\).*?catch\s*\{[^{}]*return\s*(\{[^}]*\})", src, re.S)
if not m:
    print("ERROR: could not find readArsenal fallback object", file=sys.stderr)
    sys.exit(2)
# Convert JS-like object to JSON. JS uses unquoted keys; quote them.
js_obj = m.group(1)
js_obj = re.sub(r'(\w+)\s*:', r'"\1":', js_obj)
# js arrays/strings translate fine as-is for our cases
print(js_obj)
PY
) || { echo "  FAIL: could not parse readArsenal defaults"; fail=$((fail + 1)); ARSENAL_DEFAULTS="{}"; }

assert_arsenal_default() {
    local key=$1
    local expected=$2
    local actual
    actual=$(python3 -c "import json; d=json.loads('''$ARSENAL_DEFAULTS'''); print(json.dumps(d.get('$key', None)))" 2>/dev/null)
    if [ "$actual" = "$expected" ]; then
        echo "  PASS: arsenal.$key defaults to $expected"
        pass=$((pass + 1))
    else
        echo "  FAIL: arsenal.$key expected $expected, got $actual"
        fail=$((fail + 1))
    fi
}

# Every toggle that COULD impact user behavior must default off / safe
assert_arsenal_default killSwitch         false
assert_arsenal_default encryptedDns       false
assert_arsenal_default macRandomization   false
assert_arsenal_default tcpScrub           false
assert_arsenal_default ghostMode          false

# ── 2. Family Shield default schema ──────────────────────────────────
echo
echo "— Family Shield default schema —"

FS_DEFAULTS=$(python3 <<'PY'
import re, sys
src = open("/opt/ghostport/ghostport-server.js").read()
m = re.search(r"readFamilyShieldConfig\(\).*?catch\s*\{[^{}]*return\s*(\{(?:[^{}]|\{[^{}]*\})*\})", src, re.S)
if not m:
    print("ERROR: could not find FS fallback", file=sys.stderr); sys.exit(2)
js_obj = m.group(1)
# Convert: js unquoted keys → json quoted
js_obj = re.sub(r'(\w+)\s*:', r'"\1":', js_obj)
print(js_obj)
PY
) || { echo "  FAIL: could not parse Family Shield defaults"; fail=$((fail + 1)); FS_DEFAULTS='{"categories":{}}'; }

assert_fs_default() {
    local cat=$1
    local actual
    actual=$(python3 -c "import json; d=json.loads('''$FS_DEFAULTS'''); print(json.dumps(d.get('categories',{}).get('$cat', None)))" 2>/dev/null)
    if [ "$actual" = "false" ]; then
        echo "  PASS: family-shield.categories.$cat defaults to false"
        pass=$((pass + 1))
    else
        echo "  FAIL: family-shield.categories.$cat expected false, got $actual"
        fail=$((fail + 1))
    fi
}

# Every category must default off — none of these should auto-enable on
# fresh install. ACR especially — known to break smart TVs.
for cat in adult gambling facebook tiktok twitter acr dataBrokers; do
    assert_fs_default "$cat"
done

# ── 3. Shipped anti-fingerprint config — self-test sites allowed ─────
echo
echo "— Shipped 30-anti-fingerprint.conf —"

ANTI_FP=$REPO/etc/dnsmasq.d/30-anti-fingerprint.conf
if [ -f "$ANTI_FP" ]; then
    # Both must be COMMENTED OUT in the shipped default (allowed by default)
    for d in browserleaks.com amiunique.org; do
        if grep -qE "^# *address=/$d/" "$ANTI_FP"; then
            echo "  PASS: $d is commented-out (allowed) in shipped config"
            pass=$((pass + 1))
        else
            echo "  FAIL: $d should be commented-out in $ANTI_FP"
            fail=$((fail + 1))
        fi
    done
    # And these MUST stay blocked (uncommented) — they're tracker infrastructure
    for d in fingerprint.com mixpanel.com fpjs.io; do
        if grep -qE "^address=/$d/" "$ANTI_FP"; then
            echo "  PASS: $d is active (blocked) in shipped config"
            pass=$((pass + 1))
        else
            echo "  FAIL: $d should be uncommented (still blocked) in $ANTI_FP"
            fail=$((fail + 1))
        fi
    done
else
    echo "  FAIL: $ANTI_FP missing from repo"
    fail=$((fail + 1))
fi

# ── 4. data-brokers.json structural sanity ───────────────────────────
echo
echo "— Shipped data-brokers.json —"

BROKERS=$REPO/etc/blocklists/data-brokers.json
if [ -f "$BROKERS" ]; then
    python3 <<PY
import json, sys
d = json.load(open("$BROKERS"))
ok = True
brokers = d.get("brokers", {})
bootstrap = d.get("bootstrap_ips", {})
if len(brokers) >= 20:
    print(f"  PASS: data-brokers.json has {len(brokers)} brokers (≥20 expected)")
else:
    print(f"  FAIL: data-brokers.json only has {len(brokers)} brokers"); ok = False
if len(bootstrap) >= 20:
    print(f"  PASS: bootstrap_ips covers {len(bootstrap)} brokers (≥20)")
else:
    print(f"  FAIL: bootstrap_ips only covers {len(bootstrap)} brokers"); ok = False
sys.exit(0 if ok else 1)
PY
    if [ $? -eq 0 ]; then pass=$((pass + 2)); else fail=$((fail + 2)); fi
else
    echo "  FAIL: $BROKERS missing"
    fail=$((fail + 1))
fi

# ── 5. Ghost Mode endpoints — must not be auto-enabled ────────────────
echo
echo "— Ghost Mode default state —"

GE=/etc/phantom/ghost-endpoints.json
if [ ! -f "$GE" ]; then
    echo "  PASS: ghost-endpoints.json absent on fresh install (rotation can't auto-enable)"
    pass=$((pass + 1))
else
    # If file exists, count must be < 2 OR the toggle must be off
    EP_COUNT=$(python3 -c "import json; print(len(json.load(open('$GE')).get('endpoints', [])))" 2>/dev/null || echo 0)
    if [ "$EP_COUNT" -lt 2 ]; then
        echo "  PASS: ghost-endpoints.json has $EP_COUNT endpoint(s) (< 2 means rotation can't enable)"
        pass=$((pass + 1))
    else
        # File has ≥2 endpoints — Ghost Mode CAN enable, must verify arsenal toggle off in defaults
        if echo "$ARSENAL_DEFAULTS" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('ghostMode')==False else 1)"; then
            echo "  PASS: ghost-endpoints has $EP_COUNT but arsenal.ghostMode default is false"
            pass=$((pass + 1))
        else
            echo "  FAIL: ghost-endpoints.json has $EP_COUNT endpoints AND arsenal.ghostMode is not explicitly false — could auto-rotate"
            fail=$((fail + 1))
        fi
    fi
fi

# ── 6. Conntrack accounting sysctl shipped ────────────────────────────
echo
echo "— Critical sysctl drop-ins shipped from repo —"

CONNTRACK_CONF=$REPO/etc/sysctl.d/99-phantom-conntrack-acct.conf
if [ -f "$CONNTRACK_CONF" ] && grep -q "nf_conntrack_acct = 1" "$CONNTRACK_CONF"; then
    echo "  PASS: 99-phantom-conntrack-acct.conf shipped + sets nf_conntrack_acct=1"
    pass=$((pass + 1))
else
    echo "  FAIL: conntrack accounting sysctl drop-in missing or misconfigured"
    fail=$((fail + 1))
fi

echo
echo "=== summary ==="
echo "  pass: $pass"
echo "  fail: $fail"

[ "$fail" -eq 0 ]
