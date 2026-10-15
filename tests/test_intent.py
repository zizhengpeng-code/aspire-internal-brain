"""The intent check that catches a question *about the assistant* the fixed list misses.

The app answers a question about itself ("what can I know here?") from the live permission set,
but it recognised only a fixed list of phrasings, matched on the whole message. The live demo
showed the cost: "what can i do by using this agent" fell past the list and was refused.

``intent.classify`` is the fallback — one short call that labels the message ABOUT_ASSISTANT or
KNOWLEDGE. It only chooses which UI answer to show, it never sees a document, and anything it
cannot parse as the one label counts as KNOWLEDGE. Both directions of that rule are pinned here,
with a scripted model: no API call is made.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import AIMessage  # noqa: E402

import intent  # noqa: E402


class _ScriptedLLM:
    """A model that returns one canned reply and records what it was asked."""

    def __init__(self, reply="", fails=False):
        self.reply = reply
        self.fails = fails
        self.seen = []

    def invoke(self, messages):
        self.seen.append(messages)
        if self.fails:
            raise RuntimeError("model unavailable")
        return AIMessage(content=self.reply)


class ParseReadsOneOfTwoLabels(unittest.TestCase):
    """The output is parsed as one of two labels; anything else is KNOWLEDGE."""

    def test_the_label_is_recognised_however_it_is_written(self):
        for reply in ("ABOUT_ASSISTANT", "about_assistant", "  ABOUT_ASSISTANT\n",
                      "ABOUT ASSISTANT", "ABOUT_ASSISTANT (it asks about the tool)",
                      "About_Assistant."):
            self.assertEqual(intent.parse(reply), intent.ABOUT_ASSISTANT, reply)

    def test_everything_else_is_knowledge(self):
        for reply in ("KNOWLEDGE", "knowledge", "KNOWLEDGE (about the outage)", "", None,
                      "   ", "I am not sure", "42", "ABOUT"):
            self.assertEqual(intent.parse(reply), intent.KNOWLEDGE, reply)

    def test_a_reply_that_merely_mentions_the_word_still_falls_back(self):
        # a hedge must not be read as the label
        for reply in ("not about_assistant", "this is NOT ABOUT_ASSISTANT"):
            self.assertEqual(intent.parse(reply), intent.KNOWLEDGE, reply)


class ClassifySeesNothingButTheQuestion(unittest.TestCase):
    """The call carries one system prompt and the one message — never a document or an ACL."""

    def test_the_question_is_the_only_user_message(self):
        llm = _ScriptedLLM("ABOUT_ASSISTANT")
        self.assertEqual(intent.classify("what are you for", llm), intent.ABOUT_ASSISTANT)
        messages = llm.seen[0]
        self.assertEqual(len(messages), 2, "a system prompt and the message, nothing else")
        self.assertEqual(messages[-1].content, "what are you for")
        self.assertIn("ABOUT_ASSISTANT", messages[0].content)

    def test_a_failed_call_is_a_knowledge_question(self):
        self.assertEqual(intent.classify("anything at all", _ScriptedLLM(fails=True)),
                         intent.KNOWLEDGE)

    def test_a_knowledge_reply_is_knowledge(self):
        self.assertEqual(intent.classify("what was the outage?", _ScriptedLLM("KNOWLEDGE")),
                         intent.KNOWLEDGE)


class TheScriptedFakeModelFallsBack(unittest.TestCase):
    """With the repo's scripted ``fake`` model the classifier can only return KNOWLEDGE, so the
    default (no API key) test runs keep going down the normal agent path."""

    def test_the_fake_model_is_not_mistaken_for_the_label(self):
        from llm import LLM
        llm = LLM(model="fake", cache_path=None)
        for question in ("what can i do by using this agent", "how do i use this?",
                         "what are you for"):
            self.assertEqual(intent.classify(question, llm), intent.KNOWLEDGE, question)


if __name__ == "__main__":
    unittest.main()
