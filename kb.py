"""Role-aware retrieval core: three access-control strategies + audit log over a
swappable retriever (see retrieval.py: BM25 / vector / hybrid, doc or chunk level).
"""
import json, time
from retrieval import BM25

class RoleAwareKB:
    """mode: 'no_acl' | 'post_filter' | 'pre_filter'. k counts retrieval units
    (docs at doc level, chunks at chunk level)."""
    def __init__(self, corpus_path, mode="pre_filter", k=3, refuse_below=0.0, audit_path=None, retriever=None):
        with open(corpus_path, encoding="utf-8") as f:
            corpus = json.load(f)
        self.docs = {d["id"]: d for d in corpus["documents"]}
        self.index = retriever or BM25(corpus["documents"])
        self.mode, self.k, self.refuse_below = mode, k, refuse_below
        self.audit_path = audit_path

    def allowed(self, role):
        return {i for i, d in self.docs.items() if role in d["allowed_roles"]}

    def retrieve(self, role, query):
        allowed = self.allowed(role)
        if self.mode == "pre_filter":
            hits = self.index.search(query, candidate_ids=allowed)[: self.k]
            blocked = 0
        else:
            hits = self.index.search(query)[: self.k]
            blocked = sum(1 for h in hits if h.doc not in allowed)
            if self.mode == "post_filter":
                hits = [h for h in hits if h.doc in allowed]
        refused = not hits or max(h.gate for h in hits) < self.refuse_below
        docs = list(dict.fromkeys(h.doc for h in hits))
        result = {"role": role, "query": query, "mode": self.mode,
                  "returned": [] if refused else docs,
                  "chunks": [] if refused else [h.chunk for h in hits],
                  "scores": [round(h.score, 4) for h in hits],
                  "blocked_by_acl": blocked, "refused": refused}
        self._audit(result)
        return result

    def _audit(self, rec):
        if self.audit_path:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}) + "\n")
