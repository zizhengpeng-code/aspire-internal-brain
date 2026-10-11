# Aspire FinTech Track — Role-aware Enterprise Knowledge Base

模拟公司：LumenPay（虚构的新加坡消费信贷 fintech）。语料里的公司、人物、编号和数字全部虚构。

## 结构
- `data/corpus.json`：70 篇模拟企业文档，每篇带 department / sensitivity / allowed_roles
- `data/eval.json`：测试用例，字段见下
- `retrieval.py`：切块；BM25、向量（LangChain + Chroma）、混合（RRF）三种检索器，文档级或 chunk 级
- `kb.py`：三种权限策略（no_acl / post_filter / pre_filter）、JSONL 审计日志，检索器可替换
- `sync.py`：增量同步 Chroma 索引（只重新切块、嵌入有变化的文档）
- `evaluate.py`：实验 1 权限策略对比，实验 2 拒答阈值扫描，实验 3 检索器对比；每次运行落盘到 `results/<时间戳>.md|json`
- `tests/`：用假向量验证预过滤、增量同步、权限撤销即时生效，不调用 API

## 运行
    pip install -r requirements.txt
    python evaluate.py                        # BM25，chunk 级（默认），不需要 API
    python evaluate.py --unit doc             # BM25，文档级对照
    python evaluate.py --retriever hybrid     # 需要 EMBED_MODEL，见下
    python evaluate.py --compare              # 实验 3：bm25 / vector / hybrid × doc / chunk
    python evaluate.py --no-save              # 只打印，不写 results/
    python -m unittest discover tests
审计日志写到 `logs/audit_<mode>.jsonl`。

向量检索走任意 OpenAI 兼容接口，通过环境变量配置：

| 变量 | 说明 |
|---|---|
| `EMBED_MODEL` | 嵌入模型名；`fake` 为确定性随机向量，只用于联调，`evaluate.py` 会拒绝保存其结果 |
| `EMBED_BASE_URL` / `EMBED_API_KEY` | 不设置时回落到 `LLM_BASE_URL` / `LLM_API_KEY` |

嵌入结果按 (模型, 文本) 缓存在 `.cache/embeddings.sqlite`，Chroma 索引在 `index/chroma/`，两者都不进 git。
重复跑测评或同步未变化的文档不会再调用 API。

## 测试用例字段
| 字段 | 说明 |
|---|---|
| `role` / `question` | 提问角色与问题 |
| `gold` | 回答所需文档 ID；跨文档问题为列表，需全部召回才算 full_hit |
| `expect` | `answer`（该角色有权限）或 `deny`（越权探测，gold 为该角色不可见的文档） |
| `tag` | `plain` / `colloquial` / `zh_mix` / `cross_doc` / `injection` |
| `source` | `user` = 作者本人确认过；`draft` = 候选草稿 |

报告分两段输出：`user` 子集是唯一可以对外使用的数字；`all` 含草稿，仅供内部迭代。
确认用例用复核表（需要 openpyxl，仅此脚本依赖）：
    python review.py export   # 生成 review/eval_review.xlsx
    python review.py apply    # 在「确认」列标 ✓ / 改 / 删 后写回 eval.json
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

## 检索层设计（阶段 B）
- **接口**：检索器实现 `search(query, candidate_ids) -> [Hit(chunk, doc, score, gate)]`；`scores()` 保留原来的 `[(doc_id, score), ...]`（取每篇文档最高的 chunk 分）。
- **pre_filter 在检索内部完成**：BM25 只给可见文档打分；Chroma 查询带 `where={"doc_id": {"$in": 可见文档}}`。越权 chunk 不参与打分和排序，也不会被返回。
- **权限不写进索引**：可见文档集在每次查询时根据当前 ACL 计算，所以撤销权限后下一次查询立即生效，不需要重建索引（`tests/` 有对应测试）。代价是可见文档很多时 `$in` 列表会变长；规模上去后可以改成按用户组过滤的元数据字段。
- **切块**：按句子窗口切，每块最多 30 词，前缀文档标题；70 篇切成 158 块。语料每篇只有 30–60 词，所以切块带来的差异有限，真实文档更长时作用会更明显。
- **混合检索**：BM25 与向量排名做 RRF（k=60）。RRF 分数只反映名次，不能说明“都不相关”，所以拒答阈值改用余弦相似度，不同检索器的阈值要分别标定（实验 2）。
- **增量同步**：manifest 记录每篇文档的内容哈希；同步时只处理新增、修改、删除的文档。连接器每 N 分钟轮询一次时，新内容可检索的时延上限约为 N 分钟加上 `sync.py` 打印的同步耗时。

## 当前 badcase（BM25 文档级，全部为 draft 用例，仅供内部参考）
| 原因 | 用例 | 优化动作 |
|---|---|---|
| 未召回：同义词/缩写不匹配 | A13 phone↔device fingerprint，A38 holiday↔leave，A39 payday↔paid，A51 exco，A86 rated 1 twice↔consecutive cycles | 阶段 B 加向量检索与混合（RRF） |
| 未召回：分词丢弃中文 | A83 `PIP 一般多长时间？` | 分词器支持 CJK，或依赖向量检索 |
| 排序错误：跨文档第二篇排不进 top-3 | A43，A44，A54 | 切块后按 chunk 召回；考虑 k 扫描 |
| 权限策略：post_filter 删除后 top-k 被挤空 | A07（pre_filter 命中，post_filter 丢失） | 这是 post_filter 的结构性缺陷，作为实验结论保留 |
