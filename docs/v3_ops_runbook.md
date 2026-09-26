# V3 运维手册

| 文档控制 | |
|---|---|
| 版本 | v0.8（PROD-11：诊断任务与按需模型控制器一章） |
| 日期 | 2026-09-24 |
| 作者 | Xiangzhu Yan |
| 适用 | PolyU 服务器 `ssh polyu-gpu`，仓库 `~/stf_ai_diagnosis_platform_v1`，V3 容器 `stf-v3-api` / `stf-v3-worker`，宿主机服务 `stf-v3-gpu-worker`，模型服务容器 `stf-vllm`（compose 项目 `stf_llm`） |

后续章节按里程碑追加：部署 / 回滚（PROD-04 已在 CLAUDE.md "V3 Deployment"）、备份与恢复（PROD-15）、常见故障（PROD-15）。

## 1. 队列（procrastinate）

V3 的任务队列就是 `stf_v3` 库里的几张表（`procrastinate_jobs` / `procrastinate_events` / `procrastinate_workers`）。两类工人：

| 工人 | 在哪 | 监听队列 | 干什么 |
|---|---|---|---|
| `stf-v3-worker` 容器 | Podman，`-p stf_v3` | `default`（并发 2） | 心跳、stalled 回收（每 2 分钟）、演练任务；将来诊断 |
| `stf-v3-gpu-worker` | 宿主机 systemd 用户服务，`~/venv-stf-v3` | `gpu`（并发 1） | 手册入库（MinerU → 索引 → 摘要 → 质量门）、宿主机心跳 |

所有操作用一个脚本：

```bash
bash stf_v3/scripts/queue_ops.sh status            # 1 查积压与卡点
bash stf_v3/scripts/queue_ops.sh failed            # 2 看死信及原因
bash stf_v3/scripts/queue_ops.sh retry <job_id>    # 3 手动重试一条
bash stf_v3/scripts/queue_ops.sh cancel <job_id>   #   取消排队中 / 中止运行中
bash stf_v3/scripts/queue_ops.sh drill             # 4 重启回收演练
```

### 1.1 查积压与卡点

`status` 打印三张表：每个队列各状态的任务数；正在跑 / 等待中的任务（最老在前，附等待时长）；在 `doing` 超过 30 分钟且工人心跳已停的任务（回收候选）。健康接口 `GET /v3/health` 也给出 `queue_backlog_by_queue`——`gpu` 队列有积压而 `gpu_worker.alive=false`，就是宿主机工人没在跑。

预期输出（2026-09-14 实测）：

```
 queue_name |  status   | count
 default    | succeeded |  2217
 gpu        | todo      |     1
 gpu        | doing     |     1
 gpu        | succeeded |     7
```

`gpu / doing 1` = 一本手册正在入库，正常。

### 1.2 看死信及原因

`failed` 列出 `status='failed'` 的任务及其最后一个事件，再列出 `manuals` 表里 `status='failed'` 的手册与 `error_message`（流水线写的原因，例如 `disk preflight refused`、`index gates failed: I1-coverage`、`MinerU timed out`）。

哪些失败**不会**自动重试（永久性）：找不到 MinerU（任务跑错了工人）、缺云端密钥、磁盘余量不足、源 PDF 丢失、质量门不过、被取消。哪些会（最多 3 次，间隔 60 s → 120 s）：MinerU 崩溃 / 超时、云端摘要网络错误、其他异常。重试时 MinerU 的产物与已生成的摘要从工作目录续用，不重跑显卡、不重花钱。

### 1.3 手动重试

`retry <job_id>` 调用队列库的重试把任务放回 `todo`；对手册任务，重试前先看 `failed` 里的原因——永久性原因（比如质量门）重试也会再失败，应该修文件再重新上传。

### 1.4 重启后任务回收（演练）

`drill` 做的事：投一个 60 秒的演练任务到 `default` 队列 → 8 秒后 `podman restart stf-v3-worker`（任务被杀在 `doing`）→ 容器工人重启后，周期任务 `jobs.recover_stalled` 每 2 分钟把"工人心跳停止超过 120 秒"的 `doing` 任务放回 `todo` → 任务被重新领取并完成。

2026-09-14 实测：

```
 deferred           | 12:11:29
 started            | 12:11:29
 scheduled          | 12:14:00   ← recover_stalled 回收
 deferred_for_retry | 12:14:00
 started            | 12:14:05
 succeeded          | 12:15:05
DRILL PASS: job re-picked after restart (attempts = 2)
```

宿主机工人同理：`kill -9` 正在入库的手册任务 → 2 分钟内被回收 → 从工作目录续跑（MinerU 产物已在，跳过显卡阶段）。**正常部署请不要在手册 `converting` 时重启宿主机工人**（会触发一次不必要的回收重跑）。

### 1.5 连接预算（FM-25）

Postgres `max_connections = 100`。2026-09-14 实测占用：系统 5、V1 库 1、V3 库 16（接口池 5 + 容器工人 + 宿主机工人 + 空闲）。V3 的池上限在 `settings.db_pool_size`（默认 5）；两个工人各持 1–2 条长连接。总和远低于 80% 上限，不需要调整；新增工人或提高并发时重新核一次。

### 1.6 宿主机 GPU 工人

```bash
systemctl --user status stf-v3-gpu-worker            # 状态
journalctl --user -u stf-v3-gpu-worker -n 50         # 日志
systemctl --user restart stf-v3-gpu-worker           # 部署后必做（不要在 converting 时做）
bash stf_v3/gpu_worker/install.sh --check            # 11 项自检（版本、MinerU、密钥、linger、卷读写）
bash stf_v3/gpu_worker/install.sh                    # 首次安装 / 升级环境
```

它的存活与代码版本由 `/v3/health` 的 `gpu_worker` 字段和 `deploy_check.sh` 第 7 项判断（心跳文件 `.gpu_worker_status.json` 在手册卷根目录，入库期间由任务内的 ticker 线程每 30 秒刷新）。密钥单一来源：服务器 `infra/.env`（模式 600），systemd 单元通过 `EnvironmentFile` 读取，文件里没有任何字面量密钥。

### 1.7 磁盘（FM-13）

手册入库开始前检查手册卷所在盘余量，低于 `STF_V3_MANUAL_MIN_FREE_GB`（默认 30）直接标失败不开工；失败后工作目录清理；`deploy_check.sh` 第 8 项与 `/v3/health.disk_free_gb` 报水位。这块盘与 V1/V2 的 Postgres 共用——盘满会让旧系统一起停写。

## 2. 设备接入（Jetson 上传器，PROD-07）

设备侧手册：`docs/v3_device_install.md`（给装设备的人）。服务器侧我们能做的事：

### 2.1 建车队 / 车档 / 发 token（首次或加车）

```bash
cd ~/stf_ai_diagnosis_platform_v1/stf_v3
set -a && . ../infra/.env && set +a
STF_V3_DATABASE_URL="$STF_V3_APP_DATABASE_URL" ~/venv-stf-v3/bin/python scripts/onboard_first_workshop.py \
  --workshop "PolyU STF 实验车队" --manager-codes 1 --technician-codes 1 \
  --vehicle "Toyota|Hiace|<车牌>|Hiace" --vehicle "Toyota|Corolla||Corolla" \
  --env-out-dir ~/stf_v3_tokens
```

VIN 在隐藏提示里输两遍（`ssh -t` 才有 TTY），不打印、不进任何文件；每台车得到一个 600 权限的 env 文件（token 只在文件里），按车交付。重跑安全：车队按名字、车档按 VIN 复用，只新发邀请码和 token。加车也用它（`--manager-codes 0 --technician-codes 0`）。

### 2.2 看设备被拒收了什么（FM-19）

```bash
podman logs --since 7d stf-v3-api 2>&1 | grep '"ingest.rejected"' | tail -20
```

每条含 `reason`（`vin_mismatch` / `unsupported_format` / `file_too_large`）、`device_id`、`filename`、`size_bytes`、`first_line`。`first_line` 不是 `# OBD Maximum Data Log`（真机记录脚本的格式，PROD-07 D3）/ `OBD Data Log` / `# Yamaha Dual` 说明设备固件换了格式；`vin_mismatch` 多半是配置文件装错车。车档"最近活跃"在拒收时也会刷新（FM-29）——判断入库要看 `GET /v3/vehicles/{id}/logs`。

### 2.3 吊销 / 重发 token（换车、泄露）

manager 在 API 上 `DELETE /v3/devices/{id}`（立即失效；设备端会得到 401，文件留在待传目录），再 `POST /v3/vehicles/{id}/devices` 发新 token，把新 env 文件交给装设备的人。

### 2.4 真机补录（FM-25）

截止日在开发计划 §4；补录通过后在 PROD-07 条目加一行"真机验收通过 <日期>"。

## 3. 诊断运行时（PROD-08）

诊断接口要到 PROD-11 才有；现在服务器上"跑一次诊断"只有一条路：真跑脚本。

### 3.1 跑一次诊断（等价验证 / 换模型对照）

```
cd ~/stf_ai_diagnosis_platform_v1
podman exec stf-v3-api python scripts/diagnose_once.py \
  --vehicle-id <车档 UUID> --log-id <日志 UUID> --out-dir /tmp/runs [--locale zh-TW] [--wall-clock-s 1800] [--model-wait-s 600]
podman exec stf-v3-api python scripts/diagnose_once.py --vehicle-id … --log-id … --out-dir /tmp/runs --cloud   # 云端对照（§4.5）
podman cp stf-v3-api:/tmp/runs ~/prod09_runs/     # 报告 .report.md / .report.json / 事件 .events.jsonl / 消息 .messages.json
```

- 脚本先预检：模型端点 `GET /models` 必须列出配置的模型名——本机端点最多等 `--model-wait-s`（默认 600 s，vLLM 冷启动约 5 分钟）再放弃，云端只查一次、不预热；预检失败退出码 4，跑完退出码 0，被闸门截断或报告标为部分 2，模型错误 3，超过脚本总时限 5。落文件名以 `_local` / `_cloud` 结尾。
- 事件逐条打印：`tool_call / tool_result` 带工具名、耗时、结果长度；`(in <id>)` 表示子代理内部的事件；`reasoning` 是模型思考——vLLM 档（默认）关了思考，这类事件应为 0，末行 `thinking_chars=0`；只有回退到 Ollama 的 qwen3.5 才会有。`session_start` 与报告的 `model_source` 写明 `local|cloud 档名 @主机`。
- 输出目录永远不能是日志卷或手册卷（脚本会拒绝）；文件名只含时间与日志编号前 8 位，不含 VIN；报告正文可能含 VIN，**不要把报告文件拷出服务器贴进 PR / issue**。
- 耗时：vLLM + Qwen3.6-27B 一轮见 §4.6 的实测表；Ollama qwen3.5 回退档一轮 5–40 分钟。默认墙钟随档：vLLM 15 分钟、Ollama 20 分钟（`STF_V3_AGENT_WALL_CLOCK_S` 显式设置则以设置为准）。

### 3.2 配置项（全在 `infra/.env`，前缀 `STF_V3_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | `http://127.0.0.1:8010/v1` / `Qwen/Qwen3.6-27B-FP8` / `none` | 唯一模型来源（OpenAI 兼容口）= §4 的 vLLM；`LLM_MODEL` 必须与 vLLM 的 `--served-model-name` 逐字相同。回退 Ollama：`http://127.0.0.1:11434/v1` / `qwen3.5:27b-q8_0` / `ollama`（§4.4） |
| `LLM_PROFILE` | auto | 适配档：`auto` 按地址 + 模型名选（qwen + vLLM → `qwen-vllm`：每请求 `enable_thinking=false`、思考不回灌、工具不加严格模式；qwen + Ollama 端口或带 `:` 标签 → `qwen-ollama`；qwen + OpenRouter → `qwen-openrouter`：`reasoning.enabled=false`、预算同 `qwen-vllm`、可用 `CLOUD_LLM_PROVIDER` 固定托管方；其余云端 → `generic`）；也可显式指定其一 |
| `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` / `LLM_REQUEST_TIMEOUT_S` | 档默认（8192 / 0.3 / vLLM 180 s · Ollama 300 s） | 单次请求参数；不设即取档默认 |
| `LLM_ALLOW_CLOUD` | false | 非本机地址一律拒绝启动，除非显式打开；打开后提示词里 VIN 自动换成 `V-xxxxxxxx` 假名 |
| `CLOUD_LLM_ENABLED/BASE_URL/MODEL/API_KEY` | 关 / OpenRouter / `deepseek/deepseek-v3.2` / 空 | 云端对照口，只由真跑脚本 `--cloud` 进入；`API_KEY` 为空时用手册摘要那把密钥（`STF_V3_OPENROUTER_API_KEY`，别名 `OPENROUTER_API_KEY`；§4.5） |
| `AGENT_WALL_CLOCK_S / AGENT_REQUEST_LIMIT / AGENT_TOOL_CALLS_LIMIT / AGENT_TOTAL_TOKENS_LIMIT` | 档默认：vLLM 900 / 60 / 100 / 1000000；Ollama 1200 / 80 / 120 / 600000；云端 900 / 80 / 120 / 600000 | 主 Agent 四道闸门；触发即以"部分报告"收尾；显式设置覆盖档默认 |
| `SUBAGENT_WALL_CLOCK_S / SUBAGENT_REQUEST_LIMIT` | 档默认：vLLM 180 / 12；Ollama、云端 240 / 12 | 子代理闸门；超预算返回带 `[delegation …]` 前缀的部分结果 |
| `TOOL_RESULT_MAX_TOKENS / COMPACT_THRESHOLD_TOKENS` | 2000 / 60000 | 单条工具结果截断；对话压缩阈值（中日韩字符按 1 token 估） |
| `MANUAL_IMAGES_ENABLED` | false | 手册图片是否进模型（本地模型确认支持图片前保持关） |
| `DEFAULT_LOCALE` | zh-TW | 未指定语言时的报告语言 |

### 3.3 出了问题看什么

- 容器日志里每个事件一行 `agent.event`（类型、序号、工具、耗时、长度，**不含正文与 VIN**），每次工具调用一行 `agent.tool`，每轮结束一行 `agent.run_done`（`stopped_reason`：complete / timeout / budget / cancelled / error）。
- `stopped_reason=error` 且 `error=` 以 `ModelHTTPError` / `ConnectError` 开头 → 模型服务问题：`bash infra/vllm_ctl.sh status`（§4.7）。
- 报告 `partial=true` 但 `stopped_reason=complete` → 报告落文前的残留检查命中：`limitations` 里有"tool-call markup reached the report unparsed"= vLLM 的工具调用解析器没认出模型输出（FM-41）；`filter_hits>0` = 剥掉了带标签的思考块（FM-1）。两者都说明档位或模型出了偏差，查 `session_start.profile`。
- 报告里出现 `NO_SOURCE` 的引用 = 正文提到但这一轮没读过的章节 / 没在日志里出现的故障码，是模型幻觉的标记，不是运行时错误。
- 同参数重复调用工具在事件里标 `repeated=true`（结果从记忆化缓存返回）；很多 repeated 通常意味着模型在打转，配合 `context_compact` 事件看上下文是否已压缩。

## 4. 模型服务（vLLM）

> **按需拉起，不常驻（2026-09-24 用户决定；开始稳定对外服务或硬件升级时再议，见开发计划 §4）。** 2026-09-23 vLLM 被停机、GPU 让给其他团队的训练（共享服务器）。现在的做法是评测 / 验证时才开（`vllm_ctl.sh start` + `wait`，冷启动约 10 分钟），用完 `vllm_ctl.sh stop`。vLLM 停着时部署核验第 9 项会 FAIL：部署时用 `LLM_CHECK=skip LLM_CHECK_REASON="vLLM 按需启动，当前停机" bash stf_v3/scripts/deploy_check.sh`。启动前先 `nvidia-smi` 确认两张卡空闲（共享服务器：其他团队的训练任务每卡约 21 GB，占着时 vLLM 起不来，也不要去抢）。不装开机自启单元。

V1/V2/V3 唯一的本地模型来源（设计文档 D7，PROD-09 D1）：`Qwen/Qwen3.6-27B-FP8` 由 vLLM 0.24 提供（按需拉起），两张卡并用，思考在服务端关闭。部署文件 `infra/docker-compose.vllm.yml`，**只通过** `infra/vllm_ctl.sh` 启停（项目名固定 `stf_llm` → 自己的 pod `pod_stf_llm`，不进 V1/V2 的 `pod_infra`、不进 V3 的 `pod_stf_v3`）。V3 只通过配置指向它（§3.2）。

### 4.1 启停与启动顺序

```
bash infra/vllm_ctl.sh start          # 起容器；冷启动约 10 分钟（2026-09-19 实测 599 s：权重来自本地卷 vllm_hf_cache，不联网）
bash infra/vllm_ctl.sh wait           # 轮询 /v1/models 直到列出模型（默认最多 1200 s），超时打印显卡占用 + 末 30 行日志
bash infra/vllm_ctl.sh status         # 容器状态 / 健康 / pod / 已服务模型 / 两卡显存
bash infra/vllm_ctl.sh stop           # 停容器，释放两张卡
bash infra/vllm_ctl.sh logs 200
bash infra/vllm_ctl.sh install-unit   # 开机自启单元 stf-llm.service —— 按需策略下【不安装】；只有决定常驻时才用
```

**服务器重启后的启动顺序**（FM-21；按需策略）：① `systemctl --user restart stf-v3-gpu-worker`；② V3 容器 `podman-compose -p stf_v3 … up -d stf-v3-api stf-v3-worker`；③ `LLM_CHECK=skip LLM_CHECK_REASON="vLLM 按需" bash stf_v3/scripts/deploy_check.sh`。vLLM **不随重启自起**：有诊断在等模型时由按需控制器自动拉起（§6.2），评测 / 验证时按上面的步骤手动拉起。V3 容器在 vLLM 停着时照常运行，诊断会在过程里显示"等待模型"。

**vLLM 不参与 V3 部署核验的"30 分钟内新建"检查**（FM-39）：V3 每次部署不需要重启 vLLM；核验第 9 项只看它在线、服务的是配置里的模型、能真的生成一句。

### 4.2 显存分配（D2）

两张 RTX 6000 Ada 各 46 GB；vLLM `--gpu-memory-utilization 0.80`（bake-off 用 0.90），每张卡留约 9 GB 给宿主机 GPU worker 的手册转换（MinerU，固定用第二张卡）。验收实测（T-11，2026-09-19）：vLLM 常驻（两卡各 36.8 GB）时用 GPU worker 同一个 MinerU 二进制在 GPU 1 转换库里最大的一本手册（34.6 MB，1736 页）并同时跑一次完整诊断：转换成功（2630 s），诊断 98 s 完成；GPU 1 峰值 **44.2 GB / 46 GB**（余量只剩 1.9 GB），GPU 0 不变。结论：D2 的 0.80 刚好够，**更大的手册可能装不下**——若某本手册转换在这个余量下失败（日志里 CUDA out of memory）：先 `vllm_ctl.sh stop` → 转完 → `start`（运维动作，不改配置），或把 `VLLM_GPU_MEMORY_UTILIZATION` 降到 0.75。

### 4.3 仓库配方 vs bake-off 配方（FM-10）

| 参数 | 2026-08-01 bake-off（PR #236） | 仓库 `docker-compose.vllm.yml` |
|---|---|---|
| 镜像 | vllm/vllm-openai:v0.24.0-ubuntu2404 | 同 |
| 模型 / 对外名 | Qwen/Qwen3.6-27B-FP8 | 同（`VLLM_MODEL` 可覆盖；必须与 `STF_V3_LLM_MODEL` 相同） |
| 张量并行 / 上下文 | 2 / 98304 | 同 |
| 显存占比 | 0.90 | **0.80**（D2） |
| 思考 | `--reasoning-parser qwen3` + 模板默认 `enable_thinking=false` | 同；V3 每次请求再显式带 `enable_thinking=false`（双保险） |
| 工具调用 | `--tool-call-parser qwen3_xml --enable-auto-tool-choice` | 同 |
| 前缀缓存 / 投机解码 | 开 / MTP 1 token | 同 |
| 权重来源 | HF 缓存卷 | 同 + `HF_HUB_OFFLINE=1`（不联网） |
| 端口 | 127.0.0.1:8010 | 同 |
| 健康检查 / 重启 | 无 | `/health`，起始宽限 900 s；`on-failure:10`（有上限，FM-37） |

### 4.4 回退到 Ollama（FM-9 / FM-38）与切回

回退只改配置、不改代码（qwen3.5 权重仍在 Ollama 卷里，`ollama list` 可见）：

```
bash infra/vllm_ctl.sh stop                         # 先释放显存：两者不能同时驻留（qwen3.5 常驻 57 GB）
# infra/.env 加三行：STF_V3_LLM_BASE_URL=http://127.0.0.1:11434/v1  STF_V3_LLM_MODEL=qwen3.5:27b-q8_0  STF_V3_LLM_API_KEY=ollama
cd infra && ~/.local/bin/podman-compose -p stf_v3 -f docker-compose.v3.yml -f docker-compose.v3.polyu.yml down && ~/.local/bin/podman-compose -p stf_v3 -f docker-compose.v3.yml -f docker-compose.v3.polyu.yml up -d stf-v3-api stf-v3-worker && cd ..
bash stf_v3/scripts/deploy_check.sh --max-age-min 5  # 第 9 项会对 Ollama 做同样的三步检查；档自动变为 qwen-ollama（预算回到 20 分钟）
```

**切回 vLLM**：`podman exec stf-ollama ollama stop qwen3.5:27b-q8_0`（卸载常驻模型；`ollama ps` 应为空）→ 删掉 `.env` 里那三行 → `vllm_ctl.sh start && vllm_ctl.sh wait` → V3 容器 `down` + `up` → `deploy_check.sh`。

### 4.5 云端对照（D3）

云端**只作对比**，永远不是产品路径：部署核验第 9 项在 `STF_V3_LLM_BASE_URL` 不是本机时直接 FAIL（FM-33）。进入云端只有一条路：`diagnose_once.py --cloud`，用 `STF_V3_CLOUD_LLM_*`（默认 OpenRouter + `deepseek/deepseek-v3.2`；换 `anthropic/claude-sonnet-4.6` 只改 `STF_V3_CLOUD_LLM_MODEL`），密钥为空时复用手册摘要那把 `STF_V3_OPENROUTER_API_KEY`（别名 `OPENROUTER_API_KEY`）。

**允许出境的字段**（FM-13，人工审过一份云端消息文件后固定）：车辆品牌与型号、**VIN 假名 `V-xxxxxxxx`**、车牌与昵称（车档标签，非身份）、日志的时间范围与格式、工具返回的**文本摘要**（信号统计 / 窗口抽样 / 故障码 / 手册章节文本）、系统与用户提示词。**不出境**：原始 VIN、原始日志文件、手册图片、密钥（事件 / 报告 / 消息三个落文件里也没有密钥，事件里只有 `model_source` 标签）。云端两轮的落文件留在服务器 `~/prod09_runs`（含日志摘要），不进 PR。

### 4.6 实测（PROD-09 验收，2026-09-19）

同一份 Hiace 日志（带 P00AF，4680 行）+ 同一道 golden 手册问题，2026-09-19：

| 路径 | 诊断耗时 | 请求 | 工具调用 | token | 报告 | golden 问答 |
|---|---|---|---|---|---|---|
| 本地 Qwen3.6-27B-FP8 / vLLM（qwen-vllm 档） | 97 s | 22 | 34 | 31.8 万 | 2273 字，1 引用，reasoning 事件 0 | 18 s / 8 工具（首跑以规划文字收尾 → 已加补问） |
| 云端 deepseek-v3.2（默认对照） | 66 s | 19 | 18 | 15.3 万 | 1788 字，1 引用 | 33 s / 9 工具 / 2 引用 |
| 云端 kimi-k2.5（D3 第二模型，claude-sonnet 地区受限） | 45 s | 5 | 10 | 4.3 万（另有 6.8k 字思考，未回灌） | 2060 字，1 引用 | 160 s / 8 工具 / 1 引用 |
| PROD-08 对照：Ollama qwen3.5（思考关不掉） | 309 s | 6 | 12 | 8.6 万 | 2962 字 | — |

冷启动 599 s；T-11 显存：vLLM 两卡各 36.8 GB，MinerU 转 1736 页手册时 GPU 1 峰值 44.2 GB（余量 1.9 GB）。回退演练（T-12）：停 vLLM → 测试容器只改三个环境变量指向 Ollama qwen3.5 → 一轮完整（57 s / 2 请求 / 3 工具，档自动为 qwen-ollama、墙钟回到 1200 s、thinking_chars 1286 但报告无标签）→ `ollama stop` 卸载（ollama ps 空）→ vLLM 重启 528 s 就绪 → 再跑一轮完整（34 s / 8 请求 / 11 工具，qwen-vllm 档，thinking 0）；全程零代码改动（git status 无修改文件）。

### 4.7 出了问题看什么（FM-4 / FM-22 / FM-35 / FM-37）

- **起不来 / `wait` 超时**：`vllm_ctl.sh status` 看两卡占用——若某张卡已被占 > 8 GB（Ollama 常驻模型？别的租户？）vLLM 分不到 0.80 就退出并按 `on-failure:10` 重试；`nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` 看谁占着；Ollama 的话 `podman exec stf-ollama ollama stop <模型>`。
- **健康是 healthy 但请求永不返回**（两卡通信死锁）：`deploy_check.sh` 第 9 项 30 s 内真生成一句，失败即报；`vllm_ctl.sh stop && start`。
- **404 model not found**：`STF_V3_LLM_MODEL` 与 vLLM `--served-model-name` 不一致；核验第 9 项会列出实际服务的名字。
- **报告里有思考文本 / 工具调用 XML**：`session_start.profile` 不是 `qwen-vllm`（模型名没对上 → 落到 generic）；显式设 `STF_V3_LLM_PROFILE=qwen-vllm`。
- **跳过第 9 项**（离线演练等）：`LLM_CHECK=skip LLM_CHECK_REASON="…" bash stf_v3/scripts/deploy_check.sh`——输出里会有一行 `SKIP  model service` 带理由，默认从不跳过（FM-20）。
- 重启计数：`podman inspect -f '{{.RestartCount}}' stf-vllm`；日志 `vllm_ctl.sh logs 200`。

## 5. Golden 评测（PROD-10）

同一把尺子：30 道 MWS-150-A 手册题 + 15 道 Yamaha 路试日志题，打分器与判卷（OpenRouter `z-ai/glm-5.1`）从 V2 逐字复制。门槛与基线在 `stf_v3/evals/thresholds.yaml`，成绩单在 `docs/evals/`。

### 5.1 什么时候必须跑

- PR 动了**受管路径**（`thresholds.yaml` 的 `managed_paths`：诊断运行时与工具、手册索引读取、日志读取、评测器与数据、settings、依赖锁、vLLM 部署文件）→ CI `eval-gate` 要求本 PR 新增一份**新鲜**且过线的成绩单：评测提交号在分支历史里，且其后没再改受管路径。
- 门槛：均值 ≥ 基线 − 该 lane 的容差（手册 0.03，OBD 0.06——15 题波动大，同一代码三遍相差 0.053），且基线 ≥ 0.6 的题不跌破 0.4；未过可重跑一次，最新两份里有一份过即可。
- 当前基线（2026-09-24，`thresholds.yaml`）：手册 0.878（验收线 0.831）、OBD 0.885（验收线 0.884：V3 首次实测，开发计划原写的 0.938 是旧模型 qwen3.5 在 V2 上的数字）。
- 只算：本地模型、思考关、用途 `gate` / `baseline`、完整、有效、同一判卷模型、生产预算（倍数 1）。云端 / 开思考 / 校准 / 演示成绩单永远不算。
- 看起来受管但其实无关的 PR：由**用户**加 `eval-exempt` 标签，CI 打印"已豁免"。

### 5.2 怎么跑

```
cd ~/stf_ai_diagnosis_platform_v1 && git checkout <branch> && git pull
nvidia-smi --query-gpu=index,memory.used --format=csv   # 两卡空着才启动（共享服务器）
bash infra/vllm_ctl.sh start && bash infra/vllm_ctl.sh wait   # 冷启动约 10 分钟
cd infra && GIT_COMMIT=$(git rev-parse HEAD) ~/.local/bin/podman-compose -p stf_v3 -f docker-compose.v3.yml -f docker-compose.v3.polyu.yml build && cd ..
bash stf_v3/scripts/run_golden_eval.sh --purpose gate            # 两条 lane，六路并发
tail -f ~/stf_v3_evals/<容器名>/run.log                           # 断开 SSH 不影响
cat ~/stf_v3_evals/<容器名>/exit_code                             # 0 有效 · 2 跑完但无效 · 4 拒跑 · 6 看门狗
bash infra/vllm_ctl.sh stop                                       # 评测完停机，把 GPU 还给别人
```

**显卡被占时的同模型预检**（PROD-10 D5 后续）：`--cloud --cloud-model qwen/qwen3.6-27b --cloud-provider DeepInfra --purpose comparison --lanes obd`——同一 Qwen3.6-27B（FP8 托管）经 OpenRouter，关思考、预算同本地；只作参考，成绩单记为云端，**永不计入门槛**，合并仍须本地门槛评测。

常用参数：`--lanes manual|obd`、`--ids lookup-001,cross-003`（按题号结尾匹配；两条 lane 同名时两边都跑）、`--thinking on --budget-scale 2`（开思考对照）、`--cloud`（deepseek 对照，不做预热、VIN 用假名）、`--purpose calibration --budget-scale 2`（预算校准）、`--wait`（等跑完再返回）。输出目录：`<base>.json`（完整）、`.slim.json`（精简，门槛 PR 入库用）、`.md`（摘要）、`.progress.jsonl`、`run.log`、`preflight.txt`。

前置检查会拒跑（退出码 4）：工作树有未提交改动；镜像提交号 ≠ HEAD（先重建镜像）；已有一轮在跑；某张卡 > 42 GB（Ollama / MinerU / 其他租户占着）；Ollama 驻留了模型；vLLM 正在处理请求；V3 与 V2 的手册副本哈希不同（打印 DIFFERENT）；容器内分词器、模型端点或判卷模型不可用。

入库：把输出目录里的 `.json` 或 `.slim.json` 与 `.md` 拷到本地 `docs/evals/` 提交。**基线与对照轮入库完整版，门槛 PR 入库精简版**（完整版约 1 MB）。

### 5.3 多久、多少钱

45 题六路并发实测约 15.5 分钟（开思考、预算 ×2 约 25 分钟；云端 deepseek 约 9 分钟）；vLLM 冷启动另加约 10 分钟。判卷一轮约 45 次调用、几美分；2026-09-23 ~ 24 九次评测（含云端对照）判卷 + 云端合计约 3.1 美元。评测期间 vLLM 被占满，线上诊断会变慢。判卷模型 glm-5.1 会先推理再作答，命令行已把判卷输出上限设为 8192（照抄 V2 的 2048 会让长题判卷返回空）；分词器 `cl100k_base` 已烘进镜像，拿不到时评测拒跑。

### 5.4 分数掉了先看哪

1. 摘要"失败的题"：判卷失败 → `podman run … python -m stf_v3.evals regrade --scorecard <x.json>` 只重判失败题（不重跑模型）；`run_error` = 子代理报错（端点 / 工具异常），先查 vLLM。
2. "耗时与预算"：被预算截断的题数突增 → 预算或 vLLM 抢占（`preflight.txt` 的前后抢占计数）。
3. "逐题"差值表：集中在一类题（依赖图 / 跨章节）还是全面下降；与 V2 参考比。
4. 配置快照：判卷模型、vLLM 版本、手册库哈希与基线不同 → CI 打印 WARN，基线可能过时（走 5.6）。

### 5.5 评测期间不要做

不启动 V1/V2（Ollama 与 vLLM 抢显存）；不上传手册（MinerU 与六路并发抢第二张卡）；不重启 vLLM。重新部署 V3（api / worker 停了再起）没关系，评测容器不受影响。

### 5.6 基线重置

模型、判卷、vLLM 版本或手册库本身换了，分数整体平移：开一个**只改** `thresholds.yaml` + 新增两份基线成绩单的 PR（`python -m stf_v3.evals accept --scorecards a.json b.json --lines manual_agent=0.831,obd_agent=0.884 --write-thresholds stf_v3/evals/thresholds.yaml`），由用户加 `baseline-reset` 标签。同一 PR 里再动受管代码 CI 会红。

## 6. 诊断任务与按需模型（PROD-11）

### 6.1 一次诊断怎么走

`POST /v3/vehicles/{id}/diagnose`（body `{obd_log_id}`）立刻回 202 + 会话编号；同一辆车已有排队中 / 进行中的诊断时回 200 + 原会话（`existing: true`）。容器 worker（队列 `default,diagnosis`，并发 2）**一次只跑一个诊断**（诊断任务共用锁 `diagnosis-model`），其余排队。过程看 `GET /v3/conversations/{id}/events`（默认 JSON 回放；`Accept: text/event-stream` 直播），报告看 `GET /v3/conversations/{id}/report`。

模型不在线时任务先等：每分钟写一条 `waiting` 事件，`reason` 说明在等什么（下表），自点击起最多 60 分钟（`STF_V3_DIAGNOSIS_MODEL_WAIT_S`），诊断自己的 15 分钟时限从模型就绪才开始算。

| `waiting.reason` | 意思 | 先看哪 |
|---|---|---|
| `queued` | 前面还有诊断 | `GET /v3/health` → `diagnosis` |
| `model_starting` | 控制器已拉起 vLLM，冷启动中（约 10–12 分钟） | `bash infra/vllm_ctl.sh status` |
| `gpu_busy` | 卡被其他团队（或我们的 Ollama）占着，不抢 | `nvidia-smi`；`/v3/health` → `model_service.blocked_reason` |
| `manual_converting` | 我们自己在转手册（MinerU 占第二张卡） | `/v3/manuals` 里 `converting` 的那本 |
| `model_cooldown` | 刚才拉起失败，冷却 15 分钟后重试 | `journalctl --user -u stf-v3-gpu-worker -n 100 \| grep llm.` |
| `controller_unresponsive` | 宿主机控制器 3 分钟没心跳 | `systemctl --user status stf-v3-gpu-worker` |

结束状态：`done`（`report.partial` 为真表示时限 / 用量上限 / 模型错误提前结束）、`cancelled`、`error`（`error_code`：`model_unavailable` 等满 60 分钟、`model_start_failed`、`model_stopped` 控制器拉起的 vLLM 被外部停掉、`diagnosis_interrupted` worker 中途没了、`vehicle_deleted`、`log_unavailable`、`queue_unavailable`、`run_failed` 零产出、`internal_error`）。诊断**不自动重跑**：让用户重新点。

### 6.2 按需模型控制器

跑在宿主机 GPU worker 的 `llm` 队列（该 worker 现在是 `-q gpu,llm --concurrency 2`；手册转换靠锁 `gpu-ingest` 仍一次一本）。每分钟一次，诊断等模型时再按需加一次。它只会执行 `infra/vllm_ctl.sh start|stop`：

- **拉起**：有未结束的诊断、vLLM 没在跑、不在冷却期、两张卡上没有别人的显存（其他用户的进程、查不到主人的进程、我们的 MinerU / Ollama 都算占用；按**每张卡合计**判断：除我们 vLLM 外的显存加起来 ≥ `STF_V3_LLM_GPU_FREE_MIB` = 2000 MiB 即算忙——别人多个小进程也算）。拉起时的显存快照写进 `model_service_state.gpu_snapshot` 和日志 `llm.start`。
- **不重复拉起**：加载中只等；超过 25 分钟没就绪或进程退出 → 标失败、停掉、冷却 15 分钟（`STF_V3_LLM_START_COOLDOWN_S`），等待中的诊断立即以 `model_start_failed` 结束。
- **被外部停掉不硬拉**：控制器拉起、已就绪的 vLLM 不是控制器停的却没了（有人手动 `stop`、崩溃）→ 标失败（`failure_reason` = stopped externally）+ 冷却 15 分钟，等待中的诊断以 `model_stopped` 结束，不会每分钟重新拉起跟人对着干。
- **独立 scope**：`start` 经 `systemd-run --user --scope` 执行，vLLM 的 conmon 落在自己的 `stf-llm-start-*.scope` 里，不在 GPU worker 服务的 cgroup 中——部署后重启宿主机 worker 不会连带杀掉 vLLM（`STF_V3_LLM_CTL_SCOPE=false` 关掉）。
- **自动停机**：只停**控制器自己拉起**的那次，且空闲满 30 分钟（`STF_V3_LLM_IDLE_STOP_S`）、没有未结束诊断、没有评测锁（`~/stf_v3_evals/.lock`）、vLLM 没有进行中的请求。**手动 `vllm_ctl.sh start` 拉起的不会被自动停**——用完自己 `stop`。
- 状态：`GET /v3/health` → `model_service`（`state`、`blocked_reason`、`started_by_us`、`controller_seen_s`、`idle_s`）。空闲超过 30 分钟仍 `ready` 且 `started_by_us: true` → 查 `journalctl` 里的 `llm.ctl`。
- 临时关掉自动拉起：`infra/.env` 加 `STF_V3_LLM_AUTOSTART=false`，重启宿主机 worker（诊断照样等，靠人工拉起）。

### 6.3 部署前检查（FM-34）

每次 `down`/`up` V3 容器或重启宿主机 worker **之前**：

```
bash stf_v3/scripts/predeploy_check.sh          # 有未结束诊断或手册转换中 → 拒绝（退出码 3）
ALLOW_INTERRUPT=1 bash stf_v3/scripts/predeploy_check.sh   # 确认要打断：未结束的诊断会以 diagnosis_interrupted 结束
```

宿主机 worker 的单元文件本 ticket 改过（队列与并发），部署后跑 `bash stf_v3/gpu_worker/install.sh`（重写单元 + reload）再 `systemctl --user restart stf-v3-gpu-worker`；之后每次部署照旧只需 restart。

### 6.4 卡住的会话

- 每 2 分钟的清扫任务会关掉：排队超过 5 分钟却没有任务编号的（`queue_unavailable`）、任务已结束或消失但会话没结束的（`diagnosis_interrupted`）。worker 心跳超过 2 分钟的诊断任务不重排，直接标失败并关掉会话。
- 判断"死没死"只看任务是否仍被活着的 worker 持有，不看跑了多久：等模型 60 分钟 + 诊断 15 分钟的正常会话不会被误关。
- `GET /v3/health` → `diagnosis.oldest_unfinished_s` 超过 80 分钟就有问题：`bash stf_v3/scripts/queue_ops.sh status` 看诊断任务状态。

### 6.5 回滚（FM-30）

回滚到 PROD-11 之前的版本前：① `predeploy_check.sh` 确认没有未结束诊断（或让它们结束）；② 清掉诊断队列里没开始的任务：`bash stf_v3/scripts/queue_ops.sh cancel <job_id>`（逐个）；③ 老代码不认识 `diagnosis` / `llm` 队列与新表，降级迁移 `alembic downgrade b2c3d4e5f6a7` 会**删除消息表的所有行**（新旧形状不兼容，按设计有损）；④ 宿主机 worker 按旧单元重装（`install.sh`）。

### 6.6 测试车队（D3：前端联调与 Swagger 评审）

真库里另建一个与真车队隔离的车队，邀请码写进只有自己可读的文件、不进聊天记录：

```
podman exec stf-v3-api python scripts/create_workshop.py --name "测试车队（前端联调）"     --manager-codes 1 --technician-codes 1 > ~/stf_v3_test_workshop_codes.txt && chmod 600 ~/stf_v3_test_workshop_codes.txt
```

学生注册后核对他只看得到测试车队（VIN 只显示后 4 位）：`podman exec stf-v3-api python scripts/visible_vehicles.py --username <学生的用户名>`。测试车用假 VIN（如 `1HGCM82633A123456`），日志用仓库里的 Yamaha 路试日志 `stf_v3/evals/fixtures/yamaha_road_test.csv`。
