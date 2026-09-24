"""Tests for the on-host WireGuard agent (): parsing, IP allocation,
config rendering, and the file-mutating operations with an injected runner.

No `wg` binary or root is required: `wg` calls and key generation are injected.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core import wg_agent as A  # noqa: E402

PUB_A = "A" * 43 + "="
PUB_B = "B" * 43 + "="
PUB_C = "C" * 43 + "="
PUB_NEW = "N" * 43 + "="
SERVER_PRIV = "SERVERPRIVATEKEYMATERIAL000000000000000000="
DUMP = ("wg0\t" + SERVER_PRIV + "\t" + "S" * 43 + "=\t51860\toff\n"
        f"wg0\t{PUB_A}\t(none)\t1.2.3.4:5\t10.6.0.2/32,10.6.0.3/32\t1700000000\t100\t200\t25\n")

CONF = f"""[Interface]
Address = 10.6.0.1/24
SaveConfig = true
ListenPort = 51860
PrivateKey = {SERVER_PRIV}

[Peer]
PublicKey = {PUB_A}
AllowedIPs = 10.6.0.2/32, 10.6.0.3/32
Endpoint = 1.2.3.4:5

[Peer]
PublicKey = {PUB_B}
AllowedIPs = 10.6.0.4/32, 10.6.0.5/32

[Peer]
PublicKey = {PUB_C}
AllowedIPs = 10.6.0.6/32, 10.6.0.7/32
"""


class FakeRun:
    def __init__(self, dump=DUMP):
        self.calls = []
        self.dump = dump

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        r = R()
        if cmd[:3] == ["wg", "show", "wg0"]:
            r.stdout = self.dump
        return r


@pytest.fixture
def agent(tmp_path):
    conf = tmp_path / "wg0.conf"
    conf.write_text(CONF)
    os.chmod(conf, 0o600)
    fake = FakeRun()

    def keygen():
        return ("CLIENTPRIV" + "0" * 33 + "=", PUB_NEW)

    ag = A.Agent(conf=str(conf), meta=str(tmp_path / "kai_peers.json"),
                 backup_dir=str(tmp_path / "backups"),
                 audit=str(tmp_path / "audit.log"),
                 lock=str(tmp_path / "lock"), wgd_db=str(tmp_path / "none.db"),
                 run=fake, keygen=keygen)
    ag._fake = fake
    return ag


# ── parsing ───────────────────────────────────────────────────────────────

def test_parse_conf_interface_and_peers():
    p = A.parse_conf(CONF)
    assert p["interface"]["addresses"] == ["10.6.0.1/24"]
    assert p["interface"]["listen_port"] == 51860
    assert p["interface"]["private_key"] == SERVER_PRIV
    assert [x["public_key"] for x in p["peers"]] == [PUB_A, PUB_B, PUB_C]
    assert p["peers"][0]["allowed_ips"] == ["10.6.0.2/32", "10.6.0.3/32"]


def test_parse_conf_captures_device_comment():
    text = CONF + "\n# kai-device: Phone\n[Peer]\nPublicKey = " + PUB_NEW + "\nAllowedIPs = 10.6.0.8/32\n"
    peers = A.parse_conf(text)["peers"]
    assert peers[-1]["comment"] == "# kai-device: Phone"


# ── allocation ────────────────────────────────────────────────────────────

def test_allocate_skips_used_server_and_reserved():
    used = {"10.6.0.2/32", "10.6.0.3/32", "10.6.0.4/32", "10.6.0.5/32",
            "10.6.0.6/32", "10.6.0.7/32"}
    assert A.allocate_ip(used) == "10.6.0.8/32"
    assert A.allocate_ip({"10.6.0.2/32"}) == "10.6.0.3/32"
    assert A.allocate_ip(set()) == "10.6.0.2/32"


def test_allocate_exhausted_raises():
    used = {f"10.6.0.{i}/32" for i in range(1, 3)}
    with pytest.raises(A.AgentError):
        A.allocate_ip(used, pool="10.6.0.0/30")


# ── rendering ─────────────────────────────────────────────────────────────

def test_render_wg_config_has_required_directives():
    c = A.render_wg_config("PVK=", "10.6.0.8/32", "S.PUB=")
    assert "[Interface]" in c and "[Peer]" in c
    assert "PrivateKey = PVK=" in c
    assert "Address = 10.6.0.8/32" in c
    assert "PublicKey = S.PUB=" in c
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in c
    assert "Endpoint = 162.195.35.152:51860" in c
    assert "PersistentKeepalive = 25" in c


def test_render_ddwrt_config_has_ui_values_and_commands():
    c = A.render_ddwrt_config("PVK=", "10.6.0.8", "S.PUB=")
    assert "DD-WRT" in c
    assert "Peer/Endpoint: 162.195.35.152:51860" in c
    assert "Peer/PersistentKeepalive: 25" in c
    assert "AllowedIPs: 0.0.0.0/0, ::/0" in c
    assert "wg set wg0 peer S.PUB=" in c


def test_render_openwrt_config_has_uci_and_snippet():
    c = A.render_openwrt_config("PVK=", "10.6.0.8/32", "S.PUB=")
    assert "config interface 'kaidev'" in c
    assert "option proto 'wireguard'" in c
    assert "option private_key 'PVK='" in c
    assert "list addresses '10.6.0.8/32'" in c
    assert "option endpoint_host '162.195.35.152'" in c
    assert "option endpoint_port '51860'" in c
    assert "list allowed_ips '0.0.0.0/0'" in c
    assert "list allowed_ips '::/0'" in c
    assert "uci add_list network.kaidev.addresses='10.6.0.8/32'" in c


def test_render_client_config_dispatch_and_invalid():
    assert "[Peer]" in A.render_client_config("wg", private_key="x", address="10.6.0.8", server_pubkey="s")
    assert "DD-WRT" in A.render_client_config("ddwrt", private_key="x", address="10.6.0.8", server_pubkey="s")
    assert "config interface" in A.render_client_config("openwrt", private_key="x", address="10.6.0.8", server_pubkey="s")
    with pytest.raises(A.AgentError):
        A.render_client_config("nope", private_key="x", address="y", server_pubkey="s")


# ── live dump parsing (private key must never surface) ────────────────────

def test_parse_wg_dump_never_returns_interface_private_key():
    parsed = A.parse_wg_dump(DUMP)
    blob = json.dumps(parsed)
    assert SERVER_PRIV not in blob
    assert parsed["interface_public_key"] == "S" * 43 + "="
    assert parsed["listen_port"] == 51860
    assert parsed["peers"][PUB_A]["rx_bytes"] == 100


def test_parse_wg_dump_iface_only_form():
    # `wg show wg0 dump` (no interface-name column) — the form the agent uses.
    dump = (SERVER_PRIV + "\t" + "S" * 43 + "=\t51860\toff\n"
            f"{PUB_A}\t(none)\t1.2.3.4:5\t10.6.0.2/32\t0\t0\t0\toff\n")
    parsed = A.parse_wg_dump(dump)
    assert parsed["interface_public_key"] == "S" * 43 + "="
    assert parsed["listen_port"] == 51860
    assert SERVER_PRIV not in json.dumps(parsed)


# ── operations ────────────────────────────────────────────────────────────

def test_add_allocates_free_ip_backs_up_and_applies(agent):
    r = agent.add_device("Test Phone")
    assert r["ip"] == "10.6.0.8"
    assert r["pubkey"] == PUB_NEW
    # config contains this client's private key + server pubkey, and nothing secret else
    assert "CLIENTPRIV" in r["config"] and "S" * 43 + "=" in r["config"]
    text = Path(agent.conf).read_text()
    assert "# kai-device: Test Phone" in text and PUB_NEW in text
    # backup taken
    backups = list(Path(agent.backup_dir).glob("*.bak"))
    assert len(backups) == 1 and backups[0].read_text() == CONF
    # live apply targeted only the new peer
    assert ["wg", "set", "wg0", "peer", PUB_NEW, "allowed-ips", "10.6.0.8/32"] in agent._fake.calls
    # meta persisted with 0600
    meta = json.loads(Path(agent.meta).read_text())
    assert meta[PUB_NEW]["private_key"].startswith("CLIENTPRIV")
    assert stat.S_IMODE(os.stat(agent.meta).st_mode) == 0o600


def test_add_duplicate_ip_rejected(agent):
    with pytest.raises(A.AgentError):
        agent.add_device("Dup", ip="10.6.0.2")


def test_add_invalid_name_rejected(agent):
    with pytest.raises(A.AgentError):
        agent.add_device("bad/name")


def test_list_never_exposes_private_keys(agent):
    agent.add_device("Phone")
    body = agent.list_devices()
    blob = json.dumps(body)
    assert "private_key" not in blob
    assert SERVER_PRIV not in blob
    assert any(p["pubkey"] == PUB_NEW for p in body["peers"])
    assert body["interface_public_key"] == "S" * 43 + "="


def test_pause_removes_block_and_marks_paused(agent):
    agent.add_device("Phone")
    agent.pause_device(PUB_NEW)
    assert PUB_NEW not in Path(agent.conf).read_text()
    assert ["wg", "set", "wg0", "peer", PUB_NEW, "remove"] in agent._fake.calls
    meta = json.loads(Path(agent.meta).read_text())
    assert meta[PUB_NEW]["paused"] is True
    # paused peer still listed (record not lost)
    assert any(p["pubkey"] == PUB_NEW and p["paused"] for p in agent.list_devices()["peers"])
    assert len(list(Path(agent.backup_dir).glob("*.bak"))) == 2


def test_resume_readds_block(agent):
    agent.add_device("Phone")
    agent.pause_device(PUB_NEW)
    agent.resume_device(PUB_NEW)
    assert PUB_NEW in Path(agent.conf).read_text()
    meta = json.loads(Path(agent.meta).read_text())
    assert meta[PUB_NEW]["paused"] is False


def test_delete_removes_block_and_record(agent):
    agent.add_device("Phone")
    agent.delete_device(PUB_NEW)
    assert PUB_NEW not in Path(agent.conf).read_text()
    assert PUB_NEW not in json.loads(Path(agent.meta).read_text())
    assert not any(p["pubkey"] == PUB_NEW for p in agent.list_devices()["peers"])


def test_existing_peers_untouched_by_add(agent):
    agent.add_device("Phone")
    text = Path(agent.conf).read_text()
    for pub in (PUB_A, PUB_B, PUB_C):
        assert pub in text


def test_config_for_unmanaged_peer_errors(agent):
    with pytest.raises(A.AgentError):
        agent.peer_config(PUB_A, "wg")


def test_config_export_for_added_peer(agent):
    agent.add_device("Phone")
    cfg = agent.peer_config(PUB_NEW, "openwrt")
    assert "config interface 'kaidev'" in cfg["config"]
    with pytest.raises(A.AgentError):
        agent.peer_config(PUB_NEW, "bogus")


def test_dispatch_unknown_op(agent):
    with pytest.raises(A.AgentError):
        A.dispatch(agent, {"op": "frobnicate"})


def test_audit_log_written(agent):
    agent.add_device("Phone")
    lines = Path(agent.audit).read_text().strip().splitlines()
    assert any(json.loads(l)["action"] == "add" for l in lines)
