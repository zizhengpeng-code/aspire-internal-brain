"""Tuple semantics: evaluate the relations declared in ``model.fga``.

This is the pure-Python reference evaluator, shared by the local backend and (as
a test oracle) by the OpenFGA consistency check. It implements the small subset
of Zanzibar semantics the model uses: direct relations, the ``user:*`` wildcard,
``group#member`` subjects, ``X from Y``, ``and``, ``or`` and ``but not``.
"""
from .tuples import object_id


class Decision:
    """Outcome of an access check.

    ``layer`` is ``"platform"`` when the native per-platform ACL decides (grant or
    denial), and ``"policy"`` when the platform ACL would grant but the
    organisation policy layer tightens it to a denial.
    """

    __slots__ = ("allow", "layer", "reason")

    def __init__(self, allow, layer, reason):
        self.allow = allow
        self.layer = layer
        self.reason = reason

    def as_dict(self):
        return {"allow": self.allow, "layer": self.layer, "reason": self.reason}

    def __eq__(self, other):
        return (isinstance(other, Decision) and (self.allow, self.layer) == (other.allow, other.layer))

    def __repr__(self):
        return f"Decision(allow={self.allow}, layer={self.layer!r}, reason={self.reason!r})"


class Index:
    """(object, relation) -> set of subjects, built from a tuple list."""

    def __init__(self, tuples):
        self.by = {}
        for t in tuples:
            self.by.setdefault((t["object"], t["relation"]), set()).add(t["user"])

    def subjects(self, obj, relation):
        return self.by.get((obj, relation), set())


def satisfies(index, subject, user):
    """Does ``user`` satisfy a tuple subject (``user:x``, ``user:*`` or ``group:g#member``)?"""
    if subject == "user:*":
        return True
    if subject.startswith("user:"):
        return subject[len("user:"):] == user
    if subject.startswith("group:") and subject.endswith("#member"):
        group = subject[len("group:"):-len("#member")]
        members = index.subjects(f"group:{group}", "member")
        return f"user:{user}" in members or "user:*" in members
    return False


def _grant(index, obj, relation, user):
    """Any subject of ``obj#relation`` satisfies ``user``."""
    return any(satisfies(index, s, user) for s in index.subjects(obj, relation))


def platform_viewer(index, doc, user):
    """``platform_viewer`` for a document — the native per-platform ACL, before policy."""
    obj = object_id(doc)
    if obj is None:
        return False
    source = doc.get("source")
    if source == "confluence":
        spaces = index.subjects(obj, "space")
        return (any(_grant(index, space, "viewer", user) for space in spaces)
                and _grant(index, obj, "allowed", user))
    if source == "jira":
        browser = any(_grant(index, project, "browser", user) for project in index.subjects(obj, "project"))
        unrestricted = _grant(index, obj, "unrestricted", user)
        member = any(_grant(index, level, "member", user) for level in index.subjects(obj, "security_level"))
        return browser and (unrestricted or member)
    if source == "slack":
        return any(_grant(index, channel, "member", user) or _grant(index, channel, "everyone_in", user)
                   for channel in index.subjects(obj, "channel"))
    if source == "gdrive":
        shared = _grant(index, obj, "shared_with", user)
        inherited = any(_grant(index, folder, "viewer", user) for folder in index.subjects(obj, "parent"))
        return shared or inherited
    return False


def is_blocked(index, doc, user):
    obj = object_id(doc)
    return obj is not None and _grant(index, obj, "blocked", user)


def evaluate(index, doc, user):
    """Full check: platform_viewer, then the policy layer (can_read = platform but not blocked)."""
    if not platform_viewer(index, doc, user):
        return Decision(False, "platform", f"platform ACL denies access to {object_id(doc)}")
    if is_blocked(index, doc, user):
        return Decision(False, "policy", "organisation policy: contractors cannot access restricted content")
    return Decision(True, "platform", f"platform ACL grants access to {object_id(doc)}")
