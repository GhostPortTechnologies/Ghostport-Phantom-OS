"""Sonar — scan history persistence helper (T-0035).

Persists each successful Sonar scan as a JSON snapshot under
~/.local/share/phantom/sonar-scan-history/<unix-ts>.json so customers can
diff today vs yesterday and spot APs that appeared, vanished, or shifted
between sessions. Forensic value across location-stable use cases (home,
office) where signal-to-noise is high.

Schema: {"ts": int, "our_ssid": str, "our_bssid": str, "aps": [...]}.

Capped at SNAPSHOT_MAX snapshots and TOTAL_BYTES_MAX disk total. Oldest
snapshots evict first when either cap is exceeded.

All errors are swallowed and surfaced as ok=False return values so a
failed record() never breaks the scan UX. Atomic writes (temp + replace)
guard against half-written snapshots after crash mid-scan.
"""
import json
import os
import time

HISTORY_DIR = os.path.expanduser("~/.local/share/phantom/sonar-scan-history")
SNAPSHOT_MAX = 20
TOTAL_BYTES_MAX = 10 * 1024 * 1024  # 10 MB


def _ensure_dir():
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        return True
    except OSError:
        return False


def list_snapshots():
    """Return sorted list of snapshot timestamps (oldest first). Empty
    list on missing dir or unreadable contents.
    """
    try:
        entries = []
        for name in os.listdir(HISTORY_DIR):
            if not name.endswith(".json"):
                continue
            stem = name[:-5]
            try:
                entries.append(int(stem))
            except ValueError:
                continue
        return sorted(entries)
    except OSError:
        return []


def load_snapshot(ts):
    """Return the snapshot dict for the given ts, or None on any error."""
    path = os.path.join(HISTORY_DIR, f"{int(ts)}.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record(aps, our_ssid, our_bssid):
    """Persist a snapshot. Returns (ok, ts_or_None, error_or_None).

    Idempotent on identical timestamps — at most one snapshot per second.
    """
    if not _ensure_dir():
        return False, None, "history directory unavailable"
    ts = int(time.time())
    path = os.path.join(HISTORY_DIR, f"{ts}.json")
    tmp = path + ".tmp"
    snapshot = {
        "ts": ts,
        "our_ssid": our_ssid,
        "our_bssid": our_bssid,
        "aps": aps,
    }
    try:
        with open(tmp, "w") as f:
            json.dump(snapshot, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False, None, str(exc)
    prune()
    return True, ts, None


def _delete(ts):
    path = os.path.join(HISTORY_DIR, f"{int(ts)}.json")
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def prune():
    """Trim history to SNAPSHOT_MAX entries and TOTAL_BYTES_MAX bytes.
    Oldest first. Returns count of snapshots removed.
    """
    removed = 0
    snapshots = list_snapshots()
    overflow = max(0, len(snapshots) - SNAPSHOT_MAX)
    for ts in snapshots[:overflow]:
        if _delete(ts):
            removed += 1
    snapshots = list_snapshots()
    sizes = []
    total = 0
    for ts in snapshots:
        path = os.path.join(HISTORY_DIR, f"{ts}.json")
        try:
            sz = os.path.getsize(path)
        except OSError:
            sz = 0
        sizes.append((ts, sz))
        total += sz
    while total > TOTAL_BYTES_MAX and sizes:
        ts, sz = sizes.pop(0)
        if _delete(ts):
            removed += 1
            total -= sz
    return removed


_DIFF_KEYS = ("ssid", "channel", "encryption", "ie_fingerprint", "signature_match")


def diff(ts_a, ts_b):
    """Diff two snapshots by BSSID. Returns dict with added/removed/changed
    or None if either snapshot can't be loaded.
    """
    snap_a = load_snapshot(ts_a)
    snap_b = load_snapshot(ts_b)
    if snap_a is None or snap_b is None:
        return None
    a_aps = {ap["bssid"]: ap for ap in snap_a.get("aps", []) if "bssid" in ap}
    b_aps = {ap["bssid"]: ap for ap in snap_b.get("aps", []) if "bssid" in ap}
    added = sorted(set(b_aps) - set(a_aps))
    removed = sorted(set(a_aps) - set(b_aps))
    changed = []
    for bssid in sorted(set(a_aps) & set(b_aps)):
        ap_a, ap_b = a_aps[bssid], b_aps[bssid]
        deltas = {}
        for key in _DIFF_KEYS:
            if ap_a.get(key) != ap_b.get(key):
                deltas[key] = (ap_a.get(key), ap_b.get(key))
        if deltas:
            changed.append({"bssid": bssid, "deltas": deltas})
    return {"added": added, "removed": removed, "changed": changed}
