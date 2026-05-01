"""gp_neighbor_baseline — RF-environment baseline + drift alerts (T-0055).

Companion to gp-lookout, scoped specifically to the surrounding Wi-Fi environment.
Reads beacon_seen events from gp_events DB, maintains a per-BSSID rolling
baseline of presence, security suite, vendor, and signal strength. After a
6h warm-up, emits gp_events alerts when:

  - an "expected" neighbor (seen in >50% of windows) disappears
  - a new strong-signal AP appears (RSSI > NEW_AP_RSSI_THRESHOLD)
  - a known BSSID's security suite changes
  - a known BSSID's vendor changes

State at: ~/.local/share/phantom/neighbor-baseline.json (atomic writes).

Designed to run as a 5-minute systemd timer (ghostport-neighbor-baseline.timer).
Each invocation reads the last 5-min window, updates baseline, emits any alerts,
exits 0.
"""
import json
import os
import sqlite3
import sys
import time
import hashlib
import tempfile

EVENTS_DB = os.path.expanduser("~/.local/share/phantom/events.db")
BASELINE_FILE = os.path.expanduser("~/.local/share/phantom/neighbor-baseline.json")
BASELINE_TMP = BASELINE_FILE + ".tmp"

WINDOW_S = 300                  # 5 min
WARMUP_WINDOWS = 72             # 6h before any alerts fire
EXPECTED_THRESHOLD = 0.5        # seen in >50% of past windows
NEW_AP_RSSI_THRESHOLD = -75     # dBm; only flag strong-signal new APs
DISAPPEARED_GRACE_WINDOWS = 2   # tolerate 2 missed windows before alerting

sys.path.insert(0, "/opt/ghostport/desktop")

try:
    import gp_events
except Exception as e:
    print(f"[gp-neighbor-baseline] gp_events import failed: {e}", file=sys.stderr)
    sys.exit(0)


def _security_hash(security_dict):
    """Stable hash of {mode, cipher, akm, mfp} so we can detect drift."""
    if not isinstance(security_dict, dict):
        return ""
    payload = {
        "mode":   security_dict.get("mode") or "",
        "cipher": security_dict.get("cipher") or "",
        "akm":    sorted(security_dict.get("akm") or []),
        "mfp":    security_dict.get("mfp") or "",
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def _load_baseline():
    """Load baseline state. Returns the shape we expect on a clean install."""
    try:
        with open(BASELINE_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict) and "version" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {
        "version": 1,
        "first_collected_ts": int(time.time()),
        "window_count": 0,
        "bssids": {},
        "last_window_end": 0,
    }


def _save_baseline(state):
    """Atomic write to BASELINE_FILE."""
    os.makedirs(os.path.dirname(BASELINE_FILE), exist_ok=True)
    fd, path = tempfile.mkstemp(dir=os.path.dirname(BASELINE_FILE), prefix=".nb-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(path, BASELINE_FILE)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _read_window(now_ts):
    """Read all beacon_seen events in (now - WINDOW_S, now]. Returns dict of
    bssid → most-recent details for that BSSID in the window."""
    seen = {}
    if not os.path.exists(EVENTS_DB):
        return seen
    try:
        c = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True).cursor()
        c.execute(
            "SELECT timestamp, details FROM events "
            "WHERE source='sonar-sniffer' AND category='beacon_seen' "
            "AND timestamp > ? ORDER BY timestamp ASC",
            (now_ts - WINDOW_S,),
        )
        for ts, details in c.fetchall():
            try:
                d = json.loads(details) if details else {}
            except Exception:
                continue
            bssid = d.get("bssid")
            if not bssid:
                continue
            seen[bssid] = d  # latest wins
    except sqlite3.Error:
        pass
    return seen


def collect_once():
    """One baseline tick: read window, update state, emit any drift alerts.
    Idempotent on a fresh run within the same window — won't double-count."""
    now_ts = int(time.time())
    state = _load_baseline()

    # Skip if we already collected within this window.
    if now_ts - state.get("last_window_end", 0) < WINDOW_S:
        return 0

    seen = _read_window(now_ts)
    bssids = state["bssids"]
    state["window_count"] += 1
    state["last_window_end"] = now_ts
    warmed_up = state["window_count"] >= WARMUP_WINDOWS

    # ── update per-BSSID state for everything seen this window ──
    for bssid, d in seen.items():
        b = bssids.setdefault(bssid, {
            "first_seen": now_ts,
            "windows_present": 0,
            "ssid": d.get("ssid") or "",
            "vendor": d.get("vendor"),
            "security_hash": "",
            "last_rssi_dbm": d.get("rssi_dbm", 0),
            "channel": d.get("channel", 0),
            "last_seen": now_ts,
        })
        prev_sec_hash = b.get("security_hash", "")
        prev_vendor = b.get("vendor")

        new_sec_hash = _security_hash(d.get("security") or {})

        b["windows_present"] += 1
        b["last_seen"] = now_ts
        b["last_rssi_dbm"] = d.get("rssi_dbm", b.get("last_rssi_dbm", 0))
        b["channel"] = d.get("channel", b.get("channel", 0))
        b["security_hash"] = new_sec_hash
        b["vendor"] = d.get("vendor", prev_vendor)
        b["ssid"] = d.get("ssid") or b.get("ssid", "")

        # ── alerts (only after warm-up) ──
        if not warmed_up:
            continue

        # New AP appeared (first sighting + strong signal)
        if b["windows_present"] == 1 and (b["last_rssi_dbm"] or -99) > NEW_AP_RSSI_THRESHOLD:
            gp_events.emit(
                source="neighbor-baseline",
                category="neighbor_appeared",
                severity=gp_events.SEVERITY_INFO,
                summary=f"new AP appeared: {b['ssid'] or '(hidden)'} ({bssid}) @ {b['last_rssi_dbm']} dBm",
                details={"bssid": bssid, "ssid": b["ssid"], "rssi_dbm": b["last_rssi_dbm"],
                         "vendor": b["vendor"], "channel": b["channel"]},
            )
        # Security suite changed on a known BSSID
        if prev_sec_hash and new_sec_hash and prev_sec_hash != new_sec_hash:
            gp_events.emit(
                source="neighbor-baseline",
                category="neighbor_security_changed",
                severity=gp_events.SEVERITY_DANGEROUS,
                summary=f"security changed: {b['ssid'] or '(hidden)'} ({bssid})",
                details={"bssid": bssid, "ssid": b["ssid"],
                         "prev_security_hash": prev_sec_hash,
                         "new_security_hash": new_sec_hash,
                         "new_security": d.get("security")},
            )
        # Vendor changed (BSSID's OUI mapping flipped — implausible naturally)
        if prev_vendor and b["vendor"] and prev_vendor != b["vendor"]:
            gp_events.emit(
                source="neighbor-baseline",
                category="neighbor_vendor_changed",
                severity=gp_events.SEVERITY_DANGEROUS,
                summary=f"vendor flipped: {b['ssid'] or '(hidden)'} ({bssid}) {prev_vendor}→{b['vendor']}",
                details={"bssid": bssid, "ssid": b["ssid"],
                         "prev_vendor": prev_vendor, "new_vendor": b["vendor"]},
            )

    # ── disappearance alerts (BSSIDs in baseline but missed this window) ──
    if warmed_up:
        for bssid, b in list(bssids.items()):
            if bssid in seen:
                continue
            ratio = b["windows_present"] / max(state["window_count"], 1)
            if ratio < EXPECTED_THRESHOLD:
                continue
            windows_since = (now_ts - b.get("last_seen", 0)) // WINDOW_S
            if windows_since == DISAPPEARED_GRACE_WINDOWS:
                # fire exactly once at the boundary; spam-guard via the equality check
                gp_events.emit(
                    source="neighbor-baseline",
                    category="neighbor_disappeared",
                    severity=gp_events.SEVERITY_INFO,
                    summary=f"expected neighbor missing: {b.get('ssid') or '(hidden)'} ({bssid})",
                    details={"bssid": bssid, "ssid": b.get("ssid"),
                             "vendor": b.get("vendor"),
                             "windows_present_ratio": round(ratio, 2),
                             "windows_since_last_seen": windows_since},
                )

    _save_baseline(state)
    return len(seen)


def status():
    """Print one-shot baseline summary to stdout (no alerts emitted)."""
    state = _load_baseline()
    wc = state.get("window_count", 0)
    bssids = state.get("bssids", {})
    warm = wc >= WARMUP_WINDOWS
    expected = sum(1 for b in bssids.values() if b["windows_present"] / max(wc, 1) >= EXPECTED_THRESHOLD)
    print(f"window_count:    {wc}")
    print(f"warm:            {warm} (need {WARMUP_WINDOWS})")
    print(f"baselined BSSIDs: {len(bssids)}")
    print(f"  expected (>50% presence): {expected}")
    if bssids:
        print()
        print(f"  {'BSSID':<19} {'SSID':<22} {'pres %':>7} {'last RSSI':>10} {'last seen':>14}")
        for bssid, b in sorted(bssids.items(), key=lambda x: -x[1]["windows_present"])[:12]:
            ratio = b["windows_present"] / max(wc, 1) * 100
            age = int(time.time() - b.get("last_seen", 0))
            mins = age // 60
            print(f"  {bssid:<19} {(b.get('ssid') or '(hidden)')[:22]:<22} {ratio:>6.1f}% {b.get('last_rssi_dbm','?'):>10} {mins:>10}m ago")


def reset():
    """Wipe baseline state."""
    try:
        os.unlink(BASELINE_FILE)
        print(f"removed {BASELINE_FILE}")
    except FileNotFoundError:
        print("no baseline file to remove")


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("Usage: gp_neighbor_baseline.py [collect|status|reset]")
        return 0
    cmd = argv[1]
    if cmd == "collect":
        n = collect_once()
        print(f"collected {n} BSSIDs in last {WINDOW_S}s")
        return 0
    if cmd == "status":
        status()
        return 0
    if cmd == "reset":
        reset()
        return 0
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
