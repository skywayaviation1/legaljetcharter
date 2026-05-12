# Legal Jet Charter — Part 135 Verification

A single-file web application for verifying that an aircraft and its operator are
authorized to fly under 14 CFR Part 135. Built for charter brokers, travelers, and
anyone conducting due diligence before booking a flight.

- **Search by tail number** (N-number) for instant verified-or-not-found results
- **Search by operator name** to see an operator's full certificated fleet
- **Watchlist** for surfacing operators with known financial, safety, or
  regulatory concerns — independent of the FAA registry, editorially maintained
- **Monthly data refresh** by dropping in the FAA's latest published .xlsx
- **No backend** — pure static HTML; the registry and watchlist are baked into
  the file

The registry is sourced from the FAA's published _Part 135 Operators and Aircraft
List_, updated approximately every 30 days.

---

## Project structure

```
legal-jet-charter/
├── index.html       # The entire app — HTML, CSS, JS, embedded registry, embedded watchlist
├── vercel.json      # Vercel deployment configuration (clean URLs, security headers)
├── package.json     # Project metadata
├── .gitignore       # Keeps source xlsx files and editor noise out of the repo
└── README.md        # This file
```

There is no build step. `index.html` is everything.

---

## Local development

Just open `index.html` in a browser. For a slightly more realistic local URL,
run a static server from the project directory:

```bash
python3 -m http.server 8000
# then visit http://localhost:8000
```

---

## Deploying to Vercel via GitHub

### One-time setup

1. **Create a new GitHub repository** at <https://github.com/new>.
   - Name it `legal-jet-charter` (or whatever you like)
   - Keep it private or public as you prefer
   - Do _not_ initialize with a README (this repo already has one)

2. **Push these files to the repo**:

   ```bash
   cd legal-jet-charter
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/legal-jet-charter.git
   git push -u origin main
   ```

3. **Connect the repo to Vercel**:
   - Sign in at <https://vercel.com> (use the same email as your GitHub if convenient — sign-in with GitHub is one click)
   - Click **Add New… → Project**
   - Find your `legal-jet-charter` repo and click **Import**
   - Framework Preset: **Other** (Vercel will auto-detect this is a static site)
   - Click **Deploy**

   In about 30 seconds you'll have a live URL like `legal-jet-charter-abc123.vercel.app`.

4. **(Optional) Add a custom domain**:
   - In Vercel, open your project → **Settings → Domains**
   - Add `legaljetcharter.com` (or whichever domain you own)
   - Vercel shows the DNS records to set at your registrar
   - HTTPS is provisioned automatically

### Going forward

Every `git push` to the `main` branch auto-deploys to production. Push to any
other branch and Vercel gives you a preview URL — handy for testing changes
before going live.

---

## Monthly data refresh

When the FAA publishes a new Part 135 list (~30-day cycle):

1. Visit your live site and click **Update data** in the header
2. Drop in the new `.xlsx` file (parsed entirely in your browser — nothing uploaded anywhere)
3. Click **Manage → Export updated app ↓**. A file called `index.html` downloads.
4. Replace the `index.html` in your local repo with the downloaded file:

   ```bash
   mv ~/Downloads/index.html ./index.html
   git add index.html
   git commit -m "Update FAA registry — May 2026"
   git push
   ```

5. Vercel auto-deploys within ~30 seconds. Done.

---

## Watchlist management

The Watchlist surfaces operators with known concerns (financial issues,
crashes/incidents, FAA enforcement actions). Inclusion is editorial — it does
**not** mean the operator is illegal or that the FAA has acted; it simply flags
information worth a broker's attention.

To add or edit watchlist entries:

1. Click **Manage** in the header
2. Click **+ Add entry**, fill in:
   - **Operator name** (with autocomplete against the FAA list to match exact legal names)
   - **Severity**: Caution / Warning / Critical
   - **Categories**: Financial / Safety / Regulatory / Other
   - **Summary** (one-line headline)
   - **Details** (longer factual context)
   - **Sources** (one per line, format `Label | URL`)
   - **Date of incident** (optional)
3. Save
4. When done, click **Export updated app ↓** and commit the new `index.html` to
   the repo. The pushed version is what visitors see.

Until you commit and push, your edits live only in your local browser
(`localStorage`). You can keep iterating across browser sessions before
publishing.

---

## Data sources and disclaimer

The Part 135 registry is sourced from the FAA-published _Part 135 Operators and
Aircraft List_ (the same list used internally by FAA Flight Standards). It is
the authoritative record of operators and aircraft authorized to fly under
14 CFR Part 135 (and where applicable, Part 121).

This tool is provided to assist verification only. For any safety- or
contract-critical decision, confirm directly with the operator's certificate
and current Operations Specifications, and with the FAA Flight Standards
District Office of record.

The Watchlist is editorially maintained and reflects publicly reported
information available at the time of entry. Inclusion or non-inclusion on the
Watchlist is not a guarantee of any operator's current status.

---

## Access control

The repository and Vercel project are protected by your respective account
credentials. Anyone who can visit the site can see the **Manage** and
**Update data** controls — but edits made in a visitor's browser only live in
their own browser. The published version only changes when a commit lands on
the `main` branch of this repository, which requires your GitHub login.

If you want admin controls hidden from the public, the simplest path is a
URL-hash gate (e.g. show admin UI only when the URL ends in `#manage`) — open
an issue or ask Claude to add it.

---

## License

All rights reserved. Not for redistribution without permission.
