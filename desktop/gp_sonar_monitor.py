"""gp_sonar_monitor — USB monitor-mode adapter detection for Sonar (T-0028 Phase A).

Phase A scope (this module): hardware enumeration, capability checks, monitor-mode
setup/teardown, and config-driven adapter selection. All paths gracefully degrade
when no monitor-capable adapter is present so customers without the dongle see no
errors.

Phase B (deferred — needs hardware to verify): live Scapy sniffer that consumes
Dot11Deauth + EAPOL + Dot11Beacon frames and emits to the cross-app event bus.
The functions here are designed so a Phase B daemon can call select_adapter +
setup_monitor_mode and start sniffing immediately.

Config: /etc/ghostport/sonar.json
    {"monitor_radio": "off" | "auto" | "wlanX"}

Failsafe default is "off" — customers must explicitly opt in. Auto-mode picks the
first non-AP adapter that supports monitor interface mode.
"""
import json
import os
import re
import subprocess

CONFIG_FILE = "/etc/ghostport/sonar.json"
DEFAULT_CONFIG = {"monitor_radio": "off"}

# Phys we never touch — phy0 carries the AP (wlan0). Any phy hosting an AP-mode
# interface is excluded from auto-pick because flipping it to monitor would drop
# every connected client.
EXCLUDED_INTERFACE_TYPES = ("AP", "P2P-GO")


def read_monitor_config():
    """Return the monitor_radio config value. Falls back to DEFAULT_CONFIG['monitor_radio']
    on any read/parse error so a corrupt config never crashes the caller.
    """
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("monitor_radio"), str):
            return data["monitor_radio"]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return DEFAULT_CONFIG["monitor_radio"]


def list_phys():
    """Return a list of phy names present on the system (e.g. ['phy0', 'phy1'])."""
    try:
        return sorted(os.listdir("/sys/class/ieee80211"))
    except OSError:
        return []


def _phy_info(phy):
    """Return raw 'iw phy <phy> info' output or '' on failure. Timeout-bounded."""
    try:
        proc = subprocess.run(
            ["/usr/sbin/iw", "phy", phy, "info"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def _phy_interfaces(phy):
    """Interfaces (and their types) currently bound to this phy. Used to detect
    whether the phy is hosting an AP — we never auto-pick an AP-bearing phy.
    """
    out = []
    try:
        proc = subprocess.run(
            ["/usr/sbin/iw", "dev"], capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return out
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return out

    current_phy = None
    current_iface = None
    for line in proc.stdout.splitlines():
        m_phy = re.match(r"^phy#(\d+)", line)
        if m_phy:
            current_phy = f"phy{m_phy.group(1)}"
            continue
        m_iface = re.match(r"^\s*Interface\s+(\S+)", line)
        if m_iface:
            current_iface = m_iface.group(1)
            continue
        m_type = re.match(r"^\s*type\s+(\S+)", line)
        if m_type and current_phy == phy and current_iface:
            out.append({"name": current_iface, "type": m_type.group(1)})
            current_iface = None
    return out


def _phy_supports_monitor(info_text):
    """True if 'iw phy info' lists 'monitor' under Supported interface modes."""
    in_modes = False
    for line in info_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Supported interface modes:"):
            in_modes = True
            continue
        if in_modes:
            if stripped.startswith("*"):
                if "monitor" in stripped.lower():
                    return True
            elif stripped and not stripped.startswith("*"):
                # Section ended at the first non-bullet line.
                break
    return False


def find_monitor_capable_adapters():
    """Enumerate phys and return dicts describing each monitor-capable adapter.

    Returns:
        list of {phy, supports_monitor: bool, hosts_ap: bool, interfaces: [...]}.
        Use select_adapter() to pick one for use; this function returns the raw
        inventory so the CLI can show the operator everything that's plugged in.
    """
    out = []
    for phy in list_phys():
        info = _phy_info(phy)
        ifaces = _phy_interfaces(phy)
        hosts_ap = any(i.get("type", "").upper() in EXCLUDED_INTERFACE_TYPES for i in ifaces)
        out.append({
            "phy": phy,
            "supports_monitor": _phy_supports_monitor(info),
            "hosts_ap": hosts_ap,
            "interfaces": ifaces,
        })
    return out


def select_adapter(monitor_radio=None):
    """Resolve the configured monitor_radio value to a concrete phy.

    Args:
        monitor_radio: 'off' | 'auto' | 'wlanX'. None → read from config.

    Returns:
        dict with keys {phy, reason} on success, or {phy: None, reason: <why>}
        when no adapter qualifies. reason is always a human-readable string the
        CLI can surface verbatim.
    """
    if monitor_radio is None:
        monitor_radio = read_monitor_config()

    if monitor_radio == "off":
        return {"phy": None, "reason": "monitor_radio is 'off' (failsafe default)"}

    inventory = find_monitor_capable_adapters()

    if monitor_radio.startswith("wlan") or monitor_radio.startswith("phy"):
        # Explicit pin. Honor it if it exists and supports monitor; otherwise
        # surface a clear error rather than silently falling back.
        for entry in inventory:
            if entry["phy"] == monitor_radio:
                if not entry["supports_monitor"]:
                    return {"phy": None,
                            "reason": f"{monitor_radio} does not support monitor mode"}
                return {"phy": entry["phy"], "reason": "explicit phy pin"}
            for iface in entry["interfaces"]:
                if iface["name"] == monitor_radio:
                    if not entry["supports_monitor"]:
                        return {"phy": None,
                                "reason": f"{monitor_radio}'s phy ({entry['phy']}) does not support monitor mode"}
                    return {"phy": entry["phy"], "reason": f"explicit pin via {monitor_radio}"}
        return {"phy": None, "reason": f"{monitor_radio} not found in /sys/class/ieee80211"}

    if monitor_radio == "auto":
        for entry in inventory:
            if entry["supports_monitor"] and not entry["hosts_ap"]:
                return {"phy": entry["phy"], "reason": "auto-picked first monitor-capable non-AP phy"}
        return {"phy": None,
                "reason": "no monitor-capable adapter present (built-in radio is the AP — plug in a USB dongle)"}

    return {"phy": None, "reason": f"unknown monitor_radio value: {monitor_radio!r}"}


def setup_monitor_mode(phy, monitor_iface="mon0"):
    """Bring up a monitor-mode interface on the given phy.

    Also sweeps sibling managed / AP-VLAN vifs on the same phy so the monitor
    vif owns the radio — without this, channel-lock operations on the monitor
    vif fail with EBUSY because an idle managed vif holds channel-state. AP /
    P2P-GO vifs are never touched (would drop clients). Returns (ok, message).
    """
    # Phys can host multiple vifs (mt7921 allows total<=2); a default 'wlanN'
    # managed vif left over from kernel boot will block channel ops on mon0.
    sweep_cmds = []
    for vif in _phy_interfaces(phy):
        if vif["name"] == monitor_iface:
            continue
        if vif["type"] in ("managed", "AP/VLAN"):
            sweep_cmds.append(["sudo", "-n", "/usr/sbin/ip", "link", "set", vif["name"], "down"])
            sweep_cmds.append(["sudo", "-n", "/usr/sbin/iw", "dev", vif["name"], "del"])

    cmds = sweep_cmds + [
        # Best-effort delete; ignore failure (interface may not exist yet).
        ["sudo", "-n", "/usr/sbin/ip", "link", "set", monitor_iface, "down"],
        ["sudo", "-n", "/usr/sbin/iw", "dev", monitor_iface, "del"],
        # Real setup steps; failure here is reportable.
        ["sudo", "-n", "/usr/sbin/iw", "phy", phy, "interface", "add", monitor_iface, "type", "monitor"],
        ["sudo", "-n", "/usr/sbin/ip", "link", "set", monitor_iface, "up"],
    ]
    best_effort_count = len(sweep_cmds) + 2
    for idx, cmd in enumerate(cmds):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (subprocess.TimeoutExpired, OSError) as e:
            if idx < best_effort_count:
                continue
            return False, f"{cmd[2:]} raised {type(e).__name__}: {e}"
        if proc.returncode != 0 and idx >= best_effort_count:
            return False, f"{' '.join(cmd[2:])} failed (rc={proc.returncode}): {proc.stderr.strip()[:120]}"
    return True, f"{monitor_iface} up on {phy}"


def teardown_monitor_mode(monitor_iface="mon0"):
    """Tear down a monitor-mode interface. Best-effort — never raises."""
    for cmd in (
        ["sudo", "-n", "/usr/sbin/ip", "link", "set", monitor_iface, "down"],
        ["sudo", "-n", "/usr/sbin/iw", "dev", monitor_iface, "del"],
    ):
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass


def status_summary():
    """Return a structured snapshot for CLI display: config + inventory + selection."""
    monitor_radio = read_monitor_config()
    inventory = find_monitor_capable_adapters()
    selection = select_adapter(monitor_radio)
    return {
        "config_file": CONFIG_FILE,
        "monitor_radio": monitor_radio,
        "inventory": inventory,
        "selection": selection,
    }
