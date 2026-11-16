# Golden 评测摘要

- 提交：`16da54dad422`；模式：local / 思考 off / 用途 gate / 预算倍数 1.0
- 模型：Qwen/Qwen3.6-27B-FP8（local qwen-vllm @127.0.0.1:8010）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 300.0, 'subagent_request_limit': 20, 'subagent_max_tokens': 12288, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 660.0 秒
- 第 1 遍 `20261115T145844Z`：45/45 题，有效，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.893 | 0.893 | — |
| OBD lane（15） | 0.918 | 0.918 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.710 |
| cross-section | 6 | 0.917 |
| image-required | 6 | 0.937 |
| lookup | 6 | 0.937 |
| procedural | 6 | 0.966 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.954 |
| claim_precision | 0.792 |
| exploration_cost | 0.264 |
| fact_recall | 0.872 |
| fact_density | 0.870 |
| hallucination_penalty | 0.970 |
| citation_quality | 0.953 |
| answer_quality | 0.855 |
| trajectory_efficiency | 0.679 |
| value_accuracy | 1.000 |
| overall | 0.893 |

**耗时与预算**

- 单题耗时：中位 128.3 秒，95 分位 229.5 秒，最长 267.5 秒
- 请求数 95 分位 12，token 95 分位 247649
- 收尾原因：complete 30；被预算截断（非 complete）0 题；触发补问 9 题；思考字数合计 0

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.970 |
| 1163cad68d16-image-002 | 0.970 |
| 1163cad68d16-image-003 | 0.895 |
| 1163cad68d16-image-004 | 0.898 |
| 1163cad68d16-image-005 | 0.895 |
| 1163cad68d16-image-006 | 0.992 |
| 均值 | 0.937 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.742 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.846 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.558 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.654 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.815 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.646 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.775 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.763 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.963 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 0.943 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.970 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.970 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.895 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.898 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.895 | — | — |
| 106-1163cad68d16-image-006 | image-required | 0.992 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 1.000 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.950 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.943 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.842 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.935 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.950 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.988 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-004 | procedural | 1.000 | — | — |
| 163cad68d16-procedural-005 | procedural | 0.926 | — | — |
| 163cad68d16-procedural-006 | procedural | 0.995 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.990 |
| compound_obd | 3 | 0.665 |
| dtc_decode | 3 | 0.979 |
| dtc_enumeration | 2 | 0.996 |
| event_finding | 2 | 0.992 |
| signal_statistics | 2 | 0.943 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.911 |
| claim_precision | 0.891 |
| exploration_cost | 0.000 |
| fact_recall | 0.933 |
| fact_density | 0.933 |
| hallucination_penalty | 0.980 |
| citation_quality | 0.900 |
| answer_quality | 0.837 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.918 |

**耗时与预算**

- 单题耗时：中位 50.6 秒，95 分位 118.6 秒，最长 118.6 秒
- 请求数 95 分位 20，token 95 分位 230185
- 收尾原因：budget 1, complete 14；被预算截断（非 complete）1 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.985 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.992 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.992 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.300 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.709 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.985 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.968 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.970 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 1.000 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 0.992 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 1.000 | — | — |
| oad-test-event-finding-001 | event_finding | 0.992 | — | — |
| oad-test-event-finding-002 | event_finding | 0.992 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.927 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.959 | — | — |
