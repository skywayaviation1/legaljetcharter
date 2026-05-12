# Legal Jet Charter — Part 135 Verification

A single-file web application for verifying that an aircraft and its operator are
authorized to fly under 14 CFR Part 135. Built for charter brokers, travelers, and
anyone conducting due diligence before booking a flight.

- **Search by tail number** (N-number) for instant verified-or-not-found results
- **Search by operator name** to see an operator's full certificated fleet
- **Watchlist** for surfacing operators with known financial, safety, or
  regulatory concerns — independent of the FAA registry, editorially maintained
- **Monthly data refresh** by dropping in the FAA's latest published .xlsx
- **Admin login** at `/#admin` — public visitors see only the search interface
- **No backend** — pure static HTML, deployed to Vercel from GitHub

The registry is sourced from the FAA's published _Part 135 Operators and Aircraft
List_, updated approximately every 30 days.

---

## Project structure

```
legal-jet-charter/
├── index.html       # The entire app — HTML, CSS, JS, embedded registry, watchlist, auth config
├── vercel.json      # Vercel deployment config (clean URLs, security headers)
├── package.json     # Project metadata
├── .gitignore       # Keeps source xlsx files and editor noise out of the repo
└── README.md        # This file
```

No build step. `index.html` is everything.

---

## Two-stage setup

The repo as you clone it is **unsecured** — the setup banner shows, anyone can
edit. Before you publish, you need to set admin credentials locally and commit
the secured version. This happens once.

### Stage 1 — Set admin credentials locally

1. Clone the repo and open `index.html` in a browser (double-click the file).
2. A dark banner appears at the top: **⚠ Admin access is unprotected.**
3. Click **Set up access →** on the right side of the banner.
4. A modal opens with three fields:
   - **Username** — defaults to `admin`, change if you want
   - **New passcode** — minimum 8 characters
   - **Confirm passcode**
5. Click **Save credentials**.
6. The banner disappears; you're now signed in locally.
7. Click **Manage** in the header → at the bottom, click **Export updated app ↓**.
   A file `index.html` downloads with your credentials hashed and baked in.
8. Replace `index.html` in your local repo with the downloaded file.

### Stage 2 — Push to GitHub, connect Vercel

1. **Create a new GitHub repository** at <https://github.com/new>.
   - Name it `legal-jet-charter` (or whatever you like)
   - Don't initialize with a README — the repo already has one

2. **Push the files**:

   ```bash
   cd legal-jet-charter
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/legal-jet-charter.git
   git push -u origin main
   ```

3. **Connect to Vercel**:
   - Sign in at <https://vercel.com> (sign-in with GitHub is one click)
   - Click **Add New… → Project**
   - Find `legal-jet-charter` and click **Import**
   - Framework Preset: **Other**
   - Click **Deploy**

   About 30 seconds later you have a live URL like `legal-jet-charter-abc123.vercel.app`.

4. **(Optional) Add a custom domain**:
   - Vercel project → **Settings → Domains**
   - Add `legaljetcharter.com`
   - Vercel shows DNS records to set at your registrar
   - HTTPS is provisioned automatically

5. **Test the live site**:
   - Visit your URL → you should see the public search interface, no Manage/Update buttons
   - Visit your URL with `#admin` appended → branded sign-in page
   - Enter the credentials you set in Stage 1 → admin features unlock

Every `git push` to `main` from this point on auto-deploys.

---

## Monthly data refresh

When the FAA publishes a new Part 135 list (~30-day cycle):

1. Visit your live site, append `#admin`, sign in
2. Click **Update data** in the header
3. Drop in the new `.xlsx` (parsed entirely in your browser — nothing uploaded anywhere)
4. Click **Manage → Export updated app ↓** — `index.html` downloads with the new registry, your watchlist, and your credentials all baked in
5. Replace the file in your local repo, commit, push:

   ```bash
   mv ~/Downloads/index.html ./index.html
   git add index.html
   git commit -m "Update FAA registry — May 2026"
   git push
   ```

6. Vercel auto-deploys within ~30 seconds.

---

## Watchlist management

The Watchlist surfaces operators with known concerns (financial issues,
crashes/incidents, FAA enforcement actions). Inclusion is editorial — it does
**not** mean the operator is illegal or that the FAA has acted; it simply flags
information worth a broker's attention.

To add or edit entries:

1. Sign in at `/#admin`
2. Click **Manage** in the header
3. **+ Add entry**, fill in:
   - **Operator name** (autocompletes against the FAA list — pick the exact legal name)
   - **Severity**: Caution / Warning / Critical
   - **Categories**: Financial / Safety / Regulatory / Other
   - **Summary** (one-line headline)
   - **Details** (longer factual context)
   - **Sources** (one per line, format `Label | URL`)
   - **Date of incident** (optional)
4. Save
5. When done, **Export updated app ↓** → commit → push

Until you commit and push, your edits live only in your local browser
(localStorage). You can iterate across browser sessions before publishing.

---

## Changing your admin credentials

1. Sign in at `/#admin`
2. Click **Manage** → at the bottom click **Change credentials**
3. Enter new username/passcode → Save
4. **Export updated app ↓** → commit → push

---

## Forgot your passcode

There's no email recovery — credentials are hashed and baked into the file. Two options:

1. **Easy:** If you have a browser where you're still signed in, open
   **Manage → Change credentials**, set new ones, export, commit, push.

2. **Manual:** Open `index.html` in a text editor. Find the line
   `<script id="auth-config" type="application/json">{"user":"...","hash":"..."}</script>`
   and replace its contents with `{"user":"admin","hash":null}`. Save, open the
   file, and the setup banner returns so you can set new credentials.

---

## Data sources and disclaimer

The Part 135 registry is sourced from the FAA-published _Part 135 Operators and
Aircraft List_, the authoritative record of operators and aircraft authorized
to fly under 14 CFR Part 135 (and where applicable, Part 121).

This tool is provided to assist verification only. For any safety- or
contract-critical decision, confirm directly with the operator's certificate
and current Operations Specifications, and with the FAA Flight Standards
District Office of record.

The Watchlist is editorially maintained and reflects publicly reported
information available at the time of entry. Inclusion or non-inclusion on the
Watchlist is not a guarantee of any operator's current status.

---

## Access control summary

Three layers protect the site:

1. **Vercel deployment** — only commits to your GitHub repo's `main` branch deploy
   to production, and only you can push to that repo
2. **Admin login** — public visitors at your URL see only the search; the `/#admin`
   route requires your username and passcode to access management tools
3. **Hosting account** — your Vercel and GitHub accounts are protected by your
   credentials (enable 2FA on both for full protection)

---

## License

All rights reserved. Not for redistribution without permission.
