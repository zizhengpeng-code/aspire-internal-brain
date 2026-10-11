"""Retrieval-layer evaluation.

Reports are computed per case source: `user` (confirmed by the author, the only
numbers allowed in external material) and `all` (includes unconfirmed drafts,
internal use only). Every run is also written to results/<timestamp>.{json,md}.
"""
import argparse, json, os, sys, time
from collections import defaultdict
from kb import RoleAwareKB
from retrieval import build_retriever

CORPUS, EVAL, K = "data/corpus.json", "data/eval.json", 3
MODES = ["no_acl", "post_filter", "pre_filter"]
# the refusal gate is a raw BM25 score for bm25 and a cosine similarity for vector / hybrid
TAUS = {"bm25": [0, 2, 3, 4, 5, 6, 8], "vector": [0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]}
TAUS["hybrid"] = TAUS["vector"]
COMPARE_KS = [1, 3, 5]

def load_cases():
    cases = json.load(open(EVAL, encoding="utf-8"))
    for c in cases:
        c["gold"] = [c["gold"]] if isinstance(c["gold"], str) else c["gold"]
        c.setdefault("source", "draft")
        c.setdefault("tag", "plain")
    return cases

def check_labels(cases, kb):
    """An `answer` case must only need docs the role can see; a `deny` case must target a hidden doc."""
    bad = []
    for c in cases:
        allowed = kb.allowed(c["role"])
        if not set(c["gold"]) <= set(kb.docs):
            bad.append((c["id"], "unknown gold id"))
        elif c["expect"] == "answer" and not set(c["gold"]) <= allowed:
            bad.append((c["id"], "answer case needs a doc outside the role"))
        elif c["expect"] == "deny" and set(c["gold"]) & allowed:
            bad.append((c["id"], "deny case targets a doc the role can see"))
    if bad:
        raise SystemExit("label errors:\n" + "\n".join(f"  {i}: {m}" for i, m in bad))

def run(cases, mode, retriever, refuse_below=0.0, log=False, k=K):
    kb = RoleAwareKB(CORPUS, mode=mode, k=k, refuse_below=refuse_below, retriever=retriever,
                     audit_path=f"logs/audit_{mode}.jsonl" if log else None)
    auth = [c for c in cases if c["expect"] == "answer"]
    probes = [c for c in cases if c["expect"] == "deny"]
    recall, full_hit, leak_any, probe_hit, false_refuse, correct_refuse = 0.0, 0, 0, 0, 0, 0
    n_returned, n_empty = 0, 0
    by_tag, misses = defaultdict(list), []
    for c in cases:
        r = kb.retrieve(c["role"], c["question"])
        allowed = kb.allowed(c["role"])
        if any(d not in allowed for d in r["returned"]):
            leak_any += 1
        found = [g for g in c["gold"] if g in r["returned"]]
        if c["expect"] == "answer":
            frac = len(found) / len(c["gold"])
            recall += frac
            full_hit += frac == 1
            n_returned += len(r["returned"])
            n_empty += not r["returned"]
            by_tag[c["tag"]].append(frac)
            if frac < 1:
                misses.append({"id": c["id"], "role": c["role"], "question": c["question"],
                               "gold": c["gold"], "returned": r["returned"], "scores": r["scores"],
                               "blocked_by_acl": r["blocked_by_acl"]})
            false_refuse += r["refused"]
        else:
            probe_hit += bool(found)
            correct_refuse += r["refused"]
    pct = lambda a, b: a / b if b else float("nan")
    return {"mode": mode, "tau": refuse_below, "n_answer": len(auth), "n_deny": len(probes),
            f"recall@{k}": pct(recall, len(auth)),
            f"full_hit@{k}": pct(full_hit, len(auth)),
            "avg_returned": pct(n_returned, len(auth)),
            "empty_rate": pct(n_empty, len(auth)),
            "leak_rate_all": pct(leak_any, len(cases)),
            "probe_leak": pct(probe_hit, len(probes)),
            "false_refusal": pct(false_refuse, len(auth)),
            "correct_refusal": pct(correct_refuse, len(probes)),
            "recall_by_tag": {t: (sum(v) / len(v), len(v)) for t, v in sorted(by_tag.items())},
            "misses": misses}

def report(name, cases, log, retriever, compare=None):
    lines = [f"### subset `{name}`: {len(cases)} cases "
             f"({sum(c['expect'] == 'answer' for c in cases)} authorised, "
             f"{sum(c['expect'] == 'deny' for c in cases)} unauthorised probes)", ""]
    if not cases:
        return lines + ["(no cases yet)", ""], {}
    exp1 = {m: run(cases, m, retriever, log=log) for m in MODES}
    lines += [f"Experiment 1: access-control strategy ({retriever.name}, {retriever.unit} level, k={K})", "",
              f"| mode | recall@{K} | full_hit@{K} | avg docs returned | empty results | leak(all) | probe_leak |",
              "|---|---|---|---|---|---|---|"]
    for m, r in exp1.items():
        lines.append(f"| {m} | {r[f'recall@{K}']:.1%} | {r[f'full_hit@{K}']:.1%} | "
                     f"{r['avg_returned']:.2f} | {r['empty_rate']:.1%} | {r['leak_rate_all']:.1%} | {r['probe_leak']:.1%} |")
    tags = sorted(exp1["pre_filter"]["recall_by_tag"])
    lines += ["", f"recall@{K} by tag", "", "| tag | n | " + " | ".join(MODES) + " |",
              "|---|---|" + "---|" * len(MODES)]
    for t in tags:
        n = exp1["pre_filter"]["recall_by_tag"][t][1]
        lines.append(f"| {t} | {n} | " + " | ".join(
            f"{exp1[m]['recall_by_tag'][t][0]:.1%}" for m in MODES) + " |")
    exp2 = [run(cases, "pre_filter", retriever, refuse_below=t) for t in TAUS[retriever.name]]
    gate = "BM25 score" if retriever.name == "bm25" else "cosine similarity"
    lines += ["", f"Experiment 2: refusal threshold on pre_filter (best {gate} among returned < tau => refuse)", "",
              f"| tau | recall@{K} | false_refusal | correct_refusal |", "|---|---|---|---|"]
    for r in exp2:
        lines.append(f"| {r['tau']} | {r[f'recall@{K}']:.1%} | {r['false_refusal']:.1%} | {r['correct_refusal']:.1%} |")
    for m in ["pre_filter", "post_filter"]:
        lines += ["", f"{m} misses ({len(exp1[m]['misses'])})", ""]
        lines += [f"- {x['id']} [{x['role']}] {x['question']} | gold {x['gold']} | got {x['returned']}"
                  f" | blocked {x['blocked_by_acl']}" for x in exp1[m]["misses"]]
    out = {"exp1": exp1, "exp2": exp2}
    if compare:
        exp3 = {f"{r.name}/{r.unit}": {kk: run(cases, "pre_filter", r, k=kk) for kk in COMPARE_KS} for r in compare}
        lines += ["", "Experiment 3: retriever comparison (pre_filter, no refusal threshold; "
                  "k counts docs at doc level and chunks at chunk level)", "",
                  "| retriever / unit | " + " | ".join(f"recall@{kk}" for kk in COMPARE_KS) + " | "
                  + " | ".join(f"full_hit@{kk}" for kk in COMPARE_KS) + " |",
                  "|---|" + "---|" * 2 * len(COMPARE_KS)]
        for key, rs in exp3.items():
            lines.append(f"| {key} | " + " | ".join(f"{rs[kk]['recall@' + str(kk)]:.1%}" for kk in COMPARE_KS) + " | "
                         + " | ".join(f"{rs[kk]['full_hit@' + str(kk)]:.1%}" for kk in COMPARE_KS) + " |")
        out["exp3"] = {key: {kk: {m: v for m, v in r.items() if m != "misses"} for kk, r in rs.items()}
                       for key, rs in exp3.items()}
    return lines + [""], out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true", help="do not write results/")
    ap.add_argument("--retriever", choices=["bm25", "vector", "hybrid"], default="bm25")
    ap.add_argument("--unit", choices=["doc", "chunk"], default="chunk")
    ap.add_argument("--compare", action="store_true",
                    help="experiment 3: bm25 / vector / hybrid x doc / chunk (needs EMBED_MODEL)")
    args = ap.parse_args()
    if os.environ.get("EMBED_MODEL") == "fake" and not args.no_save:
        raise SystemExit("EMBED_MODEL=fake is for wiring tests only; rerun with --no-save")
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs("logs", exist_ok=True)
    for f in os.listdir("logs"): os.remove(os.path.join("logs", f))
    cases = load_cases()
    kb = RoleAwareKB(CORPUS)
    check_labels(cases, kb)
    docs = list(kb.docs.values())
    retriever = build_retriever(args.retriever, docs, args.unit)
    compare = None
    if args.compare:
        compare = [build_retriever(n, docs, u) for n in ["bm25", "vector", "hybrid"] for u in ["doc", "chunk"]]
    embed = os.environ.get("EMBED_MODEL") if (args.compare or args.retriever != "bm25") else None
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    header = [f"# Evaluation {stamp}", "",
              f"corpus: {len(kb.docs)} simulated docs (fictional company) | retriever: {args.retriever} | "
              f"unit: {args.unit} | k={K}" + (f" | embeddings: {embed}" if embed else ""),
              "`user` = cases confirmed by the author; `all` includes unconfirmed drafts (internal use only).", ""]
    user_lines, user_res = report("user", [c for c in cases if c["source"] == "user"], False, retriever, compare)
    all_lines, all_res = report("all", cases, True, retriever, compare)
    text = "\n".join(header + user_lines + all_lines)
    print(text)
    if not args.no_save:
        os.makedirs("results", exist_ok=True)
        with open(f"results/{stamp}.md", "w", encoding="utf-8") as f:
            f.write(text)
        with open(f"results/{stamp}.json", "w", encoding="utf-8") as f:
            json.dump({"stamp": stamp, "corpus_docs": len(kb.docs), "k": K, "retriever": args.retriever, "unit": args.unit, "embeddings": embed,
                       "n_cases": {"user": sum(c["source"] == "user" for c in cases), "all": len(cases)},
                       "user": user_res, "all": all_res}, f, ensure_ascii=False, indent=1)
        print(f"saved results/{stamp}.md and .json")
