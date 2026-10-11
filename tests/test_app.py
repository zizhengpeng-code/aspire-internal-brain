"""UI smoke test (T6) via Streamlit's headless AppTest. No browser, no API calls.

    python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from streamlit.testing.v1 import AppTest
except ImportError:                       # streamlit is optional for the rest of the repo
    AppTest = None

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
TIMEOUT = 120


@unittest.skipUnless(AppTest, "streamlit not installed")
class AppSmoke(unittest.TestCase):
    def open(self, user=None, page=None):
        at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
        self.assertFalse(at.exception, f"app raised on first render: {at.exception}")
        if user:
            at.sidebar.selectbox[0].set_value(user).run()
        if page:
            at.sidebar.radio[0].set_value(page).run()
        return at

    def test_chat_page_renders_and_hides_compliance_from_a_regular_user(self):
        at = self.open()
        self.assertEqual(at.session_state["user"], "jdoe")
        # jdoe is not the compliance officer, so the page is not even offered
        self.assertEqual(at.sidebar.radio[0].options, ["Chat", "Admin", "ROI"])

    def test_compliance_page_is_offered_to_and_renders_for_the_officer(self):
        at = self.open(user="glim")
        self.assertIn("Compliance", at.sidebar.radio[0].options)
        at.sidebar.radio[0].set_value("Compliance").run()
        self.assertFalse(at.exception)
        self.assertEqual([t.label for t in at.tabs],
                         ["Audit search", "Ask (query_audit)", "Alerts", "Over-sharing", "Chain integrity"])

    def test_over_sharing_scan_offers_to_exclude_e21(self):
        at = self.open(user="glim", page="Compliance")
        self.assertIn("Exclude from AI: E21", [b.label for b in at.button])

    def test_roi_page_shows_measured_and_assumption_numbers(self):
        at = self.open(page="ROI")
        labels = [m.label for m in at.metric]
        self.assertIn("Leak rate (Targeted ASR)", labels)
        self.assertIn("Cost / query", labels)
        # every case is still a draft, so the `user` subset is empty and the page must fall
        # back to `all` instead of rendering nan
        values = {m.label: m.value for m in at.metric}
        self.assertNotIn("nan", values["Leak rate (Targeted ASR)"])
        self.assertNotIn("nan", values["Benign Utility"])

    def test_admin_page_exposes_the_five_demo_actions(self):
        at = self.open(page="Admin")
        labels = [b.label for b in at.button]
        for expected in ("Push runbook update (13:00)", "Revoke jdoe from #payments-incident",
                         "Tamper audit record", "Truncate audit log", "Reset demo state"):
            self.assertIn(expected, labels)


if __name__ == "__main__":
    unittest.main()
