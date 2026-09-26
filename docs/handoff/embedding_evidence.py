import sys
sys.path.insert(0,"/opt/kai-legal-brain")
from core.legal.embeddings import EmbeddingIndex
from legal_brain_api import engine
idx = EmbeddingIndex(engine().storage)
q = "director duties"
qv = idx.client.embed(q) if hasattr(idx,"client") else None
print("query vec dim:", len(qv) if qv else None)
# cosine vs a real company-law passage and an unrelated one
import math
def cos(a,b):
    d=sum(x*y for x,y in zip(a,b)); na=math.sqrt(sum(x*x for x in a)); nb=math.sqrt(sum(y*y for y in b))
    return d/(na*nb) if na and nb else 0
texts = {
 "companies act (directors)": "A director of a company shall act in accordance with the constitution of the company and exercise powers for a proper purpose.",
 "fisheries act": "The Commission may regulate fishing vessels and the landing of fish caught in Ghanaian waters.",
}
for label, t in texts.items():
    tv = idx.client.embed(t) if hasattr(idx,"client") else None
    if qv and tv: print("  cos(%s) = %.4f" % (label, cos(qv,tv)))
