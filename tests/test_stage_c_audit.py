"""Stage C audit: hash-chain integrity, query filters, and the alert rule.
No API calls.

    python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alerts
from audit import AuditLog


def tool_call(user, ts, doc_id, location, sensitivity, allow, layer="platform"):
    return {"event": "tool_call", "user_id": user, "ts": ts, "tool": "search",
            "args": {"query": "q"}, "returned": [],
            "decisions": [{"doc_id": doc_id, "location": location, "sensitivity": sensitivity,
                           "allow": allow, "layer": layer, "reason": "test"}]}


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "audit.jsonl")
        self.log = AuditLog(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_verify_passes_on_intact_chain(self):
        self.assertIsNone(self.log.verify())  # empty log is intact
        self.log.append({"event": "query", "user_id": "jdoe", "question": "hi"})
        self.log.append(tool_call("jdoe", "2026-10-14T14:05:00+08:00", "E01", "jira:DBMIG", "internal", True))
        self.log.append({"event": "answer", "user_id": "jdoe", "answer": "ok", "citations": ["E01"]})
        self.assertIsNone(self.log.verify())
        recs = self.log.records()
        self.assertEqual([r["seq"] for r in recs], [1, 2, 3])
        self.assertEqual(recs[0]["prev_hash"], "")
        self.assertEqual(recs[1]["prev_hash"], recs[0]["hash"])

    def test_tampering_a_line_is_detected(self):
        for i in range(3):
            self.log.append(tool_call("jdoe", f"2026-10-14T14:0{i}:00+08:00", "E01", "jira:DBMIG", "internal", True))
        with open(self.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        record = json.loads(lines[1])
        record["decisions"][0]["allow"] = False      # forge a decision
        lines[1] = json.dumps(record, ensure_ascii=False)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.assertEqual(self.log.verify(), 2)       # seq of the first bad record

    def test_deleting_a_line_is_detected(self):
        for i in range(3):
            self.log.append(tool_call("jdoe", f"2026-10-14T14:0{i}:00+08:00", "E01", "jira:DBMIG", "internal", True))
        with open(self.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join([lines[0], lines[2]]) + "\n")
        self.assertEqual(self.log.verify(), 3)       # seq jumps 1 -> 3

    def test_query_filters(self):
        self.log.append(tool_call("jdoe", "2026-10-14T14:05:00+08:00", "E06", "confluence:payment-gateway", "internal", True))
        self.log.append(tool_call("jdoe", "2026-10-14T14:06:00+08:00", "E08", "confluence:payment-gateway", "confidential", False))
        self.log.append(tool_call("atan", "2026-10-14T14:07:00+08:00", "E09", "confluence:security", "restricted", False))
        self.assertEqual(len(self.log.query(user="jdoe")), 2)
        self.assertEqual(len(self.log.query(location_prefix="confluence:payment-gateway")), 2)
        self.assertEqual(len(self.log.query(location_prefix="confluence:security")), 1)
        self.assertEqual(len(self.log.query(user="jdoe", doc="E08")), 1)
        self.assertEqual(len(self.log.query(decision="deny")), 2)
        self.assertEqual(len(self.log.query(user="jdoe", since="2026-10-14T14:06:00+08:00")), 1)
        self.assertEqual(len(self.log.query(user="jdoe", location_prefix="confluence:security")), 0)


class AlertRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.log = AuditLog(os.path.join(self.tmp.name, "audit.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_three_restricted_denies_raise_one_alert(self):
        docs = [("E09", "confluence:security"), ("E11", "confluence:security"), ("E21", "gdrive:vendor-qa")]
        fired = []
        for i, (doc_id, loc) in enumerate(docs):
            ts = f"2026-10-14T14:0{i}:00+08:00"
            self.log.append(tool_call("atan", ts, doc_id, loc, "restricted", False))
            fired.append(alerts.check(self.log, "atan", now_ts=ts))
        self.assertEqual(fired, [False, False, True])   # fires on the third
        self.assertEqual(len(self.log.query(user="atan", decision="deny")), 3)
        alert_records = [r for r in self.log.records() if r["event"] == "alert"]
        self.assertEqual(len(alert_records), 1)
        self.assertEqual(alert_records[0]["rule"], alerts.RULE)
        self.assertEqual(alert_records[0]["count"], 3)
        self.assertIsNone(self.log.verify())            # alert is chained too

    def test_no_alert_below_threshold(self):
        for i in range(2):
            ts = f"2026-10-14T14:0{i}:00+08:00"
            self.log.append(tool_call("atan", ts, "E09", "confluence:security", "restricted", False))
            alerts.check(self.log, "atan", now_ts=ts)
        self.assertEqual([r for r in self.log.records() if r["event"] == "alert"], [])

    def test_denies_outside_the_window_do_not_count(self):
        self.log.append(tool_call("atan", "2026-10-14T13:00:00+08:00", "E09", "confluence:security", "restricted", False))
        self.log.append(tool_call("atan", "2026-10-14T13:30:00+08:00", "E11", "confluence:security", "restricted", False))
        self.log.append(tool_call("atan", "2026-10-14T14:05:00+08:00", "E21", "gdrive:vendor-qa", "restricted", False))
        self.assertFalse(alerts.check(self.log, "atan", now_ts="2026-10-14T14:05:00+08:00"))


if __name__ == "__main__":
    unittest.main()
