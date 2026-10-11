"""One-off migration of the legacy corpus (D01-D70) to the platform-native schema.

Run once from the repo root:

    python scripts/migrate_legacy.py            # rewrite data/corpus.json in place
    python scripts/migrate_legacy.py --dry-run  # show what would change

What it does, for every legacy document (identified by the absence of a "source"
field):

* add ``source: "confluence"`` and a Confluence ``acl``:
    - ``space`` derived from ``department`` (see DEPT_SPACE);
    - ``restrictions`` empty when ``allowed_roles`` is all five roles, otherwise
      ``restrictions.groups = allowed_roles`` (so the page is limited to those roles);
* add ``updated_at`` (the corpus snapshot date), ``url`` and ``links: []``;
* keep ``allowed_roles`` untouched, purely for cross-checking.

The five legacy roles are represented by five persona users
(``persona_<role>``) defined in ``data/directory.json``; every legacy space is
viewable by ``group:staff``. The migration is idempotent.

Acceptance: for each role, ``authz.list_readable("persona_" + role)`` must equal the
set of documents whose ``allowed_roles`` contains that role (see
tests/test_stage_c_acl.py).
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "data", "corpus.json")

LEGACY_ROLES = ["employee", "risk_analyst", "compliance_officer", "hr_manager", "executive"]

DEPT_SPACE = {
    "HR": "hr",
    "IT": "it",
    "Finance": "finance",
    "Product": "product",
    "Risk": "risk",
    "Compliance": "compliance",
    "Executive": "exec",
    "Operations": "ops",
}

SNAPSHOT_TS = "2026-09-23T09:00:00+08:00"


def migrate_doc(doc):
    """Return a migrated copy of a legacy doc (or the doc unchanged if already migrated)."""
    if doc.get("source"):
        return doc
    space = DEPT_SPACE[doc["department"]]
    roles = list(doc["allowed_roles"])
    restrictions = {} if set(roles) == set(LEGACY_ROLES) else {"groups": roles}
    out = dict(doc)
    out["source"] = "confluence"
    out["acl"] = {"space": space, "restrictions": restrictions}
    out["updated_at"] = SNAPSHOT_TS
    out["url"] = f"https://confluence.lumenpay.example/{space}/{doc['id']}"
    out["links"] = []
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(CORPUS, encoding="utf-8") as f:
        corpus = json.load(f)

    migrated = 0
    docs = []
    for doc in corpus["documents"]:
        if not doc.get("source"):
            migrated += 1
        docs.append(migrate_doc(doc))
    corpus["documents"] = docs

    if args.dry_run:
        print(f"[dry-run] would migrate {migrated} legacy documents (of {len(docs)}).")
        return
    with open(CORPUS, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=1)
    print(f"migrated {migrated} legacy documents (of {len(docs)}) -> {os.path.relpath(CORPUS, ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
