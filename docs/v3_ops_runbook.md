# V3 运维手册

| 文档控制 | |
|---|---|
| 版本 | v0.1（PROD-06：队列一章） |
| 日期 | 2026-09-14 |
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
