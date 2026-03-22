# Database Schema Reference

**Database**: PostgreSQL 15 with pgvector 0.7.4
**ORM**: SQLAlchemy (declarative)
**Migrations**: Alembic (head: `l3m4`)

---

## Table: `users`

Local JWT authentication accounts.

| Column            | Type          | Constraints                      |
|-------------------|---------------|----------------------------------|
| `id`              | UUID          | PK, default `uuid4`             |
| `username`        | VARCHAR(50)   | UNIQUE, NOT NULL, INDEXED        |
| `hashed_password` | VARCHAR(255)  | NOT NULL                         |
| `is_active`       | BOOLEAN       | default `true`                   |
| `created_at`      | TIMESTAMP     | default `now()`                  |

**Relationships**: `sessions` → `obd_analysis_sessions` (one-to-many)

---

## Table: `obd_analysis_sessions`

Core session table for OBD-II analysis requests.

| Column                      | Type          | Constraints                                  |
|-----------------------------|---------------|----------------------------------------------|
| `id`                        | UUID          | PK, default `uuid4`                          |
| `user_id`                   | UUID          | FK → `users.id`, NOT NULL, INDEXED           |
| `vehicle_id`                | VARCHAR(50)   | NULLABLE, INDEXED                            |
| `status`                    | VARCHAR(20)   | default `'PENDING'`, INDEXED                 |
| `input_text_hash`           | VARCHAR(64)   | NOT NULL, INDEXED (SHA-256)                  |
| `input_size_bytes`          | INTEGER       | NOT NULL                                     |
| `raw_input_text`            | TEXT          | NULLABLE                                     |
| `result_payload`            | JSONB         | NULLABLE (full LogSummaryV2)                 |
| `parsed_summary_payload`    | JSONB         | NULLABLE (flat-string parsed summary)        |
| `error_message`             | TEXT          | NULLABLE                                     |
| `diagnosis_text`            | TEXT          | NULLABLE (latest local LLM diagnosis)        |
| `premium_diagnosis_text`    | TEXT          | NULLABLE (latest premium LLM diagnosis)      |
| `premium_diagnosis_model`   | VARCHAR(200)  | NULLABLE                                     |
| `created_at`                | TIMESTAMP     | default `now()`                              |
| `updated_at`                | TIMESTAMP     | default `now()`, on update `now()`           |

**Constraints**:
- `uq_user_input_hash`: UNIQUE(`user_id`, `input_text_hash`)

**Relationships**:
- `user` → `users` (many-to-one)
- `summary_feedback` → `obd_summary_feedback` (one-to-many)
- `detailed_feedback` → `obd_detailed_feedback` (one-to-many)
- `rag_feedback` → `obd_rag_feedback` (one-to-many)
- `ai_diagnosis_feedback` → `obd_ai_diagnosis_feedback` (one-to-many)
- `premium_diagnosis_feedback` → `obd_premium_diagnosis_feedback` (one-to-many)
- `diagnosis_history` → `diagnosis_history` (one-to-many)

---

## Feedback Tables

All five feedback tables share a common structure from `_OBDFeedbackMixin`, with table-specific columns noted below.

### Common Columns (all feedback tables)

| Column       | Type      | Constraints                                     |
|--------------|-----------|--------------------------------------------------|
| `id`         | UUID      | PK, default `uuid4`                             |
| `session_id` | UUID      | FK → `obd_analysis_sessions.id`, NOT NULL, INDEXED |
| `rating`     | INTEGER   | NOT NULL (1–5)                                   |
| `is_helpful` | BOOLEAN   | NOT NULL                                         |
| `comments`   | TEXT      | NULLABLE                                         |
| `created_at` | TIMESTAMP | default `now()`                                  |

### Table: `obd_summary_feedback`

Expert feedback on the OBD analysis summary view.

- Columns: common only (no additional columns)

### Table: `obd_detailed_feedback`

Expert feedback on the OBD analysis detailed view.

- Columns: common only (no additional columns)

### Table: `obd_rag_feedback`

Expert feedback on the RAG-retrieved context.

| Extra Column     | Type | Constraints |
|------------------|------|-------------|
| `retrieved_text` | TEXT | NULLABLE    |

### Table: `obd_ai_diagnosis_feedback`

Expert feedback on the local AI diagnosis.

| Extra Column     | Type | Constraints |
|------------------|------|-------------|
| `diagnosis_text` | TEXT | NULLABLE    |

### Table: `obd_premium_diagnosis_feedback`

Expert feedback on the premium AI diagnosis.

| Extra Column     | Type | Constraints |
|------------------|------|-------------|
| `diagnosis_text` | TEXT | NULLABLE    |

---

## Table: `diagnosis_history`

Immutable, append-only log of every AI diagnosis generation (local and premium).

| Column           | Type         | Constraints                                     |
|------------------|--------------|--------------------------------------------------|
| `id`             | UUID         | PK, default `uuid4`                             |
| `session_id`     | UUID         | FK → `obd_analysis_sessions.id`, NOT NULL, INDEXED |
| `provider`       | VARCHAR(20)  | NOT NULL                                         |
| `model_name`     | VARCHAR(200) | NOT NULL                                         |
| `diagnosis_text` | TEXT         | NOT NULL                                         |
| `created_at`     | TIMESTAMP    | default `now()`                                  |

**Constraints**:
- `ck_diagnosis_history_provider`: CHECK(`provider` IN (`'local'`, `'premium'`))

**Relationship**: `session` → `obd_analysis_sessions` (many-to-one)

---

## Table: `rag_chunks`

RAG knowledge chunks with pgvector embeddings for semantic retrieval. Replaces the former Weaviate `KnowledgeChunk` collection.

| Column          | Type         | Constraints                |
|-----------------|--------------|----------------------------|
| `id`            | UUID         | PK, default `uuid4`       |
| `text`          | TEXT         | NOT NULL                   |
| `doc_id`        | VARCHAR(255) | NOT NULL, INDEXED          |
| `source_type`   | VARCHAR(50)  | NOT NULL                   |
| `section_title` | VARCHAR(500) | NULLABLE                   |
| `vehicle_model` | VARCHAR(100) | NULLABLE, INDEXED          |
| `chunk_index`   | INTEGER      | NOT NULL                   |
| `checksum`      | VARCHAR(64)  | UNIQUE, NOT NULL, INDEXED  |
| `metadata_json` | JSONB        | NULLABLE                   |
| `embedding`     | VECTOR(768)  | NOT NULL                   |
| `created_at`    | TIMESTAMP    | default `now()`            |

**Index**: HNSW index on `embedding` column for approximate nearest-neighbor search.

---

## Entity Relationship Diagram (text)

```
users 1──────────< obd_analysis_sessions
                        │
                        ├──< obd_summary_feedback
                        ├──< obd_detailed_feedback
                        ├──< obd_rag_feedback
                        ├──< obd_ai_diagnosis_feedback
                        ├──< obd_premium_diagnosis_feedback
                        └──< diagnosis_history

rag_chunks (standalone, no FK relationships)
```

---

## Migration History

| Revision   | Description                                |
|------------|--------------------------------------------|
| `4724465f` | Create initial tables                      |
| `68e7defe` | Create feedback table                      |
| `5ed3c5aa` | Create OBD analysis tables                 |
| `a1b2c3d4` | Add raw and parsed columns                 |
| `b3f4a7c8` | Drop feedback session unique, add index    |
| `c4d5e6f7` | Split feedback tables                      |
| `d5e6f7a8` | Add RAG feedback table                     |
| `e6f7a8b9` | Add AI diagnosis                           |
| `f7a8b9c0` | Add diagnosis_text to feedback             |
| `a2b3c4d5` | Add retrieved_text to RAG feedback         |
| `g8h9i0j1` | Add premium diagnosis                     |
| `b2c3d4e5` | Drop corrected_diagnosis                   |
| `h9i0`     | Add diagnosis_history                      |
| `i0j1`     | Add provider CHECK constraint              |
| `j1k2`     | Auth: users table + session user_id FK     |
| `k2l3`     | Drop V1 tables                             |
| `l3m4`     | Add rag_chunks + pgvector (current head)   |
