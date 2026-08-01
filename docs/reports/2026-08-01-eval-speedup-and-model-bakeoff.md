# 评测提速 + 模型烤箱赛 —— 工作总结(2026-07-31 ~ 08-01)

作者:Xiangzhu Yan

## TL;DR

一个工作周期内完成三件事:**全量评测从 80.5 分钟压到 10.7 分钟(7.5 倍)**;
挖出并修复一个让整条 manual lane 虚假掉分 0.10 的**评分基准错位**(#234,
并非真实回归);完成 **Qwen3.6 两候选模型的量化考核**,选出与现役模型质量
打平、速度生态全面占优的继任者(Qwen3.6-27B-FP8 + vLLM,待拍板)。

## 1. HARNESS-31:评测提速(#225,PR #233 已合并)

| 阶段 | 机制 | 双 lane 全量耗时 | 分数 |
|---|---|---|---|
| 原状(串行) | — | 80.5 min | 0.790* |
| 方向 1:judge 流水线 | judge 调用移出关键路径(默认生效) | 54.6 min(−32%) | 0.791(Δ+0.002)|
| 方向 2:双 Ollama 实例 | 每卡一份模型 + `EVAL_LLM_ENDPOINTS` 池 | 26.6 min(−67%) | 0.790(Δ±0.000)|

\* 当日对照值;见 §2 的基准错位。

- 关键发现:**qwen3.5 架构在 Ollama 0.17 上不支持批量并发**(调度器强制
  `Parallel:1`),`OLLAMA_NUM_PARALLEL` 是死路 —— 双实例是正确绕行。
- 方向 3(内容寻址缓存)、方向 4(affected-tier)留作 follow-up。
- 新旋钮:`--run-concurrency` / `--judge-concurrency` /
  `EVAL_AGENT_WALL_SECONDS` / `EVAL_LLM_ENDPOINTS`。

## 2. #234:0.891→0.790 的"回归"是评分参照系错位(PR #235 已合并)

- **根因**:HARNESS-30 index 切换后 agent 引用 node-id 锚点,但 indexed
  期望文件的选择只存在于服务器临时文件状态、从未进代码;被 git 操作清掉后,
  所有评测都在拿 legacy-slug 期望给 node-id 引用打零分。
- **证据链**:judge 重判旧输出稳定(0.891→0.898);掉分全在锚点匹配维度而
  `answer_quality` 反升 +0.07;最差三题的引用与 overlay 期望逐字节一致;
  交叉重判 = 精确回到 0.891。
- **修复**:`resolve_manual_golden_path()` 随 `MANUAL_INDEX_TRACK` 选
  golden 文件(RAG lane 刻意保留 legacy);修复后全量跑 manual 0.889 /
  RAG 0.243 → `harness234_postfix_reference_20260801.json` 为新基准。
- **新工具**:`scripts/regrade_report.py` —— 离线重判存档输出
  (`--golden-file` 交叉重判),排查此类问题不再需要 GPU。
- **教训**:一切影响评分的输入选择必须进代码,不能依赖服务器手工状态。

## 3. 模型烤箱赛:Qwen3.6 双候选(PR #236 待合并)

评测体系首次用于模型验收:代码冻结、同考卷同判卷,vLLM 0.24 双卡张量并行,
6 路并发,每候选 2 轮。

| 候选 | 两轮均值 | 挂题(<0.4) | 每轮耗时 | 判词 |
|---|---|---|---|---|
| qwen3.5:27b-q8(现役,参照) | 0.889 | 0 | 51 min | — |
| **Qwen3.6-27B-FP8** | **0.881(统计打平)** | **0** | **10.7 min** | **推荐采纳** |
| Qwen3.6-35B-A3B-FP8 | 0.815 | 2-3 题两轮复现 | 6.5 min | 淘汰 |

- 35B-A3B 的失败是**稳定的行为缺陷**(第 1 轮迭代即拒答可答题;工具调用
  文本泄漏为最终答案)—— MoE 3B 激活参数(等效 ~10B dense)牺牲的是
  agent 纪律,不是知识。按总参数选型是陷阱。
- 烤箱赛逼出并修复了**两个存量 bug**:`OpenAILLMClient` 对空 tools 数组
  发 400(强制收卷路径,Ollama 容忍 / vLLM 拒绝);HARNESS-31 墙旋钮对
  frozen dataclass 赋值会崩(从未被真正执行过的潜伏弹)。
- **并行度实测**:6-8 路是甜点;15 路单题 112s→600s、撞墙伤分、吞吐
  不升反降 —— agent 负载 prefill 密集,饱和拐点远早于纯解码理论。
- vLLM 附带收益:PagedAttention 动态 KV 池(677k token,6.89 路可各自
  用满 98k 上下文)、前缀缓存、qwen35 无法批处理的限制被永久终结。

## 4. 评测耗时演进(全量 30 goldens)

```
80.5 min(串行 Ollama)→ 54.6(judge 流水线)→ 26.6(双实例)
→ 10.7 min(Qwen3.6-27B + vLLM,6 路)     累计 7.5×
```

## 5. 待决事项(用户)

1. 合并 PR #236(eval 适配 + bug 修复 + 烤箱赛报告)。
2. 是否采纳 Qwen3.6-27B-FP8 + vLLM 转正;若采纳,生产 V1 本地路径的
   Ollama→vLLM 迁移需独立 ticket(配置 + 客户端方言适配 + 部署验证)。
3. 服务器当前为实验姿态:vLLM 服务 27B 于 :8010,Ollama 大模型已卸载
   (嵌入正常),checkout 在 `model-bakeoff-vllm-adapter` 分支。

## 6. 过程资产

- 报告:`harness31_{serial_control,pipeline_validation}_20260731.json`、
  `harness31_dual_instance_c2_20260801.json`、
  `harness234_postfix_reference_20260801.json`(新基准)、
  `bakeoff_qwen36_{27b,35b}_r{1,2}.json`
- 工具:`regrade_report.py`、`orchestrator.py`/`lanes.py`、
  `/tmp/vllm_serve.sh`(服务器)
- 运维备忘(`.claude/memory.md`):podman 依赖链拆除顺序、compose 多
  -f 覆盖失效、qwen35 无并发、ssh 后台任务挂死的兜底轮询模式
