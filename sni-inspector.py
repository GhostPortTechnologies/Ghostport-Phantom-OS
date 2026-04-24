#!/usr/bin/env python3
"""
GhostPort SNI Inspector — Transparent TLS SNI-based blocker
Intercepts HTTPS from Family Shield shielded devices, reads SNI,
blocks if domain matches active blocklists. Zero decryption.

Runs as a systemd service. Managed via gp-ssl-inspect.
"""

import socket
import struct
import select
import signal
import sys
import os
import json
import time
import threading
import logging

LISTEN_PORT = 8443
LISTEN_ADDR = "0.0.0.0"
FAMILY_SHIELD_FILE = "/etc/phantom/family-shield.json"
BLOCKLIST_CACHE_FILE = "/etc/phantom/sni-blocklist.json"
PIHOLE_GRAVITY = "/etc/pihole/gravity.db"
RELOAD_INTERVAL = 300  # seconds between blocklist reloads
CONNECT_TIMEOUT = 5
PROXY_TIMEOUT = 30
BUF_SIZE = 65536

# SO_ORIGINAL_DST for recovering real destination from nftables REDIRECT
SO_ORIGINAL_DST = 80

logging.basicConfig(
    level=logging.INFO,
    format="[SNI-Inspector] %(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("sni-inspector")

blocked_domains = set()
blocked_domains_lock = threading.Lock()
running = threading.Event()
running.set()  # starts as "running"


def load_blocklist():
    """Load blocked domains from Pi-hole gravity DB + Family Shield config."""
    domains = set()

    # Read Family Shield config to know which categories are active
    try:
        with open(FAMILY_SHIELD_FILE, "r") as f:
            config = json.load(f)
        active_cats = [k for k, v in config.get("categories", {}).items() if v]
    except Exception:
        active_cats = []

    if not active_cats:
        return domains

    # Load from cached blocklist (populated by server on category change)
    try:
        with open(BLOCKLIST_CACHE_FILE, "r") as f:
            cache = json.load(f)
        for domain in cache.get("domains", []):
            domains.add(domain.lower().strip())
    except Exception:
        pass

    # Also try reading Pi-hole gravity DB directly for FamilyShield group
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{PIHOLE_GRAVITY}?mode=ro", uri=True)
        cur = conn.cursor()
        # Get FamilyShield group ID
        cur.execute("SELECT id FROM 'group' WHERE name='FamilyShield'")
        row = cur.fetchone()
        if row:
            group_id = row[0]
            # Get all domains from lists assigned to this group
            cur.execute("""
                SELECT d.domain FROM domainlist d
                INNER JOIN domainlist_by_group dg ON d.id = dg.domainlist_id
                WHERE dg.group_id = ? AND d.type IN (1, 3)
            """, (group_id,))
            for (domain,) in cur.fetchall():
                domains.add(domain.lower().strip())

            # Get domains from gravity (adlists assigned to group)
            cur.execute("""
                SELECT DISTINCT g.domain FROM gravity g
                INNER JOIN adlist_by_group ag ON g.adlist_id = ag.adlist_id
                WHERE ag.group_id = ?
            """, (group_id,))
            for (domain,) in cur.fetchall():
                domains.add(domain.lower().strip())
        conn.close()
    except Exception as e:
        log.debug(f"Gravity DB read failed (non-fatal): {e}")

    return domains


def reload_blocklist_loop():
    """Periodically reload the blocklist."""
    global blocked_domains
    _prev_count = -1
    while running.is_set():
        try:
            new_domains = load_blocklist()
            with blocked_domains_lock:
                blocked_domains = new_domains
            count = len(new_domains)
            if count != _prev_count:
                log.info(f"Blocklist loaded: {count} domains")
                _prev_count = count
            elif count > 0:
                log.debug(f"Blocklist reloaded: {count} domains (unchanged)")
        except Exception as e:
            log.error(f"Blocklist reload failed: {e}")
        for _ in range(RELOAD_INTERVAL):
            if not running.is_set():
                break
            time.sleep(1)


def is_blocked(hostname):
    """Check if hostname matches any blocked domain."""
    if not hostname:
        return False
    hostname = hostname.lower().strip()
    with blocked_domains_lock:
        # Exact match
        if hostname in blocked_domains:
            return True
        # Check parent domains (e.g., app.tiktok.com → tiktok.com)
        parts = hostname.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in blocked_domains:
                return True
    return False


def get_original_dst(sock):
    """Recover original destination from nftables REDIRECT via SO_ORIGINAL_DST."""
    try:
        dst = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
        port = struct.unpack("!H", dst[2:4])[0]
        ip = socket.inet_ntoa(dst[4:8])
        return ip, port
    except Exception as e:
        log.error(f"Failed to get original dst: {e}")
        return None, None


def extract_sni(data):
    """Extract SNI hostname from TLS ClientHello."""
    try:
        # TLS record: type(1) version(2) length(2) = 5 bytes header
        if len(data) < 5 or data[0] != 0x16:  # 0x16 = Handshake
            return None

        # Handshake header: type(1) length(3) = 4 bytes
        pos = 5
        if pos >= len(data) or data[pos] != 0x01:  # 0x01 = ClientHello
            return None

        # Skip handshake length (3 bytes) + client version (2 bytes) + random (32 bytes)
        pos += 4 + 2 + 32

        # Session ID length (1 byte) + session ID
        if pos >= len(data):
            return None
        session_len = data[pos]
        pos += 1 + session_len

        # Cipher suites length (2 bytes) + cipher suites
        if pos + 2 > len(data):
            return None
        cipher_len = struct.unpack("!H", data[pos:pos + 2])[0]
        pos += 2 + cipher_len

        # Compression methods length (1 byte) + methods
        if pos >= len(data):
            return None
        comp_len = data[pos]
        pos += 1 + comp_len

        # Extensions length (2 bytes)
        if pos + 2 > len(data):
            return None
        ext_total = struct.unpack("!H", data[pos:pos + 2])[0]
        pos += 2

        # Walk extensions looking for SNI (type 0x0000)
        end = pos + ext_total
        while pos + 4 <= end and pos + 4 <= len(data):
            ext_type = struct.unpack("!H", data[pos:pos + 2])[0]
            ext_len = struct.unpack("!H", data[pos + 2:pos + 4])[0]
            pos += 4

            if ext_type == 0x0000:  # SNI extension
                # SNI list length (2 bytes)
                if pos + 2 > len(data):
                    return None
                # sni_list_len = struct.unpack("!H", data[pos:pos+2])[0]
                pos += 2
                # SNI type (1 byte, must be 0 = hostname)
                if pos >= len(data) or data[pos] != 0x00:
                    return None
                pos += 1
                # Hostname length (2 bytes) + hostname
                if pos + 2 > len(data):
                    return None
                name_len = struct.unpack("!H", data[pos:pos + 2])[0]
                pos += 2
                if pos + name_len > len(data):
                    return None
                hostname = data[pos:pos + name_len].decode("ascii", errors="replace")
                # Reject hostnames with replacement chars (invalid ASCII)
                if "\ufffd" in hostname:
                    return None
                return hostname

            pos += ext_len

        return None
    except Exception:
        return None


def proxy_connection(client_sock, initial_data, dst_ip, dst_port):
    """Proxy TCP between client and destination after SNI check passes."""
    remote_sock = None
    try:
        remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote_sock.settimeout(CONNECT_TIMEOUT)
        remote_sock.connect((dst_ip, dst_port))
        remote_sock.settimeout(None)

        # Send the initial data (ClientHello) to the real server
        remote_sock.sendall(initial_data)

        # Bidirectional proxy
        sockets = [client_sock, remote_sock]
        while running.is_set():
            readable, _, errored = select.select(sockets, [], sockets, PROXY_TIMEOUT)
            if errored:
                break
            if not readable:
                break  # Timeout
            done = False
            for sock in readable:
                data = sock.recv(BUF_SIZE)
                if not data:
                    done = True
                    break  # Connection closed
                target = remote_sock if sock is client_sock else client_sock
                try:
                    target.sendall(data)
                except Exception:
                    done = True
                    break
            if done:
                break
    except Exception:
        pass
    finally:
        if remote_sock:
            try:
                remote_sock.close()
            except Exception:
                pass


def handle_client(client_sock, client_addr):
    """Handle an intercepted connection."""
    try:
        dst_ip, dst_port = get_original_dst(client_sock)
        if not dst_ip:
            return

        # Read initial data (should contain TLS ClientHello)
        client_sock.settimeout(5)
        try:
            data = client_sock.recv(BUF_SIZE)
        except socket.timeout:
            return

        if not data:
            return

        sni = extract_sni(data)

        # Fail-closed: if we can't extract SNI, block the connection
        if sni is None:
            log.info(f"BLOCKED {client_addr[0]} → [no SNI] ({dst_ip}:{dst_port})")
            return

        if is_blocked(sni):
            log.info(f"BLOCKED {client_addr[0]} → {sni} ({dst_ip}:{dst_port})")
            return

        # Allowed — proxy transparently
        client_sock.settimeout(None)
        proxy_connection(client_sock, data, dst_ip, dst_port)
    except Exception as e:
        log.debug(f"Client handler error: {e}")
    finally:
        try:
            client_sock.close()
        except Exception:
            pass


def shutdown(_signum, _frame):
    """Clean shutdown."""
    running.clear()
    log.info("Shutting down...")


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Write PID file with restricted permissions
    pid_file = "/run/ghostport-sni.pid"
    fd = os.open(pid_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))

    # Start blocklist reload thread
    reload_thread = threading.Thread(target=reload_blocklist_loop, daemon=True)
    reload_thread.start()

    # Allow port reuse and transparent proxying
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # IP_TRANSPARENT allows accepting redirected connections
    try:
        server.setsockopt(socket.SOL_IP, 19, 1)  # IP_TRANSPARENT = 19
    except Exception as e:
        log.warning(f"IP_TRANSPARENT failed: transparent proxying may not work: {e}")
    server.settimeout(1)
    server.bind((LISTEN_ADDR, LISTEN_PORT))
    server.listen(128)

    log.info(f"Listening on {LISTEN_ADDR}:{LISTEN_PORT}")

    while running.is_set():
        try:
            client_sock, client_addr = server.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr),
                daemon=True,
            )
            t.start()
        except socket.timeout:
            continue
        except Exception as e:
            if running.is_set():
                log.error(f"Accept error: {e}")

    server.close()
    try:
        os.remove(pid_file)
    except Exception:
        pass
    log.info("Stopped")


if __name__ == "__main__":
    main()
