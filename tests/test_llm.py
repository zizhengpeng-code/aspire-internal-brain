"""The model layer: the on-disk response cache and its thread-safety.

The Streamlit UI runs every rerun in a fresh thread, and it keeps one ``LLM`` object in
``st.session_state``. A sqlite3 connection may only be used from the thread that created
it, so the cache must not hold one — this is the regression the app hit during the first
screenshot pass (the second question raised ``ProgrammingError``).
"""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import HumanMessage  # noqa: E402

from llm import LLM  # noqa: E402

QUESTION = "What is the settlement reconciliation cutoff?"


class LlmCacheAcrossThreads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "llm.sqlite")

    def _in_thread(self, llm, results):
        def run():
            results.append(llm.invoke([HumanMessage(content=QUESTION)]))
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

    def test_the_same_llm_object_works_from_two_threads(self):
        llm = LLM(model="fake", cache_path=self.path)
        results = []
        self._in_thread(llm, results)      # the first Streamlit rerun
        self._in_thread(llm, results)      # a later rerun: a different thread
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].content, results[1].content)

    def test_the_second_thread_is_served_from_the_cache(self):
        llm = LLM(model="fake", cache_path=self.path)
        results = []
        self._in_thread(llm, results)
        self.assertEqual(llm.cache_hits, 0)
        self._in_thread(llm, results)
        self.assertEqual(llm.cache_hits, 1)

    def test_two_llm_objects_share_the_cache_file(self):
        first = LLM(model="fake", cache_path=self.path)
        second = LLM(model="fake", cache_path=self.path)
        a = first.invoke([HumanMessage(content=QUESTION)])
        b = second.invoke([HumanMessage(content=QUESTION)])
        self.assertEqual(a.content, b.content)
        self.assertEqual(second.cache_hits, 1)

    def test_a_cache_less_llm_never_touches_the_disk(self):
        llm = LLM(model="fake", cache_path=None)
        llm.invoke([HumanMessage(content=QUESTION)])
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(llm.cache_hits, 0)


if __name__ == "__main__":
    unittest.main()
