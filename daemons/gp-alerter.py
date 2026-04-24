#!/usr/bin/env python3
"""
GhostPort Alerter — multi-channel notification daemon.

Watches event sources (IDS events, health state, activity log, device profiles)
and dispatches qualifying events to configured endpoints: ntfy.sh, generic
webhooks, Discord webhooks, and SMTP email.

Config: /etc/phantom/alerts.json
Log:    journalctl -u ghostport-alerter -f

Safety:
  - Per-(trigger, entity) dedup with 60s cooldown
  - Hard cap of 30 dispatches/min per endpoint (anti-spam)
  - On startup, loads current file tails as "watermarks" so we never
    fire on historical events
  - Credentials (SMTP password) live in a separate file referenced by
    `pass_ref`; never in the JSON itself
"""

import json
import os
import sys
import time
import smtplib
import threading
from collections import deque
from email.mime.text import MIMEText
from email.utils import formatdate

try:
    import requests
except ImportError:
    sys.stderr.write("gp-alerter: `requests` module required. Install with apt or pip.\n")
    sys.exit(1)


# ── Paths ──────────────────────────────────────────────────────────

CONFIG_PATH = "/etc/phantom/alerts.json"
IDS_EVENTS  = "/etc/phantom/ids-events.json"
HEALTH      = "/etc/phantom/health-state.json"
ACTIVITY    = "/etc/phantom/activity.json"
DEVICES     = "/etc/phantom/device-profiles.json"

POLL_INTERVAL_S = 5
DEDUP_COOLDOWN_S = 60
RATE_LIMIT_PER_MIN = 30

# Severity ordering for threshold comparisons
SEV_ORDER = {"info": 0, "warning": 1, "danger": 2, "critical": 2}


# ── Logging ────────────────────────────────────────────────────────

def log(msg):
    sys.stdout.write(f"[gp-alerter] {msg}\n")
    sys.stdout.flush()

def err(msg):
    sys.stderr.write(f"[gp-alerter] ERROR: {msg}\n")
    sys.stderr.flush()


# ── Config loading ─────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_PATH):
        err(f"config not found at {CONFIG_PATH}; sleeping until one appears")
        return None
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        err(f"failed to read config: {e}")
        return None

def load_secret(pass_ref):
    """Read a single-line secret file (e.g., SMTP password)."""
    if not pass_ref:
        return ""
    try:
        with open(pass_ref) as f:
            return f.read().strip()
    except Exception as e:
        err(f"cannot read secret {pass_ref}: {e}")
        return ""


# ── Rate limiter ───────────────────────────────────────────────────

class RateLimiter:
    """Per-endpoint 30-dispatch-per-minute cap."""
    def __init__(self, per_min=RATE_LIMIT_PER_MIN):
        self.per_min = per_min
        self.windows = {}  # endpoint_key -> deque of recent timestamps

    def allow(self, key):
        now = time.time()
        dq = self.windows.setdefault(key, deque())
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.per_min:
            return False
        dq.append(now)
        return True


# ── Dedup ──────────────────────────────────────────────────────────

class Dedup:
    """Suppress repeats of (trigger, entity) within a cooldown window."""
    def __init__(self, cooldown=DEDUP_COOLDOWN_S):
        self.cooldown = cooldown
        self.seen = {}  # key -> last_dispatch_epoch

    def should_send(self, trigger, entity):
        key = (trigger, entity or "")
        now = time.time()
        last = self.seen.get(key, 0)
        if now - last < self.cooldown:
            return False
        self.seen[key] = now
        return True


# ── Channel dispatchers ────────────────────────────────────────────

def sev_num(sev):
    return SEV_ORDER.get((sev or "info").lower(), 0)

def send_ntfy(endpoint, severity, title, body, tags=None):
    url = endpoint.get("url")
    if not url:
        return False, "missing url"
    headers = {
        "Title": title[:200],
        "Priority": {"info": "2", "warning": "3", "danger": "4", "critical": "5"}.get(
            (severity or "info").lower(), "3"),
    }
    if tags:
        headers["Tags"] = ",".join(tags)[:200]
    try:
        r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=8)
        return r.ok, f"ntfy {r.status_code}"
    except Exception as e:
        return False, f"ntfy error: {e}"

def send_webhook(endpoint, severity, title, body, event_type, source):
    url = endpoint.get("url")
    if not url:
        return False, "missing url"
    payload = {
        "ts": int(time.time()),
        "severity": severity,
        "event": event_type,
        "title": title,
        "body": body,
        "source": source,
    }
    try:
        r = requests.post(url, json=payload, timeout=8)
        return r.ok, f"webhook {r.status_code}"
    except Exception as e:
        return False, f"webhook error: {e}"

def send_discord(endpoint, severity, title, body):
    url = endpoint.get("webhook_url") or endpoint.get("url")
    if not url:
        return False, "missing webhook_url"
    color = {"info": 0x3B82F6, "warning": 0xF59E0B,
             "danger": 0xEF4444, "critical": 0xB91C1C}.get(
                 (severity or "info").lower(), 0x6B7280)
    payload = {
        "username": "GhostPort",
        "embeds": [{
            "title": title[:256],
            "description": body[:2048],
            "color": color,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    }
    try:
        r = requests.post(url, json=payload, timeout=8)
        return r.ok, f"discord {r.status_code}"
    except Exception as e:
        return False, f"discord error: {e}"

def send_email(endpoint, severity, title, body):
    host = endpoint.get("smtp_host")
    port = int(endpoint.get("smtp_port") or 587)
    user = endpoint.get("user")
    password = load_secret(endpoint.get("pass_ref"))
    sender = endpoint.get("from") or user
    to = endpoint.get("to")
    if not (host and user and password and sender and to):
        return False, "email misconfigured (need smtp_host/user/pass_ref/from/to)"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[GhostPort {severity.upper()}] {title}"
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.ehlo()
            try:
                s.starttls()
                s.ehlo()
            except Exception:
                pass  # server may not support TLS; still try plain
            s.login(user, password)
            s.sendmail(sender, [to], msg.as_string())
        return True, "email sent"
    except Exception as e:
        return False, f"email error: {e}"


# ── Dispatch coordinator ───────────────────────────────────────────

def endpoint_is_configured(endpoint):
    """Silently skip endpoints whose required URL/creds aren't filled in."""
    t = (endpoint.get("type") or "").lower()
    if t in ("ntfy", "webhook"):
        return bool(endpoint.get("url"))
    if t == "discord":
        return bool(endpoint.get("webhook_url") or endpoint.get("url"))
    if t == "email":
        return all(endpoint.get(k) for k in ("smtp_host", "user", "from", "to"))
    return False


def dispatch(config, rate, event):
    """Send event to all endpoints whose priority_min is met."""
    sev = (event.get("severity") or "info").lower()
    title = event.get("title") or "GhostPort event"
    body = event.get("body") or ""
    event_type = event.get("type") or "generic"
    source = event.get("source") or ""
    tags = event.get("tags")

    for idx, endpoint in enumerate(config.get("endpoints", [])):
        if not endpoint_is_configured(endpoint):
            continue
        min_sev = (endpoint.get("priority_min") or "info").lower()
        if sev_num(sev) < sev_num(min_sev):
            continue

        ep_type = (endpoint.get("type") or "").lower()
        ep_key = f"{ep_type}:{idx}"
        if not rate.allow(ep_key):
            err(f"rate limit hit on {ep_key}; dropping '{title}'")
            continue

        if ep_type == "ntfy":
            ok, info = send_ntfy(endpoint, sev, title, body, tags)
        elif ep_type == "webhook":
            ok, info = send_webhook(endpoint, sev, title, body, event_type, source)
        elif ep_type == "discord":
            ok, info = send_discord(endpoint, sev, title, body)
        elif ep_type == "email":
            ok, info = send_email(endpoint, sev, title, body)
        else:
            err(f"unknown endpoint type {ep_type!r}")
            continue

        (log if ok else err)(f"{ep_key} -> {info} :: {title}")


# ── Event source watchers ──────────────────────────────────────────

def safe_json_load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


class Watermarks:
    """Track progress through each event source so we only fire on NEW events."""
    def __init__(self):
        # IDS events: remember max epoch seen
        self.ids_last_epoch = 0
        # Activity: remember length
        self.activity_last_len = 0
        # Devices: remember set of known MACs
        self.known_macs = set()
        # Health: remember last mode + warnings
        self.health_mode = None
        self.health_warnings = 0

    def initialize(self):
        """On startup, capture the current tails so historical events don't fire."""
        for event in safe_json_load(IDS_EVENTS, []):
            ep = int(event.get("epoch") or 0)
            if ep > self.ids_last_epoch:
                self.ids_last_epoch = ep

        self.activity_last_len = len(safe_json_load(ACTIVITY, []))

        devs = safe_json_load(DEVICES, {})
        if isinstance(devs, dict):
            self.known_macs = set(devs.keys())

        h = safe_json_load(HEALTH, {})
        self.health_mode = h.get("mode")
        self.health_warnings = int(h.get("warnings") or 0)
        log(f"watermarks: ids_epoch={self.ids_last_epoch}, "
            f"activity_len={self.activity_last_len}, "
            f"devices={len(self.known_macs)}, "
            f"mode={self.health_mode}")


def map_ids_severity(sev_raw):
    """IDS emits INFO/WARNING/DANGER/CRITICAL; normalise to our 4-level scale."""
    s = (sev_raw or "").upper()
    if s == "CRITICAL": return "critical"
    if s == "DANGER":   return "danger"
    if s == "WARNING":  return "warning"
    return "info"


def poll_once(config, rate, dedup, water, triggers):
    """One polling cycle. Fires any events that match enabled triggers."""

    # ── IDS events
    if triggers.get("ids_alert", True):
        events = safe_json_load(IDS_EVENTS, [])
        new_max = water.ids_last_epoch
        for ev in events:
            ep = int(ev.get("epoch") or 0)
            if ep <= water.ids_last_epoch:
                continue
            if ep > new_max:
                new_max = ep
            sev = map_ids_severity(ev.get("severity"))
            src = ev.get("source") or ""
            etype = ev.get("type") or "ids"
            if not dedup.should_send(f"ids:{etype}", src):
                continue
            dispatch(config, rate, {
                "severity": sev,
                "type": f"ids.{etype}",
                "source": src,
                "title": f"IDS: {etype} from {src}",
                "body": f"{ev.get('detail', '')}\n{ev.get('education', '')}".strip(),
                "tags": ["rotating_light", "ghostport"],
            })
        water.ids_last_epoch = new_max

    # ── Activity: tunnel_down / new logins
    if triggers.get("tunnel_down", True):
        activity = safe_json_load(ACTIVITY, [])
        if len(activity) > water.activity_last_len:
            for entry in activity[water.activity_last_len:]:
                msg = (entry.get("message") or "").lower()
                etype = (entry.get("type") or "").lower()
                if "tunnel" in msg and ("down" in msg or "fail" in msg or "disconnect" in msg):
                    if dedup.should_send("tunnel_down", etype):
                        dispatch(config, rate, {
                            "severity": "danger",
                            "type": "tunnel.down",
                            "source": etype,
                            "title": "Tunnel down",
                            "body": entry.get("message", ""),
                            "tags": ["warning", "ghostport"],
                        })
            water.activity_last_len = len(activity)

    # ── New device
    if triggers.get("new_device", True):
        devs = safe_json_load(DEVICES, {})
        if isinstance(devs, dict):
            current = set(devs.keys())
            new_macs = current - water.known_macs
            for mac in new_macs:
                if dedup.should_send("new_device", mac):
                    info = devs.get(mac, {}) if isinstance(devs.get(mac, {}), dict) else {}
                    hostname = info.get("hostname") or info.get("name") or "unknown"
                    dispatch(config, rate, {
                        "severity": "warning",
                        "type": "device.new",
                        "source": mac,
                        "title": f"New device on network: {hostname}",
                        "body": f"MAC: {mac}\nHost: {hostname}\nFirst seen: {info.get('first_seen', 'just now')}",
                        "tags": ["eyes", "ghostport"],
                    })
            water.known_macs = current

    # ── Health: mode change, warnings growth
    h = safe_json_load(HEALTH, {})
    new_mode = h.get("mode")
    new_warn = int(h.get("warnings") or 0)
    if new_mode and new_mode != water.health_mode:
        if dedup.should_send("mode_change", new_mode):
            dispatch(config, rate, {
                "severity": "info",
                "type": "mode.change",
                "source": new_mode,
                "title": f"Mode changed to {new_mode}",
                "body": f"Previous mode: {water.health_mode or 'unknown'}\nNew mode: {new_mode}",
                "tags": ["gear", "ghostport"],
            })
        water.health_mode = new_mode

    if new_warn > water.health_warnings:
        if dedup.should_send("health_warnings", str(new_warn)):
            dispatch(config, rate, {
                "severity": "warning",
                "type": "health.warning",
                "source": "health-guard",
                "title": f"Health warnings: {new_warn}",
                "body": f"Mode: {h.get('mode')}\nTemp: {h.get('temp_c')}°C\nMem: {h.get('mem_used_pct')}%\nDisk: {h.get('disk_used_pct')}%",
                "tags": ["warning", "ghostport"],
            })
    water.health_warnings = new_warn


# ── Main loop ──────────────────────────────────────────────────────

def main():
    log("starting")
    rate = RateLimiter()
    dedup = Dedup()
    water = Watermarks()
    water.initialize()

    while True:
        config = load_config()
        if not config:
            time.sleep(POLL_INTERVAL_S * 4)
            continue
        triggers = config.get("triggers", {})
        try:
            poll_once(config, rate, dedup, water, triggers)
        except Exception as e:
            err(f"poll cycle failed: {e}")
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
