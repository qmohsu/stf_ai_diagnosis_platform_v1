# Golden 评测摘要

- 提交：`3cc1a3aaf976`；模式：cloud / 思考 off / 用途 comparison / 预算倍数 1.67
- 模型：deepseek/deepseek-v3.2（cloud generic @openrouter.ai）；判卷：z-ai/glm-5.1；分词：cl100k_base
- 子代理预算：{'subagent_wall_clock_s': 400.79999999999995, 'subagent_request_limit': 20, 'subagent_max_tokens': 20521, 'subagent_temperature': 0.2, 'tool_result_max_tokens': 2000, 'subagent_tool_result_max_tokens': 16000}
- 并发：6 路；单题硬上限 861.5999999999999 秒
- 第 1 遍 `20260923T230536Z`：45/45 题，无效：sub-agent error (infrastructure) on 4: yamaha-road-test-compound-002, yamaha-road-test-event-finding-001, yamaha-road-test-signal-stats-001, yamaha-road-test-signal-stats-002，脱敏 0 处

## 总分

| lane | 第 1 遍 | 均值 | V2 参考 |
|---|---|---|---|
| 手册 lane（30） | 0.885 | 0.885 | — |
| OBD lane（15） | 0.689 | 0.689 | — |

## 手册 lane（30）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial | 6 | 0.728 |
| cross-section | 6 | 0.908 |
| image-required | 6 | 0.898 |
| lookup | 6 | 0.913 |
| procedural | 6 | 0.977 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.885 |
| claim_precision | 0.854 |
| exploration_cost | 0.217 |
| fact_recall | 0.883 |
| fact_density | 0.866 |
| hallucination_penalty | 0.960 |
| citation_quality | 0.920 |
| answer_quality | 0.857 |
| trajectory_efficiency | 0.861 |
| value_accuracy | 1.000 |
| overall | 0.885 |

**耗时与预算**

- 单题耗时：中位 41.7 秒，95 分位 81.2 秒，最长 84.3 秒
- 请求数 95 分位 11，token 95 分位 239505
- 收尾原因：complete 30；被预算截断（非 complete）0 题；触发补问 0 题；思考字数合计 1549

**依赖图的题（手册图片回头条件用）**

| 题 | 得分 |
|---|---|
| 1163cad68d16-image-001 | 0.686 |
| 1163cad68d16-image-002 | 0.910 |
| 1163cad68d16-image-003 | 0.902 |
| 1163cad68d16-image-004 | 0.985 |
| 1163cad68d16-image-005 | 0.925 |
| 1163cad68d16-image-006 | 0.982 |
| 均值 | 0.898 |

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| 63cad68d16-adversarial-001 | adversarial | 0.796 | — | — |
| 63cad68d16-adversarial-002 | adversarial | 0.792 | — | — |
| 63cad68d16-adversarial-003 | adversarial | 0.665 | — | — |
| 63cad68d16-adversarial-004 | adversarial | 0.665 | — | — |
| 63cad68d16-adversarial-005 | adversarial | 0.838 | — | — |
| 63cad68d16-adversarial-006 | adversarial | 0.608 | — | — |
| 106-1163cad68d16-cross-001 | cross-section | 0.741 | — | — |
| 106-1163cad68d16-cross-002 | cross-section | 1.000 | — | — |
| 106-1163cad68d16-cross-003 | cross-section | 0.992 | — | — |
| 106-1163cad68d16-cross-004 | cross-section | 0.755 | — | — |
| 106-1163cad68d16-cross-005 | cross-section | 0.982 | — | — |
| 106-1163cad68d16-cross-006 | cross-section | 0.977 | — | — |
| -a106-1163cad68d16-dtc-001 | procedural | 0.992 | — | — |
| 106-1163cad68d16-image-001 | image-required | 0.686 | — | — |
| 106-1163cad68d16-image-002 | image-required | 0.910 | — | — |
| 106-1163cad68d16-image-003 | image-required | 0.902 | — | — |
| 106-1163cad68d16-image-004 | image-required | 0.985 | — | — |
| 106-1163cad68d16-image-005 | image-required | 0.925 | — | — |
| 106-1163cad68d16-image-006 | image-required | 0.982 | — | — |
| 06-1163cad68d16-lookup-001 | lookup | 0.825 | — | — |
| 06-1163cad68d16-lookup-002 | lookup | 0.818 | — | — |
| 06-1163cad68d16-lookup-003 | lookup | 0.943 | — | — |
| 06-1163cad68d16-lookup-004 | lookup | 0.992 | — | — |
| 06-1163cad68d16-lookup-005 | lookup | 0.950 | — | — |
| 06-1163cad68d16-lookup-006 | lookup | 0.950 | — | — |
| 163cad68d16-procedural-002 | procedural | 0.992 | — | — |
| 163cad68d16-procedural-003 | procedural | 0.943 | — | — |
| 163cad68d16-procedural-004 | procedural | 0.977 | — | — |
| 163cad68d16-procedural-005 | procedural | 0.977 | — | — |
| 163cad68d16-procedural-006 | procedural | 0.977 | — | — |

## OBD lane（15）

**按题型**

| 题型 | 题数 | 均分 |
|---|---|---|
| adversarial_obd | 3 | 0.568 |
| compound_obd | 3 | 0.606 |
| dtc_decode | 3 | 0.947 |
| dtc_enumeration | 2 | 0.992 |
| event_finding | 2 | 0.646 |
| signal_statistics | 2 | 0.350 |

**按维度（所有遍的均值）**

| 维度 | 均值 |
|---|---|
| section_recall | 0.533 |
| claim_precision | 0.739 |
| exploration_cost | 0.000 |
| fact_recall | 0.667 |
| fact_density | 0.667 |
| hallucination_penalty | 0.960 |
| citation_quality | 0.767 |
| answer_quality | 0.555 |
| trajectory_efficiency | 1.000 |
| value_accuracy | 1.000 |
| overall | 0.689 |

**耗时与预算**

- 单题耗时：中位 68.9 秒，95 分位 186.2 秒，最长 186.2 秒
- 请求数 95 分位 14，token 95 分位 87052
- 收尾原因：complete 11, error 4；被预算截断（非 complete）4 题；触发补问 0 题；思考字数合计 0

**逐题**

| 题 | 题型 | 第 1 遍 | V2 参考 | 差值 |
|---|---|---|---|---|
| -road-test-adversarial-001 | adversarial_obd | 0.585 | — | — |
| -road-test-adversarial-002 | adversarial_obd | 0.177 | — | — |
| -road-test-adversarial-003 | adversarial_obd | 0.943 | — | — |
| aha-road-test-compound-001 | compound_obd | 0.714 | — | — |
| aha-road-test-compound-002 | compound_obd | 0.350 | — | — |
| aha-road-test-compound-003 | compound_obd | 0.755 | — | — |
| a-road-test-dtc-decode-001 | dtc_decode | 0.968 | — | — |
| a-road-test-dtc-decode-002 | dtc_decode | 0.925 | — | — |
| a-road-test-dtc-decode-003 | dtc_decode | 0.948 | — | — |
| aha-road-test-dtc-enum-001 | dtc_enumeration | 0.992 | — | — |
| aha-road-test-dtc-enum-002 | dtc_enumeration | 0.992 | — | — |
| oad-test-event-finding-001 | event_finding | 0.350 | — | — |
| oad-test-event-finding-002 | event_finding | 0.943 | — | — |
| road-test-signal-stats-001 | signal_statistics | 0.350 | — | — |
| road-test-signal-stats-002 | signal_statistics | 0.350 | — | — |
