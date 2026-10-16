"""Repeat-question reproduction: same code, only `seen` differs.

Run it once with the fix in place, then once with FORCE_EMPTY_SEEN=1 which reproduces the
pre-fix behaviour (`seen=[]`). Prints how many repeats were refused each way.

    python results/repro_repeat.py
    FORCE_EMPTY_SEEN=1 python results/repro_repeat.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
import simulate                    # noqa: E402
from agent import Session, answer  # noqa: E402
from audit import AuditLog         # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

FORCE_EMPTY = os.environ.get("FORCE_EMPTY_SEEN") == "1"

QUESTION = "latest payment-service runbook"
RECITAL = ("The latest runbook is **PGW-RB-8, rev 8, updated 2026-10-14 13:00** [E06]. "
           "It links to one related document, PAY-202 (In Progress, assignee Wei Lin) [E16].")


class _ScriptedReciter:
    """Retrieves for real, then re-states the previous answer (as the live model does)."""

    model = "scripted"
    calls = prompt_tokens = completion_tokens = cache_hits = 0
    cost = 0.0
    price_in = price_out = None

    def __init__(self, query, recital):
        self.query, self.recital = query, recital

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, tools=None):
        self.calls += 1
        if not any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="", tool_calls=[
                {"name": "search", "args": {"query": self.query}, "id": "call_search_1"}])
        return AIMessage(content=self.recital)


def _force_empty_seen():
    """Pre-fix behaviour: the previous turn's citations are not carried into this turn."""
    real = agent.build_graph

    def wrapped(session, max_rounds=agent.MAX_ROUNDS):
        result = real(session, max_rounds=max_rounds)     # (graph, ...) as `answer` unpacks
        graph = result[0]
        inner = graph.invoke

        def invoke(state, *a, **kw):
            return inner(dict(state, seen=[]), *a, **kw)

        graph.invoke = invoke
        return (graph, *result[1:]) if isinstance(result, tuple) else graph

    agent.build_graph = wrapped


def main():
    if FORCE_EMPTY:
        _force_empty_seen()

    simulate.update("E06")                      # the 13:00 revision, as on the demo
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    audit = AuditLog(os.path.join(tmp.name, "audit.jsonl"))

    repeats = 8
    refused = 0
    for i in range(repeats):
        session = Session("wlin", llm=_ScriptedReciter(QUESTION, RECITAL), audit=audit)
        history = [{"question": QUESTION, "answer": RECITAL, "citations": ["E06", "E16"]}]
        result = answer(session, "Show me the latest runbook for the payment service",
                        history=history)
        refused += bool(result["refused"])
        if result["refused"]:
            print(f"  repeat {i + 1}: REFUSED -> {result['guard']}")

    simulate.reset()
    tmp.cleanup()
    label = "seen=[] (pre-fix)" if FORCE_EMPTY else "seen=carried over (current code)"
    print(f"{label}: {refused}/{repeats} repeats refused")


main()
