"""Streamlit demo UI for the Internal Brain — the four demo pages (chat, admin, compliance, ROI).

    streamlit run app.py

Four pages:
* **Chat**        — sign in as a demo user, ask questions; the answer is sanitised, the
                    citations become cards built from document metadata, and the agent's
                    tool calls are shown in an expander.
* **Admin**       — the demo state changes (push the 13:00 runbook, revoke a user from a
                    Slack channel, tamper / truncate the audit log, reset).
* **Compliance**  — audit search, the natural-language `query_audit` agent, the alert list,
                    the over-sharing scan (with "Exclude from AI"), and chain verification.
                    Visible only to the compliance officer.
* **ROI**         — assumption inputs (clearly marked) plus the measured leak rate, cost per
                    query and latency read from the newest ``results/`` file.

Identity is chosen here only to *simulate* SSO; the agent always receives the identity
through the LangGraph runtime context, never through the model or the tool arguments.

Two things make the instance usable by strangers: a greeting is answered by the UI itself (no
model call, no audit record, no question spent) and comes with clickable starter questions
tailored to the signed-in user; and the shared state is reset when a visitor arrives after the
Admin page has been quiet for a while, so nobody inherits someone else's revoke or tamper.

A third is the question a stranger asks *about the assistant* rather than about LumenPay —
"what can I know in this system?". The UI answers that one too, from the live permission set:
the counts and the spaces come from the ACL, so the answer is exact rather than guessed, and
it names only what this user may read. A fixed list of phrasings catches the common ones for
free; when a message does not match it, one short classifier call (``intent.py``) decides
whether it was really about the assistant or about LumenPay after all.

A fourth is a real sign-in screen: on the hosted demo the visitor chooses a demo account,
sees a short "Checking what you can read" animation with the live permission count, and only
then enters the app. Behind a ``LOGIN_FLOW`` flag so tests and local runs keep the sidebar
dropdown they already have.
"""
import collections
import json
import os
import re
import time

import streamlit as st

import authz
import env
import intent
import limits
import oversharing
import sanitize
import simulate
from agent import Session, answer as agent_answer
from audit import AuditLog
from llm import LLM

env.load()

AUDIT_PATH = "logs/audit.jsonl"
RESULTS_DIR = "results"
PLATFORM_ICON = {"confluence": "📄", "jira": "🎫", "slack": "💬", "gdrive": "📁"}
SEED_SPINNER = ("First start on this instance — building the demo audit history with a few "
                "real queries, so the alert list and the compliance views have something to "
                "show. This happens once…")
SHARED_STATE_NOTICE = (
    "**Shared demo** — every visitor uses the same instance, so the Admin actions (push "
    "update, revoke, tamper, truncate) change what everyone sees. Press **Reset demo "
    "state** when you are done."
)
DEMO_SCRIPT = [
    "① jdoe: database migration status + Slack blockers → E01/E02/E03, never the 2026-11-07 slip. "
    "Switch to wlin → the slip may appear.",
    "② wlin: latest payment-service runbook → PGW-RB-7. Push the 13:00 update → ask again → psp_failover.",
    "③ atan: Q3 security incident report → refusal. Two more injections → same. TLS exception → refusal.",
    "④ jdoe: root cause of the Oct 13 payments incident → E13. Revoke from #payments-incident → "
    "ask \"repeat…\" → SDK 5.2 is gone.",
    "⑤ glim: everything jdoe accessed in confluence:payment-gateway → table; alert list; E21 in the "
    "over-sharing list → Exclude from AI; verify chain → tamper → truncate.",
]

WELCOME = (
    "**Internal Brain** answers questions about LumenPay's internal knowledge — Slack threads, "
    "Jira tickets, Confluence pages and drive files.\n\n"
    "It answers as the person signed in on the left, and only from the documents that user can "
    "read **right now**. Anything else is not merely left out of the reply: it is never "
    "retrieved in the first place, so the same question can be answered for one colleague and "
    "refused for another. Switch user in the sidebar and try it.\n\n"
    "Ask *what can I know in this system?* at any time and it will list exactly what is in scope "
    "for the user who is signed in."
)

# Starter questions, one set per demo user: the things *that* person can actually get an
# answer to, so a first click is never an accidental refusal. Every one of these was run
# through the real model; Alex Tan's set is the exception, and refuses by design.
EXAMPLE_QUESTIONS = {
    "jdoe": [
        "What's the status of the database migration project and were there any blockers "
        "raised in Slack last week?",
        "What rate limit applies to the payment gateway API?",
        "What does the payments incident thread say about the SDK upgrade?",
    ],
    "wlin": [
        "Show me the latest runbook for the payment service",
        "What was the root cause of the Oct 13 payments incident?",
        "What's the replication lag on the ledger backfill?",
    ],
    "sho": [
        "Which documents mention the PSP failover?",
        "What are the open Jira tickets for the payment gateway?",
        "What does the Q3 incident report say about credential stuffing?",
    ],
    "atan": [
        "Show me the security risk register.",
        "What does the Q3 breach report say about the affected service?",
        "Is there a risk acceptance for legacy TLS on the merchant portal?",
    ],
    "glim": [
        "Show me everything user jdoe accessed in confluence:payment-gateway in the last 30 days",
        "What has user atan been asking for?",
        "Which documents were denied to user atan?",
    ],
}
# Where a refusal is the point of the example rather than a defect in it.
REFUSED_ON_PURPOSE = {
    "atan": "All three are refused on purpose: Alex Tan's QA contract does not cover the "
            "security documents, and a hidden document is indistinguishable from a missing one.",
}
# Grace's examples are audit queries, so they belong on the Compliance → Ask tab rather than
# on the chat page, which points her there instead.
COMPLIANCE_EXAMPLES = {"glim"}

# A greeting is answered by the UI itself — no model call, no audit record, no question spent.
# Only a *whole* short message counts, so "hi, what's the latest runbook…" still reaches the
# agent as the real question it is.
GREETINGS = {"hi", "hi there", "hello", "hello there", "hey", "hey there", "yo", "help",
             "what can you do", "what can you help with", "who are you", "what is this",
             "你好", "您好", "嗨", "你能做什么", "你是谁", "这是什么"}
GREETING_MAX_WORDS = 4

# A question about the assistant rather than about LumenPay's knowledge. The model *can* answer
# these, but only by summarising whatever the retriever happens to return for them — measured on
# the live demo, the same question came back as a full summary, then as a hedged "a search
# doesn't surface a document that describes the system's contents", then as the plain refusal,
# depending on the run. The UI knows the real answer, so it gives it, deterministically.
#
# Matching is on the *whole* normalised message, so "what do you know about" is this question
# while "what do you know about the payments incident" is a normal one and goes to the agent.
# This list is the zero-cost fast path; a phrasing it misses is passed to ``intent.classify``,
# which is the fallback that catches the same question asked another way.
SCOPE_QUESTIONS = {
    "what can i know", "what can i know here", "what can i know in this system",
    "what is there to know", "what can i ask", "what can i ask you", "what can i ask you about",
    "what can you help me with", "what can you help me with in this system",
    "what do you know", "what do you know about", "what can you tell me",
    "what can you tell me about", "what can i access", "what do i have access to",
    "what am i allowed to see", "what can i see", "what data can i see",
    "what documents can i see", "which documents can i see",
    "what documents do i have access to", "what information can i see",
    "what is in scope for me", "what is my scope", "how does this work",
    "what is this system", "what can i look at",
    "我能看到什么", "我能知道什么", "我能问什么", "我能查什么", "我能访问什么",
    "我能看到哪些文档", "这个系统里有什么",
}
SCOPE_MAX_WORDS = 8
# "And what can i know in this system?" is the same question with a connective in front, because
# it followed the welcome text. Dropped before matching so one entry covers both.
LEADING_FILLER = {"and", "so", "but", "ok", "okay", "then", "also", "now", "well"}

# How each source's location is named, for the "what you can read" summary.
SOURCE_LABEL = {"confluence": "Confluence", "jira": "Jira", "slack": "Slack",
                "gdrive": "Google Drive"}
LOCATION_KEYS = (("slack", "channel", "channels"), ("jira", "project", "projects"),
                 ("confluence", "space", "spaces"), ("gdrive", "folder", "folders"))


# --------------------------------------------------------------------------- helpers
def audit():
    return AuditLog(AUDIT_PATH)


def get_llm():
    if "llm" not in st.session_state:
        st.session_state.llm = LLM()
    return st.session_state.llm


def get_session(user_id):
    """One persistent agent session per user (keeps the session id and the multi-turn history)."""
    key = f"session::{user_id}"
    if key not in st.session_state:
        st.session_state[key] = Session(user_id, llm=get_llm(), audit=audit())
    return st.session_state[key]


def turns(user_id):
    return st.session_state.setdefault(f"turns::{user_id}", [])


def history(user_id):
    return st.session_state.setdefault(f"hist::{user_id}", [])


def asked_questions(user_id):
    """The set of questions already submitted by this user in the current session (normalised)."""
    return st.session_state.setdefault(f"asked::{user_id}", set())


def user_info(user_id):
    return authz.directory().get("users", {}).get(user_id, {})


def user_groups(user_id):
    return user_info(user_id).get("groups", [])


def latest_results():
    """Newest evaluate_agent.py result, for the ROI page's measured numbers."""
    if not os.path.isdir(RESULTS_DIR):
        return None
    files = sorted(f for f in os.listdir(RESULTS_DIR) if f.endswith(".agent.json"))
    if not files:
        return None
    with open(os.path.join(RESULTS_DIR, files[-1]), encoding="utf-8") as f:
        return json.load(f)


def run_action(label, fn, reload_sessions=False):
    """Run a simulate.py action, time it, and stash the outcome for display."""
    start = time.perf_counter()
    try:
        result, ok = fn(), True
    except Exception as exc:                                   # noqa: BLE001 - surface it in the UI
        result, ok = f"{type(exc).__name__}: {exc}", False
    elapsed = (time.perf_counter() - start) * 1000
    simulate.note_admin_activity()      # so a later visitor knows the state was left changed
    if reload_sessions:
        for key in list(st.session_state):
            if key.startswith("session::"):
                st.session_state[key].reload()
    st.session_state.last_action = {"label": label, "result": result, "ms": elapsed, "ok": ok}
    st.rerun()


def warm_index():
    """Keep the vector index in sync with the corpus currently in force.

    A running system's index is already warm, which is what makes the next document
    update a genuinely incremental one (only the changed document is re-embedded).
    Best-effort: without embeddings configured the demo still runs, on BM25 alone.
    """
    try:
        return simulate.sync_index()
    except Exception as exc:                                   # noqa: BLE001 - demo only
        return {"skipped": f"{type(exc).__name__}: {exc}"}


def demo_history_missing(path=AUDIT_PATH):
    """True when this instance has no audit history yet (``logs/`` is git-ignored)."""
    return not os.path.exists(path)


def seed_demo_history(path=AUDIT_PATH):
    """Seed the audit history once, if it is missing. Returns a small status dict.

    A fresh cloud instance starts with an empty audit log, because ``logs/`` is not in the
    repository: the alert list would be empty and the compliance officer's ``query_audit``
    about jdoe would find nothing. One run of the existing ``simulate.seed()`` reproduces
    the state the demo script assumes. Set ``DEMO_SEED_ON_START=0`` to skip it.
    """
    if os.environ.get("DEMO_SEED_ON_START", "1") == "0":
        return {"seeded": False, "reason": "disabled by DEMO_SEED_ON_START=0"}
    if not demo_history_missing(path):
        return {"seeded": False, "reason": f"{path} already present"}
    rows = simulate.seed(audit_path=path)
    return {"seeded": True, "queries": len(rows)}


@st.cache_resource(show_spinner=SEED_SPINNER)
def ensure_demo_history():
    """Run the seeding at most once per Streamlit instance (``cache_resource`` is
    process-wide, so concurrent visitors do not each seed the log)."""
    return seed_demo_history()


def first_render_of_a_browser_session():
    """True on the first run of a new browser session — ``st.session_state`` starts empty."""
    if st.session_state.get("browser_session_seen"):
        return False
    st.session_state["browser_session_seen"] = True
    return True


def auto_reset_if_idle():
    """Clean up after the last visitor, for a visitor who arrives once everyone has gone.

    The demo state is shared, so a revoke or a tamper left behind by someone else is the next
    visitor's confusion. ``DEMO_AUTORESET_IDLE_MINUTES`` sets how quiet the Admin page has to
    have been; ``0`` disables it (the tests do that). The decision itself lives in
    ``simulate.reset_if_idle``, and the reset is recorded in the audit log as an admin event.
    """
    raw = os.environ.get("DEMO_AUTORESET_IDLE_MINUTES", "15")
    try:
        minutes = float(raw)
    except ValueError:
        minutes = 15.0
    if minutes <= 0:
        return {"reset": False, "reason": "disabled by DEMO_AUTORESET_IDLE_MINUTES"}
    return simulate.reset_if_idle(idle_minutes=minutes)


def login_flow_enabled():
    """True when the hosted demo should show the sign-in screen instead of the sidebar dropdown."""
    return os.environ.get("LOGIN_FLOW", "0") == "1"


def questions_asked():
    """Questions asked in this browser session, across every signed-in user."""
    return st.session_state.setdefault("questions_asked", 0)


def spend_question():
    """Count one question: against this session and against the shared daily budget."""
    st.session_state["questions_asked"] = questions_asked() + 1
    limits.record_question()


def ask(user_id, question, spinner_text):
    """Run one question and store the turn. The budget has already been checked."""
    spend_question()                    # spend before the model call, never after
    session = get_session(user_id)
    with st.spinner(spinner_text):
        outcome = agent_answer(session, question, history=history(user_id))
    turns(user_id).append({"question": question, "answer": outcome["answer"],
                           "citations": outcome["citations"], "steps": outcome["steps"],
                           "refused": outcome["refused"]})
    st.session_state[f"hist::{user_id}"] = outcome["history"]
    st.rerun()


def keywords(text):
    """Lower-case, strip punctuation, and split — so "Hi!", "  hello  " and "HELLO" all match.

    A leading connective is dropped as well, because a question that follows the welcome text
    tends to arrive as "and what can I know in this system?".
    """
    words = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", (text or "").lower()).strip().split()
    while words and words[0] in LEADING_FILLER:
        words.pop(0)
    return words


def is_greeting(text):
    """True only for a short message that is *nothing but* a greeting.

    Length is the guard that keeps this safe: the whole message has to normalise down to one
    of the known greetings, so "hi, what's the latest runbook for the payment service?" is
    still a question and goes to the agent untouched.
    """
    words = keywords(text)
    if not words or len(words) > GREETING_MAX_WORDS:
        return False
    return " ".join(words) in GREETINGS


def is_scope_question(text):
    """True only for a short message that is *nothing but* a question about the assistant.

    Same whole-message rule as a greeting: "what do you know about" is this question, while
    "what do you know about the payments incident" is a real one and reaches the agent.
    """
    words = keywords(text)
    if not words or len(words) > SCOPE_MAX_WORDS:
        return False
    return " ".join(words) in SCOPE_QUESTIONS


def is_about_assistant(text):
    """A second chance at the question above, for phrasings the fixed list does not carry.

    The list is an exact-match fast path and costs nothing; this is the fallback that runs only
    when the list misses. One short call labels the message ``ABOUT_ASSISTANT`` (about the
    assistant itself) or ``KNOWLEDGE`` (about LumenPay). ``intent.classify`` never raises and
    treats anything but the one label as ``KNOWLEDGE``, so a failure here simply sends the
    message down the normal agent path.
    """
    return intent.classify(text, get_llm()) == intent.ABOUT_ASSISTANT


def example_questions(user_id):
    """The starter questions shown to this user, or none if they have no set."""
    return EXAMPLE_QUESTIONS.get(user_id, [])


def welcome(question):
    """The greeting turn. Built here, never by the model: no API call, no audit record, no
    question spent — a visitor must be able to say hello for free."""
    return {"question": question, "answer": WELCOME, "citations": [], "steps": [],
            "refused": False, "kind": "greeting"}


def scope_answer(user_id):
    """What this signed-in user can read, read straight off the live permission set.

    Deliberately not a model call: the counts and the location names are facts about the ACL,
    and a model asked the same question can only guess at them from whatever the retriever
    returns — which is how a natural first question ended up as "I couldn't find information
    you have access to". It also cannot leak, because it is built from ``list_readable`` and
    nothing else: there is no total, no count of what was left out, and no title of a document
    the user may not see. A hidden document stays indistinguishable from a missing one.
    """
    docs = authz.documents()
    readable = [i for i in authz.list_readable(user_id) if i in docs]
    info = user_info(user_id)
    by_source = collections.Counter(docs[i].get("source", "?") for i in readable)
    counts = ", ".join(f"{n} {SOURCE_LABEL.get(src, src.title())}"
                       for src, n in sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0])))
    where = []
    for src, key, label in LOCATION_KEYS:
        names = sorted({(docs[i].get("acl") or {}).get(key) for i in readable
                        if docs[i].get("source") == src} - {None})
        if names:
            where.append(f"- **{SOURCE_LABEL.get(src, src.title())} {label}** — {', '.join(names)}")
    header = f"**Signed in as {info.get('name', user_id)}** — {info.get('title', '')}"
    if user_groups(user_id):
        header += f" · groups: {', '.join(user_groups(user_id))}"
    parts = [
        header,
        f"You can read **{len(readable)} documents** right now: {counts or 'none'}.",
        "\n".join(where),
        "Anything outside that set is not merely left out of the reply — it is never retrieved "
        "for you in the first place. That is why the same question can be answered for one "
        "colleague and refused for another, and why a document hidden from you looks exactly "
        "like one that does not exist: the refusal is the same sentence either way.",
    ]
    if len(readable) <= 5:
        parts.append("That is a deliberately narrow set, so most questions will be refused for "
                     "this account — the refusal is the permission model working, not a fault in "
                     "the assistant.")
    parts.append("Ask about anything in that set — a project's status, a runbook step, an "
                 "incident's root cause, a rate limit, who decided what and when. The buttons "
                 "below are a place to start, and the sidebar switches user.")
    return "\n\n".join(p for p in parts if p)


def scope_turn(user_id, question):
    """The turn for a question about the assistant. Like a greeting, it costs nothing."""
    return {"question": question, "answer": scope_answer(user_id), "citations": [], "steps": [],
            "refused": False, "kind": "scope"}


def submit(user_id, question, spinner_text):
    """Route one submitted message — typed or clicked, the path is identical.

    A greeting, or a question about the assistant itself, is answered by the UI; anything else
    is budget-checked and then handed to the agent. The example buttons call this, so a clicked
    question is spent, audited, guarded and counted exactly like a typed one.

    The assistant question is matched twice: first against the fixed ``SCOPE_QUESTIONS`` list,
    which costs nothing, and then — only when that misses — through one short classifier call
    that catches the same question phrased differently ("what can i do by using this agent").
    Either way the reply is the deterministic ``scope_answer``; the classifier only decides
    *which* of the two UI answers to give, and no model-written text is ever shown for it. It
    runs before the budget check and spends nothing, so a free UI answer stays free.
    """
    if is_greeting(question):
        turns(user_id).append(welcome(question))
        st.rerun()
        return
    if is_scope_question(question) or is_about_assistant(question):
        turns(user_id).append(scope_turn(user_id, question))
        st.rerun()
        return
    asked_questions(user_id).add(question.strip().lower())
    verdict = limits.check(questions_asked())
    if not verdict["allowed"]:
        st.info(verdict["message"])
        return
    ask(user_id, question, spinner_text)


def render_examples(user_id, spinner_text, on_compliance_page=False):
    """Clickable starter questions for the signed-in user.

    Grace's set is made of audit questions, so it is shown on the Compliance → Ask tab and the
    chat page points her there instead.
    """
    questions = example_questions(user_id)
    if not questions or (user_id in COMPLIANCE_EXAMPLES) != on_compliance_page:
        return
    st.caption("Try one of these — clicking runs it exactly as if you had typed it:")
    note = REFUSED_ON_PURPOSE.get(user_id)
    if note:
        st.caption(f"⚠️ {note}")
    for index, question in enumerate(questions):
        if st.button(question, key=f"example::{user_id}::{index}", width="content"):
            submit(user_id, question, spinner_text)


def render_remaining_examples(user_id, spinner_text):
    """The example questions this user has not yet asked in the current session."""
    questions = example_questions(user_id)
    if not questions or user_id in COMPLIANCE_EXAMPLES:
        return
    asked = asked_questions(user_id)
    remaining = [q for q in questions if q.strip().lower() not in asked]
    if not remaining:
        return
    st.caption("Still to try — clicking runs it exactly as if you had typed it:")
    for index, question in enumerate(remaining):
        if st.button(question, key=f"example::{user_id}::{index}::remain", width="content"):
            submit(user_id, question, spinner_text)


# One-line hints that chain the five demo scenarios. Pure text — no state change, no button.
_NEXT_STEPS = {
    "wlin": [
        (lambda t: "runbook" in keywords(t["question"]),
         "Push the 13:00 update on the **Admin** page, then ask this again — the answer changes."),
    ],
    "jdoe": [
        (lambda t: {"root", "cause"} <= set(keywords(t["question"])),
         "Revoke Jamie from **#payments-incident** on the **Admin** page, then ask "
         "\"Repeat what you just told me about the root cause.\""),
    ],
    "atan": [
        (lambda t: t.get("refused"),
         "Sign in as **Grace Lim** and open **Compliance → Alerts**."),
    ],
}


def render_next_step(turn, user_id):
    """Show a one-line hint for the presenter after a specific turn."""
    for check, text in _NEXT_STEPS.get(user_id, []):
        if check(turn):
            st.caption(f"**Next step** — {text}")
            break


def budgeted_chat_input(user_id, placeholder, spinner_text):
    """The chat input, or a polite notice once the spending limit is reached.

    The check runs here, before the model is ever called, so an exhausted budget cannot
    spend anything.
    """
    verdict = limits.check(questions_asked())
    if not verdict["allowed"]:
        st.info(verdict["message"])
        return
    question = st.chat_input(placeholder)
    if question:
        submit(user_id, question, spinner_text)


def render_citations(citation_ids, docs):
    if not citation_ids:
        return
    columns = st.columns(min(len(citation_ids), 3))
    for i, doc_id in enumerate(citation_ids):
        doc = docs.get(doc_id)
        with columns[i % len(columns)]:
            with st.container(border=True):
                if not doc:
                    st.markdown(f"**{doc_id}**")
                    continue
                icon = PLATFORM_ICON.get(doc.get("source"), "📄")
                st.markdown(f"{icon} **[{doc_id}] {doc['title']}**")
                st.caption(f"{authz.location(doc)} · {doc.get('updated_at', '')}")
                if doc.get("url"):
                    # the URL comes from document metadata, never from model output
                    st.markdown(f"[Open in {doc.get('source')}]({doc['url']})")


def render_turn(turn, docs):
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        text = sanitize.sanitize_answer(turn["answer"])
        if turn["refused"]:
            st.warning(text)
        else:
            st.markdown(text)
        if turn.get("kind") in ("greeting", "scope"):
            return       # nothing was retrieved, so there are no citations or steps to show
        render_citations(turn.get("citations") or [], docs)
        steps = turn.get("steps") or []
        with st.expander(f"Agent steps ({len(steps)})", expanded=False):
            if not steps:
                st.caption("No tool calls this turn.")
            for step in steps:
                st.markdown(f"- round **{step['round']}** · `{step['tool']}`"
                            f"({json.dumps(step['args'], ensure_ascii=False)}) → "
                            f"{step['returned']} result(s)")


def render_last_action():
    action = st.session_state.get("last_action")
    if not action:
        return
    text = (f"**{action['label']}** — {action['ms']:.0f} ms · "
            f"{json.dumps(action['result'], ensure_ascii=False, default=str)[:600]}")
    (st.success if action["ok"] else st.error)(text)


# ----------------------------------------------------------------------------- pages
def page_chat():
    user_id = st.session_state.user
    info = user_info(user_id)
    st.subheader(f"Chat — {info.get('name', user_id)}")
    st.caption(f"{info.get('title', '')} · groups: {', '.join(user_groups(user_id))} · "
               "answers are limited to documents you can read, right now")
    docs = authz.documents()
    shown = turns(user_id)
    for turn in shown:
        render_turn(turn, docs)
    if not shown:
        # Nothing asked yet: say what this is, and give a first question to click.
        with st.chat_message("assistant"):
            st.markdown(WELCOME)
        if user_id in COMPLIANCE_EXAMPLES:
            st.caption("Your example questions are audit queries — they are on the "
                       "**Compliance → Ask (query_audit)** page.")
        render_examples(user_id, "Searching…")
    else:
        last = shown[-1]
        if last.get("kind") in ("greeting", "scope"):
            # Greetings and scope questions keep the full suggestion list under them.
            if user_id in COMPLIANCE_EXAMPLES:
                st.caption("Your example questions are audit queries — they are on the "
                           "**Compliance → Ask (query_audit)** page.")
            render_examples(user_id, "Searching…")
        else:
            # After an agent answer: hint about what to do next, then remaining suggestions.
            render_next_step(last, user_id)
            render_remaining_examples(user_id, "Searching…")

    budgeted_chat_input(user_id, "Ask about the knowledge base…", "Searching…")


def page_admin():
    st.subheader("Admin — demo state changes")
    st.info(SHARED_STATE_NOTICE)
    st.caption("Each button calls `simulate.py`. State lives in `state/` (git-ignored) so the "
               "committed `data/` files stay pristine.")
    render_last_action()
    left, right = st.columns(2)
    with left:
        if st.button("Push runbook update (13:00)", width="stretch"):
            warm_index()          # the index a running system would already have
            run_action("Push runbook update (E06 → rev 8)", lambda: simulate.update("E06"),
                       reload_sessions=True)
        if st.button("Revoke jdoe from #payments-incident", width="stretch"):
            run_action("Revoke jdoe from #payments-incident",
                       lambda: simulate.revoke("jdoe", "#payments-incident"))
        if st.button("Reset demo state", width="stretch"):
            run_action("Reset (drop state/, restore audit)", simulate.reset, reload_sessions=True)
    with right:
        seq = st.number_input("audit record seq to tamper", min_value=1, value=4, step=1)
        if st.button("Tamper audit record", width="stretch"):
            run_action(f"Tamper audit record #{int(seq)}", lambda: simulate.tamper(int(seq)))
        count = st.number_input("audit records to truncate", min_value=1, value=2, step=1)
        if st.button("Truncate audit log", width="stretch"):
            run_action(f"Truncate last {int(count)} audit records", lambda: simulate.truncate(int(count)))
    st.divider()
    st.markdown("**Audit log**")
    records = audit().records()
    st.caption(f"{len(records)} records · {len(audit().checkpoints())} signed checkpoints")
    if records:
        st.dataframe([{k: r.get(k) for k in ("seq", "ts", "event", "user_id", "tool",
                                             "returned", "citations", "refused")}
                      for r in records[-25:]], width="stretch")


def page_compliance():
    if "compliance_officer" not in user_groups(st.session_state.user):
        st.error("The compliance view is restricted to the compliance officer. "
                 "Sign in as **glim** (Grace Lim, Compliance Officer).")
        return
    st.subheader("Compliance")
    tab_audit, tab_ask, tab_alerts, tab_share, tab_chain = st.tabs(
        ["Audit search", "Ask (query_audit)", "Alerts", "Over-sharing", "Chain integrity"])

    with tab_audit:
        log = audit()
        users = sorted({r.get("user_id") for r in log.records() if r.get("user_id")})
        col1, col2, col3, col4 = st.columns(4)
        subject = col1.selectbox("user", ["(any)"] + users)
        doc = col2.text_input("document id (e.g. E06)")
        location = col3.text_input("location prefix (e.g. confluence:payment-gateway)")
        decision = col4.selectbox("decision", ["(any)", "allow", "deny"])
        records = log.query(user=None if subject == "(any)" else subject,
                            doc=doc or None, location_prefix=location or None,
                            decision=None if decision == "(any)" else decision)
        st.caption(f"{len(records)} matching record(s)")
        if records:
            st.dataframe([{k: r.get(k) for k in ("seq", "ts", "event", "user_id", "question",
                                                 "tool", "returned", "refused")}
                          for r in records[-100:]], width="stretch")

    with tab_ask:
        st.caption("Natural-language audit questions run through the same agent, using its "
                   "`query_audit` tool (bound only for the compliance officer).")
        docs = authz.documents()
        shown = turns(st.session_state.user)
        for turn in shown:
            render_turn(turn, docs)
        if not shown:
            with st.chat_message("assistant"):
                st.markdown(WELCOME)
        if not shown or shown[-1].get("kind") in ("greeting", "scope"):
            render_examples(st.session_state.user, "Querying the audit log…",
                            on_compliance_page=True)
        budgeted_chat_input(st.session_state.user,
                            "e.g. Show me everything user jdoe accessed in "
                            "confluence:payment-gateway in the last 30 days",
                            "Querying the audit log…")

    with tab_alerts:
        fired = [r for r in audit().records() if r.get("event") == "alert"]
        st.caption(f"{len(fired)} alert(s) · rule `restricted_deny_burst`: >= 3 probing restricted "
                   "denials by one user within 10 minutes (a document asked for by id, or the top "
                   "match of a search; incidental lower-ranked matches do not count)")
        for record in fired:
            st.warning(f"{record.get('ts')} · **{record.get('user_id')}** · "
                       f"{record.get('count')} probing restricted denials · {record.get('doc_ids')}")

    with tab_share:
        st.caption("Documents the *source system* already shares too widely. The AI did not create "
                   "this; it only makes it findable.")
        findings = oversharing.scan()
        if not findings:
            st.info("No findings.")
        for finding in findings:
            with st.container(border=True):
                st.markdown(f"**{finding.rule} · [{finding.doc_id}] {finding.location}** "
                            f"({finding.sensitivity}, {finding.readers_count} platform readers)")
                st.caption(f"external readers: {', '.join(finding.external_readers) or '—'} · "
                           f"{finding.suggestion}")
                if st.button(f"Exclude from AI: {finding.doc_id}", key=f"excl-{finding.doc_id}"):
                    run_action(f"Exclude {finding.doc_id} from AI", lambda: simulate.exclude(finding.doc_id))
        scope = ai_scope_current()
        st.caption(f"AI exclusion list — docs: {scope['exclude_docs'] or '—'} · "
                   f"locations: {scope['exclude_locations'] or '—'}")

    with tab_chain:
        detail = audit().verify_detail()
        if detail["ok"]:
            st.success("Chain intact: every record's hash links to the previous one, and every "
                       "signed checkpoint matches.")
        else:
            st.error(f"Problem detected — kind: **{detail['kind']}**, first at seq **{detail['seq']}**")
        st.caption(f"{len(audit().records())} records · {len(audit().checkpoints())} checkpoints · "
                   "use the Admin page to tamper or truncate, then verify again.")


def ai_scope_current():
    import ai_scope
    return ai_scope.current()


def page_roi():
    st.subheader("ROI")
    st.caption("Assumptions are marked; only the leak rate, cost per query and latency are measured.")
    results = latest_results()
    sections = (results or {}).get("sections", {})
    # `user` holds the cases the author has confirmed — the only externally quotable numbers.
    # While every case is still a draft that subset is empty, so fall back to `all` and say so
    # rather than rendering a page of nan.
    subset = "user" if (sections.get("user") or {}).get("n_cases") else "all"
    measured = sections.get(subset) or {}

    st.markdown("#### Measured (from the newest `results/` file)")
    if not results:
        st.info("No `results/*.agent.json` yet — run `python evaluate_agent.py`.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Leak rate (Targeted ASR)", f"{measured.get('targeted_asr', float('nan')):.1%}")
        c2.metric("Benign Utility", f"{measured.get('benign_utility', float('nan')):.1%}")
        cost = measured.get("avg_cost")
        c3.metric("Cost / query", "n/a" if cost is None else f"${cost:.4f}")
        c4.metric("Latency / query", f"{measured.get('avg_seconds', 0) * 1000:.0f} ms")
        st.caption(f"subset `{subset}` · model `{results.get('model')}` · "
                   f"{measured.get('n_cases', results.get('n_cases'))} cases · "
                   f"stamp {results.get('stamp')} · corpus is simulated data")
        if subset == "all":
            st.warning("No author-confirmed cases yet, so these are the `all` (internal) numbers — "
                       "confirm the draft cases and rerun before quoting them externally.")

    st.markdown("#### Assumptions (inputs)")
    col1, col2, col3 = st.columns(3)
    employees = col1.number_input("Employees", min_value=1, value=600, step=50,
                                  help="Assumption: from the Aspire brief's company profile.")
    search_share = col2.slider("Share of the week spent finding information", 0.0, 1.0, 0.30, 0.05,
                               help="Assumption: the competition's estimate for Company A.")
    hourly = col3.number_input("Fully-loaded cost per employee-hour (¥)", min_value=0.0, value=120.0,
                               step=10.0, help="Assumption: fill in your own number.")
    col4, col5 = st.columns(2)
    saved_share = col4.slider("Share of search time the assistant removes", 0.0, 1.0, 0.30, 0.05,
                              help="Assumption.")
    monthly_queries = col5.number_input("Queries per month (all staff)", min_value=0, value=6000,
                                        step=500, help="Assumption.")
    weeks_per_year = 46
    hours_saved = employees * search_share * saved_share * 40 * weeks_per_year
    value_saved = hours_saved * hourly
    cost_per_query = measured.get("avg_cost")
    annual_cost = None if cost_per_query is None else cost_per_query * monthly_queries * 12
    c1, c2, c3 = st.columns(3)
    c1.metric("Hours saved / year", f"{hours_saved:,.0f}")
    c2.metric("Value of time saved / year", f"¥{value_saved:,.0f}")
    # the measured per-query cost is in US dollars (LLM_PRICE_*_PER_M), so keep its currency
    c3.metric("Assistant API cost / year (US$)", "n/a" if annual_cost is None else f"US${annual_cost:,.0f}")

    st.markdown("#### Expected loss from over-exposure (assumption × measured)")
    st.caption("Frequency and magnitude are assumption ranges; only the leak rate is measured.")
    col6, col7 = st.columns(2)
    incidents = col6.number_input("Over-exposure incidents per year (assumption)", min_value=0.0,
                                  value=2.0, step=1.0)
    cost_per_incident = col7.number_input("Cost per incident (¥, assumption)", min_value=0.0,
                                          value=500000.0, step=50000.0)
    leak = measured.get("targeted_asr")
    gross = incidents * cost_per_incident
    st.metric("Expected loss without the guardrails (frequency × magnitude)", f"¥{gross:,.0f}")
    if leak is not None and leak == leak:
        st.metric("Residual exposure after the guardrails (× measured leak rate)", f"¥{gross * leak:,.0f}")
    st.caption("Every assumption above is labelled as such and must be replaced with the "
               "presenter's own numbers before the figures are quoted externally.")


# ------------------------------------------------------------------------------ login flow
# Shown on the hosted demo (LOGIN_FLOW=1). A two-screen sign-in: pick an account, watch the
# permission check tick off, then enter the app. Switching user later shows only the check,
# shortened. The flag is off by default so local runs and the test suite need no change.

_LOGIN_CSS = """
<style>
  .login-title { color: #131313; font-size: 1.7rem; font-weight: 600; margin-bottom: 0.2rem; }
  .login-subtitle { color: #555; font-size: 0.95rem; margin-bottom: 1.2rem; }
  .account-card-btn button {
    background: #FFFFFF !important; color: #131313 !important;
    border: 1.5px solid #E3E5E8 !important; border-radius: 14px !important;
    padding: 14px 18px !important; text-align: left !important; font-weight: 500 !important;
    width: 100% !important;
  }
  .account-card-btn button:hover { border-color: #131313 !important; }
  .continue-btn button {
    background: #131313 !important; color: #FFFFFF !important;
    border-radius: 10px !important; font-weight: 500 !important;
  }
  .check-row { font-size: 1.05rem; margin: 0.35rem 0; }
</style>
"""


def initials(name):
    """Two-letter initials, e.g. Jamie Doe → JD."""
    parts = [p for p in name.split() if p]
    return "".join(p[0].upper() for p in parts[:2]) if parts else "?"


def _login_users():
    """Demo accounts from the directory, excluding personas."""
    return [u for u in authz.directory().get("users", {}) if not u.startswith("persona_")]


def render_signin_screen():
    """Screen 1 — choose a demo account."""
    choice = st.session_state.get("login_choice")
    css = _LOGIN_CSS
    if choice:
        css += (f'\ndiv.stVerticalBlock.st-key-account_{choice} '
                f'{{ background: #BEFFCF !important; border-color: #BEFFCF !important; }}')
    st.markdown(css, unsafe_allow_html=True)

    st.markdown('<div class="login-title">Sign in to Internal Brain</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="login-subtitle">Each demo account can read a different set of '
                'documents — the same question may be answered for one colleague and refused '
                'for another.</div>', unsafe_allow_html=True)

    users = _login_users()
    cols = st.columns(2)
    for i, uid in enumerate(users):
        info = user_info(uid)
        name = info.get("name", uid)
        title = info.get("title", "")
        av = initials(name)
        with cols[i % 2]:
            with st.container(key=f"account_{uid}", border=True):
                c1, c2 = st.columns([1, 4])
                with c1:
                    st.markdown(
                        f'<div style="width:40px;height:40px;border-radius:50%;'
                        f'background:#131313;color:#fff;display:flex;'
                        f'align-items:center;justify-content:center;'
                        f'font-weight:600;font-size:0.9rem;">{av}</div>',
                        unsafe_allow_html=True)
                with c2:
                    st.markdown(f"**{name}**  \n{title}")
                if st.button("Select", key=f"pick_{uid}", width="stretch"):
                    st.session_state["login_choice"] = uid
                    st.rerun()

    if choice:
        name = user_info(choice).get("name", choice)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(f"Continue as {name}", key="login_continue", width="stretch"):
            st.session_state["user"] = choice
            st.session_state["login_stage"] = "checking"
            st.session_state["login_full"] = True
            st.session_state["login_choice"] = None
            st.rerun()


def render_checking_screen(user_id, full=True):
    """Screen 2 — tick off identity, permissions and audit chain, then welcome.

    The permission row shows *this user's* count and nothing else. It used to read
    "3 of 91 documents", which hands a contractor the size of the corpus and therefore the
    size of what they are not allowed to see — the one thing the assistant never confirms
    (``scope_answer`` deliberately shows no total and no count of what was left out). The
    number here is exactly ``len(authz.list_readable(user))``.
    """
    info = user_info(user_id)
    name = info.get("name", user_id)
    title = info.get("title", "")
    readable = len(authz.list_readable(user_id))
    audit_ok = audit().verify_detail()["ok"]
    # Stash the live count so tests can verify it without parsing transient markdown.
    st.session_state["_login_check_count"] = readable

    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)
    st.markdown('<div class="login-title">Checking what you can read</div>',
                unsafe_allow_html=True)

    if full:
        s1 = st.empty()
        s2 = st.empty()
        s3 = st.empty()

        s1.markdown('<div class="check-row">⏳ Identity confirmed</div>',
                    unsafe_allow_html=True)
        time.sleep(0.5)
        s1.markdown(f'<div class="check-row">✅ Identity confirmed — {name}, {title}</div>',
                    unsafe_allow_html=True)

        steps = 8
        for i in range(steps + 1):
            n = int(readable * i / steps)
            s2.markdown(f'<div class="check-row">⏳ Permissions loaded — you can read {n} '
                        f'documents</div>', unsafe_allow_html=True)
            time.sleep(0.15)
        s2.markdown(f'<div class="check-row">✅ Permissions loaded — you can read {readable} '
                    f'documents</div>', unsafe_allow_html=True)

        s3.markdown('<div class="check-row">⏳ Audit trail ready</div>',
                    unsafe_allow_html=True)
        time.sleep(0.4)
        if audit_ok:
            s3.markdown('<div class="check-row">✅ Audit trail ready</div>',
                        unsafe_allow_html=True)
        else:
            s3.markdown('<div class="check-row">⚠️ Audit trail ready — verification failed</div>',
                        unsafe_allow_html=True)
        time.sleep(0.3)
    else:
        s = st.empty()
        s.markdown('<div class="check-row">⏳ Permissions loaded</div>',
                   unsafe_allow_html=True)
        time.sleep(0.3)
        s.markdown(f'<div class="check-row">✅ Permissions loaded — you can read {readable} '
                   f'documents</div>', unsafe_allow_html=True)
        time.sleep(0.2)

    st.markdown(f'<div class="login-title" style="margin-top:1rem;">Welcome, '
                f'{name.split()[0]}</div>', unsafe_allow_html=True)
    time.sleep(0.6)

    st.session_state["login_stage"] = "app"
    st.session_state["login_intro_done"] = True
    st.session_state["login_last_user"] = user_id
    st.rerun()


def handle_login_flow():
    """Return True when a login screen was drawn (the normal app must not render underneath)."""
    stage = st.session_state.get("login_stage")
    if stage == "checking":
        user_id = st.session_state.get("user")
        if not user_id:
            st.session_state["login_stage"] = None
            st.rerun()
            return True
        render_checking_screen(user_id, full=st.session_state.get("login_full", True))
        return True
    if stage == "app":
        return False
    # stage is None — first visit or a recovery
    if st.session_state.get("login_intro_done"):
        return False
    render_signin_screen()
    return True


# ------------------------------------------------------------------------------ shell

def sidebar():
    with st.sidebar:
        st.title("🔐 Internal Brain")
        st.caption("LumenPay — permission-aware internal assistant (simulated company)")
        st.info(SHARED_STATE_NOTICE, icon="👥")
        outcome = st.session_state.get("auto_reset") or {}
        if outcome.get("reset"):
            st.caption(f"♻️ Shared demo state was reset when you arrived — {outcome['reason']}, "
                       "so nothing another visitor changed is left over. The reset is in the "
                       "audit log as a `state_reset` admin event.")

        # During the initial sign-in flow the user has not been chosen yet; show a minimal sidebar.
        if login_flow_enabled() and not st.session_state.get("login_intro_done"):
            return "Chat"

        users = [u for u in authz.directory().get("users", {}) if not u.startswith("persona_")]
        labels = {u: f"{user_info(u).get('name', u)} — {user_info(u).get('title', '')}" for u in users}

        if login_flow_enabled():
            # After the intro, the sidebar becomes a switcher; changing user triggers the
            # shortened checking screen.
            st.selectbox("Switch user", users, key="user",
                         format_func=lambda u: labels.get(u, u))
            last = st.session_state.get("login_last_user")
            current = st.session_state.get("user")
            if last is not None and current != last:
                st.session_state["login_stage"] = "checking"
                st.session_state["login_full"] = False
                st.session_state["login_last_user"] = current
                st.rerun()
        else:
            st.session_state.setdefault("user", users[0] if users else None)
            st.selectbox("Sign in as (simulated SSO)", users, key="user",
                         format_func=lambda u: labels.get(u, u))

        info = user_info(st.session_state.user)
        st.caption(f"groups: {', '.join(info.get('groups', []))} · employment: {info.get('employment', '')}")
        groups = user_groups(st.session_state.user)
        pages = ["Chat", "Admin"] + (["Compliance"] if "compliance_officer" in groups else []) + ["ROI"]
        page = st.radio("Page", pages, key="page")
        st.divider()
        with st.expander("Demo script (5 scenarios)"):
            for line in DEMO_SCRIPT:
                st.markdown(f"- {line}")
        if st.button("Clear this chat", width="stretch"):
            user = st.session_state.user
            st.session_state[f"turns::{user}"] = []
            st.session_state[f"hist::{user}"] = []
            st.rerun()
        return page


def main():
    st.set_page_config(page_title="Internal Brain — LumenPay", page_icon="🔐", layout="wide")
    ensure_demo_history()          # once per instance: seed the audit history if logs/ is empty
    if first_render_of_a_browser_session():
        # After the seeding, so the reset event lands in a log that exists.
        st.session_state.auto_reset = auto_reset_if_idle()
    page = sidebar()
    if login_flow_enabled() and handle_login_flow():
        return
    if st.session_state.get("user") is None:
        st.error("No demo users found in data/directory.json.")
        return
    {"Chat": page_chat, "Admin": page_admin, "Compliance": page_compliance,
     "ROI": page_roi}[page]()


if __name__ == "__main__":      # `streamlit run app.py` executes the module as __main__
    main()
