"""Unit tests for the public-demo spending limits. No model, no network, no API calls.

The two caps are what stop a stranger from spending the whole LLM budget, so the tests
pin down the parts that matter: the defaults and the environment overrides, that a check
never spends anything by itself, that the daily counter is shared across sessions and
rolls over at midnight, and that a missing or corrupt counter file degrades to zero
instead of raising.

    python -m unittest discover tests
"""
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import limits

NOW = datetime.datetime(2026, 10, 15, 12, 0, tzinfo=datetime.timezone.utc)
TOMORROW = datetime.datetime(2026, 10, 16, 12, 0, tzinfo=datetime.timezone.utc)
ENV_VARS = ("MAX_QUESTIONS_PER_SESSION", "MAX_QUESTIONS_PER_DAY")


class LimitBase(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ENV_VARS}
        for key in ENV_VARS:
            os.environ.pop(key, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.counter = os.path.join(self.tmp.name, "questions.json")

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()


class Configuration(LimitBase):
    def test_defaults(self):
        self.assertEqual(limits.per_session_limit(), 20)
        self.assertEqual(limits.per_day_limit(), 300)

    def test_environment_overrides_the_defaults(self):
        os.environ["MAX_QUESTIONS_PER_SESSION"] = "3"
        os.environ["MAX_QUESTIONS_PER_DAY"] = "7"
        self.assertEqual(limits.per_session_limit(), 3)
        self.assertEqual(limits.per_day_limit(), 7)

    def test_nonsense_values_fall_back_to_the_default(self):
        for bad in ("", "  ", "abc", "-5", "3.5"):
            os.environ["MAX_QUESTIONS_PER_SESSION"] = bad
            self.assertEqual(limits.per_session_limit(), 20, f"value was {bad!r}")

    def test_zero_is_a_valid_limit(self):
        os.environ["MAX_QUESTIONS_PER_SESSION"] = "0"
        self.assertEqual(limits.per_session_limit(), 0)


class SessionLimit(LimitBase):
    def setUp(self):
        super().setUp()
        os.environ["MAX_QUESTIONS_PER_SESSION"] = "2"
        os.environ["MAX_QUESTIONS_PER_DAY"] = "300"

    def test_allowed_up_to_the_cap_then_blocked(self):
        self.assertTrue(limits.check(0, path=self.counter, now=NOW)["allowed"])
        self.assertTrue(limits.check(1, path=self.counter, now=NOW)["allowed"])
        blocked = limits.check(2, path=self.counter, now=NOW)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "session")
        self.assertIn("2", blocked["message"])

    def test_a_blocked_check_never_spends_anything(self):
        for _ in range(3):
            limits.check(2, path=self.counter, now=NOW)
        self.assertEqual(limits.day_count(self.counter, now=NOW), 0)

    def test_session_limit_of_zero_blocks_the_first_question(self):
        os.environ["MAX_QUESTIONS_PER_SESSION"] = "0"
        self.assertFalse(limits.check(0, path=self.counter, now=NOW)["allowed"])


class DayLimit(LimitBase):
    def setUp(self):
        super().setUp()
        os.environ["MAX_QUESTIONS_PER_SESSION"] = "20"
        os.environ["MAX_QUESTIONS_PER_DAY"] = "3"

    def test_the_counter_accumulates_and_then_blocks(self):
        for expected in (1, 2, 3):
            self.assertTrue(limits.check(0, path=self.counter, now=NOW)["allowed"])
            self.assertEqual(limits.record_question(path=self.counter, now=NOW), expected)
        blocked = limits.check(0, path=self.counter, now=NOW)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "day")
        self.assertIn("3", blocked["message"])

    def test_the_budget_is_shared_across_sessions(self):
        # two "sessions", each with its own per-session count of 0, share one daily budget
        os.environ["MAX_QUESTIONS_PER_DAY"] = "2"
        limits.record_question(path=self.counter, now=NOW)
        limits.record_question(path=self.counter, now=NOW)
        self.assertFalse(limits.check(0, path=self.counter, now=NOW)["allowed"])

    def test_the_counter_rolls_over_at_midnight(self):
        os.environ["MAX_QUESTIONS_PER_DAY"] = "1"
        limits.record_question(path=self.counter, now=NOW)
        self.assertFalse(limits.check(0, path=self.counter, now=NOW)["allowed"])
        self.assertTrue(limits.check(0, path=self.counter, now=TOMORROW)["allowed"])

    def test_record_question_creates_the_cache_directory(self):
        nested = os.path.join(self.tmp.name, ".cache", "questions.json")
        limits.record_question(path=nested, now=NOW)
        self.assertTrue(os.path.exists(nested))
        self.assertEqual(limits.day_count(nested, now=NOW), 1)

    def test_a_missing_counter_file_counts_as_zero(self):
        missing = os.path.join(self.tmp.name, "absent.json")
        self.assertEqual(limits.day_count(missing, now=NOW), 0)
        self.assertTrue(limits.check(0, path=missing, now=NOW)["allowed"])

    def test_a_corrupt_counter_file_counts_as_zero(self):
        with open(self.counter, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        self.assertEqual(limits.day_count(self.counter, now=NOW), 0)
        self.assertTrue(limits.check(0, path=self.counter, now=NOW)["allowed"])
        # and it recovers: a new question rewrites the file
        self.assertEqual(limits.record_question(path=self.counter, now=NOW), 1)

    def test_the_session_cap_wins_when_both_are_exhausted(self):
        os.environ["MAX_QUESTIONS_PER_SESSION"] = "1"
        for _ in range(3):
            limits.record_question(path=self.counter, now=NOW)
        verdict = limits.check(1, path=self.counter, now=NOW)
        self.assertEqual(verdict["reason"], "session")


if __name__ == "__main__":
    unittest.main()
