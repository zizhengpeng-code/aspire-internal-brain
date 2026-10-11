"""Over-sharing scan.

Most enterprise AI leaks are not the AI bypassing permissions — the source system
was already shared too widely, and the AI just makes it findable. So this scan looks
at the *platform* readers (before the organisation policy layer), which is exactly
where the source-system problem lives, and reports it for a human to fix.

Rules
-----
R1  a confidential/restricted document readable by a contractor
R2  a restricted document readable by more than half of all users
"""
from dataclasses import dataclass

import authz


@dataclass
class Finding:
    doc_id: str
    location: str
    sensitivity: str
    rule: str
    readers_count: int
    external_readers: list
    suggestion: str


def scan(docs=None, users=None):
    docs = authz.documents() if docs is None else docs
    users = authz.directory().get("users", {}) if users is None else users
    total = len(users) or 1
    findings = []
    for doc_id, doc in docs.items():
        sensitivity = doc.get("sensitivity")
        if sensitivity not in ("confidential", "restricted"):
            continue
        readers = authz.platform_readers(doc_id)
        external = sorted(u for u in readers if users.get(u, {}).get("employment") == "contractor")
        if external:
            findings.append(Finding(
                doc_id, authz.location(doc), sensitivity, "R1", len(readers), external,
                "Tighten sharing in the source system; until then add it to the AI exclusion list."))
        if sensitivity == "restricted" and len(readers) > total * 0.5:
            findings.append(Finding(
                doc_id, authz.location(doc), sensitivity, "R2", len(readers), [],
                "Restricted document visible to most users; tighten the source system."))
    return findings
