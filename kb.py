"""Role-aware retrieval core: BM25 + three access-control strategies + audit log.

The retriever is deliberately swappable: replace `BM25.scores` with your own
hybrid (BM25 + vector, RRF) retriever and the ACL / audit / eval layers stay the same.
"""
import json, math, re, time
from collections import Counter

STOP = set("a an the of to in on for and or is are was be by with at as it its this that do does did i we our my me you your what which who how when can any there".split())

def tokenize(text):
    toks = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for t in toks:
        if t in STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(t) > 4 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.append(t)
    return out

class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.ids = [d["id"] for d in docs]
        self.toks = [tokenize(d["title"] + " " + d["text"]) for d in docs]
        self.avgdl = sum(map(len, self.toks)) / len(self.toks)
        self.k1, self.b = k1, b
        n = len(docs)
        df = Counter(t for doc in self.toks for t in set(doc))
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(doc) for doc in self.toks]

    def scores(self, query, candidate_ids=None):
        q = tokenize(query)
        res = []
        for i, did in enumerate(self.ids):
            if candidate_ids is not None and did not in candidate_ids:
                continue
            tf, dl, s = self.tf[i], len(self.toks[i]), 0.0
            for t in q:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            res.append((did, s))
        return sorted(res, key=lambda x: -x[1])

class RoleAwareKB:
    """mode: 'no_acl' | 'post_filter' | 'pre_filter'"""
    def __init__(self, corpus_path, mode="pre_filter", k=3, refuse_below=0.0, audit_path=None):
        corpus = json.load(open(corpus_path, encoding="utf-8"))
        self.docs = {d["id"]: d for d in corpus["documents"]}
        self.index = BM25(corpus["documents"])
        self.mode, self.k, self.refuse_below = mode, k, refuse_below
        self.audit_path = audit_path

    def allowed(self, role):
        return {i for i, d in self.docs.items() if role in d["allowed_roles"]}

    def retrieve(self, role, query):
        allowed = self.allowed(role)
        if self.mode == "pre_filter":
            ranked = self.index.scores(query, candidate_ids=allowed)[: self.k]
            blocked = 0
        else:
            ranked = self.index.scores(query)[: self.k]
            blocked = sum(1 for d, _ in ranked if d not in allowed)
            if self.mode == "post_filter":
                ranked = [(d, s) for d, s in ranked if d in allowed]
        ranked = [(d, s) for d, s in ranked if s > 0]
        refused = not ranked or ranked[0][1] < self.refuse_below
        result = {"role": role, "query": query, "mode": self.mode,
                  "returned": [] if refused else [d for d, _ in ranked],
                  "scores": [round(s, 3) for _, s in ranked],
                  "blocked_by_acl": blocked, "refused": refused}
        self._audit(result)
        return result

    def _audit(self, rec):
        if self.audit_path:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}) + "\n")
