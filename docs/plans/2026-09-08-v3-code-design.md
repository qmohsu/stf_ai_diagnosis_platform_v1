# V3 代码设计蓝图（PROD-01）

| 字段 | 值 |
|---|---|
| Ticket | PROD-01 |
| 依据 | `docs/v3_design_doc.md` v1.2 · `docs/v3_dev_plan.md` v1.0 |
| 状态 | **v1.0 已通过**（Lavish 评审 2026-09-11，`.lavish/v3_code_design_review.html`）；进入 M1 / PROD-02 |
| 作者 | Xiangzhu Yan |
| 日期 | 2026-09-08（评审通过 2026-09-11） |

**评审时确认的两处产品层默认**（用户未反对，按此执行）：
① 角色权限：technician 能做全部日常操作（建车档、上传、发起诊断、看报告）；
manager 独占删车、发邀请码、发设备凭证、传 / 删手册。② 登录用**用户名**而非邮箱
（`users.username` 唯一；`email` 可空，仅作联系方式）；忘记密码 = manager 发新邀请码
重建账号，pilot 期不做邮件找回。

**怎么读**：评审只需要看 §3（API 契约）和 §11（底座验证表）。其余各节是给
PROD-02 ~ PROD-11 施工用的，细节不必逐条拍，看到不对的圈选批注即可。

**设计原则（来自已拍板决策）**：一个 workshop 拥有车（D1）；VIN 是身份、车牌是
标签（D2）；`stf_v3/` 自包含、可迁出、不与 V1/V2 绑死（D3）；fastapi-users（D4）；
procrastinate（D5）；后端从 M1 起交付 OpenAPI 契约（D6）；唯一本地 vLLM（D7）；
每笔原始数据 100% 绑定车档（D8）。

---

## 1. 目录布局与依赖

### 1.1 目录

> **PROD-02 实施注记（2026-09-11）**：实际采用 **src 布局** `stf_v3/src/stf_v3/…`
> （比下图少一层 `app/`），模块导入路径为 `stf_v3.auth`、`stf_v3.jobs.app` 等；
> `metadata.py` 汇总全部模型供 Alembic 使用。Stage 1 **不装 pgvector、不建
> rag_chunks**（见 §4 注记）。

```
stf_v3/                          # 后端，自包含（D3 约束 A）
  pyproject.toml                 # 自己的依赖清单（不复用 diagnostic_api/requirements.txt）
  Dockerfile                     # python:3.11-slim
  alembic.ini
  alembic/                       # 自己的迁移链，初始迁移 = §4 全部 DDL
  src/stf_v3/
    main.py                      # FastAPI 装配：挂路由、structlog、/health、OpenAPI 导出（PROD-03）
    settings.py                  # pydantic-settings；全部外部连接只在这里（D3 约束 B）
    db.py                        # SQLAlchemy 2.0 async engine/session（postgresql+psycopg）+ Base
    metadata.py                  # 注册全部模型；EXPECTED_TABLES
    auth/                        # fastapi-users 装配 + 邀请码注册
    workshops/                   # workshops、memberships、can_access_vehicle()
    vehicles/                    # 车档 CRUD、vehicle_devices（D8 机制）
    ingest/                      # 上传薄层、格式嗅探、文件存储、VIN 核对
      parsers/                   # jetson_tsv.py（复制自 obd_agent/log_parser.py）、yamaha_csv.py
    knowledge/                   # manuals 模型、手册转换流水线、marker_convert（复制）；无向量化
    diagnosis/                   # 会话/消息/报告/审计模型、诊断 job、SSE、回放
      agent/                     # Pydantic AI：主 Agent、manual 子 Agent、toolsets、模型适配
      tools/                     # 复制自 V2 harness_tools（见 §7）
    jobs/                        # procrastinate app、任务注册、队列、定时任务
  scripts/
    create_workshop.py           # 建 workshop + 首批邀请码（Stage 1 无管理 UI）
    copy_knowledge_from_v2.py    # 一次性拷贝 manuals/rag_chunks
    export_openapi.py            # 生成 docs/api/v3_openapi.json
    gpu_worker.sh                # 宿主机 GPU worker 启动（消费 gpu 队列）
    check_portable.sh            # D3 验收 ①：拷到空目录测试全绿
    check_unbound.sh             # D3 验收 ②：删掉老目录后 V3 照常构建
  evals/                         # 复制自 diagnostic_api/tests/harness/evals（含 golden 数据副本）
  tests/                         # 镜像 app/ 结构
obd-ui-v3/                       # 前端（归属待定，目录先占位 + README 指向 OpenAPI）
infra/
  docker-compose.v3.yml          # stf-v3-api、stf-v3-worker；独立文件（D3 约束 B）
  docker-compose.v3.polyu.yml    # Podman host-network 覆盖
  init-scripts/20-create-stf-v3-db.sql   # 同一 Postgres 实例新建 database stf_v3
docs/api/v3_openapi.json         # CI 生成并 diff（DoD）
```

**禁止**：`stf_v3/` 内任何文件 `import diagnostic_api`、`import obd_agent`、
`from app.` 指向老代码。用 `import-linter` 在 CI 里强制（§10）。

### 1.2 依赖（pin 到当前版本，M1 开工时复核）

| 用途 | 包 | 版本 | 备注 |
|---|---|---|---|
| Web | fastapi | 0.141.1 | uvicorn[standard] |
| 校验/配置 | pydantic 2 / pydantic-settings | 随 fastapi | |
| Agent 运行时 | pydantic-ai | 2.41.0 | OpenAI-compatible provider 指向 vLLM |
| Auth | fastapi-users[sqlalchemy] | 15.0.5 | JWT + Bearer transport |
| ORM | sqlalchemy[asyncio] | 2.0.x | `postgresql+psycopg` 异步方言 |
| DB 驱动 | psycopg[binary,pool] | 3.x | **一个驱动同时服务 SQLAlchemy 与 procrastinate** |
| ~~向量~~ | ~~pgvector~~ | — | Stage 1 不用（2026-09-11）；相似案例时再加 |
| 迁移 | alembic | 1.14.x | |
| 队列 | procrastinate | 3.9.0 | `PsycopgConnector` |
| 日志 | structlog | 24.x | JSON 到文件 |
| HTTP | httpx | 0.27.x | |
| 架构守护 | import-linter | 2.x | CI 检查禁止 import |
| 测试 | pytest, pytest-asyncio, pydantic-ai TestModel | | 离线 |

Python 3.11（与 V2 镜像一致）。宿主机 GPU worker 额外装 `marker-pdf` + torch
（与现状相同，见 §6.4）。

---

## 2. 模块图与依赖方向

```
            ┌──────────┐   ┌────────────┐   ┌───────────┐
            │  auth    │   │ workshops  │   │ knowledge │
            └────┬─────┘   └─────┬──────┘   └─────┬─────┘
                 │  users        │ can_access_     │ manuals / rag_chunks
                 │               │ vehicle()       │ manual_fs
                 ▼               ▼                 │
            ┌──────────────────────────┐           │
            │        vehicles          │           │
            │  vehicles · vehicle_devices          │
            └────┬────────────┬────────┘           │
                 │            │                    │
                 ▼            ▼                    ▼
            ┌─────────┐  ┌──────────────────────────────┐
            │ ingest  │  │          diagnosis           │
            │ obd_logs│◀─│ conversations·messages·      │
            └────┬────┘  │ reports·audit_events · agent │
                 │       └──────────────┬───────────────┘
                 ▼                      ▼
            ┌──────────────────────────────────────┐
            │                 jobs                 │  ← 所有模块只能"投任务"，
            │  procrastinate app · 任务注册 · 定时 │    任务函数本身在各自模块里
            └──────────────────────────────────────┘
```

**允许的依赖方向（import-linter 合同）**：

| 模块 | 可以 import | 不可以 import |
|---|---|---|
| auth | db, settings | 其他任何业务模块 |
| workshops | auth（User 类型） | vehicles 及以下 |
| vehicles | workshops, auth | ingest, diagnosis |
| ingest | vehicles, jobs（投任务） | diagnosis |
| knowledge | jobs（投任务） | vehicles, diagnosis |
| diagnosis | vehicles, ingest, knowledge, jobs | — |
| jobs | 无业务模块（任务通过注册函数反向挂入） | — |

**服务接口约定**：每个模块暴露 `service.py`（纯 Python 函数，接受 `AsyncSession`）
和 `router.py`（HTTP 薄层）。跨模块调用只走 `service.py`，不直接查别的模块的表。

**鉴权唯一入口**：`workshops/service.py::can_access_vehicle(session, user, vehicle_id) -> Vehicle`
—— Stage 1 实现 = "vehicle.workshop_id 在用户的 memberships 里"；找不到或无权一律 404
（不泄露车辆存在性）。全后端 grep 只允许这一处做权限判断（PROD-03 验收）。

---

## 3. API 契约 v1（Stage 1 全部端点）

前缀 `/v3`。鉴权：Bearer JWT（fastapi-users）；设备上传用 `X-Device-Token`。
所有错误体统一 `{"detail": str, "code": str}`。列表接口统一 `?limit=&offset=`。

### 3.1 认证与用户（auth）

| 方法 | 路径 | 谁能调 | 说明 |
|---|---|---|---|
| POST | `/v3/auth/register` | 任何人 | body `{username, password, invite_code, email?}`；邀请码有效 → 建 user + membership（workshop 与 role 来自邀请码）+ 核销；无效/已用 → 422 `invite_code_invalid`；用户名重复 → 409 |
| POST | `/v3/auth/login` | 任何人 | form `username` / `password` → `{access_token, token_type}`（fastapi-users 标准 transport，登录字段为 username） |
| POST | `/v3/auth/logout` | 登录用户 | |
| GET | `/v3/users/me` | 登录用户 | `{id, email, memberships:[{workshop_id, workshop_name, role}]}` |
| POST | `/v3/workshops/{wid}/invite-codes` | 该 workshop 的 manager | body `{role, count}` → 明文码列表（只此一次可见） |
| GET | `/v3/workshops/{wid}/invite-codes` | manager | 状态（未用 / 已用 by 谁） |

Workshop 本身由 `scripts/create_workshop.py` 建（Stage 1 无超级管理员 UI）。

### 3.2 workshop 与车档（workshops / vehicles）

| 方法 | 路径 | 谁能调 | 说明 |
|---|---|---|---|
| GET | `/v3/workshops` | 登录用户 | 我所属的 workshop 列表 |
| GET | `/v3/workshops/{wid}/members` | 成员 | |
| GET | `/v3/workshops/{wid}/vehicles` | 成员 | 车档列表；`?q=` 模糊匹配车牌 / VIN / 昵称 |
| POST | `/v3/workshops/{wid}/vehicles` | 成员 | body `{vin, manufacturer, model, plate?, nickname?}`；VIN 17 位校验 + workshop 内唯一 → 409 `vin_exists` |
| GET | `/v3/vehicles/{id}` | 成员 | 车档 + 统计（日志数、最近诊断） |
| PATCH | `/v3/vehicles/{id}` | 成员 | 可改 `plate, nickname, manufacturer, model`；**VIN 不可改**（改 = 删了重建） |
| DELETE | `/v3/vehicles/{id}` | manager | 软删除；历史会话保留 |
| POST | `/v3/vehicles/{id}/devices` | manager | body `{label}` → `{device_id, token}`（token 只返回一次；见 §5） |
| GET | `/v3/vehicles/{id}/devices` | 成员 | 设备列表（label、last_seen_at、revoked） |
| DELETE | `/v3/devices/{id}` | manager | 吊销 |

### 3.3 上传（ingest）

| 方法 | 路径 | 谁能调 | 说明 |
|---|---|---|---|
| POST | `/v3/vehicles/{id}/logs` | 成员 | multipart `file`（或 raw body + `X-Filename`）；嗅探格式 ∈ {tsv, yamaha} 否则 422 `unsupported_format`；sha256 重复 → 200 返回已有记录 + `duplicate: true`；成功 201 `{log_id, format, size_bytes, vin_from_log, vin_mismatch}` |
| POST | `/v3/ingest/device` | 持有效 `X-Device-Token` 的设备 | 同上，车由 token 决定（§5）；token 无效/吊销 → 401 |
| GET | `/v3/vehicles/{id}/logs` | 成员 | 列表 |
| GET | `/v3/logs/{id}` | 成员 | 元数据 |
| GET | `/v3/logs/{id}/raw` | 成员 | 原始字节下载 |

上传**不**触发诊断（设计文档 §1.2）。

### 3.4 诊断（diagnosis）

| 方法 | 路径 | 谁能调 | 说明 |
|---|---|---|---|
| POST | `/v3/vehicles/{id}/diagnose` | 成员 | body `{obd_log_id}` → **202** `{conversation_id}`；同一事务：插会话（status=queued）+ `run_diagnosis.defer()` |
| GET | `/v3/vehicles/{id}/conversations` | 成员 | 历史会话列表（status、created_at、report 摘要） |
| GET | `/v3/conversations/{id}` | 成员 | 会话详情 + messages |
| GET | `/v3/conversations/{id}/events?after_seq=N` | 成员 | `Accept: text/event-stream` → SSE 从 N+1 起推送直至 `done`/`error`；否则 JSON 数组（回放） |
| GET | `/v3/conversations/{id}/report` | 成员 | `{content_md, citations, model, created_at}`；未完成 404 `report_not_ready` |
| POST | `/v3/conversations/{id}/cancel` | 成员 | 请求取消（job 侧协作式检查） |

### 3.5 知识库（knowledge）

| 方法 | 路径 | 谁能调 | 说明 |
|---|---|---|---|
| GET | `/v3/manuals` | 成员 | 公共知识库列表（status、manufacturer、model、chunk_count） |
| POST | `/v3/manuals` | manager | multipart PDF + `manufacturer, model, factory_code?` → 202 `{manual_id, job_id}` |
| GET | `/v3/manuals/{id}` | 成员 | 含进度 `pages_processed/pages_total/pages_phase` |
| DELETE | `/v3/manuals/{id}` | manager | 级联删 chunks + 文件 |
| GET | `/v3/manuals/{id}/assets/{path}` | 成员 | 手册 Markdown 内引用的图片（报告引用可点） |

### 3.6 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{status, db, queue_backlog}` |
| GET | `/v3/openapi.json` | FastAPI 自动；CI 导出到 `docs/api/v3_openapi.json` 并 diff |
| GET | `/v3/jobs/{id}` | manager；procrastinate job 状态（排障用） |

### 3.7 SSE 事件类型（= `audit_events.event_type`，一份事件两个消费者）

沿用 V2 `EventType` 枚举原名，**不再做 V2 那种 wire 层改名**（session_start→status 之类）：

| event_type | payload 要点 | 何时 |
|---|---|---|
| `session_start` | `{vehicle_id, obd_log_id, model, vehicle:{manufacturer, model, vin}}` | job 开始 |
| `reasoning` | `{text}` | 模型思考片段 |
| `token` | `{text}` | 报告正文流；**落库时按段聚合**（每 N 秒或每段落一行），不逐 token 存 |
| `tool_call` | `{tool, args, call_id}` | 含子代理委托（`tool: "ask_manual_agent"`） |
| `tool_result` | `{tool, call_id, elapsed_ms, result_preview}` | 结果截断 2 KB |
| `hypothesis` | `{text}` | 预留 |
| `context_compact` | `{before_tokens, after_tokens}` | 压缩触发 |
| `diagnosis_done` | `{report_id, total_tokens, elapsed_s}` | 报告写入后 |
| `done` | `{}` | 流结束 |
| `error` | `{stage, message}` | 任一步失败；会话 status=error |

SSE 帧格式：`event: <event_type>\nid: <seq>\ndata: <json>\n\n`；客户端断线重连带
`Last-Event-ID` = 上次 seq，服务端从 `after_seq` 继续。心跳 `: keepalive` 每 15 s。

---

## 4. DDL（初始迁移，database `stf_v3`）

```sql
-- No extensions needed: gen_random_uuid() is built into PG13+; no pgvector.

-- ---------- auth ----------
CREATE TABLE users (                         -- fastapi-users 基础字段 + username
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username      VARCHAR(50) NOT NULL UNIQUE,   -- 登录名（评审确认②）
  email         VARCHAR(320) UNIQUE,           -- 可空，仅联系方式，不发邮件
  hashed_password VARCHAR(1024) NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  is_superuser  BOOLEAN NOT NULL DEFAULT FALSE,
  is_verified   BOOLEAN NOT NULL DEFAULT TRUE,   -- 邀请码即验证
  display_name  VARCHAR(100),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- workshops ----------
CREATE TABLE workshops (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          VARCHAR(200) NOT NULL UNIQUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workshop_id   UUID NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
  role          VARCHAR(20) NOT NULL CHECK (role IN ('manager','technician')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, workshop_id)
);
CREATE INDEX ix_memberships_workshop ON memberships(workshop_id);

CREATE TABLE invite_codes (
  code          VARCHAR(32) PRIMARY KEY,          -- 明文码（随机 16 字节 base32），仅 pilot
  workshop_id   UUID NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
  role          VARCHAR(20) NOT NULL CHECK (role IN ('manager','technician')),
  created_by    UUID REFERENCES users(id),
  used_by       UUID REFERENCES users(id),
  used_at       TIMESTAMPTZ,
  expires_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- vehicles ----------
CREATE TABLE vehicles (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workshop_id   UUID NOT NULL REFERENCES workshops(id),
  vin           VARCHAR(17) NOT NULL CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),  -- 身份（D2）
  plate         VARCHAR(20),                     -- 标签，可空可改，无唯一约束（D2）
  manufacturer  VARCHAR(100) NOT NULL,
  model         VARCHAR(100) NOT NULL,
  nickname      VARCHAR(100),
  deleted_at    TIMESTAMPTZ,                     -- 软删除
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_vehicles_workshop_vin ON vehicles(workshop_id, vin)
  WHERE deleted_at IS NULL;
CREATE INDEX ix_vehicles_workshop ON vehicles(workshop_id);

CREATE TABLE vehicle_devices (                   -- D8 机制（§5）
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vehicle_id    UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  label         VARCHAR(100) NOT NULL,           -- 如 "Jetson #1"
  token_hash    CHAR(64) NOT NULL UNIQUE,        -- sha256(token)，明文只在创建时返回一次
  created_by    UUID REFERENCES users(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ,
  revoked_at    TIMESTAMPTZ
);

-- ---------- ingest ----------
CREATE TABLE obd_logs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vehicle_id    UUID NOT NULL REFERENCES vehicles(id),   -- 数据归属不变量（D8）：NOT NULL
  sha256        CHAR(64) NOT NULL,
  raw_path      VARCHAR(500) NOT NULL,
  original_filename VARCHAR(255),
  source        VARCHAR(10) NOT NULL CHECK (source IN ('web','device')),
  device_id     UUID REFERENCES vehicle_devices(id),
  uploaded_by   UUID REFERENCES users(id),
  format        VARCHAR(10) NOT NULL CHECK (format IN ('tsv','yamaha')),
  size_bytes    BIGINT NOT NULL,
  vin_from_log  VARCHAR(17),                     -- 日志里读到的 VIN（Jetson TSV 有，Yamaha 无）
  vin_mismatch  BOOLEAN NOT NULL DEFAULT FALSE,  -- vin_from_log 非空且 ≠ 车档 VIN
  recorded_start TIMESTAMPTZ,                    -- 日志首/末时间戳（嗅探时读）
  recorded_end   TIMESTAMPTZ,
  uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (vehicle_id, sha256)
);
CREATE INDEX ix_obd_logs_vehicle_time ON obd_logs(vehicle_id, uploaded_at DESC);

-- ---------- diagnosis ----------
CREATE TABLE diagnosis_conversations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vehicle_id    UUID NOT NULL REFERENCES vehicles(id),
  obd_log_id    UUID NOT NULL REFERENCES obd_logs(id),
  created_by    UUID NOT NULL REFERENCES users(id),
  status        VARCHAR(10) NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','done','error','cancelled')),
  stage         VARCHAR(4) NOT NULL DEFAULT 's1',
  job_id        BIGINT,                          -- procrastinate job id（排障）
  model         VARCHAR(100),
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  error_message TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_conversations_vehicle_time ON diagnosis_conversations(vehicle_id, created_at DESC);

CREATE TABLE messages (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES diagnosis_conversations(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,
  role          VARCHAR(10) NOT NULL CHECK (role IN ('system','user','assistant','tool')),
  content       JSONB NOT NULL,                  -- Pydantic AI ModelMessage 原样序列化
  token_usage   JSONB,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
);

CREATE TABLE reports (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL UNIQUE REFERENCES diagnosis_conversations(id) ON DELETE CASCADE,
  content_md    TEXT NOT NULL,
  citations     JSONB NOT NULL DEFAULT '[]',     -- [{doc_id, section, manual_id, path}]
  model         VARCHAR(100) NOT NULL,
  total_tokens  INTEGER,
  elapsed_s     NUMERIC(8,2),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (                      -- append-only 黑匣子
  id            BIGSERIAL PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES diagnosis_conversations(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,
  event_type    VARCHAR(30) NOT NULL,            -- 与 SSE 同名（§3.7）
  payload       JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
);
-- 只插不改不删：应用层无 UPDATE/DELETE；DB 角色 stf_v3_app 对此表只授 INSERT/SELECT。

-- ---------- knowledge（按 V2 schema 复制，去掉 user_id 归属 → 公共库） ----------
CREATE TABLE manuals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  uploaded_by UUID REFERENCES users(id),
  filename VARCHAR(500) NOT NULL,
  file_hash VARCHAR(64) NOT NULL UNIQUE,
  manufacturer VARCHAR(100) NOT NULL,
  vehicle_model VARCHAR(100) NOT NULL,
  factory_code VARCHAR(100),
  status VARCHAR(20) NOT NULL DEFAULT 'uploading'
    CHECK (status IN ('uploading','queued','converting','chunking','embedding','ingested','failed')),
  file_size_bytes INTEGER NOT NULL,
  page_count INTEGER, section_count INTEGER, language VARCHAR(20), converter VARCHAR(100),
  error_message TEXT, md_file_path VARCHAR(500), pdf_file_path VARCHAR(500),
  chunk_count INTEGER, pages_processed INTEGER, pages_total INTEGER, pages_phase VARCHAR(50),
  warnings JSONB, job_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- rag_chunks / pgvector: REMOVED from Stage 1 (2026-09-11).  The agent reads
-- manual Markdown via manual_fs; vectors return with 相似案例 (dev plan §4).
-- manuals.status values are therefore: uploading, queued, converting,
-- ingested, failed (no 'chunking' / 'embedding').

-- ---------- jobs ----------
-- procrastinate 自带 schema（procrastinate_jobs / _events / _periodic_defers）：
-- 由 `procrastinate --app stf_v3.app.jobs.app schema --apply` 在同一初始迁移里调用。
```

与设计文档 §1.8 的差异（本蓝图新增，属机制层）：`vehicle_devices`（D8）、
`obd_logs.vin_from_log / vin_mismatch / device_id / source`、`diagnosis_conversations.job_id /
cancel_requested`、`manuals.job_id`、`invite_codes.workshop_id / role`。

---

## 5. D8 机制：设备如何表明"我是哪台车"

**要求**：每笔 Jetson 上传 100% 绑定唯一车档；转存不丢归属；诊断时凭车档取信息。

**做法：每台车发设备凭证**

1. manager 在车辆页（或 Stage 1 用 curl）调 `POST /v3/vehicles/{id}/devices`，得到一枚
   token（32 字节随机，base64url），**只显示一次**；服务器只存 `sha256(token)`。
2. 装机时把 token 写进 Jetson 的环境变量 `STF_V3_DEVICE_TOKEN`（连同
   `STF_V3_BASE_URL`）。一台设备装在一台车上，token 就是"我是哪台车"。
3. 上传：`POST /v3/ingest/device`，头 `X-Device-Token`。服务器
   `sha256` → 查 `vehicle_devices`（未吊销）→ 得到 `vehicle_id` → 走与网页上传**同一个**
   `ingest.service.store_log()`。设备上**不存用户密码**，也不用手打车牌 / VIN。
4. **VIN 核对**：Jetson 原生 TSV 有 VIN 列，嗅探时读出 `vin_from_log`；与车档 `vin`
   不一致 → 照常入库但 `vin_mismatch = TRUE`，structlog `warning`，车辆页显示角标。
   Yamaha CSV 无 VIN → `vin_from_log` 为空、不核对。
5. 吊销 = `revoked_at` 置值；旧 token 立即 401。丢失设备就吊销重发。

**为什么不用车牌参数**：手打会错；车牌会换（D2 已把它降为标签）。
**为什么不用账号密码**：设备上存人的密码是 V2 的做法，凭证泄露等于账号泄露；设备凭证
只能上传、只对一台车、可单独吊销。

**过渡期（V1/V2 未退役前）**：`jetson_uploader.py` 加 `--v3-base-url` /
`STF_V3_DEVICE_TOKEN`，行程结束**先推 V2（原逻辑不动）再推 V3**，两边独立成败、各自
重试（3 次指数退避，V2 现状无重试，顺手补上）。V1/V2 退役时删掉 V2 那段。

**"转存不丢归属"**：所有落盘路径为 `obd_logs/<vehicle_id>/<log_id>.<ext>`，目录名就是
车；导出 / 迁移脚本一律从 `obd_logs` 表出发 join `vehicles`。

---

## 6. jobs 设计（procrastinate）

### 6.1 App 与连接

```python
# stf_v3/app/jobs/app.py
app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=settings.database_url),
    import_paths=["stf_v3.app.knowledge.tasks", "stf_v3.app.diagnosis.tasks",
                  "stf_v3.app.jobs.maintenance"],
)
```

同一个 `database_url`、同一个 psycopg 驱动；任务表与业务表同库同事务
（API 里 `session.add(conversation); await run_diagnosis.defer_async(...)` 用同一连接）。

### 6.2 队列与任务

| 队列 | 任务 | 触发 | 重试 | 超时 | 谁消费 |
|---|---|---|---|---|---|
| `default` | `diagnosis.run_diagnosis(conversation_id)` | API 202 | **0**（诊断不自动重跑，失败落 error 让人看） | 15 min | `stf-v3-worker` 容器，concurrency 2 |
| `default` | `maintenance.archive_audit_events()` | 每天 03:00 | 1 | 10 min | 同上 |
| `default` | `maintenance.cleanup_orphan_files()` | 每天 03:30 | 1 | 10 min | 同上 |
| `default` | `maintenance.prewarm_llm()` | 启动 + 每小时 | 0 | 5 min | 同上 |
| `gpu` | `knowledge.convert_manual(manual_id)` | POST /v3/manuals | 2 | 90 min | **宿主机 GPU worker**，concurrency 1 |

进度：`convert_manual` 每页回调直接 `UPDATE manuals SET pages_processed=…`（不再写
`.progress.json`）；UI 轮询 `GET /v3/manuals/{id}`。

### 6.3 诊断任务的形状

```
run_diagnosis(conversation_id):
  1 事务：conversation.status = running；写 audit_events seq=1 session_start
  2 装配 Agent（§7），deps = {vehicle, obd_log 路径, manual_fs 根, cancel 检查函数}
  3 async for event in agent.iter(...)：
        映射为 §3.7 事件 → INSERT audit_events(seq++)（token 事件按段聚合）
        每 20 个事件或 2 s 提交一次；cancel_requested → 抛 Cancelled
  4 结束：messages 落库（ModelMessagesTypeAdapter.dump_json）、reports 落库、
        diagnosis_done + done 事件、status = done —— 同一事务
  5 任何异常：error 事件 + status = error + error_message —— 单独事务保证落库
```

SSE 端点不与任务共享内存：它只读 `audit_events`（`after_seq` 起，每 250 ms 轮询一次，
Stage 1 负载下足够；将来可换 LISTEN/NOTIFY），直到读到 `done`/`error`。**断线回放 =
同一段代码不带流式**。API 进程重启对进行中的诊断零影响。

### 6.4 worker 部署

- `stf-v3-worker`（容器，与 api 同镜像）：`procrastinate --app stf_v3.app.jobs.app worker -q default --concurrency 2`
- 宿主机 GPU worker（替代 V2 `marker_worker.py` + 文件协议）：`scripts/gpu_worker.sh` 在
  宿主机 venv（含 marker-pdf、torch）里跑 `procrastinate --app stf_v3.app.jobs.app worker -q gpu --concurrency 1`，
  systemd user service；连库 `127.0.0.1:5432/stf_v3`。它 import 的是 `stf_v3` 包（宿主机
  `pip install -e ~/stf_ai_diagnosis_platform_v1/stf_v3[gpu]`）。
- 关停回收：procrastinate 的 worker 收到 SIGTERM 等当前任务完成；被 kill -9 的任务在
  `stalled` 检测后重新入队（诊断任务 retry=0 → 直接 error，手册任务会重跑）。

### 6.5 运维演练（PROD-06 验收）

```sql
-- 积压 / 卡点
SELECT queue_name, status, count(*) FROM procrastinate_jobs GROUP BY 1,2;
SELECT id, task_name, status, attempts, scheduled_at FROM procrastinate_jobs
 WHERE status IN ('doing','todo') ORDER BY id;
-- 失败任务与原因
SELECT j.id, j.task_name, e.type, e.at FROM procrastinate_jobs j
 JOIN procrastinate_events e ON e.job_id = j.id WHERE j.status='failed' ORDER BY e.at DESC;
```
手动重试：`procrastinate --app … retry <job_id>`；死信 = `status='failed'` 且
`attempts >= max`；worker 重启回收演练 = 跑一个 60 s 的测试任务、`podman restart` worker、
确认任务被重新领取。

---

## 7. Agent 核心：从 V2 复制什么、怎么接 Pydantic AI

### 7.1 复制清单（源 → 目标，均复制不 import）

| V2 源 | V3 目标 | 改动 |
|---|---|---|
| `harness_tools/obd_signals.py`（list_signals, read_window, get_signal_stats, find_events） | `diagnosis/tools/obd_signals.py` | 去掉 `ToolDefinition` 包装，函数体保留；签名改为 Pydantic AI tool（`RunContext[DiagDeps]`） |
| `harness_tools/obd_dtcs.py`（list_dtcs, lookup_dtc）+ 标准 DTC 表 | `diagnosis/tools/obd_dtcs.py` | 同上 |
| `harness_tools/manual_tools.py`（list_manuals, get_manual_toc, read_manual_section, search_manual_text）+ `manual_fs.py` + `manual_index.py` | `knowledge/manual_fs.py`、`diagnosis/tools/manual_tools.py` | `settings.manual_storage_path` 改读 V3 settings；图片块 → Pydantic AI `BinaryContent` |
| `harness_tools/obd_loader.py` | `ingest/loader.py` | **重写数据来源**：按 `obd_log_id` 读 `raw_path`，不再查 `OBDAnalysisSession` |
| `obd_agent/log_parser.py`（parse_log_file、VIN 提取） | `ingest/parsers/jetson_tsv.py` | 去掉 pseudonymise；VIN 提取供 §5 核对 |
| Yamaha 双通道读取（`obd_dtcs.py` 内 Yamaha 元数据 + loader 分支） | `ingest/parsers/yamaha_csv.py` | 收拢为一个解析器 |
| `harness_agents/manual_agent.py` + `*_prompts.py` + `types.py` + `result_formatters.py` | `diagnosis/agent/manual_agent.py` 等 | 子代理改为 Pydantic AI `Agent`，以 `ask_manual_agent` 工具形式挂到主 Agent |
| `harness_agents/obd_agent.py` | `diagnosis/agent/obd_agent.py` | 同上（若 V2 主循环仍用 OBD 委托则保留，否则并入主 Agent 工具集） |
| `harness/harness_prompts.py`（主提示词、车辆信息注入） | `diagnosis/agent/prompts.py` | 车辆信息改从车档读 |
| `harness/context.py`（token 预算、压缩） | `diagnosis/agent/context.py` | 接 Pydantic AI `history_processors`；**去掉 import 时下载 tiktoken 的行为**（见记忆：离线测试被它卡死） |
| `tests/harness/evals/*`（runner、metrics、judge、golden 数据） | `stf_v3/evals/` | `run_manual_agent` / `run_obd_agent` 同签名适配器 |

**不复制**：`rag_tools.search_manual`、`obd_data_tools.read_obd_data`（V2 已未注册）、
`tool_registry.py`（被 Pydantic AI toolset 取代）、`harness/loop.py`（被 `Agent.iter` 取代）、
`obd_agent/{anomaly_detector, clue_generator, format_normalizer, statistics_extractor}`（D1/D2 决策）。

### 7.2 Pydantic AI 装配

```python
# diagnosis/agent/model.py —— 唯一的模型来源（D7）
provider = OpenAIProvider(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
profile  = OpenAIModelProfile(...)   # qwen 怪癖：thinking 抑制、空 tools 列表、并行工具调用
model    = OpenAIChatModel(settings.llm_model, provider=provider, profile=profile)
cloud    = OpenAIChatModel(settings.cloud_llm_model, provider=OpenAIProvider(base_url=settings.cloud_llm_base_url, ...)) if settings.cloud_llm_enabled else None

# diagnosis/agent/main_agent.py
main_agent = Agent(model, deps_type=DiagDeps, output_type=DiagnosisReport,
                   toolsets=[obd_toolset, dtc_toolset, delegation_toolset],
                   history_processors=[compact_when_over_budget])
```

- `DiagDeps`：车档（manufacturer/model/vin）、日志路径、manual_fs 根、DB session 工厂、
  cancel 检查。提示词里 `Vehicle: {manufacturer} {model} (VIN {vin})` 来自车档，不来自日志。
- 子代理委托：`ask_manual_agent(question)` 工具内部 `await manual_agent.run(question, deps=…, usage=ctx.usage)`，用量合并；事件流里表现为一次 `tool_call/tool_result`。
- 测试：`TestModel` / `FunctionModel` 驱动整轮诊断离线跑通（PROD-08 验收）。
- 设置项（全部在 `settings.py`，环境变量前缀 `STF_V3_`）：`LLM_BASE_URL`、`LLM_MODEL`、
  `LLM_API_KEY`、`CLOUD_LLM_ENABLED/BASE_URL/MODEL/API_KEY`、`MANUAL_STORAGE_PATH`、
  `OBD_LOG_STORAGE_PATH`、`DATABASE_URL`、`JWT_SECRET`。（无嵌入模型：Stage 1 不做向量化。）

---

## 8. Auth 装配（fastapi-users + 邀请码）

- `SQLAlchemyUserDatabase` + `AuthenticationBackend(BearerTransport("/v3/auth/login"), JWTStrategy(secret, lifetime=12h))`。
- **不用**库自带的 register 路由；自写 `POST /v3/auth/register`：
  同一事务内 `SELECT … FROM invite_codes WHERE code=? AND used_by IS NULL AND (expires_at IS NULL OR expires_at>now()) FOR UPDATE`
  → `user_manager.create()` → `INSERT memberships` → `UPDATE invite_codes SET used_by, used_at`。
  并发重复核销由行锁挡住。
- 密码策略：≥ 10 字符；忘记密码 = manager 发新邀请码 + 删旧账号（pilot 期不做邮件重置）。
- `current_user` 依赖 + `require_member(workshop_id)` / `require_manager(workshop_id)` 两个依赖；
  车辆级一律走 `can_access_vehicle()`。

---

## 9. 并存部署

`infra/docker-compose.v3.yml`（独立文件，不改动现有 compose）：

| 服务 | 镜像 | 端口（PolyU host 网络） | 说明 |
|---|---|---|---|
| `stf-v3-api` | `stf_v3/Dockerfile` | 8002 | `uvicorn stf_v3.app.main:app` |
| `stf-v3-worker` | 同上 | — | `procrastinate … worker -q default` |

- 复用：Postgres 实例（`DATABASE_URL=postgresql+psycopg://stf_v3_app:…@127.0.0.1:5432/stf_v3`）、
  vLLM（`LLM_BASE_URL=http://127.0.0.1:8000/v1`，具体端口以 #237 切换后为准）、nginx。
- `init-scripts/20-create-stf-v3-db.sql`：`CREATE DATABASE stf_v3; CREATE ROLE stf_v3_app …`
  （首次部署手动执行一次，因为现有 Postgres 卷已初始化，init-scripts 不会重跑）。
- nginx：新增 `location /v3/ { proxy_pass http://127.0.0.1:8002; }`（含 SSE 参数），
  以及将来前端的 `location /app/`。V1/V2 路由不动。
- 卷：`stf_v3_obd_logs`、`stf_v3_manuals`、`stf_v3_logs`（与 V1/V2 卷分开；知识库文件由
  `copy_knowledge_from_v2.py` 连同 Markdown/图片一起复制）。
- CLAUDE.md 部署流程追加 "V3 段"：`podman-compose -f docker-compose.v3.yml -f docker-compose.v3.polyu.yml …`，
  Alembic 用 `podman exec stf-v3-api alembic upgrade head`。

---

## 10. D3 两条自动化检查 + CI

- `import-linter` 合同（`stf_v3/pyproject.toml`）：`forbidden` —— `stf_v3` 不得 import
  `diagnostic_api`、`obd_agent`、`app`；`layers` —— §2 的依赖方向。
- `scripts/check_portable.sh`：`rsync stf_v3/ $TMP/ && cd $TMP && pip install -e .[dev] && pytest -q`。
- `scripts/check_unbound.sh`：`git worktree add $TMP HEAD && rm -rf $TMP/{diagnostic_api,obd_agent,obd-ui} && docker build $TMP/stf_v3 && (cd $TMP/stf_v3 && pytest -q)`。
- GitHub Actions（仓库目前**没有** `.github/workflows/`，PROD-04 新建）：`v3.yml` 四个 job：
  pytest（离线）、import-linter、check_portable、openapi-diff（`export_openapi.py` 输出与
  `docs/api/v3_openapi.json` 比对，不一致则失败）。`check_unbound` 需要构建镜像，放 nightly。

---

## 11. 底座验证表（开发计划 §4 每一项走一遍）

| 后续功能 | 将来动什么 | 动地基？ |
|---|---|---|
| S2 报告内追问 | `messages` 追加 + `POST /v3/conversations/{id}/messages` + `run_followup` 任务；`stage` 列已预留 | 否 |
| 车队历史模块（vehicle_data_twin） | 新模块 `history/`：按 `vehicles.vin` 或 `plate` 对接、脱敏视图、`get_vehicle_history` 工具挂进 toolset | 否 |
| 车队 Dashboard | 新模块 `dashboard/`：只读聚合 `vehicles ⋈ obd_logs ⋈ conversations` by `workshop_id` | 否 |
| S3 自由对话 | `diagnosis` 加输入护栏 + 会话复用；无新表 | 否 |
| S4 主动触发 | `vehicle_events` 表 + `ingest.store_log()` 末尾 `emit(log_uploaded)` + 规则任务 `rules.evaluate` 投 `run_diagnosis` | 否 |
| 相似案例 | `case_vectors` 表 + `find_similar_cases` 工具 | 否 |
| VIN 模糊化 | `vehicles` 读模型加脱敏序列化 + 导出脚本；`vin` 列不动 | 否 |
| 第二个 workshop | `create_workshop.py` 加一行；隔离由 `memberships` 天然成立 | 否 |
| 细粒度权限 | `vehicle_access` 表 + 只改 `can_access_vehicle()` | 否 |
| 实时遥测 | 新模块 + 时序存储；与现有表只通过 `vehicle_id` 关联 | 待评估（不影响现有表） |
| 新日志格式 | `ingest/parsers/<fmt>.py` + `format` CHECK 加一个值 | 否 |
| 微服务拆分 | 按 §2 模块边界拆，`service.py` 变 HTTP/RPC | 否 |
| 限流 | nginx / 中间件 | 否 |
| 模型微调 | 只改 `LLM_MODEL` | 否 |
| V1/V2 数据迁移 | 一次性脚本写 `vehicles`/`obd_logs`（V-UNKNOWN 记录需人工指派车档，否则不导） | 否 |

结论：15 项无一项需要改所有权、身份标识或拆单体。

---

## 12. 风险与开放点

| # | 风险 | 处理 |
|---|---|---|
| R1 | vLLM 端口 / #237 切换状态未核实 | PROD-04 部署前 `ssh polyu-gpu` 确认；`LLM_BASE_URL` 仅配置项 |
| R2 | 宿主机 GPU worker 要 `pip install -e stf_v3[gpu]`，宿主机 Python 版本需 ≥ 3.10 | PROD-06 第一步核实；不满足则退回"薄脚本 + procrastinate 客户端" |
| R3 | procrastinate 与 SQLAlchemy 共享连接池的事务边界（defer 与业务写同事务） | 用 `PsycopgConnector` + 传入同一 connection 的 `defer_async`；PROD-02 写一个事务回滚测试 |
| R4 | token 事件按段聚合的粒度影响前端流式体验 | 先 1 s / 段落，前端归属定了再调 |
| R5 | ~~`users.email` 作为登录名~~ | 已解决（评审确认②）：登录用 `username`，email 可空 |
| O1 | 前端归属（D6） | 下次会议 |
| O2 | Yamaha CSV 的 `recorded_start/end` 能否从文件读出 | PROD-05 嗅探时确认，读不到置空 |

---

## 13. 交付与后续

- 本文档评审通过 → `docs/v3_dev_plan.md` v1.1（PROD-02 ~ 04 细化到 ticket 级）。
- 设计文档 §1.8 追加"机制层表"一句（`vehicle_devices` 等），架构图数据层框加
  `vehicle_devices`（图已同步规则）。
