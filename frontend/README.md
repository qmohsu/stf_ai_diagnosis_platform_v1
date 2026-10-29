# Towngas 車輛健康門戶

移動優先的只讀車輛健康門戶。員工先以 V3 用戶名稱和密碼登入，再以四項車輛資料建立目前車輛上下文；Next.js BFF 以 HttpOnly Cookie 保存 V3 JWT，並從同一台 Windows 主機上的 `vehicle_data_twin` 驗證最小契約、以白名單模型回傳車輛資料。產品運行時只使用 V3 的登入、登出和目前用戶接口，不使用 `/v1` 或 `/v2`。

## 當前資料邊界

- 車牌是必填主資料，但不是 V3 車輛身份。
- VIN 遵循 V3 的 17 位規則，是正式業務身份。來源平台尚無 VIN 時，驗證頁仍可顯示該車，但以 ASCII `-` 明確標示並禁止進入車輛主頁。
- `vehicle_data_twin` 提供有效 `vin` 後，核驗成功會使用 `/vehicles/vin/{vin}`；同一已驗證車輛的舊車牌頁仍會統一切換至 VIN URL。
- 維修、事件、BOM、覆蓋率、生命周期與部件詳情來自車輛平台。BFF 不回傳 `source_xlsx`、`profile_json`、`payload_json`、證據檔案、復核清單或構建資訊。
- V3 日誌、診斷、conversation、報告與手冊是已凍結契約的未來邊界，本期不向這些業務接口發出運行時請求。
- `/login` 使用 V3 `username + password`；不提供註冊、重設密碼、刷新令牌或「記住我」。登入成功後 `/` 才要求車牌、VIN、品牌、型號四字段服務端核驗。核驗成功後以另一個 HttpOnly Cookie 保存目前車輛；上傳日誌與一鍵診斷仍不在本期。

## 員工登入邊界

瀏覽器只把用戶名稱與密碼送到同源 `/api/auth/login`。BFF 以 OAuth2 password form 呼叫 V3 `/v3/auth/login`，再以取得的 Bearer Token 呼叫 `/v3/users/me`；兩步成功後才把 Token 寫入 `portal_access_token` HttpOnly Cookie。Cookie 到期時間來自 JWT `exp`，V3 預設為 12 小時。Token 不會出現在前端 JSON、DOM、localStorage 或可由 JavaScript 讀取的 Cookie 中。

所有 active、verified 的 V3 用戶看到同一車隊；membership 和 role 不作車輛篩選。所有 `/api/portal/*` 都會先向 V3 驗證登入狀態，再執行車輛上下文檢查。登出會清除帳號 Cookie、車輛 Cookie 和舊資料快取。

## 車輛驗證邊界

車輛四字段驗證只建立目前車輛上下文，不是用戶認證或細粒度授權。VIN 會在車牌選取、頁面、URL 和門戶 JSON 響應中顯示；`robots: noindex, nofollow` 只表達搜尋引擎偏好。正式部署必須設定至少 32 字元的 `PORTAL_VEHICLE_SESSION_SECRET`，多實例使用相同密鑰。

## 本地開發

需要 Node.js 24、npm 11，以及已構建資料的 `vehicle_data_twin` 檢出目錄。

第一個終端進入 `vehicle_data_twin` 的檢出根目錄，再啟動只讀車輛平台：

```powershell
node .\src\server.mjs --no-open
```

第二個終端進入本倉庫的 `frontend` 目錄，再啟動門戶：

```powershell
Copy-Item -LiteralPath '.env.example' -Destination '.env.local'
npm ci
npm run dev
```

開啟 `http://127.0.0.1:3001`。

### Windows 一鍵啟動

完成下方一次性設定後，可以在文件資源管理器中雙擊 `OBD Expert.cmd`。啟動器會依次確認 PostgreSQL、V3 登入 API 和前端；需要時分別在獨立且可見的命令窗口啟動 V3 API 與 `npm run dev`。只有資料庫可連線、`http://127.0.0.1:8002/health` 與 V3 OpenAPI 正常，而且登入頁已就緒時，才會以 Windows 預設瀏覽器開啟 `http://127.0.0.1:3001/login`。

重複雙擊會復用已驗證的服務，不會建立重複進程。停止時在相應的 V3 API 或前端窗口按 `Ctrl+C`，或直接關閉窗口；PostgreSQL 仍作為 Windows 服務運行。若 5432、8002 或 3001 被其他程序佔用，啟動器只會提示衝突，不會終止對方，也不會在登入鏈路未就緒時打開瀏覽器。

一次性設定需要 Node.js 24、npm 11、Python 3.12、PostgreSQL 15、`frontend/node_modules`、`stf_v3/.venv` 和已遷移的 `stf_v3` 資料庫。安裝 Python 與 PostgreSQL 後，可在 PowerShell 執行 `stf_v3/scripts/setup_local_windows.ps1`；它會在本機安全提示 PostgreSQL 管理員密碼、產生隨機服務密鑰、建立低權限資料庫角色並執行遷移。密鑰不存放在倉庫，而是寫入 `%LOCALAPPDATA%\OBD Expert\stf_v3.env`，並限制為目前 Windows 用戶可讀。啟動器不會日常安裝軟件或執行資料庫遷移，也不啟動診斷 worker、LLM 或車輛資料平台。登入接口在線不代表資料庫已有帳號；空資料庫中的任意用戶名稱都會得到正常的憑據錯誤，需另行建立工作坊、邀請碼和用戶後才能成功登入。

| 變數 | 用途 |
| --- | --- |
| `VEHICLE_TWIN_API_BASE_URL` | 車輛平台只讀 API；預設 `http://127.0.0.1:4173` |
| `STF_V3_API_BASE_URL` | V3 認證 API；Windows 同機預設 `http://127.0.0.1:8002`，正式環境必須設定 |
| `PORTAL_PUBLIC_ORIGIN` | 門戶公開 Origin；用於語言偏好和車輛驗證 mutation 的同源檢查 |
| `PORTAL_VEHICLE_SESSION_SECRET` | 車輛上下文 HMAC 密鑰；正式環境至少 32 字元且多實例必須一致 |
| `PORT` | 生產服務端口；預設 `3001` |

## 驗證

```powershell
npm run typecheck
npm run lint
npm run test
npm run test:contract
npm run build
npx playwright install chromium
npm run test:e2e
# 在 production 服務已運行於 127.0.0.1:3001 時執行
npm run lighthouse:portal
```

`tests/contract/openapi.test.ts` 必定讀取本倉庫 `docs/api/v3_openapi.json`，凍結車輛、日誌、診斷、conversation、SSE、report 與 manuals 邊界。若設定 `E2E_LIVE=1` 和 `STF_V3_API_BASE_URL`，測試只讀取 `/v3/openapi.json`，不存取受保護業務資料。

Playwright 使用本地 mock V3 認證和車輛平台，覆蓋 360×800、390×844、768×1024、1280×800 和 1672×941；包括登入失敗／成功／登出、登入頁視覺、單欄車輛驗證初始／填充狀態、缺 VIN、簽名車輛上下文、切換車輛、動態 BOM、部件詳情、axe、manifest、service worker、離線頁及視覺基線。人工確認視覺變更後使用 `npm run test:e2e:update` 更新基線。

Lighthouse 固定為 13.5.0，執行目前保留的 accessibility、best-practices 與 SEO 分類並將報告寫入被忽略的 `lighthouse-report.json`。Lighthouse 13 已移除舊 PWA 分類，因此可安裝 manifest、service worker 控制與離線提示由上面的 Playwright PWA 用例作為硬性驗收，不把不存在的 Lighthouse 分類宣稱為已通過。

## PWA 與離線資料

- 靜態資源 cache-first。
- `/api/auth/*` 和 `/api/portal/*` 永遠直連網絡，不存入 Cache Storage，也不作離線回放。
- Service worker 升級時會移除舊版 `sanitized-data` 快取；登出和重新登入亦會清除遺留資料快取。
- 斷網時只顯示離線提示，不展示此前讀取的車輛業務資料。

## 生產部署（Windows 同機直跑）

1. 讓 `vehicle_data_twin` 繼續只監聽 `127.0.0.1:4173`。
2. 讓 V3 API 監聽 `127.0.0.1:8002`，並把 `STF_V3_API_BASE_URL` 指向該地址。
3. 在 `frontend` 執行 `npm ci && npm run build`。
4. 以 `npm run start` 啟動門戶；它只監聽 `127.0.0.1:3001`。
5. 反向代理只公開 3001 門戶；4173 和 8002 只供同機 BFF 使用。
6. `GET /api/health` 檢查車輛平台；上游不可用時返回 503。

上線前先停止任何佔用 3001 的舊 `obd-ui`。回滾時停止新門戶並重新啟動已記錄版本的舊服務；本門戶不執行資料庫遷移，也不修改 `stf_v3`、`infra` 或 `vehicle_data_twin`。

Dockerfile 會以 `PORTAL_STANDALONE=1` 產生 standalone 映像；一般 Windows `npm run build` 使用標準 `.next` 輸出。`scripts/next-build.cjs` 只在 Node 24/Windows 把「對非符號連結文件／目錄呼叫 `readlink`」的 `EISDIR` 正規化為 Next 15 內置追蹤器原本處理的 `EINVAL`，不改寫依賴或真正的符號連結。本期批准的部署拓撲仍是 Windows 同機直跑，以確保只綁定環回地址的車輛平台不被容器網絡或公網暴露。

## 安全與限制

- 門戶 BFF 按客戶端 IP 採用單實例令牌桶：一般請求每分鐘 120 次，登入突發容量 10 且每秒補充 5 次；登入、登出和車輛驗證 mutation 同時執行同源檢查。多實例部署必須在反向代理或共享儲存增加統一限流。
- 上游一般請求超時 8 秒；健康檢查超時 2 秒。
- CSP、`X-Frame-Options: DENY`、`nosniff`、`Referrer-Policy: no-referrer` 和 `X-Robots-Tag` 由 Next.js 統一設定。
- `ready`、`empty`、`pendingSource`、`deferred` 與 `error` 是不同狀態。只有來源尚無 VIN 才顯示 `-`；網絡或契約錯誤顯示錯誤編號和重試。
- 生命周期／風險區間只作描述性呈現，不代表概率、預測或安全結論；歷史故障事件不會被稱作「目前故障」。
