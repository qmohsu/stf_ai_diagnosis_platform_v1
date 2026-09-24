# Golden 评测摘要

- 提交：`60f81b496a8c`；模式：local / 思考 off / 用途 calibration / 预算倍数 2.0
- 模型：Qwen/Qwen3.6-27B-FP8（local qwen-vllm @127.0.0.1:8010）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 360.0, 'subagent_request_limit': 24, 'subagent_max_tokens': 24576, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000}
- 并发：6 路；单题硬上限 780.0 秒
- 第 1 遍 `20260923T200921Z`：45/45 题，有效，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.764 | 0.764 | — |
| OBD lane（15） | 0.853 | 0.853 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.722 |
| cross-section | 6 | 0.645 |
| image-required | 6 | 0.800 |
| lookup | 6 | 0.714 |
| procedural | 6 | 0.937 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.782 |
| claim_precision | 0.938 |
| exploration_cost | 0.800 |
| fact_recall | 0.688 |
| fact_density | 0.688 |
| hallucination_penalty | 0.940 |
| citation_quality | 0.310 |
| answer_quality | 0.688 |
| trajectory_efficiency | 0.598 |
| value_accuracy | 1.000 |
| overall | 0.764 |

**耗时与预算**

- 单题耗时：中位 60.5 秒，95 分位 98.1 秒，最长 105.0 秒
- 请求数 95 分位 12，token 95 分位 97553
- 收尾原因：complete 30；被预算截断（非 complete）0 题；触发补问 10 题；思考字数合计 0

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.734 |
| 1163cad68d16-image-002 | 0.912 |
| 1163cad68d16-image-003 | 0.713 |
| 1163cad68d16-image-004 | 0.739 |
| 1163cad68d16-image-005 | 0.860 |
| 1163cad68d16-image-006 | 0.842 |
| 均值 | 0.800 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.796 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.385 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.757 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.858 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.892 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.646 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.522 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 0.590 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 0.425 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.762 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 0.645 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.927 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 0.943 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.734 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.912 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.713 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.739 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.860 | — | — |
| 106-1163cad68d16-image-006 | image-required | 0.842 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 0.950 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.422 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.932 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.436 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.562 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.982 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.892 | — | — |
| 163cad68d16-procedural-004 | procedural | 0.900 | — | — |
| 163cad68d16-procedural-005 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-006 | procedural | 1.000 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.482 |
| compound_obd | 3 | 0.889 |
| dtc_decode | 3 | 0.962 |
| dtc_enumeration | 2 | 0.984 |
| event_finding | 2 | 0.992 |
| signal_statistics | 2 | 0.924 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.800 |
| claim_precision | 0.647 |
| exploration_cost | 0.000 |
| fact_recall | 0.933 |
| fact_density | 0.933 |
| hallucination_penalty | 0.980 |
| citation_quality | 0.733 |
| answer_quality | 0.810 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.853 |

**耗时与预算**

- 单题耗时：中位 44.1 秒，95 分位 101.0 秒，最长 101.0 秒
- 请求数 95 分位 18，token 95 分位 166671
- 收尾原因：complete 15；被预算截断（非 complete）0 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.578 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.275 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.593 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.817 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.858 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.992 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.968 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.925 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.992 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 1.000 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 0.968 | — | — |
| oad-test-event-finding-001 | event_finding | 0.992 | — | — |
| oad-test-event-finding-002 | event_finding | 0.992 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.913 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.935 | — | — |
