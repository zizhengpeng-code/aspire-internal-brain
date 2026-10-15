"""Entry point for the hosted public demo.

The hosting sandbox exposes exactly one HTTP port and nothing else, so the port comes from
``PORT``. Everything else the demo needs is *configuration*, not a secret, and is set here as
a default — ``setdefault``, so a real environment variable supplied by the platform still wins.

``LLM_API_KEY`` is deliberately absent: it is the one secret. ``env.py`` reads it from the
process environment, or from a local ``.env`` that is git-ignored and never committed.

    PORT=8080 python start.py
"""
import os
import sys

# Runtime configuration for the hosted demo. Values match .env.example.
RUNTIME_DEFAULTS = {
    "LLM_BASE_URL": "https://api.deepseek.com/v1",
    "LLM_MODEL": "deepseek-chat",
    "LLM_PRICE_IN_PER_M": "0.28",
    "LLM_PRICE_OUT_PER_M": "0.42",
    # The demo answers from BM25; a fake embedding keeps the vector-index warm-up cheap and
    # needs no embedding API (and therefore no second API key).
    "EMBED_MODEL": "fake",
    "AUTHZ_BACKEND": "local",
    "DEMO_NOW": "2026-10-14T14:05:00+08:00",
    "MAX_QUESTIONS_PER_SESSION": "20",
    "MAX_QUESTIONS_PER_DAY": "300",
    "LOGIN_FLOW": "1",          # show the sign-in screen on the hosted demo
}


def main():
    for key, value in RUNTIME_DEFAULTS.items():
        os.environ.setdefault(key, value)

    port = os.environ.get("PORT")
    if not port:
        sys.exit("PORT is not set; the sandbox injects it")

    # Run from the project root so app.py, data/ and logs/ resolve no matter where the
    # sandbox started us.
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    command = [sys.executable, "-m", "streamlit", "run", "app.py",
               "--server.port", str(port),
               "--server.address", "0.0.0.0",
               "--server.headless", "true",
               # Pin the keep-alive. A visitor who reads the page before typing has been
               # seeing "Please wait…": the WebSocket Streamlit holds open through the
               # sandbox gateway can go away while the tab sits idle, and a 20s ping is
               # cheap insurance against an idle timeout we do not control.
               "--server.websocketPingInterval", "20",
               "--browser.gatherUsageStats", "false"]
    os.execv(sys.executable, command)      # replace this process: the sandbox watches one PID


if __name__ == "__main__":
    main()
