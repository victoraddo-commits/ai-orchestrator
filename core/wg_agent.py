"""Kai WireGuard peer agent — runs on the WireGuard server host (CT102).

Single-file, standard-library only. It is invoked by the CT111 Command Center
controller over the existing key-based SSH chain::

    CT111 -> PVE-B -> PVE-A -> pct exec 102 -- python3 /opt/kai-wg-agent/wg_agent.py <b64(json)>

It prints exactly one JSON object on stdout. Design rules:

* The **server private key is never emitted** (the ``wg show dump`` interface
  line contains it, so we parse only the public key / port / peer fields).
* A client's private key appears **only** inside that client's own rendered
  config response.
* Every write takes a timestamped backup of ``wg0.conf`` first and is appended
  to a JSONL audit log.
* Operations are serialised with ``flock`` so two concurrent requests cannot
  interleave a read-modify-write of the config.

The pure helpers (``parse_conf``, ``allocate_ip``, ``render_*_config``) carry no
I/O and are unit-tested directly.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Environment-driven paths / parameters
# ---------------------------------------------------------------------------

WG_CONF = os.environ.get("WG_CONF", "/etc/wireguard/wg0.conf")
WG_IFACE = os.environ.get("WG_IFACE", "wg0")
WG_META = os.environ.get("WG_META", "/etc/wireguard/kai_peers.json")
WG_BACKUP_DIR = os.environ.get("WG_BACKUP_DIR", "/etc/wireguard/backups")
WG_AUDIT = os.environ.get("WG_AUDIT", "/var/log/kai-wg-agent-audit.log")
WG_LOCK = os.environ.get("WG_LOCK", "/etc/wireguard/.kai-wg-agent.lock")
WG_POOL = os.environ.get("WG_POOL", "10.6.0.0/24")
WG_SERVER_IP = os.environ.get("WG_SERVER_IP", "10.6.0.1")
WG_REMOTE_ENDPOINT = os.environ.get("WG_REMOTE_ENDPOINT", "162.195.35.152:51860")
WG_DNS = os.environ.get("WG_DNS", "1.1.1.1, 8.8.8.8")
WG_ALLOWED_IPS = os.environ.get("WG_ALLOWED_IPS", "0.0.0.0/0, ::/0")
WG_MTU = os.environ.get("WG_MTU", "1420")
WG_KEEPALIVE = os.environ.get("WG_KEEPALIVE", "25")
WG_WGD_DB = os.environ.get(
    "WG_WGD_DB", "/etc/wgdashboard/src/db/wgdashboard.db")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,63}$")
_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[A-Za-z0-9+/=]{2}$")


class AgentError(Exception):
    """User-visible error; mapped to HTTP 400 by the controller."""


# ---------------------------------------------------------------------------
# Pure parsing / allocation helpers (no I/O)
# ---------------------------------------------------------------------------

def _split_blocks(text: str):
    """Split a wg-quick config into ordered blocks.

    Each block is ``{"header": "[Peer]"|"[Interface]"|None,
    "pre": [comment/blank lines immediately preceding the header],
    "lines": [lines from the header onward]}``. ``pre`` lets us delete a peer
    together with the ``# kai-device:`` comment above it, and re-emit it later.
    """
    blocks = []
    cur = None
    pending = []

    def _flush_pending_into(cur):
        if pending:
            cur["lines"].extend(pending)
            pending.clear()

    for line in text.splitlines(True):
        st = line.strip()
        if st.startswith("[") and st.endswith("]") and len(st) < 64:
            if cur is not None:
                blocks.append(cur)
            cur = {"header": st, "pre": list(pending), "lines": [line]}
            pending.clear()
        elif cur is None:
            pending.append(line)
        elif st == "" or st.startswith("#"):
            pending.append(line)
        else:
            _flush_pending_into(cur)
            cur["lines"].append(line)

    if cur is not None:
        _flush_pending_into(cur)
        blocks.append(cur)
    elif pending:
        blocks.append({"header": None, "pre": [], "lines": list(pending)})
    return blocks


def _kv(lines):
    out = {}
    order = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            out[k.lower()] = v.strip()
            order.append(k.lower())
    out["_order"] = order
    return out


def parse_conf(text: str) -> dict:
    """Parse a wg-quick config into ``{interface, peers}``.

    The interface private key is parsed but is deliberately not part of any
    serialised response (``_public_view`` strips it).
    """
    iface = {"addresses": [], "listen_port": None, "private_key": None}
    peers = []
    for b in _split_blocks(text):
        if b["header"] is None:
            continue
        kv = _kv(b["lines"])
        if b["header"].lower() == "[interface]":
            iface["addresses"] = [a.strip() for a in
                                  kv.get("address", "").split(",") if a.strip()]
            if kv.get("listenport"):
                try:
                    iface["listen_port"] = int(kv["listenport"])
                except ValueError:
                    pass
            iface["private_key"] = kv.get("privatekey")
        elif b["header"].lower() == "[peer]":
            allowed = [a.strip() for a in
                       kv.get("allowedips", "").split(",") if a.strip()]
            keepalive = kv.get("persistentkeepalive")
            peers.append({
                "public_key": kv.get("publickey"),
                "allowed_ips": allowed,
                "endpoint": kv.get("endpoint"),
                "persistent_keepalive": int(keepalive) if keepalive else None,
                "comment": "".join(
                    l for l in b["pre"] if l.strip().startswith("#")
                ).strip() or None,
                "_block": b,
            })
    return {"interface": iface, "peers": peers, "blocks": _split_blocks(text)}


def allocate_ip(used: set, pool: str = WG_POOL,
                server_ip: str = WG_SERVER_IP,
                start: int = 2) -> str:
    """Return the first free host address in ``pool`` as ``a.b.c.d/32``.

    Skips the network/broadcast addresses, the server address and every
    address already present in ``used`` (bare IPs). Raises AgentError when the
    pool is exhausted.
    """
    net = ipaddress.ip_network(pool, strict=False)
    used_ips = set()
    for u in used:
        try:
            used_ips.add(str(ipaddress.ip_interface(u).ip))
        except ValueError:
            continue
    server = str(ipaddress.ip_interface(server_ip).ip)
    first = net.network_address + start
    last = net.broadcast_address - 1
    candidate = first
    while candidate <= last:
        ip = str(candidate)
        if ip != server and ip not in used_ips:
            return f"{ip}/32"
        candidate += 1
    raise AgentError(f"no free address left in pool {pool}")


def _split_csv(value: str):
    return [v.strip() for v in value.split(",") if v.strip()]


def render_wg_config(private_key: str, address: str, server_pubkey: str,
                     endpoint: str = WG_REMOTE_ENDPOINT, dns: str = WG_DNS,
                     allowed_ips: str = WG_ALLOWED_IPS, mtu: str = WG_MTU,
                     keepalive: str = WG_KEEPALIVE, name: str = "") -> str:
    """Standard WireGuard ``.conf`` for a client."""
    addr = address if "/" in address else f"{address}/32"
    lines = ["[Interface]",
             f"PrivateKey = {private_key}",
             f"Address = {addr}"]
    if dns:
        lines.append(f"DNS = {dns}")
    if mtu:
        lines.append(f"MTU = {mtu}")
    lines += ["", "[Peer]",
              f"PublicKey = {server_pubkey}",
              f"AllowedIPs = {allowed_ips}",
              f"Endpoint = {endpoint}"]
    if keepalive:
        lines.append(f"PersistentKeepalive = {keepalive}")
    lines.append("")
    return "\n".join(lines)


def render_ddwrt_config(private_key: str, address: str, server_pubkey: str,
                        endpoint: str = WG_REMOTE_ENDPOINT, dns: str = WG_DNS,
                        allowed_ips: str = WG_ALLOWED_IPS, mtu: str = WG_MTU,
                        keepalive: str = WG_KEEPALIVE, name: str = "") -> str:
    """DD-WRT export.

    DD-WRT has no wg-quick: peers are entered as UI values (Setup > VPN >
    WireGuard) or applied with ``wg set`` from a startup script. We emit both
    the labelled UI values and a paste-ready startup command block.
    """
    addr = address.split("/")[0]
    ui = [
        "# DD-WRT WireGuard client export",
        "# --- UI values (Setup > VPN > WireGuard -> Add Peer) ---",
        f"Interface/PrivateKey: {private_key}",
        f"Interface/Address: {addr}",
        f"Interface/DNS: {dns}",
        f"Interface/MTU: {mtu}",
        f"Peer/PublicKey: {server_pubkey}",
        f"Peer/AllowedIPs: {allowed_ips}",
        f"Peer/Endpoint: {endpoint}",
        f"Peer/PersistentKeepalive: {keepalive}",
        "",
        "# --- startup script (Administration > Commands > Save Startup) ---",
        "#!/bin/sh",
        "WGKEY=/tmp/kai_wg.key",
        f"printf '%s' '{private_key}' > $WGKEY",
        "chmod 600 $WGKEY",
        f"wg set wg0 private-key $WGKEY",
        f"ip addr add {addr}/32 dev wg0 2>/dev/null",
        f"wg set wg0 peer {server_pubkey} "
        f"allowed-ips {allowed_ips.replace(' ', '')} endpoint {endpoint} "
        f"persistent-keepalive {keepalive}",
    ]
    return "\n".join(ui) + "\n"


def render_openwrt_config(private_key: str, address: str, server_pubkey: str,
                          endpoint: str = WG_REMOTE_ENDPOINT, dns: str = WG_DNS,
                          allowed_ips: str = WG_ALLOWED_IPS, mtu: str = WG_MTU,
                          keepalive: str = WG_KEEPALIVE, name: str = "") -> str:
    """OpenWRT export: UCI commands plus the equivalent ``/etc/config/network``.

    Requires ``kmod-wireguard`` (and ``wireguard-tools`` for diagnostics).
    """
    addr = address if "/" in address else f"{address}/32"
    host, _, port = endpoint.rpartition(":")
    iface = "kaidev"
    peer = "kaipeer"
    cmds = [
        "# OpenWRT WireGuard client export",
        "# opkg update && opkg install kmod-wireguard wireguard-tools",
        f"uci set network.{iface}=interface",
        f"uci set network.{iface}.proto='wireguard'",
        f"uci set network.{iface}.private_key='{private_key}'",
        f"uci add_list network.{iface}.addresses='{addr}'",
    ]
    for d in _split_csv(dns):
        cmds.append(f"uci add_list network.{iface}.dns='{d}'")
    cmds += [
        f"uci set network.{iface}.mtu='{mtu}'",
        f"uci set network.{peer}=wireguard_{iface}",
        f"uci set network.{peer}.description='{name or 'kai'}'",
        f"uci set network.{peer}.public_key='{server_pubkey}'",
        f"uci set network.{peer}.endpoint_host='{host}'",
        f"uci set network.{peer}.endpoint_port='{port}'",
        f"uci set network.{peer}.persistent_keepalive='{keepalive}'",
        f"uci set network.{peer}.route_allowed_ips='1'",
    ]
    for a in _split_csv(allowed_ips):
        cmds.append(f"uci add_list network.{peer}.allowed_ips='{a}'")
    cmds += ["uci commit network", "/etc/init.d/network restart", ""]

    snippet = [
        "# /etc/config/network",
        f"config interface '{iface}'",
        "\toption proto 'wireguard'",
        f"\toption private_key '{private_key}'",
        f"\tlist addresses '{addr}'",
    ]
    for d in _split_csv(dns):
        snippet.append(f"\tlist dns '{d}'")
    snippet.append(f"\toption mtu '{mtu}'")
    snippet += [
        "",
        f"config wireguard_{iface} '{peer}'",
        f"\toption description '{name or 'kai'}'",
        f"\toption public_key '{server_pubkey}'",
        f"\toption endpoint_host '{host}'",
        f"\toption endpoint_port '{port}'",
        f"\toption persistent_keepalive '{keepalive}'",
        "\toption route_allowed_ips '1'",
    ]
    for a in _split_csv(allowed_ips):
        snippet.append(f"\tlist allowed_ips '{a}'")
    snippet.append("")
    return "\n".join(cmds) + "\n" + "\n".join(snippet)


def render_client_config(fmt: str, **kw) -> str:
    fmt = (fmt or "wg").lower()
    if fmt in ("wg", "wireguard", "conf"):
        return render_wg_config(**kw)
    if fmt in ("ddwrt", "dd-wrt"):
        return render_ddwrt_config(**kw)
    if fmt in ("openwrt", "openwrt-uci", "uci"):
        return render_openwrt_config(**kw)
    raise AgentError(f"unsupported config type: {fmt!r} (use wg|ddwrt|openwrt)")


# ---------------------------------------------------------------------------
# Live status helpers
# ---------------------------------------------------------------------------

def parse_wg_dump(dump: str) -> dict:
    """Parse ``wg show <iface> dump``.

    Only the interface **public** key/port and the peer rows are returned; the
    interface private key (field 2 of the first line) is discarded so it can
    never leak. Returns ``{interface_public_key, listen_port, peers: {pub:...}}``.
    """
    iface_pub = None
    port = None
    peers = {}
    for i, raw in enumerate(dump.splitlines()):
        f = raw.split("\t")
        if i == 0:
            # `wg show all dump` → [iface, priv, pub, port, fwmark]
            # `wg show <if> dump`  → [priv, pub, port, fwmark]   (no iface name)
            if len(f) >= 5:
                iface_pub, port_field = f[2], f[3]
            elif len(f) >= 4:
                iface_pub, port_field = f[1], f[2]
            else:
                continue
            try:
                port = int(port_field)
            except ValueError:
                port = None
            continue
        if len(f) == 9:
            pub, _psk, endpoint, allowed, hs, rx, tx, keepalive = f[1:]
            peers[pub] = {
                "endpoint": endpoint if endpoint != "(none)" else None,
                "allowed_ips": [a for a in allowed.split(",") if a],
                "latest_handshake": int(hs) if hs.isdigit() else 0,
                "rx_bytes": int(rx) if rx.isdigit() else 0,
                "tx_bytes": int(tx) if tx.isdigit() else 0,
                "persistent_keepalive": keepalive if keepalive != "off" else None,
            }
    return {"interface_public_key": iface_pub, "listen_port": port, "peers": peers}


# ---------------------------------------------------------------------------
# Agent (I/O)
# ---------------------------------------------------------------------------

def _default_run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20, **kw)


class Agent:
    def __init__(self, conf=WG_CONF, iface=WG_IFACE, meta=WG_META,
                 backup_dir=WG_BACKUP_DIR, audit=WG_AUDIT, lock=WG_LOCK,
                 pool=WG_POOL, server_ip=WG_SERVER_IP, wgd_db=WG_WGD_DB,
                 run=None, keygen=None, now=None):
        self.conf = conf
        self.iface = iface
        self.meta = meta
        self.backup_dir = backup_dir
        self.audit = audit
        self.lock = lock
        self.pool = pool
        self.server_ip = server_ip
        self.wgd_db = wgd_db
        self._run = run or _default_run
        self._keygen = keygen or self._wg_keygen
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())

    # -- low level ------------------------------------------------------

    def _read_conf(self) -> str:
        with open(self.conf, "r", encoding="utf-8") as fh:
            return fh.read()

    def _backup(self) -> str:
        os.makedirs(self.backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(
            self.backup_dir, f"wg0.conf.{stamp}.{time.time_ns()}.bak")
        shutil.copy2(self.conf, dest)
        return dest

    def _audit(self, action, **fields):
        rec = {"ts": self._now(), "action": action, **fields}
        try:
            os.makedirs(os.path.dirname(self.audit), exist_ok=True)
            with open(self.audit, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def _wg(self, *args):
        r = self._run(["wg", *args])
        if r.returncode != 0:
            raise AgentError(f"wg {' '.join(args)} failed: {(r.stderr or '').strip()[:200]}")
        return (r.stdout or "").strip()

    def _wg_keygen(self):
        priv = self._wg("genkey")
        p = self._run(["wg", "pubkey"], input=priv)
        if p.returncode != 0:
            raise AgentError("wg pubkey failed")
        return priv, p.stdout.strip()

    def _live(self):
        try:
            return parse_wg_dump(self._wg("show", self.iface, "dump"))
        except AgentError:
            return {"interface_public_key": None, "listen_port": None, "peers": {}}

    # -- metadata -------------------------------------------------------

    def _load_meta(self) -> dict:
        try:
            with open(self.meta, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_meta(self, data: dict):
        os.makedirs(os.path.dirname(self.meta) or ".", exist_ok=True)
        tmp = self.meta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.meta)

    def _wgd_names(self) -> dict:
        """Read-only peer names from a local WGDashboard DB, if present."""
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{self.wgd_db}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    f"select id, name from {self.iface}").fetchall()
                return {r[0]: r[1] for r in rows if r[0]}
            finally:
                con.close()
        except Exception:
            return {}

    # -- helpers --------------------------------------------------------

    def _used_ips(self, conf_text, meta) -> set:
        used = set()
        for p in parse_conf(conf_text)["peers"]:
            used.update(p["allowed_ips"])
        for m in meta.values():
            if m.get("address"):
                used.add(m["address"])
        return used

    def _find_block_index(self, blocks, pubkey):
        for i, b in enumerate(blocks):
            if b["header"] and b["header"].lower() == "[peer]":
                kv = _kv(b["lines"])
                if kv.get("publickey") == pubkey:
                    return i
        return -1

    def _write_conf(self, blocks, existing_text):
        out = "".join("".join(b["pre"]) + "".join(b["lines"]) for b in blocks)
        if not out.endswith("\n"):
            out += "\n"
        with open(self.conf, "w", encoding="utf-8") as fh:
            fh.write(out)
        os.chmod(self.conf, 0o600)

    def _require_pubkey(self, pubkey):
        if not pubkey or not _PUBKEY_RE.match(pubkey):
            raise AgentError("invalid WireGuard public key")
        return pubkey

    # -- operations -----------------------------------------------------

    def list_devices(self) -> dict:
        text = self._read_conf()
        parsed = parse_conf(text)
        meta = self._load_meta()
        wgd = self._wgd_names()
        live = self._live()
        out = []
        for p in parsed["peers"]:
            pub = p["public_key"]
            m = meta.get(pub, {})
            livep = live["peers"].get(pub, {})
            hs = livep.get("latest_handshake", 0)
            out.append({
                "name": m.get("name") or wgd.get(pub) or "",
                "pubkey": pub,
                "address": (p["allowed_ips"][0] if p["allowed_ips"] else
                            m.get("address", "")),
                "allowed_ips": p["allowed_ips"],
                "endpoint": livep.get("endpoint") or p.get("endpoint"),
                "latest_handshake": hs,
                "rx_bytes": livep.get("rx_bytes", 0),
                "tx_bytes": livep.get("tx_bytes", 0),
                "paused": bool(m.get("paused", False)),
                "managed": pub in meta,
                "created_at": m.get("created_at"),
            })
        # paused peers live only in metadata (removed from conf)
        for pub, m in meta.items():
            if m.get("paused") and not any(d["pubkey"] == pub for d in out):
                out.append({
                    "name": m.get("name", ""),
                    "pubkey": pub,
                    "address": m.get("address", ""),
                    "allowed_ips": [m.get("address", "")],
                    "endpoint": m.get("endpoint"),
                    "latest_handshake": 0,
                    "rx_bytes": 0,
                    "tx_bytes": 0,
                    "paused": True,
                    "managed": True,
                    "created_at": m.get("created_at"),
                })
        return {
            "interface": self.iface,
            "interface_public_key": live["interface_public_key"],
            "listen_port": live["listen_port"],
            "pool": self.pool,
            "count": len(out),
            "peers": out,
        }

    def add_device(self, name: str, ip=None, dns=WG_DNS, allowed_ips=WG_ALLOWED_IPS,
                   endpoint=WG_REMOTE_ENDPOINT, mtu=WG_MTU,
                   keepalive=WG_KEEPALIVE, created_by="operator") -> dict:
        if not name or not _NAME_RE.match(name):
            raise AgentError("name must be 1-64 chars [A-Za-z0-9 space . _ ( ) -]")
        text = self._read_conf()
        parsed = parse_conf(text)
        meta = self._load_meta()
        used = self._used_ips(text, meta)
        address = (f"{ip}/32" if ip and "/" not in ip else ip) or \
            allocate_ip(used, self.pool, self.server_ip)
        if ip:
            try:
                net = ipaddress.ip_network(self.pool, strict=False)
                a = ipaddress.ip_interface(address)
                if a.ip not in net or str(a.ip) == str(ipaddress.ip_interface(self.server_ip).ip):
                    raise ValueError
            except ValueError:
                raise AgentError(f"ip {ip} not usable inside pool {self.pool}")
        if address in used:
            raise AgentError(f"ip {address} already in use")
        priv, pub = self._keygen()

        block = (f"# kai-device: {name}\n[Peer]\n"
                 f"PublicKey = {pub}\nAllowedIPs = {address}\n")
        blocks = _split_blocks(text)
        # drop any trailing non-header block artifacts, then append
        blocks.append({"header": "[Peer]", "pre": [f"# kai-device: {name}\n"],
                       "lines": [f"[Peer]\n", f"PublicKey = {pub}\n",
                                 f"AllowedIPs = {address}\n"]})

        backup = self._backup()
        self._write_conf(blocks, text)

        meta[pub] = {
            "name": name, "private_key": priv, "address": address,
            "dns": dns, "allowed_ips": allowed_ips, "endpoint": endpoint,
            "mtu": mtu, "keepalive": keepalive, "created_at": self._now(),
            "created_by": created_by, "paused": False,
        }
        # apply live without restarting the interface (existing peers untouched)
        try:
            self._wg("set", self.iface, "peer", pub, "allowed-ips", address)
        except AgentError:
            # roll back the config change if the live apply failed
            shutil.copy2(backup, self.conf)
            raise
        self._save_meta(meta)

        pubkey = pub
        try:
            interface_pub = self._live()["interface_public_key"]
        except Exception:
            interface_pub = None
        config = render_wg_config(priv, address, interface_pub or "",
                                  endpoint, dns, allowed_ips, mtu, keepalive, name)
        self._audit("add", name=name, pubkey=pub, address=address, backup=backup)
        return {"name": name, "pubkey": pub, "ip": address.split("/")[0],
                "address": address, "config": config,
                "interface_public_key": interface_pub, "backup": backup}

    def _render_for(self, meta, fmt, name):
        if not meta or not meta.get("private_key"):
            raise AgentError(
                "no stored client key for this peer (it was created outside Kai)")
        interface_pub = self._live()["interface_public_key"] or ""
        return render_client_config(
            fmt, private_key=meta["private_key"], address=meta["address"],
            server_pubkey=interface_pub, endpoint=meta.get("endpoint", WG_REMOTE_ENDPOINT),
            dns=meta.get("dns", WG_DNS), allowed_ips=meta.get("allowed_ips", WG_ALLOWED_IPS),
            mtu=meta.get("mtu", WG_MTU), keepalive=meta.get("keepalive", WG_KEEPALIVE),
            name=meta.get("name", name))

    def peer_config(self, pubkey: str, fmt: str = "wg") -> dict:
        pubkey = self._require_pubkey(pubkey)
        meta = self._load_meta().get(pubkey)
        name = (meta or {}).get("name", "")
        text = self._render_for(meta, fmt, name)
        return {"pubkey": pubkey, "type": fmt, "config": text,
                "name": name, "address": (meta or {}).get("address", "")}

    def pause_device(self, pubkey: str, by="operator") -> dict:
        pubkey = self._require_pubkey(pubkey)
        text = self._read_conf()
        blocks = _split_blocks(text)
        idx = self._find_block_index(blocks, pubkey)
        meta = self._load_meta()
        if idx < 0 and pubkey not in meta:
            raise AgentError("peer not found")
        backup = self._backup()
        if idx >= 0:
            del blocks[idx]
            self._write_conf(blocks, text)
        self._wg("set", self.iface, "peer", pubkey, "remove")
        if pubkey in meta:
            meta[pubkey]["paused"] = True
        else:
            meta[pubkey] = {"name": "", "address": "", "paused": True,
                            "created_at": self._now()}
        self._save_meta(meta)
        self._audit("pause", pubkey=pubkey, backup=backup, by=by)
        return {"ok": True, "pubkey": pubkey, "paused": True, "backup": backup}

    def resume_device(self, pubkey: str, by="operator") -> dict:
        pubkey = self._require_pubkey(pubkey)
        meta = self._load_meta()
        m = meta.get(pubkey)
        if not m or not m.get("private_key") or not m.get("address"):
            raise AgentError("cannot resume: no stored record for this peer")
        text = self._read_conf()
        blocks = _split_blocks(text)
        if self._find_block_index(blocks, pubkey) < 0:
            name = m.get("name", "")
            blocks.append({"header": "[Peer]",
                           "pre": [f"# kai-device: {name}\n"],
                           "lines": [f"[Peer]\n", f"PublicKey = {pubkey}\n",
                                     f"AllowedIPs = {m['address']}\n"]})
        backup = self._backup()
        self._write_conf(blocks, text)
        self._wg("set", self.iface, "peer", pubkey, "allowed-ips", m["address"])
        m["paused"] = False
        self._save_meta(meta)
        self._audit("resume", pubkey=pubkey, backup=backup, by=by)
        return {"ok": True, "pubkey": pubkey, "paused": False, "backup": backup}

    def delete_device(self, pubkey: str, by="operator") -> dict:
        pubkey = self._require_pubkey(pubkey)
        text = self._read_conf()
        blocks = _split_blocks(text)
        idx = self._find_block_index(blocks, pubkey)
        backup = self._backup()
        if idx >= 0:
            del blocks[idx]
            self._write_conf(blocks, text)
        try:
            self._wg("set", self.iface, "peer", pubkey, "remove")
        except AgentError:
            pass
        meta = self._load_meta()
        removed = meta.pop(pubkey, None)
        self._save_meta(meta)
        self._audit("delete", pubkey=pubkey, backup=backup, by=by,
                    had_record=bool(removed))
        return {"ok": True, "pubkey": pubkey, "deleted": True, "backup": backup}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _with_lock(agent, fn, *a, **kw):
    os.makedirs(os.path.dirname(agent.lock) or ".", exist_ok=True)
    with open(agent.lock, "w") as lf:
        try:
            import fcntl
            fcntl.flock(lf, fcntl.LOCK_EX)
        except OSError:
            pass
        return fn(*a, **kw)


def dispatch(agent: Agent, payload: dict) -> dict:
    op = payload.get("op")
    if op == "list":
        return agent.list_devices()
    if op == "add":
        return agent.add_device(payload["name"], payload.get("ip"),
                                payload.get("dns", WG_DNS),
                                payload.get("allowed_ips", WG_ALLOWED_IPS),
                                payload.get("endpoint", WG_REMOTE_ENDPOINT),
                                payload.get("mtu", WG_MTU),
                                payload.get("keepalive", WG_KEEPALIVE),
                                payload.get("created_by", "operator"))
    if op == "pause":
        return agent.pause_device(payload["pubkey"], payload.get("by", "operator"))
    if op == "resume":
        return agent.resume_device(payload["pubkey"], payload.get("by", "operator"))
    if op == "delete":
        return agent.delete_device(payload["pubkey"], payload.get("by", "operator"))
    if op == "config":
        return agent.peer_config(payload["pubkey"], payload.get("type", "wg"))
    if op == "ping":
        return {"ok": True, "iface": agent.iface, "conf": agent.conf}
    raise AgentError(f"unknown op: {op!r}")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        if not argv:
            raise AgentError("missing base64 payload")
        payload = json.loads(base64.b64decode(argv[0]).decode("utf-8"))
        agent = Agent()
        result = _with_lock(agent, dispatch, agent, payload)
        print(json.dumps({"ok": True, "data": result}))
        return 0
    except AgentError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
