"""Local authorization backend (pure Python, no service).

Loads the relationship tuples currently in force (``state/tuples.json`` when it is
fresh, otherwise rebuilt from ``state/``/``data/``) and evaluates the model with
the reference evaluator in ``core.py``. Permission is re-evaluated on every call
and the tuple set is cached by mtime, so a revocation (a deleted tuple) takes
effect on the next call without touching the index.
"""
import json
import os

from . import state
from . import tuples as tuples_mod
from .core import Decision, Index, evaluate, platform_viewer


class LocalAuthz:
    def __init__(self):
        self._index = None
        self._key = None

    def invalidate(self):
        self._index = None
        self._key = None

    # --------------------------------------------------------------- internals
    def _state_key(self):
        path = tuples_mod.tuples_path()
        paths = [state.directory_path(), state.corpus_path(),
                 path if os.path.exists(path) else None]
        key = [os.path.getmtime(p) if p and os.path.exists(p) else 0 for p in paths]
        key.append(tuples_mod.policy_enabled())
        return tuple(key)

    def _idx(self):
        key = self._state_key()
        if self._index is None or key != self._key:
            self._index = Index(tuples_mod.current())
            self._key = key
        return self._index

    def _raw(self):
        return list(tuples_mod.current())

    def _persist(self, tuples_list):
        os.makedirs(state.STATE_DIR, exist_ok=True)
        with open(tuples_mod.tuples_path(), "w", encoding="utf-8") as f:
            json.dump(tuples_list, f, ensure_ascii=False, indent=1)
        state.reload()
        self.invalidate()

    # ---------------------------------------------------------------- interface
    def check(self, user_id, doc_id):
        """Decision for (user, document id)."""
        doc = state.documents().get(doc_id)
        if doc is None:
            return Decision(False, "platform", "unknown document")
        if user_id not in state.directory().get("users", {}):
            return Decision(False, "platform", "unknown user")
        return evaluate(self._idx(), doc, user_id)

    def decide(self, user_id, doc):
        """Decision for an arbitrary document *dict* (used by unit tests)."""
        directory = state.directory()
        if user_id not in directory.get("users", {}):
            return Decision(False, "platform", "unknown user")
        if tuples_mod.object_id(doc) is None:
            return Decision(False, "platform", f"unknown source '{doc.get('source')}'")
        index = Index(tuples_mod.build(directory, [doc], policy=tuples_mod.policy_enabled()))
        return evaluate(index, doc, user_id)

    def list_readable(self, user_id):
        """Every document id the user may read right now."""
        if user_id not in state.directory().get("users", {}):
            return set()
        index = self._idx()
        return {doc_id for doc_id, doc in state.documents().items() if evaluate(index, doc, user_id).allow}

    def platform_readers(self, doc_id):
        """Users the *platform* ACL lets read a document (before the policy layer),
        for the over-sharing scan."""
        doc = state.documents().get(doc_id)
        if doc is None:
            return set()
        index = self._idx()
        return {uid for uid in state.directory().get("users", {}) if platform_viewer(index, doc, uid)}

    def write(self, tuples_list):
        """Add tuples (materialises the current set first)."""
        current = self._raw()
        for t in tuples_list:
            if t not in current:
                current.append(t)
        self._persist(current)

    def delete(self, tuples_list):
        """Delete tuples — a revocation is a deleted relationship tuple."""
        self._persist([t for t in self._raw() if t not in tuples_list])
