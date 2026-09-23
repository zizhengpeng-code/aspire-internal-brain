# Aspire FinTech Track — Role-aware Enterprise Knowledge Base (baseline)

模拟公司：LumenPay（虚构的新加坡消费信贷 fintech）

## 结构
- `data/corpus.json` — 25 篇模拟企业文档，每篇带 department / sensitivity / allowed_roles
- `data/eval.json` — 55 条测试用例：37 条有权限问题（标注 gold 文档），18 条越权探测（含 1 条 prompt injection）
- `kb.py` — BM25 检索 + 三种权限策略（no_acl / post_filter / pre_filter）+ JSONL 审计日志
- `evaluate.py` — 两组实验：权限策略对比、拒答阈值扫描

## 运行
    python3 evaluate.py      # 无第三方依赖，Python 3.9+
审计日志写到 `logs/audit_<mode>.jsonl`。

## 角色权限
| 角色 | 可见范围 |
|---|---|
| employee | 全员文档（D01–D06, D23, D25） |
| risk_analyst | + 信贷政策、评分卡、催收、反欺诈、风险偏好、模型风险管理 |
| compliance_officer | + 信贷政策、风险偏好、AML/KYC、审计、数据泄露、监管变更、外包、模型风险管理 |
| hr_manager | + 薪酬带宽、绩效校准、员工关系案件 |
| executive | 全部 |

## 接入你自己的检索器
替换 `kb.py` 里 `BM25.scores(query, candidate_ids)`，返回 `[(doc_id, score), ...]` 即可。
pre_filter 的关键是把 `candidate_ids`（该角色可见文档）作为检索时的元数据过滤条件传进去，
在 Chroma 里就是 `where={"allowed_roles": ...}` 这一类过滤，而不是检索完再删。
