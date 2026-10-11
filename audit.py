"""Tamper-evident audit log.

An append-only JSONL file where every record carries ``prev_hash`` and ``hash``:

    hash = sha256(prev_hash + canonical_json(record without "hash"))

so changing any record breaks the chain from that point on. The log records, per
request: who (user_id, groups), what (question, tool calls, returned doc ids, and
a per-document allow/deny decision list), when (ts), and the final answer /
citations / refusal, plus model usage.

    log = AuditLog("logs/audit.jsonl")
    log.append({"event": "tool_call", "user_id": "jdoe", ...})
    log.verify()   # None if intact, else the seq of the first bad record
    log.query(user="jdoe", location_prefix="confluence:payment-gateway", since="...")
"""
import hashlib
import json
import os
from datetime import datetime


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(prev_hash, record):
    return hashlib.sha256((prev_hash + _canonical(record)).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # ------------------------------------------------------------------ read
    def records(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _tail(self):
        recs = self.records()
        return recs[-1] if recs else None

    # ----------------------------------------------------------------- write
    def append(self, event):
        """Append one event, filling in seq / ts / prev_hash / hash. Returns the record."""
        tail = self._tail()
        seq = tail["seq"] + 1 if tail else 1
        prev_hash = tail["hash"] if tail else ""
        record = {"seq": seq, "ts": event.get("ts") or now_iso(), "prev_hash": prev_hash}
        record.update({k: v for k, v in event.items() if k not in ("seq", "ts", "prev_hash", "hash")})
        record["hash"] = _digest(prev_hash, record)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    # ---------------------------------------------------------------- verify
    def verify(self):
        """Return None when the chain is intact, else the seq of the first bad record."""
        prev_hash = ""
        for i, rec in enumerate(self.records(), start=1):
            if rec.get("seq") != i:
                return rec.get("seq", i)
            body = {k: v for k, v in rec.items() if k != "hash"}
            if body.get("prev_hash") != prev_hash:
                return rec["seq"]
            if _digest(prev_hash, body) != rec.get("hash"):
                return rec["seq"]
            prev_hash = rec["hash"]
        return None

    # ----------------------------------------------------------------- query
    def query(self, user=None, doc=None, location_prefix=None, since=None, until=None, decision=None):
        """Filter records. `decision` is "allow" or "deny"; doc / location_prefix /
        decision are matched against the per-document decision list of a record."""
        want_allow = None if decision is None else decision == "allow"
        decision_filters = doc is not None or location_prefix is not None or decision is not None
        out = []
        for rec in self.records():
            if user is not None and rec.get("user_id") != user:
                continue
            ts = rec.get("ts", "")
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            if decision_filters:
                hit = any(
                    (doc is None or d.get("doc_id") == doc)
                    and (location_prefix is None or str(d.get("location", "")).startswith(location_prefix))
                    and (decision is None or d.get("allow") == want_allow)
                    for d in rec.get("decisions") or []
                )
                if not hit and doc is not None and doc in (rec.get("returned") or []):
                    hit = True
                if not hit:
                    continue
            out.append(rec)
        return out
