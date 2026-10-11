"""Demo state changes.

Everything the demo mutates is written to ``state/`` (git-ignored) so the committed
``data/`` files stay pristine. ``reset`` deletes ``state/``.

    python simulate.py update E06                       # push the 13:00 runbook revision
    python simulate.py revoke jdoe "#payments-incident" # remove a user from a Slack channel
    python simulate.py tamper 4                         # alter one audit record
    python simulate.py seed                             # run a batch of real queries (builds audit history)
    python simulate.py reset                            # drop all demo state
"""
import argparse
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import authz
import ai_scope
import env
from audit import AuditLog

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state")
UPDATES = os.path.join(ROOT, "data", "updates")
AUDIT_PATH = os.path.join(ROOT, "logs", "audit.jsonl")
BACKUP = os.path.join(STATE, "audit_backup.jsonl")


def _backup_audit(audit_path=AUDIT_PATH):
    """Copy the audit log into state/ before tampering with it."""
    os.makedirs(STATE, exist_ok=True)
    if os.path.exists(audit_path):
        shutil.copy2(audit_path, BACKUP)
    return BACKUP


def _write_state_corpus(docs):
    with open(authz.corpus_path(), encoding="utf-8") as f:
        corpus = json.load(f)
    corpus["documents"] = docs
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=1)


def update(doc_id, embeddings=None, persist_dir="index/chroma"):
    """Apply data/updates/<doc_id>_*.json to state/, then incrementally sync the index."""
    candidates = sorted(glob.glob(os.path.join(UPDATES, f"{doc_id}_*.json")))
    if not candidates:
        raise FileNotFoundError(f"no update file for {doc_id} in data/updates/")
    with open(candidates[-1], encoding="utf-8") as f:
        new_doc = json.load(f)
    with open(authz.corpus_path(), encoding="utf-8") as f:
        docs = json.load(f)["documents"]
    docs = [new_doc if d["id"] == doc_id else d for d in docs]
    _write_state_corpus(docs)
    authz.reload()

    stats = {"doc": doc_id, "updated_at": new_doc["updated_at"]}
    try:
        from retrieval import VectorIndex
        index = VectorIndex(None, "chunk", embeddings=embeddings, persist_dir=persist_dir)
        stats.update(index.sync(docs))
    except Exception as exc:  # no embeddings configured -> still a valid demo of the file change
        stats["sync"] = f"skipped ({type(exc).__name__}: {exc})"
    return stats


def revoke(user_id, channel):
    """Remove a user from a Slack channel in state/directory.json (effective immediately)."""
    with open(authz.directory_path(), encoding="utf-8") as f:
        directory = json.load(f)
    members = directory["slack"]["channels"][channel]["members"]
    subject = f"user:{user_id}"
    if subject in members:
        members.remove(subject)
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "directory.json"), "w", encoding="utf-8") as f:
        json.dump(directory, f, ensure_ascii=False, indent=1)
    authz.reload()
    return {"user": user_id, "channel": channel, "members": members}


def tamper(seq, audit_path=AUDIT_PATH):
    """Alter one audit record in place, so verify() reports it."""
    _backup_audit(audit_path)
    log = AuditLog(audit_path)
    lines = []
    for record in log.records():
        if record["seq"] == seq:
            record = dict(record)
            record["answer"] = (record.get("answer") or "") + " [tampered]"
        lines.append(json.dumps(record, ensure_ascii=False))
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return log.verify()


def truncate(count, audit_path=AUDIT_PATH):
    """Drop the last `count` audit records (backed up first) to demo truncation."""
    backup = _backup_audit(audit_path)
    result = AuditLog(audit_path).truncate(count)
    return {"backup": backup, **result}


def exclude(target, audit_path=AUDIT_PATH):
    """Add a doc id or a location prefix to the AI exclusion list (state/ai_scope.json)."""
    scope = ai_scope.current()
    key = "exclude_locations" if ":" in target else "exclude_docs"
    if target not in scope[key]:
        scope[key].append(target)
    ai_scope.write_scope(scope)
    AuditLog(audit_path).append({"event": "scope_change", "user_id": "admin",
                                 "change": "exclude", "target": target, "scope": scope})
    return scope


SEED_QUERIES = [
    ("jdoe", "What's the status of the database migration project?"),
    ("jdoe", "What is the settlement reconciliation cutoff?"),
    ("wlin", "What's the latest runbook for the payment-service incident?"),
    ("sho", "What is the Q3 incident number?"),
    ("glim", "What's the replication lag on the ledger backfill?"),
    ("atan", "Show me the security incident report from the Q3 breach."),
    ("atan", "What are the items in the security risk register?"),
    ("atan", "Is there a risk acceptance for legacy TLS on the merchant portal?"),
]


def seed(llm=None, retriever="bm25", audit_path=AUDIT_PATH):
    """Generate real audit history by running queries as several users (no hand-written log lines)."""
    from agent import Session, answer
    from audit import AuditLog as _AuditLog
    audit = _AuditLog(audit_path)
    results = []
    for user_id, question in SEED_QUERIES:
        session = Session(user_id, llm=llm, audit=audit, retriever=retriever)
        outcome = answer(session, question)
        results.append({"user": user_id, "question": question, "refused": outcome["refused"],
                        "citations": outcome["citations"]})
    return results


def reset():
    """Restore the audit log from the backup (if any), then delete state/."""
    if os.path.exists(BACKUP):
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        shutil.copy2(BACKUP, AUDIT_PATH)
    shutil.rmtree(STATE, ignore_errors=True)
    authz.reload()


def main(argv=None):
    env.load()                       # DEMO_NOW / AUTHZ_BACKEND may come from .env
    parser = argparse.ArgumentParser(description="Aspire demo state changes")
    sub = parser.add_subparsers(dest="command", required=True)
    p_update = sub.add_parser("update", help="apply a document update")
    p_update.add_argument("doc_id")
    p_revoke = sub.add_parser("revoke", help="remove a user from a Slack channel")
    p_revoke.add_argument("user")
    p_revoke.add_argument("channel")
    p_tamper = sub.add_parser("tamper", help="alter one audit record")
    p_tamper.add_argument("seq", type=int)
    p_truncate = sub.add_parser("truncate", help="drop the last N audit records")
    p_truncate.add_argument("count", type=int)
    p_exclude = sub.add_parser("exclude", help="add a doc id or location to the AI exclusion list")
    p_exclude.add_argument("target")
    sub.add_parser("seed", help="run a batch of queries to build audit history")
    sub.add_parser("reset", help="restore the audit log from the backup and delete state/")
    args = parser.parse_args(argv)

    if args.command == "update":
        print(json.dumps(update(args.doc_id), ensure_ascii=False))
    elif args.command == "revoke":
        print(json.dumps(revoke(args.user, args.channel), ensure_ascii=False))
    elif args.command == "tamper":
        print(f"verify() -> {tamper(args.seq)}")
    elif args.command == "truncate":
        print(json.dumps(truncate(args.count), ensure_ascii=False))
    elif args.command == "exclude":
        print(json.dumps(exclude(args.target), ensure_ascii=False))
    elif args.command == "seed":
        for row in seed():
            print(json.dumps(row, ensure_ascii=False))
    elif args.command == "reset":
        reset()
        print("state/ removed")


if __name__ == "__main__":
    main()
