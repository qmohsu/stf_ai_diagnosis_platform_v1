# V3 运维手册

| 文档控制 | |
|---|---|
| 版本 | v0.3（PROD-08：诊断运行时一章） |
| 日期 | 2026-09-17 |
| 作者 | Xiangzhu Yan |
| 适用 | PolyU 服务器 `ssh polyu-gpu`，仓库 `~/stf_ai_diagnosis_platform_v1`，V3 容器 `stf-v3-api` / `stf-v3-worker`，宿主机服务 `stf-v3-gpu-worker` |

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
  --vehicle-id <车档 UUID> --log-id <日志 UUID> --out-dir /tmp/runs [--locale zh-TW] [--wall-clock-s 2400]
podman cp stf-v3-api:/tmp/runs ~/prod08_runs/     # 报告 .report.md / .report.json / 事件 .events.jsonl / 消息 .messages.json
```

- 脚本先预检：模型端点 `GET /models` 必须列出配置的模型名，再做一次预热请求（冷加载在这里发生，不在诊断里）；预检失败退出码 4，跑完退出码 0，被闸门截断 2，模型错误 3，超过脚本总时限 5。
- 事件逐条打印：`tool_call / tool_result` 带工具名、耗时、结果长度；`(in <id>)` 表示子代理内部的事件；`reasoning` 是模型思考（qwen3.5 在 Ollama 上关不掉，PROD-09 处理）。
- 输出目录永远不能是日志卷或手册卷（脚本会拒绝）；文件名只含时间与日志编号前 8 位，不含 VIN；报告正文可能含 VIN，**不要把报告文件拷出服务器贴进 PR / issue**。
- 用 qwen3.5 27B 一轮约 10–40 分钟；默认墙钟 20 分钟（`STF_V3_AGENT_WALL_CLOCK_S`），验证时可放宽到 40 分钟。

### 3.2 配置项（全在 `infra/.env`，前缀 `STF_V3_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | `http://127.0.0.1:11434/v1` / `qwen3.5:27b-q8_0` / `ollama` | 唯一模型来源（OpenAI 兼容口）；PROD-09 改指 vLLM |
| `LLM_ALLOW_CLOUD` | false | 非本机地址一律拒绝启动，除非显式打开；打开后提示词里 VIN 自动换成 `V-xxxxxxxx` 假名 |
| `CLOUD_LLM_ENABLED/BASE_URL/MODEL/API_KEY` | 关 | 云端对照口（PROD-09 接） |
| `AGENT_WALL_CLOCK_S / AGENT_REQUEST_LIMIT / AGENT_TOOL_CALLS_LIMIT / AGENT_TOTAL_TOKENS_LIMIT` | 1200 / 80 / 120 / 600000 | 主 Agent 四道闸门；触发即以"部分报告"收尾 |
| `SUBAGENT_WALL_CLOCK_S / SUBAGENT_REQUEST_LIMIT` | 240 / 12 | 子代理闸门；超预算返回带 `[delegation …]` 前缀的部分结果 |
| `TOOL_RESULT_MAX_TOKENS / COMPACT_THRESHOLD_TOKENS` | 2000 / 60000 | 单条工具结果截断；对话压缩阈值（中日韩字符按 1 token 估） |
| `MANUAL_IMAGES_ENABLED` | false | 手册图片是否进模型（本地模型确认支持图片前保持关） |
| `DEFAULT_LOCALE` | zh-TW | 未指定语言时的报告语言 |

### 3.3 出了问题看什么

- 容器日志里每个事件一行 `agent.event`（类型、序号、工具、耗时、长度，**不含正文与 VIN**），每次工具调用一行 `agent.tool`，每轮结束一行 `agent.run_done`（`stopped_reason`：complete / timeout / budget / cancelled / error）。
- `stopped_reason=error` 且 `error=` 以 `ModelHTTPError` / `ConnectError` 开头 → 模型服务问题：`curl -s http://127.0.0.1:11434/v1/models`、`podman exec stf-ollama ollama ps`。
- 报告里出现 `NO_SOURCE` 的引用 = 正文提到但这一轮没读过的章节 / 没在日志里出现的故障码，是模型幻觉的标记，不是运行时错误。
- 同参数重复调用工具在事件里标 `repeated=true`（结果从记忆化缓存返回）；很多 repeated 通常意味着模型在打转，配合 `context_compact` 事件看上下文是否已压缩。

