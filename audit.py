"""Tamper-evident audit log with signed checkpoints.

An append-only JSONL file where every record carries ``prev_hash`` and ``hash``:

    hash = sha256(prev_hash + canonical_json(record without "hash"))

so changing any record breaks the chain from that point on. The log records, per
request: who (user_id, groups), what (question, tool calls, returned doc ids, and
a per-document allow/deny decision list), when (ts), and the final answer /
citations / refusal, plus model usage.

A hash chain alone cannot detect a *truncation* (the tail being deleted). So after
each answer a signed checkpoint is appended to a second file::

    {"seq": 42, "head_hash": "...", "ts": "...", "sig": HMAC-SHA256(key, "42|...")}

The key comes from ``AUDIT_HMAC_KEY``; when unset a random key is generated and
stored in ``.cache/audit.key`` (git-ignored, deliberately *not* under ``state/``
so ``simulate.py reset`` cannot delete it and invalidate old checkpoints).

    log = AuditLog("logs/audit.jsonl")
    log.append({"event": "tool_call", ...})
    log.checkpoint()            # after each answer round
    log.verify()                # None if intact, else the seq of the first problem
    log.verify_detail()         # {"ok", "kind": chain|checkpoint|truncation, "seq"}
    log.query(user="jdoe", location_prefix="confluence:payment-gateway", since="...")
"""
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(ROOT, ".cache", "audit.key")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(prev_hash, record):
    return hashlib.sha256((prev_hash + _canonical(record)).encode("utf-8")).hexdigest()


def _sign(key, seq, head_hash):
    return hmac.new(key, f"{seq}|{head_hash}".encode("utf-8"), hashlib.sha256).hexdigest()


def _load_key():
    """AUDIT_HMAC_KEY, else a generated key persisted in .cache/audit.key."""
    env = os.environ.get("AUDIT_HMAC_KEY")
    if env:
        return env.encode("utf-8")
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    key = secrets.token_bytes(32)
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    print(f"[audit] AUDIT_HMAC_KEY not set; generated a key at {KEY_PATH}")
    return key


class AuditLog:
    def __init__(self, path, checkpoint_path=None, key=None):
        self.path = path
        self.checkpoint_path = checkpoint_path or os.path.join(
            os.path.dirname(os.path.abspath(path)), "checkpoints.jsonl")
        self._key = key if isinstance(key, bytes) else (key.encode("utf-8") if key else None)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def key(self):
        if self._key is None:
            self._key = _load_key()
        return self._key

    # ------------------------------------------------------------------ read
    def records(self):
        return self._read(self.path)

    def checkpoints(self):
        return self._read(self.checkpoint_path)

    @staticmethod
    def _read(path):
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
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

    def checkpoint(self):
        """Sign the current head of the chain into the checkpoint file."""
        tail = self._tail()
        if tail is None:
            return None
        record = {"seq": tail["seq"], "head_hash": tail["hash"], "ts": now_iso()}
        record["sig"] = _sign(self.key(), record["seq"], record["head_hash"])
        with open(self.checkpoint_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def truncate(self, count):
        """Drop the last ``count`` records (caller backs the file up first)."""
        records = self.records()
        kept = records[: len(records) - count] if count > 0 else records
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
        return {"removed": len(records) - len(kept), "remaining": len(kept)}

    # ---------------------------------------------------------------- verify
    def verify_detail(self):
        """First problem with the log, as {"ok", "kind", "seq"}; kind is None when intact."""
        records = self.records()
        prev_hash = ""
        for i, rec in enumerate(records, start=1):
            if rec.get("seq") != i:
                return {"ok": False, "kind": "chain", "seq": rec.get("seq", i)}
            body = {k: v for k, v in rec.items() if k != "hash"}
            if body.get("prev_hash") != prev_hash:
                return {"ok": False, "kind": "chain", "seq": rec["seq"]}
            if _digest(prev_hash, body) != rec.get("hash"):
                return {"ok": False, "kind": "chain", "seq": rec["seq"]}
            prev_hash = rec["hash"]

        by_seq = {r["seq"]: r for r in records}
        checkpoints = self.checkpoints()
        if checkpoints and checkpoints[-1]["seq"] > len(records):
            return {"ok": False, "kind": "truncation", "seq": len(records) + 1}
        for cp in checkpoints:
            if not hmac.compare_digest(_sign(self.key(), cp["seq"], cp["head_hash"]), cp.get("sig", "")):
                return {"ok": False, "kind": "checkpoint", "seq": cp["seq"]}
            rec = by_seq.get(cp["seq"])
            if rec is None or rec.get("hash") != cp["head_hash"]:
                return {"ok": False, "kind": "checkpoint", "seq": cp["seq"]}

        return {"ok": True, "kind": None, "seq": None}

    def verify(self):
        """None when intact, else the seq of the first problem (backwards compatible)."""
        detail = self.verify_detail()
        return None if detail["ok"] else detail["seq"]

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
