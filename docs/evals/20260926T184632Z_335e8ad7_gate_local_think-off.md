# Golden 评测摘要

- 提交：`335e8ad77694`；模式：local / 思考 off / 用途 gate / 预算倍数 1.0
- 模型：Qwen/Qwen3.6-27B-FP8（local qwen-vllm @127.0.0.1:8010）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 300.0, 'subagent_request_limit': 20, 'subagent_max_tokens': 12288, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 660.0 秒
- 第 1 遍 `20260926T184632Z`：45/45 题，有效，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.862 | 0.862 | — |
| OBD lane（15） | 0.896 | 0.896 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.774 |
| cross-section | 6 | 0.909 |
| image-required | 6 | 0.931 |
| lookup | 6 | 0.734 |
| procedural | 6 | 0.960 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.838 |
| claim_precision | 0.750 |
| exploration_cost | 0.219 |
| fact_recall | 0.875 |
| fact_density | 0.860 |
| hallucination_penalty | 0.980 |
| citation_quality | 0.907 |
| answer_quality | 0.797 |
| trajectory_efficiency | 0.678 |
| value_accuracy | 1.000 |
| overall | 0.862 |

**耗时与预算**

- 单题耗时：中位 210.1 秒，95 分位 300.0 秒，最长 339.4 秒
- 请求数 95 分位 13，token 95 分位 276289
- 收尾原因：complete 30；被预算截断（非 complete）0 题；触发补问 10 题；思考字数合计 0

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.982 |
| 1163cad68d16-image-002 | 0.931 |
| 1163cad68d16-image-003 | 0.902 |
| 1163cad68d16-image-004 | 0.913 |
| 1163cad68d16-image-005 | 0.865 |
| 1163cad68d16-image-006 | 0.992 |
| 均值 | 0.931 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.742 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.846 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.642 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.762 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.804 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.846 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.767 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 0.988 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.763 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 0.967 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.970 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 0.992 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.982 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.931 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.902 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.913 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.865 | — | — |
| 106-1163cad68d16-image-006 | image-required | 0.992 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 0.875 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.818 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.935 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.842 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.578 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.358 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.950 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.926 | — | — |
| 163cad68d16-procedural-004 | procedural | 0.992 | — | — |
| 163cad68d16-procedural-005 | procedural | 0.938 | — | — |
| 163cad68d16-procedural-006 | procedural | 0.963 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.959 |
| compound_obd | 3 | 0.683 |
| dtc_decode | 3 | 0.974 |
| dtc_enumeration | 2 | 1.000 |
| event_finding | 2 | 0.845 |
| signal_statistics | 2 | 0.947 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.933 |
| claim_precision | 0.869 |
| exploration_cost | 0.000 |
| fact_recall | 0.900 |
| fact_density | 0.900 |
| hallucination_penalty | 0.960 |
| citation_quality | 0.767 |
| answer_quality | 0.787 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.896 |

**耗时与预算**

- 单题耗时：中位 84.2 秒，95 分位 300.1 秒，最长 300.1 秒
- 请求数 95 分位 14，token 95 分位 189459
- 收尾原因：complete 14, timeout 1；被预算截断（非 complete）1 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.943 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.950 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.985 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.323 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.742 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.985 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.968 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.977 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.977 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 1.000 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 1.000 | — | — |
| oad-test-event-finding-001 | event_finding | 0.977 | — | — |
| oad-test-event-finding-002 | event_finding | 0.713 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.943 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.952 | — | — |
