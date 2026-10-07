import sys
sys.path.insert(0, "/opt/ai-orchestrator/core")
from live_graph import build_graph

def test_edges_exist():
    g = build_graph()
    assert len(g["nodes"]) > 200
    assert len(g["edges"]) > 100

def test_hub_modules():
    g = build_graph()
    deg = g["in_degree"]
    top = sorted(deg.items(), key=lambda kv: -kv[1])[:8]
    topnames = {n for n, _ in top}
    assert any(h in topnames for h in ("core:logger", "core:memory",
                                       "core:ai", "core:config"))

def test_no_self_edges():
    g = build_graph()
    assert not any(a == b for a, b, _ in g["edges"])
