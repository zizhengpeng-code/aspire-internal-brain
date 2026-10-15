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
"""
import json
import os
import time

import streamlit as st

import authz
import env
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


def budgeted_chat_input(placeholder, spinner_text):
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
        ask(st.session_state.user, question, spinner_text)


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
    for turn in turns(user_id):
        render_turn(turn, docs)

    budgeted_chat_input("Ask about the knowledge base…", "Searching…")


def page_admin():
    st.subheader("Admin — demo state changes")
    st.info(SHARED_STATE_NOTICE)
    st.caption("Each button calls `simulate.py`. State lives in `state/` (git-ignored) so the "
               "committed `data/` files stay pristine.")
    render_last_action()
    left, right = st.columns(2)
    with left:
        if st.button("Push runbook update (13:00)", use_container_width=True):
            warm_index()          # the index a running system would already have
            run_action("Push runbook update (E06 → rev 8)", lambda: simulate.update("E06"),
                       reload_sessions=True)
        if st.button("Revoke jdoe from #payments-incident", use_container_width=True):
            run_action("Revoke jdoe from #payments-incident",
                       lambda: simulate.revoke("jdoe", "#payments-incident"))
        if st.button("Reset demo state", use_container_width=True):
            run_action("Reset (drop state/, restore audit)", simulate.reset, reload_sessions=True)
    with right:
        seq = st.number_input("audit record seq to tamper", min_value=1, value=4, step=1)
        if st.button("Tamper audit record", use_container_width=True):
            run_action(f"Tamper audit record #{int(seq)}", lambda: simulate.tamper(int(seq)))
        count = st.number_input("audit records to truncate", min_value=1, value=2, step=1)
        if st.button("Truncate audit log", use_container_width=True):
            run_action(f"Truncate last {int(count)} audit records", lambda: simulate.truncate(int(count)))
    st.divider()
    st.markdown("**Audit log**")
    records = audit().records()
    st.caption(f"{len(records)} records · {len(audit().checkpoints())} signed checkpoints")
    if records:
        st.dataframe([{k: r.get(k) for k in ("seq", "ts", "event", "user_id", "tool",
                                             "returned", "citations", "refused")}
                      for r in records[-25:]], use_container_width=True)


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
                          for r in records[-100:]], use_container_width=True)

    with tab_ask:
        st.caption("Natural-language audit questions run through the same agent, using its "
                   "`query_audit` tool (bound only for the compliance officer).")
        docs = authz.documents()
        for turn in turns(st.session_state.user):
            render_turn(turn, docs)
        budgeted_chat_input("e.g. Show me everything user jdoe accessed in "
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


# ------------------------------------------------------------------------------ shell
def sidebar():
    with st.sidebar:
        st.title("🔐 Internal Brain")
        st.caption("LumenPay — permission-aware internal assistant (simulated company)")
        st.info(SHARED_STATE_NOTICE, icon="👥")
        users = [u for u in authz.directory().get("users", {}) if not u.startswith("persona_")]
        labels = {u: f"{user_info(u).get('name', u)} — {user_info(u).get('title', '')}" for u in users}
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
        if st.button("Clear this chat", use_container_width=True):
            user = st.session_state.user
            st.session_state[f"turns::{user}"] = []
            st.session_state[f"hist::{user}"] = []
            st.rerun()
        return page


def main():
    st.set_page_config(page_title="Internal Brain — LumenPay", page_icon="🔐", layout="wide")
    ensure_demo_history()          # once per instance: seed the audit history if logs/ is empty
    page = sidebar()
    if st.session_state.get("user") is None:
        st.error("No demo users found in data/directory.json.")
        return
    {"Chat": page_chat, "Admin": page_admin, "Compliance": page_compliance,
     "ROI": page_roi}[page]()


if __name__ == "__main__":      # `streamlit run app.py` executes the module as __main__
    main()
