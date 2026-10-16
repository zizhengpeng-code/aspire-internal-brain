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
from html import escape as html_escape

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
# Cited sources carry a glyph, and it is a Material symbol rather than an emoji. An emoji is
# drawn by the operating system, so the same document rendered on macOS, Windows and Android
# comes back as three different pictures in three different colours — and the colour is never
# the brand's. `:material/…:` is Streamlit's own icon syntax: an inline SVG that inherits the
# text colour, so a citation glyph is the same ink as the sentence around it. (This is also the
# first item on the design-intelligence checklist: no emoji as icons.)
PLATFORM_ICON = {"confluence": ":material/description:", "jira": ":material/confirmation_number:",
                 "slack": ":material/chat_bubble:", "gdrive": ":material/folder:"}
PLATFORM_ICON_FALLBACK = ":material/description:"
SEED_SPINNER = ("First start on this instance — building the demo audit history with a few "
                "real queries, so the alert list and the compliance views have something to "
                "show. This happens once…")
SHARED_STATE_NOTICE = (
    "**Shared demo.** Every visitor uses the same instance, so the Admin actions (push "
    "update, revoke, tamper, truncate) change what everyone sees. Press **Reset demo "
    "state** when you are done."
)
DEMO_SCRIPT = [
    "① jdoe: database migration status + Slack blockers → E01/E02/E03/E05, never the 2026-11-07 slip. "
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
    st.caption("Clicking any of these runs it exactly as if you had typed it:")
    note = REFUSED_ON_PURPOSE.get(user_id)
    if note:
        st.caption(f":material/warning: {note}")
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
    st.caption("Still to try. Clicking one runs it exactly as if you had typed it:")
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
            st.caption(f"**Next step:** {text}")
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
    """The sources an answer stands on, three cards to a row.

    Two things were wrong with these side by side and are fixed together, because they are the
    same complaint — the row did not read as a grid:

    * the cards were as tall as their own text, so a title that wrapped to four lines made its card
      a line taller than the two beside it. The three ``Open in …`` links then landed 39px apart
      (measured: ``results/layout-before.txt``) and the row looked broken rather than aligned. Rows
      are now built three at a time, which also makes the *last* row independent: filling columns
      round-robin used to leave column 1 holding two cards while column 3 held one, so nothing
      could line up even in principle. ``_APP_CSS`` does the rest — each card fills its column and
      the footnote is pinned to the bottom, so every card in a row shares a top edge, a bottom edge
      and a link line;
    * the timestamp was the raw ``updated_at``, and ``2026-10-05T09:00:00+08:00`` does not fit a
      third of the transcript. It broke *inside the date*, on a hyphen, as
      ``2026-10-`` / ``05T09:00:00+08:00``. The card shows the day, which fits; a to-the-second
      value belongs on the audit pages, which is where anyone reads one.

    The row is as wide as the set, up to three, and *every* row keeps that width — so one citation
    still gets a full-width card and two still get a half each, but the odd card of a five-source
    answer lines up under the first column at 168px instead of stretching to the full width of the
    transcript and reading as a banner.
    """
    if not citation_ids:
        return
    width = min(len(citation_ids), 3)
    for start in range(0, len(citation_ids), width):
        row = citation_ids[start:start + width]
        for column, doc_id in zip(st.columns(width), row):
            with column:
                with st.container(border=True):
                    _citation_card(doc_id, docs.get(doc_id))


def _citation_card(doc_id, doc):
    """One citation card. The three elements are positional — ``_APP_CSS`` pins the second one to
    the bottom of the card, so the order here is load-bearing, not cosmetic."""
    if not doc:
        st.markdown(f"**{doc_id}**")
        return
    icon = PLATFORM_ICON.get(doc.get("source"), PLATFORM_ICON_FALLBACK)
    st.markdown(f"{icon} **[{doc_id}] {doc['title']}**")
    # Location above the day, as one caption rather than two elements: the card's own 16px gap
    # would put a hole between two elements that belong together. `<br>` rather than markdown's
    # trailing-double-space break, which is invisible in source and dies to any editor that trims
    # whitespace. Both arguments come from document metadata, so both are escaped.
    st.caption(f"{html_escape(authz.location(doc))}<br>{html_escape(doc.get('updated_at', '')[:10])}",
               unsafe_allow_html=True)
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
    # "Chat as <name>" rather than "Chat — <name>": the transcript is answered *as* that person,
    # which is the whole point of the demo, so the heading says the mechanic instead of naming a
    # place. It also stops every page title from being a label followed by a dash fragment.
    st.subheader(f"Chat as {info.get('name', user_id)}")
    st.caption(f"{info.get('title', '')}. Groups {', '.join(user_groups(user_id))}. "
               "Answers are limited to the documents this person can read right now.")
    docs = authz.documents()
    shown = turns(user_id)
    for turn in shown:
        render_turn(turn, docs)
    if not shown:
        # Nothing asked yet: say what this is, and give a first question to click.
        with st.chat_message("assistant"):
            st.markdown(WELCOME)
        if user_id in COMPLIANCE_EXAMPLES:
            st.caption("This account's example questions are audit queries, so they live on the "
                       "**Compliance → Ask (query_audit)** page.")
        render_examples(user_id, "Searching…")
    else:
        last = shown[-1]
        if last.get("kind") in ("greeting", "scope"):
            # Greetings and scope questions keep the full suggestion list under them.
            if user_id in COMPLIANCE_EXAMPLES:
                st.caption("This account's example questions are audit queries, so they live on "
                           "the **Compliance → Ask (query_audit)** page.")
            render_examples(user_id, "Searching…")
        else:
            # After an agent answer: hint about what to do next, then remaining suggestions.
            render_next_step(last, user_id)
            render_remaining_examples(user_id, "Searching…")

    budgeted_chat_input(user_id, "Ask about the knowledge base…", "Searching…")


def page_admin():
    st.subheader("Admin")
    st.caption("Changes the shared demo state that every visitor sees.")
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
    st.caption(f"{len(records)} records, {len(audit().checkpoints())} signed checkpoints")
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
        st.info("No `results/*.agent.json` yet. Run `python evaluate_agent.py`.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Leak rate (Targeted ASR)", f"{measured.get('targeted_asr', float('nan')):.1%}")
        c2.metric("Benign Utility", f"{measured.get('benign_utility', float('nan')):.1%}")
        cost = measured.get("avg_cost")
        c3.metric("Cost / query", "n/a" if cost is None else f"${cost:.4f}")
        c4.metric("Latency / query", f"{measured.get('avg_seconds', 0) * 1000:.0f} ms")
        st.caption(f"subset `{subset}`, model `{results.get('model')}`, "
                   f"{measured.get('n_cases', results.get('n_cases'))} cases, "
                   f"stamped {results.get('stamp')}. Corpus is simulated data.")
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
#
# Screen 2 is one HTML/CSS/JS block rather than a stack of native widgets, because native
# Streamlit widgets cannot animate: the old version could only swap a line of text every few
# hundred milliseconds. The block reproduces the opening of the project's own film
# (../video/src/scenes/OpeningApp.tsx, rendered as ../video/out/opening-v6b.mp4): the card
# fades and slides in, each tick is drawn as a stroke, the readable count runs from zero to
# the real number with an ease-out, and then "Welcome, <first name>" arrives with the name on
# a green highlight. Timings and sizes follow the film; the colours do not — the film's pale
# mint is replaced by the brand green sampled from the Aspire logo, so this screen and the pages
# behind it are one palette. The live values — name, title, the readable count
# and whether the audit chain verifies — are passed in from Python, and nothing else is: there
# is no total anywhere in the block.

# How long each variant of the checking screen animates for, before the app moves on. Matches
# the last step in `_CHECKING_JS`'s timeline plus a moment to read the welcome.
CHECKING_SECONDS = {"full": 3.1, "short": 1.6}
CHECKING_HEIGHT = {"full": 520, "short": 320}

_LOGIN_CSS = """
<style>
  .login-title { color: #131313; font-size: 1.7rem; font-weight: 700; letter-spacing: -0.01em;
                 margin-bottom: 0.2rem; text-align: center; }
  .login-subtitle { color: #555; font-size: 0.95rem; margin-bottom: 1.2rem; text-align: center; }
  .check-row { font-size: 1.05rem; margin: 0.35rem 0; }

  /* Screen 1: one bordered list of accounts, a row turns green with a tick when selected. */
  div.st-key-account_list {
    max-width: 660px; margin: 0 auto; padding: 0 !important;
    border: 1px solid #E4E7E5; border-radius: 18px; overflow: hidden;
    background: #FFFFFF;
  }
  div.st-key-account_list > div { gap: 0 !important; }
  div.st-key-account_list .stButton { width: 100%; }
  div.st-key-account_list .stButton > button {
    width: 100%; min-height: 84px; border: none; border-radius: 0; box-shadow: none;
    background: #FFFFFF; color: #131313;
    padding: 18px 26px !important; text-align: left;
    display: flex; align-items: center; justify-content: flex-start;
    font-size: 1.05rem; font-weight: 600;
    transition: background 0.18s ease, color 0.18s ease;
  }
  div.st-key-account_list .stButton > button p {
    font-size: 1.05rem; font-weight: 600; text-align: left !important; margin: 0;
  }
  div.st-key-account_list .stButton > button > div {
    flex: 1; justify-content: flex-start !important;
  }
  /* Hover is the neutral surface, not a pale green. `#F2F7F4` was a leftover from the rejected
     green-surface era: a tint of the brand green used as a resting/hover *surface*, which is the
     one thing the palette is built to avoid. It also muddied the meaning of the selected row —
     if hovering tints a row green and selecting paints it green, green no longer says "selected".
     Paper to grey keeps green for selection only. */
  div.st-key-account_list .stButton > button:hover { background: #F4F4F4; color: #131313; }
  .acct-divider { height: 1px; background: #E4E7E5; margin: 0; }

  div.st-key-continue_btn .stButton > button {
    min-height: 62px; border: none; border-radius: 14px;
    background: #131313; color: #FFFFFF; font-size: 1.05rem; font-weight: 600;
    transition: background 0.18s ease, color 0.18s ease;
  }
  div.st-key-continue_btn .stButton > button:hover { background: #02D169; color: #131313; }
  div.st-key-continue_btn .stButton > button p { font-weight: 600; }
</style>
"""

# Appended *inside* the <style> block above (not after it — appended CSS outside the tag would
# be rendered as visible text) when a row is selected: the row turns the brand green and a tick is
# pushed to its right edge, exactly as in the film. The green is the same value the app's buttons
# use, so "selected" and "you can act here" are visibly the same colour.
_SELECTED_ROW_CSS = """
  div.st-key-account___UID__, div.st-key-account___UID__ .stButton > button {
    background: #02D169 !important;
  }
  div.st-key-account___UID__ .stButton > button:hover { background: #02D169 !important; }
  div.st-key-account___UID__ .stButton > button::after {
    content: "\\2713"; margin-left: auto; padding-left: 16px;
    color: #131313; font-weight: 700; font-size: 1.15rem; line-height: 1;
  }
"""

# The masthead, and the handful of tokens and the typeface it shares with everything else.
#
# This is injected on *every* screen, including the two sign-in ones, which is why it is separate
# from `_APP_CSS`: the masthead is drawn before a user has been chosen, and a raw `<img>` with no
# sheet behind it would render at its intrinsic 736px and tear the sidebar open. It also owns the
# `@font-face` and the `:root` tokens, so both sheets and both screens read one definition.
#
# The lockup is the maker's mark. The product name under it is set in Jost, which is the same
# Futura lineage as the `aspire` wordmark inside that lockup — including the single-storey `a` —
# so the product reads as part of the same family as the company that makes it. Body copy stays
# on the system stack, which is what the sign-in screen already used.
_BRAND_CSS = """
<style>
  /* Self-hosted, so a deployment does not depend on a font CDN. Variable weight 400-700, latin
     subset only, 26 KB, generated into `static/fonts/` and served by Streamlit's static mount.
     The format hint is plain `woff2`: `woff2-variations` is not a format keyword browsers act on,
     and a source whose hint does not match is skipped silently, which costs the whole typeface
     and leaves the page looking like the default one. The weight *range* is what makes it
     variable. */
  @font-face {
    font-family: 'Jost';
    src: url('/app/static/fonts/jost-latin.woff2') format('woff2');
    font-weight: 400 700; font-style: normal; font-display: swap;
  }
  :root { --ink:#131313; --paper:#FFFFFF; --field:#F4F4F4;
          /* `--green` is the brand green, sampled out of the Aspire artwork itself by
             `scripts/make_brand_assets.py`. Two things about it are deliberate. It replaces a
             pale mint borrowed from the demo film, so the product now matches its own logo. And
             there is no lighter tint of it anywhere: a tint ramp would just reintroduce the pale
             green that read as a different, sickly colour when it covered a whole sidebar, so
             green is always this one value, and always either an action (a button, the send
             arrow, a link's underline) or a selected state — never a resting surface. */
          --green:#02D169;
          --hairline:#E4E7E5; --muted:#6A706C;
          /* The boundary of a *control*, which is a different job from a divider. `--hairline`
             is 1.25:1 against paper — right for a card edge or a table rule, where it only has
             to separate, but too faint for the outline that tells you a button is a button.
             `--control-edge` is the lightest neutral of the same hue that clears 3:1 on both
             surfaces controls actually sit on (3.46:1 on paper, 3.15:1 on the grey field), which
             is what non-text contrast asks of a boundary that carries no other affordance. */
          --control-edge:#868B88;
          --brand-font:'Jost', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }

  .brand { margin: 0 0 1.15rem; }
  .brand img { display: block; width: 130px; height: auto; }
  .brand .product { font-family: var(--brand-font); font-weight: 600; font-size: 1.24rem;
                    line-height: 1.1; letter-spacing: -0.015em; color: var(--ink);
                    margin: 0.72rem 0 0; }
  .brand .promise { color: var(--muted); font-size: 0.78rem; line-height: 1.38;
                    margin: 0.34rem 0 0; }

  /* The shared-demo dialog. It hangs off the masthead button, so it can be opened *before* a
     user has been chosen — which is why these rules live here with the tokens rather than in
     `_APP_CSS`, whose sheet is only injected after the login flow hands over.

     Three cuts, all taken from the measured DOM (`results/_dlg_struct.mjs`): Streamlit's panel
     is a direct child div of `stDialog` at 24px, one step off the radius ramp, so it comes down
     to the 18px the other cards use. The scrim is shipped as a pale grey wash that leaves the
     page behind as loud as the panel; it becomes ink instead, heavy enough to put the page back.
     And the title is a genuine `h2` inside `section[role=dialog]`, outside `stHeading`, so it
     would otherwise render in the theme font while every heading around it is set in the brand
     face. */
  [data-testid="stDialog"] { background: rgba(19, 19, 19, 0.34); }
  [data-testid="stDialog"] > div { border-radius: 18px; }
  [data-testid="stDialog"] h2 { font-family: var(--brand-font); letter-spacing: -0.015em; }
</style>
"""

# The masthead itself. The promise line is the one thing that separates this from any other chat
# box, so it belongs here rather than in a paragraph a visitor has to go looking for. It is kept
# to a single sentence: at this width a second one costs another two lines of the sidebar.
_BRAND_BLOCK = """
<div class="brand">
  <img src="/app/static/brand/aspire-lockup.png" alt="Aspire">
  <p class="product">Internal Brain</p>
  <p class="promise">Answers are limited to what the person signed in can read.</p>
</div>
"""

# The skin for everything *behind* the sign-in screen.
#
# The sign-in and checking screens are bespoke — ink type on paper, a tinted selection, one black
# button — and the pages after them were stock Streamlit, so the two halves of the demo did not
# look like the same product. This sheet carries the same tokens (`--ink`, `--paper`, `--field`,
# `--green`, the `#E4E7E5` hairline already used by `_LOGIN_CSS`) across the app.
#
# `--green` is the *brand* green, `#02D169`, sampled from the Aspire logo itself
# (`scripts/make_brand_assets.py` reads it out of the artwork). The app used to paint its actions
# with a pale mint that came from the demo film, so the product did not match its own logo.
#
# The division of labour is deliberate. Grey `--field` carries every *surface* (sidebar, info
# boxes, table header, the question bubble); a large area of pale green stops reading as the same
# colour the sign-in screen uses for a single selected row. Green is only ever an *action or a
# mark* — a hovered button, a primary button, the send arrow, the underline of a link, the logo —
# and it is always the one brand value, with no lighter tint of it anywhere.
#
# It is injected only once the login flow has handed over (`_inject_app_css`). Most of it is
# colour, type weight, radius and borders, but not all of it: three rules move things, and each one
# is there because a measurement said the default position was wrong. The assistant's avatar is the
# first of them, and it moves twice: it trades Streamlit's robot for the Aspire mark (at 28px two
# letters read as an abstract blob), and it is raised 3px, because a 32px tile whose top is flush
# with a 25.6px first line hangs below the text's optical centre. The other two stretch the citation
# cards to their column and pin each footnote to the bottom, so a row of cards reads as a grid
# instead of three piles of different heights. `__INITIALS__` is the one substitution — the
# signed-in user's initials, the same two letters the sign-in list shows.
#
# Not touched, on purpose:
#   * emoji in headings and icons — that is copy, not styling;
#   * `st.dataframe`'s grid, which is drawn to a canvas: only its frame is restyled here;
#   * `.streamlit/config.toml` sets the accent, because radio dots, sliders and focus rings are
#     painted from `primaryColor` and no stylesheet can reach them.
_APP_CSS = """
<style>
  /* `--ink`, `--paper`, `--field`, `--green`, `--hairline`, `--muted` and the Jost `@font-face`
     are declared once in `_BRAND_CSS`, which is injected on *every* screen including the sign-in
     one. This sheet only adds what the pages behind the sign-in need. */

  /* ---- type -------------------------------------------------------------------------- */
  .stApp { color: var(--ink); }
  [data-testid="stHeading"] h1, [data-testid="stHeading"] h2,
  [data-testid="stHeading"] h3, [data-testid="stHeading"] h4 {
    font-family: var(--brand-font); color: var(--ink); font-weight: 600;
    letter-spacing: -0.012em; }
  [data-testid="stHeading"] h1 { font-size: 1.7rem; }
  [data-testid="stHeading"] h2 { font-size: 1.3rem; }
  /* Captions were rendering in the body colour, so two dozen of them read as more body text. */
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
    color: var(--muted); font-size: 0.82rem; }
  [data-testid="stMarkdownContainer"] code { border-radius: 6px; padding: 0.08rem 0.32rem; }
  /* The theme sets the ink and turns the underline on; the green stroke is the sign-in screen's. */
  [data-testid="stMarkdownContainer"] a { font-weight: 600; text-decoration-thickness: 2px;
    text-decoration-color: var(--green); text-underline-offset: 3px; }
  [data-testid="stMarkdownContainer"] a:hover {
    background: var(--green); text-decoration-color: var(--green); }

  [data-testid="stSidebar"] h1 { font-size: 1.15rem !important; letter-spacing: -0.01em; }

  /* ---- buttons ----------------------------------------------------------------------- */
  /* The green is the *action* colour — a hovered button, a primary button, the send arrow —
     not a surface colour. Idle buttons stay on paper with the hairline, and that restraint is
     what keeps the green meaning "you can act here" instead of becoming wallpaper. */
  /* `showWidgetBorder` gives them the hairline; the ink edge on hover is the sign-in screen's. */
  button[data-testid="stBaseButton-secondary"]:hover {
    background: var(--green) !important; color: var(--ink) !important;
    border-color: var(--ink) !important; }
  button[data-testid="stBaseButton-primary"] {
    background: var(--green); color: var(--ink); border-color: var(--ink); font-weight: 600; }
  button[data-testid="stBaseButton-primary"]:hover {
    background: var(--ink); color: var(--green); border-color: var(--ink); }

  /* ---- chat -------------------------------------------------------------------------- */
  /* The sign-in list introduces people by initials, so the transcript does the same — but the
     two sides are not twins. The person speaking is a paper chip with ink initials and the
     hairline; the product answering is the dark tile carrying the Aspire mark. The chip is paper
     rather than grey on purpose: the question bubble is grey, so a grey chip disappeared into it
     and the initials read as part of the sentence. */
  [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {
    border-radius: 10px; display: flex; align-items: center; justify-content: center;
    /* The tile is 32px; the first line of the message is a 25.6px line box. Flush tops therefore
       leave the tile hanging ~3.2px below the text's optical centre, which is what a reader sees
       as "the avatar is not lined up with the sentence". Half the difference is the fix, and it
       was measured from both ends: the probe puts the tile centre at -494 and the line-box and
       glyph centres at -497, and the reader's own screenshot measures 3.7px. */
    transform: translateY(-3px); }
  /* Streamlit wraps the avatar glyph in a `<span>`, and that span is a flex item. Hiding the
     glyph alone therefore left the wrapper in the flow, still 20px wide inside a 32px tile: the
     mark was pushed to the right edge and — because wrapper and mark together overflowed the tile
     — flex shrank the mark from the 19px it asked for to 12px, so `background: center / contain`
     centred inside a 12px sliver that sat against the corner. Measured, not guessed:
     `results/_avatar_dump.txt`, where `::after` reports `12pxx19px` next to a `span` at `0,6`.

     The previous round did not catch it because the probe measured the tile's outer edges and the
     line of text beside them, and on those numbers the tile was aligned. An element can be in the
     right place and still have the wrong thing drawn inside it, which is what this rule is. */
  [data-testid="stChatMessageAvatarUser"] > *,
  [data-testid="stChatMessageAvatarAssistant"] > * { display: none; }
  [data-testid="stChatMessageAvatarUser"] {
    background: var(--paper) !important; color: var(--ink) !important;
    border: 1px solid var(--hairline);
    font-size: 0.78rem; font-weight: 700; letter-spacing: 0.02em; }
  /* `flex: none` on both: the replacement is the only thing left in the tile and must not be
     treated as a compressible flex item, which is how it lost 7px of width the first time. */
  [data-testid="stChatMessageAvatarUser"]::after { content: "__INITIALS__"; flex: none; }
  [data-testid="stChatMessageAvatarAssistant"] { background: var(--ink) !important; }
  [data-testid="stChatMessageAvatarAssistant"]::after {
    content: ""; display: block; flex: none; width: 19px; height: 19px;
    background: url('/app/static/brand/aspire-mark.png') center / contain no-repeat; }
  /* `layout="wide"` lets a transcript run the full width of the window, which put roughly ninety
     characters on a line — past the point where the eye loses its place on the return sweep. The
     comfortable band is 45-75 characters, with about 66 the ideal.

     The cap was first written as `66ch`, which is the unit the guidance recommends, and it was
     wrong here: `ch` is the advance of the "0" glyph *in the element it is applied to*, and this
     cap is on the row, not on the paragraph inside it. The row carries the chat chrome's own font
     context and its own padding, so `66ch` resolved to a 453px text column and the transcript
     measured 56 characters — under the ideal and visibly cramped. The unit has to be read against
     the element that owns the text, so the cap is a definite length, and that length was set by
     measuring: `42rem` gave 76 characters, `66ch` gave 56, and `37rem` gives 64 — the ideal is
     about 66, and 64 sits on it. (This is the guidance's own caveat about `ch` — it varies by face
     and size, so it has to be tested against real content rather than trusted.) The cap is on the
     *row* rather than on the content because the grey question bubble is painted on the row:
     capping only the content left the bubble full width with the sentence stopping mid-way. */
  [data-testid="stChatMessage"] { border-radius: 18px; max-width: 37rem; }
  /* The question keeps a tinted bubble; the answer stays on paper so citations carry the eye.
     Grey, not green: green is the *action* colour and a whole surface of it read as a different,
     sickly shade. */
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: var(--field); }
  [data-testid="stChatInput"] textarea { color: var(--ink); }
  /* The send arrow is the one green element that is always on screen: it is grey while the box
     is empty and turns green the moment there is something to send. */
  button[data-testid="stChatInputSubmitButton"] {
    background: var(--green); color: var(--ink); border-radius: 10px; }
  button[data-testid="stChatInputSubmitButton"]:hover {
    background: var(--ink); color: var(--green); }
  button[data-testid="stChatInputSubmitButton"][disabled] {
    background: var(--field); color: var(--muted); }

  /* ---- cards: citations, expanders, metrics ------------------------------------------ */
  /* Radii are a ramp, not one value: 12px for a control and for a bordered container, 14px for an
     expander, 18px for a transcript row, 6px for inline code. Using one radius for everything is
     what makes a page read as a pile of identical rounded boxes.

     Read the rung you mean, not the rung you sampled. An expander has two elements that could
     plausibly own its corner, and only one of them does: `summary` measures 12px, `details`
     measures 14px — and `details` is the one carrying the 0.667px border, so 14px is what you see.
     A capability probe that samples `summary` alone will report the expander as a control and
     invite you to "fix" a rule that was already right (`results/_cards_dump.txt` prints both).

     A rule here used to give `stVerticalBlockBorderWrapper` an 18px radius, so a citation card
     would rank above a control. That one really was dead: dumping every testid the running app
     emits shows this build of Streamlit has no such element — a `st.container(border=True)` is a
     plain `stVerticalBlock` that happens to carry a border, which is also why the citation-card
     rules below address the card by its position in the column. The cards keep the theme's
     measured 12px. */
  [data-testid="stExpander"] details { border-radius: 14px; }
  [data-testid="stExpander"] summary { font-weight: 600; }
  [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p { color: var(--muted); }

  /* ---- citation cards: a row, not three piles ------------------------------------------ */
  /* `st.columns` already gives every column in a row the same height — that is its job — but the
     card inside was free to be its own height, so a four-line title made one card taller than the
     two beside it and the three "Open in …" links sat 39px apart. Two rules fix it: the card fills
     the column it sits in, and the footnote is pushed to the bottom of the card, so a card whose
     title wrapped once less gets the slack *above* its footnote rather than under it. Cards in a
     row then share a top edge, a bottom edge and a link line.

     The chain below is Streamlit's real DOM, read off the page rather than assumed (there is no
     `stVerticalBlockBorderWrapper` in 1.65; `st.container(border=True)` is a plain
     `stVerticalBlock` that happens to carry a border, which is why the card is addressed through
     its position in the column instead): stColumn › stVerticalBlock › stLayoutWrapper ›
     stVerticalBlock(the card) › stElementContainer(the footnote). It is scoped to the transcript
     so no other page's columns change shape, and it is the first rule in this sheet that moves
     anything other than the avatar — hence the note in the file header, which used to claim this
     sheet stayed away from layout. */
  [data-testid="stChatMessageContent"] [data-testid="stColumn"] {
    display: flex; flex-direction: column; }
  [data-testid="stChatMessageContent"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
  [data-testid="stChatMessageContent"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"]
    > [data-testid="stLayoutWrapper"] { flex: 1 1 auto; }
  [data-testid="stChatMessageContent"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"]
    > [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"]
    > [data-testid="stElementContainer"]:nth-child(2) { margin-top: auto; }

  /* ---- control boundaries ------------------------------------------------------------ */
  /* The hairline is right for a divider and too faint for a control. On paper it resolves to
     1.25:1 — at that weight a bordered button stops reading as a button at all, which is what
     the zoomed comparison showed. Only the *interactive* boundaries move to `--control-edge`;
     card edges, dividers and the table grid keep the hairline, so the extra weight lands on the
     things you can actually press and the page does not get heavier overall. */
  [data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="base-input"],
  [data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"] {
    border-color: var(--control-edge); }
  button[data-testid="stBaseButton-secondary"] { border-color: var(--control-edge); }
  [data-testid="stChatInput"] [data-baseweb="textarea"],
  [data-testid="stChatInput"] > div { border-color: var(--control-edge); }

  /* ---- data: numbers that line up ----------------------------------------------------- */
  /* Tabular figures everywhere digits are meant to be compared — the audit answers, the ROI
     metrics and the cost figures all put numbers in columns, and proportional digits make a
     column of numbers look ragged. `st.dataframe` is a canvas and stays out of reach; these are
     the HTML surfaces. */
  [data-testid="stMetricValue"], [data-testid="stMetricLabel"],
  [data-testid="stMarkdownContainer"] table td, [data-testid="stMarkdownContainer"] table th,
  [data-testid="stMarkdownContainer"] code {
    font-variant-numeric: tabular-nums; }
  [data-testid="stMarkdownContainer"] table th { text-align: left; }

  /* ---- motion ------------------------------------------------------------------------- */
  /* Streamlit ships every state change as an instant swap: the resolved transition on a button
     is `all 0s`, so a hover goes from paper to green with no interval at all, which reads as a
     flicker rather than a response. 180ms sits in the 150-300ms band the guidance gives. Only
     colour and shadow are named, so nothing here can trigger layout, and the whole block is
     switched off for anyone who has asked for reduced motion. */
  button, [data-testid="stMarkdownContainer"] a,
  [data-baseweb="input"] > div, [data-baseweb="select"] > div,
  [data-testid="stChatInput"] textarea, [data-testid="stExpander"] summary,
  [data-testid="stChatMessageAvatarUser"] {
    transition: background-color 0.18s ease, color 0.18s ease, border-color 0.18s ease,
                box-shadow 0.18s ease; }
  @media (prefers-reduced-motion: reduce) {
    button, [data-testid="stMarkdownContainer"] a,
    [data-baseweb="input"] > div, [data-baseweb="select"] > div,
    [data-testid="stChatInput"] textarea, [data-testid="stExpander"] summary,
    [data-testid="stChatMessageAvatarUser"] { transition: none; }
  }
</style>
"""

# The animated checking screen. `%` is not used for substitution — CSS percentages would fight
# it — so the live values fill unique __TOKENS__ instead, and nothing the visitor typed is ever
# interpolated (only the directory's own name/title, the count and a boolean).
_CHECKING_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  /* The same two values the app uses: ink on paper, and the one brand green — sampled from the
     Aspire logo rather than the film's pale mint, so the checking screen and the pages behind it
     cannot drift apart. `--mist` used to be declared here and was never referenced. */
  :root { --ink:#131313; --paper:#FFFFFF; --green:#02D169;
          --line:#E4E7E5; --soft:rgba(19,19,19,0.55); }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; background:transparent; }
  body { color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         -webkit-font-smoothing:antialiased; }
  .stage { padding: 6px 2px 14px; }

  .head { text-align:center; font-weight:800; font-size:1.85rem; letter-spacing:-0.02em;
          opacity:0; transform:translateY(12px);
          transition:opacity .5s ease, transform .5s cubic-bezier(.65,0,.35,1); }
  .head.in { opacity:1; transform:none; }

  .card { width:100%; max-width:660px; margin:26px auto 0; background:var(--paper);
          border:1px solid var(--line); border-radius:18px; padding:0 30px;
          box-shadow:0 22px 60px rgba(19,19,19,0.06);
          opacity:0; transform:translateY(18px);
          transition:opacity .55s ease, transform .55s cubic-bezier(.65,0,.35,1); }
  .card.in { opacity:1; transform:none; }

  .row { display:flex; align-items:center; gap:22px; min-height:102px;
         border-top:1px solid var(--line);
         opacity:0; transform:translateY(10px);
         transition:opacity .45s ease, transform .45s cubic-bezier(.65,0,.35,1); }
  .row.first { border-top:none; }
  .row.in { opacity:1; transform:none; }

  .mark { flex:0 0 40px; width:40px; height:40px; border-radius:20px;
          border:2px solid var(--line); display:flex; align-items:center; justify-content:center;
          transition:background-color .22s ease, border-color .22s ease; }
  .mark svg { width:20px; height:20px; display:block; }
  .mark .tick, .mark .bang { fill:none; stroke-linecap:round; stroke-linejoin:round;
                             stroke-dasharray:26; stroke-dashoffset:26; }
  .mark .tick { stroke:var(--ink); stroke-width:3; }
  .mark .bang { stroke:#C0392B; stroke-width:3; }
  .row.done .mark { background:var(--green); border-color:var(--ink); }
  .row.done .mark .tick { animation: draw .42s cubic-bezier(.65,0,.35,1) both; }
  .row.failed .mark { background:#FFE6E3; border-color:#C0392B; }
  .row.failed .mark .tick { display:none; }
  .row.failed .mark .bang { animation: draw .42s cubic-bezier(.65,0,.35,1) both; }

  .label { font-size:1.15rem; font-weight:600; }
  .detail { font-size:0.98rem; color:var(--soft); margin-top:3px; }
  .row.failed .detail { color:#C0392B; }
  .count { color:var(--ink); font-weight:700; font-variant-numeric:tabular-nums; }

  .welcome { text-align:center; margin-top:30px; font-weight:800; font-size:2.5rem;
             letter-spacing:-0.03em; opacity:0; transform:translateY(14px);
             transition:opacity .5s ease, transform .5s cubic-bezier(.65,0,.35,1); }
  .welcome.in { opacity:1; transform:none; }
  .hl { background-image:linear-gradient(var(--green),var(--green));
        background-repeat:no-repeat; background-position:0 55%; background-size:0% 86%;
        border-radius:12px; padding:0 14px;
        transition:background-size .5s cubic-bezier(.65,0,.35,1) .08s; }
  .welcome.in .hl { background-size:100% 86%; }

  @keyframes draw { to { stroke-dashoffset:0; } }

  /* Reduced motion: the final state, at once. */
  @media (prefers-reduced-motion: reduce) {
    .head, .card, .row, .welcome, .hl { transition:none !important; }
    .head, .card, .row.in, .welcome.in { opacity:1 !important; transform:none !important; }
    .row.done .mark .tick, .row.failed .mark .bang {
      animation:none !important; stroke-dashoffset:0 !important; }
    .welcome.in .hl { background-size:100% 86% !important; }
  }
</style>
</head>
<body>
  <div class="stage">
    <div class="head">Checking what you can read</div>
    <div class="card" id="card">
__ROWS__
    </div>
    <div class="welcome" id="welcome">Welcome, <span class="hl">__FIRST__</span></div>
  </div>
<script>
(function () {
  var FULL = "__MODE__" === "full";
  var TARGET = __COUNT__;
  var AUDIT_OK = __AUDIT_OK__;
  var counter = document.querySelector(".count");
  var welcome = document.getElementById("welcome");
  var byId = function (id) { return document.getElementById(id); };
  var add = function (el, cls) { if (el) { el.classList.add(cls); } };
  var setCount = function (n) { if (counter) { counter.textContent = String(n); } };
  var R = { identity: byId("row-identity"), permissions: byId("row-permissions"),
            audit: byId("row-audit") };

  function animateCount() {
    var span = FULL ? 1000 : 520;
    var start = null;
    function frame(ts) {
      if (start === null) { start = ts; }
      var p = Math.min(1, (ts - start) / span);
      var eased = 1 - Math.pow(1 - p, 3);        /* ease-out cubic */
      setCount(Math.round(eased * TARGET));
      if (p < 1) { requestAnimationFrame(frame); } else { setCount(TARGET); }
    }
    requestAnimationFrame(frame);
  }

  var reduced = window.matchMedia &&
                window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) {
    add(document.querySelector(".head"), "in");
    add(byId("card"), "in");
    Object.keys(R).forEach(function (k) { add(R[k], "in"); });
    add(R.identity, "done");
    add(R.permissions, "done");
    add(R.audit, AUDIT_OK ? "done" : "failed");
    setCount(TARGET);
    add(welcome, "in");
    return;
  }

  var schedule = FULL ? [
    [60,   function () { add(document.querySelector(".head"), "in"); }],
    [220,  function () { add(byId("card"), "in"); }],
    [340,  function () { add(R.identity, "in"); }],
    [720,  function () { add(R.identity, "done"); }],
    [860,  function () { add(R.permissions, "in"); }],
    [1000, animateCount],
    [2040, function () { add(R.permissions, "done"); }],
    [1500, function () { add(R.audit, "in"); }],
    [1900, function () { add(R.audit, AUDIT_OK ? "done" : "failed"); }],
    [2350, function () { add(welcome, "in"); }]
  ] : [
    [40,   function () { add(document.querySelector(".head"), "in"); }],
    [140,  function () { add(byId("card"), "in"); }],
    [260,  function () { add(R.permissions, "in"); }],
    [380,  animateCount],
    [900,  function () { add(R.permissions, "done"); }],
    [1150, function () { add(welcome, "in"); }]
  ];
  schedule.forEach(function (step) { setTimeout(step[1], step[0]); });
})();
</script>
</body>
</html>
"""

_CHECK_ROW = """
      <div class="row__FIRST__" id="row-__KEY__">
        <div class="mark">
          <svg viewBox="0 0 24 24">
            <path class="tick" d="M5 12.5l4.5 4.5L19 7.5"></path>
            <path class="bang" d="M12 5.5v9.5"></path>
            <circle class="bang" cx="12" cy="18.5" r="0.1"></circle>
          </svg>
        </div>
        <div>
          <div class="label">__LABEL__</div>
          <div class="detail">__DETAIL__</div>
        </div>
      </div>"""


def _check_rows(name, title, readable, audit_ok, full):
    """The rows of the checking screen, as (key, label, detail) triples.

    The permission detail is the *only* place the count appears, and it is this user's count:
    ``you can read <N> documents``. There is no total in the block, on purpose — see
    ``render_checking_screen``.
    """
    count = (f'you can read <span class="count" data-count="{readable}">0</span> documents')
    if not full:
        return [("permissions", "Permissions loaded", count)]
    audit_detail = ("Every answer is logged and signed" if audit_ok
                    else "Verification failed — see Compliance → Chain integrity")
    return [
        ("identity", "Identity confirmed", f"{html_escape(name)}, {html_escape(title)}"),
        ("permissions", "Permissions loaded", count),
        ("audit", "Audit trail ready", audit_detail),
    ]


def initials(name):
    """Two-letter initials, e.g. Jamie Doe → JD."""
    parts = [p for p in name.split() if p]
    return "".join(p[0].upper() for p in parts[:2]) if parts else "?"


def _login_users():
    """Demo accounts from the directory, excluding personas."""
    return [u for u in authz.directory().get("users", {}) if not u.startswith("persona_")]


def _account_chip_css(users):
    """One rule per account row, so the initials live in a chip instead of in the button label.

    A button label carries a single style, so `JD · Jamie Doe — Backend Engineer` read as three
    facts strung together rather than a person with a role. Drawing the chip as a `::before` also
    keeps the `::after` free for the tick that marks the selected row.

    The whole rule is repeated per user rather than shared behind a wildcard, for two reasons that
    both bite silently: the list container's own key is `account_list`, which also starts with
    `account_`, so a `[class*="st-key-account_"]` selector would prepend the box to the list as
    well; and a shared rule would out-specify the per-user `content` once its `:not(...)` is
    counted, leaving every chip empty.
    """
    rules = []
    for uid in users:
        mark = initials(user_info(uid).get("name", uid))
        rules.append(
            f'  div.st-key-account_{uid} .stButton > button::before {{\n'
            f'    content: "{mark}";\n'
            f'    flex: 0 0 36px; width: 36px; height: 36px; border-radius: 10px;\n'
            f'    background: #FFFFFF; color: #131313; border: 1px solid #E4E7E5;\n'
            f'    display: flex; align-items: center; justify-content: center;\n'
            f'    font-size: 0.78rem; font-weight: 700; letter-spacing: 0.02em;\n'
            f'    margin-right: 16px;\n'
            f'  }}')
    return "\n".join(rules)


def render_signin_screen():
    """Screen 1 — choose a demo account, as one list of rows.

    Native widgets, styled with injected CSS to match the film's reference: one bordered list,
    a row that turns green with a tick when selected, and a black rounded "Continue as <name>"
    button below it. The initials are a chip drawn by `_account_chip_css`, not part of the label.
    """
    choice = st.session_state.get("login_choice")
    users = _login_users()

    css = _LOGIN_CSS.replace("</style>", _account_chip_css(users) + "</style>")
    if choice:
        css = css.replace("</style>", _SELECTED_ROW_CSS.replace("__UID__", choice) + "</style>")
    st.markdown(css, unsafe_allow_html=True)

    st.markdown('<div class="login-title">Sign in to Internal Brain</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="login-subtitle">LumenPay is a simulated company. Each demo account '
                'can read a different set of documents, so the same question is answered for one '
                'colleague and refused for another.</div>', unsafe_allow_html=True)

    with st.container(key="account_list"):
        for i, uid in enumerate(users):
            info = user_info(uid)
            name = info.get("name", uid)
            title = info.get("title", "")
            if i:
                st.markdown('<div class="acct-divider"></div>', unsafe_allow_html=True)
            with st.container(key=f"account_{uid}"):
                if st.button(f"{name} — {title}", key=f"pick_{uid}", width="stretch"):
                    st.session_state["login_choice"] = uid
                    st.rerun()

    if choice:
        name = user_info(choice).get("name", choice)
        st.markdown('<div style="height:22px"></div>', unsafe_allow_html=True)
        with st.container(key="continue_btn"):
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

    The whole screen is one HTML/CSS/JS block, because native Streamlit widgets cannot animate
    (the old version could only swap a line of text every few hundred milliseconds). Python
    hands the block the live values it may show — name, title, the readable count, whether the
    audit chain verifies — and nothing else: no total, no withdrawn count. Then it waits out
    the animation and reruns into the app.
    """
    info = user_info(user_id)
    name = info.get("name", user_id)
    title = info.get("title", "")
    readable = len(authz.list_readable(user_id))
    audit_ok = audit().verify_detail()["ok"]
    # Stash the live count so tests can verify it without parsing the component's markup.
    st.session_state["_login_check_count"] = readable

    mode = "full" if full else "short"
    rows = [_CHECK_ROW.replace("__FIRST__", " first" if i == 0 else "")
                        .replace("__KEY__", key)
                        .replace("__LABEL__", label)
                        .replace("__DETAIL__", detail)
            for i, (key, label, detail) in enumerate(_check_rows(name, title, readable, audit_ok,
                                                                 full))]
    body = (_CHECKING_HTML
            .replace("__ROWS__", "".join(rows))
            .replace("__FIRST__", name.split()[0] if name.split() else name)
            .replace("__MODE__", mode)
            .replace("__COUNT__", str(readable))
            .replace("__AUDIT_OK__", "true" if audit_ok else "false"))
    st.components.v1.html(body, height=CHECKING_HEIGHT[mode], scrolling=False)

    # Blank the sign-in screen's element slots *now*, before the wait: Streamlit keeps the
    # previous run's elements on the page until the run ends, and this run ends three seconds
    # from here. Without this the sign-in list sat greyed out underneath the whole animation.
    _clear_trailing_elements()

    time.sleep(CHECKING_SECONDS[mode])

    st.session_state["login_stage"] = "app"
    st.session_state["login_intro_done"] = True
    st.session_state["login_last_user"] = user_id
    # The caller reruns — see handle_login_flow.


def handle_login_flow():
    """Return True when a login screen was drawn (the normal app must not render underneath).

    The checking screen blanks the sign-in screen's element slots before it hands over, because
    Streamlit keeps the previous run's elements on the page until the run *ends* — and this run
    ends three seconds later, after the animation. Without it the sign-in list sat greyed out
    underneath the animation for its whole duration.
    """
    stage = st.session_state.get("login_stage")
    if stage == "checking":
        user_id = st.session_state.get("user")
        if not user_id:
            st.session_state["login_stage"] = None
            st.rerun()
            return True
        render_checking_screen(user_id, full=st.session_state.get("login_full", True))
        st.rerun()
        return True
    if stage == "app":
        return False
    # stage is None — first visit or a recovery
    if st.session_state.get("login_intro_done"):
        return False
    render_signin_screen()
    return True


def _clear_trailing_elements(count=16):
    """Blank the element slots the previous screen filled.

    Streamlit matches elements by position and keeps the previous run's leftovers on the page
    until the run ends. The sign-in screen is a handful of elements, so a run of empty
    placeholders overwrites those slots and leaves only the animated block on the page.
    """
    for _ in range(count):
        st.empty()


# ------------------------------------------------------------------------------ shell

def _inject_app_css(user_id):
    """Skin the signed-in app so the pages match the sign-in screen they follow.

    Called only after `handle_login_flow` has handed over. The sign-in and checking screens
    carry their own stylesheets, and they are rendered with this one absent — otherwise the
    generic button and alert rules here would fight the bespoke list and the black Continue
    button. One substitution: the user's initials, for the transcript avatars.
    """
    name = user_info(user_id).get("name", "")
    st.markdown(_APP_CSS.replace("__INITIALS__", initials(name)), unsafe_allow_html=True)


@st.dialog("Shared demo", icon=":material/groups:")
def _shared_demo_dialog():
    """The shared-instance warning, as a modal rather than a permanent sidebar box.

    The box cost seven lines of the navigation column on every screen, for something a visitor
    reads once. The trigger sits exactly where the box used to be, so the warning is not hidden
    behind a menu — it is one click away instead of always on screen.
    """
    st.markdown(SHARED_STATE_NOTICE)
    st.markdown(
        "Only the **Admin** page changes anything: push the runbook update, revoke a permission, "
        "tamper with an audit record, truncate the log. `data/` and `logs/` are committed; the "
        "working copy lives in `state/`, which is git-ignored."
    )
    st.caption("An instance nobody has touched for a while resets itself when the next visitor "
               "arrives.")
    if st.button("Got it", type="primary", width="stretch"):
        st.session_state["shared_demo_open"] = False
        st.rerun()


def sidebar():
    with st.sidebar:
        # The masthead goes up first, and on the sign-in screens too — which is exactly why its
        # stylesheet is injected here instead of with `_APP_CSS`. An unstyled `<img>` would render
        # at its intrinsic 736px and tear the sidebar open before the login flow has finished.
        st.markdown(_BRAND_CSS, unsafe_allow_html=True)
        st.markdown(_BRAND_BLOCK, unsafe_allow_html=True)
        # The shared-instance warning lives in a dialog. The flag is set here and acted on in
        # `main()` rather than opening the dialog on the spot: Streamlit re-runs the whole script
        # on every interaction inside a dialog, so the flag has to survive that re-run until the
        # visitor acknowledges it, and it can only be opened from the top level of the script.
        if st.button("Shared demo", icon=":material/groups:", key="shared_demo_notice",
                     width="stretch"):
            st.session_state["shared_demo_open"] = True
        outcome = st.session_state.get("auto_reset") or {}
        if outcome.get("reset"):
            st.caption(f":material/refresh: Shared demo state was reset when you arrived — "
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
        st.caption(f"Groups {', '.join(info.get('groups', []))}. "
                   f"Employment {info.get('employment', '')}.")
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
    # The tab carries the mark, so the browser tab is branded too. An absolute path computed from
    # this file, because `streamlit run` does not guarantee the working directory.
    st.set_page_config(
        page_title="Internal Brain — LumenPay",
        page_icon=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "static", "brand", "favicon.png"),
        layout="wide")
    ensure_demo_history()          # once per instance: seed the audit history if logs/ is empty
    if first_render_of_a_browser_session():
        # After the seeding, so the reset event lands in a log that exists.
        st.session_state.auto_reset = auto_reset_if_idle()
    page = sidebar()
    if st.session_state.get("shared_demo_open"):
        _shared_demo_dialog()
    if login_flow_enabled() and handle_login_flow():
        return
    if st.session_state.get("user") is None:
        st.error("No demo users found in data/directory.json.")
        return
    _inject_app_css(st.session_state.user)
    {"Chat": page_chat, "Admin": page_admin, "Compliance": page_compliance,
     "ROI": page_roi}[page]()


if __name__ == "__main__":      # `streamlit run app.py` executes the module as __main__
    main()
