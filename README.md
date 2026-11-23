# Legal Jet Charter

A public check against the FAA’s own Part 135 lists. Search by operator name, doing-business-as name, certificate number, designator, city, state, or tail number. The site does not invent operators or fill in fields the FAA files do not contain.

Operated for SkyVia Ops / Skyway Aviation (Jake Cambria).

## What the page is reading

Two FAA sources are merged. A row that exists in only one of them is kept and labeled. It is not dropped to force the lists to agree.

1. **Part 135 operators and aircraft workbook.** The spreadsheet linked from [FAA-certificated Aircraft Operators (Legal Part 135 holders)](https://www.faa.gov/about/officeorg/headquartersoffices/avs/faa-certificated-aircraft-operators-legal-part-135-holders). The FAA says this file is updated weekly. The sync reads the page, finds the current `.xlsx` / `.xls` / `.csv` link, and uses the file’s `Last-Modified` date as the file date shown on the site.
2. **Air Operator 14 CFR search.** [faa.gov/av-info/air-operator-far-search](https://www.faa.gov/av-info/air-operator-far-search). The page embeds a Tableau workbook. Its Download sheet is a CSV on `explore.dot.gov`. Akamai rejects plain HTTP clients, so the sync opens the FAA page and then that workbook in Google Chrome, and requests one Part 135 letter file at a time from inside the browser session, about one second apart. Each file has to come back as Part 135 only, with names starting with that letter, and a row count that is a multiple of the staffing measures. A refusal or a short file fails the job and nothing is published. The CSV publishes the certificate number, designator, address, city, state, officers, phone, and staffing counts. It does not include a DBA column, and the DBA worksheet is not a separate public file, so DBA is left blank. Names that start with a digit are not returned by the letter filter; those holders stay on the Part 135 list and are labeled as list-only.

The on-page line is:

> Data from FAA, updated &lt;Part 135 file date&gt;; last checked &lt;date&gt;

If the Air Operator label is a different day, that date is shown too. Both FAA pages are linked.

### How a record is matched

- **Designator first.** The workbook’s `DSGN` code is matched to the Air Operator designator code. That is the certificate key.
- **Legal name, only when it is unique.** If one side has no designator, and exactly one unmatched record on each side shares the same legal name (suffixes such as LLC and INC ignored), they are joined. If the same legal name sits on two certificates, they stay separate. Two records that both have designators are never joined on name when those designators differ.
- **Not guessed.** City, DBA, certificate number, and issue date are whatever the Air Operator export actually contains. District office (`CHDO`) and authorized aircraft come from the workbook. A blank stays blank. Columns the FAA adds later are kept and shown with the column name they arrived under. A column we no longer recognize fails the job instead of being mapped to the wrong field.

Differing values (legal name, FSDO, or a CFR part with no overlap) are stored on the operator and counted in `data/meta.json`. They are not overwritten.

### Editorial watchlist

`data/watchlist.json` is not FAA data. The sync never rewrites it. A note appears on an operator only when the normalized name matches. It is labeled as editorial.

## Automatic update

GitHub Action: `.github/workflows/sync-faa.yml`.

- **How often:** every day at 12:15 UTC, plus any manual run from the Actions tab (`workflow_dispatch`).
- **What a run does:** fetches the holders page and the workbook headers. If the link, `Last-Modified`, length, and `ETag` match the last publish, the workbook is not downloaded again. It always re-downloads the 26 Air Operator letter files, hashes the parsed operators, and rewrites operator rows only when that hash changed.
- **When it commits:** after a successful check it updates `data/meta.json` (`lastChecked`, and the new dataset when a hash changed) and pushes to `main`. A run whose operator rows did not change still commits the check time, so the page can say when the FAA was last looked at. Operator rows are rewritten only when a source hash changed. A push to `main` does not start another Actions run; Vercel redeploys from the push.
- **When it refuses to publish:** missing file link, unreadable workbook, missing required columns, zero rows, an Air Operator export that is not a Part 135-sized list, or a drop of more than 25% versus the previous publish (operators or aircraft). The job fails and the previous files stay as they are.

The normalized extracts live in `data/sources/` so a later run can reuse them when the FAA fingerprint has not moved. `data/operators.json` is the merged file the page loads.

### One-time setup

The workflow asks for `permissions: contents: write`. In the GitHub repo, **Settings → Actions → General → Workflow permissions** must be **Read and write permissions**. Without that, the job can read the FAA files and then fail on `git push`. No extra secret is required; the run uses `GITHUB_TOKEN`.

There is no branch protection ruleset on `main` that this account can see. If one is added later, the `github-actions[bot]` push has to be allowed to bypass it, or the daily commit will be rejected.

### Vercel

Pushes are already connected. The Vercel GitHub app deployed this branch as a preview on team `skywayaviation1s-projects`, project `legaljetcharter`:

https://legaljetcharter-git-cursor-faa-03a3bb-skywayaviation1s-projects.vercel.app

Earlier production deploys of `main` were also created by `vercel[bot]`. A push to `main` from the daily job redeploys production the same way. No Vercel token is required in GitHub. `GITHUB_TOKEN` pushes still notify Vercel.

## Local

```bash
python3 -m pip install -r scripts/requirements.txt
python3 scripts/test_sync.py
python3 scripts/sync_faa.py
python3 -m http.server 8000
```

A second `python3 scripts/sync_faa.py` immediately afterwards is the no-change run: it should leave `data/operators.json` byte-for-byte as it was and only move `lastChecked`.

`explore.dot.gov` rejects curl, including curl that sends a Chrome user agent, because Akamai checks the TLS fingerprint. The sync therefore uses the installed Google Chrome. On Linux it runs under `xvfb-run` (a headed browser on a virtual display). On macOS the workflow sets `FAA_CHROME_HEADED=1` and opens `/Applications/Google Chrome.app`. Headless Chrome on that Mac gets the same Akamai 403; a normal visible window loads the FAA page and the Tableau view. A hard denial fails the job and does not publish.

GitHub-hosted runners are refused as well. A manual run on `ubuntu-latest` with this Chrome session loaded `faa.gov`, then got HTTP 403 Access Denied from `explore.dot.gov` before the workbook opened: https://github.com/skywayaviation1/legaljetcharter/actions/runs/45096629213. The same refusal happened earlier with headless Chrome on both Ubuntu and macOS GitHub runners. That block is the runner address, not the missing TLS fingerprint. A self-hosted runner on a network Akamai allows is required for the daily download. Set the repository variable `FAA_RUNNER` to that runner's label. Until then the scheduled job fails closed and leaves the published list in place.

### Self-hosted runner on Jake's Mac

Machine: arm64, macOS 27.2, no Xcode command-line tools. `/usr/bin/python3` is a stub that asks for those tools. Do not install the command-line tools, and do not use Homebrew. Ubuntu jobs still use `actions/setup-python`. The Mac job does not: that action tries to create `/Users/runner/hostedtoolcache`, which this account cannot write, and its package installer needs `sudo`. The Mac job instead installs CPython 3.12 with uv into the runner work directory (`_work/_tool`) and refuses to continue if `python3` is still `/usr/bin/python3`. That interpreter has no ensurepip, so `python3 -m venv` fails. The sync job installs uv if it is not already on `PATH` and creates the virtualenv with `uv venv`. Ubuntu uses that same uv path. It launches `/Applications/Google Chrome.app` directly and does not download a Playwright browser. The result is pushed with the credentials `actions/checkout` already stored. A second Authorization header is not added, because GitHub rejects that as a duplicate. `credential.helper` is empty for that push so the Mac keychain is not used.

1. Google Chrome must be the normal app at `/Applications/Google Chrome.app`. The macOS job launches that app in a visible window (`FAA_CHROME_HEADED=1`). Headless mode on this Mac is refused by Akamai.
2. In the repo on GitHub: Settings → Actions → Runners → New self-hosted runner → macOS → ARM64. Download the tar.gz it offers (a prebuilt binary, nothing to compile). Then, in Terminal, while logged in to the Mac desktop:

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
tar xzf ~/Downloads/actions-runner-osx-arm64-*.tar.gz
./config.sh --url https://github.com/skywayaviation1/legaljetcharter --token <token from that page> --labels faa-sync --name jakes-mac
./run.sh
```

Leave that Terminal window open, and stay logged in, while the job runs. Headed Chrome needs the window server, so do not install the runner as a background service with `./svc.sh`.

3. Settings → Secrets and variables → Actions → Variables → New repository variable. Name `FAA_RUNNER`, value `faa-sync`.
4. Actions → Sync FAA operator data → Run workflow, branch `main`. The job should print `Opening the FAA Air Operator page in Chrome` and then one line per letter, and finish green. A Chrome window has to appear on the Mac; that is the session Akamai accepts.

## Safety history

Each operator page has a Safety history section.

- **NTSB.** The public aviation accident database `avall.zip` from [data.ntsb.gov/avdata](https://data.ntsb.gov/avdata/), plus every weekly `upDDMMM.zip` file on that page whose timestamp is later than `avall.zip`. The full file contributes FAR part 135 rows. A later weekly file can add or replace an event, including a preliminary row that is not coded part 135 yet. Aviation investigation pages dated on or after the `avall.zip` file date are added when that NTSB number is still absent from the database; each of those items links to its page on ntsb.gov. A designator in `oper_code` is a certificate match. An N-number that appears on exactly one operator’s FAA aircraft list is a registration match. Otherwise the legal name or DBA, including a DBA printed by NTSB, has to match exactly one operator. If two certificates share that name, the aircraft model on the NTSB record can select the certificate that lists that model, and the page still says “name match - verify”.
- **FAA enforcement.** Spreadsheet quarters linked from the [FAA quarterly enforcement reports](https://www.faa.gov/about/office_org/headquarters_offices/agc/practice_areas/enforcement/reports). Those reports are closed civil penalties and certificate suspensions or revocations against regulated entities. Only air-carrier / commercial-operator rows are considered. The reports do not include a certificate designator, so a match is by the legal name as printed, with punctuation ignored, and is labeled “name match - verify”. Quarters that are not spreadsheets (older PDFs, and the 2023 files on that page) are not parsed. The page says which dates the spreadsheets cover.
- **Not used.** The FAA Enforcement Information System is internal. ASIAS detailed queries are not an open download. NTSB’s newer API needs a developer account. This site does not guess from press-release prose.

Absence of a row is stated for that source and date range. It is not a clean-record claim. If the NTSB extract is far smaller than the last good one, or an enforcement spreadsheet cannot be read, the sync fails and does not publish.

## Files

- `index.html`, `styles.css`, `app.js` — the site
- `data/operators.json` — merged operators the page searches
- `data/meta.json` — file date, last check, counts, conflict summary
- `data/sources/part135.json` — normalized workbook
- `data/sources/air-operator.json` — normalized Part 135 Air Operator extract
- `data/watchlist.json` — editorial notes, not touched by the sync
- `scripts/sync_faa.py` — check, parse, merge, refuse bad publishes
- `scripts/test_sync.py` — parsing, matching, and sanity tests
