"""Incremental sync of data/corpus.json into the Chroma index.

    python sync.py              # sync both doc- and chunk-level collections, print what changed
    python sync.py --unit chunk

Only documents whose title/text changed are re-chunked and re-embedded (embeddings are
also cached by content), so a connector polling every N minutes keeps new content
searchable within N minutes + the sync time printed here.
"""
import argparse, json, sys
from retrieval import VectorIndex

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="data/corpus.json")
    ap.add_argument("--unit", choices=["doc", "chunk", "both"], default="both")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    docs = json.load(open(args.corpus, encoding="utf-8"))["documents"]
    for unit in (["doc", "chunk"] if args.unit == "both" else [args.unit]):
        idx = VectorIndex(None, unit)  # open the collection without syncing
        stats = idx.sync(docs)
        print(f"{idx.collection}: {stats} | units in index: {idx.size} | texts embedded: {idx.emb.texts_embedded}")
