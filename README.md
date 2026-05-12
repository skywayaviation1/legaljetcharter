# Legal Jet Charter — Part 135 Verification

Single-file static web app. Public visitors search the FAA Part 135 registry by tail number or operator name and cross-check against an editorial watchlist. Admins manage registry data and watchlist from a separate `/admin` dashboard.

## URLs

- **`/`** — public verification page. Search tail number, operator name, browse watchlist. No admin UI is visible.
- **`/admin`** — admin dashboard. Sign-in screen if not authenticated; full dashboard if signed in.

A small "Admin →" link in the public footer points to `/admin` for discoverability.

## Default credentials

- Username: `admin`
- Passcode: `LegalJet2026`

**Change these immediately after first deploy** via `/admin` → Account → Change credentials → Save → Export updated app → push.

## Admin dashboard sections

- **Overview** — registry & watchlist stats; quick-action cards to jump to any section
- **FAA Data** — drop the latest FAA Part 135 `.xlsx` to refresh the registry; reset to built-in
- **Watchlist** — add, edit, or delete flagged operators with severity, categories, summary, sources
- **Account** — change username/passcode; recovery instructions; sign out

Every section has an "Export updated app ↓" button. Exporting downloads a new `index.html` with the current data, watchlist, and credentials hashed and baked in. Push the file to redeploy.

## Deployment workflow

```bash
# After changes (data update, watchlist edit, password change):
# 1. In /admin, click "Export updated app ↓" — index.html downloads
# 2. Replace the index.html in this folder with the downloaded file
# 3. Commit and push:
git add index.html
git commit -m "Update registry / watchlist / credentials"
git push
# 4. Vercel auto-deploys in ~30 seconds
```

## Security model

- Two layers of access control:
  1. Hosting account — only the GitHub repo owner can push files
  2. In-app admin login — required at `/admin` to access the dashboard
- Passcodes are SHA-256 hashed before being baked into the file
- Sign-in state lives in `sessionStorage` and clears on tab close
- No server, no database — everything is client-side; the file IS the database

## Recovery (forgot credentials)

There's no email recovery. If locked out:

1. Open `index.html` in a text editor
2. Find the `auth-config` script tag near the top:
   ```html
   <script id="auth-config" type="application/json">{"user":"admin","hash":"..."}</script>
   ```
3. Replace with `{"user":"admin","hash":null}` (sets `hash` to `null`)
4. Commit, push, redeploy
5. Visit `/admin` — sign in with any credentials (since `hash` is null, the check passes for `admin`). Then go to Account → Change credentials → Save → Export & redeploy.

## Files

- `index.html` — the entire app (HTML, CSS, JS, FAA data, watchlist data, hashed credentials)
- `vercel.json` — Vercel config (clean URLs, `/admin` rewrite, security headers)
- `package.json` — project metadata
- `.gitignore`
- `README.md` — this file

## Built with

- Plain HTML/CSS/JS (no framework, no build step)
- [SheetJS](https://sheetjs.com/) (CDN) for in-browser `.xlsx` parsing
- Vercel for hosting

## Updating FAA data

The FAA publishes the Part 135 Operators and Aircraft List approximately every 30 days. To refresh:

1. Download the latest `Part_135_Operators_and_Aircraft_List.xlsx` from the FAA
2. Sign in to `/admin`
3. Go to **FAA Data** section
4. Drop the file in the upload area
5. Click **Export updated app ↓**
6. Replace `index.html` and push
