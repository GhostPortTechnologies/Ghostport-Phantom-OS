#!/bin/bash
# test_ghost_rotate_rollback.sh
#
# Verifies apply_endpoint's rollback-on-failure path in gp-ghost-rotate.
# Mocks the `wg show` + `wg set` surface so we don't need an actual
# dead relay to exercise the failure-recovery code.
#
# Covered scenarios:
#   1. Fresh handshake within window → success, no rollback
#   2. Handshake never advances → revert to captured endpoint
#   3. Zero pre-handshake + any post-handshake → success (fresh install)

set -euo pipefail

SCRIPT=/usr/local/bin/gp-ghost-rotate
TMPMOD=/tmp/gpgr-test-$$.py

[ -x "$SCRIPT" ] || { echo "FAIL: $SCRIPT not installed"; exit 1; }

trap 'sudo rm -f "$TMPMOD"' EXIT
sudo cp "$SCRIPT" "$TMPMOD"
sudo chmod 644 "$TMPMOD"

echo "=== test_ghost_rotate_rollback ==="
pass=0
fail=0

run_case() {
    local name=$1
    local pre_handshake=$2
    local post_handshake_advances=$3  # "yes" | "no"
    local expect_ok=$4                  # "yes" | "no"
    local expect_rollback=$5            # "yes" | "no"

    sudo python3 <<PY
import sys, importlib.util
spec = importlib.util.spec_from_file_location("gpgr", "$TMPMOD")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Mock state: "current endpoint" + "latest handshake" can be controlled
state = {
    "pubkey":          "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE=",
    "endpoint_before": "1.1.1.1:51820",
    "endpoint_now":    "1.1.1.1:51820",
    "handshake_now":   $pre_handshake,
    "set_calls":       [],
}

def mock_pubkey():      return state["pubkey"]
def mock_endpoint():    return state["endpoint_now"]
def mock_handshake():   return state["handshake_now"]

def mock_wg_set(pubkey, endpoint_str):
    state["set_calls"].append((pubkey, endpoint_str))
    state["endpoint_now"] = endpoint_str
    return True, ""

m.get_wg1_peer_pubkey        = mock_pubkey
m.get_wg1_current_endpoint   = mock_endpoint
m.get_latest_handshake_epoch = mock_handshake
m._wg_set_endpoint           = mock_wg_set

# Simulate handshake advancing during the health window (or not)
if "$post_handshake_advances" == "yes":
    orig_hs = mock_handshake
    def advancing_hs():
        state["handshake_now"] = state["handshake_now"] + 100
        return state["handshake_now"]
    m.get_latest_handshake_epoch = advancing_hs

# Short timeout so test runs fast
ok, msg = m.apply_endpoint({"host": "2.2.2.2", "port": 51820, "label": "test"}, health_timeout_sec=3)

# Evaluate expectations
expected_ok = ("$expect_ok" == "yes")
expected_rollback = ("$expect_rollback" == "yes")

actual_rollback = (len(state["set_calls"]) >= 2)  # second set = rollback

problems = []
if ok != expected_ok:
    problems.append(f"ok={ok} (expected {expected_ok})")
if actual_rollback != expected_rollback:
    problems.append(f"rollback_occurred={actual_rollback} (expected {expected_rollback})")

if problems:
    print(f"  FAIL: $name — " + "; ".join(problems))
    print(f"    msg: {msg}")
    print(f"    set_calls: {state['set_calls']}")
    sys.exit(1)
else:
    print(f"  PASS: $name (ok={ok} rollback={actual_rollback})")
PY
    rc=$?
    if [ "$rc" -eq 0 ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
    fi
}

#         name                        pre_handshake  post_advances  expect_ok  expect_rollback
run_case  "fresh_handshake_succeeds"  1234567890     yes            yes        no
run_case  "no_handshake_rollback"     1234567890     no             no         yes
run_case  "zero_pre_any_post_ok"      0              yes            yes        no

echo
echo "=== summary ==="
echo "  pass: $pass"
echo "  fail: $fail"

[ "$fail" -eq 0 ]
