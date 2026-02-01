# Dify Login Issue - Browser Debugging Steps

## CRITICAL: Check Browser Console for Errors

**Please follow these steps EXACTLY:**

### Step 1: Open Browser DevTools

1. Open **Chrome** or **Edge** browser (recommended)
2. Go to: http://localhost:3000/signin
3. Press **F12** key (or right-click → "Inspect")
4. Click the **Console** tab at the top

### Step 2: Try to Login

1. Enter email: `ipnl@local.dev`
2. Enter password: `Ipnl0000`
3. Click "Sign In"
4. **Watch the Console tab** - any RED error messages?

### Step 3: Check Network Tab

1. In DevTools, click the **Network** tab
2. Make sure "Preserve log" is checked
3. Try to login again
4. Look for a request to `/console/api/login`
   - **If you see it:** Click on it and check:
     - Status code (should be 200)
     - Response tab (should show success or error)
   - **If you DON'T see it:** The JavaScript isn't running

---

## Common Errors and Fixes

### Error: "Failed to fetch" or "Network error"

**This means:** Browser can't reach the API at http://localhost:5001

**Fix:**
```powershell
# Test from PowerShell:
curl http://localhost:5001/health -UseBasicParsing
```

Should return: `{"status": "ok", "version": "0.6.13"}`

If it doesn't work, the API isn't accessible from Windows.

---

### Error: "CORS policy" or "Access-Control-Allow-Origin"

**This means:** CORS (Cross-Origin Resource Sharing) is blocking the request

**Fix:** Run this command:
```bash
cd c:\Users\AAE\stf_ai_diagnosis_platform_v1\infra
wsl -d Ubuntu-22.04 bash -c "docker compose logs dify-api | grep CORS"
```

---

### Error: Nothing happens, no errors in console

**This means:** JavaScript may not be loading or there's a page error

**Fix:**
1. Hard refresh: Press **Ctrl+Shift+R**
2. Clear cache: Press **Ctrl+Shift+Delete** → Clear cache
3. Try incognito/private window: **Ctrl+Shift+N**

---

## Manual API Test from Browser

Open a **new browser tab** and try these URLs:

1. http://localhost:5001/health
   - Should show: `{"status": "ok", "version": "0.6.13"}`

2. http://localhost:5001/console/api/setup
   - Should show: `{"step": "finished", "setup_at": "2026-02-01T04:03:54"}`

**If these DON'T work in your browser:**
- The API is not accessible from Windows
- Need to check Docker port mapping

---

## Test Login via PowerShell (Bypass Browser)

Run this to test if login works:

```powershell
$body = @{
    email = "ipnl@local.dev"
    password = "Ipnl0000"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5001/console/api/login" `
    -Method POST `
    -ContentType "application/json" `
    -Body $body | Select-Object -ExpandProperty Content
```

Should return: `{"result": "success", "data": "eyJ..."}`

---

## Alternative: Use Windows Browser's Network Tools

If the Console doesn't show errors:

1. Open http://localhost:3000/signin
2. Press F12 → Network tab
3. Check "Disable cache"
4. Filter by "XHR" or "Fetch"
5. Try to login
6. Look for failed requests (they'll be red)
7. Click on them to see details

---

## What to Report Back

Please tell me:

1. **What errors do you see in the Console?** (exact message)
2. **Do you see a request to `/console/api/login` in Network tab?**
   - If yes: What status code?
   - If no: No request is being sent
3. **Can you access http://localhost:5001/health in your browser?**
   - If yes: Shows what?
   - If no: Error message?
4. **What browser are you using?** (Chrome, Edge, Firefox, etc.)
5. **Screenshot of browser console** (if possible)

---

## Quick Checklist

- [ ] Tried hard refresh (Ctrl+Shift+R)
- [ ] Checked browser console for errors (F12)
- [ ] Verified http://localhost:5001/health works in browser
- [ ] Tried different browser (Chrome/Edge)
- [ ] Cleared browser cache
- [ ] Tried incognito/private window

---

**This information will help me identify the exact issue!**
