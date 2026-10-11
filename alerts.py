"""Alert rules evaluated over the audit log.

Current rule: the same user accumulating >= 3 *probing* denials on *restricted*
documents within a 10-minute window raises a single alert event (written back
into the audit log so the compliance officer sees it alongside everything else).

Not every denial is probing. A document asked for by id (``open_doc`` /
``follow_links``) counts. A shadow-search denial counts only when the hidden document
was the single best match for the query (``rank == 1``): lower-ranked shadow hits come
from word overlap ("project" in a migration question matching an executive project
note), not from intent. A withheld link (``via == "link"``) was never asked for, so it
never counts. Records written before ``via`` existed count, as they always did.
"""
from datetime import datetime, timedelta

from audit import now_iso

RULE = "restricted_deny_burst"


def _parse(ts):
    return datetime.fromisoformat(ts)


def _is_probe(decision):
    via = decision.get("via")
    if via is None:
        return True
    if via in ("open_doc", "follow_links"):
        return True
    if via == "shadow_search":
        return decision.get("rank") == 1
    return False


def check(audit, user_id, now_ts=None, window_minutes=10, threshold=3, restricted_only=True):
    """Append an alert if `user_id` has >= threshold probing restricted denies in the window.

    Returns True when an alert was appended (and False when nothing fired or an
    alert for this window already exists, so repeated calls stay idempotent).
    """
    records = audit.records()
    if now_ts is None:
        now_ts = records[-1]["ts"] if records else now_iso()
    now = _parse(now_ts)
    window_start = now - timedelta(minutes=window_minutes)

    denies = []
    for rec in records:
        if rec.get("user_id") != user_id:
            continue
        ts = _parse(rec["ts"])
        if ts < window_start or ts > now:
            continue
        for d in rec.get("decisions") or []:
            if d.get("allow"):
                continue
            if restricted_only and d.get("sensitivity") != "restricted":
                continue
            if not _is_probe(d):
                continue
            denies.append(d.get("doc_id"))

    if len(denies) < threshold:
        return False
    for rec in records:
        if rec.get("event") == "alert" and rec.get("user_id") == user_id and _parse(rec["ts"]) >= window_start:
            return False  # already alerted in this window

    groups = next((r.get("groups") for r in reversed(records) if r.get("user_id") == user_id), None)
    audit.append({
        "event": "alert", "ts": now.isoformat(timespec="seconds"), "user_id": user_id, "groups": groups,
        "rule": RULE, "count": len(denies), "window_minutes": window_minutes, "doc_ids": denies,
    })
    return True
