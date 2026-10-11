"""AI retrieval scope — an exclusion list on top of the source systems' permissions.

A document can be perfectly readable in its source system yet excluded from the AI
assistant (e.g. a report mistakenly placed in an externally shared folder, found by
the over-sharing scan). Excluded documents behave as if they did not exist for
search / open_doc / follow_links, and the audit reason is ``ai_scope_excluded``.
Changing the list is recorded as a ``scope_change`` audit event.

``data/ai_scope.json`` is the committed base; ``state/ai_scope.json`` overrides it.
"""
import json
import os

from authz import location, state

EMPTY = {"exclude_docs": [], "exclude_locations": []}


def current():
    path = state.resolve("ai_scope.json")
    if not os.path.exists(path):
        return {"exclude_docs": [], "exclude_locations": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {"exclude_docs": list(data.get("exclude_docs", [])),
            "exclude_locations": list(data.get("exclude_locations", []))}


def reason(doc):
    """``ai_scope_excluded`` when the document is out of the AI's scope, else None."""
    if doc is None:
        return None
    scope = current()
    if doc.get("id") in scope["exclude_docs"]:
        return "ai_scope_excluded"
    loc = location(doc)
    for prefix in scope["exclude_locations"]:
        if loc == prefix or loc.startswith(prefix):
            return "ai_scope_excluded"
    return None


def allowed_ids(doc_ids, docs):
    """Drop the excluded ids from a candidate set."""
    return {i for i in doc_ids if i in docs and reason(docs[i]) is None}


def write_scope(scope):
    """Persist the exclusion list to state/ai_scope.json."""
    os.makedirs(state.STATE_DIR, exist_ok=True)
    with open(os.path.join(state.STATE_DIR, "ai_scope.json"), "w", encoding="utf-8") as f:
        json.dump({"exclude_docs": list(scope.get("exclude_docs", [])),
                   "exclude_locations": list(scope.get("exclude_locations", []))},
                  f, ensure_ascii=False, indent=1)
    state.reload()
