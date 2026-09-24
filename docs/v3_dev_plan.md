# V3 开发计划 — 产品化底座（v1.0 定稿）

**Stage 1 一键诊断报告 + 面向车队监控/AI 诊断的可扩展底座**

| 字段 | 值 |
|---|---|
| **架构文档** | `docs/v3_design_doc.md`（v1.2） |
| **架构图** | `docs/diagrams/stf_v3_final_architecture.excalidraw`（图文强制同步；预览由 `diagrams/render_excalidraw.py` 生成） |
| **决策存档** | `.lavish/v3_dev_plan_decisions.html`（D1–D9，2026-09-08，本地不提交） |
| **Ticket 前缀** | `PROD-XX` |
| **版本** | v1.12（PROD-10 后续：OBD 拒答提示词适配、服务器升级回头条件、V3 迁移命令修正） |
| **作者** | Xiangzhu Yan |
| **最后更新** | 2026-09-24 |

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
- **测试补齐（§2.4）**：新功能同时扩展 `scripts/smoke_e2e.py`；接口级集成测试覆盖新端点；部署前后跑 `scripts/isolation_check.sh snapshot / compare`
- **PR 附"挑战清单"**：3–5 条"你可以试什么、预期看到什么"，供用户在 Swagger / curl 上挑战
- V1/V2 在服务器上继续正常运行（并存不互扰）

### 2.4 测试策略（2026-09-11 对齐；所有测试都要补齐）

八类测试，前四类跑在代码上，后四类跑在部署上。明确不做：压测、混沌、覆盖率 KPI。

| 类别 | 回答什么 | 在哪跑 | V3 落点 |
|---|---|---|---|
| 单元 | 这段逻辑本身对不对 | CI，每 PR，秒级 | `stf_v3/tests/test_unit_*.py` |
| 集成 | 和真实 Postgres / 队列一起对不对 | CI 临时 Postgres（PROD-04）或服务器一次性库 | `tests/test_migrations.py`、`tests/test_api_*.py`（ASGI 客户端 + 真库） |
| 契约 | 接口形状、表结构、模块边界有没有悄悄变 | CI，秒级 | `scripts/export_openapi.py --check`、`alembic check`、import-linter |
| Golden 评测 | 换提示词 / 模型后诊断质量有没有掉 | 服务器 GPU，只在动 Agent 时 | PROD-10 移植 |
| 端到端冒烟 | 真实用户流程在线上能否走完 | 分支部署后 | `scripts/smoke_e2e.py`（每张 ticket 追加步骤） |
| 部署核验 | 部署完的东西是不是预期的那份 | 每次部署后 | `scripts/check_schema.py`；PROD-04 补 `deploy_check.sh`（容器新鲜度、head、worker、health） |
| 隔离回归 | V1/V2 有没有被 V3 碰坏 | 每次部署前后 | `scripts/isolation_check.sh snapshot` / `compare` |
| 运维演练 | 备份能恢复吗、队列卡了能救吗 | 每个里程碑一次，人工按手册 | PROD-06、PROD-15 |

CI 两层（PROD-04）：GitHub 上跑单元 + 契约 + 集成；服务器上跑冒烟 + 部署核验 + 隔离回归，结果以一张表进 PR。

### 2.5 开工前审核流程（2026-09-14 定，自 PROD-06 起每张 ticket 适用）

每张 ticket 在写任何代码之前走**三轮 Lavish 审核**，每轮由用户拍板后才进下一轮；三轮追加在同一份页面（`.lavish/prodNN_plan_review.html`，本地不提交），决策来源集中一处。可调用的技能 `.claude/skills/ticket-kickoff/SKILL.md` 是本节的执行模板。

| 轮次 | 内容 | 用户做什么 |
|---|---|---|
| **一 · 高层介绍** | 现状（含服务器实况）、打算做的事、验收目标、需求层决策、"我自己定的"。通俗语言，正文**不出现代码路径 / 类名 / 目录名**；页尾一个折叠的"涉及文件"清单（每文件一行说明用途）是唯一例外。每件事**不附**测试方法 | 拍方向与需求层决策 |
| **二 · 失效模式（盲审）** | 一个**看不到代码**的子代理，只凭第一轮文字 + 验收目标 + 环境事实 + 分类清单（数据丢失 / 归属越权 / 部分失败 / 并发重复 / 外部依赖 / 升级回滚 / 可观测 / 运维 / 密钥隐私 / 时间费用 / V1V2 互扰）列出失效模式；AI 另补一份"来自代码细读"的清单，分开标注。每条编号 `FM-n`，附**推荐**（处理 / 推迟 / 接受） | 只改不同意的条目，其余默认按推荐 |
| **三 · 测试方案** | 每条"处理"的 FM 至少一条测试（单元 / 集成 / 契约 / 端到端冒烟 / 部署核验 / 隔离回归 / 运维演练 / golden），编号 `T-n` 标明覆盖哪些 FM、怎么做、预期看到什么、在哪跑；"推迟"的 FM 写回头条件进 §4 | 通过后才开工 |

交付核对板（§2.3 DoD）在原有"通俗说明 → 代码锚点 → 测试结果"之外加一列 **FM → T → 结果**，让每条风险都能追到盖住它的测试。代码锚点只出现在核对板，不出现在计划页。

**为什么**：PROD-06 第一版计划页里近半句子指向类或目录，影响判断方向；按功能清单推导的测试会漏掉"两件事一起坏"的场景；盲审避免被已知实现绑住思路。

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

Status: **✅ DONE**（2026-09-11，分支 `prod-03-backend-skeleton`；服务器：19 个 pytest 全过、冒烟 23 步全过、隔离回归通过；详见 PR）
**目标**：模块化单体骨架成形，用户能注册、登录、在 workshop 里建车档、发设备凭证；OpenAPI 契约 v1 落盘。
**方法**：FastAPI 应用按模块分包（src 布局）；`auth`（fastapi-users JWT，用户名登录，自写邀请码注册：锁码 → 建用户 → 入组 → 核销同一事务）；`workshops`（成员、角色、邀请码；因分层需要其端点由 auth 路由提供）；`vehicles`（车档 CRUD、VIN 不可改、软删除、设备凭证只存哈希、`can_access_vehicle()` 唯一鉴权入口，无权一律 404）；structlog JSON；`/health` 含队列积压；`scripts/export_openapi.py` → `docs/api/v3_openapi.json`（12 个路径）；`scripts/create_workshop.py`；`scripts/smoke_e2e.py`；`scripts/isolation_check.sh`。
**验收（已达成）**：邀请码 → 注册 → 登录 → 建车 → 列车 → 改车牌 → 发码 → 技师注册 → 设备凭证 → 吊销 → 软删全链路（冒烟 23 步）；非成员访问返回 404、角色不足 403；全后端只有一处权限判断入口；无效 / 重复邀请码 422；VIN 重复 409、格式错误 422；OpenAPI 可用。

#### PROD-04 — 并存部署、CI 与迁移性检查

Status: **✅ DONE**（2026-09-13，分支 `prod-04-deploy-ci`；计划页 `.lavish/prod04_plan_review.html` D1 = 对外可达、D2 = GitHub Actions）
**做了**：`stf-v3-api` + `stf-v3-worker` 常驻（独立 Compose 项目 `-p stf_v3`，镜像带 git commit 标签；worker 跑空的 procrastinate 应用 + 每分钟心跳）；nginx 新增 `upstream api_v3` + `/v3/auth/`（复用 auth 限流）+ `/v3/`（SSE 参数），V1/V2 块未动；`/v3/health` 别名（含 commit）；`.github/workflows/v3.yml` 四个 job（unit+contract、integration 临时 Postgres 15、portable、unbound 每晚）；`scripts/deploy_check.sh`（5 项）、`check_portable.sh`、`check_unbound.sh`；冒烟改走 `/v3/health` 并在 8003 + 一次性库上跑；CLAUDE.md 新增 V3 部署段；服务器 `infra/.env` 补三项密钥（不入库）。
**服务器结果**：deploy_check 5/5 PASS；失败演练：commit 不匹配 FAIL（推脚本未重建时自然出现）、worker 停止 FAIL（心跳 + 容器状态）；`/v3/health`、`/v3/docs` 服务器内与公网（stf-diagnosis.dev）均 200，V1 `/health` 与旧前端 200；API 重启 3 s 恢复；冒烟 23/23 PASS（8003 + stf_v3_test），正式库 smoke 残留 0；只读探测 openapi 200 / 错密码 400 / 无 token 401；日志无密钥；隔离回归 PASS。
**抓到的问题（已修）**：① podman-compose 默认项目名 = 目录名 `infra`，V3 被放进 V1/V2 同一个 pod，`down` 试图拆共享 pod → 一律 `-p stf_v3`；② nginx.conf 是单文件 bind 挂载，`git pull` 换 inode 后容器内仍是旧文件，`nginx -s reload` 无效 → 改配置必须重建 nginx 容器；③ deploy_check 容器年龄计算丢时区 → 修。

**目标**：V3 后端在 PolyU 服务器上与 V1/V2 并存运行；每个 PR 自动跑测试和两条迁移性检查。
**方法**：`infra/docker-compose.v3.yml`（`stf-v3-api`、`stf-v3-worker`，独立端口）+ nginx 路由前缀；共用 Postgres 实例与 vLLM（配置连接）；GitHub Actions：pytest（离线）+ OpenAPI diff + 可迁出检查（拷贝两目录到空目录跑测试）+ 不绑死检查（临时检出删除老目录后构建）；部署步骤追加到 CLAUDE.md。
**验收**：服务器上 V1/V2/V3 健康检查同时通过；Cloudflare 隧道能访问 V3 `/health`；CI 四项在 PR 上绿灯。

### 3.3 M2 — 数据入口

#### PROD-05 — 上传薄层与文件存储 — **DONE（2026-09-13，PR #243）**

**目标**：日志文件能上传入库并绑定车档，不触发诊断。
**方法**：`ingest` 模块：上传端点挂在车辆之下；格式嗅探（仅 tsv / yamaha，其余 422）、sha256、`(vehicle_id, sha256)` 唯一、原始字节落文件存储、`obd_logs` 记录；日志内读到 VIN 时与车档核对，**不一致直接拒收 422 `vin_mismatch`、不落盘不入库**（决策 D2，2026-09-13：原"照常入库 + 告警"改为拒收，错车数据永远进不了库；错误信息指出文件 VIN 对应同车队哪台车）。
**验收**：两种格式上传成功并可下载原字节；第三种格式 422；同文件二次上传返回已存在；上传后 `diagnosis_conversations` 无新增行；每条 `obd_logs` 都能 join 到车档；VIN 不一致的日志被拒且有 `ingest.vin_mismatch` 告警日志。
**开工前决策（Lavish `.lavish/prod05_plan_review.html`，2026-09-13）**：D1 本轮不碰真 Jetson（curl 模拟设备验收；Jetson 双推待 PROD-06 后单开小 ticket）；D2 VIN 不一致拒收；D3 备份按原计划等 PROD-15（此前 V3 只装测试数据）。
**实现**：`stf_v3/src/stf_v3/ingest/{parsers/,schemas,storage,service,router}.py`；5 个端点（成员上传 / 设备上传 `X-Device-Token` / 列表 / 元数据 / 原字节下载）；解析器从 V2 复制（`jetson_tsv.py`、`yamaha_csv.py`，去 VIN 假名化，只嗅探前 64 行；Yamaha 判定收紧为首行 `# Yamaha Dual`）；存储 `<卷>/<vehicle_id>/<log_id>.<ext>`，具名卷 `stf_v3_obd_logs`（api + worker 挂载）；单文件上限 50 MB（413）；只收 multipart；删车不删文件；**无新迁移**。蓝图 O2 已解：Yamaha 起止时间取 `# Start:` / `# End:` 行。
**测试**：`test_unit_ingest.py` 12 个函数 16 用例（嗅探 / 解析器 / 存储 / 反例）、`test_api_ingest.py` 11 个（两格式、422、重复、413、非成员 404、D2 拒收两入口、设备 token 三种 401、下载比对、删车 404、归属不变量）；夹具 `tests/fixtures/`（假 VIN，≤ 40 行）；`smoke_e2e.py` 23 → 32 步；`deploy_check.sh` 第 6 项（存储卷可写）；`isolation_check.sh` 快照加 V1 日志卷文件数；OpenAPI 13 → 17 路径。

#### PROD-06 — 知识库拷贝与 jobs 队列 — **DONE（2026-09-14，PR #245）**

**目标**：V2 的手册知识库在 V3 可用；procrastinate 队列上线，手册入库改由队列异步执行；完成一次队列运维演练。
**方法**：一次性脚本拷贝 `manuals` 元数据行 + 每本手册的**整个目录**（Markdown、图片、原始 PDF、HARNESS-30 索引 sidecar）到 `stf_v3`（**不拷 rag_chunks，不做向量化**）；`jobs` 模块（procrastinate app、队列划分、重试策略、stalled 回收）；`stf-v3-worker` 容器；宿主机 GPU worker 直连库消费 `gpu` 队列（并发 1），V2 文件协议退役；手册入库包成 job，进度写库供 UI 查询。
**开工前三轮审核（§2.5 首次执行，`.lavish/prod06_plan_review.html`，2026-09-14）**：D1 **新手册入库一步到位**——MinerU 转换 → 建目录树与索引 → 章节摘要 → 八道质量门 → 全过才"已入库"，**marker 在 V3 不再使用**（用户否决两段式："没有几分钟内可读的 SLA，中间会读到粗版资料"）；宿主机 worker 不走蓝图 R2 的薄脚本退路，而是用用户级 `uv` 装 V3 专属 Python 3.11 环境直接跑 V3 代码（用户："不要为注定退役的东西写适配层"）；盲审子代理 27 条 + 代码细读 10 条失效模式，全部按推荐处置（处理 30 / 推迟 3 / 接受 4）；19 条测试 T-1 ~ T-19 每条对应 FM。
**验收**：V3 库中 manuals 行数与 V2 一致（2），两本均以索引轨被 V3 读取器加载（目录树、章节、图片、搜索）；提交一份新手册 → job 完成 → 列表 / 目录树 / 搜索命中；损坏 PDF → 重试到上限后失败且原因可见；**运维演练四项各做一遍并写进运维手册**（`docs/v3_ops_runbook.md`）：查积压与卡点、看死信及原因、手动重试失败任务、worker 重启后进行中的任务被正确回收（`jobs.recover_stalled` 每 2 分钟回收 stalled 任务）。
**实现**：`stf_v3/src/stf_v3/knowledge/{schemas,service,router,tasks,ingest}.py` + `knowledge/pipeline/`（从 V2 `manual_pipeline` 复制，加按手册隔离的摘要缓存）+ `knowledge/{manual_fs,manual_index}.py`（复制）；6 个端点（列表 / 详情 / 目录树 / 搜索 / 上传投任务 / 删除，公共库：成员可读、manager 可写、搬来的 seed 手册接口不可删）；任务 `knowledge.ingest_manual`（gpu 队列，永久性错误不重试，瞬时错误最多 3 次，MinerU 产物与摘要缓存持久化可续跑）、`knowledge.gpu_heartbeat`、`jobs.recover_stalled`；`stf_v3/gpu_worker/{install.sh,stf-v3-gpu-worker.service}`（uv → Python 3.11 → `venv-stf-v3` → `stf_v3[gpu]`；MinerU 为外部 CLI `STF_V3_MINERU_BIN`；11 项自检）；`scripts/copy_manuals_from_v2.py`（JSON 导出输入，逐文件 sha256 校验，V2 密码不经过 V3）；`scripts/queue_ops.sh`（status / failed / retry / cancel / drill）；具名卷 `stf_v3_manuals`；`/v3/health` 增加 gpu_worker、分队列积压、磁盘余量；`deploy_check.sh` 第 7 项（宿主机 worker 存活且提交号一致）、第 8 项（磁盘 ≥ 30 GB）；隔离快照加 V1 手册卷文件数与 V2 marker-worker 状态；**无新迁移**。
**测试**：`test_unit_knowledge.py` 13、`test_api_manuals.py` 8；契约新增"接口层不许导入流水线"（import-linter）+ 运行时 sys.modules 检查；`smoke_e2e.py` 32 → 39 步；OpenAPI 17 → 21 路径；服务器实测见 PR #245 验证表（Corolla 277 页整条链耗时与费用、损坏 PDF 失败路径、运维四项 + kill -9 回收）。

#### PROD-07 — Jetson 上传器接入 — **DONE（2026-09-17，PR #246；真机补录待协作者）**

**目标**：行程结束自动上传到 V3，且每笔数据绑定唯一车档（D8）。
**方法**：按 PROD-01 选定的机制改 `jetson_uploader`；过渡期同时推 V2（原参数）与 V3；失败重试。
**验收**：真机一次行程结束后 `obd_logs` 出现新行且归属正确车档；断网重连后补传成功；V2 侧照常收到同一行程。

**开工前三轮审核（§2.5，`.lavish/prod07_plan_review.html`，2026-09-17）**：
- D1 第一个真实车队 = "PolyU STF 实验车队"；项目负责人注册 manager，Perry 一枚技师邀请码。
- D2 完成边界 = 脚本 + 补传 + 装机手册 + 建档发 token + **服务器等价验证**全过；真机一趟作为**验收补录**，不阻塞 PROD-08（截止 2026-10-01，见 §4）。
- 第二轮 36 条失效模式全按推荐（处理 27 / 推迟 2 / 接受 7）；两处修正第一轮：401（token 吊销）留在待传目录而非拒收目录；V3 后端加拒收结构化日志（不改接口）。
- 第三轮 15 条测试 T-1 ~ T-15 全部实施，"处理"条目无一遗漏。
- **D3（开工后发现，2026-09-17）**：旧系统卷里真实设备的 68 份日志中 23 份是 "OBD Maximum Data Log"（Perry 的记录脚本自己的 CSV），只有 2 份原生 TSV（测试上传）；V3 会拒收真机格式。决定 **A：V3 针对性加 `maxlog` 解析器**（嗅探 banner、`# vehicle_id:` / Mode 09 VIN、`# Start Time:` / `# Log End Time:` 时间窗，原始字节照存），`obd_logs.format` CHECK 加 `maxlog`（迁移 `b2c3d4e5f6a7`）。这是 §4"通用日志格式转换层"回头条件的第一次触发。

**实现**：
- `obd_agent/jetson_uploader.py`：V2 那腿逐字节不变（11 个旧测试原样通过）；V3 那腿只在设备上有 env 文件（`STF_V3_BASE_URL` / `STF_V3_DEVICE_TOKEN` / `STF_V3_VEHICLE_ID`，token 永不作命令行参数）时启用；先推 V2 再推 V3；V3 连接错误 / 5xx 重试 5 / 15 / 45 s 后进待传目录（临时名 + 原子改名，原始文件不动，上限 200 文件 / 500 MB）；`--drain` 逐文件补传、遇网络错误即停、退避 10 → 60 min；413 / 422 进拒收目录 + `.error.txt`；401 留在待传并停止；回应车档编号与配置不符即报错；文件锁；滚动运行日志；`--self-check`；退出码 0 / 1 / 2；写超时按文件大小放宽（≤ 10 min）。零新依赖，Python 3.8 语法（CI 双版本跑）。
- `stf_v3/ingest/service.py`：每次拒收（413 / 422）写 `ingest.rejected` 结构化事件（原因、设备、文件名、大小、首行）。
- `stf_v3/scripts/onboard_first_workshop.py`：建车队 / 邀请码 / 车档 / token；VIN 两次隐藏输入、库内读回核对、不打印；token 按车写成 600 权限的 env 文件。
- `docs/v3_device_install.md`：装机手册（13 节，CI 契约测试锁定必需小节）；`infra/README_OBD_AGENT_SETUP.md` 加 V3 一节。
- 冒烟追加 3 步（39 → 42）；CI 新增 `uploader` job（py3.8 + 3.11）。

**服务器等价验证（2026-09-17）**：见 PR #246 验证表：真实 Hiace 日志双推（V2 新会话 + V3 201 绑定 Hiace 车档、二传 200 已存在、无 VIN 日志 201）；V3 指向不通端口 → 待传目录 1、V2 照常；恢复后 `--drain` → 0；再 drain 不重复；补传期间 V2 调用 0 次；正式库建档：1 车队 / manager + 技师邀请码 / 2 车档 / 2 token。**真机补录**：待协作者装机后在此补一行。

### 3.4 M3 — Agent 核心

#### PROD-08 — Pydantic AI 运行时与工具组 — **DONE（2026-09-17，PR #247）**

**目标**：V2 的诊断能力在 Pydantic AI 上复现。
**方法**：复制 V2 `harness_tools/` 进 `stf_v3`（不抽共享包）；OBD 原始读取 / 信号 / DTC / manual_fs 工具注册；Manual 子代理 = agent-as-tool；Context/Memory 策略（会话记忆、压缩触发、车辆信息注入：品牌型号 → 手册匹配，VIN → 历史）；TestModel 离线测试。
**验收**：每个工具有单测且输出为文本摘要；TestModel 下完整一轮诊断可跑通；子代理委托可在事件流中观察到；提示词中的车辆信息来自车档而非日志。

**开工前三轮审核（§2.5，`.lavish/prod08_plan_review.html`，2026-09-17）**：
- D1 服务器"真跑一次"用现有 Ollama qwen3.5 27B（接受 thinking 关不掉的慢与杂质，只求链路通；模型适配与 vLLM 切换仍是 PROD-09 / #237）。
- D2 报告默认语言 **繁体中文（zh-TW）**，请求可指定 en / zh-CN；PROD-10 评测固定 en 与 V2 同尺。
- 第一轮纠正一个事实：旧系统"读得懂" OBD Maximum 是因为上传时先转成 TSV；V3 存原始字节，**PROD-08 的读取器直读三种原始格式**（信号名按 V2 转换规则去单位后缀、跳过状态列，与 V2 golden 一致）。
- 第二轮 57 条失效模式（盲审 36 + 代码细读 21）全按推荐（处理 47 / 推迟 2 / 接受 8）；与盲审不同的一条：FM-26（读原始值窗口本质是原始数据）按 V2 已锁定决定（HARNESS-19）接受。第三轮 17 条测试 T-1 ~ T-17（CI 14 / 服务器 3），"处理"条目无一遗漏。
- 蓝图条件"V2 主循环仍在用 OBD 委托"成立 → **两个子代理都搬**（手册 + OBD），各以工具形式挂到主 Agent。

**实现**（Pydantic AI 2.44.0，`pydantic-ai-slim[openai]`；零 numpy / pandas）：
- `stf_v3/ingest/loader.py`：三种原始格式（Jetson TSV / Yamaha CSV / OBD Maximum CSV）读成同一内部形状；maxlog 元数据 DTC 行可读，Mode 43 / 47 / 4A 原始帧解码（`430100AF` → P00AF；空帧不列）；读文件前先核对日志行归属车档（FM-4）。
- `stf_v3/diagnosis/tools/`：6 个 OBD 工具 + 4 个手册工具（函数体照抄 V2，参数描述逐字进 docstring）+ 2 个委托工具；统一执行路径（同参重复调用记忆化、结果截断、轨迹、只记大小的日志）；手册编号 = 数据库编号（索引轨按此命中）；手册图片默认不进模型（开关 `STF_V3_MANUAL_IMAGES_ENABLED`）；列手册标出"是否与本车车档匹配"。
- `stf_v3/diagnosis/agent/`：依赖包（车辆信息来自车档；非本机模型时 VIN 用 `V-xxxxxxxx` 假名）、10 种事件（蓝图 §3.7 同名）+ 事件槽、上下文（中日韩字符按 1 token 估算；压缩为历史处理器——只改发给模型的内容、折叠后仍是合法历史）、记忆（消息列表 ⇄ JSON，二进制剥离）、手册子代理四道护栏（锁定手册 / 拦截外车手册 / 4 次读取后强制收尾 / 别名归一）、统一驱动器（`agent.iter` 逐节点出事件；墙钟 / 请求数 / 工具数 / 总 token 四道闸门 → 部分报告而非异常；取消回调）、主 Agent + 单次可迭代事件流、报告对象（正文 + 从工具轨迹确定性抽出的引用，正文里没读过的引用标 `NO_SOURCE`）、唯一模型来源（非本机地址须 `STF_V3_LLM_ALLOW_CLOUD`）、`bootstrap.py`（唯一的数据库访问：行 → 依赖包）。
- `stf_v3/scripts/diagnose_once.py`：服务器真跑脚本（端点预检 + 预热、总超时、事件逐条打印、报告 / 事件 / 消息落独立目录且文件名不含 VIN）。
- 设置：`STF_V3_LLM_BASE_URL / MODEL / API_KEY / ALLOW_CLOUD`、云端一组（默认关）、预算（`agent_*` / `subagent_*` / 截断 / 压缩阈值）、`default_locale`；compose 透传；import-linter"接口层不许导入流水线"契约把 diagnosis 纳入来源。**无新迁移、无新接口**（诊断接口在 PROD-11）。
**测试**：`test_unit_loader` 7、`test_unit_tools_obd` 20、`test_unit_tools_manual` 12、`test_unit_agent_contract` 5、`test_unit_context_memory` 5、`test_unit_manual_guards` 6、`test_unit_agent_offline` 20（TestModel 三格式整轮、剧本 FunctionModel 嵌套委托 / 共享用量 / 文字+工具调用继续 / 引用 NO_SOURCE / 思考不进正文、四道闸门、模型断连、小助手超预算、取消、空回复、车档身份与假名、日志无内容无 VIN、记忆化）、`test_db_diagnosis` 1（行 → 依赖包 → 整轮）。**服务器等价验证（2026-09-17，PR #247 验证表）**：镜像内 148 passed + lint-imports 3 kept；deploy_check 8/8、隔离 PASS；真跑两次（现有 Ollama qwen3.5，zh-TW）：健康 Hiace 行程 346 s / 8 请求 / 20 工具调用 / 104k token / 52 事件 / 报告 2034 字，P00AF 行程 309 s / 6 请求 / 12 工具调用 / 86k token / 34 事件 / 报告 2962 字（P00AF 解码为主故障）；运维演练四项（端口不通 / 模型名错 / 云端未允许 / 输出目录在卷内）均预检拒绝。发现并修正：空 Mode 43/47 帧不再列为故障码。两次真跑 qwen3.5 未选择委托（委托证据 = 离线剧本 T-9）。

#### PROD-09 — 模型适配层与云端接口 — **DONE（2026-09-19，PR #248）**

**目标**：唯一本地 vLLM + Qwen3.6-27B 为默认，云端只作对比。
**方法**：ModelProfile 收敛 qwen 工具调用怪癖与 thinking 抑制；OpenRouter 接口用配置开关；~~嵌入模型走同一 vLLM~~（D4 划掉：V3 无嵌入用途，见 §4）；模型地址只在配置里。
**验收**：同一 golden 用例本地与云端各跑通一次；切换只改配置不改代码；qwen 无 thinking 泄漏到报告。

**开工前三轮审核（§2.5，`.lavish/prod09_plan_review.html`，2026-09-18）**：
- D1 vLLM 用**独立部署文件**（基础设施层，compose 项目 `stf_llm`），V3 只通过配置指向；V1/V2 应用改造仍归 #237。
- D2 两卡各占 **80%**（bake-off 90%），给手册转换留约 9 GB；验收实测"vLLM 常驻 + 转最大一本手册 + 同时跑诊断"。
- D3 云端对照验收时 **deepseek-v3.2 与 claude-sonnet-4.6 都跑**，默认配置 deepseek。
- D4 "嵌入模型走同一 vLLM"**划掉**（V3 没有嵌入用途），写回头条件。D5 本 ticket **不开手册图片**（Qwen3.6-27B 有视觉架构但未验证），写回头条件。
- 用户补充：先关思考求速度，另立待办验证开/关思考对时间与分数的影响（FM-45，§4）。
- 第一轮纠正的事实：服务器两张卡当时都是空的（Ollama 里已无驻留模型，"先卸载"不需要）；vLLM 启动配方只在 /tmp。第二轮 45 条失效模式（盲审 28 + 代码细读 17）全按推荐（处理 33 / 推迟 3 / 接受 9）；改盲审推荐 4 条（FM-2 / 3 / 17 / 19 改为接受）。第三轮 16 条测试 T-1 ~ T-16（CI 9 / 服务器 7），"处理"条目无一遗漏。

**实现**：
- `infra/docker-compose.vllm.yml` + `infra/vllm_ctl.sh`（start / wait / status / stop / logs / install-unit，项目名写死 `stf_llm`）+ `infra/stf-llm.service`（用户级 systemd，重启后自起）：bake-off 配方照抄，只改显存 0.80；`HF_HUB_OFFLINE=1`；健康检查起始宽限 900 s（冷启动实测 599 s）；重启 `on-failure:10`。
- `diagnosis/agent/model.py`：三档适配（`qwen-vllm` / `qwen-ollama` / `generic`）按地址 + 模型名自动选或 `STF_V3_LLM_PROFILE` 显式指定；vLLM 档每请求带 `chat_template_kwargs.enable_thinking=false`（不用 Pydantic AI 的通用思考档位——它会翻成 OpenAI 的 `reasoning_effort`）；思考不回灌、工具不加严格模式沿用；`ModelSource`（本地/云端、主机、档）随模型对象走，写进 `session_start`、`done` 与报告；云端密钥回退到 `OPENROUTER_API_KEY`；预算与请求参数**按档给默认值**，显式设置覆盖。
- `report.py` / `main_agent.py`：报告落文前剥离**带标签**的思考块并计数（`filter_hits / filter_removed_chars`）、统计思考字数、工具调用 XML 残留 → 部分报告 + limitation。
- `scripts/diagnose_once.py`：`--cloud`、`--model-wait-s`（本机默认等 600 s，云端 0 且不预热）、落文件名 `_local` / `_cloud`、末行打印档与来源。
- `scripts/deploy_check.sh` 第 9 项：主模型地址必须本机、`/models` 含配置名、30 s 内真生成一句、vLLM 容器不在 V1/V3 的 pod；只能 `LLM_CHECK=skip` 显式跳过并打印理由；失败时打印 nvidia-smi 占用。
- 默认配置改指 vLLM（`settings.py`、`docker-compose.v3.yml`）；CI 路径加入 vLLM 部署文件与运维手册；`.env.example` 加 V3 段。**无新迁移、无新接口。**
**测试**：`test_unit_model_profile` 17（T-1 选档表 + 别名 / 大小写 / 未知名告警、T-2 三档实际请求体、T-3 残留过滤 + done 计数、T-4 工具调用残留、T-5 密钥回退 / 云端守卫 / 假名、T-6 预算随档）、`test_scripts_diagnose_once` 6（T-7）、`test_infra_vllm_compose` 2（T-8）、`test_docs_prod09` 2（T-16）；既有 `test_unit_agent_contract` 默认模型名随之更新。**服务器验证（2026-09-19，PR #248 验证表）**：镜像内 178 passed + lint-imports 3 kept；deploy_check **9/9**（第 9 项 generated="ready"，vLLM 在 pod_stf_llm）、`LLM_CHECK=skip` 打印 SKIP 行、隔离 PASS（vLLM 常驻）。T-10 从零冷启动 **599 s**（宽限改 900 s）。T-13 同一 Hiace 日志（P00AF）三路：本地 Qwen3.6/vLLM **97 s / 22 请求 / 34 工具 / 31.8 万 token**（主动委托了两个子代理）、云端 deepseek-v3.2 66 s / 19 / 18 / 15.3 万、云端 kimi-k2.5 45 s / 5 / 10 / 4.3 万（Anthropic / OpenAI / Google 模型从服务器 403 地区限制，运行时收成 error + 部分报告）；同一 golden 手册问题（cross-001）本地 18 s / deepseek 33 s / kimi 160 s 各跑通。T-14 vLLM 轮 reasoning 事件 0、thinking_chars 0、报告无标签；云端落文件原始 VIN 0 次、假名存在、密钥 0 次。T-11 vLLM 常驻 + MinerU 转 1736 页手册（2630 s 成功）+ 同时诊断 98 s 完成，GPU 1 峰值 44.2 / 46 GB（余量 1.9 GB，运维手册记“更大手册先停 vLLM”）。T-12 停 vLLM → 测试容器只改三个环境变量指向 Ollama qwen3.5 → 一轮完整（57 s / 2 请求 / 3 工具，档自动为 qwen-ollama、墙钟回到 1200 s、thinking_chars 1286 但报告无标签）→ `ollama stop` 卸载（ollama ps 空）→ vLLM 重启 528 s 就绪 → 再跑一轮完整（34 s / 8 请求 / 11 工具，qwen-vllm 档，thinking 0）；全程零代码改动（git status 无修改文件）。 T-9 反向：vLLM 停机时第 9 项 FAIL 并点名“configured model not served (served: <endpoint down>)”，附两卡占用摘要；真跑脚本对停机端点 30 s 等待上限内退出码 4（34 s），不挂起。 发现并修正：Qwen3.6 关思考后偶尔以规划文字收尾 → 子代理一次性补问（b556a3a）；首轮 31.8 万 token → vLLM 档 token 门 100 万。

#### PROD-10 — Golden 评测移植与门槛 — **DONE（2026-09-24，PR #249）**

**目标**：V3 的每次改动都能用与 V2 同一把尺子衡量；换模型时 V2/V3 各跑一遍（D7）。
**方法**：eval runner 复制进 `stf_v3/evals/`，适配 Pydantic AI 调用签名；golden 数据文件复制；一条命令跑 manual lane 30 + OBD 15。
**验收**：V3 首次基线 ≥ 0.831（manual）/ ≥ 0.938（OBD）；低于基线的 PR 不得合并；评测报告落 `docs/evals/`。

**开工前三轮审核（§2.5，`.lavish/prod10_plan_review.html`，2026-09-19 ~ 09-23）**：
- D1 "不掉分" = **均值 ≥ 基线 − 0.03，且基线 ≥ 0.6 的题不跌破 0.4**；未过可重跑一次，最新两份里有一份过即可（FM-3 细化）。
- D2 **只对"动了 Agent 脑子"的 PR 强制**（受门槛路径写在 `stf_v3/evals/thresholds.yaml`：诊断运行时与工具、手册索引读取、日志读取、评测器与数据、settings、依赖锁、vLLM 部署文件）；证据 = 本 PR 新增的成绩单；CI 自动拦；`eval-exempt` 标签由用户加才可免。
- D3 本 ticket **顺手跑一遍 45 题"开思考"对照**，小助手层面结案；主 Agent 部分留待办。
- D4 首次基线不达标：先分清评测对接差异还是运行时差异并修，**最多两天**，仍不达标带数字与原因回来定。
- 验收细化（FM-11）：两遍均值 ≥ 线，且每遍不低于线 − 0.03。
- 第二轮 59 条失效模式（盲审 39 + 代码细读 20）全按推荐（处理 49 / 推迟 2 / 接受 8）；我改盲审推荐 6 条，其中 FM-14 改机制——评测在**一次性评测容器**里跑（同一镜像、手册卷只读、宿主目录做输出），不在 API 容器里 exec。第三轮 23 条测试 T-1 ~ T-23（纯 CI 12 / 涉及服务器 11），"处理"条目无一遗漏。
- 第一轮纠正的事实：V3 手册库的手册编号与 V2 **逐字相同**（PROD-06 连编号一起搬），PROD-08 FM-56 的"编号映射"不需要做映射，只加保险；OBD 路试日志是 74 KB 的 `yamaha_dual_road_test_20260508.csv`。

**实现**：
- `stf_v3/src/stf_v3/evals/`：`schemas / metrics / metrics_obd / judge_prompts / judge` **从 V2 逐字复制**（只改 import 路径与判卷密钥来源：V3 的 OpenRouter 密钥），连同 V2 的 205 条单元测试一起复制过来照样通过；`lanes`（V2 的"结果 → 打分文本"拼法逐字复制 + V3 执行层：每题新建独立运行依赖，车辆固定 Yamaha TRICITY155 + 测试假 VIN，OBD 读日志副本，走生产子代理与生产配置）；`orchestrator`（V2 流水线 + 单题硬上限 2 × 子代理墙钟 + 60 s、判卷失败延迟重判、逐题回调）；`scorecard`（与 V2 同格式 + 模式 / 完整性 / 有效性 / 白名单配置快照 / 密钥脱敏 / 精简版 / 原子写与逐题增量写）；`gate`（门槛、验收、基线、预算校准的纯函数，只依赖 PyYAML）；`summary`（Markdown 摘要：按 lane、题型、维度、耗时与预算截断、依赖图六题单列、逐题与 V2 参考的差值）；`runner` + `cli`（`python -m stf_v3.evals run / regrade / summary / calibrate / accept`）。
- 评测数据 `stf_v3/evals/`：两份锁定题目集（V2 `locked/mws150a_indexed.jsonl` 30 题、`locked/yamaha_road_test.jsonl` 15 题）+ 路试日志副本，均为已提交字节的原样副本；`MANIFEST.json` 记 sha256、V2 来源与复制日期，每次开跑先校验，`.gitattributes` 标 `-text` 防换行转换。
- `stf_v3/scripts/run_golden_eval.sh`：服务器一条命令——检查工作树干净且镜像提交号 = HEAD、一次只跑一轮（锁）、两卡显存与 Ollama 未驻留、vLLM 空闲并记抢占计数、V3 与 V2 手册副本哈希一致；然后起一次性容器（环境变量取自运行中的 API 容器、只传名字不传值，不传登录密钥、不碰 `STF_V3_LLM_ALLOW_CLOUD`），`tail -f run.log` 看进度，断开 SSH 或重新部署 V3 不影响它。
- `stf_v3/scripts/check_eval_gate.py` + CI job `eval-gate`（检出 PR head、完整历史）：没动受管路径 → 跳过；首次建立 / 基线重置 / 常规三种判定；成绩单必须"新鲜"（评测提交在分支历史里且之后没再动受管路径）；同一 PR 既动代码又动基线 → 红。
- 生产代码只加两处：子代理结果带 `nudged`（补问是否触发，供统计）；`STF_V3_LLM_THINKING`（默认关，只有开思考对照轮打开）。`tiktoken==0.14.0` 显式钉版并在镜像里预下载 `cl100k_base`（打分器按 token 计啰嗦度，不能静默退回"字数 ÷ 4"）。import-linter 新增"生产代码不许 import 评测包"。**无新迁移、无新接口。**

**测试**：`test_evals_v2_*`（V2 的打分 / 判卷 / 数据结构 / 流水线 / OBD 拼法测试 221 条原样复制）、`test_evals_equivalence`（T-1：从 V2 存档成绩单取 5 道手册题 + 3 道 OBD 题，V3 拼法逐字节复现存档输出，确定性维度与 V2 今日打分器逐位相同）、`test_evals_data`（T-2 / T-12）、`test_evals_gate_rules`（T-3 / T-9）、`test_evals_gate_script`（T-4，临时 git 仓库 10 种情形）、`test_evals_pipeline`（T-5 / T-6 / T-7 / T-10，真子代理 + 脚本化假模型整轮跑）、`test_evals_cli`（T-8 / T-11）、`test_docs_prod10`（T-13）。

**发现并修正（PROD-09 遗留）**：`infra/docker-compose.v3.yml` 的 `${STF_V3_OPENROUTER_API_KEY:-${OPENROUTER_API_KEY:-}}` 嵌套默认值被 podman-compose 1.5 读成"密钥 + `}`"，自 09-19 部署起 V3 容器里的 OpenRouter 密钥一直无效（401；宿主机 GPU worker 直接读 `infra/.env`，章节摘要不受影响）。判卷自检（T-17）发现；改为单层默认，新增测试禁止嵌套默认值。

**判卷漂移（T-17 发现）**：OpenRouter 上的 glm-5.1（现由 AtlasCloud 提供）会先做隐藏推理再作答；V2 照抄的判卷输出上限 2048 token 在长中文题上被推理耗尽，回复为空（finish=length，image-005 实需 3448）。V3 在命令行把上限提到 8192（`judge.py` 保持逐字复制），并把判卷模型 / 温度 / 上限记进每份成绩单；上限只决定判卷能否答完，原本 2048 内答完的分数不变。

**预算校准（T-18，两次）**：第一次（修复前，45 题、预算 ×2、六路并发）单题耗时 95 分位 98 秒、请求数 95 分位 13（最多 18，原上限 12 不够）。修掉下面三处运行时差异后小助手读到完整目录与章节，耗时与 token 翻倍，第二次校准（0 题截断、vLLM 0 次抢占）：耗时 95 分位 198 秒（最长 299）、请求数 95 分位 13（最多 17）、单题 token 95 分位 25.5 万。按 95 分位 × 1.5：vLLM 档子代理墙钟 180 → **300 秒**、请求上限 12 → **20 次**。一次完整诊断（主 Agent + 两个小助手）按此仍在主 Agent 的 100 万 token 门内。

**D4 归因与修正（V3 运行时与 V2 的三处差异）**：第一次校准手册 lane 只有 0.764——30 题中 20 题答案没有引用，但读的章节是对的。逐次记录模型请求后找到三处 V3 独有的问题：
1. **强制收尾那一轮答案丢失**：搜索工具全部收走后 Qwen3.6 仍想调工具，调用被写成文字（有时整份答案写成工具调用格式），vLLM 不解析、答案丢失。现在那一轮只给一个 `final_answer` 工具（摘要 + 引用对象），交卷后不再给任何工具；平时不给它——试过让它一直可用，模型读一两段就交卷，手册掉到 0.609。
2. **字符串引用被丢弃**：模型常把引用写成 `"<章节>: <原文>"`，照抄 V2 的解析器只收对象。现在接受，但只接受能对上读过章节的（`"Section '<标题>': …"` 这类对不上的自由文字丢弃，否则算错引用）。
3. **小助手的工具返回被截断**：V3 把主 Agent"每个工具返回 2000 token"的上限套到了小助手身上，MWS-150-A 的目录（约 4200 字）被截掉中间，模型看不到"火星塞的檢查"等章节而乱猜 / 答"找不到"；V2 的小助手从不截断。现在 2000 只用于主 Agent 直接调用，小助手只保留 16 000 token 的防失控上限（`STF_V3_SUBAGENT_TOOL_RESULT_MAX_TOKENS`）。
这同时了结 PROD-09 §5 暂缓的"结构化输出"（回头条件"PROD-10 基线后若引用抽取失误明显"已触发）：结构化只用在强制收尾那一轮。

**开工后决策（§9，2026-09-24）**：D5 **OBD 验收线以 V3 实测为准**——两遍 0.897 / 0.872，均值 0.885，验收线取 0.884；开发计划原写的 0.938 是 7 月旧模型 qwen3.5 在 V2 上的数字，OBD 提示词与工具与 V2 一致，差距集中在"日志无证据时新模型正确拒答却附引用"与两道综合题的结论；拒答方式的提示词适配另开小 PR（见 §4）。D6 **容差按 lane 分**：手册 0.03，OBD 0.06（15 题，同一代码三遍 0.897 / 0.872 / 0.844）。受管路径细化为影响分数的模块（`gate.py` / `summary.py` / `scorecard.py` 只判定或报告，不在其内）。

**服务器验证（2026-09-23 ~ 24，PR #249 验证表）**：按用户定的"评测时才开 vLLM、跑完就停"，vLLM 冷启动 570 秒，评测完已停机、显存回到空闲。
- **首次基线（T-19）**：手册 0.862 / 0.894（均值 **0.878**，线 0.831，通过）、OBD 0.897 / 0.872（均值 **0.885**，线 0.884，通过）；45/45 有效、0 评测超时、0 判卷失败；每轮约 15.5 分钟；手册按题型：查数值 0.827、查步骤 0.973、跨章节 0.881、依赖图 0.921、对抗 0.788。两份完整成绩单与摘要在 `docs/evals/`，门槛写入 `stf_v3/evals/thresholds.yaml`。
- **开思考对照（T-20，预算 ×2）**：手册 0.853、OBD 0.902，一轮 1525 秒（关思考约 930 秒）；分数无明显提升，**两个小助手维持关思考**。
- **云端对照（T-21，deepseek-v3.2，请求上限对齐 20）**：手册 **0.885**（与本地持平）；OBD 0.689 且 4 题因 deepseek 反复写错 `get_signal_stats` 的 `include` 参数而出错，本轮无效、只作参照（§4）；落文件只有假 VIN 的假名、无密钥。
- **门槛演示（T-22）**：临时分支故意改坏手册提示词、单独构建演示镜像跑一轮 → CI 门槛脚本判红（手册 0.837 < 0.878 − 0.03，procedural-004 从 0.994 跌到 0.215）；OBD 代码没改也判红（0.844），据此定 D6。演示分支、演示镜像已删，主分支零残留。
- **判卷自检（T-17）**：V3 复制的判卷器重判 V2 存档的 30 题 bake-off 成绩单，程序计算的 8 个维度差值 0，判卷均分 0.831 → 0.823，总分 0.884 → 0.885，0 判卷失败。
- **一条命令的前置检查（T-14）**：工作树有改动 / 镜像提交号不符 / 已有评测在跑 / vLLM 停着 / 模型名写错 → 各自拒跑（退出码 4，模型名写错 17 秒内）；V3 与 V2 两库的手册正文与索引 6 个文件逐字节一致。
- **不写坏东西（T-15）**：一整轮前后 V3 业务表行数完全一致（只有队列表随 worker 定时任务变化）；评测容器里手册卷只读、写入被拒；容器里没有登录密钥与"允许走云端"开关。
- **运维演练（T-16）**：第二遍跑到第 15 题时按手册重新部署 V3（api / worker 停了再起），评测容器不受影响、45 题跑完；重新部署后部署核验 9/9。
- **分词器（T-11）**：断网容器里 `cl100k_base` 照常加载（已烘进镜像）。
- **隔离与部署核验**：每次分支部署后部署核验 9/9、V1/V2 隔离比对 PASS。
- **费用**：本轮 9 次评测（含两次校准、开思考、云端、演示、判卷自检）判卷 + 云端合计约 3.1 美元。

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
| 通用日志格式层 | 第三种日志格式出现 | 针对该格式加解析器，不建通用层 | 否 | 暂缓（2026-09-17 首次触发：真机 "OBD Maximum" 格式按此加了 `maxlog` 解析器，PROD-07 D3；通用层仍不建） |
| 微服务 / K8s | 单机资源或团队规模撑不住 | 按模块边界拆 | 否（模块化单体即为此准备） | 暂缓 |
| 配额 / 限流 | 出现滥用或资源争抢 | 网关层限流 | 否 | 暂缓 |
| 模型微调 | golden 分数在提示词/工具优化后仍达不到目标 | 走 V1 §11 路线 | 否 | 暂缓 |
| V1/V2 数据迁移 | 明确需要老数据在 V3 可见 | 一次性导入脚本 | 否 | 暂缓（默认放弃） |
| 队列库升级（PROD-06 FM-17） | 升级 procrastinate 时 | 容器与宿主机 worker 同步升级到同一版本并跑它的库迁移；`install.sh` 版本校验改 pin | 否 | 暂缓（两边锁死 3.9.0） |
| 手册摘要出境开关（PROD-06 FM-22） | 正式对外 / 出现非内部用户前（与 VIN 模糊化同批） | 设置项切换章节摘要到本地 vLLM，或关闭摘要 | 否 | 暂缓（对外前必做） |
| CJK 手册质量门调参（PROD-06 FM-36） | 某本 CJK 手册反复过不了 I1–I8 | 单开小 ticket 调 MinerU / 门阈值，不改门 | 否 | 暂缓 |
| 第二位 manager（PROD-07 FM-6） | pilot 上线前 | 同一车队再发一枚 manager 邀请码给第二个人（`create_workshop.py --manager-codes 1`） | 否 | 暂缓 |
| 存储卷使用率巡检（PROD-07 FM-13） | 接入第三台车，或任一 V3 卷使用率过半 | 把 `stf_v3_obd_logs` / `stf_v3_manuals` 使用率加进日常巡检或 `deploy_check.sh` 报数 | 否 | 暂缓（deploy_check 第 8 项已看整盘余量） |
| **真机补录（PROD-07 FM-25）** | **截止 2026-10-01**（PR 开出日 2026-09-17 + 14 天） | 协作者按 `docs/v3_device_install.md` 装机并跑一趟；我们在 PROD-07 条目补"真机验收通过"。到期未跑 → PROD-07 标"部分验收"并在下次汇报提出，不阻塞 PROD-08 | 否 | 等待外部 |
| 并发诊断排队（PROD-08 FM-11） | PROD-11 建诊断队列时 | 诊断 job 并发设为 1（单模型串行），每次模型调用耗时已在事件里；出现真实并发需求再评估模型服务并发 | 否 | 暂缓 |
| golden 手册编号映射（PROD-08 FM-56） | PROD-10 开工时 | 核实：V3 库的手册编号与 V2 逐字相同（PROD-06 连编号一起搬），无需映射；评测开跑前核对 golden 引用的编号都在库里，缺失时映射到唯一匹配车型的手册并记入成绩单，否则拒跑 | 否 | 已处理（2026-09-23，PROD-10） |
| Ollama 常驻显存（PROD-08） | PROD-09 起 vLLM 前 | 服务器 Ollama `keep_alive=-1`，qwen3.5 常驻 57 GB；起 vLLM 前先卸载（`ollama stop` 或停容器） | 是 | 已处理（2026-09-19：vLLM 常驻是新常态；回退 Ollama 的互斥步骤见运维手册 §4.4） |
| 预算校准（PROD-09 FM-25） | PROD-10 有打分器与多用例后 | vLLM 档默认值只按 PROD-09 的三轮真跑定（带安全余量）；PROD-10 跑完 45 条 golden 后按 P95 耗时 / 请求数重定 | 否 | 已处理（2026-09-24：两次 golden 校准，小助手墙钟 300 秒、请求 20 次；主 Agent 无整诊断 golden，预算维持） |
| V1/V2 与 vLLM 抢显存（PROD-09 FM-27） | 若 V1/V2 容器重新启用 | 两者不能同时驻留模型：V1/V2 的 Ollama 模型与 vLLM 互斥（运维手册 §4.4）；应用改造归 #237 | 否 | 暂缓（V1/V2 将退役） |
| 开/关思考的影响（PROD-09 FM-45） | 小助手：PROD-10 已做；主 Agent：有整条诊断流的 golden 后 | 小助手层面 45 题对照：开思考 0.853 / 0.902、关思考 0.878 / 0.885，一轮慢约 60%，维持关思考；主 Agent 与完整诊断的对照等整诊断 golden | 否 | 部分处理（小助手结案，主 Agent 暂缓） |
| 嵌入模型（PROD-09 D4） | V3 引入向量检索时 | 在同一 vLLM 上加嵌入模型（或 Ollama `nomic-embed-text`）；本 ticket 划掉"嵌入模型走同一 vLLM" | 否 | 暂缓 |
| 手册图片（PROD-09 D5） | PROD-10 基线后若"依赖图"的题明显失分 | Qwen3.6-27B 有视觉架构；打开 `STF_V3_MANUAL_IMAGES_ENABLED` 前先验证 vLLM 带图参数与显存预留 | 否 | 暂缓（2026-09-24 基线：依赖图六题 0.921，手册 lane 第二高，未触发） |
| 评测进程持有生产凭据（PROD-10 FM-16） | 出现真实用户流量前 | 评测容器改用只读数据库角色、不注入与评测无关的变量（PROD-10 已做到：一次性容器、手册卷只读、不传登录密钥、行数前后一致） | 否 | 暂缓 |
| 评测与手册转换抢第二张卡（PROD-10 FM-38） | 多人使用或手册上传 > 每周一次 | 评测前置检查 GPU 队列空闲并在评测期间暂停手册入库；此前运维手册写明"评测期间不上传手册" | 否 | 暂缓 |
| OBD 拒答方式适配（PROD-10 D5） | PROD-10 合并后 | 根因：V2 提示词里"总结里提到的 DTC 都要引用"与"无证据拒答不附引用"两条规则在拒答时互相矛盾，Qwen3.6 选了前者（把看过的转速和两个解不开的雅马哈 DTC 当引用）；另一题（adversarial-002）一直分析含义不明的原始氧传感器信号直到用满请求。提示词改为：拒答规则明确优先、给出拒答样例、发现阶段后先判定能不能答（拒答最多再调一次统计）、问到具体 DTC 时引用它。单开小 PR，用新门槛衡量（预计收回 adversarial 题约 0.3） | 否 | 进行中（PR #250；同模型 API 预检 2026-09-24：OBD 0.948，三道拒答题 0.58 / 0.62 / 0.93 → 1.00 / 0.99 / 0.98、均不附引用，adversarial-002 请求数 20 → 9；门槛评测待 GPU 空闲） |
| vLLM 常驻与共享服务器（PROD-10） | PROD-11 上线前 | 2026-09-23 vLLM 被停机让给其他团队训练；现为"评测 / 验证时才开、用完即停"。产品诊断需要随时有模型：定常驻 / 按需拉起 / 与管理员约时段之一，并定部署核验第 9 项的常态 | 否 | 待决策 |
| OBD 请求上限尾部（PROD-10） | PROD-11 真实诊断里出现"用满 20 次请求" | adversarial-002 两遍基线都用满 20 次请求（一直在找不存在的证据）；届时评估提高上限或在提示词里给"找不到就停"的步数。D5 小 PR 已在提示词里加"发现阶段后先判定、拒答最多再调一次统计"，看它的门槛评测里 adversarial-002 是否还用满 | 否 | 暂缓（提示词侧已处理，待评测确认） |
| 云端对照 OBD 工具参数（PROD-10） | 需要云端 OBD 对照时 | deepseek-v3.2 反复写错 `get_signal_stats` 的 `include` 参数格式，4 题出错；放宽参数格式或在描述里给例子 | 否 | 暂缓 |
| 服务器升级到 4 × RTX PRO 6000（2026-09-24 获悉，约两个月内） | 升级排期确定时 | ① 与管理员谈卡的分配：能分到一两张专用卡则"vLLM 常驻"一项随之解决；② 升级前确认软件栈支持 Blackwell：驱动与 CUDA 12.8 以上、vLLM 镜像、宿主机 MinerU 的 PyTorch、V1/V2 的 Ollama、Podman 的 GPU 插件，预留 V3 停机窗口；③ 同一模型先在新卡上跑一轮 golden（vLLM 部署文件在门槛管理路径里，改它的 PR 会被强制评测），27B 可改为一卡一份以提高并发；④ 换更大的模型走基线重置 PR，先逐个候选跑 golden，再按新模型重校子代理预算 | 否 | 暂缓 |
| 前端语言 / 样式 | 前端归属确定后 | 由前端需求文档定 | 否 | 等待外部 |

## 5. 待决策

全部 9 项已拍板（2026-09-08），结果见 §0.1；存档 `.lavish/v3_dev_plan_decisions.html`。

新增开放项：**前端实现归属**（学生 vs 我们）→ 下次会议；在此之前 M5 不排期。

## 6. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-07 | 初稿：7 个里程碑、15 张 PROD ticket（PROD-01 到验收级，其余到目标/方法/验收层）、非目标清单草案、9 项待决策 |
| v1.12 | 2026-09-24 | PROD-10 后续小 PR（D5，PR #250）：OBD 小助手提示词适配新模型的拒答方式——拒答规则明确优先于"总结里的 DTC 都要引用"、给出拒答样例、发现阶段后先判定能否回答（拒答最多再调一次统计）、问到具体 DTC 时引用它；新增单元测试 6 条；门槛评测结果见 §4 该行。§4 新增"服务器升级到 4 × RTX PRO 6000"回头条件，"OBD 请求上限尾部"记提示词侧处理。修正 CLAUDE.md 与 V3 部署文件注释里的迁移命令：迁移服务带 `migrate` profile，原写法在 podman-compose 1.5 下只打警告不执行（缺 `--profile migrate`）。运维手册 v0.6（基线重置示例的 OBD 验收线改为 0.884）。显卡被占时的同模型 API 预检：模型适配档新增 `qwen-openrouter`（同一 Qwen3.6-27B 经 OpenRouter，用 OpenRouter 的 `reasoning.enabled=false` 关思考、可固定托管方、预算同本地），评测命令行加 `--cloud-model` / `--cloud-provider`；预检成绩单记为云端、不计入门槛（设计文档 v1.13，图无需改动） |
| v1.11 | 2026-09-24 | PROD-10 DONE（PR #249，交付核对板通过；服务器磁盘清理旧镜像后可用 122 GB）：golden 评测移植进 `stf_v3/evals/`（V2 打分器 / 判卷 / 拼法逐字复制 + V3 执行层）、题目与日志副本带哈希清单、一次性评测容器脚本、CI `eval-gate`；V3 首次基线手册 0.878 / OBD 0.885。修掉 V3 小助手三处与 V2 的差异（强制收尾答案丢失 → 只在那一轮给 `final_answer`；字符串引用被丢；工具返回被截断），预算重校为墙钟 300 秒 / 请求 20 次；顺手修 PROD-09 的密钥嵌套默认值与判卷输出上限。三轮审核：D1–D4 + FM-3 / FM-11 细化；59 条 FM；23 条测试；开工后 D5（OBD 线用 V3 实测）/ D6（容差按 lane：0.03 / 0.06）。§4：FM-56 编号映射、FM-25 预算校准结案，FM-45 小助手结案，手册图片填入 0.921；新增 OBD 拒答适配、vLLM 常驻与共享服务器、OBD 请求上限尾部、云端 OBD 参数、FM-16、FM-38。设计文档 v1.12（§1.7 评测门槛落地、§1.4 vLLM 按需；图无需改动） |
| v1.10 | 2026-09-19 | PROD-09 DONE（vLLM 正式化：独立部署文件 + 控制脚本 + systemd 单元，显存 0.80；三档模型适配 `qwen-vllm / qwen-ollama / generic`，思考关 + 残留过滤 + 工具调用残留检查；云端对照口 `--cloud`（密钥回退 OPENROUTER_API_KEY）；预算按档默认；部署核验第 9 项；默认配置改指 vLLM）。三轮审核：D1–D5 + 思考待办；45 条 FM 全按推荐（改盲审 4 条为接受）；16 条测试。§4 新增 FM-25 / FM-27 / FM-45 / D4 / D5 五条回头条件，Ollama 常驻项关闭。设计文档同步至 v1.11（§1.4 LLM 供给落地；架构图已含 vLLM，无需改动） |
| v1.9 | 2026-09-17 | PROD-08 DONE（Pydantic AI 2.44 运行时：三格式直读器、12 个工具、两个子代理以工具形式挂载、10 种事件、四道预算闸门 → 部分报告、压缩 / 记忆、手册子代理护栏、报告引用抽取、唯一模型来源 + 云端守卫、`diagnose_once.py` 真跑脚本；无新迁移、无新接口）。三轮审核：D1 冒烟用现有 Ollama、D2 默认繁体中文；57 条 FM 全按推荐（FM-26 按 V2 锁定决定接受）。§4 新增 FM-11 并发排队、FM-56 golden 编号映射、Ollama 常驻显存。设计文档同步至 v1.10（§1.4 运行时口径落地；架构图已含 Pydantic AI 与子代理，无需改动） |
| v1.8 | 2026-09-17 | PROD-07 DONE（**D3：真机日志是 "OBD Maximum" 格式，V3 针对性加 `maxlog` 解析器 + 迁移 `b2c3d4e5f6a7`**；Jetson 上传器双推：V2 腿不变、V3 腿由设备 env 文件开关；重试 + 待传目录 + `--drain` 退避补传 + 拒收目录 + 401 留待传；`--self-check`；退出码 0/1/2；V3 拒收结构化日志；`onboard_first_workshop.py` 建档脚本；装机手册 `docs/v3_device_install.md`；CI `uploader` job py3.8/3.11；冒烟 42 步）。三轮审核：D1 车队 "PolyU STF 实验车队" / 负责人 manager / Perry 技师，D2 服务器等价验证即完成、真机作补录；36 条 FM 全按推荐。§4 新增 FM-6 第二 manager、FM-13 卷巡检、FM-25 真机补录截止 2026-10-01。设计文档同步至 v1.9（§1.8 设备上传口径补充；无架构变化，图无需改动） |
| v1.7 | 2026-09-14 | PROD-06 DONE（`knowledge` 模块：手册库接口、一步到位入库流水线、gpu 队列任务、宿主机 GPU worker、V2 知识库搬迁、队列运维演练四项 + stalled 回收）。三轮审核决策：入库一步到位、marker 退出 V3、宿主机 worker 用 uv 装 3.11 直接跑 V3 代码；§4 新增 3 条推迟项（FM-17 队列库升级、FM-22 摘要出境开关、FM-36 CJK 质量门）。设计文档同步至 v1.8（§1.4 队列口径、§1.8 知识库口径；无架构变化，图无需改动） |
| v1.6 | 2026-09-14 | 新增 §2.5 **开工前三轮审核流程**（高层介绍 → 盲审失效模式 → 按失效模式推导测试 → 才开工；计划页不引用代码路径；核对板加 FM → T → 结果追溯）；技能 `.claude/skills/ticket-kickoff/SKILL.md`。自 PROD-06 起适用。无架构变化，图无需改动 |
| v1.5 | 2026-09-13 | PROD-05 DONE（`ingest` 模块：成员 / 设备上传、格式嗅探、sha256 去重、文件存储卷、VIN 核对）。开工前决策 D1–D3：不碰真 Jetson、**VIN 不一致改为拒收**（验收条款随之改写）、备份等 PROD-15。§4 无新增暂缓项（Jetson 双推 = PROD-06 后小 ticket，见 PROD-05 决策）。设计文档同步至 v1.7（§1.8 `obd_logs` 口径改为拒收；无架构变化，图无需改动） |
| v1.4 | 2026-09-13 | PROD-04 DONE（V3 常驻 + 对外可达 + CI + 部署核验；三个部署坑记入 CLAUDE.md）。设计文档同步至 v1.6（nginx 路由属部署拓扑，图无需改动） |
| v1.3 | 2026-09-11 | PROD-03 DONE（FastAPI 骨架、fastapi-users + 邀请码、workshop / 车档 / 设备凭证 API、OpenAPI 契约 v1 `docs/api/v3_openapi.json`）；新增 §2.4 测试策略（八类测试、CI 两层）；DoD 加"测试补齐 + 挑战清单"；新增可复用脚本 `smoke_e2e.py`、`isolation_check.sh`、`export_openapi.py`、`create_workshop.py`。设计文档同步至 v1.5（无架构变化，图无需改动） |
| v1.2 | 2026-09-11 | PROD-02 开工（分支 `prod-02-db-migration`）：**Stage 1 去掉 pgvector / rag_chunks / 向量化**（§1.1、PROD-02、PROD-06 改写；§4 新增"向量检索 / RAG"暂缓项与回头条件）。设计文档同步至 v1.4，架构图已同步 |
| v1.1 | 2026-09-11 | PROD-01 DONE：代码设计蓝图 v1.0 评审通过；M0 状态更新；§2.1 M0 改为"已完成"。设计文档同步至 v1.3（机制层表 `vehicle_devices` 等），架构图已同步 |
| **v1.0** | 2026-09-08 | **定稿**：D1–D9 全部拍板并并入（§0.1）；所有权挂 workshop、VIN 身份 / 车牌标签、同仓新目录 + 可迁出/不绑死约束与自动化检查、fastapi-users、procrastinate `jobs`、前端暂缓（M5 待定 · 外部）、唯一 vLLM、数据归属不变量入 DoD；M1 起交付 OpenAPI 契约；PROD-06 增加队列运维演练；§4 改为"暂缓事项与回头条件"（逐项触发条件 / 动作 / 状态）。设计文档同步至 v1.2，架构图已同步 |
