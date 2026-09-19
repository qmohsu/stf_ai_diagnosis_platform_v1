# V3 运维手册

| 文档控制 | |
|---|---|
| 版本 | v0.4（PROD-09：模型服务 vLLM 一章；§3 改为 vLLM 默认） |
| 日期 | 2026-09-19 |
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
| `LLM_PROFILE` | auto | 适配档：`auto` 按地址 + 模型名选（qwen + vLLM → `qwen-vllm`：每请求 `enable_thinking=false`、思考不回灌、工具不加严格模式；qwen + Ollama 端口或带 `:` 标签 → `qwen-ollama`；其余 / 云端 → `generic`）；也可显式指定三者之一 |
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

V1/V2/V3 唯一的本地模型来源（设计文档 D7，PROD-09 D1）：`Qwen/Qwen3.6-27B-FP8` 由 vLLM 0.24 常驻，两张卡并用，思考在服务端关闭。部署文件 `infra/docker-compose.vllm.yml`，**只通过** `infra/vllm_ctl.sh` 启停（项目名固定 `stf_llm` → 自己的 pod `pod_stf_llm`，不进 V1/V2 的 `pod_infra`、不进 V3 的 `pod_stf_v3`）。V3 只通过配置指向它（§3.2）。

### 4.1 启停与启动顺序

```
bash infra/vllm_ctl.sh start          # 起容器；冷启动约 10 分钟（2026-09-19 实测 599 s：权重来自本地卷 vllm_hf_cache，不联网）
bash infra/vllm_ctl.sh wait           # 轮询 /v1/models 直到列出模型（默认最多 1200 s），超时打印显卡占用 + 末 30 行日志
bash infra/vllm_ctl.sh status         # 容器状态 / 健康 / pod / 已服务模型 / 两卡显存
bash infra/vllm_ctl.sh stop           # 停容器，释放两张卡
bash infra/vllm_ctl.sh logs 200
bash infra/vllm_ctl.sh install-unit   # 装用户级 systemd 单元 stf-llm.service：重启机器后自动 start（已 enable-linger）
```

**服务器重启后的启动顺序**（FM-21）：① `vllm_ctl.sh start` → `wait`（或 `stf-llm.service` 自动起）；② `systemctl --user restart stf-v3-gpu-worker`；③ V3 容器 `podman-compose -p stf_v3 … up -d stf-v3-api stf-v3-worker`；④ `bash stf_v3/scripts/deploy_check.sh`。V3 容器比 vLLM 早起也不会坏——真跑脚本与（PROD-11 的）任务预检会等模型就绪，只是那约 10 分钟内的诊断请求会等或失败。

**vLLM 不参与 V3 部署核验的"30 分钟内新建"检查**（FM-39）：V3 每次部署不需要重启 vLLM；核验第 9 项只看它在线、服务的是配置里的模型、能真的生成一句。

### 4.2 显存分配（D2）

两张 RTX 6000 Ada 各 46 GB；vLLM `--gpu-memory-utilization 0.80`（bake-off 用 0.90），每张卡留约 9 GB 给宿主机 GPU worker 的手册转换（MinerU，固定用第二张卡）。验收实测（T-11）：vLLM 常驻时重新入库库里最大的一本手册并同时跑一次诊断——结果见 §4.6。若某本手册转换在这个余量下失败：先 `vllm_ctl.sh stop` → 转完 → `start`（运维动作，不改配置）。

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

（服务器验证后填：本地 Qwen3.6 / 云端 deepseek / 云端 claude-sonnet 三轮的耗时、请求数、工具调用数、token、费用；T-11 显存峰值；冷启动耗时。）

### 4.7 出了问题看什么（FM-4 / FM-22 / FM-35 / FM-37）

- **起不来 / `wait` 超时**：`vllm_ctl.sh status` 看两卡占用——若某张卡已被占 > 8 GB（Ollama 常驻模型？别的租户？）vLLM 分不到 0.80 就退出并按 `on-failure:10` 重试；`nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` 看谁占着；Ollama 的话 `podman exec stf-ollama ollama stop <模型>`。
- **健康是 healthy 但请求永不返回**（两卡通信死锁）：`deploy_check.sh` 第 9 项 30 s 内真生成一句，失败即报；`vllm_ctl.sh stop && start`。
- **404 model not found**：`STF_V3_LLM_MODEL` 与 vLLM `--served-model-name` 不一致；核验第 9 项会列出实际服务的名字。
- **报告里有思考文本 / 工具调用 XML**：`session_start.profile` 不是 `qwen-vllm`（模型名没对上 → 落到 generic）；显式设 `STF_V3_LLM_PROFILE=qwen-vllm`。
- **跳过第 9 项**（离线演练等）：`LLM_CHECK=skip LLM_CHECK_REASON="…" bash stf_v3/scripts/deploy_check.sh`——输出里会有一行 `SKIP  model service` 带理由，默认从不跳过（FM-20）。
- 重启计数：`podman inspect -f '{{.RestartCount}}' stf-vllm`；日志 `vllm_ctl.sh logs 200`。
