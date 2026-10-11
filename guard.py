"""Answer guard: the last check before an answer reaches the user.

``check(answer, seen_ids, user_id)`` returns ``(ok, reason)``. It fails closed: when it
does not pass, the caller replaces the answer with the refusal template, so the three
cases (no results, blocked by access control, blocked by the guard) look identical.

Rules
-----
1. every cited doc id must have been returned by a tool *this turn*;
2. every cited doc id must be visible to the user *right now* (re-checked);
3. a non-refusal answer must cite at least one document — except on an audit turn
   (``query_audit``), whose answer is grounded in audit records, not documents.
"""
import re

import authz

REFUSAL_EN = ("I couldn't find information you have access to that answers this. "
              "If you think you need it, please ask the relevant team owner for access.")
REFUSAL_ZH = ("在你有权限访问的资料里，我没有找到能回答这个问题的内容。"
              "如果你认为需要这些信息，请联系相关团队负责人申请权限。")

CITATION = re.compile(r"\[([A-Za-z]+\d+)\]")


def citations(answer):
    return list(dict.fromkeys(CITATION.findall(answer or "")))


def is_refusal(answer):
    return (answer or "").strip() in (REFUSAL_EN, REFUSAL_ZH)


def filter_history(history, user_id):
    """Drop the turns whose cited documents the user can no longer read.

    Multi-turn history keeps only (question, guarded answer, citations) — never the
    tool outputs. Re-checking the citations before each turn stops the model from
    reciting, from memory, content that has just been revoked.
    """
    visible = authz.list_readable(user_id)
    return [turn for turn in history if set(turn.get("citations") or []) <= visible]


def check(answer, seen_ids, user_id, tools_used=()):
    """``tools_used`` are the tools called this turn. An audit answer (``query_audit``) is
    grounded in audit records rather than documents, so it is allowed to carry no document
    citation; every citation it *does* carry is still checked against rules 1 and 2."""
    text = (answer or "").strip()
    if not text:
        return False, "empty answer"
    if is_refusal(text):
        return True, "refusal template"
    cited = citations(text)
    audit_turn = "query_audit" in set(tools_used)
    if not cited and not audit_turn:
        return False, "answer has no citations"
    seen = set(seen_ids)
    not_returned = [c for c in cited if c not in seen]
    if not_returned:
        return False, f"cites documents not returned this turn: {not_returned}"
    visible = authz.list_readable(user_id)
    forbidden = [c for c in cited if c not in visible]
    if forbidden:
        return False, f"cites documents the user cannot see: {forbidden}"
    return True, "ok"
