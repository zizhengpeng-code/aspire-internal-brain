"""One short call that tells a question *about the assistant* from a question about LumenPay.

The app already answers a fixed list of "what can I know here?" phrasings itself, from the live
permission set (``app.scope_answer``). That list is an exact-match one, so the same question
asked a different way — "what can i do by using this agent", say — fell past it and went to the
agent, which could only answer by summarising whatever the retriever happened to return, and on
the live demo sometimes refused instead.

This module adds the cheap fallback: when the exact-match list misses, one short call labels the
message ``ABOUT_ASSISTANT`` or ``KNOWLEDGE``. ``ABOUT_ASSISTANT`` shows the same deterministic
answer the list would have; ``KNOWLEDGE`` goes to the agent exactly as before.

Safety — what the classifier can and cannot do:

* it selects which *UI* answer to show, and nothing else. It cannot change an answer, open a
  document, change a permission or touch the audit log;
* it never sees a document, a citation, the user's name, or the permission set. Its whole input
  is the one message the visitor typed, so there is nothing in it to leak;
* its output is parsed as one of exactly two labels, and **anything else counts as
  ``KNOWLEDGE``** (see ``parse``). A malformed reply therefore degrades to the normal path;
* a *wrong* label can at worst show a signed-in user their own scope — the set of documents they
  can already read — which they can see on request anyway. It can never reveal someone else's
  documents or a document this user may not read.

The call is one turn, one short prompt, one word out: cheap, and cached like every other call by
``llm.LLM``. It is deliberately *not* counted against the demo's question budget — it is a
routing decision, not a question spent (``app.submit`` only spends on the agent path).
"""
import re

from langchain_core.messages import HumanMessage, SystemMessage

ABOUT_ASSISTANT = "ABOUT_ASSISTANT"
KNOWLEDGE = "KNOWLEDGE"

# The label with everything but its letters stripped, so the reply is compared to the label and
# not to its punctuation: "ABOUT_ASSISTANT" and "ABOUT ASSISTANT" both reduce to this.
_LABEL = re.sub(r"[^A-Z]", "", ABOUT_ASSISTANT)

# One system message and one word out. The labels are spelled out so a small model can copy one
# back verbatim, and the split is stated in terms of *what* is asked, not who asks it.
SYSTEM_PROMPT = (
    "You label one message a person sent to an internal assistant at LumenPay.\n"
    "Reply with exactly one word.\n"
    "ABOUT_ASSISTANT — the message asks about the assistant itself: what it can do, how to use "
    "it, what the sender is allowed to see or access, or who or what it is. It is about the "
    "tool, not about the company's documents.\n"
    "KNOWLEDGE — everything else: any question about LumenPay's projects, services, runbooks, "
    "incidents, policies, numbers, people or events.\n"
    "Reply with one word only: ABOUT_ASSISTANT or KNOWLEDGE."
)


def parse(reply):
    """``ABOUT_ASSISTANT`` only when the reply actually says it; every other answer is KNOWLEDGE.

    The reply is reduced to its letters, so "ABOUT_ASSISTANT", "about assistant" and
    "ABOUT_ASSISTANT (it asks about the tool)" all count, while "KNOWLEDGE", "" and anything
    else — including a hedge like "not about_assistant" — fall back to the safe label.
    """
    letters = re.sub(r"[^A-Z]", "", (reply or "").upper())
    return ABOUT_ASSISTANT if letters.startswith(_LABEL) else KNOWLEDGE


def classify(question, llm):
    """Label one message ``ABOUT_ASSISTANT`` or ``KNOWLEDGE``. Never raises, never returns
    anything else: a failed or timed-out call is a ``KNOWLEDGE`` question, so the visitor gets
    the normal agent path rather than an error."""
    try:
        reply = llm.invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(question)])
    except Exception:                                          # noqa: BLE001 - routing must not break the chat
        return KNOWLEDGE
    return parse(getattr(reply, "content", ""))
