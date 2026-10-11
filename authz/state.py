"""Runtime state resolution.

The committed ``data/`` files are the source of truth; the demo mutates copies
under ``state/`` (git-ignored). Every read prefers ``state/<name>`` when it exists
and otherwise falls back to ``data/<name>``. Results are cached by file mtime so a
change (a revocation, a document update, a new tuple set) is picked up on the very
next call without re-indexing.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
DATA_DIR = os.path.join(ROOT, "data")

_cache = {}


def resolve(name):
    """Path of ``name`` currently in force: state/<name> if present, else data/<name>."""
    staged = os.path.join(STATE_DIR, name)
    return staged if os.path.exists(staged) else os.path.join(DATA_DIR, name)


def load(name):
    path = resolve(name)
    mtime = os.path.getmtime(path)
    hit = _cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _cache[name] = (mtime, data)
    return data


def reload():
    """Drop the mtime cache (tests, and after writing state files)."""
    _cache.clear()


def corpus_path():
    return resolve("corpus.json")


def directory_path():
    return resolve("directory.json")


def directory():
    return load("directory.json")


def document_list():
    return load("corpus.json")["documents"]


def documents():
    return {d["id"]: d for d in document_list()}
