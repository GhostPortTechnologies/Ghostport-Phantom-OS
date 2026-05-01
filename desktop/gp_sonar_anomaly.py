"""gp_sonar_anomaly — beacon-profile anomaly scoring for Sonar.

Learns the "normal" beacon profile of every AP Sonar has seen, then scores
new observations for drift. Catches the slow attacks static rules miss:

  - "This AP I've watched for 3 weeks just changed channel."
  - "RSSI of my router jumped 15 dB suddenly."
  - "A new IE element appeared on my AP that wasn't there last week."

Design choices
--------------
- Stdlib only — no scipy / numpy / sklearn. Welford running variance is enough.
- Per-AP record stored as JSON in ~/.local/share/phantom/sonar-baseline.json.
  Same user-writable path as gp-correlator's findings file; /etc/phantom is
  not user-writable for the ghostport-admin user.
- Multi-feature additive score so several small drifts can alert even when
  no single feature crosses the 2-sigma threshold.
- "Still learning" flag below MIN_OBSERVATIONS — protects the user from
  false positives during the first few days when the baseline is thin.
- Atomic writes (tempfile → fsync → os.replace) — same pattern T-0017
  enforced for save_trusted_aps.
- Hard caps on entry count + file size so a flood of one-off SSIDs from
  a busy public WiFi area can't grow the baseline without bound.

Public API
----------
    update_baseline(scan)               # absorb one scan's observations
    score(observation, baseline=None)   # z-score-based anomaly value
    score_scan(scan)                    # {bssid: score} for one scan
    emit_anomalies(scan, threshold=2.0) # score + push to gp_events bus
    prune_stale(max_age_days=30)        # drop APs unseen for N days
    status_summary()                    # {ap_count, oldest_seen, ...}
    reset_baseline()                    # wipe (for testing / operator reset)

Each `observation` / `scan` entry is a dict with at least:
    {"bssid": str, "ssid": str, "channel": int|str, "rssi": int,
     "ie_hash": str (optional), "beacon_interval": int (optional)}

CLI: `python3 gp_sonar_anomaly.py --status | --reset | --help`
"""

import json
import math
import os
import sys
import time

# Bus is sibling module in the same directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gp_events  # noqa: E402

BASELINE_DIR = os.path.expanduser("~/.local/share/phantom")
BASELINE_FILE = os.path.join(BASELINE_DIR, "sonar-baseline.json")
TMP_FILE = BASELINE_FILE + ".tmp"

MIN_OBSERVATIONS = 50          # below this, score returns 0 with "learning"
DEFAULT_THRESHOLD = 2.0        # alert score
MAX_AP_ENTRIES = 500           # LRU cap on baseline (oldest-seen evicted)
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10MB hard ceiling
MAX_HISTOGRAM_KEYS = 32        # per-feature histogram cap (channels, IE hashes)

SOURCE = "sonar"
CATEGORY_ANOMALY = "beacon_anomaly"


# ── Storage ──────────────────────────────────────────────────────────

def _empty_record(bssid, ssid):
    return {
        "bssid": bssid,
        "ssid": ssid or "",
        "first_seen": time.time(),
        "last_seen": time.time(),
        "obs_count": 0,
        "rssi":   {"n": 0, "mean": 0.0, "m2": 0.0},
        "channels": {},
        "ie_hashes": {},
        "beacon_interval": {"n": 0, "mean": 0.0, "m2": 0.0},
    }


def _load():
    try:
        with open(BASELINE_FILE) as f:
            data = json.load(f)
            if not isinstance(data, dict) or "aps" not in data:
                return {"version": 1, "aps": {}}
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "aps": {}}


def _save(data):
    os.makedirs(BASELINE_DIR, exist_ok=True)
    payload = json.dumps(data, indent=2)
    if len(payload.encode("utf-8")) > MAX_FILE_BYTES:
        # Evict oldest-seen until we fit. Caller should also call prune_stale.
        aps = data.get("aps", {})
        ordered = sorted(aps.items(), key=lambda kv: kv[1].get("last_seen", 0))
        while ordered and len(json.dumps(data).encode("utf-8")) > MAX_FILE_BYTES:
            bssid, _ = ordered.pop(0)
            aps.pop(bssid, None)
        payload = json.dumps(data, indent=2)
    with open(TMP_FILE, "w") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(TMP_FILE, BASELINE_FILE)


def _evict_lru(data):
    aps = data.get("aps", {})
    if len(aps) <= MAX_AP_ENTRIES:
        return
    ordered = sorted(aps.items(), key=lambda kv: kv[1].get("last_seen", 0))
    drop = len(aps) - MAX_AP_ENTRIES
    for bssid, _ in ordered[:drop]:
        aps.pop(bssid, None)


# ── Welford running mean / variance ─────────────────────────────────

def _welford_update(stat, value):
    """Update {n, mean, m2} in place with one new observation."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    stat["n"] += 1
    delta = v - stat["mean"]
    stat["mean"] += delta / stat["n"]
    delta2 = v - stat["mean"]
    stat["m2"] += delta * delta2


def _stddev(stat):
    if stat["n"] < 2:
        return 0.0
    return math.sqrt(stat["m2"] / (stat["n"] - 1))


# ── Histogram helpers ───────────────────────────────────────────────

def _bump_histogram(hist, key):
    if key in (None, ""):
        return
    k = str(key)
    hist[k] = hist.get(k, 0) + 1
    if len(hist) > MAX_HISTOGRAM_KEYS:
        # Drop the rarest key to bound the dict.
        rarest = min(hist.items(), key=lambda kv: kv[1])[0]
        if rarest != k:
            hist.pop(rarest, None)


def _hist_freq(hist, key):
    if not hist or key in (None, ""):
        return 0.0
    total = sum(hist.values())
    if not total:
        return 0.0
    return hist.get(str(key), 0) / total


# ── Public API ──────────────────────────────────────────────────────

def update_baseline(scan):
    """Absorb one scan's beacon observations into the baseline."""
    if not scan:
        return
    data = _load()
    aps = data.setdefault("aps", {})
    now = time.time()
    for obs in scan:
        bssid = (obs.get("bssid") or "").lower().strip()
        if not bssid:
            continue
        rec = aps.get(bssid) or _empty_record(bssid, obs.get("ssid"))
        rec["last_seen"] = now
        rec["obs_count"] += 1
        if obs.get("ssid"):
            rec["ssid"] = obs["ssid"]
        if obs.get("rssi") is not None:
            _welford_update(rec["rssi"], obs["rssi"])
        if obs.get("channel") is not None:
            _bump_histogram(rec["channels"], obs["channel"])
        if obs.get("ie_hash"):
            _bump_histogram(rec["ie_hashes"], obs["ie_hash"])
        if obs.get("beacon_interval") is not None:
            _welford_update(rec["beacon_interval"], obs["beacon_interval"])
        aps[bssid] = rec
    _evict_lru(data)
    _save(data)


def score(observation, baseline=None):
    """Return (score, is_learning, evidence) for one observation.

    score is the additive deviation across all features. is_learning is True
    while the AP has fewer than MIN_OBSERVATIONS samples — score is 0 in that
    state. evidence is a list of (feature, contribution) for explainability.
    """
    if baseline is None:
        baseline = _load()
    aps = baseline.get("aps", {})
    bssid = (observation.get("bssid") or "").lower().strip()
    rec = aps.get(bssid)
    if not rec:
        return 0.0, True, [("unseen_bssid", 0.0)]
    if rec["obs_count"] < MIN_OBSERVATIONS:
        return 0.0, True, [("learning", float(rec["obs_count"]))]

    total = 0.0
    evidence = []
    rssi = observation.get("rssi")
    if rssi is not None and rec["rssi"]["n"] >= 2:
        sd = _stddev(rec["rssi"])
        if sd > 0:
            z = abs(float(rssi) - rec["rssi"]["mean"]) / sd
            if z > 1.0:
                total += z - 1.0
                evidence.append(("rssi_zscore", round(z, 2)))

    chan = observation.get("channel")
    if chan is not None:
        freq = _hist_freq(rec["channels"], chan)
        if freq < 0.05:
            total += 1.5
            evidence.append(("channel_novelty", round(freq, 3)))

    ie = observation.get("ie_hash")
    if ie is not None:
        if str(ie) not in rec["ie_hashes"]:
            total += 2.0
            evidence.append(("ie_hash_new", str(ie)[:12]))
        else:
            freq = _hist_freq(rec["ie_hashes"], ie)
            if freq < 0.05:
                total += 0.75
                evidence.append(("ie_hash_rare", round(freq, 3)))

    bi = observation.get("beacon_interval")
    if bi is not None and rec["beacon_interval"]["n"] >= 2:
        sd = _stddev(rec["beacon_interval"])
        if sd > 0:
            z = abs(float(bi) - rec["beacon_interval"]["mean"]) / sd
            if z > 1.0:
                total += (z - 1.0) * 0.5
                evidence.append(("beacon_interval_zscore", round(z, 2)))

    return round(total, 3), False, evidence


def score_scan(scan):
    """Return {bssid: (score, is_learning, evidence)} for one scan."""
    baseline = _load()
    out = {}
    for obs in scan:
        bssid = (obs.get("bssid") or "").lower().strip()
        if not bssid:
            continue
        out[bssid] = score(obs, baseline)
    return out


def emit_anomalies(scan, threshold=DEFAULT_THRESHOLD):
    """Score `scan` and emit gp_events for each AP above `threshold`.

    Returns the list of (bssid, score, evidence) tuples emitted.
    """
    emitted = []
    results = score_scan(scan)
    obs_by_bssid = {(o.get("bssid") or "").lower().strip(): o for o in scan}
    for bssid, (s, learning, evidence) in results.items():
        if learning or s < threshold:
            continue
        obs = obs_by_bssid.get(bssid, {})
        ssid = obs.get("ssid") or ""
        gp_events.emit(
            SOURCE, CATEGORY_ANOMALY,
            gp_events.SEVERITY_SUSPICIOUS,
            f"Sonar: beacon drift on {ssid or bssid} (score={s})",
            details={
                "bssid": bssid,
                "ssid": ssid,
                "score": s,
                "evidence": evidence,
                "threshold": threshold,
            },
        )
        emitted.append((bssid, s, evidence))
    return emitted


def prune_stale(max_age_days=30):
    """Drop APs unseen for max_age_days. Returns count removed."""
    cutoff = time.time() - (max_age_days * 86400)
    data = _load()
    aps = data.get("aps", {})
    stale = [b for b, rec in aps.items() if rec.get("last_seen", 0) < cutoff]
    for b in stale:
        aps.pop(b, None)
    if stale:
        _save(data)
    return len(stale)


def status_summary():
    data = _load()
    aps = data.get("aps", {})
    if not aps:
        return {"ap_count": 0, "learning_count": 0, "trained_count": 0,
                "oldest_seen": None, "newest_seen": None,
                "file": BASELINE_FILE, "file_bytes": 0}
    learning = sum(1 for r in aps.values() if r["obs_count"] < MIN_OBSERVATIONS)
    timestamps = [r.get("last_seen", 0) for r in aps.values()]
    try:
        size = os.path.getsize(BASELINE_FILE)
    except OSError:
        size = 0
    return {
        "ap_count": len(aps),
        "learning_count": learning,
        "trained_count": len(aps) - learning,
        "oldest_seen": min(timestamps) if timestamps else None,
        "newest_seen": max(timestamps) if timestamps else None,
        "file": BASELINE_FILE,
        "file_bytes": size,
    }


def reset_baseline():
    """Wipe the baseline. For tests and operator reset."""
    try:
        os.remove(BASELINE_FILE)
    except FileNotFoundError:
        pass


# ── CLI ──────────────────────────────────────────────────────────────

def _cli():
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        sys.stdout.write(__doc__ or "")
        return 0
    if "--status" in args:
        sys.stdout.write(json.dumps(status_summary(), indent=2) + "\n")
        return 0
    if "--reset" in args:
        reset_baseline()
        sys.stdout.write("baseline reset\n")
        return 0
    sys.stderr.write("unknown args; try --help\n")
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
