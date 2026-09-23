"""Retrieval-layer evaluation.

Reports are computed per case source: `user` (confirmed by the author, the only
numbers allowed in external material) and `all` (includes unconfirmed drafts,
internal use only). Every run is also written to results/<timestamp>.{json,md}.
"""
import argparse, json, os, sys, time
from collections import defaultdict
from kb import RoleAwareKB

CORPUS, EVAL, K = "data/corpus.json", "data/eval.json", 3
MODES = ["no_acl", "post_filter", "pre_filter"]
TAUS = [0, 2, 3, 4, 5, 6, 8]

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

def run(cases, mode, refuse_below=0.0, log=False):
    kb = RoleAwareKB(CORPUS, mode=mode, k=K, refuse_below=refuse_below,
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
            f"recall@{K}": pct(recall, len(auth)),
            f"full_hit@{K}": pct(full_hit, len(auth)),
            "avg_returned": pct(n_returned, len(auth)),
            "empty_rate": pct(n_empty, len(auth)),
            "leak_rate_all": pct(leak_any, len(cases)),
            "probe_leak": pct(probe_hit, len(probes)),
            "false_refusal": pct(false_refuse, len(auth)),
            "correct_refusal": pct(correct_refuse, len(probes)),
            "recall_by_tag": {t: (sum(v) / len(v), len(v)) for t, v in sorted(by_tag.items())},
            "misses": misses}

def report(name, cases, log):
    lines = [f"### subset `{name}`: {len(cases)} cases "
             f"({sum(c['expect'] == 'answer' for c in cases)} authorised, "
             f"{sum(c['expect'] == 'deny' for c in cases)} unauthorised probes)", ""]
    if not cases:
        return lines + ["(no cases yet)", ""], {}
    exp1 = {m: run(cases, m, log=log) for m in MODES}
    lines += [f"Experiment 1: access-control strategy (k={K})", "",
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
    exp2 = [run(cases, "pre_filter", refuse_below=t) for t in TAUS]
    lines += ["", "Experiment 2: refusal threshold on pre_filter (top BM25 score < tau => refuse)", "",
              f"| tau | recall@{K} | false_refusal | correct_refusal |", "|---|---|---|---|"]
    for r in exp2:
        lines.append(f"| {r['tau']} | {r[f'recall@{K}']:.1%} | {r['false_refusal']:.1%} | {r['correct_refusal']:.1%} |")
    for m in ["pre_filter", "post_filter"]:
        lines += ["", f"{m} misses ({len(exp1[m]['misses'])})", ""]
        lines += [f"- {x['id']} [{x['role']}] {x['question']} | gold {x['gold']} | got {x['returned']}"
                  f" | blocked {x['blocked_by_acl']}" for x in exp1[m]["misses"]]
    return lines + [""], {"exp1": exp1, "exp2": exp2}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true", help="do not write results/")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs("logs", exist_ok=True)
    for f in os.listdir("logs"): os.remove(os.path.join("logs", f))
    cases = load_cases()
    kb = RoleAwareKB(CORPUS)
    check_labels(cases, kb)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    header = [f"# Evaluation {stamp}", "",
              f"corpus: {len(kb.docs)} simulated docs (fictional company) | retriever: BM25 | k={K}",
              "`user` = cases confirmed by the author; `all` includes unconfirmed drafts (internal use only).", ""]
    user_lines, user_res = report("user", [c for c in cases if c["source"] == "user"], log=False)
    all_lines, all_res = report("all", cases, log=True)
    text = "\n".join(header + user_lines + all_lines)
    print(text)
    if not args.no_save:
        os.makedirs("results", exist_ok=True)
        with open(f"results/{stamp}.md", "w", encoding="utf-8") as f:
            f.write(text)
        with open(f"results/{stamp}.json", "w", encoding="utf-8") as f:
            json.dump({"stamp": stamp, "corpus_docs": len(kb.docs), "k": K, "retriever": "bm25",
                       "n_cases": {"user": sum(c["source"] == "user" for c in cases), "all": len(cases)},
                       "user": user_res, "all": all_res}, f, ensure_ascii=False, indent=1)
        print(f"saved results/{stamp}.md and .json")
