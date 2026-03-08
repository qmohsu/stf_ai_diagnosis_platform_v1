# Design: Authentication & Per-User Session Isolation

**Date**: 2026-03-08
**Status**: Approved
**Author**: Li-Ta Hsu

## Problem

The platform has no authentication. All endpoints are public, sessions
have no owner, and hash-based deduplication is global (same file =
same session for all users). This prevents multi-user pilot deployment.

## Requirements

1. Username + password authentication with JWT tokens.
2. Self-registration (open signup).
3. Same file uploaded by different users creates separate sessions
   (full re-run, complete isolation).
4. Same user uploading the same file reuses their existing session
   (user-scoped dedup via unique constraint on `user_id + input_text_hash`).
5. All `/v2/*` endpoints require JWT. Health and docs remain public.
6. Clean-slate migration (drop existing session/feedback/history data).

## Approach

FastAPI built-in `OAuth2PasswordBearer` + `python-jose` for JWT +
`passlib[bcrypt]` for password hashing. No external auth service.
Fits local-only deployment constraint.

Alternatives considered and rejected:
- **fastapi-users**: Too heavy, YAGNI for pilot.
- **Session cookies**: Poor fit for SSE streaming endpoints.

## Design

### 1. Authentication Backend

**New dependencies**: `passlib[bcrypt]`, `python-jose[cryptography]`,
`python-multipart`

**New files**:
- `diagnostic_api/app/auth/__init__.py`
- `diagnostic_api/app/auth/security.py` -- bcrypt hashing, JWT
  create/decode, `get_current_user` FastAPI dependency
- `diagnostic_api/app/auth/router.py` -- `POST /auth/register`,
  `POST /auth/login`

**Config additions** (`config.py` + `.env.example`):
- `JWT_SECRET_KEY` -- random secret in `.env`
- `JWT_ALGORITHM` = `HS256`
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` = `1440` (24 hours)

**Flow**:
1. `POST /auth/register` -- `{username, password}` -> hash, create
   User, return 201.
2. `POST /auth/login` -- OAuth2 password form -> verify hash, return
   `{access_token, token_type: "bearer"}`.
3. Protected endpoints: `current_user: User = Depends(get_current_user)`
   decodes JWT from `Authorization: Bearer <token>`.

### 2. Database Schema Changes

**Users table** (modify existing unused stub):
```
users
  id              UUID PK
  username        String(50), unique, indexed
  hashed_password String(255), not null
  is_active       Boolean, default True
  created_at      DateTime
```
Drop `email` column (unnecessary for pilot).

**OBDAnalysisSession** -- add ownership:
```
obd_analysis_sessions
  ... (existing columns)
  user_id   UUID, FK -> users.id, NOT NULL, indexed
```
Unique constraint: `(user_id, input_text_hash)` -- one session per
user per file.

**Migration** (single Alembic revision):
1. Truncate `diagnosis_history`, all 5 feedback tables,
   `obd_analysis_sessions` (clean slate).
2. Drop `email` from `users`, add `hashed_password`.
3. Add `user_id` (NOT NULL FK) to `obd_analysis_sessions`.
4. Add unique constraint `uq_user_input_hash` on
   `(user_id, input_text_hash)`.

No changes to feedback or history table schemas -- ownership is
enforced at the session level.

### 3. Endpoint Authorization

All `/v2/obd/*` endpoints inject `current_user` dependency.

**Ownership helper**:
```python
def _get_owned_session(
    session_id: uuid.UUID,
    user: User,
    db: Session,
) -> OBDAnalysisSession:
    """Fetch session owned by user or raise 404."""
```
Returns 404 (not 403) to avoid leaking session existence.

**Session creation** (`POST /v2/obd/analyze`):
- Dedup query: `user_id == current_user.id AND input_text_hash == hash`
- New sessions: `user_id = current_user.id`

**All other session endpoints**: Use `_get_owned_session` to verify
ownership before any read/write.

**Unprotected**: `/health`, `/docs`, `/openapi.json`,
`/auth/register`, `/auth/login`.

### 4. Frontend Changes

**New pages**:
- `/login` -- login form (username + password)
- `/register` -- registration form (username + password + confirm)

**Auth context** (`AuthProvider.tsx`):
- Holds JWT in `localStorage`
- Provides `login()`, `logout()`, `register()`
- On load: check token, redirect to `/login` if missing/expired

**API client** (`api.ts`):
- All fetch calls add `Authorization: Bearer <token>` header
- 401 response -> clear token, redirect to `/login`

**Route protection**: Home and analysis pages redirect to `/login`
if unauthenticated. Login/register redirect to `/` if already
authenticated.

**Logout**: Button in UI header, clears localStorage, redirects to
`/login`. No server-side revocation (stateless JWT, acceptable for
pilot).

### 5. Testing

**Backend** (`diagnostic_api/tests/`):
- `test_auth.py` -- register, login, duplicate username, wrong
  password, missing/invalid token (401)
- `test_session_isolation.py` -- two users same file get separate
  sessions, cross-user access returns 404, same user same file
  returns existing session
- Existing tests updated to include auth headers

No new frontend tests (pilot scope).

## Files Modified

| File | Change |
|------|--------|
| `diagnostic_api/app/models_db.py` | Modify User, add user_id to OBDAnalysisSession |
| `diagnostic_api/app/config.py` | Add JWT config vars |
| `diagnostic_api/app/main.py` | Mount auth router |
| `diagnostic_api/app/api/v2/endpoints/obd_analysis.py` | Add auth deps, ownership checks, user-scoped dedup |
| `diagnostic_api/app/api/v2/endpoints/obd_premium.py` | Add auth deps |
| `infra/.env.example` | Add JWT_SECRET_KEY |
| `diagnostic_api/requirements.txt` | Add 3 packages |
| `obd-ui/src/lib/api.ts` | Add auth header, login/register functions |
| `obd-ui/src/app/layout.tsx` | Wrap with AuthProvider |
| **New**: `diagnostic_api/app/auth/` | security.py, router.py |
| **New**: `obd-ui/src/app/login/page.tsx` | Login page |
| **New**: `obd-ui/src/app/register/page.tsx` | Register page |
| **New**: `obd-ui/src/components/AuthProvider.tsx` | Auth context |
| **New**: `diagnostic_api/alembic/versions/...` | Migration |
| **New**: `diagnostic_api/tests/test_auth.py` | Auth tests |
| **New**: `diagnostic_api/tests/test_session_isolation.py` | Isolation tests |
