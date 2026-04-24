#!/bin/bash
# GhostPort Alert Monitor — runs via cron, sends alerts through bridge
# Checks: fail2ban bans, auth failures, disk usage, service health, AIDE changes
# Cron: */10 * * * * /home/ghostport-admin/compliance/alert-monitor.sh

BRIDGE="http://10.66.66.1:8080/messages"
# Bridge auth token — loaded from gitignored config so it never lands in git history.
# File provisioned once with: jq -n --arg t "$TOKEN" '{alert_token:$t}' | sudo tee /etc/phantom/bridge-auth.json
# Fallback: ALERT_BRIDGE_TOKEN env var, then empty string (alerts fail loud, not silently).
if command -v jq >/dev/null 2>&1 && [ -r /etc/phantom/bridge-auth.json ]; then
    TOKEN=$(jq -r '.alert_token // empty' /etc/phantom/bridge-auth.json 2>/dev/null)
fi
TOKEN="${TOKEN:-${ALERT_BRIDGE_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
    echo "$(date -Iseconds) [alert-monitor] missing bridge token — skipping alerts this run" >> /var/log/ghostport-alerts.log 2>/dev/null
    exit 0
fi
ALERT_LOG="/var/log/ghostport-alerts.log"
STATE_DIR="/tmp/gp-alert-state"
mkdir -p "$STATE_DIR"

send_alert() {
    local severity="$1"
    local message="$2"
    local body="[ALERT][$severity] $message"
    echo "$(date -Iseconds) $body" >> "$ALERT_LOG"
    curl -s -X POST "$BRIDGE" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"from\":\"pi\",\"to\":\"server\",\"type\":\"alert\",\"body\":\"$body\"}" \
        > /dev/null 2>&1
}

# 1. Check fail2ban recent bans (last 10 min)
F2B_BANS=$(sudo fail2ban-client status sshd 2>/dev/null | grep "Currently banned" | awk '{print $NF}')
if [ "${F2B_BANS:-0}" -gt 0 ]; then
    LAST_BAN_CHECK="$STATE_DIR/f2b-count"
    PREV=${LAST_BAN_CHECK:+$(cat "$LAST_BAN_CHECK" 2>/dev/null)}
    if [ "$F2B_BANS" != "${PREV:-0}" ]; then
        send_alert "HIGH" "fail2ban: $F2B_BANS IPs currently banned on SSH"
        echo "$F2B_BANS" > "$LAST_BAN_CHECK"
    fi
fi

# 2. Check for recent auth failures in ghostport server log (last 10 min)
AUTH_FAILS=$(journalctl -u ghostport --since "10 min ago" --no-pager 2>/dev/null | grep -c "\[Auth\] Failed login")
if [ "${AUTH_FAILS:-0}" -ge 3 ]; then
    send_alert "HIGH" "Dashboard auth: $AUTH_FAILS failed login attempts in last 10 min"
fi

# 3. Check disk usage
DISK_PCT=$(df / --output=pcent | tail -1 | tr -d '% ')
if [ "${DISK_PCT:-0}" -ge 85 ]; then
    send_alert "MEDIUM" "Pi disk usage at ${DISK_PCT}%"
fi

# 4. Check critical services
for svc in ghostport hostapd pihole-FTL; do
    if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
        SVC_STATE="$STATE_DIR/svc-$svc"
        if [ ! -f "$SVC_STATE" ]; then
            send_alert "CRITICAL" "Service $svc is DOWN"
            touch "$SVC_STATE"
        fi
    else
        rm -f "$STATE_DIR/svc-$svc" 2>/dev/null
    fi
done

# 5. Check Tailscale (management plane — never should be down)
if ! systemctl is-active --quiet tailscaled 2>/dev/null; then
    if [ ! -f "$STATE_DIR/svc-tailscale" ]; then
        send_alert "CRITICAL" "Tailscale management plane is DOWN — remote access lost"
        touch "$STATE_DIR/svc-tailscale"
    fi
else
    rm -f "$STATE_DIR/svc-tailscale" 2>/dev/null
fi

# 6. Check AIDE results (if ran today)
AIDE_LOG="/var/log/aide/aide-check.log"
if [ -f "$AIDE_LOG" ]; then
    AIDE_MOD=$(stat -c %Y "$AIDE_LOG" 2>/dev/null)
    NOW=$(date +%s)
    # Only alert if log was updated in last 24h and has changes
    if [ $((NOW - ${AIDE_MOD:-0})) -lt 86400 ]; then
        AIDE_CHANGES=$(grep -c "changed:" "$AIDE_LOG" 2>/dev/null)
        AIDE_NEW=$(grep -c "added:" "$AIDE_LOG" 2>/dev/null)
        AIDE_REMOVED=$(grep -c "removed:" "$AIDE_LOG" 2>/dev/null)
        TOTAL=$((${AIDE_CHANGES:-0} + ${AIDE_NEW:-0} + ${AIDE_REMOVED:-0}))
        if [ "$TOTAL" -gt 0 ]; then
            AIDE_STATE="$STATE_DIR/aide-last"
            CURRENT_HASH=$(md5sum "$AIDE_LOG" 2>/dev/null | awk '{print $1}')
            PREV_HASH=$(cat "$AIDE_STATE" 2>/dev/null)
            if [ "$CURRENT_HASH" != "$PREV_HASH" ]; then
                send_alert "HIGH" "AIDE detected $TOTAL file integrity changes — check $AIDE_LOG"
                echo "$CURRENT_HASH" > "$AIDE_STATE"
            fi
        fi
    fi
fi

# 6b. Check psad for port scan detections
if command -v psad &>/dev/null; then
    PSAD_DANGER=$(sudo psad --Status 2>/dev/null | grep -c "DL[3-5]")
    if [ "${PSAD_DANGER:-0}" -gt 0 ]; then
        PSAD_STATE="$STATE_DIR/psad-last"
        PSAD_NOW=$(sudo psad --Status 2>/dev/null | grep "DL[3-5]" | head -5)
        PSAD_HASH=$(echo "$PSAD_NOW" | md5sum | awk '{print $1}')
        PREV_HASH=$(cat "$PSAD_STATE" 2>/dev/null)
        if [ "$PSAD_HASH" != "$PREV_HASH" ]; then
            send_alert "HIGH" "psad: $PSAD_DANGER high-danger port scan sources detected"
            echo "$PSAD_HASH" > "$PSAD_STATE"
        fi
    fi
fi

# 6c. Check rkhunter results (if ran recently)
RKH_LOG="/var/log/rkhunter-weekly.log"
if [ -f "$RKH_LOG" ]; then
    RKH_MOD=$(stat -c %Y "$RKH_LOG" 2>/dev/null)
    NOW=$(date +%s)
    if [ $((NOW - ${RKH_MOD:-0})) -lt 604800 ]; then
        RKH_WARNINGS=$(grep -c "Warning:" "$RKH_LOG" 2>/dev/null)
        if [ "${RKH_WARNINGS:-0}" -gt 0 ]; then
            RKH_STATE="$STATE_DIR/rkh-last"
            RKH_HASH=$(md5sum "$RKH_LOG" 2>/dev/null | awk '{print $1}')
            PREV_HASH=$(cat "$RKH_STATE" 2>/dev/null)
            if [ "$RKH_HASH" != "$PREV_HASH" ]; then
                send_alert "CRITICAL" "rkhunter: $RKH_WARNINGS warnings detected — possible rootkit"
                echo "$RKH_HASH" > "$RKH_STATE"
            fi
        fi
    fi
fi

# 7. Check auditd for recent privilege escalation
if command -v ausearch &>/dev/null; then
    PRIV_ESC=$(sudo ausearch -k privilege-escalation -ts recent 2>/dev/null | grep -c "type=EXECVE")
    if [ "${PRIV_ESC:-0}" -ge 10 ]; then
        send_alert "MEDIUM" "auditd: $PRIV_ESC privilege escalation events in recent window"
    fi
fi
