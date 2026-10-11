"""Authorization — the only module ``kb.py`` and the tool layer depend on.

Permissions are a relationship-tuple model (Google Zanzibar; OpenFGA is the open
implementation): a **model** (authz/model.fga — object types and how permissions
derive), a set of **tuples** ("who · relation · on what"), and **queries**
(check / list_readable). The per-platform permission semantics are never flattened
into ad-hoc rules; they live in the model.

Two interchangeable backends, chosen with ``AUTHZ_BACKEND=local|openfga`` (default
``local``):

    authz.check(user_id, doc_id)        -> Decision(allow, layer, reason)
    authz.list_readable(user_id)        -> {doc_id, ...}
    authz.platform_readers(doc_id)      -> {user_id, ...}   (over-sharing scan)
    authz.write(tuples) / delete(tuples)                    (revocation)

Permission is resolved on every call (state files are cached by mtime), so a
revocation takes effect immediately and without re-indexing.
"""
import os

from . import state
from .core import Decision
from .local import LocalAuthz
from .openfga import OpenFGAAuthz

# Re-exported state helpers (kb.py / simulate.py / agent.py use these).
from .state import corpus_path, directory_path, directory, documents, document_list  # noqa: F401

_backends = {}


def get_authz():
    """The backend instance for the current ``AUTHZ_BACKEND`` (cached per name)."""
    name = (os.environ.get("AUTHZ_BACKEND") or "local").strip().lower()
    if name not in _backends:
        _backends[name] = OpenFGAAuthz() if name == "openfga" else LocalAuthz()
    return _backends[name]


def reset_backend():
    """Forget cached backends (tests that switch AUTHZ_BACKEND at runtime)."""
    _backends.clear()


# ------------------------------------------------------------------ interface
def check(user_id, doc_id):
    return get_authz().check(user_id, doc_id)


def decide(user_id, doc):
    """Decision for a document *dict* (kept for the unit tests of platform rules)."""
    return get_authz().decide(user_id, doc)


def list_readable(user_id):
    return get_authz().list_readable(user_id)


def visible_ids(user_id):
    """Alias kept for the existing retrieval/evaluation code."""
    return get_authz().list_readable(user_id)


def platform_readers(doc_id):
    return get_authz().platform_readers(doc_id)


def write(tuples_list):
    return get_authz().write(tuples_list)


def delete(tuples_list):
    return get_authz().delete(tuples_list)


def reload():
    """Re-read state/ files and drop every backend's cache."""
    state.reload()
    for backend in _backends.values():
        backend.invalidate()


def location(doc):
    """Human-readable source-system location, e.g. 'confluence:payment-gateway'."""
    acl = doc.get("acl") or {}
    source = doc.get("source")
    if source == "confluence":
        return f"confluence:{acl.get('space')}"
    if source == "jira":
        return f"jira:{acl.get('project')}"
    if source == "slack":
        return f"slack:{acl.get('channel')}"
    if source == "gdrive":
        return f"gdrive:{acl.get('folder')}"
    return source or "unknown"
