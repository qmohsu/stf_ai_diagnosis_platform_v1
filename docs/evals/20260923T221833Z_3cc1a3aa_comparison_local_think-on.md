# Golden 评测摘要

- 提交：`3cc1a3aaf976`；模式：local / 思考 on / 用途 comparison / 预算倍数 2.0
- 模型：Qwen/Qwen3.6-27B-FP8（local qwen-vllm @127.0.0.1:8010）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 600.0, 'subagent_request_limit': 40, 'subagent_max_tokens': 24576, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 1260.0 秒
- 第 1 遍 `20260923T221833Z`：45/45 题，有效，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.853 | 0.853 | — |
| OBD lane（15） | 0.902 | 0.902 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.736 |
| cross-section | 6 | 0.874 |
| image-required | 6 | 0.876 |
| lookup | 6 | 0.817 |
| procedural | 6 | 0.960 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.844 |
| claim_precision | 0.750 |
| exploration_cost | 0.294 |
| fact_recall | 0.832 |
| fact_density | 0.831 |
| hallucination_penalty | 0.980 |
| citation_quality | 0.827 |
| answer_quality | 0.839 |
| trajectory_efficiency | 0.718 |
| value_accuracy | 1.000 |
| overall | 0.853 |

**耗时与预算**

- 单题耗时：中位 271.6 秒，95 分位 418.5 秒，最长 450.4 秒
- 请求数 95 分位 11，token 95 分位 224118
- 收尾原因：complete 30；被预算截断（非 complete）0 题；触发补问 2 题；思考字数合计 370724

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.467 |
| 1163cad68d16-image-002 | 0.963 |
| 1163cad68d16-image-003 | 0.917 |
| 1163cad68d16-image-004 | 0.977 |
| 1163cad68d16-image-005 | 0.955 |
| 1163cad68d16-image-006 | 0.977 |
| 均值 | 0.876 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.742 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.799 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.654 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.558 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.815 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.846 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.730 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.763 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 0.824 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.930 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 0.992 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.467 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.963 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.917 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.977 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.955 | — | — |
| 106-1163cad68d16-image-006 | image-required | 0.977 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 0.875 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.733 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.943 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.887 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.608 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.858 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.935 | — | — |
| 163cad68d16-procedural-004 | procedural | 0.935 | — | — |
| 163cad68d16-procedural-005 | procedural | 1.000 | — | — |
| 163cad68d16-procedural-006 | procedural | 0.955 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.738 |
| compound_obd | 3 | 0.854 |
| dtc_decode | 3 | 0.964 |
| dtc_enumeration | 2 | 0.988 |
| event_finding | 2 | 0.995 |
| signal_statistics | 2 | 0.950 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.933 |
| claim_precision | 0.816 |
| exploration_cost | 0.000 |
| fact_recall | 0.933 |
| fact_density | 0.933 |
| hallucination_penalty | 0.920 |
| citation_quality | 0.700 |
| answer_quality | 0.871 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.902 |

**耗时与预算**

- 单题耗时：中位 63.0 秒，95 分位 254.7 秒，最长 254.7 秒
- 请求数 95 分位 14，token 95 分位 215102
- 收尾原因：complete 15；被预算截断（非 complete）0 题；触发补问 0 题；思考字数合计 85423

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.943 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.328 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.943 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.748 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.825 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.988 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.943 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.963 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.988 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 1.000 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 0.975 | — | — |
| oad-test-event-finding-001 | event_finding | 0.992 | — | — |
| oad-test-event-finding-002 | event_finding | 0.997 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.932 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.967 | — | — |
