# Golden 评测摘要

- 提交：`9803b99103f7`；模式：cloud / 思考 off / 用途 comparison / 预算倍数 1.0
- 模型：qwen/qwen3.6-27b（cloud qwen-openrouter @openrouter.ai）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 300.0, 'subagent_request_limit': 20, 'subagent_max_tokens': 12288, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 660.0 秒
- 第 1 遍 `20260924T103842Z`：15/15 题，有效，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| OBD lane（15） | 0.948 | 0.948 | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.992 |
| compound_obd | 3 | 0.843 |
| dtc_decode | 3 | 0.952 |
| dtc_enumeration | 2 | 0.996 |
| event_finding | 2 | 0.992 |
| signal_statistics | 2 | 0.943 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.978 |
| claim_precision | 0.855 |
| exploration_cost | 0.000 |
| fact_recall | 1.000 |
| fact_density | 1.000 |
| hallucination_penalty | 0.960 |
| citation_quality | 0.933 |
| answer_quality | 0.851 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.948 |

**耗时与预算**

- 单题耗时：中位 57.7 秒，95 分位 147.4 秒，最长 147.4 秒
- 请求数 95 分位 10，token 95 分位 100843
- 收尾原因：complete 15；被预算截断（非 complete）0 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 1.000 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.992 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.985 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.825 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.710 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.992 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.943 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.925 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.988 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 1.000 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 0.992 | — | — |
| oad-test-event-finding-001 | event_finding | 0.992 | — | — |
| oad-test-event-finding-002 | event_finding | 0.992 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.927 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.959 | — | — |
