# Golden 评测摘要

- 提交：`46b4bac8d6d9`；模式：local / 思考 off / 用途 calibration / 预算倍数 2.0
- 模型：Qwen/Qwen3.6-27B-FP8（local qwen-vllm @127.0.0.1:8010）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 300.0, 'subagent_request_limit': 40, 'subagent_max_tokens': 24576, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 660.0 秒
- 第 1 遍 `20260923T212636Z`：45/45 题，有效，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.857 | 0.857 | — |
| OBD lane（15） | 0.854 | 0.854 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.666 |
| cross-section | 6 | 0.916 |
| image-required | 6 | 0.906 |
| lookup | 6 | 0.827 |
| procedural | 6 | 0.970 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.874 |
| claim_precision | 0.792 |
| exploration_cost | 0.253 |
| fact_recall | 0.849 |
| fact_density | 0.828 |
| hallucination_penalty | 0.960 |
| citation_quality | 0.930 |
| answer_quality | 0.805 |
| trajectory_efficiency | 0.744 |
| value_accuracy | 1.000 |
| overall | 0.857 |

**耗时与预算**

- 单题耗时：中位 138.4 秒，95 分位 258.6 秒，最长 298.8 秒
- 请求数 95 分位 13，token 95 分位 257826
- 收尾原因：complete 30；被预算截断（非 complete）0 题；触发补问 10 题；思考字数合计 0

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.827 |
| 1163cad68d16-image-002 | 0.946 |
| 1163cad68d16-image-003 | 0.880 |
| 1163cad68d16-image-004 | 0.890 |
| 1163cad68d16-image-005 | 0.895 |
| 1163cad68d16-image-006 | 1.000 |
| 均值 | 0.906 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.808 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.858 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.750 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.600 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.454 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.527 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.773 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 0.992 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.763 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.970 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 0.992 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.827 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.946 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.880 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.890 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.895 | — | — |
| 106-1163cad68d16-image-006 | image-required | 1.000 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 1.000 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.592 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.950 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.864 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.570 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.985 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-004 | procedural | 1.000 | — | — |
| 163cad68d16-procedural-005 | procedural | 0.946 | — | — |
| 163cad68d16-procedural-006 | procedural | 0.995 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.479 |
| compound_obd | 3 | 0.871 |
| dtc_decode | 3 | 0.957 |
| dtc_enumeration | 2 | 1.000 |
| event_finding | 2 | 0.996 |
| signal_statistics | 2 | 0.949 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.800 |
| claim_precision | 0.662 |
| exploration_cost | 0.000 |
| fact_recall | 0.933 |
| fact_density | 0.933 |
| hallucination_penalty | 0.940 |
| citation_quality | 0.767 |
| answer_quality | 0.835 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.854 |

**耗时与预算**

- 单题耗时：中位 46.8 秒，95 分位 126.7 秒，最长 126.7 秒
- 请求数 95 分位 17，token 95 分位 162251
- 收尾原因：complete 15；被预算截断（非 complete）0 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.578 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.267 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.593 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.770 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.851 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.992 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.968 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.910 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.992 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 1.000 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 1.000 | — | — |
| oad-test-event-finding-001 | event_finding | 1.000 | — | — |
| oad-test-event-finding-002 | event_finding | 0.992 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.938 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.959 | — | — |
