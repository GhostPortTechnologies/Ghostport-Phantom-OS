#!/usr/bin/env python3
"""gp-sonar — SONAR: Rogue AP / Evil Twin WiFi Scanner for Phantom OS"""
import sys, os, re, json, time, copy, subprocess, hashlib, secrets
from collections import OrderedDict
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp
import gp_events
import gp_sonar_wigle
import gp_sonar_encounters
import gp_sonar_history
import gp_sonar_anomaly  # T-0040: behavioral anomaly scoring
import gp_sonar_report  # T-0037: full session report builder

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

# ── Constants ─────────────────────────────────────────────────────────

TRUSTED_APS_FILE = os.path.expanduser("~/.config/phantom/trusted-aps.json")
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"

# Attack-toolkit signature database. User override > bundled default.
SIGNATURES_FILE_USER = "/etc/phantom/sonar-signatures.json"
SIGNATURES_FILE_BUNDLED = "/opt/phantom/desktop/sonar-signatures.json"

# T-0036: background-detect state. gp-sonar-detect helper writes; GUI reads.
# Same path it uses to dedup; the state JSON has last_run + findings list.
BG_STATE_FILE = os.path.expanduser("~/.local/share/phantom/sonar-detect-state.json")
BG_POLL_INTERVAL_SEC = 30

# T-0046: rogue-block CLI from T-0031 Phase A. The button only appears when
# the wrapper is installed (operator action: sudo install gp-rogue-block to
# /usr/local/bin/). State file is what the wrapper writes to track armed
# blocks; the GUI reads it to decide Arm vs Release rendering and to drive
# the armed-state banner.
ROGUE_BLOCK_CMD = "/usr/local/bin/gp-rogue-block"
ROGUE_BLOCKS_FILE = os.path.expanduser("~/.local/share/phantom/rogue-blocks.json")
ROGUE_POLL_INTERVAL_SEC = 30

# T-0165 — Probe Captures tab. Polls the gp_events bus for category=probe_request.
PROBE_POLL_INTERVAL_SEC = 5
PROBE_LIST_LIMIT = 250  # bound the list so a busy AP doesn't melt GTK
OUI_EXTRAS_FILE = "/etc/phantom/oui-extras.json"

# Bounded session memory (T-0017). Long-running Sonar GUI on busy/hostile
# RF environments can otherwise grow these without limit. Oldest entries
# evict first via OrderedDict.popitem(last=False).
MAX_KARMA_RIGS = 5000
MAX_EMITTED_EVENTS = 5000

# T-0027: dual-band scan. wlan0 in AP mode (5GHz channel 149) historically
# only saw 5GHz beacons — a 2.4GHz Pineapple within range was invisible.
# We pass an explicit `freq <list>` to iw so the driver scans both bands.
# Some chipsets refuse to leave the AP channel and will return only the
# operating band's results; the caller falls back gracefully (run_scan).
SCAN_FREQS_24 = [2407 + ch * 5 for ch in range(1, 14)]   # ch 1..13
SCAN_FREQS_5 = [
    5180, 5200, 5220, 5240,                              # UNII-1 (36-48)
    5260, 5280, 5300, 5320,                              # UNII-2A DFS
    5500, 5520, 5540, 5560, 5580, 5600, 5620, 5640,      # UNII-2C DFS
    5660, 5680, 5700, 5720,
    5745, 5765, 5785, 5805, 5825,                        # UNII-3 (149-165)
]
SCAN_FREQS_ALL = SCAN_FREQS_24 + SCAN_FREQS_5

# Karma hunt: SSIDs commonly auto-saved on phones/laptops that wordlist-Karma
# rigs target. We probe for these AND a set of random decoys; any rig that
# responds to a random decoy OR claims one of these wordlist names while
# beaconing a different SSID is a Karma rig.
KARMA_WORDLIST = (
    "xfinitywifi",
    "Starbucks WiFi",
    "linksys",
    "NETGEAR",
    "ATT-WIFI",
    "Free Public WiFi",
    "AmazonConnect",
    "Boingo Hotspot",
    "T-Mobile Wi-Fi",
    "GoogleGuest",
)
KARMA_RANDOM_COUNT = 5
KARMA_RANDOM_PREFIX = "gp-decoy-"


def _decode_iw_ssid(s):
    """Inverse of iw's print_ssid_escaped: decodes \\xNN and \\\\ back to bytes,
    then UTF-8 decodes. Without this, SSIDs with non-printable / multi-byte
    chars retain literal \\xNN sequences and fail equality checks against our
    own SSID, breaking evil-twin / spoofed-IE detection.
    """
    if "\\" not in s:
        return s
    buf = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n:
            nxt = s[i+1]
            if nxt == '\\':
                buf.append(0x5C)
                i += 2
                continue
            if nxt == 'x' and i + 3 < n:
                try:
                    buf.append(int(s[i+2:i+4], 16))
                    i += 4
                    continue
                except ValueError:
                    pass
        buf.extend(c.encode('utf-8'))
        i += 1
    return buf.decode('utf-8', errors='replace')


def _resolve_encryption(signals):
    """Resolve encryption from a set of iw-output signals using fixed
    precedence (WPA3 > WPA2 > WPA > WEP > Open). Order-independent so
    driver/version variance in iw output ordering doesn't flip results.
    """
    has_rsn = "rsn" in signals
    has_wpa = "wpa" in signals
    has_sae = "sae" in signals
    has_mfp = "group_mgmt" in signals
    if (has_sae or has_mfp) and (has_rsn or has_wpa):
        return "WPA3"
    if has_rsn:
        return "WPA2"
    if has_wpa:
        return "WPA"
    if "wep" in signals or "privacy" in signals:
        return "WEP"
    return "Open"


def get_our_ap():
    """Read our own AP SSID and BSSID. Tries hostapd.conf first; falls back to
    `iw dev wlan0 info` if hostapd.conf is unreadable (a future hardening pass
    tightening to 0640 root:hostapd would otherwise silently break evil-twin
    detection).
    """
    ssid = ""
    bssid = ""
    try:
        with open(HOSTAPD_CONF) as f:
            for line in f:
                if line.startswith("ssid="):
                    ssid = line.strip().split("=", 1)[1]
                    break
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["ip", "link", "show", "wlan0"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "link/ether" in line:
                bssid = line.split()[1].lower()
                break
    except Exception:
        pass
    if not ssid:
        try:
            result = subprocess.run(
                ["sudo", "-n", "iw", "dev", "wlan0", "info"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("ssid "):
                    ssid = _decode_iw_ssid(stripped[5:].strip())
                    break
        except Exception:
            pass
    return ssid, bssid


def load_trusted_aps():
    """Load trusted AP list from JSON file.

    New schema:  [{"bssid": "...", "ssid": "...", "fingerprint": "..."}]
    Legacy:      ["aa:bb:cc:dd:ee:ff", ...]    (just BSSIDs)

    Legacy entries are silently upgraded to the new schema with empty
    ssid/fingerprint fields; they re-snapshot the next time the AP is seen.
    """
    try:
        with open(TRUSTED_APS_FILE) as f:
            data = json.load(f)
            if not isinstance(data, list):
                return []
            upgraded = []
            for item in data:
                if isinstance(item, str):
                    upgraded.append({"bssid": item.lower(), "ssid": "", "fingerprint": ""})
                elif isinstance(item, dict) and "bssid" in item:
                    upgraded.append({
                        "bssid": item["bssid"].lower(),
                        "ssid": item.get("ssid", ""),
                        "fingerprint": item.get("fingerprint", ""),
                    })
            return upgraded
    except Exception:
        pass
    return []


def save_trusted_aps(trusted):
    """Save trusted AP list to JSON file atomically. Raises OSError on failure
    so callers can surface the error instead of pretending the write succeeded.
    """
    os.makedirs(os.path.dirname(TRUSTED_APS_FILE), exist_ok=True)
    tmp = TRUSTED_APS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(trusted, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, TRUSTED_APS_FILE)


# ── Attack-toolkit signature DB ───────────────────────────────────────
#
# Each signature flags an AP whose beacon matches a known offensive-tool
# default. The DB lives in JSON so it can be updated via OTA without a
# code change. User override at SIGNATURES_FILE_USER takes priority over
# the bundled default.
#
# Match types (any subset, ANDed):
#   ssid_regex     — Python regex against ap["ssid"]
#   bssid_prefix   — case-insensitive prefix of ap["bssid"]
#   capability_hex — exact match on the (0x____) capability value (string)
#   ie_pattern     — substring search across the AP's raw IE lines
#
# Severity must be one of: warning, suspicious, dangerous

def load_signatures():
    """Load the attack-toolkit signatures, preferring the user override.

    Returns (signatures, error_msg). error_msg is non-None when the USER
    override exists but fails to load — the caller should surface it so the
    user knows their custom DB isn't active and we silently fell back to the
    bundled defaults.
    """
    user_error = None
    for path in (SIGNATURES_FILE_USER, SIGNATURES_FILE_BUNDLED):
        try:
            with open(path) as f:
                data = json.load(f)
                sigs = data.get("signatures", []) if isinstance(data, dict) else []
                # Pre-compile regexes once at load time so per-scan match is cheap.
                for s in sigs:
                    m = s.get("match") or {}
                    if "ssid_regex" in m:
                        try:
                            s["_ssid_re"] = re.compile(m["ssid_regex"])
                        except re.error:
                            s["_ssid_re"] = None
                return [s for s in sigs if s.get("id") and s.get("name")], user_error
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError) as e:
            if path == SIGNATURES_FILE_USER:
                user_error = (
                    f"Sonar: custom signature DB at {path} failed to load ({e}); "
                    f"falling back to bundled defaults — your overrides are NOT active."
                )
                sys.stderr.write(f"[gp-sonar] {user_error}\n")
            continue
    return [], user_error


def match_signatures(ap, signatures, ie_lines=None):
    """Return the first matching signature for this AP, or None.

    `ie_lines` (optional) is the per-BSS line buffer; needed for ie_pattern
    matches. If absent, ie_pattern matches are skipped (graceful degradation).
    """
    for sig in signatures:
        m = sig.get("match") or {}

        if "ssid_regex" in m:
            rx = sig.get("_ssid_re")
            if rx is None or not ap.get("ssid"):
                continue
            if not rx.search(ap["ssid"]):
                continue

        if "bssid_prefix" in m:
            prefix = m["bssid_prefix"].lower()
            if not ap.get("bssid", "").lower().startswith(prefix):
                continue

        if "capability_hex" in m:
            # Capability hex match (e.g. "0x0411") — looks for exact value
            # in any IE line; cheap because parse_iw_scan keeps the raw line.
            wanted = m["capability_hex"].lower()
            found = False
            for line in (ie_lines or []):
                if wanted in line.lower():
                    found = True
                    break
            if not found:
                continue

        if "ie_pattern" in m:
            wanted = m["ie_pattern"]
            if not ie_lines:
                continue
            if not any(wanted in line for line in ie_lines):
                continue

        # All present matchers passed
        return sig
    return None


# ── IE Fingerprint ────────────────────────────────────────────────────
#
# An IE fingerprint is a SHA256 hash of the stable Information Elements
# in an AP's beacon. Two scans of the same physical AP produce identical
# fingerprints; an evil twin spoofing the same SSID/BSSID would have to
# replicate every one of these IE blocks bit-for-bit to evade detection.
#
# Stable IE markers — identical across scans of the same AP:
IE_FINGERPRINT_MARKERS = (
    "capability:",
    "Supported rates:",
    "Extended supported rates:",
    "Country:",
    "Power constraint:",
    "RSN:",
    "WPA:",
    "RM enabled capabilities:",
    "Extended capabilities:",
    "HT capabilities:",
    "HT operation:",
    "VHT capabilities:",
    "VHT operation:",
    "HE capabilities:",
    "HE operation:",
    "EHT capabilities:",
    "EHT operation:",
    "BSS Load:",
    "QBSS:",
)

# Volatile lines explicitly excluded from fingerprint:
IE_FINGERPRINT_EXCLUDE = (
    "last seen:",
    "TSF:",
    "signal:",
    "freq:",
    "beacon interval:",
    "TIM:",
    "BSS ",
    "SSID:",
    "DS Parameter set:",  # channel — can change after radar / DFS
)


def compute_ie_fingerprint(lines):
    """Hash the stable IE blocks of a BSS into a canonical fingerprint hex.

    Walks the per-BSS lines, accumulates any line whose stripped form starts
    with a known IE marker, plus indented sub-lines that follow. Volatile
    fields (signal, last seen, TSF, channel) are skipped. Whitespace is
    normalized so trivial format changes don't perturb the hash.
    """
    canon = []
    accumulating = False
    for raw in lines:
        s = raw.strip()
        if not s:
            accumulating = False
            continue
        if any(s.startswith(p) for p in IE_FINGERPRINT_EXCLUDE):
            accumulating = False
            continue
        if any(s.startswith(p) for p in IE_FINGERPRINT_MARKERS):
            accumulating = True
            canon.append(re.sub(r"\s+", " ", s))
            continue
        # Indented continuation of a prior marker block — keep
        if accumulating and (raw.startswith("\t\t") or raw.startswith("    ")):
            canon.append(re.sub(r"\s+", " ", s))
            continue
        # Top-level non-marker line — stop accumulating
        accumulating = False

    blob = "\n".join(canon)
    return hashlib.sha256(blob.encode()).hexdigest()


def parse_iw_scan(output, our_ssid, our_bssid, trusted_aps, signatures=None):
    """Parse 'sudo iw dev wlan0 scan' output into list of AP dicts.

    `trusted_aps` is now a list of dicts (with backward-compat for legacy
    BSSID-string lists) — see load_trusted_aps for schema.
    `signatures` is the optional attack-toolkit signature DB; if provided,
    each AP gets a `signature_match` field populated when a sig fires.
    """
    if signatures is None:
        signatures = []
    aps = []
    current = None

    for line in output.splitlines():
        line_stripped = line.strip()

        # New BSS block
        if line.startswith("BSS "):
            if current:
                aps.append(current)
            bssid_match = re.match(r'BSS\s+([0-9a-f:]{17})', line)
            bssid = bssid_match.group(1).lower() if bssid_match else ""
            current = {
                "bssid": bssid,
                "ssid": "",
                "freq": 0,
                "channel": 0,
                "signal": -100,
                "encryption": "Open",
                "wpa_version": "",
                "is_ours": False,
                "threat": "safe",
                "threat_label": "SAFE",
                "ie_lines": [],
                "ie_fingerprint": "",
                "signature_match": None,
                "_enc_signals": set(),
            }
            continue

        if current is None:
            continue

        # Collect every non-BSS line under the current BSS for fingerprinting.
        current["ie_lines"].append(line)

        if line_stripped.startswith("SSID:"):
            current["ssid"] = _decode_iw_ssid(line_stripped[5:].strip())
        elif line_stripped.startswith("freq:"):
            try:
                current["freq"] = int(line_stripped.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
        elif line_stripped.startswith("signal:"):
            sig_match = re.search(r'(-?\d+\.?\d*)', line_stripped)
            if sig_match:
                current["signal"] = float(sig_match.group(1))
        elif line_stripped.startswith("DS Parameter set: channel"):
            ch_match = re.search(r'channel\s+(\d+)', line_stripped)
            if ch_match:
                current["channel"] = int(ch_match.group(1))
        elif "RSN:" in line_stripped:
            current["_enc_signals"].add("rsn")
            current["wpa_version"] = "RSN"
        elif "WPA:" in line_stripped:
            current["_enc_signals"].add("wpa")
        elif "SAE" in line_stripped:
            current["_enc_signals"].add("sae")
        elif "Group management" in line_stripped:
            current["_enc_signals"].add("group_mgmt")
        elif "WEP" in line_stripped and "Privacy" in line_stripped:
            current["_enc_signals"].add("wep")
        elif line_stripped.startswith("capability:"):
            if "Privacy" in line_stripped:
                current["_enc_signals"].add("privacy")

    if current:
        aps.append(current)

    # Derive channel from freq if not set, resolve encryption with fixed
    # precedence so iw output ordering can't flip WPA3 → WPA2/WPA.
    for ap in aps:
        if ap["channel"] == 0 and ap["freq"] > 0:
            ap["channel"] = freq_to_channel(ap["freq"])
        ap["encryption"] = _resolve_encryption(ap.pop("_enc_signals", set()))

    # Compute IE fingerprint AND attack-toolkit signature match per AP from
    # the collected lines, then strip the lines so they don't bloat downstream
    # JSON / UI dicts. Signature match must happen here while ie_lines exist.
    for ap in aps:
        ap["ie_fingerprint"] = compute_ie_fingerprint(ap["ie_lines"])
        match = match_signatures(ap, signatures, ie_lines=ap["ie_lines"])
        if match is not None:
            ap["signature_match"] = {
                "id": match.get("id", ""),
                "name": match.get("name", "Unknown"),
                "severity": match.get("severity", "suspicious"),
                "description": match.get("description", ""),
            }
        del ap["ie_lines"]

    # Classify threats (now fingerprint-aware)
    for ap in aps:
        ap["is_ours"] = (ap["bssid"] == our_bssid)
        ap["threat"], ap["threat_label"] = classify_ap(
            ap, our_ssid, our_bssid, trusted_aps
        )

    # Sort: our AP first, then by threat (dangerous > suspicious > warning > safe), then signal
    threat_order = {"dangerous": 0, "suspicious": 1, "warning": 2, "safe": 3}
    aps.sort(key=lambda a: (
        0 if a["is_ours"] else 1,
        threat_order.get(a["threat"], 4),
        a["signal"],  # lower (more negative) = weaker, so stronger first
    ))

    return aps


def freq_to_channel(freq):
    """Convert WiFi frequency to channel number."""
    if 2412 <= freq <= 2484:
        if freq == 2484:
            return 14
        return (freq - 2412) // 5 + 1
    elif 5170 <= freq <= 5825:
        return (freq - 5000) // 5
    elif 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return 0


def _trusted_lookup(bssid, trusted_aps):
    """Return the trusted entry matching this BSSID, or None."""
    bssid = bssid.lower()
    for entry in trusted_aps:
        if entry.get("bssid", "").lower() == bssid:
            return entry
    return None


def classify_ap(ap, our_ssid, our_bssid, trusted_aps):
    """Return (threat_level, threat_label) for an AP."""
    trusted = _trusted_lookup(ap["bssid"], trusted_aps)

    # Our own AP — always check IE fingerprint first. A trusted-but-IE-mismatched
    # entry on our own BSSID is the classic evil-twin-with-cloned-MAC signature.
    # Note: we deliberately skip signature matching for our own AP — even if the
    # user nicknames their network "Pineapple" we shouldn't flag it.
    if ap["bssid"] == our_bssid:
        if trusted and trusted.get("fingerprint") and ap.get("ie_fingerprint"):
            if trusted["fingerprint"] != ap["ie_fingerprint"]:
                return "dangerous", "SPOOFED IE"
        return "safe", "OUR AP"

    # Attack-toolkit signature — fires before evil-twin/trusted/heuristics
    # because it's the most specific signal we have.
    sig = ap.get("signature_match")
    if sig:
        severity = sig.get("severity", "suspicious")
        if severity not in ("warning", "suspicious", "dangerous"):
            severity = "suspicious"
        return severity, f"ATTACK TOOLKIT: {sig['name'].upper()}"

    # Evil twin by SSID — same name as ours, different BSSID
    if ap["ssid"] and ap["ssid"] == our_ssid and ap["bssid"] != our_bssid:
        return "dangerous", "EVIL TWIN"

    # Trusted AP — verify IE fingerprint if we have one stored
    if trusted is not None:
        stored_fp = trusted.get("fingerprint", "")
        current_fp = ap.get("ie_fingerprint", "")
        if stored_fp and current_fp and stored_fp != current_fp:
            return "dangerous", "SPOOFED IE"
        return "safe", "TRUSTED"

    # Open network (no encryption)
    if ap["encryption"] == "Open":
        return "warning", "OPEN"

    # WEP (broken encryption)
    if ap["encryption"] == "WEP":
        return "warning", "WEAK (WEP)"

    # Very strong signal from unknown AP (possible rogue).
    # T-0021 item 1: tightened from -40 → -25 dBm. -40 fired on every cafe
    # AP at typical seating distance and trained users to ignore the alert.
    # -25 dBm corresponds to ~1m line-of-sight — atypical for a legitimate
    # neighbor AP, plausible for a hostile rig sitting at the next table.
    # Open + WEP networks are already flagged above (warning tier) so this
    # branch only fires for encrypted-but-suspiciously-close unknown APs.
    if ap["signal"] >= -25:
        return "suspicious", "STRONG SIGNAL"

    return "safe", "SAFE"


# ── Sonar App ─────────────────────────────────────────────────────────

class SonarApp(GhostPortApp):
    # Per-region contextual help. Dialog logic lives in GhostPortApp.show_help_dialog.
    HELP_SECTIONS = [
        ("What is Sonar?",
         "Sonar scans the airwaves for WiFi access points near your device — your own, "
         "your neighbors', and anything suspicious in between.\n\n"
         "It's a rogue-AP detector: its main job is spotting \"evil twins\" (an "
         "attacker setting up a clone of your own network to lure devices into "
         "connecting to them) and unknown APs appearing at your location. Think of "
         "it as a radar dish pinging the wireless neighborhood."),

        ("SCAN + HUNT KARMA buttons (top right)",
         "TWO buttons because two different scan modes:\n\n"
         "SCAN — passive. Sonar listens for beacons, it doesn't probe. "
         "Neighboring networks see nothing; you're just reading broadcasts. "
         "Safe, silent, and the right default for routine awareness scans. "
         "Takes ~5-15 seconds.\n\n"
         "HUNT KARMA — active probe. Broadcasts directed probe requests for "
         "5 random decoy SSIDs and 10 popular wordlist SSIDs (xfinitywifi, "
         "Starbucks WiFi, etc.). Any AP that responds to a random decoy, OR "
         "claims a wordlist SSID while beaconing a different real one, is a "
         "Karma rig. Takes ~10 seconds (passive baseline + active probe). "
         "See the Karma section below for details.\n\n"
         "Neither runs on a timer — explicit clicks only. Hunt Karma when "
         "you've arrived somewhere new, or after seeing suspicious activity."),

        ("Info bar — Our AP",
         "\"Our AP\" shows your own GhostPort access point's SSID and BSSID "
         "(WiFi name + hardware address). Everything else in the list gets "
         "compared against this.\n\n"
         "An evil twin would broadcast a matching SSID but have a DIFFERENT "
         "BSSID. Sonar flags that automatically.\n\n"
         "\"Last scan\" — when the current results were collected. Results don't "
         "auto-refresh; re-scan if you want fresh data."),

        ("AP list (left panel)",
         "Every access point Sonar heard during the scan, sorted by signal strength. "
         "Each row shows: SSID, BSSID, channel, signal strength, encryption type.\n\n"
         "Color coding:\n"
         "• Accent (bright) — your own AP, or an AP you've marked Trusted.\n"
         "• Dim — known nearby network, not classified.\n"
         "• Red/warning — flagged as suspicious (open encryption, BSSID mismatch, "
         "duplicate SSID).\n\n"
         "Click a row to see its details in the right panel."),

        ("Detail panel (right)",
         "When you select an AP, this panel shows everything known about it: full "
         "BSSID, signal history (if scanned repeatedly), first/last seen time, "
         "encryption breakdown, vendor guess from the MAC prefix.\n\n"
         "This is where you decide whether something is benign or worth investigating. "
         "A high-signal unknown AP in a spot where you know nobody's broadcasting? "
         "Worth a second look."),

        ("Trust / Untrust / Ignore / Snapshot / Export buttons",
         "Trust Selected — mark a neighbor's AP as known-good (also captures its "
         "current IE fingerprint as the baseline). Future scans won't flag it "
         "unless its IE blocks drift.\n\n"
         "Untrust Selected — remove the trusted flag and stored fingerprint. "
         "Next scan re-evaluates against all suspicion rules.\n\n"
         "Ignore (this session) — silence the threat label on a known-noisy AP "
         "for the rest of this Sonar session WITHOUT recording its IE "
         "fingerprint. The card renders dimmed with [IGNORED]. Use this for "
         "the cafe AP you're sitting next to that keeps firing STRONG SIGNAL "
         "— Trust would baseline its beacon as authoritative, which is "
         "overkill and exposes you to a cafe-twin spoof later. Ignore "
         "clears on app restart by design.\n\n"
         "Snapshot My AP — capture YOUR OWN AP's IE fingerprint as the trust "
         "baseline. See the dedicated Snapshot section below for when and how.\n\n"
         "Export Results — write the current scan to a timestamped file. Useful "
         "when you want a record before leaving a location, or to share with "
         "someone technical."),

        ("Snapshot My AP — the IE fingerprint baseline",
         "Sonar can fingerprint your own AP's beacon and alert you if it ever "
         "drifts. The fingerprint is a SHA256 of stable Information Elements: "
         "capability flags, RSN/WPA blocks, country/regulatory grid, supported "
         "rates, and HT/VHT/HE/EHT capability blobs.\n\n"
         "Click Snapshot My AP exactly once, in a trusted environment (typically "
         "at home with no active spoofing), to capture the legitimate beacon. "
         "From that moment on, any drift in the IE blocks raises a SPOOFED IE "
         "alert — the highest threat tier.\n\n"
         "WHY MANUAL: snapshotting auto-on-first-scan would be a footgun — if an "
         "attacker is already broadcasting your SSID/BSSID at first launch, they'd "
         "silently become the trust anchor. Click is explicit on purpose.\n\n"
         "WHEN TO RE-SNAPSHOT: only when your AP legitimately changes (firmware "
         "update, country/region change, new hardware). Do NOT re-snapshot to "
         "dismiss an alert you don't trust — that defeats the whole feature."),

        ("Hunt Karma — active probe-response detection (KARMA RIG alert)",
         "A Karma rig is a WiFi attack tool that responds to probe requests "
         "for ANY SSID. When your phone has 'starbucks' saved and broadcasts "
         "a probe asking 'is starbucks here?', a Karma rig answers 'yes, I'm "
         "starbucks' regardless of its real identity. Your phone auto-connects, "
         "the attacker proxies your traffic.\n\n"
         "WHAT HUNT KARMA DOES:\n"
         "1. Passive baseline scan — records each BSSID's actual beacon SSID.\n"
         "2. Active directed probe scan — broadcasts requests for:\n"
         "   - 5 random decoys (gp-decoy-<random hex>) — no real network would\n"
         "     ever advertise these. Any responder is busted.\n"
         "   - 10 popular wordlist SSIDs (xfinitywifi, Starbucks WiFi, linksys,\n"
         "     NETGEAR, ATT-WIFI, Free Public WiFi, AmazonConnect, Boingo Hotspot,\n"
         "     T-Mobile Wi-Fi, GoogleGuest) — common Karma targets.\n"
         "3. Compares the two scans:\n"
         "   - BSSID claims a decoy SSID -> Karma rig (zero false-positive).\n"
         "   - BSSID beacons 'X' but probe-responds with 'Starbucks WiFi' ->\n"
         "     Karma rig (identity mismatch, also unambiguous).\n\n"
         "PERSISTS FOR THE SESSION: Karma findings stay flagged across "
         "subsequent passive scans until Sonar is closed. Karma is a "
         "physical-location threat — a rig that moves away simply stops "
         "responding; closing the app clears the slate.\n\n"
         "WHAT IT MISSES (be honest):\n"
         "- Target-list-restricted rigs that only reply to specific MAC\n"
         "  addresses on their hit list (won't respond to our Pi's MAC).\n"
         "- Anti-recon Pineapples that detect our probe pattern as a scan\n"
         "  and stay silent.\n"
         "Hunt Karma is a strong signal but not a complete one — it catches\n"
         "default and sloppy Karma deploys, the categories that matter to\n"
         "most users."),

        ("Attack-toolkit signature library (ATTACK TOOLKIT alert)",
         "Sonar ships with a small database of signatures for known offensive "
         "WiFi tools: Hak5 WiFi Pineapple (and PineAP module / named instances), "
         "Hak5 Mango / Tetra / Coconut, hostapd-mana, bettercap, plus the historic "
         "'Free Public WiFi' worm / Mana honeypot template. When a scanned AP's "
         "beacon matches a signature, Sonar flags it with an ATTACK TOOLKIT: "
         "<name> label.\n\n"
         "Tools that copy a target SSID (Wifiphisher, Fluxion) or take a fully "
         "user-chosen SSID (airbase-ng, Eaphammer) cannot be SSID-fingerprinted "
         "and are NOT in this DB by design. Sonar catches those via Hunt Karma "
         "(probe-list mismatch) and IE fingerprinting (SPOOFED IE alert) — see "
         "those sections.\n\n"
         "The DB is at /opt/phantom/desktop/sonar-signatures.json (default) "
         "with an override at /etc/phantom/sonar-signatures.json. It's plain "
         "JSON — patterns can be added or tuned without a code change.\n\n"
         "Discipline: every signature in the default DB cites a public source "
         "(tool's own docs, repo, or research paper). False-positive prevention "
         "matters more than coverage — better to ship 10 verifiable signatures "
         "than 50 hallucinated ones. Generic names like 'Free WiFi' are NOT "
         "matched because legitimate venues use them.\n\n"
         "If Sonar misses an attack toolkit you know exists, drop a signature "
         "into the override file. OTA updates only refresh the bundled default; "
         "your overrides stick."),

        ("IE fingerprint — what it catches (SPOOFED IE alert)",
         "An advanced evil twin spoofs both your SSID AND your BSSID. The simple "
         "BSSID-mismatch check misses that. IE fingerprinting catches it because "
         "the attacker would need to replicate every stable IE bit-for-bit — "
         "different hardware, driver, country code, and regulatory profile make "
         "this nearly impossible.\n\n"
         "When the alert fires: your own AP shows SPOOFED IE in red. That means "
         "either (a) someone is actively impersonating your AP right now — "
         "disconnect immediately and investigate, or (b) your real AP changed "
         "legitimately and you need to re-snapshot.\n\n"
         "Trusted neighbor APs get fingerprinted the moment you click Trust "
         "Selected (their current beacon becomes the baseline). They get the "
         "same SPOOFED IE protection."),

        ("What's an evil twin and why does this matter?",
         "An evil twin is a WiFi access point impersonating another. Attacker sets "
         "up a laptop broadcasting \"Starbucks WiFi\" in a Starbucks — your phone "
         "auto-connects to the stronger signal, the attacker now routes your "
         "internet and sees everything not encrypted end-to-end.\n\n"
         "Sonar spots twins three ways: (1) same SSID as your AP but different BSSID "
         "= EVIL TWIN, flagged immediately. (2) Same SSID AND same BSSID but "
         "different IE fingerprint = SPOOFED IE, the harder-to-detect cloned-MAC "
         "version. (3) Unknown open network with unusually strong signal.\n\n"
         "Best defense beyond Sonar: don't let your devices auto-join open networks, "
         "and always use a VPN on public WiFi."),

        ("Arm rogue-block — defensive Pi-hole poison",
         "When Sonar confirms an EVIL TWIN or matches an ATTACK TOOLKIT signature, "
         "the detail panel shows an Arm rogue-block button. Clicking it (after a "
         "confirmation dialog) tells Pi-hole to return NXDOMAIN for the captive-"
         "portal probe domains that Windows / iOS / Android use to detect "
         "internet connectivity.\n\n"
         "EFFECT: a device that autoconnects to the rogue gets a visible 'no "
         "internet' warning instead of silently routing through it. The user "
         "notices and disconnects.\n\n"
         "NETWORK-WIDE TRADEOFF: the block is a Pi-hole regex entry, so it "
         "applies to ALL clients on your LAN — not just clients on the rogue. "
         "While armed, Windows / iOS / Android devices on your real AP will "
         "ALSO see captive-portal probes fail. That's intentional: a fleet-"
         "wide failure is a loud signal that an evil twin is currently flagged.\n\n"
         "AUTO-EXPIRE: 24 hours. A timer (ghostport-rogue-expire) clears stale "
         "entries automatically. You can also Release manually from the same "
         "panel — the button flips to Release while armed.\n\n"
         "STRICT GATE: Arm only appears for EVIL TWIN or ATTACK TOOLKIT labels — "
         "deliberately NOT for SPOOFED IE alone. SPOOFED IE can fire from a "
         "legitimate beacon drift (firmware update, regulatory tweak); poisoning "
         "the network on a soft signal would be reckless. EVIL TWIN (same SSID, "
         "different BSSID) is a structural confirmation; an ATTACK TOOLKIT match "
         "is a known offensive-tool fingerprint. Either is enough.\n\n"
         "When at least one block is armed, the top of the Sonar window shows "
         "an amber banner reminding you which BSSIDs are currently poisoned."),

        ("Probe-Request Capture (button bar)",
         "WHAT: Wi-Fi clients (phones, laptops) constantly broadcast \"probe "
         "requests\" — short frames asking \"is this network nearby?\" — for "
         "every SSID they remember. A Wi-Fi Pineapple / Karma rig listens for "
         "these and replies \"yes I'm that network\" to lure the device into "
         "connecting. Probe-Request Capture lets Sonar see the probes too, so "
         "we can spot devices being targeted and rigs that respond to anything.\n\n"
         "PRIVACY: probes leak. They reveal which networks a device has joined "
         "before — \"home-WiFi\", \"airport-lounge-2024\", \"Bob's iPhone hotspot\". "
         "That's neighbors' personal data, not just yours. Sonar will not "
         "capture probe-requests by default. Three modes:\n\n"
         "  OFF (default) — no probe-request capture. Sonar still sees beacons, "
         "deauth, and EAPOL.\n"
         "  MAC-ONLY — captures source-MAC + signal + channel, redacts the "
         "requested SSID at the parser. Lets you detect \"someone is probing "
         "near my Pi\" without recording which networks they remember. Privacy-"
         "preserving for the use case of presence detection.\n"
         "  FULL — captures source-MAC + signal + channel + the requested SSID. "
         "Active threat-hunt mode; surfaces Pineapple-style targeting in real "
         "time. Use only when you actively suspect rogue activity. Other "
         "people's probe SSIDs end up in your event database.\n\n"
         "MECHANICS: setting takes effect immediately — the sniffer service "
         "restarts on save. Off-by-default is enforced by the daemon, not just "
         "the UI: a missing or corrupt config file is treated as Off. The "
         "privacy-disclosure modal fires every time you promote to Full, not "
         "just the first time, so the consent is renewed on every change."),

        ("Probe Captures tab",
         "Switch to the Probe Captures tab to see live probe-request events without "
         "leaving the app — every device near you that's looking for a known network "
         "shows up here as it's heard.\n\n"
         "Top of the tab: capture-mode buttons (Off / MAC-Only / Full). They do the "
         "same thing as the ⚙ Probe Capture settings dialog — the dialog adds the "
         "long-form privacy explanation and a confirmation modal for Full.\n\n"
         "Each row shows: time-since (e.g. \"12s ago\"), source MAC + vendor, signal "
         "strength as a colored bar, and the SSID being probed (or \"(broadcast)\" "
         "when the device is doing a generic scan).\n\n"
         "Filter row: time window (Last 5m / 1h / All) and a checkbox to hide "
         "broadcast probes (useful when you only care about devices probing for "
         "a specific named network)."),
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)

    # ── T-0059 Probe-request capture settings ──────────────────────────
    # Three-tier toggle (off / mac-only / full) for Wi-Fi Pineapple and
    # Karma detection. Default off; a separate disclosure modal fires
    # every time the user promotes to Full so the privacy-sensitive mode
    # can't be enabled by accident. State persists in /etc/ghostport/sonar.json
    # and the sniffer service restarts on save.

    # (value, icon, label, badge_text, badge_css_class, description, frame_css_class)
    # badge_text + badge_css_class identify the privacy stance at a glance:
    #   off → "RECOMMENDED" green tag; mac → no badge; full → "PRIVACY-SENSITIVE" amber.
    PROBE_CAPTURE_TIERS = [
        ("off", "🟢", "Off",
         "RECOMMENDED", "probe-tier-recommended",
         "Probe-requests aren't captured. Sonar still sees beacons, deauth, and EAPOL.",
         "probe-tier-off"),
        ("mac-only", "🔵", "MAC-Only",
         "PRIVACY-PRESERVING", "probe-tier-info",
         "See who's probing near your Pi without recording which networks they remember. SSID is redacted at the parser before it reaches the bus, database, or logs.",
         "probe-tier-mac"),
        ("full", "🟠", "Full",
         "PRIVACY-SENSITIVE", "probe-tier-warning-badge",
         "Active threat-hunt. Records the network names every nearby device has previously connected to. Use only when you actively suspect a rogue rig.",
         "probe-tier-full"),
    ]

    def _read_probe_mode(self):
        """Read current probe_capture mode via the sudo helper.
        Falls back to 'off' on any error — privacy-safe default."""
        try:
            out, _err, rc = self.run_sudo(["/usr/local/bin/gp-sonar-config", "get", "probe_capture"], timeout=5)
            v = (out or "").strip()
            if v in ("off", "mac-only", "full"):
                return v
        except Exception:
            pass
        return "off"

    def _write_probe_mode(self, mode):
        """Write new probe_capture mode via the sudo helper. Returns
        (success, error_message). Helper restarts the sniffer service on
        successful write so the new mode takes effect immediately."""
        if mode not in ("off", "mac-only", "full"):
            return False, f"Invalid mode: {mode!r}"
        try:
            out, err, rc = self.run_sudo(
                ["/usr/local/bin/gp-sonar-config", "set", "probe_capture", mode],
                timeout=10,
            )
            if rc == 0:
                return True, ""
            return False, (err or out or "Unknown error").strip()
        except Exception as e:
            return False, str(e)

    # ── T-0165 OUI lookup (compact, in-app) ────────────────────────────
    # Slim built-in map for the most common consumer devices; extended at
    # runtime via /etc/phantom/oui-extras.json (same file Stonefish/Crew
    # Manifest read). Kept inline so Sonar has no import-time dep on
    # gp-stonefish.py (which is a script, not a module).
    _OUI_BUILTIN = {
        "00:1A:11": "Google", "F4:F5:D8": "Google", "FC:AA:14": "Google",
        "A4:77:33": "Google", "20:DF:B9": "Google", "AC:63:BE": "Google-Nest",
        "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi",
        "E4:5F:01": "Raspberry Pi", "28:CD:C1": "Raspberry Pi",
        "AC:DE:48": "Apple", "3C:22:FB": "Apple", "DC:A9:04": "Apple",
        "F8:FF:C2": "Apple", "A4:83:E7": "Apple", "F4:1B:A1": "Apple",
        "00:25:00": "Apple", "14:BD:61": "Apple",
        "00:1D:D8": "Microsoft", "00:50:F2": "Microsoft", "7C:1E:52": "Microsoft",
        "F0:18:98": "Samsung", "5C:0A:5B": "Samsung", "78:25:AD": "Samsung",
        "BC:72:B1": "Samsung-TV", "44:65:0D": "Amazon-Echo", "F0:D2:F1": "Amazon-Echo",
        "00:17:88": "Philips-Hue", "EC:B5:FA": "Philips-Hue",
        "D0:52:A8": "TP-Link", "F0:9F:C2": "Ubiquiti", "24:5A:4C": "Ubiquiti",
        "18:E8:29": "Ubiquiti", "70:8B:CD": "Ubiquiti",
        "88:C9:D0": "LG", "B8:AD:3E": "LG-Smart-TV",
        "E0:75:7D": "Roku", "CC:6D:A0": "Roku",
    }

    def _load_oui_extras(self):
        try:
            with open(OUI_EXTRAS_FILE) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return {}

    def _oui_lookup(self, mac):
        """Return vendor name for MAC prefix, or '' for unknown.
        Locally-administered (random) MACs return 'Random' since the OUI
        is meaningless for privacy-randomized addresses."""
        if not mac or len(mac) < 8:
            return ""
        try:
            first_byte = int(mac[:2], 16)
        except ValueError:
            return ""
        if first_byte & 0x02:
            return "Random"
        prefix = mac.upper().replace("-", ":")[:8]
        # Lazy-load extras once per session — file is 39k entries (per memory)
        if not hasattr(self, "_oui_extras_cache"):
            self._oui_extras_cache = self._load_oui_extras()
        return (self._OUI_BUILTIN.get(prefix)
                or self._oui_extras_cache.get(prefix, ""))

    def _confirm_full_capture(self):
        """Privacy disclosure shown every time the user picks Full mode.
        Tighter than a generic GtkMessageDialog so the ask reads quickly:
        what the mode logs, whose data it logs, retention window."""
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Enable Full probe capture?",
        )
        dlg.format_secondary_markup(
            "Full mode logs the <b>previously-joined network names</b> of every "
            "nearby phone, laptop, and Wi-Fi device. Those devices broadcast that "
            "information in cleartext — but their owners did not consent to your "
            "Pi recording it.\n\n"
            "Use only when you actively suspect rogue Wi-Fi activity. "
            "For routine awareness, <b>MAC-Only</b> gives you presence detection "
            "without recording anyone's network history.\n\n"
            "<small>Captured SSIDs are kept in <tt>/opt/phantom/data/events.db</tt> "
            "for 7 days, then auto-pruned.</small>"
        )
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        btn_ok = dlg.add_button("Yes, enable Full", Gtk.ResponseType.OK)
        try:
            btn_ok.get_style_context().add_class("destructive-action")
        except Exception:
            pass
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    # ── T-0165 Probe Captures tab ──────────────────────────────────────

    PROBE_TIME_WINDOWS = [
        ("5m",  "Last 5 min", 300),
        ("1h",  "Last 1 hour", 3600),
        ("all", "All",         86400),  # cap at 24h to bound the SQL scan
    ]

    def _build_probe_captures_page(self):
        """T-0165 — second Notebook page surfacing live probe-request events
        in a clickable list, with inline capture-mode controls so the user
        can start/stop/change capture without leaving the GUI."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_start(8)
        page.set_margin_end(8)
        page.set_margin_top(8)
        page.set_margin_bottom(8)

        # ── Capture-mode control row ──
        ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ctrl_row.pack_start(self.make_label("Capture mode:", "gp-dim"), False, False, 0)

        self._probe_mode_buttons = {}
        for value, _icon, label, *_rest in self.PROBE_CAPTURE_TIERS:
            btn = Gtk.ToggleButton(label=label)
            btn.connect("toggled", self._on_probe_mode_toggled, value)
            self._probe_mode_buttons[value] = btn
            ctrl_row.pack_start(btn, False, False, 0)

        # Status indicator on the right end of the control row
        self.lbl_probe_status = self.make_label("", "gp-dim")
        ctrl_row.pack_end(self.lbl_probe_status, False, False, 0)

        page.pack_start(ctrl_row, False, False, 0)

        # ── Filter row ──
        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filter_row.pack_start(self.make_label("Show:", "gp-dim"), False, False, 0)

        self._probe_window_buttons = {}
        for key, label, _seconds in self.PROBE_TIME_WINDOWS:
            btn = Gtk.ToggleButton(label=label)
            btn.connect("toggled", self._on_probe_window_toggled, key)
            self._probe_window_buttons[key] = btn
            filter_row.pack_start(btn, False, False, 0)
        # Default window = 5m
        self._probe_window = "5m"
        self._probe_window_buttons["5m"].set_active(True)

        self.chk_hide_broadcast = Gtk.CheckButton(label="Hide broadcast probes")
        self.chk_hide_broadcast.connect("toggled", lambda *_a: self._refresh_probe_captures())
        filter_row.pack_start(self.chk_hide_broadcast, False, False, 12)

        page.pack_start(filter_row, False, False, 0)

        # ── List of probe events ──
        self.probe_listbox = Gtk.ListBox()
        self.probe_listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        # Empty-state placeholder — replaced when refresh finds rows.
        self.probe_empty_label = self.make_label("", "gp-dim")
        self.probe_empty_label.set_halign(Gtk.Align.CENTER)
        self.probe_empty_label.set_valign(Gtk.Align.CENTER)
        self.probe_listbox.set_placeholder(self.probe_empty_label)
        self.probe_empty_label.show()

        scrolled = self.make_scrolled(self.probe_listbox)
        page.pack_start(scrolled, True, True, 0)

        # Prime status + first refresh
        self._refresh_probe_status()
        self._refresh_probe_captures()
        self.poll_start(PROBE_POLL_INTERVAL_SEC, self._on_probe_poll_tick)

        return page

    def _on_probe_poll_tick(self):
        """Combined tick — keep status indicator + list current."""
        self._refresh_probe_status()
        self._refresh_probe_captures()

    def _refresh_probe_status(self):
        """Update the capture-mode toggle states + status text from current config."""
        mode = self._read_probe_mode()
        # Suppress the toggle handler while we sync state to avoid a cascade
        # of save calls when refresh_status discovers external changes.
        for value, btn in self._probe_mode_buttons.items():
            btn.handler_block_by_func(self._on_probe_mode_toggled)
            btn.set_active(value == mode)
            btn.handler_unblock_by_func(self._on_probe_mode_toggled)

        # Sniffer service active?
        try:
            rc = subprocess.run(
                ["systemctl", "is-active", "ghostport-sonar-sniffer.service"],
                capture_output=True, text=True, timeout=3,
            ).returncode
            sniffer_active = (rc == 0)
        except (subprocess.TimeoutExpired, OSError):
            sniffer_active = False

        if mode == "off":
            self.lbl_probe_status.set_text("Capture: Off")
        elif sniffer_active:
            self.lbl_probe_status.set_text(f"Capture: {mode} • sniffer active")
        else:
            self.lbl_probe_status.set_text(f"Capture: {mode} • sniffer inactive (no monitor adapter?)")

    def _on_probe_mode_toggled(self, btn, value):
        if not btn.get_active():
            return  # untoggle event — ignore; only act on activations
        # Full-mode privacy disclosure piggybacks on the existing dialog helper.
        if value == "full" and not self._confirm_full_capture():
            # User cancelled — restore the prior selection silently.
            self._refresh_probe_status()
            return
        ok, err = self._write_probe_mode(value)
        if not ok:
            self._notify_error("Probe capture", f"Could not change mode: {err}")
            self._refresh_probe_status()
            return
        # Success — sniffer service auto-restarts. Status will refresh on next poll.
        self.set_status(f"Probe capture set to: {value}")
        # Force one immediate status refresh so the label updates without delay.
        GLib.timeout_add(800, lambda: (self._refresh_probe_status(), False)[1])

    def _on_probe_window_toggled(self, btn, key):
        if not btn.get_active():
            return
        # Single-select segmented behavior — turn off the others.
        for other_key, other_btn in self._probe_window_buttons.items():
            if other_key != key and other_btn.get_active():
                other_btn.handler_block_by_func(self._on_probe_window_toggled)
                other_btn.set_active(False)
                other_btn.handler_unblock_by_func(self._on_probe_window_toggled)
        self._probe_window = key
        self._refresh_probe_captures()

    def _on_notebook_switch_page(self, _notebook, _page, page_num):
        # Page index 1 = Probe Captures (per build_ui order). Refresh on entry.
        if page_num == 1:
            self._refresh_probe_status()
            self._refresh_probe_captures()

    def _format_age(self, ts):
        age = max(0, int(time.time() - ts))
        if age < 60:
            return f"{age}s ago"
        if age < 3600:
            return f"{age // 60}m ago"
        if age < 86400:
            return f"{age // 3600}h ago"
        return f"{age // 86400}d ago"

    def _format_rssi_bar(self, dbm):
        """RSSI -30 (excellent) to -90 (faint) → 5-block bar."""
        if dbm is None:
            return "····· "
        try:
            d = int(dbm)
        except (TypeError, ValueError):
            return "····· "
        # Map dBm to filled blocks: -30 → 5, -50 → 4, -65 → 3, -75 → 2, -85 → 1
        if d >= -50: filled = 5
        elif d >= -60: filled = 4
        elif d >= -70: filled = 3
        elif d >= -80: filled = 2
        else: filled = 1
        return "▮" * filled + "▯" * (5 - filled)

    def _refresh_probe_captures(self):
        """Pull category=probe_request from the events bus and rebuild the list.
        Newest first; capped at PROBE_LIST_LIMIT to keep GTK responsive."""
        if not hasattr(self, "probe_listbox"):
            return  # build_ui not finished yet; first poll will retry
        # Resolve the active time window (seconds)
        window_sec = next(
            (s for k, _l, s in self.PROBE_TIME_WINDOWS if k == self._probe_window),
            300,
        )
        try:
            events = gp_events.recent(
                since_seconds=window_sec,
                source="sonar-sniffer",
                category="probe_request",
            )
        except Exception as e:
            sys.stderr.write(f"[sonar] probe-capture refresh failed: {e}\n")
            events = []

        hide_bcast = self.chk_hide_broadcast.get_active()
        if hide_bcast:
            events = [e for e in events if (e.get("details") or {}).get("ssid")]

        events = events[:PROBE_LIST_LIMIT]

        # Wipe + rebuild. ListBox is small (≤250 rows) so full rebuild beats
        # diff'ing — same pattern the AP list uses.
        for row in self.probe_listbox.get_children():
            self.probe_listbox.remove(row)

        if not events:
            mode = self._read_probe_mode()
            if mode == "off":
                msg = "Capture is Off — pick MAC-Only or Full above to start."
            elif window_sec <= 300:
                msg = "No probes in the last 5 minutes."
            else:
                msg = "No probes recorded in this window."
            self.probe_empty_label.set_text(msg)
            return

        for ev in events:
            row = self._build_probe_row(ev)
            self.probe_listbox.add(row)
        self.probe_listbox.show_all()

    def _build_probe_row(self, ev):
        details = ev.get("details") or {}
        src = details.get("src", "??:??:??:??:??:??")
        ssid = details.get("ssid") or "(broadcast)"
        rssi = details.get("rssi_dbm")
        channel = details.get("channel")
        vendor = self._oui_lookup(src)

        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_margin_start(8)
        hbox.set_margin_end(8)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)

        # Time
        lbl_time = self.make_label(self._format_age(ev["timestamp"]), "gp-dim")
        lbl_time.set_xalign(0)
        lbl_time.set_size_request(80, -1)
        hbox.pack_start(lbl_time, False, False, 0)

        # MAC + vendor
        mac_text = src
        if vendor:
            mac_text = f"{src}  ({vendor})"
        lbl_mac = self.make_label(mac_text, "")
        lbl_mac.set_xalign(0)
        lbl_mac.set_size_request(280, -1)
        hbox.pack_start(lbl_mac, False, False, 0)

        # Signal bar
        lbl_signal = self.make_label(self._format_rssi_bar(rssi), "gp-dim")
        lbl_signal.set_xalign(0)
        if rssi is not None:
            lbl_signal.set_tooltip_text(f"{rssi} dBm")
        hbox.pack_start(lbl_signal, False, False, 0)

        # SSID (or broadcast)
        ssid_class = "gp-dim" if ssid == "(broadcast)" else ""
        lbl_ssid = self.make_label(ssid, ssid_class)
        lbl_ssid.set_xalign(0)
        lbl_ssid.set_ellipsize(Pango.EllipsizeMode.END)
        hbox.pack_start(lbl_ssid, True, True, 0)

        # Channel
        if channel is not None:
            lbl_ch = self.make_label(f"ch{channel}", "gp-dim")
            lbl_ch.set_xalign(1)
            hbox.pack_end(lbl_ch, False, False, 0)

        row.add(hbox)
        return row

    def _on_probe_capture_settings(self, _btn):
        # Re-styled 2026-05-01 — replaced stacked radio+label rows with framed
        # tier-cards (icon + title + privacy badge + description, full-card
        # click selects). Color-coded borders signal privacy stance at a
        # glance. Currently-active tier is highlighted up top so the user
        # always knows what the system is doing.
        current = self._read_probe_mode()
        current_label = next(
            (label for v, _i, label, *_ in self.PROBE_CAPTURE_TIERS if v == current),
            "Off",
        )

        dlg = Gtk.Dialog(
            title="Probe-Request Capture",
            transient_for=self,
            modal=True,
        )
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save_btn = dlg.add_button("Save", Gtk.ResponseType.OK)
        try:
            save_btn.get_style_context().add_class("suggested-action")
        except Exception:
            pass
        dlg.set_default_size(580, -1)

        content = dlg.get_content_area()
        content.set_spacing(10)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(16)
        content.set_margin_bottom(12)

        # Header — title + one-line summary + current state badge.
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_lbl = Gtk.Label()
        title_lbl.set_markup(
            "<span size='large' weight='bold'>Probe-Request Capture</span>"
        )
        title_lbl.set_xalign(0)
        header_box.pack_start(title_lbl, False, False, 0)

        subtitle_lbl = Gtk.Label(
            label="Detect Wi-Fi Pineapple / Karma rigs by listening to what "
                  "networks nearby devices are looking for."
        )
        subtitle_lbl.set_line_wrap(True)
        subtitle_lbl.set_xalign(0)
        subtitle_lbl.get_style_context().add_class("dim-label")
        header_box.pack_start(subtitle_lbl, False, False, 0)

        current_lbl = Gtk.Label()
        current_lbl.set_markup(
            f"<span size='small'>Currently active: <b>{current_label}</b></span>"
        )
        current_lbl.set_xalign(0)
        current_lbl.set_margin_top(4)
        header_box.pack_start(current_lbl, False, False, 0)

        content.pack_start(header_box, False, False, 0)

        # Spacer.
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(8)
        sep.set_margin_bottom(4)
        content.pack_start(sep, False, False, 0)

        # Tier cards — each row is a clickable frame containing radio + icon +
        # title + privacy badge + description. Whole frame is the click target
        # via Gtk.EventBox so the user doesn't have to hit the tiny radio dot.
        radios = []
        first_radio = None
        for value, icon, label, badge_text, badge_class, desc, frame_class in self.PROBE_CAPTURE_TIERS:
            frame = Gtk.Frame()
            ctx = frame.get_style_context()
            ctx.add_class("probe-tier-frame")
            ctx.add_class(frame_class)
            if value == current:
                ctx.add_class("probe-tier-active")

            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            inner.set_margin_start(12)
            inner.set_margin_end(12)
            inner.set_margin_top(10)
            inner.set_margin_bottom(10)

            radio = Gtk.RadioButton.new_from_widget(first_radio)
            if first_radio is None:
                first_radio = radio
            radio._gp_value = value
            if value == current:
                radio.set_active(True)
            radios.append(radio)
            inner.pack_start(radio, False, False, 0)

            # Big emoji icon — quickly differentiates the three tiers visually.
            icon_lbl = Gtk.Label()
            icon_lbl.set_markup(f"<span size='x-large'>{icon}</span>")
            inner.pack_start(icon_lbl, False, False, 0)

            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            tier_title = Gtk.Label()
            tier_title.set_markup(f"<b>{label}</b>")
            tier_title.set_xalign(0)
            title_row.pack_start(tier_title, False, False, 0)

            badge = Gtk.Label(label=badge_text)
            badge.get_style_context().add_class(badge_class)
            badge.set_xalign(0)
            title_row.pack_start(badge, False, False, 0)
            text_box.pack_start(title_row, False, False, 0)

            desc_lbl = Gtk.Label(label=desc)
            desc_lbl.set_line_wrap(True)
            desc_lbl.set_xalign(0)
            desc_lbl.get_style_context().add_class("dim-label")
            text_box.pack_start(desc_lbl, False, False, 0)

            inner.pack_start(text_box, True, True, 0)
            frame.add(inner)

            # Whole-card click selects the tier — Gtk.EventBox wraps the frame
            # so press events anywhere inside activate the radio.
            eb = Gtk.EventBox()
            eb.add(frame)
            eb.connect(
                "button-press-event",
                lambda _w, _e, r=radio: (r.set_active(True), False)[1],
            )
            content.pack_start(eb, False, False, 0)

        dlg.show_all()

        # Apply the dialog-specific CSS once on first show.
        try:
            self._apply_css(extra_css=self._extra_css())
        except Exception:
            pass

        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                dlg.destroy()
                return
            chosen = next((r._gp_value for r in radios if r.get_active()), "off")
            if chosen == current:
                dlg.destroy()
                return
            # Stronger gate when promoting to Full from anything else.
            if chosen == "full" and not self._confirm_full_capture():
                # User backed out of the disclosure — leave dialog open so
                # they can pick a different tier instead.
                continue
            ok, errmsg = self._write_probe_mode(chosen)
            dlg.destroy()
            if ok:
                self.set_status(f"Probe capture: {chosen.upper()} (sniffer restarted)")
            else:
                err_dlg = Gtk.MessageDialog(
                    transient_for=self,
                    modal=True,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Could not save probe-capture setting",
                )
                err_dlg.format_secondary_text(errmsg or "Unknown error")
                err_dlg.run()
                err_dlg.destroy()
            return

    # T-0021 item 2: 4-tier severity color scheme. Base class only ships
    # gp-card-danger / gp-card-warning, so suspicious + warning rendered
    # identically. Add a distinct amber tier for suspicious and a dimmer
    # neutral tier for ignored APs (item 3).
    def _extra_css(self):
        return """
.gp-status-bar { font-size: 13px; }
.gp-card-suspicious {
    background-color: rgba(255, 140, 50, 0.08);
    border: 1px solid rgba(255, 140, 50, 0.40);
    border-radius: 8px;
    padding: 10px;
    margin: 4px 0;
}
.gp-card-ignored {
    background-color: rgba(140, 140, 140, 0.05);
    border: 1px solid rgba(140, 140, 140, 0.20);
    border-radius: 8px;
    padding: 10px;
    margin: 4px 0;
    opacity: 0.65;
}
.gp-warning-amber {
    color: rgb(255, 165, 70);
    font-family: monospace;
    font-size: 12px;
    font-weight: bold;
}
.gp-ignored {
    color: rgb(160, 160, 160);
    font-family: monospace;
    font-size: 11px;
    font-style: italic;
}

/* T-0059 Probe-Capture dialog — color-coded tier cards. The frame border
   signals privacy stance; the active tier gets a brighter green border so
   the user immediately sees what's currently saved. */
.probe-tier-frame {
    border-radius: 8px;
    margin: 4px 0;
    background-color: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(140, 140, 140, 0.20);
}
.probe-tier-frame:hover {
    background-color: rgba(255, 255, 255, 0.05);
}
.probe-tier-off {
    border-color: rgba(140, 140, 140, 0.30);
}
.probe-tier-mac {
    border-color: rgba(80, 180, 255, 0.35);
}
.probe-tier-full {
    border-color: rgba(255, 140, 50, 0.45);
}
.probe-tier-active {
    border-width: 2px;
    border-color: rgb(120, 220, 140);
    background-color: rgba(120, 220, 140, 0.06);
}
/* Tier-badge labels — small uppercase tags next to each tier name. */
.probe-tier-recommended {
    color: rgb(120, 220, 140);
    font-size: 9px;
    font-weight: bold;
    letter-spacing: 1px;
}
.probe-tier-info {
    color: rgb(80, 180, 255);
    font-size: 9px;
    font-weight: bold;
    letter-spacing: 1px;
}
.probe-tier-warning-badge {
    color: rgb(255, 165, 70);
    font-size: 9px;
    font-weight: bold;
    letter-spacing: 1px;
}
"""

    def on_theme_changed(self):
        # Re-apply Sonar-specific CSS after a theme swap; base class poll
        # otherwise wipes our extra rules on every theme.json change.
        self._apply_css(extra_css=self._extra_css())

    def __init__(self):
        super().__init__("SONAR", "sonar", (900, 650))
        self.aps = []
        self.scanning = False
        self.our_ssid = ""
        self.our_bssid = ""
        self.trusted = []
        self.trusted_bssids = set()
        self.signatures = []
        # Karma rigs detected by Hunt Karma — bssid -> {reason, real_ssid, decoy_ssid, signal}
        # In-memory only; Karma is a temporal/location threat, no disk persistence.
        self.karma_rigs = OrderedDict()
        self.scanning_karma = False
        # Event-emit dedup: tracks (category, bssid) tuples we've already
        # written to the cross-app event bus this session, so a sustained
        # threat condition doesn't spam the bus on every passive scan.
        self._emitted_events = OrderedDict()
        # T-0021 item 3: per-session "Ignore" set. User can silence a known
        # legitimate-but-noisy AP (coffee-shop STRONG SIGNAL etc.) without
        # going through Trust (which baselines its IE fingerprint). Cleared
        # on app restart by design — Trust is for permanence, Ignore for now.
        self.ignored_bssids = set()
        self.selected_ap = None
        self.last_scan_time = None
        # T-0040: anomaly score map populated after every scan; consumed by
        # _show_detail to surface the score to the user.
        self.anomaly_scores = {}

        # Load our AP info, trusted list, attack-toolkit signatures
        self.our_ssid, self.our_bssid = get_our_ap()
        self._reload_trusted()
        self.signatures, self._sig_load_error = load_signatures()

        self.build_ui()

        # T-0021 item 2: load Sonar-specific CSS (4-tier severity tiers).
        # Must come after build_ui so the provider attaches to the screen
        # before the listbox first paints.
        self._apply_css(extra_css=self._extra_css())

        # Surface any signature-load problem after the status bar exists so
        # the user notices their custom DB isn't active (T-0018).
        if self._sig_load_error:
            self.set_status(self._sig_load_error)
            self._notify_error("Sonar signature DB", self._sig_load_error)

        # T-0036: prime the background-status label and start the poll. Base
        # class _on_destroy clears all timers so no manual cleanup needed.
        self._refresh_bg_status()
        self.poll_start(BG_POLL_INTERVAL_SEC, self._refresh_bg_status)

        # T-0046: prime the rogue-block banner + start its poll. Banner
        # stays hidden when no blocks are armed; reveals when 1+ exist.
        self._refresh_rogue_banner()
        self.poll_start(ROGUE_POLL_INTERVAL_SEC, self._refresh_rogue_banner)

        # T-0040: prune stale entries from the anomaly baseline once at startup.
        # 30 days matches the module's default and bounds disk growth without
        # losing the slow-moving AP fingerprints (home/cafe regulars).
        try:
            gp_sonar_anomaly.prune_stale(30)
        except Exception as e:
            sys.stderr.write(f"[sonar] anomaly prune failed: {e}\n")

    def _notify_error(self, title, body):
        """Best-effort desktop notification for failures the user must know about."""
        try:
            subprocess.Popen(
                ["notify-send", "-u", "critical", "-i", "dialog-error", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    # T-0021 item 5: critical-threat notification. Bus emit reaches the
    # correlation engine but doesn't pop a notification — user must be
    # focused on Sonar's window to see SPOOFED IE / EVIL TWIN / KARMA RIG /
    # ATTACK TOOLKIT. notify-send fires regardless of focus, dedup happens
    # at the emit-call site (same set as the bus-event dedup).
    def _notify_threat(self, label, body):
        try:
            subprocess.Popen(
                ["notify-send", "-u", "critical", "-i", "dialog-warning",
                 f"Sonar: {label}", body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    # T-0036: surface gp-sonar-detect background helper findings.
    # Helper writes BG_STATE_FILE (atomic os.replace, single writer); GUI
    # polls every BG_POLL_INTERVAL_SEC. Stale or missing file degrades
    # silently to "Background scan: never".
    def _read_bg_state(self):
        try:
            with open(BG_STATE_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _refresh_bg_status(self):
        if not getattr(self, "lbl_bg_status", None):
            return True  # widget not built yet — keep polling
        state = self._read_bg_state()
        if not state or not state.get("last_run"):
            self.lbl_bg_status.set_text("Background scan: never")
            return True
        last = state["last_run"]
        try:
            ts_str = time.strftime("%H:%M", time.localtime(last))
        except (TypeError, OSError):
            ts_str = "?"
        n_findings = len(state.get("findings", []))
        emitted = state.get("emitted_this_run", 0)
        # Recent unread findings → bold accent; old/empty → dim.
        if emitted:
            self.lbl_bg_status.set_text(
                f"Background scan: {ts_str} ({emitted} new, {n_findings} total)"
            )
            self.lbl_bg_status.get_style_context().remove_class("gp-dim")
            self.lbl_bg_status.get_style_context().add_class("gp-warning-amber")
        elif n_findings:
            self.lbl_bg_status.set_text(
                f"Background scan: {ts_str} ({n_findings} historical)"
            )
            self.lbl_bg_status.get_style_context().remove_class("gp-warning-amber")
            self.lbl_bg_status.get_style_context().add_class("gp-dim")
        else:
            self.lbl_bg_status.set_text(f"Background scan: {ts_str} (clean)")
            self.lbl_bg_status.get_style_context().remove_class("gp-warning-amber")
            self.lbl_bg_status.get_style_context().add_class("gp-dim")
        return True

    def _on_bg_status_clicked(self, _widget, _event):
        state = self._read_bg_state() or {}
        findings = state.get("findings", [])

        dialog = Gtk.Dialog(
            title="Background Sonar Findings",
            transient_for=self,
            modal=True,
        )
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(640, 420)

        content = dialog.get_content_area()
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        if state.get("last_run"):
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["last_run"]))
            ap_count = state.get("ap_count", "?")
            header = self.make_label(
                f"Last background run: {ts_str}  •  {ap_count} APs seen",
                "gp-accent",
            )
        else:
            header = self.make_label(
                "Background helper has not run yet. The systemd timer fires "
                "every ~5 minutes once enabled.",
                "gp-dim",
            )
        content.pack_start(header, False, False, 4)

        if findings:
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            listbox = Gtk.ListBox()
            # Newest first
            for f in reversed(findings):
                row = Gtk.ListBoxRow()
                vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                vb.set_margin_start(8)
                vb.set_margin_end(8)
                vb.set_margin_top(6)
                vb.set_margin_bottom(6)
                ts = f.get("ts", 0)
                try:
                    ts_str = time.strftime("%m-%d %H:%M", time.localtime(ts))
                except (TypeError, OSError):
                    ts_str = "?"
                top = self.make_label(
                    f"{ts_str}  •  {f.get('label', 'UNKNOWN')}  •  "
                    f"{f.get('ssid') or '(Hidden)'}",
                    "gp-warning-amber",
                )
                bot = self.make_label(
                    f"BSSID {f.get('bssid', '?')}  •  "
                    f"{f.get('signal', '?')} dBm  •  "
                    f"{f.get('encryption', '?')}",
                    "gp-dim",
                )
                vb.pack_start(top, False, False, 0)
                vb.pack_start(bot, False, False, 0)
                row.add(vb)
                listbox.add(row)
            sw.add(listbox)
            content.pack_start(sw, True, True, 4)
        else:
            empty = self.make_label(
                "No findings recorded. Either the helper hasn't run, or "
                "no SPOOFED IE / EVIL TWIN / ATTACK TOOLKIT detections "
                "have happened in the background.",
                "gp-dim",
            )
            content.pack_start(empty, True, True, 4)

        dialog.show_all()
        try:
            dialog.run()
        finally:
            dialog.destroy()

    # T-0046: rogue-block (T-0031 Phase B). Read-only helpers + subprocess
    # wrappers for the gp-rogue-block CLI. All paths degrade silently when
    # the wrapper isn't installed yet — Arm/Release buttons stay hidden.
    def _rogue_block_available(self):
        return os.access(ROGUE_BLOCK_CMD, os.X_OK)

    def _read_rogue_blocks(self):
        """Returns dict {bssid_lower: entry} or {} on missing/corrupt."""
        try:
            with open(ROGUE_BLOCKS_FILE) as f:
                data = json.load(f)
            blocks = data.get("blocks") or data
            if isinstance(blocks, list):
                return {b.get("bssid", "").lower(): b for b in blocks if b.get("bssid")}
            if isinstance(blocks, dict):
                return {k.lower(): v for k, v in blocks.items()}
            return {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _refresh_rogue_banner(self):
        """Update the top-of-window armed-state banner. Polled every
        ROGUE_POLL_INTERVAL_SEC; returns True so the timer keeps firing."""
        if not getattr(self, "rogue_banner", None):
            return True
        blocks = self._read_rogue_blocks()
        n = len(blocks)
        if n == 0:
            self.rogue_banner.set_reveal_child(False)
            return True
        # Show count + reminder of network-wide effect.
        if hasattr(self, "lbl_rogue_banner") and self.lbl_rogue_banner:
            self.lbl_rogue_banner.set_text(
                f"⚠ rogue-block ARMED for {n} BSSID(s) — captive-portal probes "
                f"are blocked NETWORK-WIDE. Auto-expires in 24h."
            )
        self.rogue_banner.set_reveal_child(True)
        return True

    def _on_arm_rogue_block(self, _btn):
        """Confirmation dialog → subprocess gp-rogue-block arm → result dialog."""
        ap = self.selected_ap
        if not ap:
            return
        bssid = ap.get("bssid", "")
        ssid = ap.get("ssid", "") or "(Hidden)"
        if not bssid:
            self.set_status("Arm rogue-block: AP has no BSSID")
            return

        # Confirmation dialog — explicit network-wide warning.
        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Arm rogue-block on {ssid} ({bssid[:8]}...)?",
        )
        confirm.format_secondary_text(
            "This will inject NXDOMAIN entries into Pi-hole for the captive-"
            "portal probe domains (msftconnecttest.com, captive.apple.com, "
            "connectivitycheck.gstatic.com).\n\n"
            "EFFECT: ALL Pi-hole clients on the LAN — not just clients on the "
            "rogue AP — will see captive-portal probes fail. A device that "
            "autoconnected to the rogue will surface a visible 'no internet' "
            "warning instead of silently routing through it.\n\n"
            "AUTO-EXPIRES: 24h. Click OK to arm; click the Release button "
            "later to clear early."
        )
        try:
            response = confirm.run()
        finally:
            confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return

        try:
            result = subprocess.run(
                ["sudo", "-n", ROGUE_BLOCK_CMD, "arm", bssid, "--ssid", ssid],
                capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self._rogue_result_dialog("Arm failed", f"Could not run wrapper: {e}")
            return

        if result.returncode == 0:
            self.set_status(f"rogue-block ARMED for {ssid} ({bssid[:8]}...)")
            self._rogue_result_dialog(
                "Rogue-block armed",
                result.stdout or "Armed successfully. Auto-expires in 24h."
            )
        else:
            self._rogue_result_dialog(
                f"Arm failed (rc={result.returncode})",
                (result.stderr or result.stdout or "no output").strip()
            )
        # Force banner refresh + re-render the detail panel so the button
        # flips to Release immediately.
        self._refresh_rogue_banner()
        if self.selected_ap:
            self._show_detail(self.selected_ap)

    def _on_release_rogue_block(self, _btn):
        ap = self.selected_ap
        if not ap:
            return
        bssid = ap.get("bssid", "")
        if not bssid:
            return
        try:
            result = subprocess.run(
                ["sudo", "-n", ROGUE_BLOCK_CMD, "release", bssid],
                capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            self._rogue_result_dialog("Release failed", f"Could not run wrapper: {e}")
            return
        if result.returncode == 0:
            self.set_status(f"rogue-block RELEASED for {bssid[:8]}...")
            self._rogue_result_dialog(
                "Rogue-block released",
                result.stdout or "Released. Pi-hole entries removed."
            )
        else:
            self._rogue_result_dialog(
                f"Release failed (rc={result.returncode})",
                (result.stderr or result.stdout or "no output").strip()
            )
        self._refresh_rogue_banner()
        if self.selected_ap:
            self._show_detail(self.selected_ap)

    def _rogue_result_dialog(self, title, body):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dlg.format_secondary_text(body[:1200] if body else "")
        try:
            dlg.run()
        finally:
            dlg.destroy()

    def _reload_trusted(self):
        self.trusted = load_trusted_aps()
        self.trusted_bssids = {t.get("bssid", "").lower() for t in self.trusted}

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # Header row with scan button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.get_style_context().add_class("gp-header")

        header_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_label = Gtk.Label(label="SONAR")
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class("gp-header-title")
        header_left.pack_start(title_label, False, False, 0)

        sub_label = Gtk.Label(label="Rogue AP Scanner")
        sub_label.set_halign(Gtk.Align.START)
        sub_label.get_style_context().add_class("gp-header-subtitle")
        header_left.pack_start(sub_label, False, False, 0)

        header_box.pack_start(header_left, True, True, 0)

        # Scan button + spinner
        scan_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        scan_box.set_valign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        scan_box.pack_start(self.spinner, False, False, 0)

        self.btn_scan = self.make_button("SCAN", self._on_scan, "gp-btn-primary")
        scan_box.pack_start(self.btn_scan, False, False, 0)

        # HUNT KARMA — explicit active probe for evil-twin wordlist + random decoys
        self.btn_hunt = self.make_button("HUNT KARMA", self._on_hunt_karma, "gp-btn-warning")
        scan_box.pack_start(self.btn_hunt, False, False, 0)

        header_box.pack_end(scan_box, False, False, 8)
        root.pack_start(header_box, False, False, 0)

        # T-0046: armed-rogue-block banner — Gtk.Revealer hidden until at
        # least one BSSID is in rogue-blocks.json. Reminds the user that
        # captive-portal probes are blocked network-wide while armed.
        self.rogue_banner = Gtk.Revealer()
        self.rogue_banner.set_reveal_child(False)
        self.rogue_banner.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        banner_box.get_style_context().add_class("gp-card-warning")
        banner_box.set_margin_start(8)
        banner_box.set_margin_end(8)
        banner_box.set_margin_top(2)
        banner_box.set_margin_bottom(2)
        self.lbl_rogue_banner = self.make_label("", "gp-warning-amber")
        banner_box.pack_start(self.lbl_rogue_banner, True, True, 8)
        self.rogue_banner.add(banner_box)
        root.pack_start(self.rogue_banner, False, False, 0)

        # Info bar: our AP + last scan time
        info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        info_bar.set_margin_start(12)
        info_bar.set_margin_end(12)
        info_bar.set_margin_top(6)
        info_bar.set_margin_bottom(4)

        self.lbl_our_ap = self.make_label(
            f"Our AP: {self.our_ssid} ({self.our_bssid})", "gp-dim"
        )
        info_bar.pack_start(self.lbl_our_ap, False, False, 0)

        self.lbl_scan_time = self.make_label("Last scan: never", "gp-dim")
        info_bar.pack_end(self.lbl_scan_time, False, False, 0)

        # T-0036: clickable label that surfaces what the headless gp-sonar-detect
        # helper has been finding while Sonar's window was closed. The Label
        # itself doesn't take clicks — wrap in EventBox.
        self.lbl_bg_status = self.make_label(
            "Background scan: never", "gp-dim"
        )
        bg_evbox = Gtk.EventBox()
        bg_evbox.add(self.lbl_bg_status)
        bg_evbox.connect("button-press-event", self._on_bg_status_clicked)
        bg_evbox.set_tooltip_text(
            "Click to view findings from the background helper "
            "(runs every ~5 min while Sonar is closed)."
        )
        info_bar.pack_end(bg_evbox, False, False, 0)

        root.pack_start(info_bar, False, False, 0)

        # Main content: paned with AP list on left, detail on right
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(520)

        # Left: AP list
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)

        scrolled_list = self.make_scrolled(self.listbox)
        scrolled_list.set_min_content_width(400)
        paned.pack1(scrolled_list, True, False)

        # Right: detail panel
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.detail_box.get_style_context().add_class("gp-sidebar")
        self.detail_box.set_margin_start(0)

        self.lbl_detail_placeholder = self.make_label(
            "Select an AP to view details", "gp-dim"
        )
        self.lbl_detail_placeholder.set_halign(Gtk.Align.CENTER)
        self.lbl_detail_placeholder.set_valign(Gtk.Align.CENTER)
        self.detail_box.pack_start(self.lbl_detail_placeholder, True, True, 20)

        scrolled_detail = self.make_scrolled(self.detail_box)
        scrolled_detail.set_min_content_width(250)
        paned.pack2(scrolled_detail, False, True)

        # T-0165: Wrap the main content in a Notebook so we can add the Probe
        # Captures page alongside the existing AP-list view. Bottom button bar
        # stays global below the notebook — Trust/Untrust/Snapshot/etc. only
        # make sense for an AP selection but they grey out otherwise so leaving
        # them visible while on Probe Captures tab is fine.
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(False)

        networks_label = Gtk.Label(label="Networks")
        self.notebook.append_page(paned, networks_label)

        probe_page = self._build_probe_captures_page()
        probe_label = Gtk.Label(label="Probe Captures")
        self.notebook.append_page(probe_page, probe_label)

        # Refresh probe list whenever the tab is shown — saves a poll cycle's
        # worth of latency for the most common case (user just clicked the tab).
        self.notebook.connect("switch-page", self._on_notebook_switch_page)

        root.pack_start(self.notebook, True, True, 0)

        # Bottom button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(8)
        btn_bar.set_margin_end(8)
        btn_bar.set_margin_top(4)
        btn_bar.set_margin_bottom(4)

        btn_export = self.make_button("Export Results", self._on_export, "gp-btn")
        btn_bar.pack_end(btn_export, False, False, 0)

        # T-0037: full session evidence report (AP list + Karma rigs + IE
        # baseline + signature DB hash + cross-session history). HTML default.
        btn_report = self.make_button("Full Session Report", self._on_export_full_session, "gp-btn")
        btn_bar.pack_end(btn_report, False, False, 0)

        btn_trust = self.make_button("Trust Selected", self._on_trust, "gp-btn")
        btn_bar.pack_end(btn_trust, False, False, 0)

        btn_untrust = self.make_button("Untrust Selected", self._on_untrust, "gp-btn-danger")
        btn_bar.pack_end(btn_untrust, False, False, 0)

        # T-0021 item 3: per-session ignore. User can silence a known-but-
        # noisy AP (the cafe's STRONG SIGNAL beacon when they're sitting near
        # it) without going through Trust — Trust would baseline a foreign
        # AP's IE fingerprint, which is overkill and exposes them to a
        # cafe-twin attack later. Ignore expires on app restart by design.
        btn_ignore = self.make_button("Ignore (this session)", self._on_ignore_session, "gp-btn")
        btn_bar.pack_end(btn_ignore, False, False, 0)

        # IE-fingerprint baseline — explicit click, never auto-snapped, so a
        # spoofed beacon at first launch can't silently become the trust anchor.
        btn_snapshot = self.make_button("Snapshot My AP", self._on_snapshot_my_ap, "gp-btn-primary")
        btn_bar.pack_end(btn_snapshot, False, False, 0)

        btn_bar.pack_start(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)

        # T-0059 — probe-request capture toggle. Gear-prefix label signals
        # "settings", not "action button" so it doesn't compete visually with
        # Trust/Untrust/Snapshot/Export. Opens the redesigned tier-card dialog.
        btn_probe = self.make_button("⚙ Probe Capture", self._on_probe_capture_settings, "gp-btn")
        btn_probe.set_tooltip_text("Configure Wi-Fi probe-request capture for Pineapple / Karma detection")
        btn_bar.pack_start(btn_probe, False, False, 0)

        root.pack_start(btn_bar, False, False, 0)

        # Status bar
        root.pack_start(self.make_status_bar("Ready -- press SCAN to begin"), False, False, 0)

    # ── Scanning ─────────────────────────────────────────────────────

    def _on_scan(self, btn):
        if self.scanning:
            return
        self.scanning = True
        self.btn_scan.set_sensitive(False)
        self.btn_scan.set_label("SCANNING...")
        self.spinner.start()
        self.set_status("Scanning nearby access points...")
        try:
            self.run_async(self._do_scan, self._on_scan_done)
        except Exception:
            # run_async raised synchronously; restore UI so the button isn't
            # permanently dead and the user can retry.
            self.scanning = False
            self.btn_scan.set_sensitive(True)
            self.btn_scan.set_label("SCAN")
            self.spinner.stop()
            raise

    def _scan_with_retry(self, cmd):
        """Run an iw scan command with one busy-retry. Shared by passive and
        active scan paths; keeps the timeout/retry policy in one place."""
        stdout, stderr, rc = self.run_sudo(cmd, timeout=45)
        if rc != 0 and "busy" in stderr.lower():
            time.sleep(2)
            stdout, stderr, rc = self.run_sudo(cmd, timeout=45)
        return stdout, stderr, rc

    def _run_passive_scan_dual_band(self):
        """T-0027: passive scan covering 2.4GHz + 5GHz with graceful fallback.

        Some chipsets refuse to leave the AP channel for a freq-list scan and
        return rc!=0 immediately. In that case we drop the freq list and let
        iw scan whatever the driver feels like — same behavior as pre-T-0027,
        so dual-band is a strict upgrade where the driver supports it.
        """
        dual = (
            ["iw", "dev", "wlan0", "scan", "passive", "freq"]
            + [str(f) for f in SCAN_FREQS_ALL]
        )
        stdout, stderr, rc = self._scan_with_retry(dual)
        if rc == 0:
            return stdout, stderr, rc
        # Fallback: driver refused explicit freq list — single-band scan.
        return self._scan_with_retry(["iw", "dev", "wlan0", "scan", "passive"])

    def _do_scan(self):
        """Background: run a passive iw scan.

        `passive` makes iw listen for beacons rather than broadcasting probe
        requests. Slower (must wait for each channel's beacon interval) but
        keeps Sonar invisible to other networks during the scan — matches the
        privacy claim in HELP_SECTIONS.
        """
        return self._run_passive_scan_dual_band()

    def _on_scan_done(self, result):
        """Main thread: process scan results."""
        self.scanning = False
        self.btn_scan.set_sensitive(True)
        self.btn_scan.set_label("SCAN")
        self.spinner.stop()

        if isinstance(result, Exception):
            self.set_status(f"Scan error: {result}")
            return

        stdout, stderr, rc = result
        if rc != 0 and not stdout:
            self.set_status(f"Scan failed: {stderr[:80]}")
            return

        self._reload_trusted()
        self.aps = parse_iw_scan(
            stdout, self.our_ssid, self.our_bssid, self.trusted, self.signatures
        )
        self.last_scan_time = time.strftime("%H:%M:%S")
        self.lbl_scan_time.set_text(f"Last scan: {self.last_scan_time}")

        # Apply persistent Karma findings to the fresh scan results so a rig
        # we detected earlier in the session keeps its KARMA RIG label even
        # in subsequent passive scans.
        self._apply_karma_overrides()
        # Push detections into the cross-app event bus for correlation.
        self._emit_events_for_aps()
        # T-0040: feed observations to the behavioral-anomaly module and
        # emit anomaly events for any AP scoring above threshold. Stash the
        # score map so _show_detail can display it for the selected AP.
        self._update_anomaly_scores()
        self._rebuild_list()

        dangers = sum(1 for a in self.aps if a["threat"] == "dangerous")
        suspicious = sum(1 for a in self.aps if a["threat"] == "suspicious")
        warnings = sum(1 for a in self.aps if a["threat"] == "warning")
        safe = sum(1 for a in self.aps if a["threat"] == "safe")
        self.set_status(
            f"Found {len(self.aps)} APs | "
            f"{dangers} dangerous | {suspicious} suspicious | "
            f"{warnings} warnings | {safe} safe"
        )
        gp_sonar_history.record(self.aps, self.our_ssid, self.our_bssid)

    # ── Karma Hunt ───────────────────────────────────────────────────

    def _apply_karma_overrides(self):
        """Stamp KARMA RIG threat onto APs that Hunt Karma already flagged."""
        for ap in self.aps:
            if ap["bssid"] in self.karma_rigs:
                ap["threat"] = "dangerous"
                ap["threat_label"] = "KARMA RIG"

    def _emit_events_for_aps(self):
        """Emit cross-app bus events for any new threat-worthy detections.

        Dedupe on (category, bssid) per-session so a sustained condition
        doesn't spam the bus on every passive scan. New BSSIDs / categories
        always emit fresh; the correlation engine sees them.
        """
        for ap in self.aps:
            label = ap.get("threat_label", "")
            bssid = ap.get("bssid", "")
            if not bssid:
                continue
            # T-0021 item 3: ignored APs skip the bus emit — chronically
            # ignored cafe APs would otherwise hammer the correlation
            # engine each scan with the same SPOOFED-IE-but-actually-fine
            # signal.
            if bssid.lower() in self.ignored_bssids:
                continue
            cat = None
            sev = gp_events.SEVERITY_DANGEROUS
            summary = ""
            if label == "SPOOFED IE":
                cat = "spoofed_ie"
                summary = f"Sonar: AP {bssid[:8]}... fingerprint mismatch (possible cloned-MAC twin)"
            elif label == "EVIL TWIN":
                cat = "evil_twin"
                summary = f"Sonar: AP {bssid[:8]}... claims our SSID '{ap.get('ssid','')}'"
            elif label.startswith("ATTACK TOOLKIT"):
                cat = "attack_toolkit"
                sig = ap.get("signature_match") or {}
                summary = f"Sonar: {sig.get('name','attack toolkit')} signature on {bssid[:8]}..."
            if cat is None:
                continue
            key = (cat, bssid)
            if key in self._emitted_events:
                continue
            self._emitted_events[key] = True
            while len(self._emitted_events) > MAX_EMITTED_EVENTS:
                self._emitted_events.popitem(last=False)
            gp_events.emit(
                "sonar", cat, sev, summary,
                details={
                    "bssid": bssid,
                    "ssid": ap.get("ssid", ""),
                    "signal": ap.get("signal"),
                    "encryption": ap.get("encryption"),
                },
            )
            # T-0021 item 5: pop a critical desktop notification regardless
            # of Sonar window focus. Dedup is shared with the bus emit above
            # — _emitted_events guards both.
            self._notify_threat(label, summary)

    # T-0040: behavioral anomaly scoring. The anomaly module wants observations
    # in {bssid, ssid, rssi, channel, ie_hash, beacon_interval} shape — Sonar's
    # parse_iw_scan dict uses signal/ie_fingerprint, so map field names. We
    # don't have beacon_interval in the parser yet — left as None; the score
    # function ignores it.
    def _aps_to_anomaly_obs(self):
        out = []
        for ap in self.aps:
            bssid = (ap.get("bssid") or "").lower().strip()
            if not bssid:
                continue
            out.append({
                "bssid": bssid,
                "ssid": ap.get("ssid", ""),
                "rssi": ap.get("signal"),
                "channel": ap.get("channel"),
                "ie_hash": ap.get("ie_fingerprint") or None,
            })
        return out

    def _update_anomaly_scores(self):
        """Feed scan into baseline + emit anomaly events. Stash score map for
        the detail panel. Failures are non-fatal — anomaly is a soft signal."""
        try:
            obs = self._aps_to_anomaly_obs()
            if not obs:
                self.anomaly_scores = {}
                return
            gp_sonar_anomaly.update_baseline(obs)
            # score_scan returns {bssid: (score, learning, evidence)}
            self.anomaly_scores = gp_sonar_anomaly.score_scan(obs)
            gp_sonar_anomaly.emit_anomalies(obs)
        except Exception as e:
            sys.stderr.write(f"[sonar] anomaly scoring failed: {e}\n")
            self.anomaly_scores = {}

    def _on_hunt_karma(self, _btn):
        if self.scanning or self.scanning_karma:
            return
        self.scanning_karma = True
        self.btn_hunt.set_sensitive(False)
        self.btn_hunt.set_label("HUNTING...")
        self.btn_scan.set_sensitive(False)
        self.spinner.start()
        self.set_status(
            "Hunting Karma rigs — sending random decoy + wordlist probes "
            "(passive baseline + active probe, ~10s)..."
        )
        try:
            self.run_async(self._do_hunt_karma, self._on_hunt_karma_done)
        except Exception:
            self.scanning_karma = False
            self.btn_hunt.set_sensitive(True)
            self.btn_hunt.set_label("HUNT KARMA")
            self.btn_scan.set_sensitive(True)
            self.spinner.stop()
            raise

    def _do_hunt_karma(self):
        """Run a passive baseline scan + a directed probe scan with random decoys
        and a wordlist of commonly-targeted SSIDs. Returns parsed BSSID->SSID
        maps for both, plus the decoys we used.

        Both scans go dual-band (T-0027) — a Karma rig on 2.4GHz when wlan0
        is AP'ing on 5GHz was previously invisible. Falls back to single-band
        if the driver refuses an explicit freq list (same behavior as before).
        """
        # Pass 1: passive baseline — what each BSSID actually beacons
        pb_out, pb_err, pb_rc = self._run_passive_scan_dual_band()

        # Pass 2: directed scan — broadcast probes for randoms + wordlist in
        # a single iw call so all responses land in one scan window.
        decoys = [
            f"{KARMA_RANDOM_PREFIX}{secrets.token_hex(4)}"
            for _ in range(KARMA_RANDOM_COUNT)
        ]
        # T-0027: build the active probe across both bands. iw syntax allows
        # `freq <list>` and `ssid <list>` in any order; the kernel sends each
        # probe on each listed frequency.
        dual_cmd = ["iw", "dev", "wlan0", "scan", "freq"] + [str(f) for f in SCAN_FREQS_ALL]
        for s in decoys:
            dual_cmd.extend(["ssid", s])
        for s in KARMA_WORDLIST:
            dual_cmd.extend(["ssid", s])
        dp_out, dp_err, dp_rc = self._scan_with_retry(dual_cmd)
        if dp_rc != 0:
            # Fallback: drop the freq list, let iw scan whatever band it can.
            single_cmd = ["iw", "dev", "wlan0", "scan"]
            for s in decoys:
                single_cmd.extend(["ssid", s])
            for s in KARMA_WORDLIST:
                single_cmd.extend(["ssid", s])
            dp_out, dp_err, dp_rc = self._scan_with_retry(single_cmd)

        return (pb_out, pb_rc, dp_out, dp_rc, decoys)

    def _on_hunt_karma_done(self, result):
        self.scanning_karma = False
        self.btn_hunt.set_sensitive(True)
        self.btn_hunt.set_label("HUNT KARMA")
        self.btn_scan.set_sensitive(True)
        self.spinner.stop()

        if isinstance(result, Exception):
            self.set_status(f"Hunt error: {result}")
            return
        pb_out, pb_rc, dp_out, dp_rc, decoys = result
        if dp_rc != 0 and not dp_out:
            self.set_status("Hunt failed: directed probe rejected by driver")
            return

        decoy_set = set(decoys)
        wordlist_set = set(KARMA_WORDLIST)

        # Parse both scans into bssid -> ssid maps. We intentionally pass empty
        # trusted/signatures — we only care about (bssid, ssid) pairs here.
        baseline = {}
        for ap in parse_iw_scan(pb_out, "", "", [], []):
            if ap["ssid"]:
                baseline[ap["bssid"]] = (ap["ssid"], ap.get("signal", -100))

        directed = {}
        for ap in parse_iw_scan(dp_out, "", "", [], []):
            if ap["ssid"]:
                directed[ap["bssid"]] = (ap["ssid"], ap.get("signal", -100))

        new_findings = 0
        for bssid, (dp_ssid, dp_signal) in directed.items():
            # Don't flag our own AP no matter what — saves us from ever
            # accidentally classifying ourselves as a Karma rig.
            if bssid == self.our_bssid:
                continue

            beacon_ssid, beacon_signal = baseline.get(bssid, ("", dp_signal))
            real_ssid = beacon_ssid or "(not seen in beacon)"
            reason = None

            if dp_ssid in decoy_set:
                # Random-decoy match — zero false-positive (decoy is a unique
                # random hex string no real network would advertise)
                reason = f"responded to random decoy probe '{dp_ssid}'"
            elif dp_ssid in wordlist_set:
                # Wordlist probe response — only suspicious if the BSSID's
                # real beacon SSID differs (a legit Starbucks AP beacons
                # 'Starbucks WiFi' AND probe-responds with the same — no
                # mismatch). A Karma rig beacons something else but claims
                # the wordlist name when probed.
                if beacon_ssid and beacon_ssid != dp_ssid:
                    reason = (
                        f"beacons '{beacon_ssid}' but probe-responded as '{dp_ssid}' "
                        f"(identity mismatch — Karma signature)"
                    )

            if reason and bssid not in self.karma_rigs:
                self.karma_rigs[bssid] = {
                    "reason": reason,
                    "real_ssid": real_ssid,
                    "decoy_ssid": dp_ssid,
                    "signal": dp_signal,
                    "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                while len(self.karma_rigs) > MAX_KARMA_RIGS:
                    self.karma_rigs.popitem(last=False)
                new_findings += 1
                # Push to event bus — Karma is a high-priority signal that
                # the correlation engine will combine with firewall pressure
                # or ARP changes to detect coordinated attacks.
                karma_summary = (
                    f"Sonar: Karma rig at {bssid[:8]}... (real ssid: {real_ssid})"
                )
                gp_events.emit(
                    "sonar", "karma_rig", gp_events.SEVERITY_DANGEROUS,
                    karma_summary,
                    details={
                        "bssid": bssid,
                        "real_ssid": real_ssid,
                        "decoy_ssid": dp_ssid,
                        "signal": dp_signal,
                        "reason": reason,
                    },
                )
                # T-0021 item 5: notify-send for Karma rig. new_findings is
                # already the per-hunt dedup gate; if we're here, this is
                # the first time this BSSID was flagged as a Karma rig.
                self._notify_threat("KARMA RIG", karma_summary)
                # T-0034: persist this encounter to disk so the customer has
                # forensic memory across sessions. Failures are swallowed by
                # the helper — never block the GUI on disk I/O.
                gp_sonar_encounters.append_encounter(
                    bssid=bssid,
                    decoy_ssid=dp_ssid,
                    real_ssid=real_ssid,
                    reason=reason,
                    signal_dbm=dp_signal,
                    our_ssid=self.our_ssid,
                    our_bssid=self.our_bssid,
                )

        # Rebuild the visible list using whichever scan baseline we now have.
        # If self.aps is empty (user never hit SCAN), seed it from the passive
        # baseline so the new KARMA RIG entries actually show up.
        if not self.aps:
            self._reload_trusted()
            self.aps = parse_iw_scan(
                pb_out, self.our_ssid, self.our_bssid, self.trusted, self.signatures
            )
            self.last_scan_time = time.strftime("%H:%M:%S")
            self.lbl_scan_time.set_text(f"Last scan: {self.last_scan_time}")

        self._apply_karma_overrides()
        # T-0040: feed the karma-baseline scan into the anomaly module too.
        # No-op when self.aps came from a prior _on_scan_done (already scored).
        self._update_anomaly_scores()
        self._rebuild_list()

        total = len(self.karma_rigs)
        if new_findings == 0 and total == 0:
            self.set_status(
                "Hunt clean — no Karma rigs detected. "
                "Note: target-list-restricted rigs may not respond to our probes."
            )
        elif new_findings == 0:
            self.set_status(f"Hunt complete — no new rigs ({total} previously flagged still active)")
        else:
            self.set_status(
                f"Hunt complete — {new_findings} new Karma rig(s) detected, "
                f"{total} total this session"
            )

    # ── List Rebuild ─────────────────────────────────────────────────

    def _rebuild_list(self):
        """Rebuild the ListBox from self.aps."""
        # Clear
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        for i, ap in enumerate(self.aps):
            row = Gtk.ListBoxRow()
            row.ap_index = i
            card = self._make_ap_card(ap)
            row.add(card)
            self.listbox.add(row)

        self.listbox.show_all()

    def _make_ap_card(self, ap):
        """Create a card widget for a single AP."""
        threat = ap["threat"]
        # T-0021 item 3: ignored AP overrides every other tier — render
        # neutral and skip the threat badge below.
        is_ignored = ap.get("bssid", "").lower() in self.ignored_bssids
        # T-0021 item 2: 4-tier severity tier (dangerous / suspicious / warning / safe).
        # suspicious now distinct from warning (was: both → gp-card-warning).
        if is_ignored:
            css_class = "gp-card-ignored"
        elif threat == "dangerous":
            css_class = "gp-card-danger"
        elif threat == "suspicious":
            css_class = "gp-card-suspicious"
        elif threat == "warning":
            css_class = "gp-card-warning"
        elif ap["is_ours"]:
            css_class = "gp-card-info"
        else:
            css_class = "gp-card"

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.get_style_context().add_class(css_class)

        # Row 1: SSID + threat badge
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        ssid_display = ap["ssid"] if ap["ssid"] else "(Hidden)"
        if ap["is_ours"]:
            ssid_display = f"{ssid_display}  [OUR AP]"
        lbl_ssid = self.make_label(ssid_display, "gp-accent")
        row1.pack_start(lbl_ssid, True, True, 0)

        # Threat badge — ignored APs show "[IGNORED]" instead of severity tier.
        if is_ignored:
            lbl_threat = self.make_label("IGNORED (this session)", "gp-ignored")
        else:
            threat_css = {
                "dangerous": "gp-danger",
                "suspicious": "gp-warning-amber",
                "warning": "gp-warning",
                "safe": "gp-success",
            }.get(threat, "gp-dim")
            lbl_threat = self.make_label(ap["threat_label"], threat_css)
        row1.pack_end(lbl_threat, False, False, 0)

        card.pack_start(row1, False, False, 0)

        # Row 2: BSSID, Channel, Signal, Encryption
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        lbl_bssid = self.make_label(ap["bssid"], "gp-dim")
        row2.pack_start(lbl_bssid, False, False, 0)

        lbl_ch = self.make_label(f"CH {ap['channel']}", "gp-text")
        row2.pack_start(lbl_ch, False, False, 0)

        # Signal strength with color
        signal = ap["signal"]
        if signal >= -50:
            sig_css = "gp-success"
        elif signal >= -70:
            sig_css = "gp-accent"
        elif signal >= -80:
            sig_css = "gp-warning"
        else:
            sig_css = "gp-danger"
        lbl_sig = self.make_label(f"{signal:.0f} dBm", sig_css)
        row2.pack_start(lbl_sig, False, False, 0)

        # Encryption
        enc = ap["encryption"]
        enc_css = "gp-success" if enc in ("WPA3", "WPA2") else (
            "gp-warning" if enc == "WPA" else (
                "gp-danger" if enc in ("WEP", "Open") else "gp-dim"
            )
        )
        lbl_enc = self.make_label(enc, enc_css)
        row2.pack_start(lbl_enc, False, False, 0)

        card.pack_start(row2, False, False, 0)

        return card

    # ── Detail Panel ─────────────────────────────────────────────────

    def _on_row_selected(self, listbox, row):
        if row is None:
            self.selected_ap = None
            return

        idx = row.ap_index
        if idx >= len(self.aps):
            return

        self.selected_ap = self.aps[idx]
        self._show_detail(self.selected_ap)

    def _show_detail(self, ap):
        """Show detailed info for selected AP in right panel."""
        for child in self.detail_box.get_children():
            self.detail_box.remove(child)

        # Title
        ssid = ap["ssid"] if ap["ssid"] else "(Hidden Network)"
        lbl_title = self.make_label(ssid, "gp-accent")
        lbl_title.override_font(Pango.FontDescription("monospace bold 14"))
        self.detail_box.pack_start(lbl_title, False, False, 4)

        sep = Gtk.Separator()
        self.detail_box.pack_start(sep, False, False, 4)

        # T-0040: anomaly score for this AP (0.0 if unscored / learning).
        # Module returns (score, is_learning, evidence) tuples.
        bssid_lc = (ap.get("bssid") or "").lower().strip()
        score_tuple = self.anomaly_scores.get(bssid_lc)
        if score_tuple is None:
            anomaly_text = "—"
        else:
            score_val, is_learning, _ = score_tuple
            if is_learning:
                anomaly_text = "learning"
            else:
                anomaly_text = f"{score_val:.2f}"

        # Detail fields
        fields = [
            ("BSSID", ap["bssid"]),
            ("Channel", str(ap["channel"])),
            ("Frequency", f"{ap['freq']} MHz"),
            ("Signal", f"{ap['signal']:.0f} dBm"),
            ("Encryption", ap["encryption"]),
            ("Anomaly", anomaly_text),
            ("Threat", ap["threat_label"]),
        ]

        for label_text, value_text in fields:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl_key = self.make_label(f"{label_text}:", "gp-dim")
            lbl_key.set_size_request(90, -1)
            row.pack_start(lbl_key, False, False, 0)

            # Color the value based on content
            val_css = "gp-text"
            if label_text == "Threat":
                # ATTACK TOOLKIT labels carry the toolkit name appended, so
                # match by prefix rather than exact.
                if value_text.startswith("ATTACK TOOLKIT"):
                    # Severity carried in ap["threat"], not the label string —
                    # but for the detail panel a danger-class red is right for
                    # all but warning-severity sigs.
                    val_css = "gp-warning" if "FREE PUBLIC" in value_text else "gp-danger"
                else:
                    val_css = {
                        "EVIL TWIN": "gp-danger",
                        "SPOOFED IE": "gp-danger",
                        "KARMA RIG": "gp-danger",
                        "STRONG SIGNAL": "gp-warning",
                        "OPEN": "gp-warning",
                        "WEAK (WEP)": "gp-warning",
                        "SAFE": "gp-success",
                        "TRUSTED": "gp-success",
                        "OUR AP": "gp-accent",
                    }.get(value_text, "gp-text")
            elif label_text == "Encryption":
                val_css = "gp-success" if value_text in ("WPA3", "WPA2") else (
                    "gp-danger" if value_text in ("Open", "WEP") else "gp-text"
                )
            elif label_text == "Anomaly":
                # T-0040: above DEFAULT_THRESHOLD = drift, dim while learning.
                if value_text == "learning" or value_text == "—":
                    val_css = "gp-dim"
                else:
                    try:
                        sval = float(value_text)
                    except ValueError:
                        sval = 0.0
                    if sval >= gp_sonar_anomaly.DEFAULT_THRESHOLD:
                        val_css = "gp-warning-amber"
                    elif sval > 0:
                        val_css = "gp-text"
                    else:
                        val_css = "gp-success"

            lbl_val = self.make_label(value_text, val_css)
            row.pack_start(lbl_val, False, False, 0)
            self.detail_box.pack_start(row, False, False, 2)

        # Signal strength bar (visual)
        sep2 = Gtk.Separator()
        self.detail_box.pack_start(sep2, False, False, 4)

        lbl_sig_title = self.make_label("SIGNAL STRENGTH", "gp-dim")
        self.detail_box.pack_start(lbl_sig_title, False, False, 2)

        sig_bar = self._make_signal_bar(ap["signal"])
        self.detail_box.pack_start(sig_bar, False, False, 2)

        # Threat explanation
        if ap["threat"] != "safe" or ap["is_ours"]:
            sep3 = Gtk.Separator()
            self.detail_box.pack_start(sep3, False, False, 4)

            lbl_expl_title = self.make_label("ASSESSMENT", "gp-dim")
            self.detail_box.pack_start(lbl_expl_title, False, False, 2)

            explanation = self._get_threat_explanation(ap)
            lbl_expl = self.make_label(explanation, "gp-text")
            lbl_expl.set_line_wrap(True)
            lbl_expl.set_max_width_chars(30)
            self.detail_box.pack_start(lbl_expl, False, False, 2)

        # Trust status
        is_trusted = ap["bssid"] in self.trusted_bssids
        sep4 = Gtk.Separator()
        self.detail_box.pack_start(sep4, False, False, 4)
        trust_text = "TRUSTED" if is_trusted else "UNTRUSTED"
        trust_css = "gp-success" if is_trusted else "gp-dim"
        lbl_trust = self.make_label(f"Status: {trust_text}", trust_css)
        self.detail_box.pack_start(lbl_trust, False, False, 2)

        # T-0046: rogue-block buttons. Strict gate: only confirmed-attack
        # labels (EVIL TWIN or ATTACK TOOLKIT). NOT shown for SPOOFED IE
        # (could be a legitimate beacon drift) or KARMA RIG (different
        # response path). Hidden entirely if Phase A wrapper isn't installed.
        threat_label = ap.get("threat_label", "")
        is_confirmed_attack = (
            threat_label == "EVIL TWIN"
            or threat_label.startswith("ATTACK TOOLKIT")
        )
        if is_confirmed_attack and self._rogue_block_available():
            sep5 = Gtk.Separator()
            self.detail_box.pack_start(sep5, False, False, 4)
            lbl_rb = self.make_label("ROGUE-BLOCK (Pi-hole)", "gp-dim")
            self.detail_box.pack_start(lbl_rb, False, False, 2)

            blocks = self._read_rogue_blocks()
            is_armed = ap.get("bssid", "").lower() in blocks
            if is_armed:
                btn = self.make_button(
                    "Release rogue-block",
                    self._on_release_rogue_block,
                    "gp-btn",
                )
                hint = self.make_label(
                    "Captive-portal probes blocked network-wide for this AP. "
                    "Auto-expires in 24h.",
                    "gp-warning-amber",
                )
            else:
                btn = self.make_button(
                    "Arm rogue-block",
                    self._on_arm_rogue_block,
                    "gp-btn-warning",
                )
                hint = self.make_label(
                    "Inject NXDOMAIN entries for captive-portal domains so a "
                    "device that connects to this rogue surfaces a visible "
                    "'no internet' warning. Network-wide effect — read the "
                    "confirmation dialog.",
                    "gp-dim",
                )
            hint.set_line_wrap(True)
            hint.set_max_width_chars(34)
            self.detail_box.pack_start(btn, False, False, 2)
            self.detail_box.pack_start(hint, False, False, 2)

        # WiGLE history (T-0030). Manual lookup; gated on auth file presence.
        self._build_wigle_section(ap)

        self.detail_box.show_all()

    def _build_wigle_section(self, ap):
        """Add WiGLE lookup widgets to the detail panel.

        If the WiGLE auth file isn't configured, render a quiet hint and stop.
        Otherwise render a 'Look up on WiGLE' button that fires an async
        lookup and appends the result lines on completion.
        """
        sep = Gtk.Separator()
        self.detail_box.pack_start(sep, False, False, 4)

        title = self.make_label("WiGLE HISTORY", "gp-dim")
        self.detail_box.pack_start(title, False, False, 2)

        if not gp_sonar_wigle.wigle_available():
            hint = self.make_label(
                "Not configured (drop API key in /etc/phantom/wigle-auth.json)",
                "gp-dim",
            )
            hint.set_line_wrap(True)
            hint.set_max_width_chars(36)
            self.detail_box.pack_start(hint, False, False, 2)
            return

        result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.detail_box.pack_start(result_box, False, False, 2)

        bssid = ap.get("bssid", "")

        def _render(result):
            for child in result_box.get_children():
                result_box.remove(child)
            for label_text, value_text, css in gp_sonar_wigle.render_lines(result):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                lbl_key = self.make_label(f"{label_text}:", "gp-dim")
                lbl_key.set_size_request(90, -1)
                row.pack_start(lbl_key, False, False, 0)
                lbl_val = self.make_label(value_text, css)
                lbl_val.set_line_wrap(True)
                lbl_val.set_max_width_chars(28)
                row.pack_start(lbl_val, False, False, 0)
                result_box.pack_start(row, False, False, 0)
            result_box.show_all()

        def _on_click(btn):
            target_bssid = bssid
            btn.set_sensitive(False)
            btn.set_label("Looking up...")

            def _job():
                return gp_sonar_wigle.wigle_lookup(target_bssid)

            def _cb(result):
                # Stale-callback guard: ignore if user navigated to another AP.
                still_selected = (
                    self.selected_ap is not None
                    and self.selected_ap.get("bssid", "") == target_bssid
                )
                if still_selected:
                    _render(result)
                btn.set_sensitive(True)
                btn.set_label("Look up on WiGLE")

            self.run_async(_job, _cb)

        btn = self.make_button("Look up on WiGLE", _on_click, "gp-btn")
        self.detail_box.pack_start(btn, False, False, 4)

    def _make_signal_bar(self, signal):
        """Create a text-based signal strength indicator."""
        # Map signal to 0-5 bars
        # -30 dBm = excellent, -90 dBm = terrible
        bars = max(0, min(5, int((signal + 90) / 12)))
        bar_text = "|" * bars + "." * (5 - bars)

        if signal >= -50:
            quality = "Excellent"
            css = "gp-success"
        elif signal >= -60:
            quality = "Good"
            css = "gp-success"
        elif signal >= -70:
            quality = "Fair"
            css = "gp-accent"
        elif signal >= -80:
            quality = "Weak"
            css = "gp-warning"
        else:
            quality = "Very Weak"
            css = "gp-danger"

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_bar = self.make_label(f"[{bar_text}]", css)
        lbl_bar.override_font(Pango.FontDescription("monospace bold 14"))
        box.pack_start(lbl_bar, False, False, 0)

        lbl_q = self.make_label(quality, css)
        box.pack_start(lbl_q, False, False, 0)

        return box

    def _get_threat_explanation(self, ap):
        """Return a human-readable threat explanation."""
        t = ap["threat_label"]
        if t == "KARMA RIG":
            karma = self.karma_rigs.get(ap["bssid"], {})
            reason = karma.get("reason", "responded to a Karma decoy probe")
            real_ssid = karma.get("real_ssid", "(unknown)")
            return (
                f"CRITICAL: this BSSID is a Karma rig. {reason.capitalize()}.\n\n"
                f"A Karma rig is a WiFi attack tool (e.g. WiFi Pineapple) that "
                f"responds to probe requests for any SSID, luring devices that "
                f"have those networks in their saved-WiFi list into auto-connecting. "
                f"Once connected, the attacker can intercept all traffic.\n\n"
                f"This rig's real beacon SSID appears to be: {real_ssid}\n\n"
                f"Do NOT connect to this AP under any name. If signal is strong, "
                f"the operator is physically nearby — consider leaving the area."
            )
        sig = ap.get("signature_match")
        if t.startswith("ATTACK TOOLKIT") and sig:
            desc = sig.get("description", "")
            return (
                f"This AP's beacon matches a known signature for "
                f"{sig['name']} — an offensive WiFi tool used by penetration "
                f"testers and attackers to lure devices into connecting "
                f"through it. {desc}\n\n"
                f"If you didn't deploy this device yourself, treat it as "
                f"actively hostile: do not connect, and avoid the area if "
                f"the signal is strong (it indicates the operator is nearby)."
            )
        if t == "SPOOFED IE":
            return (
                "CRITICAL: this AP claims an SSID/BSSID we trust, but the "
                "Information Element fingerprint (capabilities, RSN block, "
                "country/regulatory data, supported rates) doesn't match the "
                "snapshot we took the first time we saw it. That signature "
                "is extremely hard to forge — a mismatch usually means a "
                "different physical AP is impersonating yours. Disconnect "
                "and investigate immediately."
            )
        if ap["is_ours"]:
            return "This is your GhostPort access point."
        if t == "EVIL TWIN":
            return (
                f"CRITICAL: This AP uses the same SSID as your network "
                f"({ap['ssid']}) but has a different BSSID. This is a "
                f"classic evil twin attack used to intercept traffic."
            )
        if t == "OPEN":
            return (
                "This network has no encryption. Any traffic sent over "
                "it can be intercepted by anyone nearby."
            )
        if t == "WEAK (WEP)":
            return (
                "This network uses WEP encryption which is broken and "
                "can be cracked in minutes."
            )
        if t == "STRONG SIGNAL":
            return (
                "This unknown AP has an unusually strong signal, which "
                "could indicate a nearby rogue access point attempting "
                "to lure devices."
            )
        if t == "TRUSTED":
            return "You have marked this AP as trusted."
        return "No known threats detected from this access point."

    # ── Trust / Untrust / Ignore / Snapshot ──────────────────────────

    # T-0021 item 3: per-session Ignore action. Trust baselines an IE
    # fingerprint and persists across launches; Ignore is in-memory only
    # and clears on restart. The right tool for "yes the cafe AP is
    # legit, hush for now" — wrong tool would be Trust, which would
    # accept the cafe's IE as authoritative and let a cafe-twin spoof it
    # later. Also suppresses the cross-app event-bus emit so a chronically
    # ignored AP doesn't hammer the correlation engine each scan.
    def _on_ignore_session(self, _btn):
        if not self.selected_ap:
            self.set_status("No AP selected — click an AP first")
            return
        bssid = (self.selected_ap.get("bssid") or "").lower()
        if not bssid:
            self.set_status("Selected AP has no BSSID — cannot ignore")
            return
        if self.selected_ap.get("is_ours"):
            self.set_status("Cannot Ignore your own AP — it's classified specially")
            return
        ssid = self.selected_ap.get("ssid", "") or "(Hidden)"
        if bssid in self.ignored_bssids:
            self.ignored_bssids.discard(bssid)
            self.set_status(f"Un-ignored {ssid} ({bssid[:8]}...)")
        else:
            self.ignored_bssids.add(bssid)
            self.set_status(
                f"Ignored {ssid} ({bssid[:8]}...) for this session — "
                f"clears on restart"
            )
        self._rebuild_list()
        self._show_detail(self.selected_ap)

    def _on_snapshot_my_ap(self, _btn):
        """Capture the current IE fingerprint of our own AP as the trust baseline.

        Explicit user action — never auto-fired — so a spoofed beacon present
        at first launch can't silently become the trust anchor (TOFU footgun).
        Shows a confirmation dialog with a clear environment-safety prompt.
        """
        if not self.our_bssid:
            self.set_status("Our AP BSSID is unknown — check hostapd config")
            return

        ap = next((a for a in self.aps if a["bssid"] == self.our_bssid), None)
        if ap is None:
            self.set_status("Our AP not seen in last scan — run a scan first")
            return
        if not ap.get("ie_fingerprint"):
            self.set_status("Our AP has no fingerprint to snapshot (rare)")
            return

        existing = _trusted_lookup(self.our_bssid, self.trusted)
        is_overwrite = existing is not None and existing.get("fingerprint") and existing["fingerprint"] != ap["ie_fingerprint"]

        body = (
            # T-0021 item 4: lead with the explicit safety check. Earlier
            # text said "trusted environment" — too abstract. Reword so the
            # user is forced to consciously verify they're seeing their
            # OWN AP's beacon, not an active spoofer's, before snapshotting.
            "BEFORE clicking OK, confirm you are connected RIGHT NOW to your "
            "real GhostPort access point — not to anything else, and not in a "
            "place where a spoofer might be broadcasting your SSID/BSSID.\n\n"
            "Snapshot YOUR AP's current beacon as the IE fingerprint baseline?\n\n"
            "From this moment on, any drift in the beacon's stable Information "
            "Elements (capability flags, RSN block, country/regulatory data, "
            "supported rates, HT/VHT/HE capabilities) will trigger a SPOOFED IE "
            "alert.\n\n"
            "Snapshot at HOME, with no other devices in range claiming to be "
            "your network. If you snapshot in a contested space (coffee shop, "
            "hotel, airport) and an attacker is broadcasting your SSID/BSSID "
            "stronger than your real router, you'll baseline THEM as legitimate "
            "and your real AP will start firing SPOOFED IE alerts forever.\n\n"
            f"Current beacon hash: {ap['ie_fingerprint'][:32]}...\n"
        )
        if is_overwrite:
            body += (
                f"\nWARNING: replacing existing baseline "
                f"({existing['fingerprint'][:32]}...). Only do this if your AP "
                f"legitimately changed (firmware update, country/region change, "
                f"new hardware) — not as a way to silence a SPOOFED IE alert "
                f"you don't trust.\n"
            )

        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=("Re-snapshot My AP?" if is_overwrite else "Snapshot My AP?"),
        )
        dialog.format_secondary_text(body)

        try:
            response = dialog.run()
        finally:
            dialog.destroy()

        if response != Gtk.ResponseType.OK:
            self.set_status("Snapshot cancelled")
            return

        prev_trusted = copy.deepcopy(self.trusted)
        prev_bssids = set(self.trusted_bssids)

        if existing is None:
            self.trusted.append({
                "bssid": self.our_bssid,
                "ssid": ap.get("ssid", self.our_ssid),
                "fingerprint": ap["ie_fingerprint"],
                "added": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            self.trusted_bssids.add(self.our_bssid)
        else:
            existing["fingerprint"] = ap["ie_fingerprint"]
            existing["ssid"] = ap.get("ssid", existing.get("ssid", ""))
            existing["added"] = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            save_trusted_aps(self.trusted)
        except OSError as e:
            self.trusted = prev_trusted
            self.trusted_bssids = prev_bssids
            self.set_status(f"Snapshot save FAILED (not persisted): {e}")
            self._notify_error("Sonar snapshot save failed", str(e))
            return

        # Re-classify and rebuild so any prior SPOOFED IE label clears
        for a in self.aps:
            a["threat"], a["threat_label"] = classify_ap(
                a, self.our_ssid, self.our_bssid, self.trusted
            )
        self._rebuild_list()
        self._show_detail(ap)
        self.set_status(f"Snapshot saved: {ap['ie_fingerprint'][:16]}...")

    def _on_trust(self, btn):
        if not self.selected_ap:
            self.set_status("No AP selected")
            return
        ap = self.selected_ap
        if ap["bssid"] in self.trusted_bssids:
            self.set_status(f"Already trusted: {ap['bssid']}")
            return
        prev_trusted = copy.deepcopy(self.trusted)
        prev_bssids = set(self.trusted_bssids)
        self.trusted.append({
            "bssid": ap["bssid"],
            "ssid": ap["ssid"],
            "fingerprint": ap.get("ie_fingerprint", ""),
            "added": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.trusted_bssids.add(ap["bssid"])
        try:
            save_trusted_aps(self.trusted)
        except OSError as e:
            self.trusted = prev_trusted
            self.trusted_bssids = prev_bssids
            self.set_status(f"Trust save FAILED (not persisted): {e}")
            self._notify_error("Sonar trust save failed", str(e))
            return
        # Re-classify and rebuild (pass full trusted list so fingerprint check fires)
        for a in self.aps:
            a["threat"], a["threat_label"] = classify_ap(
                a, self.our_ssid, self.our_bssid, self.trusted
            )
        self._rebuild_list()
        self._show_detail(ap)
        self.set_status(f"Trusted: {ap['ssid']} ({ap['bssid']})")

    def _on_untrust(self, btn):
        if not self.selected_ap:
            self.set_status("No AP selected")
            return
        ap = self.selected_ap
        if ap["bssid"] not in self.trusted_bssids:
            self.set_status(f"Not in trusted list: {ap['bssid']}")
            return
        prev_trusted = copy.deepcopy(self.trusted)
        prev_bssids = set(self.trusted_bssids)
        self.trusted = [t for t in self.trusted if t.get("bssid", "").lower() != ap["bssid"]]
        self.trusted_bssids.discard(ap["bssid"])
        try:
            save_trusted_aps(self.trusted)
        except OSError as e:
            self.trusted = prev_trusted
            self.trusted_bssids = prev_bssids
            self.set_status(f"Untrust save FAILED (not persisted): {e}")
            self._notify_error("Sonar untrust save failed", str(e))
            return
        for a in self.aps:
            a["threat"], a["threat_label"] = classify_ap(
                a, self.our_ssid, self.our_bssid, self.trusted
            )
        self._rebuild_list()
        self._show_detail(ap)
        self.set_status(f"Removed from trusted: {ap['ssid']} ({ap['bssid']})")

    # ── Export ───────────────────────────────────────────────────────

    def _on_export(self, btn):
        if not self.aps:
            self.set_status("No scan results to export")
            return

        dialog = Gtk.FileChooserDialog(
            title="Export Scan Results",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_current_name(f"sonar-{time.strftime('%Y%m%d-%H%M%S')}.json")

        filt = Gtk.FileFilter()
        filt.set_name("JSON files")
        filt.add_pattern("*.json")
        dialog.add_filter(filt)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            try:
                export_data = {
                    "scan_time": self.last_scan_time or "unknown",
                    "our_ssid": self.our_ssid,
                    "our_bssid": self.our_bssid,
                    "aps": self.aps,
                }
                with open(filepath, "w") as f:
                    json.dump(export_data, f, indent=2)
                self.set_status(f"Exported {len(self.aps)} APs to {os.path.basename(filepath)}")
            except Exception as e:
                self.set_status(f"Export failed: {e}")
        dialog.destroy()

    def _on_export_full_session(self, btn):
        """T-0037: bundle AP list + Karma rigs + IE baseline + signature DB into
        a self-contained evidence report. Format follows the chosen filename
        extension (.html → HTML, .json → JSON). HTML default for human readers.
        """
        documents_dir = os.path.expanduser("~/Documents")
        try:
            os.makedirs(documents_dir, exist_ok=True)
        except OSError:
            documents_dir = os.path.expanduser("~")

        dialog = Gtk.FileChooserDialog(
            title="Export Full Session Report",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_current_folder(documents_dir)
        dialog.set_current_name(f"sonar-session-{time.strftime('%Y%m%d-%H%M%S')}.html")
        for label, pat in (("HTML report", "*.html"), ("JSON report", "*.json")):
            filt = Gtk.FileFilter()
            filt.set_name(label)
            filt.add_pattern(pat)
            dialog.add_filter(filt)

        if dialog.run() != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        filepath = dialog.get_filename()
        dialog.destroy()
        if not filepath:
            return

        try:
            history = gp_sonar_encounters.read_encounters(limit=500)
        except OSError:
            history = []

        kwargs = dict(
            aps=self.aps,
            karma_rigs=self.karma_rigs,
            our_ssid=self.our_ssid,
            our_bssid=self.our_bssid,
            trusted=self.trusted,
            signatures=self.signatures,
            last_scan_time=self.last_scan_time,
            sonar_version="phantom-sonar",
            encounter_history=history,
        )

        try:
            if filepath.lower().endswith(".json"):
                payload = json.dumps(gp_sonar_report.build_json_report(**kwargs), indent=2)
            else:
                payload = gp_sonar_report.build_html_report(**kwargs)
            tmp = filepath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, filepath)
            self.set_status(
                f"Session report saved: {len(self.aps)} APs, "
                f"{len(self.karma_rigs)} Karma rig(s), {len(history)} historical → "
                f"{os.path.basename(filepath)}"
            )
        except OSError as e:
            self.set_status(f"Report export failed: {e}")
            self._notify_error("Sonar report export failed", str(e))


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = SonarApp()
    app.run()
