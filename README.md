# Aspire FinTech Track — Role-aware Enterprise Knowledge Base

模拟公司：LumenPay（虚构的新加坡消费信贷 fintech）。语料里的公司、人物、编号和数字全部虚构。

## 结构
- `data/corpus.json`：70 篇模拟企业文档，每篇带 department / sensitivity / allowed_roles
- `data/eval.json`：测试用例，字段见下
- `kb.py`：BM25 检索、三种权限策略（no_acl / post_filter / pre_filter）、JSONL 审计日志
- `evaluate.py`：实验 1 权限策略对比，实验 2 拒答阈值扫描；每次运行落盘到 `results/<时间戳>.md|json`

## 运行
    python evaluate.py            # 无第三方依赖，Python 3.9+
    python evaluate.py --no-save  # 只打印，不写 results/
审计日志写到 `logs/audit_<mode>.jsonl`。

## 测试用例字段
| 字段 | 说明 |
|---|---|
| `role` / `question` | 提问角色与问题 |
| `gold` | 回答所需文档 ID；跨文档问题为列表，需全部召回才算 full_hit |
| `expect` | `answer`（该角色有权限）或 `deny`（越权探测，gold 为该角色不可见的文档） |
| `tag` | `plain` / `colloquial` / `zh_mix` / `cross_doc` / `injection` |
| `source` | `user` = 作者本人确认过；`draft` = 候选草稿 |

报告分两段输出：`user` 子集是唯一可以对外使用的数字；`all` 含草稿，仅供内部迭代。
确认一条用例的方式：核对问题措辞和 `gold`，改好后把 `source` 改成 `"user"`。
`evaluate.py` 启动时会校验标注：`answer` 的 gold 必须对该角色可见，`deny` 的 gold 必须不可见，否则直接报错。

## 角色权限
| 角色 | 可见范围 |
|---|---|
| employee | `internal` 全员文档 |
| risk_analyst | + 信贷政策、评分卡及监控、定价、额度、例外、催收、反欺诈、风险偏好、模型风险 |
| compliance_officer | + 信贷政策、定价、风险偏好、AML/KYC、制裁、审计、数据泄露、监管变更、外包、举报、模型风险 |
| hr_manager | + 薪酬、奖金、调薪、绩效校准与分布、PIP、员工关系案件 |
| executive | 全部 |

逐篇权限以 `corpus.json` 的 `allowed_roles` 为准。

## 语料设计
新增的 D26–D70 按主题成组：同一主题既有全员可见版本，也有若干措辞相近的受限版本（例如 D06/D29/D56 全员版 vs D14/D26/D27/D28 受限版）。
目的是让受限文档在全库检索中排到有权限文档前面，从而区分 post_filter（检索后删除，可能把 top-k 挤空）和 pre_filter（检索时过滤）。

## 接入你自己的检索器
替换 `kb.py` 里 `BM25.scores(query, candidate_ids)`，返回 `[(doc_id, score), ...]` 即可。
pre_filter 的关键是把 `candidate_ids`（该角色可见文档）作为检索时的元数据过滤条件传进去，
在 Chroma 里就是 `where={"allowed_roles": ...}` 这一类过滤，而不是检索完再删。

## 当前 badcase（BM25，全部为 draft 用例，仅供内部参考）
| 原因 | 用例 | 优化动作 |
|---|---|---|
| 未召回：同义词/缩写不匹配 | A13 phone↔device fingerprint，A38 holiday↔leave，A39 payday↔paid，A51 exco，A86 rated 1 twice↔consecutive cycles | 阶段 B 加向量检索与混合（RRF） |
| 未召回：分词丢弃中文 | A83 `PIP 一般多长时间？` | 分词器支持 CJK，或依赖向量检索 |
| 排序错误：跨文档第二篇排不进 top-3 | A43，A44，A54 | 切块后按 chunk 召回；考虑 k 扫描 |
| 权限策略：post_filter 删除后 top-k 被挤空 | A07（pre_filter 命中，post_filter 丢失） | 这是 post_filter 的结构性缺陷，作为实验结论保留 |
