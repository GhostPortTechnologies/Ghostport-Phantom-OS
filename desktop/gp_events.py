"""gp_events — shared event bus for cross-app correlation.

Every detection-class app (Sonar, Stonefish, Crow's Nest) emits structured
events here. The correlation engine subscribes to recent events and looks
for patterns across sources within a time window — e.g. SPOOFED IE on our
AP + ARP gateway change within 60s indicates a coordinated MITM attack
that none of the individual apps can call out alone.

Design choices:
- SQLite for ACID and concurrent reads/writes from multiple processes
- User-writable path (no sudo) so any desktop app can emit
- Short-lived connections (open, write, close) — no threading hazards
- JSON-blob details column for category-specific data without schema rigidity
- Append-only with periodic prune (events older than EVENT_RETAIN_DAYS dropped)
"""

import os
import json
import time
import sqlite3
import logging
from contextlib import contextmanager

# ── Storage location ──────────────────────────────────────────────────
EVENTS_DIR = os.path.expanduser("~/.local/share/phantom")
EVENTS_DB = os.path.join(EVENTS_DIR, "events.db")

# Events older than this are dropped on next prune (keeps DB small).
EVENT_RETAIN_DAYS = 7

# ── Severity ladder ───────────────────────────────────────────────────
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_SUSPICIOUS = "suspicious"
SEVERITY_DANGEROUS = "dangerous"
SEVERITY_CRITICAL = "critical"  # reserved for correlation-engine outputs

VALID_SEVERITIES = (
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SEVERITY_SUSPICIOUS,
    SEVERITY_DANGEROUS,
    SEVERITY_CRITICAL,
)


# ── DB lifecycle ──────────────────────────────────────────────────────

def _ensure_db():
    """Create the events DB and schema if missing. Idempotent."""
    os.makedirs(EVENTS_DIR, exist_ok=True)
    with sqlite3.connect(EVENTS_DB, timeout=10) as conn:
        # WAL journaling so prune() DELETE doesn't block concurrent emit().
        # Persists in DB header — only takes effect on first write to a fresh
        # DB or first call against a legacy rollback-journal DB. Idempotent.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                summary TEXT NOT NULL,
                details TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_cat ON events(category)")


@contextmanager
def _db():
    """Short-lived connection — open, yield, close. No retained connections."""
    _ensure_db()
    conn = sqlite3.connect(EVENTS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Public API ────────────────────────────────────────────────────────

def emit(source, category, severity, summary, details=None):
    """Insert an event into the shared bus.

    source   — short app name: "sonar" | "stonefish" | "crowsnest" | ...
    category — short event type: "karma_rig" | "spoofed_ie" | "arp_change" | ...
    severity — one of VALID_SEVERITIES
    summary  — one-line plain-English description
    details  — optional dict; serialized as JSON

    Failures are swallowed (logged at debug only) — emit must never crash
    the calling app even if the DB is locked or the disk is full.
    """
    if severity not in VALID_SEVERITIES:
        severity = SEVERITY_INFO
    try:
        details_json = json.dumps(details) if details else None
        with _db() as conn:
            conn.execute(
                "INSERT INTO events (timestamp, source, category, severity, summary, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), source, category, severity, summary, details_json),
            )
    except Exception as e:
        logging.debug("gp_events.emit failed: %s", e)


def recent(since_seconds=300, source=None, category=None, min_severity=None):
    """Return list of recent events as dicts, newest first.

    since_seconds — only events from the last N seconds (default 5min)
    source        — filter by exact source name
    category      — filter by exact category
    min_severity  — only events at this severity or higher
    """
    cutoff = time.time() - since_seconds
    sql = "SELECT * FROM events WHERE timestamp >= ?"
    params = [cutoff]
    if source:
        sql += " AND source = ?"
        params.append(source)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY timestamp DESC"

    rows = []
    try:
        with _db() as conn:
            for r in conn.execute(sql, params):
                rows.append({
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "source": r["source"],
                    "category": r["category"],
                    "severity": r["severity"],
                    "summary": r["summary"],
                    "details": json.loads(r["details"]) if r["details"] else {},
                })
    except Exception as e:
        logging.debug("gp_events.recent failed: %s", e)
        return []

    if min_severity:
        rank = {s: i for i, s in enumerate(VALID_SEVERITIES)}
        threshold = rank.get(min_severity, 0)
        rows = [r for r in rows if rank.get(r["severity"], 0) >= threshold]
    return rows


def prune(retain_days=EVENT_RETAIN_DAYS):
    """Drop events older than retain_days. Safe to call periodically."""
    cutoff = time.time() - (retain_days * 86400)
    try:
        with _db() as conn:
            conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
    except Exception as e:
        logging.debug("gp_events.prune failed: %s", e)


# ── Correlation engine ────────────────────────────────────────────────
#
# Patterns are time-window joins across event categories. Each pattern
# returns at most one finding per call (dedup is the consumer's problem).

CORRELATION_PATTERNS = [
    {
        "id": "coordinated_mitm",
        "name": "Coordinated MITM attack",
        "description": (
            "Sonar detected SPOOFED IE on our AP at the same time Stonefish "
            "saw a gateway MAC change. Classic clone-and-redirect signature."
        ),
        "severity": SEVERITY_CRITICAL,
        "window_seconds": 90,
        "requires": [
            {"source": "sonar", "category": "spoofed_ie"},
            {"source": "stonefish", "category": "arp_gateway_change"},
        ],
    },
    {
        "id": "karma_plus_drops",
        "name": "Active Karma rig + firewall pressure",
        "description": (
            "Hunt Karma flagged a rig at the same time as a burst of firewall "
            "drops. The attacker is both luring AND probing."
        ),
        "severity": SEVERITY_CRITICAL,
        "window_seconds": 120,
        "requires": [
            {"source": "sonar", "category": "karma_rig"},
            {"source": "crowsnest", "category": "firewall_drop_burst"},
        ],
    },
    {
        "id": "wardriving_pattern",
        "name": "Possible wardriving / serial evil-twin attempts",
        "description": (
            "Multiple distinct evil-twin or attack-toolkit signatures from "
            "different BSSIDs within a short window — consistent with a moving "
            "attacker or a multi-radio rig."
        ),
        "severity": SEVERITY_DANGEROUS,
        "window_seconds": 600,
        "requires_count": 3,
        "requires_any": [
            {"source": "sonar", "category": "evil_twin"},
            {"source": "sonar", "category": "attack_toolkit"},
        ],
    },
]


def detect_correlations():
    """Scan recent events for active correlation patterns.

    Returns list of {pattern_id, name, description, severity, evidence}
    dicts. The consumer is responsible for deduplication (so the same
    pattern doesn't notify repeatedly while still active).
    """
    findings = []
    now = time.time()

    for pat in CORRELATION_PATTERNS:
        window = pat.get("window_seconds", 60)
        evidence = []

        if "requires" in pat:
            # ALL of the requires entries must have at least one match in window
            all_matched = True
            for req in pat["requires"]:
                matches = recent(
                    since_seconds=window,
                    source=req.get("source"),
                    category=req.get("category"),
                )
                if not matches:
                    all_matched = False
                    break
                evidence.append(matches[0])  # most recent
            if not all_matched:
                continue
        elif "requires_any" in pat:
            # ANY of the entries — but cumulative count must hit requires_count
            need = pat.get("requires_count", 1)
            seen_bssids = set()
            for req in pat["requires_any"]:
                matches = recent(
                    since_seconds=window,
                    source=req.get("source"),
                    category=req.get("category"),
                )
                for m in matches:
                    bssid = (m.get("details") or {}).get("bssid", m["id"])
                    if bssid in seen_bssids:
                        continue
                    seen_bssids.add(bssid)
                    evidence.append(m)
            if len(evidence) < need:
                continue
        else:
            continue

        findings.append({
            "pattern_id": pat["id"],
            "name": pat["name"],
            "description": pat["description"],
            "severity": pat["severity"],
            "first_evidence_ts": min(e["timestamp"] for e in evidence) if evidence else now,
            "evidence_count": len(evidence),
            "evidence_summary": [f"{e['source']}: {e['summary']}" for e in evidence],
        })
    return findings
