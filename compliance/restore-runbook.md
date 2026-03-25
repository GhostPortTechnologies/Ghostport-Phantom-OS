# GhostPort — Disaster Recovery & Restore Runbook
**Version:** 1.0 | **Date:** 2026-03-24

---

## EC2 Fleet Server — Full Restore

### From AMI Snapshot

**Current snapshot:** `ghostport-fleet-2026-03-23-stable`

1. **Launch new EC2 instance:**
   - AWS Console → EC2 → AMIs → Select `ghostport-fleet-2026-03-23-stable`
   - Instance type: t3.micro (or same as current)
   - Attach same security group (ports: 51820/udp, 80, 443, 22 from 10.66.66.0/24)
   - Enable EBS encryption (check the box)
   - Launch

2. **Assign Elastic IP:**
   - If old instance is gone: re-associate EIP `44.214.101.82`
   - If migrating: allocate new EIP and update DNS

3. **Update DNS (if IP changed):**
   - Cloudflare/registrar: update `api.ghostporttechnologies.com` A record
   - Wait for propagation (TTL-dependent)

4. **Restore latest data (when S3 backups are configured):**
   ```bash
   aws s3 cp s3://ghostport-backups/latest/fleet.db /opt/ghostport-fleet/fleet.db
   aws s3 cp s3://ghostport-backups/latest/stripe.json /opt/ghostport-fleet/stripe.json
   aws s3 cp s3://ghostport-backups/latest/auth.json /opt/ghostport-fleet/auth.json
   sudo chown ghostport:ghostport /opt/ghostport-fleet/*.json /opt/ghostport-fleet/*.db
   sudo chmod 600 /opt/ghostport-fleet/stripe.json /opt/ghostport-fleet/auth.json
   ```

5. **Renew SSL certificate (if new instance):**
   ```bash
   sudo certbot --nginx -d api.ghostporttechnologies.com
   ```

6. **Restart services:**
   ```bash
   sudo systemctl restart ghostport-health nginx unbound
   ```

7. **Update WireGuard peer config on Pi (if server key changed):**
   ```bash
   # On Pi: update server pubkey and endpoint in /etc/wireguard/wg0.conf
   sudo wg setconf wg0 /etc/wireguard/wg0.conf
   ```

8. **Verify:**
   ```bash
   curl https://api.ghostporttechnologies.com/webhooks/health
   # From Pi: ping -c3 10.66.66.1 && curl http://10.66.66.1:8080/health
   ```

### Estimated RTO: 30-60 minutes

---

## Pi Device — Full Restore

### From Golden SD Card Image (when available)

1. Flash image to SD card using Raspberry Pi Imager
2. Insert and boot
3. Device auto-registers with fleet server on first boot
4. Set WiFi password via dashboard
5. Choose network mode

### From Git Repo (manual restore)

1. Flash fresh Raspberry Pi OS to SD card
2. Enable SSH, set hostname
3. Clone repo: `git clone https://github.com/GhostPortTechnologies/Ghostport-OS.git /opt/ghostport`
4. Run setup script (when created) or manually:
   ```bash
   # Install dependencies
   cd /opt/ghostport && npm install

   # Copy scripts
   sudo cp scripts/gp-* /usr/local/bin/
   sudo chmod +x /usr/local/bin/gp-*

   # Copy firewall profiles
   sudo cp -r etc/gpmodes /etc/

   # Copy systemd services
   sudo cp systemd/*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable ghostport ghostport-boot

   # Configure auth (generates new passcode)
   sudo gp-passcode reset

   # Start
   sudo systemctl start ghostport
   ```

5. Register with fleet: `sudo gp-new <license-key>`

### Estimated RTO: 45-90 minutes (from git), 15 minutes (from golden image)

---

## Manual Failover (EC2)

If EC2 is unrecoverable and no AMI exists:

1. Spin fresh Ubuntu 24.04 EC2 in same region
2. Install dependencies: `python3, pip install stripe, nginx, certbot, unbound, wireguard-tools`
3. Copy fleet-api.py from git or local backup
4. Restore fleet.db from S3 backup
5. Reconfigure WireGuard, nginx, unbound from documented configs
6. Update DNS
7. Re-link Stripe webhooks in Stripe Dashboard → Developers → Webhooks

**Estimated RTO: 2-4 hours**

---

## Backup Verification Checklist

Run monthly (or after backup cron is set up):

- [ ] S3 bucket accessible: `aws s3 ls s3://ghostport-backups/`
- [ ] fleet.db backup exists and is > 0 bytes
- [ ] stripe.json backup exists
- [ ] auth.json backup exists
- [ ] AMI snapshot is less than 30 days old
- [ ] Pi golden image is less than 30 days old
- [ ] Test restore to a non-production instance (quarterly)
