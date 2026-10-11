"""Stage B guarantees, checked with fake embeddings (no API calls):
pre-filtering inside the vector store, incremental sync, immediate revocation.

    python -m unittest discover tests
"""
import copy, json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.embeddings import DeterministicFakeEmbedding
import acl
from kb import RoleAwareKB
from retrieval import BM25, CachedEmbeddings, Hybrid, VectorIndex

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "data", "corpus.json")
DOCS = json.load(open(CORPUS, encoding="utf-8"))["documents"]
QUERIES = ["salary band for senior engineers", "fraud rules", "board minutes", "annual leave", "breach register"]

class StageB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.emb = CachedEmbeddings(DeterministicFakeEmbedding(size=32), "fake",
                                    path=os.path.join(self.tmp.name, "emb.sqlite"))

    def tearDown(self):
        self.emb.db.close()
        self.tmp.cleanup()

    def index(self, docs, unit="chunk"):
        return VectorIndex(docs, unit, embeddings=self.emb, persist_dir=os.path.join(self.tmp.name, "chroma"), n_candidates=1000)  # fake vectors rank randomly

    def test_pre_filter_never_returns_hidden_chunks(self):
        vec = self.index(DOCS)
        retrievers = [vec, BM25(DOCS, "chunk"), Hybrid(BM25(DOCS, "chunk"), vec)]
        kb = RoleAwareKB(CORPUS)
        for role in ["employee", "risk_analyst", "hr_manager"]:
            allowed = kb.allowed(role)
            for r in retrievers:
                for q in QUERIES:
                    self.assertTrue({h.doc for h in r.search(q, allowed)} <= allowed, (r.name, role, q))

    def test_incremental_sync_reembeds_only_changed_docs(self):
        vec = self.index(DOCS)
        docs = copy.deepcopy(DOCS)
        docs[0]["text"] += " Leave can also be bought back at year end."
        docs.append({**docs[1], "id": "D99", "title": "New Policy", "text": "A brand new policy sentence."})
        removed = docs.pop(2)["id"]
        before = self.emb.texts_embedded
        stats = vec.sync(docs)
        self.assertEqual((stats["added"], stats["changed"], stats["removed"]), (1, 1, 1))
        self.assertLessEqual(self.emb.texts_embedded - before, 5)  # D01's new chunk(s) + D99, not 158
        ids = {h.doc for h in vec.search("brand new policy")}
        self.assertIn("D99", ids)
        self.assertNotIn(removed, ids)
        self.assertEqual(vec.sync(docs)["added"] + vec.sync(docs)["changed"], 0)  # idempotent

    def test_revocation_takes_effect_without_reindex(self):
        vec = self.index(DOCS)
        kb = RoleAwareKB(CORPUS, retriever=vec, k=200)
        self.assertIn("D07", kb.retrieve("risk_analyst", "LumenFlex credit policy")["returned"])
        # Revoke through the directory (the source of truth for group membership):
        # only state/directory.json changes, the index and the corpus are untouched.
        state = os.path.join(ROOT, "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(ROOT, "data", "directory.json"), encoding="utf-8") as f:
            directory = json.load(f)
        directory["users"]["persona_risk_analyst"]["groups"].remove("risk_analyst")
        with open(os.path.join(state, "directory.json"), "w", encoding="utf-8") as f:
            json.dump(directory, f)
        self.addCleanup(lambda: (shutil.rmtree(state, ignore_errors=True), acl.reload()))
        acl.reload()
        self.assertNotIn("D07", kb.retrieve("risk_analyst", "LumenFlex credit policy")["returned"])

if __name__ == "__main__":
    unittest.main()
