#!/bin/bash
# test_ota_deploy_coverage.sh
#
# Verifies that the gp-update deploy logic would copy every critical file
# from the tonight's feature wave to the correct live paths on a fresh
# customer device. Uses grep against gp-update's apply branch rather than
# actually running an OTA (can't self-OTA on a single dev Pi).
#
# Catches the failure mode where a new sudoers drop-in, sysctl setting,
# or blocklist file is shipped to the repo but forgotten in gp-update's
# copy loop — customer receives the tarball, half the feature works.

set -euo pipefail

GP_UPDATE=/opt/ghostport/scripts/gp-update
REPO=/opt/ghostport

[ -f "$GP_UPDATE" ] || { echo "FAIL: $GP_UPDATE missing"; exit 1; }

echo "=== test_ota_deploy_coverage ==="
pass=0
fail=0

check() {
    local name=$1
    local pattern=$2
    if grep -qE "$pattern" "$GP_UPDATE"; then
        echo "  PASS: $name"
        pass=$((pass + 1))
    else
        echo "  FAIL: $name — pattern '$pattern' not found in gp-update"
        fail=$((fail + 1))
    fi
}

check_pair() {
    # Verifies that BOTH patterns appear in gp-update. Multi-line-safe — avoids
    # the "for loop on line N, cp on line N+2" grep blindness.
    local name=$1
    local p1=$2
    local p2=$3
    if grep -qE "$p1" "$GP_UPDATE" && grep -qE "$p2" "$GP_UPDATE"; then
        echo "  PASS: $name"
        pass=$((pass + 1))
    else
        echo "  FAIL: $name — missing '$p1' and/or '$p2' in gp-update"
        fail=$((fail + 1))
    fi
}

# Critical source-path → destination-path pairs that the deploy block must cover.

check_pair "scripts deploy"          'scripts/gp-\*'          '/usr/local/bin/'
check_pair "systemd units deploy"    'systemd/\*\.service'    '/etc/systemd/system'
check_pair "nftables profiles"       'etc/gpmodes/\*\.nft'    '/etc/gpmodes'
check_pair "sysctl drop-ins"         'etc/sysctl\.d'          '/etc/sysctl\.d'
check_pair "sudoers drop-ins"        'etc/sudoers\.d/\*'      '/etc/sudoers\.d/'
check       "sudoers validated"       'visudo -c'
check_pair "blocklists deploy"       'etc/blocklists'         '/etc/phantom/blocklists'
check_pair "timers auto-enabled"     'systemctl enable --now' 'phantom-\*\.timer'
check_pair "non-timer services"      'systemctl enable'       'phantom-\*\.service'
check       "broker table init"       'gp-broker-counters init'
check       "MAC blocklist reload"    'gp-mac-block reload'
check       "sysctl reload"           'sysctl --system'
check_pair  "dnsmasq.d drop-ins"      'etc/dnsmasq\.d'         '/etc/dnsmasq\.d'
check       "pihole-FTL reload after dnsmasq"  'systemctl restart pihole-FTL'

# 13. Every repo file under etc/ appears in the deploy block — catches
# the "I added a new config directory but forgot to wire it" case.
echo
echo "=== repo etc/ subdirs that must have deploy coverage ==="
for subdir in $(ls -d "$REPO"/etc/*/ 2>/dev/null); do
    sub=$(basename "$subdir")
    if grep -qE "etc/$sub" "$GP_UPDATE"; then
        echo "  COVERED: etc/$sub"
        pass=$((pass + 1))
    else
        echo "  MISSING: etc/$sub — gp-update has no deploy logic for this path"
        fail=$((fail + 1))
    fi
done

echo
echo "=== summary ==="
echo "  pass: $pass"
echo "  fail: $fail"

[ "$fail" -eq 0 ]
