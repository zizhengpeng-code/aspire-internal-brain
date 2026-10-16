"""Repro for one failing check: wlin asks for the payment-service runbook, before the update.

    python results/repro-runbook.py [n]

Scenario ② opens with exactly this question (it is also the first suggestion button for
wlin), and the five-scenario check got a degenerate answer once: a single closing line
about the linked Jira item, with no runbook content. This runs it ``n`` times (default 5)
against the real model, cache off, and reports how often the answer is complete.

Same hygiene as results/check-five-scenarios.py: own audit log, state/ removed, and
logs/audit.jsonl + logs/checkpoints.jsonl restored byte-for-byte on exit.
"""
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import env  # noqa: E402

env.load()

from agent import Session, answer  # noqa: E402
from audit import AuditLog  # noqa: E402
from llm import LLM  # noqa: E402

AUDIT = os.path.join(".cache", "scenario-check-repro", "audit.jsonl")
KEEP = [os.path.join("logs", "audit.jsonl"), os.path.join("logs", "checkpoints.jsonl")]
QUESTION = "What's the latest runbook for the payment-service incident?"


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    snapshot = {p: (open(p, "rb").read() if os.path.exists(p) else None) for p in KEEP}
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    rows = []
    llm = LLM(cache_path=None)
    try:
        shutil.rmtree("state", ignore_errors=True)
        shutil.rmtree(os.path.dirname(AUDIT), ignore_errors=True)
        audit = AuditLog(AUDIT)
        for i in range(1, runs + 1):
            session = Session("wlin", llm=llm, audit=audit, retriever="bm25")
            outcome = answer(session, QUESTION)
            text = (outcome["answer"] or "")
            raw = (outcome.get("raw_answer") or "")
            row = {"run": i, "refused": outcome["refused"], "citations": outcome["citations"],
                   "steps": outcome.get("steps") or [], "chars": len(text),
                   "has_rev7": "PGW-RB-7" in text or "rev 7" in text.lower(),
                   "complete": ("PGW-RB-7" in text or "rev 7" in text.lower())
                               and len(text) > 300,
                   "same_as_raw": text.strip() == raw.strip(), "guard": outcome.get("guard"),
                   "search_k": [s.get("args", {}).get("k") for s in (outcome.get("steps") or [])
                                if s.get("tool") == "search"],
                   "rounds": len(outcome.get("steps") or []),
                   "answer": text, "raw": raw}
            rows.append(row)
            print(f"  run {i}: refused={row['refused']} cites={row['citations']} "
                  f"chars={row['chars']} rev7={row['has_rev7']} complete={row['complete']} "
                  f"rounds={row['rounds']} k={row['search_k']}", flush=True)
    finally:
        for path, blob in snapshot.items():
            if blob is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(blob)
        shutil.rmtree("state", ignore_errors=True)

    ok = sum(1 for r in rows if r["complete"])
    out = os.path.join("results", f"runbook-repro-{stamp}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 复现：wlin 问「最新 payment-service runbook」（更新前）· 模型 {llm.model}\n\n"
                f"完整回答 **{ok}/{len(rows)}**（完整 = 含 PGW-RB-7/rev 7 且正文 >300 字符）\n\n"
                f"| # | 拒答 | 引用 | 工具调用 | search k | 字符数 | 含 rev 7 | 完整 |\n|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['run']} | {'yes' if r['refused'] else 'no'} | "
                    f"{', '.join(r['citations']) or '—'} | {len(r['steps'])} | "
                    f"{r['search_k']} | {r['chars']} | "
                    f"{'yes' if r['has_rev7'] else 'no'} | {'✅' if r['complete'] else '❌'} |\n")
        for r in rows:
            f.write(f"\n## run {r['run']} — {'✅ 完整' if r['complete'] else '❌ 退化'}\n\n"
                    f"工具调用：\n\n```json\n"
                    + json.dumps(r["steps"], ensure_ascii=False, indent=1) + "\n```\n\n"
                    f"> " + r["answer"].replace("\n", "\n> ") + "\n")
        f.write("\n```json\n" + json.dumps(rows, ensure_ascii=False, indent=1) + "\n```\n")
    print(f"\ncomplete {ok}/{len(rows)} — wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
