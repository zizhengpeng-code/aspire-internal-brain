"""Materialise the relationship tuples from directory.json + corpus.json.

    python scripts/build_tuples.py            # write state/tuples.json
    python scripts/build_tuples.py --dry-run  # print a summary only

Run once after migrate_legacy.py / seed_stage_c.py. ``state/tuples.json`` is what
``LocalAuthz`` reads (when fresh) and what an OpenFGA store would be loaded with.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from authz import state, tuples  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build relationship tuples")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    directory = state.directory()
    documents = state.document_list()
    policy = tuples.policy_enabled()
    built = tuples.build(directory, documents, policy=policy)

    by_relation = {}
    for t in built:
        by_relation[t["relation"]] = by_relation.get(t["relation"], 0) + 1
    print(f"tuples: {len(built)}  (policy={'on' if policy else 'off'})")
    for relation in sorted(by_relation):
        print(f"  {relation:16s} {by_relation[relation]}")

    if args.dry_run:
        return
    os.makedirs(state.STATE_DIR, exist_ok=True)
    with open(tuples.tuples_path(), "w", encoding="utf-8") as f:
        json.dump(built, f, ensure_ascii=False, indent=1)
    state.reload()
    print(f"wrote {tuples.tuples_path()}")


if __name__ == "__main__":
    main()
