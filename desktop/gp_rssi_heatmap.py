"""gp_rssi_heatmap — terminal RSSI heatmap per BSSID over last hour (T-0056).

Reads beacon_seen events from events.db, buckets observations into 12 × 5-min
windows, renders a per-BSSID strip of unicode block characters whose intensity
encodes RSSI strength.

  Block intensity scale:
    -50 dBm or stronger  →  █ (full)
    -90 dBm or weaker    →  · (sparse dot)
    no observation       →  (space)

Highlights:
  *   our own BSSID (matches /etc/hostapd/hostapd.conf bssid)
  !   BSSIDs that have been flagged by gp-neighbor-baseline in last 1h

Invoked from gp-heatmap rssi.
"""
import os
import re
import sqlite3
import sys
import time
import json

EVENTS_DB = os.path.expanduser("~/.local/share/phantom/events.db")
WINDOW_S = 300              # 5-min buckets
BUCKETS = 12                # last 60 min
RSSI_STRONG_DBM = -50
RSSI_WEAK_DBM = -90
BLOCKS = " ·░▒▓█"  # 6-step intensity ramp


def _own_bssid():
    """Read our own AP's BSSID from hostapd. Return '' if unreadable."""
    try:
        # bssid line is rare — most installs use the MAC of wlan0. Read live.
        with open("/sys/class/net/wlan0/address") as f:
            return f.read().strip().lower()
    except Exception:
        return ""


def _flagged_bssids():
    """BSSIDs that have a recent neighbor-baseline drift event (last hour)."""
    flagged = set()
    if not os.path.exists(EVENTS_DB):
        return flagged
    now = time.time()
    try:
        c = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True).cursor()
        c.execute(
            "SELECT details FROM events WHERE source='neighbor-baseline' "
            "AND timestamp > ? AND severity IN ('dangerous','warning')",
            (now - 3600,),
        )
        for (details,) in c.fetchall():
            try:
                d = json.loads(details) if details else {}
            except Exception:
                continue
            b = d.get("bssid")
            if b:
                flagged.add(b)
    except sqlite3.Error:
        pass
    return flagged


def _rssi_to_block(rssi):
    """Map an RSSI dBm value to a block character. None → space."""
    if rssi is None:
        return " "
    rssi = int(rssi)
    if rssi >= RSSI_STRONG_DBM:
        return BLOCKS[-1]
    if rssi <= RSSI_WEAK_DBM:
        return BLOCKS[1]  # sparse dot — saw it but very weak
    # Linear interpolate over the dB range to a block index 2..len-1
    span = RSSI_STRONG_DBM - RSSI_WEAK_DBM
    frac = (rssi - RSSI_WEAK_DBM) / span
    idx = 2 + int(frac * (len(BLOCKS) - 3))
    idx = max(2, min(idx, len(BLOCKS) - 1))
    return BLOCKS[idx]


def _gather():
    """Return {bssid: {ssid, vendor, last_rssi, buckets[12]}} for last hour."""
    now = time.time()
    out = {}
    if not os.path.exists(EVENTS_DB):
        return out, now
    try:
        c = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True).cursor()
        c.execute(
            "SELECT timestamp, details FROM events WHERE source='sonar-sniffer' "
            "AND category='beacon_seen' AND timestamp > ? ORDER BY timestamp ASC",
            (now - WINDOW_S * BUCKETS,),
        )
        for ts, details in c.fetchall():
            try:
                d = json.loads(details) if details else {}
            except Exception:
                continue
            bssid = d.get("bssid")
            if not bssid:
                continue
            entry = out.setdefault(bssid, {
                "ssid": d.get("ssid") or "",
                "vendor": d.get("vendor"),
                "last_rssi": None,
                "buckets": [None] * BUCKETS,
            })
            # bucket 0 = oldest, bucket BUCKETS-1 = newest
            age_s = now - ts
            bucket_idx = (BUCKETS - 1) - int(age_s // WINDOW_S)
            if 0 <= bucket_idx < BUCKETS:
                # Strongest sample wins per bucket (closer = more reliable)
                rssi = d.get("rssi_dbm")
                if rssi is not None:
                    cur = entry["buckets"][bucket_idx]
                    if cur is None or rssi > cur:
                        entry["buckets"][bucket_idx] = rssi
                entry["last_rssi"] = rssi if rssi is not None else entry["last_rssi"]
                entry["ssid"] = d.get("ssid") or entry["ssid"]
                entry["vendor"] = d.get("vendor") or entry["vendor"]
    except sqlite3.Error:
        return out, now
    return out, now


def render():
    own = _own_bssid()
    flagged = _flagged_bssids()
    data, now = _gather()
    if not data:
        print("(no beacon_seen events in last hour — sniffer may be off, "
              "or monitor radio hasn't been deployed)")
        return 0

    rows = sorted(data.items(), key=lambda kv: -(kv[1]["last_rssi"] or -120))

    print(f"\nRF Neighborhood — RSSI heatmap (last 60 min, {WINDOW_S // 60}-min buckets)")
    print(f"Strong {BLOCKS[-1]} ─────── Weak {BLOCKS[1]}    blank = no observation")
    print(f"{'BSSID':<19} {'SSID':<22} {'Vendor':<20} {'-60m':<6}{'heatmap':<{BUCKETS}}{'-now':<5} {'last':>6} flag")
    print("-" * (19 + 22 + 20 + 6 + BUCKETS + 5 + 7 + 6))
    for bssid, e in rows:
        strip = "".join(_rssi_to_block(b) for b in e["buckets"])
        ssid = (e["ssid"] or "(hidden)")[:22]
        vendor = (e["vendor"] or "—")[:20]
        last = e["last_rssi"]
        last_str = f"{last:>5}" if last is not None else "  ?"
        flag = ""
        if bssid == own:
            flag = "*own"
        elif bssid in flagged:
            flag = "!alert"
        print(f"{bssid:<19} {ssid:<22} {vendor:<20} {'':<6}{strip:<{BUCKETS}}{'':<5} {last_str:>6} {flag}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(render())
