# Golden 评测摘要

- 提交：`b06e5dfd518d`；模式：local / 思考 off / 用途 gate / 预算倍数 1.0
- 模型：Qwen/Qwen3.6-27B-FP8（local qwen-vllm @127.0.0.1:8010）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 300.0, 'subagent_request_limit': 20, 'subagent_max_tokens': 12288, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 660.0 秒
- 第 1 遍 `20260927T232236Z`：45/45 题，无效：sub-agent error (infrastructure) on 1: 0a2ba199-665f-41aa-a106-1163cad68d16-image-006，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.829 | 0.829 | — |
| OBD lane（15） | 0.858 | 0.858 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.841 |
| cross-section | 6 | 0.694 |
| image-required | 6 | 0.893 |
| lookup | 6 | 0.830 |
| procedural | 6 | 0.886 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.792 |
| claim_precision | 0.757 |
| exploration_cost | 0.319 |
| fact_recall | 0.831 |
| fact_density | 0.808 |
| hallucination_penalty | 0.950 |
| citation_quality | 0.807 |
| answer_quality | 0.743 |
| trajectory_efficiency | 0.693 |
| value_accuracy | 1.000 |
| overall | 0.829 |

**耗时与预算**

- 单题耗时：中位 152.8 秒，95 分位 245.8 秒，最长 268.8 秒
- 请求数 95 分位 13，token 95 分位 275137
- 收尾原因：complete 29, error 1；被预算截断（非 complete）1 题；触发补问 12 题；思考字数合计 0

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.977 |
| 1163cad68d16-image-002 | 0.945 |
| 1163cad68d16-image-003 | 0.872 |
| 1163cad68d16-image-004 | 0.880 |
| 1163cad68d16-image-005 | 0.902 |
| 1163cad68d16-image-006 | 0.780 |
| 均值 | 0.893 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.742 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.858 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.785 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.773 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.935 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.954 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.300 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 0.825 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.287 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 0.977 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.773 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 1.000 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.977 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.945 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.872 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.880 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.902 | — | — |
| 106-1163cad68d16-image-006 | image-required | 0.780 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 0.875 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.818 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.918 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.797 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.578 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.992 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.926 | — | — |
| 163cad68d16-procedural-004 | procedural | 0.550 | — | — |
| 163cad68d16-procedural-005 | procedural | 0.933 | — | — |
| 163cad68d16-procedural-006 | procedural | 0.963 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.892 |
| compound_obd | 3 | 0.669 |
| dtc_decode | 3 | 0.966 |
| dtc_enumeration | 2 | 0.989 |
| event_finding | 2 | 0.706 |
| signal_statistics | 2 | 0.946 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.844 |
| claim_precision | 0.822 |
| exploration_cost | 0.000 |
| fact_recall | 0.833 |
| fact_density | 0.833 |
| hallucination_penalty | 0.980 |
| citation_quality | 0.867 |
| answer_quality | 0.771 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.858 |

**耗时与预算**

- 单题耗时：中位 54.7 秒，95 分位 123.1 秒，最长 123.1 秒
- 请求数 95 分位 20，token 95 分位 266456
- 收尾原因：budget 1, complete 14；被预算截断（非 complete）1 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.992 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.693 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.992 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.300 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.707 | — | — |
| aha-road-test-compound-003 | compound_obd | 1.000 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.985 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.925 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.988 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 1.000 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 0.977 | — | — |
| oad-test-event-finding-001 | event_finding | 0.992 | — | — |
| oad-test-event-finding-002 | event_finding | 0.420 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.943 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.950 | — | — |
