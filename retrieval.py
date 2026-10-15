"""Retrievers: BM25, vector (LangChain + Chroma), hybrid (RRF), at document or chunk level.

Every retriever exposes the same call:

    search(query, candidate_ids=None) -> [Hit, ...]   sorted by score, best first

`candidate_ids` is the set of *document* ids the caller may see. It is applied inside
the retriever (BM25 skips other docs; Chroma gets a `where={"doc_id": {"$in": ...}}`
filter), so unauthorised chunks are never scored, ranked or returned. ACLs are not
copied into the index: they are resolved at query time, so a revocation takes effect
on the next query without re-indexing.

`scores(query, candidate_ids)` keeps the original `[(doc_id, score), ...]` interface
(max over each doc's chunks).
"""
import hashlib, json, math, os, re, sqlite3, time
from collections import Counter, namedtuple

# chunk: chunk id ("D07#1", or "D07" at doc level); doc: parent doc id;
# score: the ranking score; gate: the score the refusal threshold is applied to
Hit = namedtuple("Hit", "chunk doc score gate")

# ---------------------------------------------------------------- chunking

def split_sentences(text):
    return [s for s in re.split(r"(?<=[.!?。！？])\s+", text.strip()) if s]

def chunk_document(doc, max_words=30, overlap=1):
    """Sentence windows of at most `max_words` words, `overlap` sentences shared with
    the previous window. The title is prefixed to every chunk so a chunk stays
    interpretable on its own."""
    sents = split_sentences(doc["text"])
    chunks, start = [], 0
    while start < len(sents):
        end, words = start, 0
        while end < len(sents) and (end == start or words + len(sents[end].split()) <= max_words):
            words += len(sents[end].split())
            end += 1
        chunks.append(" ".join(sents[start:end]))
        if end >= len(sents):
            break
        start = max(end - overlap, start + 1)
    return [{"id": f"{doc['id']}#{i}", "doc_id": doc["id"], "text": f"{doc['title']}. {c}"}
            for i, c in enumerate(chunks)]

def make_units(docs, unit):
    if unit == "doc":
        return [{"id": d["id"], "doc_id": d["id"], "text": f"{d['title']}. {d['text']}"} for d in docs]
    return [c for d in docs for c in chunk_document(d)]

def doc_hash(doc):
    return hashlib.sha256(json.dumps([doc["title"], doc["text"]], ensure_ascii=False).encode()).hexdigest()[:16]

# ---------------------------------------------------------------- BM25

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
    name = "bm25"

    def __init__(self, docs, unit="doc", k1=1.5, b=0.75):
        self.unit, self.k1, self.b = unit, k1, b
        self.build(docs)

    def build(self, docs):
        units = make_units(docs, self.unit)
        self.ids = [u["id"] for u in units]
        self.doc_of = {u["id"]: u["doc_id"] for u in units}
        self.toks = [tokenize(u["text"]) for u in units]
        self.avgdl = sum(map(len, self.toks)) / len(self.toks)
        n = len(units)
        df = Counter(t for toks in self.toks for t in set(toks))
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(toks) for toks in self.toks]

    def search(self, query, candidate_ids=None):
        q = tokenize(query)
        res = []
        for i, uid in enumerate(self.ids):
            if candidate_ids is not None and self.doc_of[uid] not in candidate_ids:
                continue
            tf, dl, s = self.tf[i], len(self.toks[i]), 0.0
            for t in q:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                res.append(Hit(uid, self.doc_of[uid], s, s))
        return sorted(res, key=lambda h: -h.score)

    def scores(self, query, candidate_ids=None):
        return doc_scores(self.search(query, candidate_ids))

def doc_scores(hits):
    best = {}
    for h in hits:
        best.setdefault(h.doc, h.score)
    return list(best.items())

# ---------------------------------------------------------------- embeddings

class CachedEmbeddings:
    """Wraps a LangChain Embeddings object with an on-disk cache keyed by (model, text),
    so re-running the evaluation or re-syncing unchanged chunks costs nothing."""

    def __init__(self, inner, model, path=".cache/embeddings.sqlite"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.inner, self.model = inner, model
        self.db = sqlite3.connect(path)
        self.db.execute("create table if not exists emb (k text primary key, v text)")
        self.calls = self.texts_embedded = 0

    def _key(self, text):
        return hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()

    def embed_documents(self, texts):
        keys = [self._key(t) for t in texts]
        have = {}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            q = f"select k, v from emb where k in ({','.join('?' * len(part))})"
            have.update({k: json.loads(v) for k, v in self.db.execute(q, part)})
        todo = [(k, t) for k, t in zip(keys, texts) if k not in have]
        todo = list(dict(todo).items())
        if todo:
            vecs = self.inner.embed_documents([t for _, t in todo])
            self.calls += 1
            self.texts_embedded += len(todo)
            self.db.executemany("insert or replace into emb values (?, ?)",
                                [(k, json.dumps(v)) for (k, _), v in zip(todo, vecs)])
            self.db.commit()
            have.update({k: v for (k, _), v in zip(todo, vecs)})
        return [have[k] for k in keys]

    def embed_query(self, text):
        return self.embed_documents([text])[0]

def get_embeddings():
    """EMBED_MODEL selects the model; EMBED_BASE_URL / EMBED_API_KEY fall back to
    LLM_BASE_URL / LLM_API_KEY (any OpenAI-compatible endpoint).
    EMBED_MODEL=fake gives deterministic random vectors for wiring tests only."""
    model = os.environ.get("EMBED_MODEL")
    if not model:
        raise RuntimeError("EMBED_MODEL is not set (see README: vector retrieval)")
    if model == "fake":
        from langchain_core.embeddings import DeterministicFakeEmbedding
        return CachedEmbeddings(DeterministicFakeEmbedding(size=64), "fake")
    from langchain_openai import OpenAIEmbeddings
    inner = OpenAIEmbeddings(
        model=model,
        base_url=os.environ.get("EMBED_BASE_URL") or os.environ.get("LLM_BASE_URL"),
        api_key=os.environ.get("EMBED_API_KEY") or os.environ.get("LLM_API_KEY"),
        chunk_size=10,  # the embedding API accepts at most 10 texts per request
        check_embedding_ctx_length=False,  # send raw strings; many compatible APIs reject token ids
    )
    return CachedEmbeddings(inner, model)

# ---------------------------------------------------------------- vector (Chroma)

class VectorIndex:
    name = "vector"

    def __init__(self, docs, unit="doc", embeddings=None, persist_dir="index/chroma", n_candidates=50):
        from langchain_chroma import Chroma
        self.emb = embeddings or get_embeddings()
        self.unit, self.n = unit, n_candidates
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", self.emb.model).strip("-")
        self.collection = f"kb-{unit}-{slug}"[:60]
        self.store = Chroma(collection_name=self.collection, embedding_function=self.emb,
                            persist_directory=persist_dir, collection_metadata={"hnsw:space": "cosine"})
        self.manifest_path = os.path.join(persist_dir, f"{self.collection}.manifest.json")
        self.size = sum(len(v["units"]) for v in self._manifest().values())
        if docs is not None:
            self.sync(docs)

    def _manifest(self):
        if os.path.exists(self.manifest_path):
            with open(self.manifest_path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def sync(self, docs):
        """Incremental sync: only docs whose title/text hash changed are re-chunked and
        re-embedded; deleted docs are removed. Returns what changed and how long it took."""
        t0 = time.perf_counter()
        old, new = self._manifest(), {}
        current = {d["id"]: d for d in docs}
        added = changed = removed = 0
        for did in set(old) - set(current):
            self.store.delete(ids=old[did]["units"])
            removed += 1
        for did, d in current.items():
            h = doc_hash(d)
            if did in old and old[did]["hash"] == h:
                new[did] = old[did]
                continue
            if did in old:
                self.store.delete(ids=old[did]["units"])
                changed += 1
            else:
                added += 1
            units = make_units([d], self.unit)
            meta = {"doc_id": did, "department": d["department"], "sensitivity": d["sensitivity"]}
            if d.get("source"):
                meta["source"] = d["source"]
            self.store.add_texts([u["text"] for u in units], metadatas=[meta] * len(units),
                                 ids=[u["id"] for u in units])
            new[did] = {"hash": h, "units": [u["id"] for u in units]}
        os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(new, f)
        self.size = sum(len(v["units"]) for v in new.values())
        return {"added": added, "changed": changed, "removed": removed,
                "seconds": round(time.perf_counter() - t0, 3)}

    def search(self, query, candidate_ids=None):
        if not self.size or (candidate_ids is not None and not candidate_ids):
            return []
        flt = None if candidate_ids is None else {"doc_id": {"$in": sorted(candidate_ids)}}
        res = self.store.similarity_search_with_score(query, k=min(self.n, self.size), filter=flt)
        hits = []
        for doc, dist in res:
            sim = 1 - dist  # cosine distance -> cosine similarity
            hits.append(Hit(doc.id, doc.metadata["doc_id"], sim, sim))
        return sorted(hits, key=lambda h: -h.score)

    def scores(self, query, candidate_ids=None):
        return doc_scores(self.search(query, candidate_ids))

# ---------------------------------------------------------------- hybrid

class Hybrid:
    """Reciprocal rank fusion of BM25 and vector rankings over the same units.
    The ranking score is RRF; the refusal gate uses cosine similarity, because RRF
    scores carry rank information only and cannot say "nothing here is relevant"."""
    name = "hybrid"

    def __init__(self, bm25, vector, k_rrf=60):
        assert bm25.unit == vector.unit
        self.bm25, self.vector, self.k_rrf, self.unit = bm25, vector, k_rrf, bm25.unit

    def search(self, query, candidate_ids=None):
        fused, doc_of = Counter(), {}
        vec = self.vector.search(query, candidate_ids)
        for hits in (self.bm25.search(query, candidate_ids)[: self.vector.n], vec):
            for rank, h in enumerate(hits, 1):
                fused[h.chunk] += 1 / (self.k_rrf + rank)
                doc_of[h.chunk] = h.doc
        cos = {h.chunk: h.score for h in vec}
        hits = [Hit(c, doc_of[c], s, cos.get(c, 0.0)) for c, s in fused.items()]
        return sorted(hits, key=lambda h: -h.score)

    def scores(self, query, candidate_ids=None):
        return doc_scores(self.search(query, candidate_ids))

def build_retriever(name, docs, unit="doc"):
    if name == "bm25":
        return BM25(docs, unit)
    if name == "vector":
        return VectorIndex(docs, unit)
    if name == "hybrid":
        return Hybrid(BM25(docs, unit), VectorIndex(docs, unit))
    raise ValueError(name)
