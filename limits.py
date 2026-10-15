"""Spending limits for the public demo.

The app is deployed where strangers can reach it, so every question is capped twice:
once per browser session and once per calendar day across *all* sessions. Both checks
run *before* the model is called, so a visitor cannot spend past the cap.

    MAX_QUESTIONS_PER_SESSION   default 20    per browser session
    MAX_QUESTIONS_PER_DAY       default 300   across all sessions

The daily counter lives in a tiny JSON file under ``.cache/`` (git-ignored), keyed by
calendar date in UTC so the reset point does not depend on the server's timezone. It is
best-effort by design: a missing or unreadable file counts as zero, and a failed write is
swallowed, so a read-only filesystem degrades to the per-session cap instead of breaking
the chat. Nothing here touches authorization, the guard, the audit log or retrieval.

    verdict = limits.check(questions_asked_this_session)
    if verdict["allowed"]:
        limits.record_question()      # spend one, then call the model
    else:
        show(verdict["message"])
"""
import datetime
import json
import os

DEFAULT_PER_SESSION = 20
DEFAULT_PER_DAY = 300
COUNTER_PATH = os.path.join(".cache", "questions.json")

SESSION_MESSAGE = (
    "You have reached this demo session's limit of {limit} questions. "
    "Reload the page to start a fresh session — this is a shared demo, so please "
    "leave room for other visitors."
)
DAY_MESSAGE = (
    "The demo has reached its shared daily budget of {limit} questions, across all "
    "visitors. Please come back tomorrow."
)


def _int_env(name, default):
    """Read a non-negative integer from the environment, falling back on anything odd."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def per_session_limit():
    return _int_env("MAX_QUESTIONS_PER_SESSION", DEFAULT_PER_SESSION)


def per_day_limit():
    return _int_env("MAX_QUESTIONS_PER_DAY", DEFAULT_PER_DAY)


def today(now=None):
    """The counter's key: the UTC calendar date."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%d")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def day_count(path=COUNTER_PATH, now=None):
    """Questions already asked today, across all sessions (0 if unknown)."""
    value = _read(path).get(today(now))
    return value if isinstance(value, int) and value >= 0 else 0


def record_question(path=COUNTER_PATH, now=None):
    """Count one question against today's budget. Returns the new count."""
    date = today(now)
    data = _read(path)
    count = data.get(date)
    data[date] = count + 1 if isinstance(count, int) and count >= 0 else 1
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass                       # a read-only disk must not break the chat
    return data[date]


def check(session_count, path=COUNTER_PATH, now=None):
    """Decide whether another question may be asked, before any model call.

    ``session_count`` is how many questions this browser session has already asked.
    Pure with respect to the counters: it never records anything, so it is safe to call
    on every render. Returns a dict with ``allowed``, ``reason`` (``None``/``"session"``/
    ``"day"``), a ready-to-show ``message``, and the numbers behind the decision.
    """
    session_limit = per_session_limit()
    day_limit = per_day_limit()
    used_today = day_count(path, now)
    verdict = {"allowed": True, "reason": None, "message": "",
               "session_count": session_count, "session_limit": session_limit,
               "day_count": used_today, "day_limit": day_limit}
    if session_count >= session_limit:
        verdict.update(allowed=False, reason="session",
                       message=SESSION_MESSAGE.format(limit=session_limit))
    elif used_today >= day_limit:
        verdict.update(allowed=False, reason="day",
                       message=DAY_MESSAGE.format(limit=day_limit))
    return verdict
