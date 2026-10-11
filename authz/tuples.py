"""Relationship-tuple generation.

A tuple is ``{"user": <subject>, "relation": <relation>, "object": <type:id>}`` —
the same shape OpenFGA uses. Tuples are derived from two "source-system facts":
``directory.json`` (who belongs to which group; each platform's native sharing)
and ``corpus.json`` (each document's platform location and restrictions). The
organisation policy layer is expressed as extra ``blocked`` tuples (it can only
tighten).

Object ids drop a leading ``#`` and replace spaces with ``-`` so ``#db-migration``
becomes ``slack_channel:db-migration``.
"""
import json
import os

from . import state

TYPE = {"confluence": "confluence_page", "jira": "jira_issue",
        "slack": "slack_thread", "gdrive": "gdrive_file"}


def oid(raw):
    """Object id for a platform object: drop a leading '#', spaces -> '-'."""
    return str(raw).lstrip("#").replace(" ", "-")


def to_subject(raw):
    """Directory subject -> tuple subject: 'group:eng' -> 'group:eng#member'."""
    return raw + "#member" if str(raw).startswith("group:") else raw


def object_id(doc):
    """The OpenFGA object for a document, e.g. 'confluence_page:E01' (None if unknown)."""
    kind = TYPE.get(doc.get("source"))
    return f"{kind}:{doc['id']}" if kind else None


def policy_enabled():
    """CONTRACTOR_BLOCKS_RESTRICTED (default on): contractors never see restricted
    content, even when the platform ACL would allow it."""
    raw = os.environ.get("CONTRACTOR_BLOCKS_RESTRICTED")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def build(directory, documents, policy=True):
    """Generate the full tuple set from a directory and a document list."""
    out = []

    def add(user, relation, obj):
        out.append({"user": user, "relation": relation, "object": obj})

    # --- identity: group membership -------------------------------------------------
    for uid, user in directory.get("users", {}).items():
        for group in user.get("groups", []):
            add(f"user:{uid}", "member", f"group:{group}")

    # --- platform state -------------------------------------------------------------
    for space, cfg in directory.get("confluence", {}).get("spaces", {}).items():
        for subject in cfg.get("view", []):
            add(to_subject(subject), "viewer", f"confluence_space:{oid(space)}")

    for project, cfg in directory.get("jira", {}).get("projects", {}).items():
        for subject in cfg.get("browse", []):
            add(to_subject(subject), "browser", f"jira_project:{oid(project)}")

    for level, subjects in directory.get("jira", {}).get("security_levels", {}).items():
        for subject in subjects:
            add(to_subject(subject), "member", f"jira_security_level:{oid(level)}")

    for channel, cfg in directory.get("slack", {}).get("channels", {}).items():
        for subject in cfg.get("members", []):
            add(to_subject(subject), "member", f"slack_channel:{oid(channel)}")
        if not cfg.get("private"):
            # a public channel is visible to all staff; guests only if listed as members
            add("group:staff#member", "everyone_in", f"slack_channel:{oid(channel)}")

    for folder, cfg in directory.get("gdrive", {}).get("folders", {}).items():
        for subject in cfg.get("shared_with", []):
            add(to_subject(subject), "viewer", f"gdrive_folder:{oid(folder)}")

    # --- documents ------------------------------------------------------------------
    for doc in documents:
        obj = object_id(doc)
        if obj is None:
            continue
        source, acl = doc.get("source"), doc.get("acl") or {}
        if source == "confluence":
            add(f"confluence_space:{oid(acl.get('space'))}", "space", obj)
            restrictions = acl.get("restrictions") or {}
            subjects = ([to_subject(f"user:{u}") for u in restrictions.get("users", [])]
                        + [to_subject(f"group:{g}") for g in restrictions.get("groups", [])])
            if subjects:
                for subject in subjects:
                    add(subject, "allowed", obj)
            else:
                add("user:*", "allowed", obj)
        elif source == "jira":
            add(f"jira_project:{oid(acl.get('project'))}", "project", obj)
            level = acl.get("security_level")
            if level:
                add(f"jira_security_level:{oid(level)}", "security_level", obj)
            else:
                add("user:*", "unrestricted", obj)
        elif source == "slack":
            add(f"slack_channel:{oid(acl.get('channel'))}", "channel", obj)
        elif source == "gdrive":
            add(f"gdrive_folder:{oid(acl.get('folder'))}", "parent", obj)
            for subject in acl.get("shared_with") or []:
                add(to_subject(subject), "shared_with", obj)
        if policy and doc.get("sensitivity") == "restricted":
            add("group:contractors#member", "blocked", obj)

    return out


def tuples_path():
    return os.path.join(state.STATE_DIR, "tuples.json")


def current(policy=None):
    """The tuple set in force.

    Prefer a materialised ``state/tuples.json`` when it is at least as new as the
    directory/corpus it was built from; otherwise rebuild from the current state
    (so an edit to ``state/directory.json`` alone still takes effect). The policy
    flag is honoured even against a materialised file: a policy-off request drops
    the ``blocked`` tuples, and a policy-on request rebuilds if they are missing.
    """
    if policy is None:
        policy = policy_enabled()
    path = tuples_path()
    if os.path.exists(path):
        newest_source = max(os.path.getmtime(state.directory_path()),
                            os.path.getmtime(state.corpus_path()))
        if os.path.getmtime(path) >= newest_source:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            has_blocked = any(t["relation"] == "blocked" for t in data)
            if policy and not has_blocked:
                pass  # materialised without the policy layer -> rebuild below
            elif not policy and has_blocked:
                return [t for t in data if t["relation"] != "blocked"]
            else:
                return data
    return build(state.directory(), state.document_list(), policy=policy)
