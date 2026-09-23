import json, os
from kb import RoleAwareKB

CORPUS, EVAL = "data/corpus.json", "data/eval.json"
cases = json.load(open(EVAL))
auth = [c for c in cases if c["expect"] == "answer"]
probes = [c for c in cases if c["expect"] == "deny"]
os.makedirs("logs", exist_ok=True)

def run(mode, refuse_below=0.0, log=False):
    kb = RoleAwareKB(CORPUS, mode=mode, k=3, refuse_below=refuse_below,
                     audit_path=f"logs/audit_{mode}.jsonl" if log else None)
    recall, leak_any, probe_hit, false_refuse, correct_refuse, misses = 0, 0, 0, 0, 0, []
    for c in cases:
        r = kb.retrieve(c["role"], c["question"])
        allowed = kb.allowed(c["role"])
        if any(d not in allowed for d in r["returned"]):
            leak_any += 1
        if c["expect"] == "answer":
            if c["gold"] in r["returned"]: recall += 1
            else: misses.append((c["id"], c["question"], r["returned"]))
            if r["refused"]: false_refuse += 1
        else:
            if c["gold"] in r["returned"]: probe_hit += 1
            if r["refused"]: correct_refuse += 1
    return {"mode": mode, "tau": refuse_below,
            "recall@3": recall / len(auth),
            "leak_rate_all": leak_any / len(cases),
            "probe_leak": probe_hit / len(probes),
            "false_refusal": false_refuse / len(auth),
            "correct_refusal": correct_refuse / len(probes), "misses": misses}

if __name__ == "__main__":
    for f in os.listdir("logs"): os.remove(os.path.join("logs", f))
    print(f"cases: {len(cases)} ({len(auth)} authorised, {len(probes)} unauthorised probes)\n")
    print("== Experiment 1: access-control strategy (k=3) ==")
    print(f"{'mode':<12}{'recall@3':>10}{'leak(all)':>11}{'probe_leak':>12}")
    results = {}
    for m in ["no_acl", "post_filter", "pre_filter"]:
        r = run(m, log=True); results[m] = r
        print(f"{m:<12}{r['recall@3']:>10.1%}{r['leak_rate_all']:>11.1%}{r['probe_leak']:>12.1%}")
    print("\n== Experiment 2: refusal threshold on pre_filter (top BM25 score < tau => refuse) ==")
    print(f"{'tau':>5}{'recall@3':>10}{'false_refusal':>15}{'correct_refusal':>17}")
    for tau in [0, 2, 3, 4, 5, 6, 8]:
        r = run("pre_filter", refuse_below=tau)
        print(f"{tau:>5}{r['recall@3']:>10.1%}{r['false_refusal']:>15.1%}{r['correct_refusal']:>17.1%}")
    print("\n== pre_filter misses (for badcase analysis) ==")
    for m in results["pre_filter"]["misses"]: print(" ", m)
    print("\n== no_acl misses ==")
    for m in results["no_acl"]["misses"]: print(" ", m)
