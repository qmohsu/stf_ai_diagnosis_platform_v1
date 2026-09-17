# V3 设备接入手册 — Jetson 上传器"双推"

> 给装设备的人（目前是 Perry）照着做。做完后，每趟行程结束的日志会
> **同时**送到旧系统（和今天一样）和新系统 V3（记在这台车的车档下）。
> 旧系统那条路一行不改；V3 那条路只靠一个配置文件开关。
>
> 对应 ticket：PROD-07（`docs/v3_dev_plan.md`）。脚本：`obd_agent/jetson_uploader.py`。
> 作者：Xiangzhu Yan · 2026-09-17

---

## 0. 开始前先回答这 5 个问题（发给我们，手册按你的答案定稿）

1. 设备上 `python3 --version` 输出什么？（脚本要求 ≥ 3.8，和今天一样只用 `httpx`）
2. 今天的上传脚本放在哪个目录、用哪个用户运行？
3. 今天是怎么触发上传的：行程结束自动跑、cron、还是手动敲命令？
4. 设备上能不能用 `crontab -e`？有没有 systemd（`systemctl --user`）？
5. 设备上网走什么：4G 模块、Wi-Fi、还是拉线？断网常不常见？

你的环境和本手册假设不同时，**先联系我们再改**，不要自行改脚本。

---

## 1. 第一步：设备能不能连到 V3

V3 和旧系统在同一台服务器、同一个域名、同一张证书后面。旧系统能通，V3 就能通。先在设备上确认：

```bash
curl -sS https://stf-diagnosis.dev/v3/health
```

看到一行 JSON（含 `"status"`）即可。如果这里报证书或连接错误，停下来把报错发给我们（FM-14）。

---

## 2. 在 V3 建车档、发 token（manager 做，不是装机的人）

第一个车队、manager 账号、两台车（Hiace、Corolla）的车档和 token 由我们用
`stf_v3/scripts/onboard_first_workshop.py` 在服务器上建好（VIN 只进数据库，不进任何文件）。
你会收到：

- **一个技师邀请码**：用它在 V3 注册你自己的账号（注册后能看车、看日志）。
- **每台车一个配置文件**：`v3_uploader_toyota_hiace.env`、`v3_uploader_toyota_corolla.env`。
  文件名写明是哪台车，**不要互换**（FM-4：Corolla 的日志没有 VIN，装错车服务器拦不住）。

以后再加车：manager 在 V3 里建车档 → 给车档发一个设备凭证（token 只显示一次）→ 按第 3 节写配置文件。

---

## 3. 配置文件写到设备上（每台车一个）

把对应这台车的文件放到设备上的固定位置，并把权限收紧：

```bash
mkdir -p ~/.config/stf
cp v3_uploader_toyota_hiace.env ~/.config/stf/v3_uploader.env
chmod 600 ~/.config/stf/v3_uploader.env
```

文件内容长这样（token 是示例，**真 token 只在你收到的文件里**）：

```
STF_V3_BASE_URL=https://stf-diagnosis.dev
STF_V3_DEVICE_TOKEN=<示例，替换为收到的文件里的值>
STF_V3_VEHICLE_ID=<这台车的车档编号，文件里已填好>
# 可选：待传目录，默认在配置文件旁边的 spool/
# STF_V3_SPOOL_DIR=/var/lib/stf/spool
```

规则（FM-22）：

- token **只**存在这个文件里；不要写进命令行、不要贴进聊天、不要写进别的脚本。
- 文件权限必须是 `600`（脚本发现太宽会打警告）。
- 怀疑泄露 → 立刻告诉我们，我们吊销并重发；旧 token 立即失效。

想放到别的路径也可以：运行脚本时加 `--v3-env-file /那个/路径`，或设环境变量 `STF_V3_ENV_FILE`。

---

## 4. 更新脚本并自检

1. 用仓库里最新的 `obd_agent/jetson_uploader.py` 覆盖设备上的同名文件（**只有这一个文件**；依赖没有变，仍然只需要 `httpx`）。
2. 自检（不会上传任何东西）：

```bash
python3 -m obd_agent.jetson_uploader --self-check
```

全部 `OK` 才继续。`FAIL` 的那一行说明了原因（配置文件不存在 / 缺 token / 待传目录不可写 / 连不上 V3）。

自检验证不了 token 本身对不对——那要靠第 5 节真的发一份日志。

---

## 5. 验证：发一份旧日志

拿一份**这台车**以前的行程日志（Hiace 请用带 VIN 的那种），像今天一样跑一次：

```bash
python3 -m obd_agent.jetson_uploader \
    --base-url https://stf-diagnosis.dev \
    --username <你的旧系统账号> --password '<旧系统密码>' \
    --manufacturer Toyota --model Hiace \
    --log-file /path/to/an/old/trip.tsv
```

预期：

- 第一行日志：`v3: enabled base_url=https://stf-diagnosis.dev expected_vehicle=... spool=...`
  （如果写的是 `v3: disabled (env file not found ...)`，说明配置文件路径不对，见第 3 节）。
- 旧系统照常返回一个 `session_id`（打印在最后一行）。
- 最后有一行 `v3_result: stored ...`（第一次）或 `v3_result: duplicate ...`（同一份文件再发）。
- 退出码 `0`。

然后在 V3 里（用你的技师账号）打开这台车的车档：日志列表里应出现这份文件，来源 `device`。

发错车（比如把 Hiace 的日志用 Corolla 的配置发）会得到 `v3_result: rejected status=422 ... vin_mismatch`，退出码 2，文件副本在 `spool/rejected/`——这是预期的拦截，不是故障。

---

## 6. 定时补传（cron 首选）+ 重启后确认

行程结束时 V3 不通（4G 断了），文件会自动进待传目录 `spool/pending/`，**下一次行程结束时先补传积压的再传新的**。
如果希望不等下一趟就补，挂一个每 10 分钟跑一次的"只补传"命令：

```bash
crontab -e
# 加一行（路径按你的环境改）：
*/10 * * * * cd /path/to/repo && /usr/bin/python3 -m obd_agent.jetson_uploader --drain >> ~/.config/stf/cron.log 2>&1
```

`--drain` 只推 V3 的积压，不碰旧系统，不需要 `--log-file`。连续失败时它会自动退避（10 → 20 → 40 → 60 分钟），不会一直烧流量（FM-11）。
两个实例撞在一起也没关系：后来的那个看到锁就退出（FM-9）。

用 systemd 也行（`systemctl --user` 需要先 `loginctl enable-linger $USER`，否则重启后不跑）。cron 更省事。

### 重启后确认定时器还在（FM-20）

设备断电重启后跑一次：

```bash
crontab -l | grep jetson_uploader        # 应看到上面那一行
tail -3 ~/.config/stf/cron.log           # 最近一次 --drain 的输出
```

---

## 7. 退出码（写包装脚本 / 报警时用）

| 退出码 | 含义 | 该做什么 |
|---|---|---|
| `0` | 旧系统成功；V3 已入库 / 已存在 / 已进待传目录 / 未启用 | 不用管（待传的会自动补） |
| `1` | **旧系统失败**（登录、上传或网络） | 和今天一样处理；V3 那条路的结果在日志里另有一行 |
| `2` | 旧系统成功，但 V3 **拒收**（文件问题）、**token 无效 / 被吊销**、**车档不符**或**待传目录已满** | 看 `spool/uploader.log` 最后几行；token 问题联系我们 |

`--drain` 模式：`0` = 正常（包括"网络不通、下次再试"），`2` = token 无效（联系我们）。

---

## 8. 回滚（想让一切回到今天的样子）

```bash
mv ~/.config/stf/v3_uploader.env ~/.config/stf/v3_uploader.env.off
```

没有这个文件，脚本就是今天的脚本：只推旧系统，一行 V3 代码都不会跑（日志第一行写 `v3: disabled`）。
待传目录里的文件会原地保留，恢复配置后继续补传。脚本本身不用换回旧版本（FM-16）。

---

## 9. 换车 / 换设备（FM-5）

设备挪到另一台车之前：**先告诉我们吊销这台车的 token**，再拿新车的配置文件装上（第 3 节）。
Corolla 这种日志里没有 VIN 的车，服务器完全靠 token 判断是哪台车——配置不换，数据就会一直记在旧车名下，谁都发现不了。

token 泄露同样先吊销、再重发。

---

## 10. 每周检查（两分钟）

1. `ls ~/.config/stf/spool/rejected/` —— 应为空。有文件说明服务器拒收过（同目录 `<文件名>.error.txt` 写着原因），把文件名和原因发给我们；**不要删**（FM-3）。
2. `ls ~/.config/stf/spool/pending/ | wc -l` —— 应为 0 或很小。持续增长说明 V3 一直没通（第 1 节重查）。
3. 在 V3 车档页看设备的**最近活跃**时间：超过 7 天没动要查（FM-7）。
   注意：**最近活跃只证明 token 被用过，不证明日志入库了**（被拒收也会刷新）；要看"日志列表"才算数（FM-29）。

---

## 11. 我们要日志时你怎么取（FM-18）

脚本每次运行都往 `~/.config/stf/spool/uploader.log` 追加几行（自动滚动，最多 4 MB）。我们需要时：

```bash
tail -50 ~/.config/stf/spool/uploader.log
ls -la ~/.config/stf/spool/pending ~/.config/stf/spool/rejected
```

把输出发给我们即可。里面**没有 token**（脚本从不打印它）；VIN 可能出现在拒收原因里，只在设备上，不要贴到公开的地方。

---

## 12. 旧系统退役时（FM-24）

旧系统下线那天：从命令 / 包装脚本里删掉 `--base-url/--username/--password`（旧系统凭证），并从设备上删掉旧系统的账号密码。V3 那条路和配置文件不变。

---

## 13. 排障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `v3: disabled (env file not found ...)` | 配置文件不在默认路径 | 第 3 节；或加 `--v3-env-file` |
| `v3: enabled but unusable: ... required` | 配置文件缺 `STF_V3_BASE_URL` 或 token | 用收到的文件覆盖 |
| `v3_result: config_error status=401` | token 无效或已吊销 | 文件留在待传目录；联系我们重发 token |
| `v3_result: rejected status=422 ... vin_mismatch` | 用了另一台车的配置 | 检查配置文件是哪台车的 |
| `v3_result: rejected status=422 ... unsupported_format` | 文件不是 Jetson TSV / Yamaha CSV | 把文件发给我们看 |
| `v3_result: rejected status=413` | 文件超过 50 MB | 联系我们 |
| `v3_result: vehicle_mismatch` | 服务器把日志记到了别的车档 | 立刻联系我们（配置里的车档编号和 token 不是同一台车） |
| `v3_spool_full` | 待传目录超过 500 MB / 200 个文件 | V3 长期不通，先查第 1 节 |
| `v3_locked: another uploader is running` | 上一次还没跑完（大文件 / 补传中） | 正常，会自动排到下一次 |
