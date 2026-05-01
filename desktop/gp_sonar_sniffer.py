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
    from scapy.all import sniff, Dot11, Dot11Deauth, Dot11Beacon, EAPOL
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


def _ssid_from_beacon(pkt):
    """Extract SSID from a beacon's elements list. Returns '' on failure."""
    try:
        # scapy exposes the SSID via the info field on the Dot11Elt with ID=0
        elt = pkt.getlayer("Dot11Elt")
        while elt is not None:
            if getattr(elt, "ID", None) == 0:
                info = getattr(elt, "info", b"") or b""
                if isinstance(info, bytes):
                    try:
                        return info.decode("utf-8", errors="replace")
                    except Exception:
                        return info.hex()
                return str(info)
            elt = elt.payload.getlayer("Dot11Elt") if elt.payload else None
    except Exception:
        pass
    return ""


def make_handler(rate):
    """Return the scapy prn callback closure with the shared rate limiter bound."""

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

            if pkt.haslayer(Dot11Beacon):
                bssid = _bssid_of(pkt)
                ssid = _ssid_from_beacon(pkt)
                if rate.should_emit(("beacon", bssid), BEACON_EMIT_INTERVAL_S):
                    gp_events.emit(
                        source="sonar-sniffer",
                        category="beacon_seen",
                        severity=gp_events.SEVERITY_INFO,
                        summary=f"beacon observed: {ssid or '(hidden)'} ({bssid})",
                        details={"ssid": ssid, "bssid": bssid},
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
    handler = make_handler(rate)
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
