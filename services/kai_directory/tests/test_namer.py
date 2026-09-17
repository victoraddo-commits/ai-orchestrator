from services.kai_directory.models import Record
from services.kai_directory.namer import (
    assign_names, policy_block, serve_commands, SUFFIX,
)


def test_assign_names_basic():
    recs = [Record(id="money", name="money"), Record(id="bet", name="bet")]
    out = assign_names(recs, proxy_for={"money": "proxmox-b", "bet": "proxmox-b"})
    assert out[0].tailnet_name == f"money.{SUFFIX}"
    assert out[0].internal_url == f"https://money.{SUFFIX}/"
    assert out[0].proxy_node == "proxmox-b"


def test_assign_names_collision_suffix():
    recs = [Record(id="money", name="money"), Record(id="money-2", name="money")]
    out = assign_names(recs, proxy_for={})
    names = [r.name for r in out]
    assert names[0] == "money"
    assert names[1] == "money-2"


def test_policy_block_shape():
    recs = [Record(id="money", name="money", tailnet_name=f"money.{SUFFIX}")]
    blk = policy_block(recs)
    assert "svc:money" in blk
    assert '"tcp:443"' in blk


def test_serve_commands_shape():
    recs = [Record(id="money", name="money", target_url="http://192.168.1.118:8095",
                   proxy_node="proxmox-b")]
    cmds = serve_commands(recs, node="proxmox-b")
    assert cmds and cmds[0] == "tailscale serve --bg --service=svc:money http://192.168.1.118:8095"
