import sys
sys.path.insert(0,"/opt/kai-legal-brain")
from legal_brain_api import engine
from core.legal.hybrid import hybrid_search
from core.legal.embeddings import EmbeddingIndex
st = engine().storage
idx = EmbeddingIndex(st)
for q in ["director duties","company registration"]:
    print("=== %r ===" % q)
    rows = hybrid_search(q, limit=5, storage=st, embed_index=idx)
    for r in rows:
        print("   id=%s strat=%s bm25=%s dense=%s score=%s %r" % (
            r.get("id"), r.get("match_strategy"), r.get("bm25_rank"),
            r.get("dense_sim"), r.get("score"), str(r.get("title"))[:38]))
