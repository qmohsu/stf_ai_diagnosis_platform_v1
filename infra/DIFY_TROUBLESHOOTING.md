# Dify Web Interface - Troubleshooting Guide

**Issue:** Browser stuck when accessing http://127.0.0.1:3000

---

## Quick Fixes

### 1. **Clear Browser Cache and Reload**
- Press `Ctrl+F5` (Windows) or `Cmd+Shift+R` (Mac) for hard refresh
- Or open browser DevTools (F12) → Network tab → Check "Disable cache"

### 2. **Try Direct Login URL**
Instead of `http://127.0.0.1:3000`, try:
- **Sign In:** http://127.0.0.1:3000/signin
- **Apps Dashboard:** http://127.0.0.1:3000/apps

### 3. **Check API Connectivity**
Open your browser's Developer Console (F12) and check for errors:
- Look for failed requests to `http://localhost:5001`
- Check if there are CORS errors
- Verify network requests in the Network tab

### 4. **Verify Services Are Running**
```bash
cd infra
wsl -d Ubuntu-22.04 bash -c "make health"
```

All services should show "healthy" except ollama (no healthcheck).

### 5. **Test API Directly**
Open a new terminal and run:
```bash
curl http://127.0.0.1:5001/console/api/setup
```
Should return: `{"step": "finished", "setup_at": "2026-02-01T04:03:54"}`

---

## Common Issues

### Issue 1: "Cannot connect to localhost:5001"

**Symptom:** Browser shows connection errors in console

**Fix:**
1. Check if API is running:
   ```bash
   curl http://127.0.0.1:5001/health
   ```
2. If not responding, restart Dify API:
   ```bash
   cd infra
   wsl -d Ubuntu-22.04 bash -c "docker compose restart dify-api"
   ```

### Issue 2: Blank white screen

**Symptom:** Page loads but shows nothing

**Fix:**
1. Check browser console for JavaScript errors (F12)
2. Try a different browser (Chrome, Firefox, Edge)
3. Restart Dify web:
   ```bash
   cd infra
   wsl -d Ubuntu-22.04 bash -c "docker compose restart dify-web"
   ```

### Issue 3: "Invalid languages" error

**Symptom:** Logs show "Invalid languages: *"

**Fix:** This is a warning, not an error. It doesn't prevent login. Safe to ignore.

### Issue 4: Page keeps loading/spinning

**Symptom:** Loading spinner never stops

**Fix:**
1. Wait 30 seconds (initial load can be slow)
2. Check if API is reachable from browser:
   - Open http://127.0.0.1:5001/health in a new tab
   - Should see: `{"status": "ok", "version": "0.6.13"}`
3. If API is not reachable:
   ```bash
   cd infra
   wsl -d Ubuntu-22.04 bash -c "docker compose logs dify-api | tail -50"
   ```

---

## Manual Login Test

If the web interface isn't working, you can test login via API:

```bash
curl -X POST http://127.0.0.1:5001/console/api/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ipnl@local.dev","password":"Ipnl0000"}'
```

If this returns a token, the backend is working correctly.

---

## Nuclear Option: Complete Restart

If nothing else works:

```bash
cd infra
wsl -d Ubuntu-22.04 bash -c "docker compose down"
wsl -d Ubuntu-22.04 bash -c "docker compose up -d"
```

Wait 60 seconds for all services to start, then try again.

---

## Browser Recommendations

**Recommended browsers:**
- Chrome (best compatibility)
- Microsoft Edge
- Firefox

**Not recommended:**
- Internet Explorer (not supported)
- Very old browser versions

---

## Still Stuck?

1. **Check Docker logs:**
   ```bash
   cd infra
   wsl -d Ubuntu-22.04 bash -c "docker compose logs dify-web | tail -100"
   ```

2. **Check all container status:**
   ```bash
   cd infra
   wsl -d Ubuntu-22.04 bash -c "docker ps"
   ```

3. **Verify database setup:**
   ```bash
   curl http://127.0.0.1:5001/console/api/setup
   ```
   Should show: `{"step": "finished", ...}`

---

## Access Information (Reminder)

**URL:** http://127.0.0.1:3000/signin  
**Email:** ipnl@local.dev  
**Password:** Ipnl0000

---

**Note:** The first load can take 10-30 seconds. Be patient and check browser console for errors.
