# Golden 评测摘要

- 提交：`3cc1a3aaf976`；模式：local / 思考 off / 用途 baseline / 预算倍数 1.0
- 模型：Qwen/Qwen3.6-27B-FP8（local qwen-vllm @127.0.0.1:8010）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 300.0, 'subagent_request_limit': 20, 'subagent_max_tokens': 12288, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 660.0 秒
- 第 1 遍 `20260923T220146Z`：45/45 题，有效，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.894 | 0.894 | — |
| OBD lane（15） | 0.872 | 0.872 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.815 |
| cross-section | 6 | 0.892 |
| image-required | 6 | 0.911 |
| lookup | 6 | 0.873 |
| procedural | 6 | 0.979 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.865 |
| claim_precision | 0.854 |
| exploration_cost | 0.186 |
| fact_recall | 0.914 |
| fact_density | 0.912 |
| hallucination_penalty | 0.960 |
| citation_quality | 0.930 |
| answer_quality | 0.844 |
| trajectory_efficiency | 0.696 |
| value_accuracy | 1.000 |
| overall | 0.894 |

**耗时与预算**

- 单题耗时：中位 148.1 秒，95 分位 217.0 秒，最长 219.1 秒
- 请求数 95 分位 13，token 95 分位 271813
- 收尾原因：complete 30；被预算截断（非 complete）0 题；触发补问 10 题；思考字数合计 0

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.830 |
| 1163cad68d16-image-002 | 0.968 |
| 1163cad68d16-image-003 | 0.917 |
| 1163cad68d16-image-004 | 0.865 |
| 1163cad68d16-image-005 | 0.895 |
| 1163cad68d16-image-006 | 0.992 |
| 均值 | 0.911 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.796 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.823 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.762 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.762 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.838 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.911 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.833 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 0.818 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.755 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 0.967 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.977 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 0.995 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.830 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.968 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.917 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.865 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.895 | — | — |
| 106-1163cad68d16-image-006 | image-required | 0.992 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 0.875 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.992 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.950 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.857 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.570 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.992 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.992 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.926 | — | — |
| 163cad68d16-procedural-004 | procedural | 0.995 | — | — |
| 163cad68d16-procedural-005 | procedural | 1.000 | — | — |
| 163cad68d16-procedural-006 | procedural | 0.963 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.749 |
| compound_obd | 3 | 0.786 |
| dtc_decode | 3 | 0.957 |
| dtc_enumeration | 2 | 0.885 |
| event_finding | 2 | 0.985 |
| signal_statistics | 2 | 0.934 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.889 |
| claim_precision | 0.761 |
| exploration_cost | 0.000 |
| fact_recall | 0.933 |
| fact_density | 0.933 |
| hallucination_penalty | 0.960 |
| citation_quality | 0.700 |
| answer_quality | 0.743 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.872 |

**耗时与预算**

- 单题耗时：中位 57.8 秒，95 分位 121.6 秒，最长 121.6 秒
- 请求数 95 分位 20，token 95 分位 216228
- 收尾原因：budget 2, complete 13；被预算截断（非 complete）2 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.578 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.727 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.943 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.680 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.802 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.875 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.968 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.910 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.992 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 1.000 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 0.770 | — | — |
| oad-test-event-finding-001 | event_finding | 0.977 | — | — |
| oad-test-event-finding-002 | event_finding | 0.992 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.927 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.940 | — | — |
