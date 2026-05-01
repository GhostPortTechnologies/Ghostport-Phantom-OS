"""Sonar — WiGLE BSSID lookup helper.

Optional integration: queries api.wigle.net for a BSSID's observed history.
A BSSID claiming to be your office AP that WiGLE has logged 500 miles away is
suspicious. Catches things pure-IE / signature checks can't.

Auth file: /etc/phantom/wigle-auth.json (0600, gitignored). Format:
    {"api_name": "...", "api_token": "...", "daily_cap": 50}

Cache: /etc/phantom/wigle-cache.json — 24h TTL per BSSID. Daily counter rolls
at UTC midnight. Hard skip when over cap.

Network calls timeout aggressively (5s) per ai-dev-guide §2. All errors return
None so callers can render a graceful "lookup unavailable" line.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_FILE = "/etc/phantom/wigle-auth.json"
CACHE_FILE = "/etc/phantom/wigle-cache.json"
CACHE_TTL_SEC = 24 * 3600
DEFAULT_DAILY_CAP = 50
HTTP_TIMEOUT = 5
WIGLE_URL = "https://api.wigle.net/api/v2/network/search"


def _read_auth():
    """Return (api_name, api_token, daily_cap) or None if not configured."""
    try:
        with open(AUTH_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    name = data.get("api_name")
    token = data.get("api_token")
    if not name or not token:
        return None
    cap = data.get("daily_cap", DEFAULT_DAILY_CAP)
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = DEFAULT_DAILY_CAP
    return name, token, cap


def wigle_available():
    """True if auth file is present and parses with api_name + api_token."""
    return _read_auth() is not None


def _today_utc():
    """UTC date string (YYYY-MM-DD) for daily-counter bucketing."""
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"entries": {}, "counter": {"date": _today_utc(), "count": 0}}


def _save_cache(cache):
    """Atomic write — never leave a half-written cache file behind."""
    tmp = CACHE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _check_and_bump_counter(cache, cap):
    """Return True if we may make a fresh API call; bumps counter on True."""
    today = _today_utc()
    counter = cache.get("counter", {})
    if counter.get("date") != today:
        counter = {"date": today, "count": 0}
        cache["counter"] = counter
    if counter["count"] >= cap:
        return False
    counter["count"] += 1
    return True


def _normalize_bssid(bssid):
    return (bssid or "").strip().lower()


def _http_lookup(bssid, api_name, api_token):
    """One HTTP call to WiGLE. Returns parsed JSON or None on any error."""
    auth_raw = f"{api_name}:{api_token}".encode("utf-8")
    auth_header = "Basic " + base64.b64encode(auth_raw).decode("ascii")
    qs = urllib.parse.urlencode({"netid": bssid})
    req = urllib.request.Request(
        f"{WIGLE_URL}?{qs}",
        headers={
            "Authorization": auth_header,
            "Accept": "application/json",
            "User-Agent": "GhostPort-Sonar/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read()
        return json.loads(body)
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None


def _summarize(payload):
    """Reduce WiGLE response to a small dict the UI can render directly."""
    if not isinstance(payload, dict):
        return {"found": False}
    results = payload.get("results") or []
    if not results:
        return {"found": False}
    first = results[0]
    return {
        "found": True,
        "ssid": first.get("ssid", "") or "",
        "first_seen": first.get("firsttime", "") or "",
        "last_seen": first.get("lasttime", "") or "",
        "lat": first.get("trilat"),
        "lon": first.get("trilong"),
        "country": first.get("country", "") or "",
        "city": first.get("city", "") or "",
        "encryption": first.get("encryption", "") or "",
        "result_count": len(results),
    }


def wigle_lookup(bssid):
    """Look up a BSSID. Returns dict with summary, or None if unavailable.

    Result shape:
        {"status": "ok"|"cached"|"no_auth"|"rate_limited"|"error",
         "summary": {...} | None,  # see _summarize for shape
         "fetched_at": int unix-ts}
    """
    bssid = _normalize_bssid(bssid)
    if not bssid:
        return {"status": "error", "summary": None, "fetched_at": int(time.time())}

    auth = _read_auth()
    if not auth:
        return {"status": "no_auth", "summary": None, "fetched_at": int(time.time())}
    api_name, api_token, cap = auth

    cache = _load_cache()
    entries = cache.setdefault("entries", {})
    now = int(time.time())

    cached = entries.get(bssid)
    if cached and (now - cached.get("fetched_at", 0)) < CACHE_TTL_SEC:
        return {
            "status": "cached",
            "summary": cached.get("summary"),
            "fetched_at": cached.get("fetched_at", now),
        }

    if not _check_and_bump_counter(cache, cap):
        _save_cache(cache)
        return {"status": "rate_limited", "summary": None, "fetched_at": now}

    payload = _http_lookup(bssid, api_name, api_token)
    if payload is None:
        _save_cache(cache)
        return {"status": "error", "summary": None, "fetched_at": now}

    summary = _summarize(payload)
    entries[bssid] = {"summary": summary, "fetched_at": now}
    _save_cache(cache)
    return {"status": "ok", "summary": summary, "fetched_at": now}


def render_lines(result):
    """Format a wigle_lookup result as a list of (label, value, css_hint) tuples
    so callers can render without re-implementing the same status logic.
    """
    if result is None:
        return [("WiGLE", "lookup unavailable", "gp-dim")]
    status = result.get("status")
    if status == "no_auth":
        return [("WiGLE", "not configured (see /etc/phantom/wigle-auth.json)", "gp-dim")]
    if status == "rate_limited":
        return [("WiGLE", "daily lookup cap reached", "gp-warning")]
    if status == "error":
        return [("WiGLE", "network error or invalid response", "gp-warning")]
    summary = result.get("summary") or {}
    if not summary.get("found"):
        return [("WiGLE", "no history (BSSID not in WiGLE)", "gp-dim")]
    loc_bits = [b for b in (summary.get("city"), summary.get("country")) if b]
    location = ", ".join(loc_bits) if loc_bits else "(no location)"
    lines = [
        ("WiGLE SSID", summary.get("ssid") or "(empty)", "gp-text"),
        ("Seen at", location, "gp-text"),
        ("First seen", summary.get("first_seen") or "(unknown)", "gp-text"),
        ("Last seen", summary.get("last_seen") or "(unknown)", "gp-text"),
        ("Records", str(summary.get("result_count", 0)), "gp-text"),
    ]
    return lines
