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
import acl
from audit import AuditLog

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state")
UPDATES = os.path.join(ROOT, "data", "updates")
AUDIT_PATH = os.path.join(ROOT, "logs", "audit.jsonl")


def _write_state_corpus(docs):
    with open(acl.corpus_path(), encoding="utf-8") as f:
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
    with open(acl.corpus_path(), encoding="utf-8") as f:
        docs = json.load(f)["documents"]
    docs = [new_doc if d["id"] == doc_id else d for d in docs]
    _write_state_corpus(docs)
    acl.reload()

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
    with open(acl.directory_path(), encoding="utf-8") as f:
        directory = json.load(f)
    members = directory["slack"]["channels"][channel]["members"]
    subject = f"user:{user_id}"
    if subject in members:
        members.remove(subject)
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "directory.json"), "w", encoding="utf-8") as f:
        json.dump(directory, f, ensure_ascii=False, indent=1)
    acl.reload()
    return {"user": user_id, "channel": channel, "members": members}


def tamper(seq, audit_path=AUDIT_PATH):
    """Alter one audit record in place, so verify() reports it."""
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
    shutil.rmtree(STATE, ignore_errors=True)
    acl.reload()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aspire demo state changes")
    sub = parser.add_subparsers(dest="command", required=True)
    p_update = sub.add_parser("update", help="apply a document update")
    p_update.add_argument("doc_id")
    p_revoke = sub.add_parser("revoke", help="remove a user from a Slack channel")
    p_revoke.add_argument("user")
    p_revoke.add_argument("channel")
    p_tamper = sub.add_parser("tamper", help="alter one audit record")
    p_tamper.add_argument("seq", type=int)
    sub.add_parser("seed", help="run a batch of queries to build audit history")
    sub.add_parser("reset", help="delete state/")
    args = parser.parse_args(argv)

    if args.command == "update":
        print(json.dumps(update(args.doc_id), ensure_ascii=False))
    elif args.command == "revoke":
        print(json.dumps(revoke(args.user, args.channel), ensure_ascii=False))
    elif args.command == "tamper":
        print(f"verify() -> {tamper(args.seq)}")
    elif args.command == "seed":
        for row in seed():
            print(json.dumps(row, ensure_ascii=False))
    elif args.command == "reset":
        reset()
        print("state/ removed")


if __name__ == "__main__":
    main()
