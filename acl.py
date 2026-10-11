"""Access decisions: each platform's native ACL AND the organisation policy layer.

    decide(user_id, doc)  -> Decision(allow, layer, reason)
    visible_ids(user_id)  -> {doc_id, ...}
    location(doc)         -> "confluence:payment-gateway" / "slack:#db-migration" / ...

Design notes
------------
* Every call re-reads the *current* state, so a revocation or a document update
  takes effect on the very next call (no re-indexing). Files are cached by mtime
  so this stays cheap.
* Runtime state lives in ``state/`` when present (``state/directory.json`` and
  ``state/corpus.json``); otherwise the committed ``data/`` files are used.
  ``simulate.py`` writes to ``state/`` and ``simulate.py reset`` deletes it.
* The platform ACL can only *grant*; the organisation policy layer can only
  *tighten* (never widen). A grant therefore has layer "platform"; a policy
  denial has layer "policy" (used by the audit and the demo).
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
DATA_DIR = os.path.join(ROOT, "data")

_cache = {}


def _resolve(name):
    """Prefer state/<name> when it exists, else data/<name>."""
    staged = os.path.join(STATE_DIR, name)
    return staged if os.path.exists(staged) else os.path.join(DATA_DIR, name)


def _load(name):
    path = _resolve(name)
    mtime = os.path.getmtime(path)
    hit = _cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _cache[name] = (mtime, data)
    return data


def reload():
    """Drop the mtime cache (tests / after writing state files)."""
    _cache.clear()


def corpus_path():
    """Resolved path of the corpus currently in force (state/ or data/)."""
    return _resolve("corpus.json")


def directory_path():
    return _resolve("directory.json")


def directory():
    return _load("directory.json")


def documents():
    return {d["id"]: d for d in _load("corpus.json")["documents"]}


def _flag(name, default=True):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def policy_enabled():
    """CONTRACTOR_BLOCKS_RESTRICTED (default on): contractors never see restricted
    content, even when the platform ACL would allow it."""
    return _flag("CONTRACTOR_BLOCKS_RESTRICTED", True)


class Decision:
    __slots__ = ("allow", "layer", "reason")

    def __init__(self, allow, layer, reason):
        self.allow = allow
        self.layer = layer      # "platform" | "policy"
        self.reason = reason

    def as_dict(self):
        return {"allow": self.allow, "layer": self.layer, "reason": self.reason}

    def __repr__(self):
        return f"Decision(allow={self.allow}, layer={self.layer!r}, reason={self.reason!r})"


def _subject_hit(user_id, user, subject):
    if subject.startswith("user:"):
        return subject[5:] == user_id
    if subject.startswith("group:"):
        return subject[6:] in set(user.get("groups", []))
    return False


def _any(user_id, user, subjects):
    return any(_subject_hit(user_id, user, s) for s in subjects)


def _platform(user_id, user, doc, d):
    """Native per-platform ACL. Returns (ok, reason)."""
    source = doc.get("source")
    acl = doc.get("acl") or {}

    if source == "confluence":
        space = acl.get("space")
        view = d.get("confluence", {}).get("spaces", {}).get(space, {}).get("view", [])
        if not _any(user_id, user, view):
            return False, f"not a viewer of Confluence space '{space}'"
        r = acl.get("restrictions") or {}
        users = [f"user:{u}" for u in r.get("users", [])]
        groups = [f"group:{g}" for g in r.get("groups", [])]
        if (users or groups) and not _any(user_id, user, users + groups):
            return False, "Confluence page restriction excludes this user"
        return True, "Confluence space view + page restriction allow"

    if source == "jira":
        project = acl.get("project")
        browse = d.get("jira", {}).get("projects", {}).get(project, {}).get("browse", [])
        if not _any(user_id, user, browse):
            return False, f"no browse permission on Jira project '{project}'"
        level = acl.get("security_level")
        if level:
            members = d.get("jira", {}).get("security_levels", {}).get(level, [])
            if not _any(user_id, user, members):
                return False, f"not in Jira issue security level '{level}'"
        return True, "Jira browse + issue security level allow"

    if source == "slack":
        channel = acl.get("channel")
        info = d.get("slack", {}).get("channels", {}).get(channel)
        if info is None:
            return False, f"unknown Slack channel '{channel}'"
        member = _any(user_id, user, info.get("members", []))
        if info.get("private"):
            return (True, "member of private Slack channel") if member else \
                   (False, "not a member of private Slack channel")
        if user.get("employment") == "employee":
            return True, "public Slack channel (employee)"
        return (True, "public Slack channel (guest invited)") if member else \
               (False, "external user not a member of public Slack channel")

    if source == "gdrive":
        folder = acl.get("folder")
        file_share = acl.get("shared_with") or []
        folder_share = d.get("gdrive", {}).get("folders", {}).get(folder, {}).get("shared_with", [])
        if _any(user_id, user, file_share + folder_share):
            return True, "Google Drive file/folder share"
        return False, f"no share on Google Drive folder '{folder}'"

    return False, f"unknown source '{source}'"


def decide(user_id, doc):
    d = directory()
    user = d.get("users", {}).get(user_id)
    if user is None:
        return Decision(False, "platform", "unknown user")
    ok, reason = _platform(user_id, user, doc, d)
    if not ok:
        return Decision(False, "platform", reason)
    if policy_enabled() and doc.get("sensitivity") == "restricted" and user.get("employment") == "contractor":
        return Decision(False, "policy", "organisation policy: contractors cannot access restricted content")
    return Decision(True, "platform", reason)


def visible_ids(user_id):
    return {i for i, doc in documents().items() if decide(user_id, doc).allow}


def location(doc):
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
