#!/usr/bin/env python3
"""gp_sonar_sniffer — Phase B live monitor-mode sniffer for Sonar.

Long-running daemon that uses gp_sonar_monitor's adapter-management layer to
bring up `mon0` in monitor mode, then sniffs Dot11Deauth + EAPOL handshake +
Dot11Beacon frames via scapy. Each interesting frame is summarized and
emitted to the gp_events bus so downstream correlators (gp-correlator) can
fold sniffer events into multi-source detections.

Failure modes are SILENT and SAFE:
  - No monitor radio configured  → exit 0, single info log line
  - python3-scapy missing        → exit 0, single info log line
  - mon0 setup fails             → exit 0, single info log line
  - Adapter detached at runtime  → terminate cleanly so systemd restarts

We never crash on a single bad frame — exceptions in the per-frame handler
are caught + logged at debug level only.

Run via the ghostport-sonar-sniffer.service systemd unit. CAP_NET_RAW +
CAP_NET_ADMIN are granted via systemd AmbientCapabilities so we don't run
as root.
"""

import json
import logging
import os
import signal
import sys
import time
from collections import OrderedDict

sys.path.insert(0, "/opt/phantom/desktop")

# ── Optional dependencies — graceful degrade ─────────────────────────

try:
    import gp_events
except Exception as _e:  # pragma: no cover — graceful exit on import failure
    print(f"[gp-sonar-sniffer] gp_events import failed: {_e}; exiting 0", file=sys.stderr)
    sys.exit(0)

try:
    import gp_sonar_monitor
except Exception as _e:  # pragma: no cover
    print(f"[gp-sonar-sniffer] gp_sonar_monitor import failed: {_e}; exiting 0", file=sys.stderr)
    sys.exit(0)

try:
    from scapy.all import sniff, Dot11, Dot11Deauth, Dot11Beacon, Dot11Elt, Dot11ProbeReq, EAPOL, RadioTap
except Exception as _e:  # pragma: no cover
    print(f"[gp-sonar-sniffer] scapy unavailable ({_e}); exiting 0", file=sys.stderr)
    sys.exit(0)


# ── Constants ─────────────────────────────────────────────────────────

LOG = logging.getLogger("gp-sonar-sniffer")
MONITOR_IFACE = "mon0"

# Beacon frames are extremely high-volume (~10/sec/AP). Per-BSSID rate-limit
# so we get a baseline emit per-AP-per-window without flooding the bus.
BEACON_EMIT_INTERVAL_S = 60
# Deauth + EAPOL are rare and important — emit every event but cap memory
# of recent deauth (src,dst) pairs we've already counted to avoid log noise.
DEAUTH_DEDUP_INTERVAL_S = 5
EAPOL_DEDUP_INTERVAL_S = 30
# Per-key dedup map size cap (LRU eviction).
DEDUP_MAX = 1024

# T-0058 — out-of-throttle change detection for evil-twin / RSSI-anomaly
# response. Beacons get a faster cadence change-check (every 5s per BSSID);
# any meaningful delta vs cached state emits a `beacon_change` event with
# escalated severity, regardless of the 60s `beacon_seen` throttle.
CHANGE_DETECT_INTERVAL_S = 5
RSSI_CHANGE_THRESHOLD_DB = 10
# Require this many consecutive observations of an RSSI delta past threshold
# before alerting — guards against single-sample noise in monitor-mode RX.
RSSI_CONFIRM_COUNT = 2

# T-0059 — probe-request capture for Wi-Fi Pineapple / Karma detection.
# Default OFF. mac-only redacts the requested SSID before emit (presence
# detection without privacy leak); full emits the SSID (active threat hunt).
# Per-source-MAC dedup so a phone polling 30 SSIDs in a burst becomes one event.
PROBE_REQ_DEDUP_INTERVAL_S = 60

# OUI vendor lookup — top consumer + enterprise vendors seen in residential RF.
# Universally-administered MACs only; locally-administered (LA-bit set) returns
# None because they're typically virtual/random privacy MACs without vendor info.
# Format: lowercase "xx:xx:xx" → vendor short name.
OUI_TABLE = {
    # Major US ISP / gateway vendors
    "1c:93:7c": "Sercomm/Cox",
    "40:e1:e4": "T-Mobile",
    "00:24:d4": "Comcast",
    "00:1f:e2": "Verizon",
    "94:9b:2c": "Sagemcom",
    "44:fb:5a": "Sagemcom",
    "00:1d:c1": "Cisco",
    # Apple
    "f0:18:98": "Apple", "a4:5e:60": "Apple", "8c:85:90": "Apple",
    "f4:0f:24": "Apple", "3c:22:fb": "Apple", "ac:bc:32": "Apple",
    # Google / Nest / Pixel
    "1c:f2:9a": "Google", "44:07:0b": "Google", "94:eb:cd": "Google",
    "f4:f5:d8": "Google", "f4:f5:e8": "Google",
    # Amazon / Eero / Lab126
    "f8:bb:bf": "Eero", "70:8a:09": "Eero", "fc:65:de": "Amazon", "44:65:0d": "Amazon",
    # Common consumer router brands
    "a4:2b:b0": "TP-Link", "50:c7:bf": "TP-Link", "98:da:c4": "ASUS",
    "10:0d:7f": "Netgear", "20:0c:c8": "Netgear",
    "00:0d:b9": "PC Engines", "c0:c9:e3": "Linksys", "60:38:e0": "Belkin",
    "04:42:1a": "Belkin", "00:90:4c": "Epigram/Broadcom",
    # IoT / printers / mesh
    "fa:b4:6a": "HP", "98:e7:f4": "HP", "70:5a:0f": "HP",
    # ISP gateway OEMs seen in residential RF (TMOBILE-/SETUP- family devices)
    "ac:df:9f": "Sagemcom/T-Mobile", "12:a7:93": "ISP-gateway",
    "00:80:77": "Brother", "30:cd:a7": "Samsung",
    "b0:7f:b9": "Roku", "ac:3a:7a": "Roku",
    # Wi-Fi chip vendors (often appear in vendor IEs, sometimes as primary)
    "00:03:7f": "Atheros", "8c:fd:f0": "Qualcomm",
    "00:50:f2": "Microsoft",  # WPS / WMM IE OUI
    # Espressif (ESP8266/ESP32 — DIY IoT)
    "84:0d:8e": "Espressif", "fc:f5:c4": "Espressif", "a4:cf:12": "Espressif",
    # Raspberry Pi Foundation
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi",
    "e4:5f:01": "Raspberry Pi", "2c:cf:67": "Raspberry Pi",
    # Intel / Broadcom (Wi-Fi adapters in laptops/phones)
    "ac:de:48": "Intel", "00:13:46": "Intel", "94:65:9c": "Intel",
    # Ubiquiti / MikroTik (prosumer / SMB)
    "44:d9:e7": "Ubiquiti", "fc:ec:da": "Ubiquiti",
    "4c:5e:0c": "MikroTik", "b8:69:f4": "MikroTik",
    # Aruba / HPE
    "20:4c:03": "Aruba", "94:b4:0f": "Aruba",
}


# ── Per-frame handlers ────────────────────────────────────────────────


class RateLimiter:
    """LRU map of arbitrary key → last-emit-epoch with TTL gate."""

    def __init__(self, max_entries=DEDUP_MAX):
        self._d = OrderedDict()
        self._max = max_entries

    def should_emit(self, key, ttl_s):
        now = time.time()
        last = self._d.get(key, 0.0)
        if now - last < ttl_s:
            return False
        self._d[key] = now
        self._d.move_to_end(key)
        while len(self._d) > self._max:
            self._d.popitem(last=False)
        return True


def _bssid_of(pkt):
    """Best-effort extract BSSID (addr3) from a Dot11 frame."""
    try:
        return getattr(pkt, "addr3", None) or getattr(pkt, "addr1", None) or "??"
    except Exception:
        return "??"


def _walk_ies(pkt):
    """Yield (id, bytes) for every Dot11Elt in the frame, including IE-extension
    (id=255) which carries the ext-id as the first byte of payload."""
    try:
        elt = pkt.getlayer(Dot11Elt)
    except Exception:
        return
    while elt is not None:
        eid = getattr(elt, "ID", None)
        info = getattr(elt, "info", b"") or b""
        if eid is not None:
            yield int(eid), bytes(info) if not isinstance(info, bytes) else info
        try:
            elt = elt.payload.getlayer(Dot11Elt) if elt.payload else None
        except Exception:
            break


def _ssid_from_beacon(pkt):
    """Extract SSID from IE 0. Returns '' on failure."""
    for eid, info in _walk_ies(pkt):
        if eid == 0:
            try:
                return info.decode("utf-8", errors="replace")
            except Exception:
                return info.hex()
    return ""


def _is_hidden(ssid_str, ssid_bytes_present):
    """Hidden SSID: empty string, or all-NUL bytes, or no IE 0 at all."""
    if not ssid_bytes_present:
        return True
    return ssid_str == "" or all(c == "\x00" for c in ssid_str)


def _radiotap_signal(pkt):
    """Extract RSSI / freq / channel from RadioTap. Returns dict of available
    fields; missing fields omitted rather than None-filled."""
    out = {}
    try:
        rt = pkt.getlayer(RadioTap)
    except Exception:
        return out
    if rt is None:
        return out
    rssi = getattr(rt, "dBm_AntSignal", None)
    if rssi is not None:
        out["rssi_dbm"] = int(rssi)
    freq = getattr(rt, "ChannelFrequency", None) or getattr(rt, "Channel", None)
    if freq is not None:
        out["freq_mhz"] = int(freq)
        out["channel"] = _freq_to_channel(int(freq))
    return out


def _freq_to_channel(freq_mhz):
    """Map a freq in MHz to a channel number. Returns 0 on unrecognized band."""
    if 2412 <= freq_mhz <= 2472:
        return (freq_mhz - 2407) // 5
    if freq_mhz == 2484:
        return 14
    if 5170 <= freq_mhz <= 5895:
        return (freq_mhz - 5000) // 5
    if 5955 <= freq_mhz <= 7115:  # 6 GHz / Wi-Fi 6E
        return (freq_mhz - 5950) // 5
    return 0


_RSN_AKM = {
    1: "1x", 2: "psk", 3: "ft-1x", 4: "ft-psk",
    5: "1x-sha256", 6: "psk-sha256", 7: "tdls",
    8: "sae", 9: "ft-sae", 11: "1x-suite-b",
    12: "1x-suite-b-192", 18: "owe",
}
_RSN_CIPHER = {
    1: "wep40", 2: "tkip", 4: "ccmp", 5: "wep104",
    6: "bip-cmac-128", 8: "gcmp-128", 9: "gcmp-256",
    10: "ccmp-256", 11: "bip-gmac-128", 12: "bip-gmac-256",
}


def _security_suite(pkt, cap_privacy):
    """Parse RSN IE 48 (and Microsoft WPA1 IE 221/00:50:F2 type 1) into a
    security descriptor: {present, akm:[...], cipher, mfp}.

    cap_privacy: bool from beacon capability flags. Privacy=1 + no RSN+no WPA1
    IE = WEP. Privacy=0 = open."""
    out = {"present": False, "akm": [], "cipher": None, "mfp": "absent"}
    rsn_seen = False
    wpa1_seen = False

    for eid, info in _walk_ies(pkt):
        if eid == 48:  # RSN (WPA2/WPA3)
            rsn_seen = True
            try:
                # RSN IE format: version(2) + group_cipher(4) + pcs_count(2)
                # + pcs_list(4*N) + akm_count(2) + akm_list(4*M) + caps(2)
                if len(info) < 8:
                    continue
                # group cipher (skip OUI 3 bytes, take type byte)
                out["cipher"] = _RSN_CIPHER.get(info[5], f"unk({info[5]})")
                # pcs count
                pcs_n = int.from_bytes(info[6:8], "little")
                pos = 8 + 4 * pcs_n
                if len(info) < pos + 2:
                    continue
                # akm count
                akm_n = int.from_bytes(info[pos:pos + 2], "little")
                pos += 2
                akms = []
                for i in range(akm_n):
                    if len(info) < pos + 4 * (i + 1):
                        break
                    akm_type = info[pos + 4 * i + 3]
                    akms.append(_RSN_AKM.get(akm_type, f"unk({akm_type})"))
                if akms:
                    out["akm"] = akms
                # RSN capabilities (2 bytes after akm list)
                cap_pos = pos + 4 * akm_n
                if len(info) >= cap_pos + 2:
                    rsn_caps = int.from_bytes(info[cap_pos:cap_pos + 2], "little")
                    mfp_required = bool(rsn_caps & 0x40)
                    mfp_capable = bool(rsn_caps & 0x80)
                    if mfp_required:
                        out["mfp"] = "required"
                    elif mfp_capable:
                        out["mfp"] = "capable"
                    else:
                        out["mfp"] = "absent"
            except Exception:
                pass
        elif eid == 221:  # vendor-specific
            # Microsoft WPA1: OUI 00:50:F2, type 1
            if len(info) >= 4 and info[:3] == b"\x00\x50\xf2" and info[3] == 1:
                wpa1_seen = True

    if rsn_seen:
        out["present"] = True
        # RSN + AKM containing SAE = WPA3; PSK only = WPA2; mixed = WPA2/3 transition
        if any(a in ("sae", "ft-sae", "owe") for a in out["akm"]):
            out["mode"] = "wpa3" if not any(a in ("psk", "psk-sha256") for a in out["akm"]) else "wpa2/3"
        elif any(a in ("psk", "psk-sha256") for a in out["akm"]):
            out["mode"] = "wpa2"
        elif any(a.startswith("1x") for a in out["akm"]):
            out["mode"] = "wpa2-enterprise"
        else:
            out["mode"] = "wpa2-other"
    elif wpa1_seen:
        out["present"] = True
        out["mode"] = "wpa1"
    elif cap_privacy:
        out["present"] = True
        out["mode"] = "wep"
    else:
        out["mode"] = "open"
    return out


def _phy_type(pkt, freq_mhz=0):
    """Detect highest-supported PHY: ax > ac > n > a/g/b. Walks IEs once."""
    has_ht = has_vht = has_he = False
    for eid, info in _walk_ies(pkt):
        if eid == 45:
            has_ht = True
        elif eid == 191:
            has_vht = True
        elif eid == 255 and len(info) >= 1:
            ext_id = info[0]
            if ext_id in (35, 36):  # HE Capabilities / HE Operation
                has_he = True
    if has_he:
        return "ax"
    if has_vht:
        return "ac"
    if has_ht:
        return "n"
    if freq_mhz >= 5000:
        return "a"
    return "g"


def _wps_state(pkt):
    """Detect WPS via vendor IE 221 with OUI 00:50:F2 type 4. Returns
    'configured', 'unconfigured', or 'absent'."""
    for eid, info in _walk_ies(pkt):
        if eid != 221 or len(info) < 5:
            continue
        if info[:3] != b"\x00\x50\xf2" or info[3] != 4:
            continue
        # WPS attributes are TLV: 2-byte BE attr-id, 2-byte BE length, value.
        # Walk for attr 0x1044 (WPS State): 1-byte value.
        pos = 4
        body = info[4:]
        i = 0
        while i + 4 <= len(body):
            attr_id = int.from_bytes(body[i:i + 2], "big")
            attr_len = int.from_bytes(body[i + 2:i + 4], "big")
            if i + 4 + attr_len > len(body):
                break
            if attr_id == 0x1044 and attr_len >= 1:
                state = body[i + 4]
                return "configured" if state == 0x02 else "unconfigured"
            i += 4 + attr_len
        return "configured"  # WPS IE present but no explicit state attr
    return "absent"


def _vendor_oui(bssid):
    """Lookup vendor by BSSID OUI. Returns short name or None on miss.
    Some real devices (HP printers, etc.) ship with LA-flagged OUIs, so we
    don't pre-filter LA — we just try the table. Privacy MACs miss naturally."""
    if not bssid or len(bssid) < 8:
        return None
    return OUI_TABLE.get(bssid[:8].lower())


class _BeaconChangeDetector:
    """Per-BSSID cache of (rssi, sec_hash, vendor, channel) used to detect
    changes between consecutive observations. Returns a list of change-tuples
    when something meaningful drifted, or [] if stable."""

    def __init__(self, max_entries=DEDUP_MAX):
        self._cache = OrderedDict()  # bssid -> {rssi, sec_hash, vendor, channel, pending_rssi_count}
        self._max = max_entries

    def _security_hash_of(self, sec):
        if not isinstance(sec, dict):
            return ""
        akm = ",".join(sorted(sec.get("akm") or []))
        return f"{sec.get('mode','')}|{sec.get('cipher','')}|{akm}|{sec.get('mfp','')}"

    def check(self, bssid, details):
        """Compare new details to cached. Returns a list of change descriptions
        (each: (kind, prev, new)) — empty list if nothing meaningful drifted."""
        new_rssi = details.get("rssi_dbm")
        new_sec = self._security_hash_of(details.get("security") or {})
        new_vendor = details.get("vendor")
        new_channel = details.get("channel")

        cached = self._cache.get(bssid)
        if cached is None:
            self._cache[bssid] = {
                "rssi": new_rssi, "sec_hash": new_sec,
                "vendor": new_vendor, "channel": new_channel,
                "pending_rssi_count": 0,
            }
            self._cache.move_to_end(bssid)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
            return []

        changes = []
        # Categorical changes — single occurrence is enough.
        if cached["sec_hash"] and new_sec and cached["sec_hash"] != new_sec:
            changes.append(("security", cached["sec_hash"], new_sec))
        if cached["vendor"] and new_vendor and cached["vendor"] != new_vendor:
            changes.append(("vendor", cached["vendor"], new_vendor))
        if cached["channel"] and new_channel and cached["channel"] != new_channel:
            changes.append(("channel", cached["channel"], new_channel))

        # RSSI requires 2 consecutive over-threshold samples to fire.
        # Critical: cached["rssi"] is the BASELINE we compare deltas against —
        # don't roll it forward on every call or "consecutive" loses meaning.
        if (isinstance(new_rssi, int) and isinstance(cached["rssi"], int)
                and abs(new_rssi - cached["rssi"]) >= RSSI_CHANGE_THRESHOLD_DB):
            cached["pending_rssi_count"] += 1
            if cached["pending_rssi_count"] >= RSSI_CONFIRM_COUNT:
                changes.append(("rssi", cached["rssi"], new_rssi))
                cached["pending_rssi_count"] = 0
                cached["rssi"] = new_rssi  # promote new baseline AFTER firing
        else:
            cached["pending_rssi_count"] = 0
            if isinstance(new_rssi, int):
                cached["rssi"] = new_rssi  # stable read → roll baseline forward

        # Categorical fields always reflect latest seen.
        cached["sec_hash"] = new_sec or cached["sec_hash"]
        cached["vendor"] = new_vendor or cached["vendor"]
        cached["channel"] = new_channel or cached["channel"]
        self._cache.move_to_end(bssid)
        return changes


def _read_probe_capture_mode():
    """Return 'off' | 'mac-only' | 'full' from /etc/ghostport/sonar.json.
    Defaults to 'off' on any read/parse failure — privacy-safe failure mode.
    Read once at startup; UI restarts the service when the user changes it.
    """
    try:
        import json
        with open("/etc/ghostport/sonar.json") as f:
            data = json.load(f)
        v = data.get("probe_capture") if isinstance(data, dict) else None
        if v in ("off", "mac-only", "full"):
            return v
    except (FileNotFoundError, OSError, ValueError):
        pass
    return "off"


def make_handler(rate, change_detector=None, probe_mode="off"):
    """Return the scapy prn callback closure with the shared rate limiter
    + optional change detector bound.

    probe_mode is "off" | "mac-only" | "full" — gates Dot11ProbeReq emission.
    "off" skips the frame entirely. "mac-only" emits source MAC + signal +
    channel, redacts requested SSID. "full" emits everything including SSID.
    """
    if change_detector is None:
        change_detector = _BeaconChangeDetector()

    def handle(pkt):
        try:
            if not pkt.haslayer(Dot11):
                return

            if pkt.haslayer(Dot11Deauth):
                src = getattr(pkt, "addr2", "??")
                dst = getattr(pkt, "addr1", "??")
                bssid = _bssid_of(pkt)
                reason = getattr(pkt.getlayer(Dot11Deauth), "reason", -1)
                # Dedup at (src, dst, reason) granularity over a short window.
                if rate.should_emit(("deauth", src, dst, reason), DEAUTH_DEDUP_INTERVAL_S):
                    gp_events.emit(
                        source="sonar-sniffer",
                        category="deauth_attack",
                        severity=gp_events.SEVERITY_DANGEROUS,
                        summary=f"Dot11Deauth observed: {src} -> {dst} (reason={reason})",
                        details={"src": src, "dst": dst, "bssid": bssid, "reason": int(reason)},
                    )
                return

            if pkt.haslayer(EAPOL):
                src = getattr(pkt, "addr2", "??")
                dst = getattr(pkt, "addr1", "??")
                bssid = _bssid_of(pkt)
                if rate.should_emit(("eapol", src, dst, bssid), EAPOL_DEDUP_INTERVAL_S):
                    gp_events.emit(
                        source="sonar-sniffer",
                        category="eapol_observed",
                        severity=gp_events.SEVERITY_INFO,
                        summary=f"EAPOL frame: {src} -> {dst} (bssid={bssid})",
                        details={"src": src, "dst": dst, "bssid": bssid},
                    )
                return

            if pkt.haslayer(Dot11ProbeReq):
                # T-0059 — Pineapple/Karma detection feed. Off by default;
                # operator opts in via Sonar UI. mac-only redacts SSID at the
                # parser so the SSID never reaches the bus / DB / logs.
                if probe_mode == "off":
                    return
                src = getattr(pkt, "addr2", "??")
                # Per-MAC dedup so a single device polling 30 cached SSIDs
                # becomes one bus event, not thirty.
                if not rate.should_emit(("probe-req", src), PROBE_REQ_DEDUP_INTERVAL_S):
                    return
                rt = _radiotap_signal(pkt)
                details = {
                    "src": src,
                    "rssi_dbm": rt.get("rssi_dbm"),
                    "channel": rt.get("channel"),
                }
                if probe_mode == "full":
                    # Only attached when operator explicitly chose 'full' —
                    # this is the privacy-sensitive payload (which networks
                    # the device has previously connected to).
                    ssid = _ssid_from_beacon(pkt)
                    details["ssid"] = ssid
                    summary = f"probe-request: {src} -> {ssid or '(broadcast)'}"
                else:
                    summary = f"probe-request: {src}"
                gp_events.emit(
                    source="sonar-sniffer",
                    category="probe_request",
                    severity=gp_events.SEVERITY_INFO,
                    summary=summary,
                    details=details,
                )
                return

            if pkt.haslayer(Dot11Beacon):
                bssid = _bssid_of(pkt)
                # Two parallel throttles: change-check (5s) and full emit (60s).
                # We enrich at most once per CHANGE_DETECT_INTERVAL_S per BSSID.
                can_check = rate.should_emit(("beacon-check", bssid), CHANGE_DETECT_INTERVAL_S)
                can_emit = rate.should_emit(("beacon", bssid), BEACON_EMIT_INTERVAL_S)
                if not (can_check or can_emit):
                    return
                ssid = _ssid_from_beacon(pkt)
                rt = _radiotap_signal(pkt)
                freq = rt.get("freq_mhz", 0)
                cap = int(getattr(pkt.getlayer(Dot11Beacon), "cap", 0) or 0)
                cap_privacy = bool(cap & 0x10)
                beacon = pkt.getlayer(Dot11Beacon)
                interval = getattr(beacon, "beacon_interval", 0) or 0
                details = {
                    "ssid": ssid,
                    "bssid": bssid,
                    "hidden": _is_hidden(ssid, ssid != ""),
                    "phy": _phy_type(pkt, freq),
                    "security": _security_suite(pkt, cap_privacy),
                    "vendor": _vendor_oui(bssid),
                    "wps": _wps_state(pkt),
                    "interval_ms": round(interval * 1.024, 1),
                }
                details.update(rt)  # rssi_dbm, freq_mhz, channel
                # T-0058 change-detect — fires at 5s cadence, bypasses 60s emit
                # throttle when significant drift is detected.
                if can_check:
                    changes = change_detector.check(bssid, details)
                    if changes:
                        kinds = [c[0] for c in changes]
                        gp_events.emit(
                            source="sonar-sniffer",
                            category="beacon_change",
                            severity=gp_events.SEVERITY_DANGEROUS if "security" in kinds or "vendor" in kinds
                                else gp_events.SEVERITY_INFO,
                            summary=f"beacon drift: {ssid or '(hidden)'} ({bssid}) {','.join(kinds)}",
                            details={**details, "changes": [{"kind": k, "prev": p, "new": n} for k, p, n in changes]},
                        )
                if can_emit:
                    gp_events.emit(
                        source="sonar-sniffer",
                        category="beacon_seen",
                        severity=gp_events.SEVERITY_INFO,
                        summary=f"beacon observed: {ssid or '(hidden)'} ({bssid})",
                        details=details,
                    )
                return
        except Exception as e:
            LOG.debug("frame handler error: %s", e)

    return handle


# ── Main loop ─────────────────────────────────────────────────────────

_running = True


def _stop(_sig=None, _frame=None):
    global _running
    _running = False


def main():
    logging.basicConfig(
        level=os.environ.get("GP_SONAR_SNIFFER_LOG", "INFO"),
        format="%(asctime)s [%(name)s] %(message)s",
    )

    cfg = gp_sonar_monitor.read_monitor_config()
    if cfg in (None, "off", ""):
        LOG.info("monitor_radio is 'off' (failsafe default) — no sniffer; exiting 0")
        return 0

    selected = gp_sonar_monitor.select_adapter(cfg)
    if not selected:
        LOG.info("no monitor-capable adapter selected (config=%r) — exiting 0", cfg)
        return 0
    phy = selected.get("phy") if isinstance(selected, dict) else selected
    if not phy:
        LOG.info("select_adapter returned no phy — exiting 0")
        return 0

    LOG.info("setting up monitor mode on %s -> %s", phy, MONITOR_IFACE)
    try:
        ok = gp_sonar_monitor.setup_monitor_mode(phy, monitor_iface=MONITOR_IFACE)
    except Exception as e:
        LOG.info("setup_monitor_mode raised %s — exiting 0", e)
        return 0
    if not ok:
        LOG.info("setup_monitor_mode failed; the adapter may have been pulled — exiting 0")
        return 0

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    rate = RateLimiter()
    probe_mode = _read_probe_capture_mode()
    LOG.info("probe_capture mode: %s", probe_mode)
    handler = make_handler(rate, probe_mode=probe_mode)
    try:
        # store=False keeps memory bounded — scapy would otherwise hold every
        # captured packet. stop_filter polls _running so SIGTERM unblocks.
        sniff(
            iface=MONITOR_IFACE,
            prn=handler,
            store=False,
            stop_filter=lambda _p: not _running,
        )
    except Exception as e:
        LOG.warning("sniff loop terminated: %s", e)
    finally:
        try:
            gp_sonar_monitor.teardown_monitor_mode(monitor_iface=MONITOR_IFACE)
        except Exception as e:
            LOG.debug("teardown_monitor_mode error: %s", e)
    LOG.info("sniffer exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
