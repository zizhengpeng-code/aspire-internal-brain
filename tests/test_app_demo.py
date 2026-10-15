"""App-level checks for the public-demo safety features.

Two things make the app safe to hand to strangers, and both are asserted here against the
real Streamlit script (headless AppTest, no browser, no API calls):

* the shared-state notice is shown in the sidebar and on the Admin page, because every
  visitor mutates the same instance;
* once the per-session question budget is spent the chat input is replaced by a polite
  notice, so no model call happens.

The seeding decision is a plain function, so it is tested directly instead of by running
a real seed.

    python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from streamlit.testing.v1 import AppTest
    import app
except ImportError:                       # streamlit is optional for the rest of the repo
    AppTest = app = None

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
TIMEOUT = 120
ENV_VARS = ("MAX_QUESTIONS_PER_SESSION", "MAX_QUESTIONS_PER_DAY", "DEMO_SEED_ON_START")


class EnvBase(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ENV_VARS}
        os.environ["DEMO_SEED_ON_START"] = "0"   # never run a real seed during tests
        os.environ["MAX_QUESTIONS_PER_DAY"] = "1000000"   # keep the day cap out of the way

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


if __name__ == "__main__":
    unittest.main()
