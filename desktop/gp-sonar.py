#!/usr/bin/env python3
"""gp-sonar — SONAR: Rogue AP / Evil Twin WiFi Scanner for Phantom OS"""
import sys, os, re, json, time, subprocess, hashlib, secrets
sys.path.insert(0, "/opt/phantom/desktop")
from gp_app_base import GhostPortApp
import gp_events

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

# ── Constants ─────────────────────────────────────────────────────────

TRUSTED_APS_FILE = os.path.expanduser("~/.config/phantom/trusted-aps.json")
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"

# Attack-toolkit signature database. User override > bundled default.
SIGNATURES_FILE_USER = "/etc/phantom/sonar-signatures.json"
SIGNATURES_FILE_BUNDLED = "/opt/phantom/desktop/sonar-signatures.json"

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


def get_our_ap():
    """Read our own AP SSID and BSSID from hostapd config and interface."""
    ssid = ""
    bssid = ""
    try:
        with open(HOSTAPD_CONF) as f:
            for line in f:
                if line.startswith("ssid="):
                    ssid = line.strip().split("=", 1)[1]
                elif line.startswith("interface="):
                    iface = line.strip().split("=", 1)[1]
        # Get BSSID from interface
        result = subprocess.run(
            ["ip", "link", "show", "wlan0"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "link/ether" in line:
                bssid = line.split()[1].lower()
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
    """Save trusted AP list to JSON file (always in the dict schema)."""
    try:
        os.makedirs(os.path.dirname(TRUSTED_APS_FILE), exist_ok=True)
        with open(TRUSTED_APS_FILE, "w") as f:
            json.dump(trusted, f, indent=2)
    except Exception:
        pass


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
    """Load the attack-toolkit signatures, preferring the user override."""
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
                return [s for s in sigs if s.get("id") and s.get("name")]
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        except Exception:
            continue
    return []


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
            }
            continue

        if current is None:
            continue

        # Collect every non-BSS line under the current BSS for fingerprinting.
        current["ie_lines"].append(line)

        if line_stripped.startswith("SSID:"):
            current["ssid"] = line_stripped[5:].strip()
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
        elif "RSN:" in line_stripped or "WPA:" in line_stripped:
            if "RSN:" in line_stripped:
                current["encryption"] = "WPA2"
                current["wpa_version"] = "RSN"
            elif "WPA:" in line_stripped:
                if current["encryption"] == "Open":
                    current["encryption"] = "WPA"
        elif "SAE" in line_stripped or "Group management" in line_stripped:
            if current["encryption"] in ("WPA2", "WPA"):
                current["encryption"] = "WPA3"
        elif "WEP" in line_stripped and "Privacy" in line_stripped:
            if current["encryption"] == "Open":
                current["encryption"] = "WEP"
        elif line_stripped.startswith("capability:"):
            if "Privacy" in line_stripped and current["encryption"] == "Open":
                current["encryption"] = "WEP"

    if current:
        aps.append(current)

    # Derive channel from freq if not set
    for ap in aps:
        if ap["channel"] == 0 and ap["freq"] > 0:
            ap["channel"] = freq_to_channel(ap["freq"])

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

    # Very strong signal from unknown AP (possible rogue)
    if ap["signal"] >= -40:
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

        ("Trust Selected / Untrust Selected / Snapshot My AP / Export Results",
         "Trust Selected — mark a neighbor's AP as known-good (also captures its "
         "current IE fingerprint as the baseline). Future scans won't flag it "
         "unless its IE blocks drift.\n\n"
         "Untrust Selected — remove the trusted flag and stored fingerprint. "
         "Next scan re-evaluates against all suspicion rules.\n\n"
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
         "     NETGEAR, ATT-WIFI, Free Public WiFi, AmazonConnect, Boingo,\n"
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
         "WiFi tools (Hak5 WiFi Pineapple / Mango / Tetra / Coconut, hostapd-"
         "mana, bettercap, etc.). When a scanned AP's beacon matches a "
         "signature, Sonar flags it with an ATTACK TOOLKIT: <name> label.\n\n"
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
    ]

    def _on_help(self, _btn):
        self.show_help_dialog(self.HELP_SECTIONS)


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
        self.karma_rigs = {}
        self.scanning_karma = False
        # Event-emit dedup: tracks (category, bssid) tuples we've already
        # written to the cross-app event bus this session, so a sustained
        # threat condition doesn't spam the bus on every passive scan.
        self._emitted_events = set()
        self.selected_ap = None
        self.last_scan_time = None

        # Load our AP info, trusted list, attack-toolkit signatures
        self.our_ssid, self.our_bssid = get_our_ap()
        self._reload_trusted()
        self.signatures = load_signatures()

        self.build_ui()

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

        root.pack_start(paned, True, True, 0)

        # Bottom button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_start(8)
        btn_bar.set_margin_end(8)
        btn_bar.set_margin_top(4)
        btn_bar.set_margin_bottom(4)

        btn_export = self.make_button("Export Results", self._on_export, "gp-btn")
        btn_bar.pack_end(btn_export, False, False, 0)

        btn_trust = self.make_button("Trust Selected", self._on_trust, "gp-btn")
        btn_bar.pack_end(btn_trust, False, False, 0)

        btn_untrust = self.make_button("Untrust Selected", self._on_untrust, "gp-btn-danger")
        btn_bar.pack_end(btn_untrust, False, False, 0)

        # IE-fingerprint baseline — explicit click, never auto-snapped, so a
        # spoofed beacon at first launch can't silently become the trust anchor.
        btn_snapshot = self.make_button("Snapshot My AP", self._on_snapshot_my_ap, "gp-btn-primary")
        btn_bar.pack_end(btn_snapshot, False, False, 0)

        btn_bar.pack_start(self.make_help_button(sections=self.HELP_SECTIONS), False, False, 0)

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
        self.run_async(self._do_scan, self._on_scan_done)

    def _do_scan(self):
        """Background: run a passive iw scan.

        `passive` makes iw listen for beacons rather than broadcasting probe
        requests. Slower (must wait for each channel's beacon interval) but
        keeps Sonar invisible to other networks during the scan — matches the
        privacy claim in HELP_SECTIONS.
        """
        stdout, stderr, rc = self.run_sudo(["iw", "dev", "wlan0", "scan", "passive"], timeout=45)
        if rc != 0 and "busy" in stderr.lower():
            # Interface busy, retry once
            time.sleep(2)
            stdout, stderr, rc = self.run_sudo(["iw", "dev", "wlan0", "scan", "passive"], timeout=45)
        return stdout, stderr, rc

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
            self._emitted_events.add(key)
            gp_events.emit(
                "sonar", cat, sev, summary,
                details={
                    "bssid": bssid,
                    "ssid": ap.get("ssid", ""),
                    "signal": ap.get("signal"),
                    "encryption": ap.get("encryption"),
                },
            )

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
        self.run_async(self._do_hunt_karma, self._on_hunt_karma_done)

    def _do_hunt_karma(self):
        """Run a passive baseline scan + a directed probe scan with random decoys
        and a wordlist of commonly-targeted SSIDs. Returns parsed BSSID->SSID
        maps for both, plus the decoys we used."""
        # Pass 1: passive baseline — what each BSSID actually beacons
        pb_out, pb_err, pb_rc = self.run_sudo(
            ["iw", "dev", "wlan0", "scan", "passive"], timeout=45
        )
        if pb_rc != 0 and "busy" in pb_err.lower():
            time.sleep(2)
            pb_out, pb_err, pb_rc = self.run_sudo(
                ["iw", "dev", "wlan0", "scan", "passive"], timeout=45
            )

        # Pass 2: directed scan — broadcast probes for randoms + wordlist in
        # a single iw call so all responses land in one scan window.
        decoys = [
            f"{KARMA_RANDOM_PREFIX}{secrets.token_hex(4)}"
            for _ in range(KARMA_RANDOM_COUNT)
        ]
        cmd = ["iw", "dev", "wlan0", "scan"]
        for s in decoys:
            cmd.extend(["ssid", s])
        for s in KARMA_WORDLIST:
            cmd.extend(["ssid", s])
        dp_out, dp_err, dp_rc = self.run_sudo(cmd, timeout=45)
        if dp_rc != 0 and "busy" in dp_err.lower():
            time.sleep(2)
            dp_out, dp_err, dp_rc = self.run_sudo(cmd, timeout=45)

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
                new_findings += 1
                # Push to event bus — Karma is a high-priority signal that
                # the correlation engine will combine with firewall pressure
                # or ARP changes to detect coordinated attacks.
                gp_events.emit(
                    "sonar", "karma_rig", gp_events.SEVERITY_DANGEROUS,
                    f"Sonar: Karma rig at {bssid[:8]}... (real ssid: {real_ssid})",
                    details={
                        "bssid": bssid,
                        "real_ssid": real_ssid,
                        "decoy_ssid": dp_ssid,
                        "signal": dp_signal,
                        "reason": reason,
                    },
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
        if threat == "dangerous":
            css_class = "gp-card-danger"
        elif threat in ("suspicious", "warning"):
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

        # Threat badge
        threat_css = {
            "dangerous": "gp-danger",
            "suspicious": "gp-warning",
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

        # Detail fields
        fields = [
            ("BSSID", ap["bssid"]),
            ("Channel", str(ap["channel"])),
            ("Frequency", f"{ap['freq']} MHz"),
            ("Signal", f"{ap['signal']:.0f} dBm"),
            ("Encryption", ap["encryption"]),
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

        self.detail_box.show_all()

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

    # ── Trust / Untrust / Snapshot ───────────────────────────────────

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
            "Snapshot YOUR AP's current beacon as the IE fingerprint baseline?\n\n"
            "From this moment on, any drift in the beacon's stable Information "
            "Elements (capability flags, RSN block, country/regulatory data, "
            "supported rates, HT/VHT/HE capabilities) will trigger a SPOOFED IE "
            "alert.\n\n"
            "ONLY snapshot in a trusted environment — typically at home, with no "
            "active spoofing. If you snapshot in a contested space (coffee shop, "
            "hotel, airport) and an attacker is broadcasting your SSID/BSSID right "
            "now, you'll baseline THEM as legitimate.\n\n"
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

        save_trusted_aps(self.trusted)

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
        self.trusted.append({
            "bssid": ap["bssid"],
            "ssid": ap["ssid"],
            "fingerprint": ap.get("ie_fingerprint", ""),
            "added": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.trusted_bssids.add(ap["bssid"])
        save_trusted_aps(self.trusted)
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
        self.trusted = [t for t in self.trusted if t.get("bssid", "").lower() != ap["bssid"]]
        self.trusted_bssids.discard(ap["bssid"])
        save_trusted_aps(self.trusted)
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


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = SonarApp()
    app.run()
