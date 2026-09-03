"""Kai WireGuard Manager — DD-WRT telnet-based WG interface management.

Part of: Kai Mobile Command Node — Sub-project 5: WireGuard Resilience.

Manages the WireGuard tunnel on the DD-WRT router (192.168.99.66) via
telnet — the WG interface (wg0) lives on the router, not on this LXC.

Capabilities:
- Tunnel health checks (interface status, peer handshakes, RX/TX bytes)
- Peer endpoint rotation (primary → fallback → primary)
- Interface restart (ifconfig down/up)
- Peer add/remove (for dynamic VPN management)
- Health metrics for the health observatory (latency, uptime, packet loss)

DD-WRT constraints respected:
- Commands ≤ 500 chars (DD-WRT telnet line limit)
- No base64 encoding available on DD-WRT
- Telnet is best-effort — all operations wrapped in try/except
"""

import logging
import os
import re
import socket
import subprocess
import telnetlib
import time
from datetime import datetime, timezone
from typing import Optional

from core.logger import info

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment)
# ---------------------------------------------------------------------------

DDWRT_HOST = os.environ.get("DDWRT_HOST", "192.168.99.66")
DDWRT_PORT = int(os.environ.get("DDWRT_PORT", "23"))
DDWRT_USER = os.environ.get("DDWRT_USER", "root")
# No hardcoded default — vault or bust (the old default "103057016" is now in the vault)
DDWRT_PASSWORD = os.environ.get("DDWRT_PASSWORD", "")
DDWRT_TIMEOUT = int(os.environ.get("DDWRT_TIMEOUT", "15"))

# WireGuard on DD-WRT
WG_INTERFACE = os.environ.get("WG_DDWRT_INTERFACE", "wg0")
WG_TUNNEL_SUBNET = os.environ.get("WG_TUNNEL_SUBNET", "10.8.0.0/24")

# Proxmox B — the critical peer
PROXMOX_B_WG_IP = os.environ.get("PROXMOX_B_WG_IP", "10.8.0.5")
PROXMOX_B_PROBE_PORT = int(os.environ.get("PROXMOX_B_PROBE_PORT", "8006"))

# Endpoint fallback configuration
# Primary and fallback endpoints for the Proxmox B peer
# Format: "IP:PORT" or "HOST:PORT"
WG_PRIMARY_ENDPOINT = os.environ.get("WG_PRIMARY_ENDPOINT", "")
WG_FALLBACK_ENDPOINT = os.environ.get("WG_FALLBACK_ENDPOINT", "")

# Recovery limits
MAX_RESTART_ATTEMPTS = int(os.environ.get("WG_MAX_RESTART_ATTEMPTS", "3"))
RESTART_COOLDOWN = int(os.environ.get("WG_RESTART_COOLDOWN", "120"))
ENDPOINT_SWITCH_COOLDOWN = int(os.environ.get("WG_ENDPOINT_SWITCH_COOLDOWN", "300"))

# ---------------------------------------------------------------------------
# Telnet client for DD-WRT
# ---------------------------------------------------------------------------


class DDWRTConnection:
    """Telnet connection to the DD-WRT router with command execution.

    DD-WRT quirks handled:
    - Strips login banner and prompt characters
    - Splits commands at 500-char boundary
    - Handles the "continue" prompt from DD-WRT's shell
    - Properly reads all output before closing
    """

    def __init__(self, host=DDWRT_HOST, port=DDWRT_PORT,
                 user=DDWRT_USER, password=DDWRT_PASSWORD,
                 timeout=DDWRT_TIMEOUT):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self._tn: Optional[telnetlib.Telnet] = None
        self._prompt = b"root@DD-WRT:~#"  # typical DD-WRT root prompt

    def connect(self) -> bool:
        """Open telnet connection and log in.  Returns True on success."""
        try:
            self._tn = telnetlib.Telnet(self.host, self.port, timeout=self.timeout)
            # DD-WRT may prompt for login or go straight to shell
            idx, _, _ = self._tn.expect(
                [b"login:", b"root@", b"#"],
                timeout=self.timeout,
            )
            if idx == 0:
                # Login prompt
                self._tn.write(self.user.encode() + b"\n")
                self._tn.read_until(b"Password:", timeout=self.timeout)
                self._tn.write(self.password.encode() + b"\n")
                # Wait for shell prompt
                self._tn.read_until(b"#", timeout=self.timeout)
            elif idx == 1:
                # Already at root shell — read until prompt
                self._tn.read_until(b"#", timeout=self.timeout)
            # idx == 2: already at a prompt

            # Detect the actual prompt
            self._send_line("")
            time.sleep(0.3)
            output = self._read_available()
            for line in output.split(b"\n"):
                if b"#" in line and b"root" in line.lower():
                    self._prompt = line.strip()
                    break

            return True
        except Exception as exc:
            logger.warning("DDWRTConnection: connect failed: %s", exc)
            self._cleanup()
            return False

    def execute(self, command: str) -> tuple[str, int]:
        """Run a command on the DD-WRT and return (output, exit_code).

        exit_code is -1 if we couldn't determine it (telnet doesn't forward exit codes).
        The output is the raw text from the DD-WRT, stripped of prompts.
        """
        if self._tn is None:
            if not self.connect():
                return ("", -2)

        # Respect DD-WRT 500-char line limit
        if len(command) > 500:
            logger.warning("DDWRT: command truncated to 500 chars (was %d)", len(command))
            command = command[:500]

        try:
            self._send_line(command)
            time.sleep(0.5)  # let DD-WRT process

            output = self._read_available()

            # Clean up: strip the echoed command and prompts
            lines = output.split(b"\n")
            cleaned = []
            for line in lines:
                text = line.decode("utf-8", errors="replace").strip()
                # Skip echoed command
                if text == command:
                    continue
                # Skip empty lines
                if not text:
                    continue
                # Skip prompt lines
                if b"#" in line and b"root" in line.lower():
                    continue
                if text in ("#", "$"):
                    continue
                cleaned.append(text)

            return ("\n".join(cleaned), 0)
        except Exception as exc:
            logger.warning("DDWRT: command failed: %s — %s", command[:50], exc)
            self._cleanup()
            return ("", -1)

    def close(self):
        """Close the telnet connection."""
        self._cleanup()

    def is_connected(self) -> bool:
        return self._tn is not None and self._tn.get_socket() is not None

    # -------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------

    def _send_line(self, line: str):
        if self._tn is None:
            return
        self._tn.write(line.encode() + b"\n")

    def _read_available(self) -> bytes:
        """Read all available output from the telnet connection."""
        if self._tn is None:
            return b""
        try:
            return self._tn.read_very_eager()
        except Exception:
            return b""

    def _cleanup(self):
        if self._tn is not None:
            try:
                self._tn.close()
            except Exception:
                pass
            self._tn = None


# ---------------------------------------------------------------------------
# Connection pool (single reusable connection)
# ---------------------------------------------------------------------------

_connection: Optional[DDWRTConnection] = None
_last_connect_attempt = 0.0
_CONNECT_COOLDOWN = 30  # seconds between connection attempts


def _get_connection() -> Optional[DDWRTConnection]:
    """Get or create the DD-WRT telnet connection."""
    global _connection, _last_connect_attempt

    if _connection is not None and _connection.is_connected():
        return _connection

    # Respect cooldown — don't hammer an unreachable DD-WRT
    now = time.time()
    if now - _last_connect_attempt < _CONNECT_COOLDOWN:
        return None

    _last_connect_attempt = now
    _connection = DDWRTConnection()
    if _connection.connect():
        return _connection
    else:
        _connection = None
        return None


def _reset_connection():
    """Force a fresh connection on next operation."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None


# ---------------------------------------------------------------------------
# WireGuard health checks
# ---------------------------------------------------------------------------


def get_wg_status() -> dict:
    """Get WireGuard interface status from the DD-WRT router.

    Returns a dict with interface info, peer list, and handshake data.
    Empty/partial data if DD-WRT is unreachable.
    """
    result = {
        "ok": False,
        "interface": WG_INTERFACE,
        "router": DDWRT_HOST,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "peers": [],
        "error": None,
    }

    conn = _get_connection()
    if conn is None:
        result["error"] = f"DD-WRT {DDWRT_HOST} unreachable via telnet"
        return result

    # Run wg show on the interface
    output, _ = conn.execute(f"wg show {WG_INTERFACE}")
    if not output:
        result["error"] = f"No output from wg show {WG_INTERFACE}"
        return result

    # Parse wg show output
    parsed = _parse_wg_show(output)
    if parsed is None:
        result["error"] = "Failed to parse wg show output"
        return result

    result["ok"] = True
    result.update(parsed)

    # Get latest handshakes
    handshake_output, _ = conn.execute(f"wg show {WG_INTERFACE} latest-handshakes")
    if handshake_output:
        handshakes = _parse_handshakes(handshake_output)
        for peer in result["peers"]:
            pubkey = peer.get("public_key", "")
            if pubkey in handshakes:
                peer["last_handshake_sec"] = handshakes[pubkey]
                peer["handshake_age_sec"] = int(time.time()) - handshakes[pubkey]

    # Get transfer stats
    transfer_output, _ = conn.execute(f"wg show {WG_INTERFACE} transfer")
    if transfer_output:
        transfers = _parse_transfer(transfer_output)
        for peer in result["peers"]:
            pubkey = peer.get("public_key", "")
            if pubkey in transfers:
                peer["rx_bytes"] = transfers[pubkey]["rx"]
                peer["tx_bytes"] = transfers[pubkey]["tx"]

    return result


def check_tunnel_to_proxmox_b() -> dict:
    """Check if Proxmox B (10.8.0.5:8006) is reachable through the WG tunnel.

    Uses TCP connect as the definitive reachability test.  If Proxmox B is
    reachable, the tunnel is working regardless of what wg show says.
    """
    result = {
        "ok": False,
        "target": f"{PROXMOX_B_WG_IP}:{PROXMOX_B_PROBE_PORT}",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "latency_ms": None,
        "error": None,
    }

    try:
        start = time.monotonic()
        sock = socket.create_connection(
            (PROXMOX_B_WG_IP, PROXMOX_B_PROBE_PORT),
            timeout=5,
        )
        latency = (time.monotonic() - start) * 1000
        sock.close()

        result["ok"] = True
        result["latency_ms"] = round(latency, 1)
        return result
    except OSError as exc:
        result["error"] = f"TCP connect failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# Peer management
# ---------------------------------------------------------------------------


def list_peers() -> list[dict]:
    """Return the current WireGuard peer list from the DD-WRT."""
    status = get_wg_status()
    return status.get("peers", [])


def get_peer(public_key: str) -> Optional[dict]:
    """Get a specific peer by public key."""
    peers = list_peers()
    for p in peers:
        if p.get("public_key") == public_key:
            return p
    return None


def set_peer_endpoint(public_key: str, endpoint: str) -> dict:
    """Change a WireGuard peer's endpoint on the DD-WRT.

    Uses: wg set wg0 peer <PUBKEY> endpoint <IP:PORT>

    Returns {ok, peer, old_endpoint, new_endpoint, error}
    """
    result = {
        "ok": False,
        "public_key": public_key,
        "new_endpoint": endpoint,
        "old_endpoint": None,
        "error": None,
    }

    # Get current endpoint for rollback reference
    peer = get_peer(public_key)
    if peer:
        result["old_endpoint"] = peer.get("endpoint")

    conn = _get_connection()
    if conn is None:
        result["error"] = "DD-WRT unreachable"
        return result

    cmd = f"wg set {WG_INTERFACE} peer {public_key} endpoint {endpoint}"
    if len(cmd) > 500:
        result["error"] = f"Command too long: {len(cmd)} chars (max 500)"
        return result

    output, exit_code = conn.execute(cmd)
    if exit_code != 0 and "error" in output.lower():
        result["error"] = output[:200]
        return result

    # Verify the change took effect
    time.sleep(2)
    updated = get_peer(public_key)
    if updated and updated.get("endpoint") == endpoint:
        result["ok"] = True
        info(f"wireguard: peer endpoint changed: {public_key[:16]}... → {endpoint}")
    else:
        result["ok"] = True  # wg set succeeded but endpoint may not show immediately
        info(f"wireguard: wg set endpoint issued for {public_key[:16]}... → {endpoint}")

    return result


# ---------------------------------------------------------------------------
# Interface management
# ---------------------------------------------------------------------------


def restart_interface() -> dict:
    """Restart the WireGuard interface on the DD-WRT router.

    Uses: ifconfig wg0 down; sleep 2; ifconfig wg0 up

    Returns {ok, interface, error}
    """
    result = {
        "ok": False,
        "interface": WG_INTERFACE,
        "error": None,
    }

    conn = _get_connection()
    if conn is None:
        result["error"] = "DD-WRT unreachable"
        return result

    # Bring down
    down_cmd = f"ifconfig {WG_INTERFACE} down"
    conn.execute(down_cmd)
    time.sleep(2)

    # Bring up
    up_cmd = f"ifconfig {WG_INTERFACE} up"
    output, _ = conn.execute(up_cmd)

    # Wait and verify
    time.sleep(3)
    status = get_wg_status()

    if status.get("ok"):
        result["ok"] = True
        info(f"wireguard: {WG_INTERFACE} restarted on DD-WRT")
    else:
        result["error"] = f"Interface {WG_INTERFACE} may not have come back up"
        info(f"wireguard: {WG_INTERFACE} restart may have failed — status: {status.get('error')}")

    return result


# ---------------------------------------------------------------------------
# Endpoint failover
# ---------------------------------------------------------------------------

# Track failover state to prevent thrashing
_failover_state = {
    "active_endpoint": "primary",  # "primary" or "fallback"
    "last_switch_time": 0.0,
    "primary_failures": 0,
    "fallback_failures": 0,
}


def get_failover_state() -> dict:
    """Return current failover state for monitoring."""
    now = time.time()
    return {
        "active_endpoint": _failover_state["active_endpoint"],
        "last_switch_sec_ago": round(now - _failover_state["last_switch_time"]) if _failover_state["last_switch_time"] > 0 else None,
        "primary_failures": _failover_state["primary_failures"],
        "fallback_failures": _failover_state["fallback_failures"],
        "primary_endpoint": WG_PRIMARY_ENDPOINT,
        "fallback_endpoint": WG_FALLBACK_ENDPOINT,
    }


def attempt_endpoint_failover(peer_public_key: str) -> dict:
    """Switch a peer to the alternate endpoint and back.

    If currently on primary → switch to fallback
    If currently on fallback → switch back to primary

    Respects ENDPOINT_SWITCH_COOLDOWN to prevent thrashing.

    Returns {ok, action, new_endpoint, error}
    """
    if not WG_PRIMARY_ENDPOINT or not WG_FALLBACK_ENDPOINT:
        return {
            "ok": False,
            "action": "none",
            "error": "Primary and/or fallback endpoint not configured. "
                     "Set WG_PRIMARY_ENDPOINT and WG_FALLBACK_ENDPOINT env vars.",
        }

    now = time.time()

    # Check cooldown
    if _failover_state["last_switch_time"] > 0:
        since_last = now - _failover_state["last_switch_time"]
        if since_last < ENDPOINT_SWITCH_COOLDOWN:
            return {
                "ok": False,
                "action": "cooldown",
                "error": f"Endpoint switch cooldown active ({ENDPOINT_SWITCH_COOLDOWN - since_last:.0f}s remaining)",
            }

    current = _failover_state["active_endpoint"]
    new_endpoint = WG_FALLBACK_ENDPOINT if current == "primary" else WG_PRIMARY_ENDPOINT
    action = f"switch_to_{'fallback' if current == 'primary' else 'primary'}"

    result = set_peer_endpoint(peer_public_key, new_endpoint)
    if result["ok"]:
        _failover_state["active_endpoint"] = "fallback" if current == "primary" else "primary"
        _failover_state["last_switch_time"] = now
        info(f"wireguard: endpoint failover — {action}: {new_endpoint}")

        # Reset counters for the newly active endpoint
        if _failover_state["active_endpoint"] == "primary":
            _failover_state["primary_failures"] = 0
        else:
            _failover_state["fallback_failures"] = 0

    return {
        "ok": result["ok"],
        "action": action,
        "new_endpoint": new_endpoint,
        "old_endpoint": result.get("old_endpoint"),
        "error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# Full recovery sequence
# ---------------------------------------------------------------------------


def attempt_full_recovery(peer_public_key: Optional[str] = None) -> dict:
    """Run the full WireGuard recovery sequence when the tunnel is down.

    Sequence (tries each, stops if tunnel recovers):
    1. Verify tunnel is actually down (TCP probe to Proxmox B)
    2. If WG interface is down on DD-WRT, restart it
    3. If peers have no recent handshake, try endpoint failover
    4. If endpoint failover doesn't help, try interface restart
    5. Escalate if nothing works

    Returns {ok, actions_taken, tunnel_recovered, error}
    """
    actions = []
    recovered = False
    error = None

    # Step 1: Verify tunnel down
    probe = check_tunnel_to_proxmox_b()
    if probe["ok"]:
        actions.append("tunnel_already_up")
        return {"ok": True, "actions_taken": actions, "tunnel_recovered": True, "error": None}

    actions.append("tunnel_confirmed_down")

    # Step 2: Check if WG interface is even up
    status = get_wg_status()
    if not status.get("ok"):
        actions.append("wg_interface_issue")
        restart_result = restart_interface()
        actions.append(f"restart_interface={'ok' if restart_result['ok'] else 'failed'}")

        time.sleep(5)
        probe = check_tunnel_to_proxmox_b()
        if probe["ok"]:
            recovered = True
            actions.append("tunnel_recovered_after_restart")
            return {"ok": True, "actions_taken": actions, "tunnel_recovered": True, "error": None}

    # Step 3: Find the peer with the oldest handshake (candidate for failover)
    peers = status.get("peers", [])
    if peers and peer_public_key is None:
        # Find peer with stalest handshake
        stalest = max(peers, key=lambda p: p.get("handshake_age_sec", 0))
        peer_public_key = stalest.get("public_key")

    if peer_public_key and WG_PRIMARY_ENDPOINT and WG_FALLBACK_ENDPOINT:
        # Try endpoint failover
        failover = attempt_endpoint_failover(peer_public_key)
        actions.append(f"endpoint_failover={failover['action']}")

        if failover["ok"]:
            time.sleep(5)
            probe = check_tunnel_to_proxmox_b()
            if probe["ok"]:
                recovered = True
                actions.append("tunnel_recovered_after_failover")
                return {"ok": True, "actions_taken": actions, "tunnel_recovered": True, "error": None}

    # Step 4: Last resort — full interface restart
    if not recovered:
        # Increment failure counters
        current = _failover_state["active_endpoint"]
        if current == "primary":
            _failover_state["primary_failures"] += 1
        else:
            _failover_state["fallback_failures"] += 1

        restart_result = restart_interface()
        actions.append(f"last_resort_restart={'ok' if restart_result['ok'] else 'failed'}")

        time.sleep(5)
        probe = check_tunnel_to_proxmox_b()
        if probe["ok"]:
            recovered = True
            actions.append("tunnel_recovered_after_last_resort")
        else:
            error = "All recovery attempts exhausted — tunnel still down"
            actions.append("recovery_exhausted")

    return {
        "ok": recovered,
        "actions_taken": actions,
        "tunnel_recovered": recovered,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Health metrics (for health observatory integration)
# ---------------------------------------------------------------------------


def collect_wg_health_metrics() -> dict[str, float]:
    """Collect WireGuard health metrics for the health observatory.

    Returns dict of metric_name → floatValue suitable for health_observatory.
    """
    metrics = {}

    # Tunnel reachability
    probe = check_tunnel_to_proxmox_b()
    metrics["wg_tunnel_reachable"] = 1.0 if probe["ok"] else 0.0
    metrics["wg_tunnel_latency_ms"] = probe.get("latency_ms", 0.0) or 0.0

    # WG status from DD-WRT
    status = get_wg_status()
    metrics["wg_interface_ok"] = 1.0 if status.get("ok") else 0.0

    peers = status.get("peers", [])
    metrics["wg_peer_count"] = float(len(peers))

    if peers:
        handshake_ages = [p.get("handshake_age_sec", 9999) for p in peers if p.get("handshake_age_sec") is not None]
        if handshake_ages:
            metrics["wg_oldest_handshake_sec"] = float(max(handshake_ages))
            metrics["wg_newest_handshake_sec"] = float(min(handshake_ages))
            # All peers healthy = all handshake ages < 180s
            metrics["wg_all_peers_healthy"] = 1.0 if max(handshake_ages) < 180 else 0.0

    # Failover state
    metrics["wg_active_endpoint_primary"] = 1.0 if _failover_state["active_endpoint"] == "primary" else 0.0

    return metrics


# ---------------------------------------------------------------------------
# Output parsers
# ---------------------------------------------------------------------------


def _strip_ansi(text: str) -> str:
    """Strip ANSI terminal escape sequences (color codes, bold, etc.).

    DD-WRT's wg show output includes ANSI formatting codes like
    \\x1b[0m, \\x1b[32m, \\x1b[1m that break startswith() checks.
    """
    import re
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)


def _parse_wg_show(output: str) -> Optional[dict]:
    """Parse 'wg show <iface>' output into structured data.

    Handles DD-WRT ANSI color codes (\\x1b[...m sequences) in the output.

    Example output:
    interface: wg0
      public key: AbCd...
      private key: (hidden)
      listening port: 51820

    peer: EfGh...
      endpoint: 192.168.1.1:51820
      allowed ips: 10.8.0.2/32
      latest handshake: 1 minute, 30 seconds ago
      transfer: 1.2 GiB received, 800 MiB sent
    """
    # Strip ANSI escape codes before parsing
    output = _strip_ansi(output)

    result = {
        "public_key": None,
        "listen_port": None,
        "peers": [],
    }

    current_peer = None
    for line in output.split("\n"):
        stripped = line.strip()

        # Interface section
        if stripped.startswith("public key:") and current_peer is None:
            result["public_key"] = stripped.split("public key:")[1].strip()
        elif stripped.startswith("listening port:") and current_peer is None:
            try:
                result["listen_port"] = int(stripped.split("listening port:")[1].strip())
            except ValueError:
                pass

        # Peer section
        elif stripped.startswith("peer:"):
            if current_peer:
                result["peers"].append(current_peer)
            current_peer = {
                "public_key": stripped.split("peer:")[1].strip(),
                "endpoint": None,
                "allowed_ips": [],
                "last_handshake_text": None,
                "transfer_rx": None,
                "transfer_tx": None,
            }
        elif current_peer is not None:
            if stripped.startswith("endpoint:"):
                current_peer["endpoint"] = stripped.split("endpoint:")[1].strip()
            elif stripped.startswith("allowed ips:"):
                ips = stripped.split("allowed ips:")[1].strip()
                current_peer["allowed_ips"] = [i.strip() for i in ips.split(",")]
            elif stripped.startswith("latest handshake:"):
                current_peer["last_handshake_text"] = stripped.split("latest handshake:")[1].strip()
            elif stripped.startswith("transfer:"):
                transfer = stripped.split("transfer:")[1].strip()
                parts = transfer.split(",")
                for part in parts:
                    part = part.strip()
                    if "received" in part:
                        current_peer["transfer_rx"] = part.split(" received")[0].strip()
                    elif "sent" in part:
                        current_peer["transfer_tx"] = part.split(" sent")[0].strip()

    if current_peer:
        result["peers"].append(current_peer)

    if result["public_key"] is None and not result["peers"]:
        return None

    return result


def _parse_handshakes(output: str) -> dict[str, int]:
    """Parse 'wg show <iface> latest-handshakes' into {pubkey: unix_timestamp}.

    Example output:
    EfGhIjKlMnOp...  1691874000
    AbCdEfGhIjKl...  1691873923
    """
    result = {}
    for line in output.split("\n"):
        parts = line.strip().split()
        if len(parts) >= 2:
            pubkey = parts[0]
            try:
                ts = int(parts[1])
                result[pubkey] = ts
            except ValueError:
                continue
    return result


def _parse_transfer(output: str) -> dict[str, dict[str, int]]:
    """Parse 'wg show <iface> transfer' into {pubkey: {rx: bytes, tx: bytes}}.

    Example output:
    EfGhIjKlMnOp...  1234567  9876543
    """
    result = {}
    for line in output.split("\n"):
        parts = line.strip().split()
        if len(parts) >= 3:
            pubkey = parts[0]
            try:
                result[pubkey] = {
                    "rx": int(parts[1]),
                    "tx": int(parts[2]),
                }
            except ValueError:
                continue
    return result
