"""App-level checks for the public-demo safety and usability features.

Four things make the app safe to hand to strangers, and all of them are asserted here against
the real Streamlit script (headless AppTest, no browser; the one test that asks a question
pins the scripted `fake` model, so no API call is made):

* the shared-state notice is shown in the sidebar and on the Admin page, because every
  visitor mutates the same instance;
* once the per-session question budget is spent the chat input is replaced by a polite
  notice, so no model call happens;
* a greeting is answered by the UI itself — no model call, no audit record, no question
  spent — and comes with clickable starter questions for the signed-in user;
* the shared state is reset for a visitor who arrives after the Admin page has been quiet,
  and the reset is written to the audit log as an admin event.

The seeding and reset decisions are plain functions, so they are tested directly as well as
through the app.

    python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import simulate
from audit import AuditLog
try:
    from streamlit.testing.v1 import AppTest
    import app
except ImportError:                       # streamlit is optional for the rest of the repo
    AppTest = app = None

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
TIMEOUT = 120
ENV_VARS = ("MAX_QUESTIONS_PER_SESSION", "MAX_QUESTIONS_PER_DAY", "DEMO_SEED_ON_START",
            "DEMO_AUTORESET_IDLE_MINUTES", "LLM_MODEL")


class EnvBase(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ENV_VARS}
        os.environ["DEMO_SEED_ON_START"] = "0"   # never run a real seed during tests
        os.environ["MAX_QUESTIONS_PER_DAY"] = "1000000"   # keep the day cap out of the way
        os.environ["DEMO_AUTORESET_IDLE_MINUTES"] = "0"   # no surprise reset mid-suite

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def open(self):
        at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
        self.assertFalse(at.exception, f"app raised on first render: {at.exception}")
        return at


class RuntimeFilesUntouched(unittest.TestCase):
    """Put the instance's own runtime files back exactly as they were.

    ``AppTest`` executes ``app.py`` in a fresh namespace, so the script's ``AUDIT_PATH`` cannot
    be patched from here: a test that asks a question through the app writes to the real
    ``logs/audit.jsonl``, ``logs/checkpoints.jsonl`` and the shared daily counter in
    ``.cache/questions.json``. Those are git-ignored and rebuilt on first start, but the suite
    should still not leave its own records behind in the instance someone is about to demo.
    """

    RUNTIME_FILES = ("logs/audit.jsonl", "logs/checkpoints.jsonl", ".cache/questions.json")

    def setUp(self):
        super().setUp()
        self.root = os.path.dirname(APP)
        self.saved_files = {name: self._read(self.runtime_path(name))
                            for name in self.RUNTIME_FILES}
        self.addCleanup(self._restore)

    def runtime_path(self, name):
        return os.path.join(self.root, *name.split("/"))

    @staticmethod
    def _read(path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def _restore(self):
        for name, content in self.saved_files.items():
            path = self.runtime_path(name)
            if content is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "wb") as f:
                    f.write(content)

    def audit_snapshot(self):
        """Size and mtime of the audit log the app actually writes."""
        path = self.runtime_path("logs/audit.jsonl")
        if not os.path.exists(path):
            return None
        return (os.path.getsize(path), os.path.getmtime(path))


@unittest.skipUnless(AppTest, "streamlit not installed")
class SharedStateNotice(EnvBase):
    def test_the_notice_is_shown_in_the_sidebar(self):
        at = self.open()
        notices = [i.value for i in at.sidebar.info]
        self.assertTrue(any("Shared demo" in n for n in notices), notices)
        self.assertTrue(any("Reset demo state" in n for n in notices), notices)

    def test_the_notice_is_shown_on_the_admin_page(self):
        at = self.open()
        at.sidebar.radio[0].set_value("Admin").run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Shared demo" in i.value for i in at.info),
                        [i.value for i in at.info])


@unittest.skipUnless(AppTest, "streamlit not installed")
class ChatBudget(EnvBase):
    def test_the_chat_input_is_available_below_the_limit(self):
        at = self.open()
        self.assertEqual(len(at.chat_input), 1)

    def test_the_chat_input_is_replaced_by_a_notice_at_the_limit(self):
        os.environ["MAX_QUESTIONS_PER_SESSION"] = "0"
        at = self.open()
        self.assertEqual(len(at.chat_input), 0)
        self.assertTrue(any("limit" in i.value for i in at.info),
                        [i.value for i in at.info])


@unittest.skipUnless(app, "streamlit not installed")
class SeedingDecision(unittest.TestCase):
    """`seed_demo_history` decides whether the first-start seed runs. No real seed here."""

    def setUp(self):
        self.saved = os.environ.get("DEMO_SEED_ON_START")

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("DEMO_SEED_ON_START", None)
        else:
            os.environ["DEMO_SEED_ON_START"] = self.saved

    def test_nothing_happens_when_the_log_already_exists(self):
        os.environ["DEMO_SEED_ON_START"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
            self.assertEqual(app.seed_demo_history(path=path),
                             {"seeded": False, "reason": f"{path} already present"})

    def test_seeding_can_be_disabled_by_environment(self):
        os.environ["DEMO_SEED_ON_START"] = "0"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            result = app.seed_demo_history(path=path)
        self.assertFalse(result["seeded"])
        self.assertIn("DEMO_SEED_ON_START", result["reason"])

    def test_seeding_runs_once_when_the_log_is_missing(self):
        os.environ["DEMO_SEED_ON_START"] = "1"
        calls = []
        original = app.simulate.seed
        app.simulate.seed = lambda audit_path=None: calls.append(audit_path) or [{}] * 3
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "audit.jsonl")
                result = app.seed_demo_history(path=path)
        finally:
            app.simulate.seed = original
        self.assertEqual(result, {"seeded": True, "queries": 3})
        self.assertEqual(calls, [path])


@unittest.skipUnless(app, "streamlit not installed")
class GreetingIsRecognised(unittest.TestCase):
    """Only a *whole* short message counts as a greeting."""

    def test_variants_of_case_whitespace_and_punctuation_are_recognised(self):
        for text in ("hi", "Hi!", "  HELLO  ", "hey", "help", "what can you do?", "who are you",
                     "你好", "你能做什么？", "Yo..."):
            self.assertTrue(app.is_greeting(text), text)

    def test_a_question_that_merely_starts_with_a_greeting_is_not_one(self):
        for text in ("hi, what's the latest runbook for the payment service?",
                     "hello there, can you tell me about the payment service runbook?",
                     "What's the status of the database migration project?",
                     "What is the settlement reconciliation cutoff?", ""):
            self.assertFalse(app.is_greeting(text), text)


@unittest.skipUnless(AppTest, "streamlit not installed")
class GreetingIsFree(RuntimeFilesUntouched, EnvBase):
    """A greeting is answered by the UI: no model call, no audit record, no question spent."""

    def test_a_greeting_is_answered_without_spending_anything(self):
        before = self.audit_snapshot()
        at = self.open()
        at.chat_input[0].set_value("Hello").run()
        self.assertFalse(at.exception)

        turn = at.session_state["turns::jdoe"][-1]
        self.assertEqual(turn["kind"], "greeting")
        self.assertIn("Internal Brain", turn["answer"])
        self.assertEqual(turn["steps"], [])
        self.assertEqual(turn["citations"], [])
        # nothing was spent, and the agent was never built for this turn
        self.assertEqual(at.session_state.get("questions_asked"), 0)
        self.assertIsNone(at.session_state.get("hist::jdoe"))
        self.assertEqual(self.audit_snapshot(), before, "a greeting must not write an audit record")

    def test_the_examples_are_offered_on_an_empty_chat_and_after_a_greeting(self):
        at = self.open()
        expected = app.EXAMPLE_QUESTIONS["jdoe"]
        self.assertEqual([b.label for b in at.button][:len(expected)], expected)
        at.chat_input[0].set_value("hi").run()
        self.assertEqual([b.label for b in at.button][:len(expected)], expected)

    def test_clicking_an_example_takes_the_same_path_as_typing_it(self):
        os.environ["LLM_MODEL"] = "fake"          # scripted model: no API call
        question = app.EXAMPLE_QUESTIONS["jdoe"][0]

        clicked = self.open()
        [b for b in clicked.button if b.label == question][0].click().run()
        self.assertFalse(clicked.exception)

        typed = self.open()
        typed.chat_input[0].set_value(question).run()
        self.assertFalse(typed.exception)

        for at in (clicked, typed):
            turn = at.session_state["turns::jdoe"][-1]
            self.assertEqual(turn["question"], question)
            self.assertTrue(turn["steps"], "the question is retrieved like any other")
            self.assertFalse(turn["refused"])
            self.assertEqual(at.session_state.get("questions_asked"), 1)
        # same question in, same turn out: the button is a shortcut, not a second code path
        self.assertEqual(clicked.session_state["turns::jdoe"],
                         typed.session_state["turns::jdoe"])


class IdleAutoReset(unittest.TestCase):
    """`reset_if_idle` decides when a new visitor inherits a clean shared state."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.audit_path = os.path.join(self.tmp.name, "audit.jsonl")
        self.activity = os.path.join(self.tmp.name, "demo_activity.json")
        self.saved_activity = simulate.ACTIVITY
        simulate.ACTIVITY = self.activity
        self.addCleanup(setattr, simulate, "ACTIVITY", self.saved_activity)
        self.resets = []
        self.saved_reset = simulate.reset
        simulate.reset = lambda: self.resets.append(True)
        self.addCleanup(setattr, simulate, "reset", self.saved_reset)

    def idle(self, now, minutes=15):
        return simulate.reset_if_idle(idle_minutes=minutes, now=now, audit_path=self.audit_path)

    def test_nothing_is_reset_when_no_admin_action_is_on_record(self):
        outcome = simulate.reset_if_idle(now=1000.0)
        self.assertFalse(outcome["reset"])
        self.assertTrue(os.path.exists(self.activity), "the first visit sets the baseline")
        self.assertEqual(self.resets, [])

    def test_an_admin_action_inside_the_window_holds_the_reset_off(self):
        simulate.note_admin_activity(now=1000.0)
        outcome = self.idle(now=1000.0 + 14 * 60)
        self.assertFalse(outcome["reset"])
        self.assertEqual(self.resets, [])

    def test_a_quiet_spell_resets_and_records_it_as_an_admin_event(self):
        simulate.note_admin_activity(now=1000.0)
        outcome = self.idle(now=1000.0 + 16 * 60)
        self.assertTrue(outcome["reset"])
        self.assertEqual(self.resets, [True])
        events = [r for r in AuditLog(self.audit_path).records() if r["event"] == "state_reset"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["user_id"], "admin")
        self.assertEqual(events[0]["reason"], "idle")

    def test_the_baseline_moves_forward_so_the_next_visitor_is_left_alone(self):
        simulate.note_admin_activity(now=1000.0)
        self.idle(now=1000.0 + 16 * 60)
        again = self.idle(now=1000.0 + 17 * 60)
        self.assertFalse(again["reset"], "a second arrival must not reset again straight away")
        self.assertEqual(self.resets, [True])


@unittest.skipUnless(AppTest, "streamlit not installed")
class IdleAutoResetThroughTheUi(EnvBase):
    """A visitor who arrives after a quiet spell gets a clean state, and is told so."""

    def setUp(self):
        super().setUp()
        os.environ.pop("DEMO_AUTORESET_IDLE_MINUTES", None)   # the real 15-minute window
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.audit_path = os.path.join(tmp.name, "audit.jsonl")
        patches = [(simulate, "ACTIVITY", os.path.join(tmp.name, "demo_activity.json")),
                   (simulate, "STATE", os.path.join(tmp.name, "state")),   # never the real state/
                   (simulate, "AUDIT_PATH", self.audit_path)]
        for module, name, value in patches:
            self.addCleanup(setattr, module, name, getattr(module, name))
            setattr(module, name, value)

    def mark(self, age_seconds):
        with open(simulate.ACTIVITY, "w", encoding="utf-8") as f:
            json.dump({"last_admin_action": time.time() - age_seconds}, f)

    def test_a_state_left_behind_by_someone_else_is_reset_on_arrival(self):
        self.mark(age_seconds=3600)
        at = self.open()
        self.assertFalse(at.exception)
        self.assertTrue(at.session_state["auto_reset"]["reset"])
        events = [r for r in AuditLog(self.audit_path).records() if r["event"] == "state_reset"]
        self.assertEqual(len(events), 1, "the reset is recorded as an admin event")
        self.assertTrue(any("reset" in c.value for c in at.sidebar.caption),
                        [c.value for c in at.sidebar.caption])

    def test_a_recent_admin_action_is_left_exactly_as_it_was(self):
        self.mark(age_seconds=60)
        at = self.open()
        self.assertFalse(at.exception)
        self.assertFalse(at.session_state["auto_reset"]["reset"])
        self.assertFalse(os.path.exists(self.audit_path), "nothing was reset, so nothing is logged")


if __name__ == "__main__":
    unittest.main()
