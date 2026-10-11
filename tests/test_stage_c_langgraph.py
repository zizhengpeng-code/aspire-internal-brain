"""LangGraph agent invariants (section 9 items 9-10), fake mode, no API calls.

    python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guard
import simulate
from agent import Session, answer
from audit import AuditLog
from llm import LLM
from tools import make_tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = {"user", "user_id", "role", "groups", "as_user", "session", "session_id", "principal"}
DEMO_USERS = ["jdoe", "wlin", "sho", "atan", "glim"]


class ToolSchemasCarryNoIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.llm = LLM(model="fake", cache_path=os.path.join(self.tmp.name, "llm.sqlite"))
        self.audit = AuditLog(os.path.join(self.tmp.name, "audit.jsonl"))

    def test_no_identity_parameters_for_any_demo_user(self):
        for user in DEMO_USERS:
            session = Session(user, llm=self.llm, audit=self.audit)
            schemas, _ = make_tools(session)
            self.assertTrue(schemas, user)
            for schema in schemas:
                name = schema["function"]["name"]
                props = set((schema["function"]["parameters"].get("properties") or {}).keys())
                self.assertEqual(props & FORBIDDEN, set(), f"{user}: {name} exposes {props & FORBIDDEN}")


class MultiTurnRevocation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(simulate.reset)
        self.llm = LLM(model="fake", cache_path=os.path.join(self.tmp.name, "llm.sqlite"))
        self.audit = AuditLog(os.path.join(self.tmp.name, "audit.jsonl"))

    def session(self, user):
        return Session(user, llm=self.llm, audit=self.audit)

    def test_repeat_after_revocation_does_not_recite_the_revoked_content(self):
        session = self.session("jdoe")
        first = answer(session, "What was the root cause of the payments incident on Oct 13?")
        self.assertIn("SDK 5.2", first["answer"])              # grounded in E13
        simulate.revoke("jdoe", "#payments-incident")          # no reindex
        second = answer(session, "repeat what you just told me about the root cause",
                        history=first["history"])
        self.assertNotIn("SDK 5.2", second["answer"])

    def test_filter_history_drops_a_turn_whose_documents_are_gone(self):
        turn = {"question": "q", "answer": "a [E13]", "citations": ["E13"]}
        self.assertEqual(guard.filter_history([turn], "jdoe"), [turn])   # E13 visible
        simulate.revoke("jdoe", "#payments-incident")
        self.assertEqual(guard.filter_history([turn], "jdoe"), [])       # dropped


if __name__ == "__main__":
    unittest.main()
