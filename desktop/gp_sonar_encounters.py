"""gp_sonar_encounters — persist Karma encounters across Sonar sessions (T-0034).

Sonar's in-memory karma_rigs dict (gp-sonar.py:1260) is per-session by design — a
Karma rig is a temporal/location threat. But customers want forensic memory across
sessions: the same Pineapple at the same coffee shop two Tuesdays in a row is a
much louder signal than either visit alone.

This module appends every newly-detected Karma rig to a JSON-lines log under
~/.local/share/phantom/sonar-encounters.log. Append-only, rotation at 10000 lines
or 10MB (whichever first), single rollover slot (.1) so total disk footprint
stays bounded at ~20MB.

Schema (one record per line):
    {
      "ts": ISO8601 UTC,
      "bssid": "aa:bb:cc:...",
      "decoy_ssid": "Starbucks WiFi",
      "real_ssid": "what the rig actually beacons",
      "reason": "...identity mismatch... / probe-replies-to-decoy / ...",
      "signal_dbm": int | null,
      "context": {
          "our_ssid": "GhostPort SSID at time of encounter",
          "our_bssid": "...",
      }
    }

The append path is deliberately lenient: any I/O error is swallowed and reported
via the returned bool, so a disk-full or perms problem never blocks the GUI.
"""
import json
import os
import time

ENCOUNTERS_DIR = os.path.expanduser("~/.local/share/phantom")
ENCOUNTERS_FILE = os.path.join(ENCOUNTERS_DIR, "sonar-encounters.log")

MAX_LINES = 10000
MAX_BYTES = 10 * 1024 * 1024  # 10MB


def _ensure_dir():
    try:
        os.makedirs(ENCOUNTERS_DIR, exist_ok=True)
        return True
    except OSError:
        return False


def _line_count(path):
    """Cheap line count via byte scan; returns 0 on any error."""
    try:
        n = 0
        with open(path, "rb") as f:
            for _ in f:
                n += 1
        return n
    except OSError:
        return 0


def _rotate_if_needed(path):
    """Rotate path → path.1 when over MAX_LINES or MAX_BYTES. The previous .1
    is overwritten — we keep one rollover slot only. Best-effort.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < MAX_BYTES and _line_count(path) < MAX_LINES:
        return
    try:
        os.replace(path, path + ".1")
    except OSError:
        # Couldn't rotate; surface as no-op so writes continue into oversized file.
        # Better to grow than to lose evidence.
        pass


def append_encounter(bssid, decoy_ssid, real_ssid, reason, signal_dbm=None,
                     our_ssid="", our_bssid=""):
    """Append one Karma encounter to the log. Returns True on success.

    Errors are swallowed — Sonar's GUI must never block on this. Callers can
    inspect the bool if they want to surface a status message, but they should
    not raise.
    """
    if not _ensure_dir():
        return False
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bssid": (bssid or "").lower(),
        "decoy_ssid": decoy_ssid or "",
        "real_ssid": real_ssid or "",
        "reason": reason or "",
        "signal_dbm": signal_dbm,
        "context": {
            "our_ssid": our_ssid or "",
            "our_bssid": (our_bssid or "").lower(),
        },
    }
    try:
        _rotate_if_needed(ENCOUNTERS_FILE)
        with open(ENCOUNTERS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_encounters(limit=None, bssid=None):
    """Yield encounter records from oldest first across the rolled file + live file.

    Args:
        limit: cap total records yielded (most-recent first if set).
        bssid: filter to this BSSID only (case-insensitive).

    Errors are swallowed — returns whatever could be read.
    """
    bssid = (bssid or "").lower() or None
    paths = [ENCOUNTERS_FILE + ".1", ENCOUNTERS_FILE]
    records = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if bssid and rec.get("bssid", "").lower() != bssid:
                        continue
                    records.append(rec)
        except OSError:
            continue
    if limit:
        records = records[-limit:]
    return records
