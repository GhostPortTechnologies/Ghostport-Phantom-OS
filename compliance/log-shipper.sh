#!/bin/bash
# GhostPort Log Shipper — sends Pi logs to EC2 for centralized storage
# Runs via cron every hour, ships: ghostport journal, auditd, auth, fail2ban
# Stored on EC2 at /var/log/ghostport-central/pi/

EC2="ubuntu@10.66.66.1"
# T-0172 — SSH key path parameterized so the public repo doesn't disclose the
# operator's deploy-key filename. Set GP_DEPLOY_KEY in your shell or sourced
# env file (e.g. ~/.config/ghostport/deploy.env) to your actual key path.
[ -r "$HOME/.config/ghostport/deploy.env" ] && . "$HOME/.config/ghostport/deploy.env"
SSH_KEY="${GP_DEPLOY_KEY:?GP_DEPLOY_KEY not set — see ~/.config/ghostport/deploy.env}"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes"
REMOTE_DIR="/var/log/ghostport-central/pi"
STATE_DIR="/tmp/gp-log-shipper"
TIMESTAMP=$(date +%Y%m%d-%H%M)

mkdir -p "$STATE_DIR"

# Ensure remote directory exists
ssh $SSH_OPTS $EC2 "sudo mkdir -p $REMOTE_DIR && sudo chown ubuntu:ubuntu $REMOTE_DIR" 2>/dev/null || exit 0

# 1. Ghostport service logs (last hour)
LAST_SHIP=$(cat "$STATE_DIR/last-journal" 2>/dev/null || echo "1 hour ago")
journalctl -u ghostport --since "$LAST_SHIP" --no-pager 2>/dev/null | \
    ssh $SSH_OPTS $EC2 "cat >> $REMOTE_DIR/ghostport-$TIMESTAMP.log" 2>/dev/null
date -Iseconds > "$STATE_DIR/last-journal"

# 2. Audit log (incremental — last hour of entries)
sudo ausearch -ts recent 2>/dev/null | \
    ssh $SSH_OPTS $EC2 "cat >> $REMOTE_DIR/audit-$TIMESTAMP.log" 2>/dev/null

# 3. Auth log (last hour)
sudo journalctl -t sshd -t sudo --since "1 hour ago" --no-pager 2>/dev/null | \
    ssh $SSH_OPTS $EC2 "cat >> $REMOTE_DIR/auth-$TIMESTAMP.log" 2>/dev/null

# 4. AIDE check results (if updated today)
AIDE_LOG="/var/log/aide/aide-check.log"
if [ -f "$AIDE_LOG" ]; then
    AIDE_MOD=$(stat -c %Y "$AIDE_LOG" 2>/dev/null)
    AIDE_LAST=$(cat "$STATE_DIR/last-aide" 2>/dev/null || echo 0)
    if [ "${AIDE_MOD:-0}" -gt "${AIDE_LAST:-0}" ]; then
        scp $SSH_OPTS "$AIDE_LOG" "$EC2:$REMOTE_DIR/aide-$TIMESTAMP.log" 2>/dev/null
        echo "$AIDE_MOD" > "$STATE_DIR/last-aide"
    fi
fi

# 5. fail2ban status
sudo fail2ban-client status sshd 2>/dev/null | \
    ssh $SSH_OPTS $EC2 "cat >> $REMOTE_DIR/fail2ban-$TIMESTAMP.log" 2>/dev/null

# Cleanup old shipped logs on EC2 (keep 90 days)
ssh $SSH_OPTS $EC2 "find $REMOTE_DIR -name '*.log' -mtime +90 -delete" 2>/dev/null
