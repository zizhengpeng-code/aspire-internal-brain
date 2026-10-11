"""The .env loader (env.py): parsing, quoting and precedence. Hermetic, no real .env."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import env

SAMPLE = """# comment
export LLM_MODEL=deepseek-chat
LLM_API_KEY="sk-abc"
LLM_BASE_URL=https://api.deepseek.com/v1   # inline comment
EMPTY=
"""


class EnvLoader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, ".env")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SAMPLE)
        for key in ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "EMPTY"):
            saved = os.environ.pop(key, None)
            self.addCleanup(lambda k=key, v=saved: os.environ.__setitem__(k, v) if v is not None
                            else os.environ.pop(k, None))

    def test_parses_quotes_comments_and_export(self):
        env.load(self.path)
        self.assertEqual(os.environ["LLM_API_KEY"], "sk-abc")                 # quotes stripped
        self.assertEqual(os.environ["LLM_BASE_URL"], "https://api.deepseek.com/v1")  # inline # stripped
        self.assertEqual(os.environ["EMPTY"], "")

    def test_real_environment_wins(self):
        os.environ["LLM_MODEL"] = "preexisting"
        env.load(self.path)
        self.assertEqual(os.environ["LLM_MODEL"], "preexisting")

    def test_missing_file_is_a_noop(self):
        self.assertEqual(env.load(os.path.join(self.tmp.name, "nope.env")), [])


if __name__ == "__main__":
    unittest.main()
