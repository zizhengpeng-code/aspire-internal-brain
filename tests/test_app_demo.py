"""App-level checks for the public-demo safety and usability features.

Five things make the app safe to hand to strangers, and all of them are asserted here against
the real Streamlit script (headless AppTest, no browser; the one test that asks a question
pins the scripted `fake` model, so no API call is made):

* the shared-state notice is shown in the sidebar and on the Admin page, because every
  visitor mutates the same instance;
* once the per-session question budget is spent the chat input is replaced by a polite
  notice, so no model call happens;
* a greeting is answered by the UI itself — no model call, no audit record, no question
  spent — and comes with clickable starter questions for the signed-in user;
* a question *about the assistant* ("what can I know in this system?") is answered by the UI
  too, from the live permission set, because the model can only guess at it from whatever the
  retriever happens to return — and on the live demo it sometimes refused instead;
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
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import intent
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
            "DEMO_AUTORESET_IDLE_MINUTES", "LLM_MODEL", "LOGIN_FLOW")


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


    def test_the_greeting_turn_records_what_the_visitor_actually_said(self):
        at = self.open()
        at.chat_input[0].set_value("你好").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["turns::jdoe"][-1]["question"], "你好")


@unittest.skipUnless(app, "streamlit not installed")
class ScopeQuestionIsRecognised(unittest.TestCase):
    """A question *about the assistant* is matched on the whole message, like a greeting."""

    def test_variants_of_case_whitespace_and_punctuation_are_recognised(self):
        for text in ("And what can i know in this system?", "What can I know in this system",
                     "what can i know here", "What can you help me with?",
                     "  What do I have access to?  ", "What documents can I see?",
                     "what do you know about", "So, what can I ask you about?",
                     "how does this work", "我能看到什么", "这个系统里有什么"):
            self.assertTrue(app.is_scope_question(text), text)

    def test_a_real_question_that_merely_starts_like_one_is_not(self):
        for text in ("what do you know about the payments incident",
                     "What documents can I see about the PSP failover?",
                     "What can you tell me about the ledger backfill?",
                     "Which documents mention the PSP failover?",
                     "What's the status of the database migration project?", ""):
            self.assertFalse(app.is_scope_question(text), text)

    def test_it_does_not_swallow_a_greeting(self):
        for text in ("hi", "hello", "what can you do", "who are you"):
            self.assertFalse(app.is_scope_question(text), text)


@unittest.skipUnless(app, "streamlit not installed")
class ScopeAnswerNamesOnlyWhatTheUserCanRead(unittest.TestCase):
    """The summary is computed from the ACL, so it cannot describe anything else."""

    def test_the_count_matches_the_permission_set(self):
        docs = app.authz.documents()
        for user in ("jdoe", "wlin", "sho", "atan", "glim"):
            readable = [i for i in app.authz.list_readable(user) if i in docs]
            answer = app.scope_answer(user)
            self.assertIn(f"**{len(readable)} documents**", answer)
            self.assertIn(app.user_info(user).get("name", user), answer)

    def test_it_never_names_a_document_the_user_cannot_read(self):
        docs = app.authz.documents()
        for user in ("jdoe", "wlin", "sho", "atan", "glim"):
            readable = set(app.authz.list_readable(user))
            answer = app.scope_answer(user)
            for doc_id, doc in docs.items():
                if doc_id in readable:
                    continue
                self.assertNotIn(doc_id, answer)
                title = doc.get("title")
                if title:
                    self.assertNotIn(title, answer, f"{user} must not be told about {doc_id}")

    def test_it_states_no_total_and_no_count_of_what_was_withheld(self):
        # a hidden document has to stay indistinguishable from a missing one
        docs = app.authz.documents()
        for user in ("jdoe", "atan"):
            readable = set(app.authz.list_readable(user))
            answer = app.scope_answer(user)
            self.assertNotIn(f"{len(docs)} documents", answer)
            self.assertNotIn(f"{len(docs) - len(readable)} documents", answer)

    def test_a_channel_the_user_cannot_read_is_not_named(self):
        answer = app.scope_answer("atan")
        self.assertIn("#payments-eng", answer)
        self.assertNotIn("#payments-incident", answer)

    def test_a_narrow_account_is_told_the_refusals_are_expected(self):
        self.assertIn("permission model", app.scope_answer("atan"))
        self.assertNotIn("permission model", app.scope_answer("jdoe"))


@unittest.skipUnless(AppTest, "streamlit not installed")
class ScopeQuestionIsFree(RuntimeFilesUntouched, EnvBase):
    """The screenshot defect: a natural first question about the system came back refused.

    It was answered by the model, so the result depended on what the retriever returned for it —
    measured on the live demo, the same question produced a full summary, then a hedged
    non-answer, then the plain refusal. The UI knows the answer, so it gives it, for free.
    """

    def test_asking_what_you_can_know_costs_nothing(self):
        before = self.audit_snapshot()
        at = self.open()
        at.chat_input[0].set_value("And what can i know in this system?").run()
        self.assertFalse(at.exception)

        turn = at.session_state["turns::jdoe"][-1]
        self.assertEqual(turn["kind"], "scope")
        self.assertIn("You can read", turn["answer"])
        self.assertFalse(turn["refused"])
        self.assertEqual(turn["steps"], [])
        self.assertEqual(turn["citations"], [])
        # nothing was spent, and the agent was never built for this turn
        self.assertEqual(at.session_state.get("questions_asked"), 0)
        self.assertIsNone(at.session_state.get("hist::jdoe"))
        self.assertEqual(self.audit_snapshot(), before, "no model call means no audit record")

    def test_it_is_answered_rather_than_refused(self):
        at = self.open()
        at.chat_input[0].set_value("what can you help me with?").run()
        self.assertFalse(at.exception)
        turn = at.session_state["turns::jdoe"][-1]
        self.assertFalse(turn["refused"])
        self.assertNotIn("couldn't find information", turn["answer"])

    def test_the_examples_are_still_offered_after_a_scope_answer(self):
        at = self.open()
        at.chat_input[0].set_value("what can I know here").run()
        expected = app.EXAMPLE_QUESTIONS["jdoe"]
        self.assertEqual([b.label for b in at.button][:len(expected)], expected)


def _scripted_classifier(labels):
    """A stand-in for ``intent.classify``: the message decides the label. No model call.

    Keys are normalised the same way the lookup is, so the scripted labels are case- and
    whitespace-insensitive like the real classifier's own input.
    """
    normalised = {" ".join(k.lower().split()): v for k, v in labels.items()}

    def classify(question, llm):
        return normalised.get(" ".join((question or "").lower().split()), intent.KNOWLEDGE)
    return classify


@unittest.skipUnless(app, "streamlit not installed")
class AssistantQuestionIsCaughtHoweverItIsPhrased(unittest.TestCase):
    """The live defect: the fixed SCOPE_QUESTIONS list is an exact match, so the same question
    phrased differently ("what can i do by using this agent") reached the agent and was refused.

    ``is_about_assistant`` is the fallback — one short classifier call, run only when the fixed
    list misses. These tests drive ``app.submit`` directly with a scripted classifier, so the
    routing is asserted without an API call and without an AppTest rerun settling the screen.
    """

    ABOUT = ("what can i do by using this agent", "how do I use this?", "what are you for")
    KNOWLEDGE = ("what can I do about the payment outage?",
                 "how do I claim travel expenses?")

    def setUp(self):
        os.environ["MAX_QUESTIONS_PER_DAY"] = "1000000"     # the day cap must not interfere
        self.labels = {t: intent.ABOUT_ASSISTANT for t in self.ABOUT}
        self.labels.update({t: intent.KNOWLEDGE for t in self.KNOWLEDGE})
        self.saved = app.intent.classify
        app.intent.classify = _scripted_classifier(self.labels)

    def tearDown(self):
        app.intent.classify = self.saved

    def _submit(self, question, user="jdoe", ask=None):
        """Run submit() against the stub. ``ask`` collects the agent-path calls when given."""
        stub = _StubSt()
        stub.session_state = _AttrDict({"llm": object(), "user": user})
        saved = (app.st, app.time.sleep, app.ask)
        app.st, app.time.sleep = stub, lambda _seconds: None
        if ask is not None:
            app.ask = lambda u, q, s: ask.append((u, q))
        try:
            try:
                app.submit(user, question, "Searching…")
            except _Rerun:
                pass
        finally:
            app.st, app.time.sleep, app.ask = saved
        return stub

    def test_about_assistant_phrasings_show_the_scope_answer(self):
        for text in self.ABOUT:
            stub = self._submit(text)
            turn = stub.session_state["turns::jdoe"][-1]
            self.assertEqual(turn["kind"], "scope", text)
            self.assertFalse(turn["refused"], text)
            self.assertEqual(turn["steps"], [], text)
            self.assertEqual(turn["citations"], [], text)
            # the deterministic answer, not model-written text
            self.assertEqual(turn["answer"], app.scope_answer("jdoe"), text)
            self.assertIn("You can read", turn["answer"], text)

    def test_knowledge_phrasings_still_reach_the_agent(self):
        for text in self.KNOWLEDGE:
            asked = []
            stub = self._submit(text, ask=asked)
            self.assertEqual(asked, [("jdoe", text)], text)
            self.assertNotIn("turns::jdoe", stub.session_state, text)

    def test_the_fixed_list_is_a_zero_cost_fast_path(self):
        """An exact match must not pay for a classifier call."""
        def forbidden(question, llm):
            raise AssertionError("the classifier ran for an exact-match scope question")
        app.intent.classify = forbidden
        stub = self._submit("what can i know in this system?")
        self.assertEqual(stub.session_state["turns::jdoe"][-1]["kind"], "scope")

    def test_a_misclassification_can_only_show_the_user_their_own_scope(self):
        """Even a wrong ABOUT_ASSISTANT label shows scope_answer for *this* user, and nothing
        else — no document they cannot read, no total. That is the worst a mislabel can do."""
        app.intent.classify = lambda question, llm: intent.ABOUT_ASSISTANT   # mislabel everything
        stub = self._submit("how do I claim travel expenses?")
        turn = stub.session_state["turns::jdoe"][-1]
        self.assertEqual(turn["kind"], "scope")
        self.assertEqual(turn["answer"], app.scope_answer("jdoe"))
        docs, readable = app.authz.documents(), set(app.authz.list_readable("jdoe"))
        for doc_id, doc in docs.items():
            if doc_id in readable:
                continue
            self.assertNotIn(doc_id, turn["answer"])
            if doc.get("title"):
                self.assertNotIn(doc["title"], turn["answer"], f"jdoe must not be told of {doc_id}")

    def test_a_greeting_never_pays_for_the_classifier_either(self):
        def forbidden(question, llm):
            raise AssertionError("the classifier ran for a greeting")
        app.intent.classify = forbidden
        stub = self._submit("hello")
        self.assertEqual(stub.session_state["turns::jdoe"][-1]["kind"], "greeting")


@unittest.skipUnless(AppTest, "streamlit not installed")
class SuggestionsStayAvailable(RuntimeFilesUntouched, EnvBase):
    """After every answer the remaining example suggestions are shown under the last turn.

    A click takes exactly the same path as typing. Once all are asked, nothing is shown.
    One-line hints chain the demo scenarios.
    """

    def test_after_one_example_the_other_two_are_still_shown(self):
        os.environ["LLM_MODEL"] = "fake"
        at = self.open()
        first = app.EXAMPLE_QUESTIONS["jdoe"][0]
        [b for b in at.button if b.label == first][0].click().run()
        self.assertFalse(at.exception)

        remaining = [b.label for b in at.button]
        self.assertNotIn(first, remaining)
        for q in app.EXAMPLE_QUESTIONS["jdoe"][1:]:
            self.assertIn(q, remaining)

    def test_after_all_three_examples_no_suggestions_remain(self):
        os.environ["LLM_MODEL"] = "fake"
        at = self.open()
        for q in app.EXAMPLE_QUESTIONS["jdoe"]:
            [b for b in at.button if b.label == q][0].click().run()
        self.assertFalse(at.exception)
        # No remaining suggestion buttons at all
        remaining = [b.label for b in at.button]
        for q in app.EXAMPLE_QUESTIONS["jdoe"]:
            self.assertNotIn(q, remaining)

    def test_typing_an_example_counts_as_asked(self):
        os.environ["LLM_MODEL"] = "fake"
        at = self.open()
        first = app.EXAMPLE_QUESTIONS["jdoe"][0]
        at.chat_input[0].set_value(first).run()
        self.assertFalse(at.exception)

        remaining = [b.label for b in at.button]
        self.assertNotIn(first, remaining)
        self.assertIn(app.EXAMPLE_QUESTIONS["jdoe"][1], remaining)

    def test_a_greeting_keeps_the_full_list(self):
        at = self.open()
        at.chat_input[0].set_value("Hello").run()
        self.assertFalse(at.exception)
        self.assertEqual([b.label for b in at.button][:3], app.EXAMPLE_QUESTIONS["jdoe"])

    def test_a_scope_question_keeps_the_full_list(self):
        at = self.open()
        at.chat_input[0].set_value("what can I know in this system?").run()
        self.assertFalse(at.exception)
        self.assertEqual([b.label for b in at.button][:3], app.EXAMPLE_QUESTIONS["jdoe"])

    def test_hint_for_jdoe_after_root_cause_question(self):
        os.environ["LLM_MODEL"] = "fake"
        at = self.open()
        q = "What was the root cause of the Oct 13 payments incident?"
        at.chat_input[0].set_value(q).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("payments-incident" in c.value for c in at.caption),
                        [c.value for c in at.caption])

    def test_hint_for_wlin_after_runbook_question(self):
        os.environ["LLM_MODEL"] = "fake"
        at = self.open()
        # switch to wlin first
        at.sidebar.selectbox[0].set_value("wlin").run()
        q = "Show me the latest runbook for the payment service"
        at.chat_input[0].set_value(q).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("13:00" in c.value for c in at.caption),
                        [c.value for c in at.caption])

    def test_hint_for_atan_after_a_refusal(self):
        # The fake model never refuses (it always returns citations), so we test the check
        # directly rather than through a full agent run.
        turn = {"question": "Show me the security risk register.", "refused": True, "steps": []}
        matched = any(check(turn) for check, _ in app._NEXT_STEPS.get("atan", []))
        self.assertTrue(matched, "atan's hint must fire on a refused turn")
        _, text = app._NEXT_STEPS["atan"][0]
        self.assertIn("Grace Lim", text)

    def test_no_hint_for_an_unrelated_question(self):
        os.environ["LLM_MODEL"] = "fake"
        at = self.open()
        q = "What rate limit applies to the payment gateway API?"
        at.chat_input[0].set_value(q).run()
        self.assertFalse(at.exception)
        # no "Next step" caption should appear
        self.assertFalse(any("Next step" in c.value for c in at.caption),
                         [c.value for c in at.caption])


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


class _Rerun(Exception):
    """Raised by the stub's st.rerun() to stop a screen exactly where the app would."""


class _StubSlot:
    def __init__(self, sink):
        self._sink = sink

    def markdown(self, body, **kwargs):
        self._sink.append(body)


class _AttrDict(dict):
    """A dict that also answers attribute reads, like ``st.session_state`` does."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _StubSt:
    """The smallest stand-in for `streamlit` that records what a screen renders.

    Used to read the checking screen's own text without a browser: the real screen writes into
    `st.empty()` slots and then calls `st.rerun()`, so an AppTest run never still has the text
    to look at.
    """

    def __init__(self):
        self.markdowns = []
        self.htmls = []
        self.session_state = _AttrDict()
        self.components = types.SimpleNamespace(v1=types.SimpleNamespace(html=self._html))

    def _html(self, body, **kwargs):
        self.htmls.append(body)

    def markdown(self, body, **kwargs):
        self.markdowns.append(body)

    def empty(self):
        return _StubSlot(self.markdowns)

    def rerun(self):
        raise _Rerun()

    def __getattr__(self, name):                     # anything else the screen may touch
        return lambda *a, **k: None


def rendered_screen(app_module, fn, *args, **kwargs):
    """Run a screen function against the stub, with its sleeps removed. Returns the stub."""
    stub = _StubSt()
    saved = (app_module.st, app_module.time.sleep)
    app_module.st, app_module.time.sleep = stub, lambda _seconds: None
    try:
        try:
            fn(*args, **kwargs)
        except _Rerun:
            pass
    finally:
        app_module.st, app_module.time.sleep = saved
    return stub


@unittest.skipUnless(app, "streamlit not installed")
class SignInScreenHidesTheCorpusTotal(unittest.TestCase):
    """Telling a contractor "3 of 91 documents" reveals that 88 documents exist that they
    cannot see — exactly what the design refuses to confirm anywhere else."""

    def test_the_screen_shows_only_this_users_count(self):
        for user in ("jdoe", "atan", "glim"):
            readable = len(app.authz.list_readable(user))
            stub = rendered_screen(app, app.render_checking_screen, user, full=True)
            blob = "\n".join(stub.markdowns)
            self.assertIn(f"you can read {readable} documents", blob, user)
            self.assertEqual(stub.session_state["_login_check_count"], readable, user)

    def test_no_corpus_total_appears_anywhere_on_the_screen(self):
        total = len(app.authz.documents())
        for user in ("jdoe", "atan", "glim"):
            for full in (True, False):
                stub = rendered_screen(app, app.render_checking_screen, user, full=full)
                blob = "\n".join(stub.markdowns + stub.htmls)
                self.assertNotIn(f"of {total}", blob, (user, full))
                self.assertNotIn(f"{total} documents", blob, (user, full))


@unittest.skipUnless(AppTest, "streamlit not installed")
class LoginFlowScreen(unittest.TestCase):
    """Sign-in screen behind the LOGIN_FLOW flag."""

    def setUp(self):
        self.saved_login = os.environ.get("LOGIN_FLOW")
        os.environ["DEMO_SEED_ON_START"] = "0"
        os.environ["MAX_QUESTIONS_PER_DAY"] = "1000000"
        os.environ["DEMO_AUTORESET_IDLE_MINUTES"] = "0"

    def tearDown(self):
        if self.saved_login is None:
            os.environ.pop("LOGIN_FLOW", None)
        else:
            os.environ["LOGIN_FLOW"] = self.saved_login

    def open(self):
        at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
        self.assertFalse(at.exception, f"app raised on first render: {at.exception}")
        return at

    def _settle(self, at, max_runs=8):
        """Let st.rerun() calls from the checking animation settle."""
        for _ in range(max_runs):
            at.run()
            if at.session_state.get("login_stage") == "app":
                break

    def test_login_flow_off_app_unchanged(self):
        """With the flag off the sidebar still shows the original dropdown."""
        os.environ["LOGIN_FLOW"] = "0"
        at = self.open()
        labels = [s.label for s in at.sidebar.selectbox]
        self.assertTrue(any("Sign in as" in l for l in labels),
                        f"sidebar selectbox labels: {labels}")

    def test_login_flow_on_shows_signin(self):
        """With the flag on the first thing shown is the sign-in screen."""
        os.environ["LOGIN_FLOW"] = "1"
        at = self.open()
        self.assertTrue(
            any("Sign in to Internal Brain" in m.value for m in at.markdown),
            [m.value for m in at.markdown],
        )
        # The sidebar must not have the user picker during sign-in.
        self.assertFalse(
            any("Sign in as" in s.label for s in at.sidebar.selectbox),
            [s.label for s in at.sidebar.selectbox],
        )

    def _btn(self, at, key):
        """Find a button by its key."""
        return next(b for b in at.button if b.key == key)

    def test_login_flow_select_and_continue(self):
        """Selecting an account and continuing lands in the app with that user set."""
        os.environ["LOGIN_FLOW"] = "1"
        with unittest.mock.patch.object(app.time, "sleep", lambda _sec: None):
            at = self.open()
            self._btn(at, "pick_jdoe").click().run()
            self.assertEqual(at.session_state.get("login_choice"), "jdoe")
            self.assertTrue(
                any("Continue as Jamie Doe" in b.label for b in at.button),
                [b.label for b in at.button],
            )
            self._btn(at, "login_continue").click().run()
            self._settle(at)
            self.assertEqual(at.session_state.get("user"), "jdoe")
            self.assertEqual(at.session_state.get("login_stage"), "app")
            self.assertTrue(at.session_state.get("login_intro_done"))

    def test_login_flow_jdoe_readable_count(self):
        """After sign-in as jdoe the screen shows exactly len(authz.list_readable)."""
        os.environ["LOGIN_FLOW"] = "1"
        with unittest.mock.patch.object(app.time, "sleep", lambda _sec: None):
            at = self.open()
            self._btn(at, "pick_jdoe").click().run()
            self._btn(at, "login_continue").click().run()
            self._settle(at)
            import authz
            expected = len(authz.list_readable("jdoe"))
            self.assertEqual(at.session_state.get("user"), "jdoe")
            self.assertEqual(at.session_state.get("_login_check_count"), expected)

    def test_login_flow_atan_readable_count(self):
        """After sign-in as atan the screen shows exactly len(authz.list_readable)."""
        os.environ["LOGIN_FLOW"] = "1"
        with unittest.mock.patch.object(app.time, "sleep", lambda _sec: None):
            at = self.open()
            self._btn(at, "pick_atan").click().run()
            self._btn(at, "login_continue").click().run()
            self._settle(at)
            import authz
            expected = len(authz.list_readable("atan"))
            self.assertEqual(at.session_state.get("user"), "atan")
            self.assertEqual(at.session_state.get("_login_check_count"), expected)

    def test_login_flow_switch_user_triggers_checking(self):
        """Switching user from the sidebar shows the shortened checking screen."""
        os.environ["LOGIN_FLOW"] = "1"
        with unittest.mock.patch.object(app.time, "sleep", lambda _sec: None):
            at = self.open()
            # Sign in as jdoe first.
            self._btn(at, "pick_jdoe").click().run()
            self._btn(at, "login_continue").click().run()
            self._settle(at)
            self.assertEqual(at.session_state.get("user"), "jdoe")
            # Switch to atan via the sidebar selectbox.
            at.sidebar.selectbox[0].set_value("atan").run()
            self._settle(at)
            self.assertEqual(at.session_state.get("user"), "atan")
            self.assertEqual(at.session_state.get("login_stage"), "app")


@unittest.skipUnless(os.environ.get("RUN_REAL_LLM") == "1",
                     "set RUN_REAL_LLM=1 (with a real LLM model configured) to call the model")
class IntentWithTheRealModel(unittest.TestCase):
    """The exact phrasing from the live demo, checked against the real model — one call.

    Skipped by default so the suite never touches the network; run it as
    ``RUN_REAL_LLM=1 python -m unittest tests.test_app_demo.IntentWithTheRealModel``.
    """

    def test_the_live_phrasing_is_labelled_about_the_assistant(self):
        llm = app.LLM()
        self.assertFalse(llm.is_fake, "RUN_REAL_LLM=1 needs a real LLM_MODEL configured")
        self.assertEqual(intent.classify("what can i do by using this agent", llm),
                         intent.ABOUT_ASSISTANT)


if __name__ == "__main__":
    unittest.main()
