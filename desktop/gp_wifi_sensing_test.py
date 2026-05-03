"""gp_wifi_sensing_test — unit-tests the ISP-SSID matcher used by the
WiFi Sensing Audit feature.

The dashboard's /api/wifi-sensing-check endpoint runs `iw dev wlan1 scan`
and matches each captured SSID against ISP_SSID_PATTERNS. If any match,
the dashboard fires a "ISP WIFI SENSING RISK DETECTED" banner pointing
the user at modem-guide.html.

This test asserts the matcher fires on real ISP SSIDs and DOESN'T fire
on plausible-looking false positives. Run before shipping new SSID
patterns or trusting the detector in production.

The patterns are MIRRORED from ghostport-server.js:2566. Keep in sync —
when the server list changes, update this file too. (TODO: extract to
shared JSON or auto-generate from the server source.)

Usage:
    python3 gp_wifi_sensing_test.py             # run all tests
    python3 gp_wifi_sensing_test.py --live      # also hit the live API
"""
import re
import sys
import json
import urllib.request
import ssl

# ── Pattern library mirror ────────────────────────────────────────────
# Must match ghostport-server.js:ISP_SSID_PATTERNS.

ISP_SSID_PATTERNS = [
    (re.compile(r"^(cox|CoxWiFi)", re.I),                    "Cox"),
    (re.compile(r"^(XFINITY|xfinitywifi|HOME-.*-(2\.4|5))", re.I), "Comcast/Xfinity"),
    (re.compile(r"^(ATT|att-wifi|2WIRE)", re.I),             "AT&T"),
    (re.compile(r"^(SPECTRUM|SpectrumSetup|MySpectrum)", re.I), "Spectrum"),
    (re.compile(r"^(Verizon|Fios-|VerizonFiOS)", re.I),      "Verizon"),
    (re.compile(r"^(CenturyLink)", re.I),                    "CenturyLink"),
    (re.compile(r"^(FRONTIER)", re.I),                       "Frontier"),
    (re.compile(r"^(T-Mobile|TMOBILE)", re.I),               "T-Mobile"),
    (re.compile(r"^(Optimum|OPTIMUM)", re.I),                "Optimum"),
    (re.compile(r"^(TWC|BrightHouse)", re.I),                "TWC/Bright House"),
]


def match(ssid):
    """Return ISP name if SSID matches any pattern, else None."""
    for pat, isp in ISP_SSID_PATTERNS:
        if pat.search(ssid):
            return isp
    return None


# ── Test cases ────────────────────────────────────────────────────────

POSITIVE = [
    # (ssid, expected_isp)
    ("CoxWiFi",                "Cox"),
    ("Cox-Guest-1234",         "Cox"),
    ("XFINITY",                "Comcast/Xfinity"),
    ("xfinitywifi",            "Comcast/Xfinity"),
    ("HOME-1A2B-2.4",          "Comcast/Xfinity"),
    ("HOME-9F88-5",            "Comcast/Xfinity"),
    ("ATT-WIFI-1234",          "AT&T"),
    ("att-wifi",               "AT&T"),
    ("2WIRE123",               "AT&T"),
    ("SpectrumSetup-A1",       "Spectrum"),
    ("MySpectrumWiFi-3a-2G",   "Spectrum"),
    ("Verizon_AB1234",         "Verizon"),
    ("Fios-A1B2C",             "Verizon"),
    ("CenturyLink9999",        "CenturyLink"),
    ("FRONTIER-12345",         "Frontier"),
    ("TMOBILE-0D88",           "T-Mobile"),
    ("T-Mobile-Home-Internet-A1", "T-Mobile"),
    ("Optimum-Wifi-A1",        "Optimum"),
    ("BrightHouse-1234",       "TWC/Bright House"),
]

# Plausible false-positive risks — SSIDs that LOOK ISP-y but shouldn't fire.
NEGATIVE = [
    "Ghostport Technologies",   # our own AP
    "MyHomeNetwork",            # generic
    "NETGEAR1234",              # router brand, not ISP
    "TP-LINK_5GHz",             # router brand
    "ASUS_Office",              # router brand
    "JoesPlace",                # human-named
    "guest-wifi",               # generic guest
    "DIRECT-81-HP OfficeJet",   # printer P2P
    "SETUP-3FC8",               # ISP-looking but not in patterns; SHOULDN'T fire — caught by Sonar separately
    "android-hotspot",          # phone tether
    "iPhone-Tether",            # phone tether
    "Home Sweet Home",          # custom-named (lowercase 'home' but doesn't match the FULL HOME-X-Y/Z pattern)
]


def run_unit_tests():
    """Returns (passed, failed) counts."""
    passed = 0
    failed = 0
    print("=== POSITIVE — these MUST fire ===")
    for ssid, expected in POSITIVE:
        got = match(ssid)
        ok = (got == expected)
        if ok:
            passed += 1
            print(f"  PASS  {ssid:<35} → {got}")
        else:
            failed += 1
            print(f"  FAIL  {ssid:<35} → {got!r}  (expected {expected!r})")

    print()
    print("=== NEGATIVE — these MUST NOT fire ===")
    for ssid in NEGATIVE:
        got = match(ssid)
        ok = (got is None)
        if ok:
            passed += 1
            print(f"  PASS  {ssid:<35} → no match")
        else:
            failed += 1
            print(f"  FAIL  {ssid:<35} → {got!r}  (expected no match)")

    return passed, failed


def run_live_test():
    """Hit the running dashboard's /api/wifi-sensing-check and report."""
    print()
    print("=== LIVE — hitting /api/wifi-sensing-check ===")
    print("Note: this requires an authenticated session; without a cookie")
    print("the endpoint returns 401. This test reports raw HTTP behavior.")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = "https://localhost:4201/api/wifi-sensing-check"
    try:
        with urllib.request.urlopen(url, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read())
            print(f"  HTTP {resp.status}")
            print(f"  ispWifiDetected: {data.get('ispWifiDetected')}")
            print(f"  detectedNetworks: {len(data.get('detectedNetworks', []))} entries")
            for n in data.get("detectedNetworks", [])[:5]:
                print(f"    - {n}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} — auth required (expected if no session cookie)")
        return 0
    except Exception as e:
        print(f"  ERROR: {e}")
        return 1


def main():
    passed, failed = run_unit_tests()
    print()
    print(f"=== RESULT: {passed} passed, {failed} failed ===")
    rc = 0 if failed == 0 else 1

    if "--live" in sys.argv:
        rc |= run_live_test()

    return rc


if __name__ == "__main__":
    sys.exit(main())
