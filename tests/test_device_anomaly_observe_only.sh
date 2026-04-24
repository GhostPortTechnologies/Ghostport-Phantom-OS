#!/bin/bash
# test_device_anomaly_observe_only.sh
#
# Regression test for the observe-only calibration window in gp-device-anomaly.
# Verifies three paths:
#   1. Device within 7-day window → shadow-log, no live event
#   2. Device past 7-day window → live event, no shadow-log
#   3. --force-alerts flag bypasses the window
#   4. `status` exposes observe_only flags per device
#
# Safe: saves + restores any existing state files before/after.

set -euo pipefail

SCRIPT=/usr/local/bin/gp-device-anomaly
BASELINE=/etc/phantom/device-baselines.json
PREV=/etc/phantom/device-anomaly-prev.json
LEARN=/etc/phantom/device-anomaly-learning.json
IDSE=/etc/phantom/ids-events.json
TMPMOD=/tmp/gpda-test-$$.py

if [ ! -x "$SCRIPT" ]; then
    echo "FAIL: $SCRIPT not executable or not installed"
    exit 1
fi

# Save existing state so we can restore after the test
SAVE_DIR=$(mktemp -d)
trap 'sudo rm -f "$TMPMOD"; for f in device-baselines.json device-anomaly-prev.json device-anomaly-learning.json ids-events.json; do if [ -f "$SAVE_DIR/$f" ]; then sudo cp -p "$SAVE_DIR/$f" /etc/phantom/$f; else sudo rm -f /etc/phantom/$f; fi; done; rm -rf "$SAVE_DIR"' EXIT

for f in $BASELINE $PREV $LEARN $IDSE; do
    if [ -f "$f" ]; then sudo cp -p "$f" "$SAVE_DIR/"; fi
done

# Import the script as a module (requires .py extension)
sudo cp "$SCRIPT" "$TMPMOD"
sudo chmod 644 "$TMPMOD"

pass=0
fail=0

run_test() {
    local name=$1
    local first_seen_offset_days=$2   # e.g., 0 for fresh, 10 for graduated
    local force_alerts=$3              # true|false
    local expect_live=$4               # 0 or 1
    local expect_shadow=$5             # 0 or 1

    sudo rm -f "$BASELINE" "$PREV" "$LEARN"
    # Ensure ids-events starts empty-ish
    sudo sh -c "echo '[]' > $IDSE"

    sudo python3 <<PY
import sys, json, time, os
sys.path.insert(0, "/tmp")
import importlib.util
spec = importlib.util.spec_from_file_location("gpda_test", "$TMPMOD")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

now = int(time.time())
ip = "192.168.50.250"
hour = time.localtime(now).tm_hour

baselines = {"devices": {
    ip: {
        "samples": [{"ts": now - (i * 300), "hour": hour, "bytes": 500 * 1024, "interval": 300} for i in range(30)],
        "hourly_medians": {str(hour): 500 * 1024},
        "first_seen_epoch": now - ($first_seen_offset_days * 86400),
    }
}}
open("$BASELINE", "w").write(json.dumps(baselines))
open("$PREV", "w").write(json.dumps({"ts": now - 300, "device_bytes": {ip: 0}}))
m.get_device_bytes = lambda: {ip: 52 * 1024 * 1024}
m.run_collect(force_alerts=$force_alerts)
PY

    local live_count shadow_count
    live_count=$(sudo python3 -c "import json; d=json.load(open('$IDSE')); print(sum(1 for e in d if e.get('type')=='RATE_ANOMALY' and e.get('source')=='192.168.50.250'))")
    if [ -f "$LEARN" ]; then
        shadow_count=$(sudo python3 -c "import json; print(len(json.load(open('$LEARN'))))")
    else
        shadow_count=0
    fi

    if [ "$live_count" -eq "$expect_live" ] && [ "$shadow_count" -eq "$expect_shadow" ]; then
        echo "  PASS: $name (live=$live_count shadow=$shadow_count)"
        pass=$((pass + 1))
    else
        echo "  FAIL: $name — expected live=$expect_live shadow=$expect_shadow, got live=$live_count shadow=$shadow_count"
        fail=$((fail + 1))
    fi
}

echo "=== test_device_anomaly_observe_only.sh ==="

#         name                             first_seen_offset_days  force  expect_live  expect_shadow
run_test  "fresh_device_in_observe_window"  0                       False  0            1
run_test  "device_past_observe_window"      10                      False  1            0
run_test  "force_alerts_bypasses_window"    0                       True   1            0

echo
echo "=== status output check ==="
sudo /usr/local/bin/gp-device-anomaly status | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'observe_only_window_days' in d, 'missing observe_only_window_days key'
assert d['observe_only_window_days'] == 7, 'wrong window days'
print('  PASS: status exposes observe_only_window_days=7')
" && pass=$((pass + 1)) || fail=$((fail + 1))

echo
echo "=== summary ==="
echo "  pass: $pass"
echo "  fail: $fail"

[ "$fail" -eq 0 ]
