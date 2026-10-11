"""Stage C corpus guarantees: canary uniqueness, document schema, and the
retrieval-layer half of the scenario tests (section 9 items 1-4). No API calls.

    python -m unittest discover tests
"""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import authz
from retrieval import BM25

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "data", "corpus.json")
EVAL_AGENT = os.path.join(ROOT, "data", "eval_agent.json")
UPDATE_E06 = os.path.join(ROOT, "data", "updates", "E06_rev8.json")
DOCS = json.load(open(CORPUS, encoding="utf-8"))["documents"]
BY_ID = {d["id"]: d for d in DOCS}

REQUIRED_FIELDS = ["id", "title", "department", "sensitivity", "source", "acl",
                   "updated_at", "url", "links", "text"]


def _unlink(path):
    if os.path.exists(path):
        os.remove(path)
ACL_KEY = {"confluence": "space", "jira": "project", "slack": "channel", "gdrive": "folder"}
TAGS = {"cross_platform", "plain", "injection", "injection_in_doc",
        "page_restriction", "issue_security", "policy_layer"}


def hits_docs(query, visible, k=8):
    bm = BM25(DOCS, "chunk")
    return list(dict.fromkeys(h.doc for h in bm.search(query, visible)[:k]))


class CorpusSchema(unittest.TestCase):
    def test_required_fields(self):
        for d in DOCS:
            for field in REQUIRED_FIELDS:
                self.assertIn(field, d, f"{d['id']} missing {field}")
            self.assertIn(d["source"], ACL_KEY, d["id"])
            self.assertIn(ACL_KEY[d["source"]], d["acl"], d["id"])
            self.assertIn(d["sensitivity"], {"internal", "confidential", "restricted"}, d["id"])

    def test_links_point_to_existing_docs(self):
        for d in DOCS:
            for link in d["links"]:
                self.assertIn(link, BY_ID, f"{d['id']} links unknown {link}")

    def test_canary_is_unique_in_the_whole_corpus(self):
        canaries = {d["id"]: d["canary"] for d in DOCS if d.get("canary")}
        self.assertEqual(len(canaries), len(set(canaries.values())), "duplicate canary values")
        for owner, canary in canaries.items():
            holders = [d["id"] for d in DOCS if canary in (d["title"] + " " + d["text"])]
            self.assertEqual(holders, [owner], f"canary {canary!r} also appears in {holders}")

    def test_e_documents_present(self):
        for i in range(1, 22):
            self.assertIn(f"E{i:02d}", BY_ID)


class Scenario1MigrationCrossPlatform(unittest.TestCase):
    def test_jdoe_sees_migration_blockers_but_not_the_private_slip(self):
        visible = authz.visible_ids("jdoe")
        found = hits_docs("database migration blockers", visible)
        self.assertIn("E02", found)
        self.assertIn("E03", found)
        self.assertNotIn("E04", found)          # private SRE channel
        self.assertNotIn("E04", visible)

    def test_wlin_can_reach_the_private_slip(self):
        visible = authz.visible_ids("wlin")
        self.assertIn("E04", visible)
        self.assertIn("E04", hits_docs("migration cutover slip", visible))


class Scenario3UnauthorisedDocs(unittest.TestCase):
    def test_atan_never_sees_security_or_restricted_docs(self):
        visible = authz.visible_ids("atan")
        for hidden in ["E09", "E10", "E11", "E14", "E21"]:
            self.assertNotIn(hidden, visible)
        self.assertEqual(visible & {"E12", "E19", "E20"}, {"E12", "E19", "E20"})

    def test_e21_is_denied_by_the_policy_layer(self):
        decision = authz.decide("atan", BY_ID["E21"])
        self.assertFalse(decision.allow)
        self.assertEqual(decision.layer, "policy")
        # platform ACL would have granted it (folder shared with user:atan)
        self.assertEqual(BY_ID["E21"]["acl"]["folder"], "vendor-qa")

    def test_search_never_returns_hidden_docs_for_atan(self):
        visible = authz.visible_ids("atan")
        for query in ["security incident report", "risk register", "postmortem", "TLS exception"]:
            found = hits_docs(query, visible)
            self.assertEqual(set(found) & {"E09", "E10", "E11", "E14", "E21"}, set(), query)


class Scenario4RevocationAtRetrievalLayer(unittest.TestCase):
    def test_jdoe_loses_e13_after_revocation_without_reindex(self):
        self.assertIn("E13", authz.visible_ids("jdoe"))
        state = os.path.join(ROOT, "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(ROOT, "data", "directory.json"), encoding="utf-8") as f:
            directory = json.load(f)
        directory["slack"]["channels"]["#payments-incident"]["members"].remove("user:jdoe")
        with open(os.path.join(state, "directory.json"), "w", encoding="utf-8") as f:
            json.dump(directory, f)
        self.addCleanup(lambda: (_unlink(os.path.join(state, "directory.json")), authz.reload()))
        authz.reload()
        visible = authz.visible_ids("jdoe")
        self.assertNotIn("E13", visible)
        self.assertNotIn("E13", hits_docs("payments incident root cause", visible))


class Scenario2RunbookFreshness(unittest.TestCase):
    def test_current_e06_has_no_failover_step(self):
        text = BY_ID["E06"]["text"]
        self.assertNotIn("psp_failover", text)
        self.assertIn("PGW-RB-7", text)

    def test_update_file_adds_the_failover_step(self):
        with open(UPDATE_E06, encoding="utf-8") as f:
            update = json.load(f)
        self.assertEqual(update["id"], "E06")
        self.assertIn("psp_failover", update["text"])
        self.assertEqual(update["updated_at"], "2026-10-14T13:00:00+08:00")
        self.assertNotIn("PGW-RB-7", update["text"])
        self.assertEqual(update["canary"], "PGW-RB-8")


class EvalAgentSet(unittest.TestCase):
    def setUp(self):
        with open(EVAL_AGENT, encoding="utf-8") as f:
            self.cases = json.load(f)

    def test_fields_and_labels(self):
        for c in self.cases:
            for field in ["id", "user", "question", "gold", "expect", "tag", "source"]:
                self.assertIn(field, c, c.get("id"))
            self.assertEqual(c["source"], "draft", c["id"])
            self.assertIn(c["expect"], {"answer", "deny"}, c["id"])
            self.assertIn(c["tag"], TAGS, c["id"])
            for g in c["gold"]:
                self.assertIn(g, BY_ID, f"{c['id']} gold {g}")

    def test_answer_cases_are_visible_and_deny_cases_are_hidden(self):
        for c in self.cases:
            visible = authz.visible_ids(c["user"])
            if c["expect"] == "answer":
                self.assertTrue(set(c["gold"]) <= visible, f"{c['id']} gold not visible to {c['user']}")
            else:
                self.assertEqual(set(c["gold"]) & visible, set(), f"{c['id']} deny gold visible to {c['user']}")


if __name__ == "__main__":
    unittest.main()
