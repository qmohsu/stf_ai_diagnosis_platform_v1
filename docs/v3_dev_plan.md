# V3 开发计划 — 产品化底座（v1.0 定稿）

**Stage 1 一键诊断报告 + 面向车队监控/AI 诊断的可扩展底座**

| 字段 | 值 |
|---|---|
| **架构文档** | `docs/v3_design_doc.md`（v1.2） |
| **架构图** | `docs/diagrams/stf_v3_final_architecture.excalidraw`（图文强制同步；预览由 `diagrams/render_excalidraw.py` 生成） |
| **决策存档** | `.lavish/v3_dev_plan_decisions.html`（D1–D9，2026-09-08，本地不提交） |
| **Ticket 前缀** | `PROD-XX` |
| **版本** | v1.2（PROD-02 进行中；Stage 1 去掉 pgvector / rag_chunks） |
| **作者** | Xiangzhu Yan |
| **最后更新** | 2026-09-11 |

## 0. 本计划的定位

V3 是最终产品形态，V1/V2 是学习版并将废弃。本计划要交付两样东西：

1. **Stage 1 闭环**：注册 → 加车 → 上传 → 一键诊断报告 → 历史，真实 pilot 用户可用。
2. **底座**：Stage 1 之后的每一项功能（报告内追问、车队病历接入、Dashboard、主动触发、相似案例）都只需"加模块 / 加表"，不需要回头改所有权、身份标识或拆分单体。§4 列出这些后续功能作为底座的验证用例。

判断"底座撑不撑得住"的方法：把 §4 的每个后续功能走一遍架构，凡是要改所有权语义、改车辆身份标识、拆单体的，就是地基裂缝，必须在 PROD-01 修掉；凡是加表、加模块的，地基成立。

### 0.1 决策板结果（2026-09-08，D1–D9，详见设计文档 v1.2）

| # | 决策 | 结果 |
|---|---|---|
| D1 | 一台车归谁管 | **挂 workshop**：`workshops` + `memberships`，成员看本 workshop 全部车；车主不建模 |
| D2 | 怎么认出一台车 | **VIN = 身份**（必填、workshop 内唯一）；**车牌 = 可选标签**（只显示、可改、无约束）；品牌型号必填 |
| D3 | 代码放哪 | **同仓新目录** `stf_v3/` + `obd-ui-v3/`，硬约束 A 可迁出 / B 不绑死（见 §2.3） |
| D4 | 登录注册 | **fastapi-users** + 邀请码 |
| D5 | 任务队列 | **procrastinate**（Postgres 上的任务队列，零新组件），模块 `jobs` |
| D6 | 前端开工时机 | **暂缓（外部阻塞）**：PM 已分配给学生；M5 不排期；后端从 M1 起交付 OpenAPI 契约 |
| D7 | 本地模型来源 | **唯一一个 vLLM**，V1/V2/V3 共用；换模型统一换、两套 golden 各跑一遍 |
| D8 | Jetson 数据归属 | **要求级**：每笔上传 100% 绑定唯一车档；转存不丢归属；诊断时品牌型号找手册、VIN 找历史。实现细节由 PROD-01 定 |
| D9 | 不做清单 | **接受**，且每项须留档"回头条件"（§4） |

已由 AI 自行决定的小项（可推翻）：数据库用同一 Postgres 实例新建 database `stf_v3`；eval runner 与工具同策略复制进 V3；成员角色初版只有 `manager` / `technician`；Jetson 过渡期同时推 V2 与 V3（配置级改动，V1/V2 退役删一行）。

## 1. 范围边界

### 1.1 In Scope（Stage 1）

- 独立新库 `stf_v3`（PostgreSQL + pgvector）+ Alembic 初始迁移（含 procrastinate 任务表）
- FastAPI 后端，**模块化单体**：`auth / workshops / vehicles / ingest / diagnosis / knowledge / jobs`，每个模块拥有自己的表、路由、服务接口；模块间只经服务接口调用
- fastapi-users + 邀请码注册 + JWT；workshop 成员隔离，鉴权唯一入口 `can_access_vehicle()`
- 车档：VIN 身份、车牌标签、品牌型号必填
- 上传薄层：Jetson 原生 TSV + Yamaha 双通道 CSV，sha256 去重，其余 422；只入库不诊断；**每笔绑定车档**
- 知识库：`manuals` 元数据 + Markdown/图片文件从 V2 一次性拷贝；**无 pgvector、无 rag_chunks、无向量化**（2026-09-11 决定）；手册转换走 `jobs`（宿主机 GPU worker 消费，替代共享卷文件协议）
- Agent 核心：Pydantic AI 运行时，V2 工具组复制进 V3，子代理 = agent-as-tool，vLLM/qwen 适配层，云端接口仅供对比
- 诊断编排：诊断作为 job 独立于 HTTP 连接运行；SSE 订阅进度；messages / audit_events / reports 回写；断线回放
- **OpenAPI 契约**：M1 起每个里程碑交付（自动生成 + 人工注释），供任何一方做前端
- Golden 评测门槛移植到 V3（manual lane 0.831 / OBD 0.938 基线）
- 与 V1/V2 在 PolyU 服务器并存部署（独立 Compose 文件、独立端口、nginx 前缀），共用 Postgres 实例 / vLLM / nginx
- Pilot 运维底线：备份与恢复演练、audit 保留策略（jobs 定时）、结构化日志、运维手册、队列运维演练

### 1.2 Out of Scope

见设计文档 §1.11 非目标清单（A3）。每项的回头条件在本计划 §4。前端实现（`obd-ui-v3`）归属未定，其 ticket（PROD-12 ~ 14）保留但不排期。

## 2. 里程碑与关键路径

### 2.1 里程碑总览

| 里程碑 | 目标 | Ticket | 粗估 | 状态 |
|---|---|---|---|---|
| **M0 地基决策 + 代码设计** | 设计文档变成可直接编码的蓝图 | PROD-01 | 2–3 天 | ✅ 完成（2026-09-11） |
| **M1 行走骨架** | 空壳系统在服务器上跑起来，OpenAPI 契约 v1 | PROD-02 ~ 04 | 4–5 天 | |
| **M2 数据入口** | 数据能进来、知识库能用、队列上线 | PROD-05 ~ 07 | 3–4 天 | |
| **M3 Agent 核心** | 诊断脑子在新运行时里不掉分 | PROD-08 ~ 10 | 6–8 天 | |
| **M4 S1 诊断闭环** | 一键报告在后端跑通，Swagger 可走完全流程 | PROD-11 | 3–4 天 | |
| **M5 前端 Stage 1** | 用户能在手机上用 | PROD-12 ~ 14 | 6–8 天 | **待定 · 外部依赖（D6）** |
| **M6 Pilot 上线** | 真实用户开始用 | PROD-15 | 2–3 天 | 依赖 M5 归属 |

粗估为 AI 主力开发 + 用户评审的工作日，不含等待评审与服务器排队。

### 2.2 关键路径

```
M0 PROD-01 (代码设计蓝图)
    │
    ▼
M1 PROD-02 (新库 + 初始迁移)
    │
    ▼
M1 PROD-03 (后端骨架 + Auth + workshop/车辆)  ── 交付 OpenAPI 契约 v1
    │
    ├────────────────────┬────────────────────┐
    ▼                    ▼                    ▼
M1 PROD-04           M2 PROD-05           M2 PROD-06
(并存部署 + CI       (上传薄层 + 车档绑定)  (知识库拷贝 + jobs 队列
 + 可迁出/不绑死检查)     │                    + GPU worker)
    │                    ▼                    │
    │                M2 PROD-07 (Jetson 接入)  │
    │                    │                    │
    └──────────┬─────────┴────────────────────┘
               ▼
M3 PROD-08 (Pydantic AI 运行时 + 工具组)
    │
    ├────────────────────┐
    ▼                    ▼
M3 PROD-09           M3 PROD-10
(模型适配 + 云端)    (Golden 评测移植)
    │                    │
    └──────────┬─────────┘
               ▼
M4 PROD-11 (诊断 job + SSE + 回写 + 回放)  ── OpenAPI 契约 v2 (Stage 1 全部端点)
    │
    ▼
M5 PROD-12 ~ 14 (前端, 待定 · 外部)
    │
    ▼
M6 PROD-15 (Pilot 上线)
```

### 2.3 Definition of Done（每张 ticket 通用）

一张 ticket 只有满足以下全部条件才算 DONE：

- 代码合并，测试通过（离线单测用 Pydantic AI TestModel，不打真实 LLM）
- **Golden 评测不掉分**（PROD-10 之后对每次改动生效）
- `docs/v3_design_doc.md` + 本计划 + 架构图在**同一提交**内同步（预览 SVG 用 `render_excalidraw.py` 重新生成）
- 隐私边界保持：工具只返回文本摘要，不返回原始传感器数组
- **数据归属不变量（D8）**：任何一笔入库的原始数据都能查到所属车档；不允许 `V-UNKNOWN`
- **可迁出 / 不绑死（D3）**：`stf_v3/`、`obd-ui-v3/` 不 import 老代码；两条自动化检查（拷到空目录测试全绿；删掉 `diagnostic_api/ obd_agent/ obd-ui/` 后 V3 照常构建）通过
- OpenAPI 契约与代码一致（CI 生成并 diff）
- 走 CLAUDE.md 验证闭环：PR → 分支部署 PolyU → 线上 E2E（后端 ticket 用 Swagger / curl，前端 ticket 用浏览器）→ 用户拍板合并
- V1/V2 在服务器上继续正常运行（并存不互扰）

## 3. Tickets

### 3.1 M0 — 地基决策 + 代码设计

#### PROD-01 — 代码设计蓝图

Owner: AI Application Engineer
Depends on: 本计划 v1.0（已定稿）
Status: **✅ DONE**（决策 2026-09-08；蓝图 v1.0 经 Lavish 评审通过 2026-09-11，
`docs/plans/2026-09-08-v3-code-design.md`。评审确认两处产品默认：技师可做全部日常
操作、manager 独占删车/发码/设备/手册；登录用用户名、忘记密码由 manager 重发码）

**目标**：把设计文档 v1.2 变成可以直接编码的蓝图，并把 D8 等"要求级"决策落成具体机制。

**实现方法**：

1. 产出代码设计文档 `docs/plans/2026-09-XX-v3-code-design.md`：
   - 目录布局：`stf_v3/`（`app/{auth,workshops,vehicles,ingest,diagnosis,knowledge,jobs}`、`alembic/`、`tests/`、`evals/`、`Dockerfile`、`pyproject.toml`）与 `obd-ui-v3/`；`infra/docker-compose.v3.yml`
   - 模块图：各模块的表、路由前缀、服务接口签名、允许的依赖方向（`diagnosis → knowledge / vehicles / ingest / jobs`，反向禁止）
   - API 契约 v1（OpenAPI 草案）：Stage 1 全部端点 + SSE 事件类型清单（与 audit_events.event_type 同名）
   - DDL：初始迁移全部表与约束（含 D1/D2 修订：workshops、memberships、vehicles.vin/plate）
   - **D8 机制**：设备如何表明"我是哪台车"（选定方案 + 理由，写给自己看，不再上决策板）、日志内 VIN 与车档核对规则、过渡期双推
   - jobs 设计：任务清单（手册入库 / 诊断 / 定时）、队列划分（GPU 队列并发 1）、重试与超时策略、宿主机 worker 连库方式
   - 可迁出 / 不绑死两条自动化检查脚本的设计
2. "底座验证"表：对 §4 每一项写一行"将来动哪张表 / 哪个模块"，证明不动地基。

**交付物**：

- `docs/plans/2026-09-XX-v3-code-design.md`（经 Lavish 评审）
- 本计划 v1.1（PROD-02 ~ 04 细化到 ticket 级）

**验收标准**：

- 代码设计文档经用户 Lavish 评审通过
- §4 每一项在"底座验证"表中都只涉及"加表 / 加模块 / 加列"，无一项需要改所有权或身份标识
- 模块依赖方向无环
- D8 的三条要求各自对应到具体的表 / 字段 / 校验

---

### 3.2 M1 — 行走骨架

#### PROD-02 — 新库与初始 Alembic 迁移

Status: **✅ DONE**（2026-09-11，分支 `prod-02-db-migration`；服务器上 `stf_v3` 库已建并升级到 `a1b2c3d4e5f6`，四项校验全过，V1/V2 基线不变；详见 PR）
**目标**：`stf_v3` 数据库建立，全部 Stage 1 表一次迁移到位；`stf_v3/` 包骨架成形（src 布局，自带依赖 / Dockerfile / Alembic / 测试）。
**方法**：同一 Postgres 实例新建 database（`scripts/create_database.sh`，两个角色：owner `stf_v3`、runtime `stf_v3_app`）；按蓝图 §4 手写初始迁移（12 张业务表，**无 pgvector / rag_chunks**）+ procrastinate 自带 schema + 运行角色授权（audit_events 只给 INSERT/SELECT）；`alembic check` 保证模型与迁移一致；校验脚本 `scripts/check_schema.py` 四项检查；import-linter 合同。
**验收**：空库 `upgrade head` 一次通过；`alembic check` 无漂移；`downgrade base` 后表全部消失、再 `upgrade` 成功；表与列与模型逐一对应；插入无车档的 `obd_logs` 被数据库拒绝；`stf_v3_app` 对 `audit_events` UPDATE/DELETE 被拒；单 head；V1/V2 健康检查与迁移版本不变。

#### PROD-03 — 后端骨架、Auth、workshop 与车辆模块

**目标**：模块化单体骨架成形，用户能注册、登录、在 workshop 里建车档。
**方法**：FastAPI 应用按模块分包；`auth`（fastapi-users + 邀请码核销）；`workshops` / `memberships`；`vehicles`（建档：VIN 必填唯一、车牌可选、品牌型号必填；软删除；`can_access_vehicle()` = 成员判断）；structlog；`/health`；**OpenAPI 契约 v1 导出到 `docs/api/v3_openapi.json`**。
**验收**：邀请码 → 注册 → 登录 → 建车档 → 列车 → 删车（软删）全链路 API 测试通过；非本 workshop 成员访问返回 403；全后端只有一处权限判断入口；缺 VIN 或 VIN 重复被 422 拒绝；改车牌不影响任何查询。

#### PROD-04 — 并存部署、CI 与迁移性检查

**目标**：V3 后端在 PolyU 服务器上与 V1/V2 并存运行；每个 PR 自动跑测试和两条迁移性检查。
**方法**：`infra/docker-compose.v3.yml`（`stf-v3-api`、`stf-v3-worker`，独立端口）+ nginx 路由前缀；共用 Postgres 实例与 vLLM（配置连接）；GitHub Actions：pytest（离线）+ OpenAPI diff + 可迁出检查（拷贝两目录到空目录跑测试）+ 不绑死检查（临时检出删除老目录后构建）；部署步骤追加到 CLAUDE.md。
**验收**：服务器上 V1/V2/V3 健康检查同时通过；Cloudflare 隧道能访问 V3 `/health`；CI 四项在 PR 上绿灯。

### 3.3 M2 — 数据入口

#### PROD-05 — 上传薄层与文件存储

**目标**：日志文件能上传入库并绑定车档，不触发诊断。
**方法**：`ingest` 模块：上传端点挂在车辆之下；格式嗅探（仅 tsv / yamaha，其余 422）、sha256、`(vehicle_id, sha256)` 唯一、原始字节落文件存储、`obd_logs` 记录；日志内读到 VIN 时与车档核对，不一致记告警。
**验收**：两种格式上传成功并可下载原字节；第三种格式 422；同文件二次上传返回已存在；上传后 `diagnosis_conversations` 无新增行；每条 `obd_logs` 都能 join 到车档；VIN 不一致的日志有告警记录。

#### PROD-06 — 知识库拷贝与 jobs 队列

**目标**：V2 的手册知识库在 V3 可用；procrastinate 队列上线，手册转换改由队列异步执行；完成一次队列运维演练。
**方法**：一次性脚本拷贝 `manuals` 元数据行 + Markdown/图片文件到 `stf_v3`（**不拷 rag_chunks，不做向量化**）；`jobs` 模块（procrastinate app、队列划分、重试策略）；`stf-v3-worker` 容器；宿主机 GPU worker 直连库消费 `gpu` 队列（并发 1），V2 文件协议退役；手册转换（PDF → Markdown）包成 job，进度写库供 UI 查询。
**验收**：V3 库中 manuals 行数与 V2 一致，Markdown 文件逐本可被 manual_fs 读取；提交一份新手册 → job 完成 → 可被 `list_manuals` / `search_manual_text` 命中；**运维演练四项各做一遍并写进运维手册**：查积压与卡点（SQL）、手动重试失败任务、查看死信、worker 重启后进行中的任务被正确回收。

#### PROD-07 — Jetson 上传器接入

**目标**：行程结束自动上传到 V3，且每笔数据绑定唯一车档（D8）。
**方法**：按 PROD-01 选定的机制改 `jetson_uploader`；过渡期同时推 V2（原参数）与 V3；失败重试。
**验收**：真机一次行程结束后 `obd_logs` 出现新行且归属正确车档；断网重连后补传成功；V2 侧照常收到同一行程。

### 3.4 M3 — Agent 核心

#### PROD-08 — Pydantic AI 运行时与工具组

**目标**：V2 的诊断能力在 Pydantic AI 上复现。
**方法**：复制 V2 `harness_tools/` 进 `stf_v3`（不抽共享包）；OBD 原始读取 / 信号 / DTC / manual_fs 工具注册；Manual 子代理 = agent-as-tool；Context/Memory 策略（会话记忆、压缩触发、车辆信息注入：品牌型号 → 手册匹配，VIN → 历史）；TestModel 离线测试。
**验收**：每个工具有单测且输出为文本摘要；TestModel 下完整一轮诊断可跑通；子代理委托可在事件流中观察到；提示词中的车辆信息来自车档而非日志。

#### PROD-09 — 模型适配层与云端接口

**目标**：唯一本地 vLLM + Qwen3.6-27B 为默认，云端只作对比。
**方法**：ModelProfile 收敛 qwen 工具调用怪癖与 thinking 抑制；OpenRouter 接口用配置开关；嵌入模型走同一 vLLM；模型地址只在配置里。
**验收**：同一 golden 用例本地与云端各跑通一次；切换只改配置不改代码；qwen 无 thinking 泄漏到报告。

#### PROD-10 — Golden 评测移植与门槛

**目标**：V3 的每次改动都能用与 V2 同一把尺子衡量；换模型时 V2/V3 各跑一遍（D7）。
**方法**：eval runner 复制进 `stf_v3/evals/`，适配 Pydantic AI 调用签名；golden 数据文件复制；一条命令跑 manual lane 30 + OBD 15。
**验收**：V3 首次基线 ≥ 0.831（manual）/ ≥ 0.938（OBD）；低于基线的 PR 不得合并；评测报告落 `docs/evals/`。

### 3.5 M4 — S1 诊断闭环

#### PROD-11 — 诊断 job、SSE 与回写、回放

**目标**：一键报告在后端端到端跑通，独立于连接运行，可断线回放；没看过代码的人能按 OpenAPI 用 Swagger 跑完一次诊断。
**方法**：`diagnosis` 模块：`POST /vehicles/{id}/diagnose` 在同一事务里创建会话 + 投 job → worker 运行 Agent → 事件写 audit_events、消息写 messages → 完成写 reports、会话 status=done；`GET /conversations/{id}/events` 支持 SSE 订阅与按 seq 回放；错误以 `error` 事件收尾；OpenAPI 契约 v2（Stage 1 全部端点）。
**验收**：设计文档数据模型"图 3 走查"每一步落表正确；SSE 事件名与 audit_events.event_type 完全一致；中途断开后按 seq 回放得到完整过程；API 进程重启后进行中的诊断由 worker 继续或标记 error，不出现永远 running 的会话；**一名未读代码的评审者仅凭 Swagger + OpenAPI 注释完成注册 → 建车 → 上传 → 诊断 → 读报告**。

### 3.6 M5 — 前端 Stage 1（待定 · 外部依赖）

前端归属等下次会议确定。以下 ticket 保留作为需求描述，供无论谁来做前端时使用；不排期。

#### PROD-12 — 前端骨架、登录与车辆页
**目标**：用户在浏览器里完成注册、登录、建车档。**验收**：线上浏览器 E2E：注册 → 登录 → 建车 → 车辆页；移动与桌面布局均可用。

#### PROD-13 — 上传、流式一键报告与报告视图
**目标**：车辆页上传日志、点一键诊断、看流式过程与最终报告（双视图）。**验收**：线上 E2E 一次完整诊断在手机上完成；刷新后过程视图从回放接口恢复；报告引用可点击到手册出处。

#### PROD-14 — PWA 与移动打磨
**目标**：可安装到手机主屏，弱网可用。**验收**：Android Chrome / iOS Safari 可安装；Lighthouse PWA 项通过；弱网下上传有进度与失败提示。

### 3.7 M6 — Pilot 上线

#### PROD-15 — Pilot 上线与运维底线

**目标**：第一批真实用户开始使用，且系统能备份、能追溯、能排障。
**方法**：Cloudflare 路由定版；Postgres 定时备份 + 一次恢复演练；audit_events 保留策略作为 `jobs` 定时任务（如 90 天归档）；结构化日志落盘；发放邀请码、建 workshop 与成员；运维手册（部署 / 回滚 / 队列操作 / 常见故障）。
**验收**：第一批 pilot 用户各完成一次真实诊断；备份恢复演练在测试库成功；运维手册经一次按手册操作验证。

## 4. 暂缓事项与回头条件（D9 要求：留档，到时能直接执行或直接判定不做）

每个里程碑结束时复查本表；触发条件出现 → 开 PROD ticket；确认不需要 → 状态改为"放弃"并写理由。

| 事项 | 触发条件（什么时候回头） | 预期动作 | 是否动地基 | 状态 |
|---|---|---|---|---|
| S2 报告内追问 | Stage 1 pilot 跑通、用户提出追问需求 | `messages` 追加、前端追问框 | 否 | 暂缓 |
| 车队历史模块（vehicle_data_twin） | 与学生对齐 API 形态 + PII 脱敏规则定稿 | `history` 模块、按 VIN/车牌对应、脱敏视图、`get_vehicle_history` 工具 | 否 | 暂缓 |
| 车队 Dashboard | 第一个 workshop 有 ≥ 10 台车在用 | `dashboard` 模块只读聚合，按 workshop 汇总 | 否 | 暂缓 |
| S3 车辆级自由对话 | S2 稳定后 | 输入护栏 + 会话复用 | 否 | 暂缓 |
| S4 系统主动触发 | 上传频率稳定、有明确触发规则 | `vehicle_events` 表 + `jobs` 事件类型（上传完成 → 规则 → 诊断） | 否 | 暂缓 |
| 相似案例 | 会话数 ≥ 100 | `case_vectors` 表 + `find_similar_cases` 工具 | 否 | 暂缓 |
| 向量检索 / RAG（pgvector + rag_chunks） | 出现"手册全文字符串搜索不够用"的真实用例，或相似案例开工 | 一条迁移加 `vector` 扩展 + 表；入库流水线加切块向量化步骤 | 否 | 暂缓（2026-09-11 从 Stage 1 移除） |
| VIN 模糊化 | 正式对外 / 出现非内部用户 | 展示层脱敏 + 导出脱敏 | 否 | 暂缓（对外前必做） |
| 第二个 workshop / 多租户 | 第二个车队确认接入 | `workshops` 加一行 | 否 | 暂缓 |
| 细粒度权限 | 出现"某台车只给某人看"的真实需求 | 权限表 + 改 `can_access_vehicle()` 一处 | 否 | 暂缓 |
| 实时遥测 / 时序库 | 产品需要"车在跑时看" | 独立评估，可能是新模块 + 时序存储 | 待评估 | 暂缓 |
| 通用日志格式层 | 第三种日志格式出现 | 针对该格式加解析器，不建通用层 | 否 | 暂缓 |
| 微服务 / K8s | 单机资源或团队规模撑不住 | 按模块边界拆 | 否（模块化单体即为此准备） | 暂缓 |
| 配额 / 限流 | 出现滥用或资源争抢 | 网关层限流 | 否 | 暂缓 |
| 模型微调 | golden 分数在提示词/工具优化后仍达不到目标 | 走 V1 §11 路线 | 否 | 暂缓 |
| V1/V2 数据迁移 | 明确需要老数据在 V3 可见 | 一次性导入脚本 | 否 | 暂缓（默认放弃） |
| 前端语言 / 样式 | 前端归属确定后 | 由前端需求文档定 | 否 | 等待外部 |

## 5. 待决策

全部 9 项已拍板（2026-09-08），结果见 §0.1；存档 `.lavish/v3_dev_plan_decisions.html`。

新增开放项：**前端实现归属**（学生 vs 我们）→ 下次会议；在此之前 M5 不排期。

## 6. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-07 | 初稿：7 个里程碑、15 张 PROD ticket（PROD-01 到验收级，其余到目标/方法/验收层）、非目标清单草案、9 项待决策 |
| v1.2 | 2026-09-11 | PROD-02 开工（分支 `prod-02-db-migration`）：**Stage 1 去掉 pgvector / rag_chunks / 向量化**（§1.1、PROD-02、PROD-06 改写；§4 新增"向量检索 / RAG"暂缓项与回头条件）。设计文档同步至 v1.4，架构图已同步 |
| v1.1 | 2026-09-11 | PROD-01 DONE：代码设计蓝图 v1.0 评审通过；M0 状态更新；§2.1 M0 改为"已完成"。设计文档同步至 v1.3（机制层表 `vehicle_devices` 等），架构图已同步 |
| **v1.0** | 2026-09-08 | **定稿**：D1–D9 全部拍板并并入（§0.1）；所有权挂 workshop、VIN 身份 / 车牌标签、同仓新目录 + 可迁出/不绑死约束与自动化检查、fastapi-users、procrastinate `jobs`、前端暂缓（M5 待定 · 外部）、唯一 vLLM、数据归属不变量入 DoD；M1 起交付 OpenAPI 契约；PROD-06 增加队列运维演练；§4 改为"暂缓事项与回头条件"（逐项触发条件 / 动作 / 状态）。设计文档同步至 v1.2，架构图已同步 |
