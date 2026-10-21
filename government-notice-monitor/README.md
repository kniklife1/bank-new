# Indian Government & Banking Notice Monitor

An autonomous, production-grade Python monitoring pipeline that tracks 23 verified official Indian banking, insurance, regulatory, and public-sector portals for new recruitment notices, examination schedules, admit cards, and official circulars, dispatching notifications to Telegram via GitHub Actions triggered every 10 minutes by `cron-job.org`.

---

## 1. Project Architecture

- **Language:** Python (3.10+ compatible).
- **Execution Engine:** GitHub Actions (`ubuntu-latest`, `workflow_dispatch`).
- **Scheduler:** `cron-job.org` triggers the GitHub REST API (`workflow_dispatch`) every 10 minutes (`{"ref":"main"}`). No competing cron trigger is defined inside `.github/workflows/monitor.yml`.
- **State Persistence:** `state/notices.json` is atomically saved and committed back to the repository after each run.
- **Deduplication:** Deterministic SHA-256 fingerprint generated from normalized URL + normalized title prevents repeat notifications.
- **Baseline Initialization:** The first run records all existing notices into `state/notices.json` without sending Telegram messages.
- **Complete Error Isolation:** Per-website exception handling guarantees that a timeout, HTTP 403/404/429/500, SSL error, or HTML parser issue on any website never stops the scraper from processing the remaining organizations.
- **Strict SSL & Extraction Verification:** Enforces `ssl.CERT_REQUIRED` and `check_hostname = True` (never disables SSL verification) with RFC 5280 AIA intermediate certificate resolution, and separates HTTP 200 fetch status from actual notice link extraction.

---

## 2. Minimal Project Structure

```text
government-notice-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml       # GitHub Actions workflow_dispatch automation
├── config/
│   └── websites.json         # All 23 verified active institutions and notice endpoints
├── src/
│   └── monitor.py            # Core scraping, deduplication, and alerting engine
├── state/
│   └── notices.json          # Persistent state and notice fingerprint index
├── requirements.txt          # Python dependencies (requests, beautifulsoup4, urllib3)
└── README.md                 # Deployment, configuration, and verification manual
```

---

## 3. Actively Monitored Institutions (23 Verified Portals)

All 23 active official organizations and their verified notice/career endpoints are configured in `config/websites.json`:

| # | ID | Organization | Official URL | Verified Notice / Career Endpoints | Category |
|---:|---|---|---|---|---|
| 1 | `ibps` | IBPS | `https://www.ibps.in/` | `https://www.ibps.in/index.php/crp-po-mt/`, `https://www.ibps.in/index.php/crp-rrbs/` | Exam Institute |
| 2 | `sbi` | State Bank of India (SBI) | `https://sbi.co.in/` | `https://sbi.co.in/web/careers/current-openings` | Public Sector Bank |
| 3 | `sbi-careers` | SBI Careers | `https://sbi.co.in/web/careers` | `https://sbi.co.in/web/careers/post-your-query` | Public Sector Bank |
| 4 | `rbi` | Reserve Bank of India (RBI) | `https://www.rbi.org.in/` | `https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx`, `https://www.rbi.org.in/Scripts/NotificationUser.aspx` | Financial Regulator |
| 5 | `rbi-opportunities` | RBI Opportunities | `https://opportunities.rbi.org.in/` | `https://opportunities.rbi.org.in/scripts/vacancies.aspx`, `https://opportunities.rbi.org.in/scripts/results.aspx` | Financial Regulator |
| 6 | `sidbi` | SIDBI | `https://www.sidbi.in/` | `https://www.sidbi.in/en/careers` | Development Bank |
| 7 | `nhb` | National Housing Bank (NHB) | `https://www.nhb.org.in/` | `https://www.nhb.org.in/opportunities-at-nhb/` | Development Bank |
| 8 | `pnb` | Punjab National Bank (PNB) | `https://www.pnbindia.in/` | `https://www.pnbindia.in/recruitment.html` | Public Sector Bank |
| 9 | `canara-bank` | Canara Bank | `https://canarabank.com/` | `https://canarabank.com/career` | Public Sector Bank |
| 10 | `indian-bank` | Indian Bank | `https://indianbank.in/` | `https://indianbank.in/career/` | Public Sector Bank |
| 11 | `central-bank-of-india` | Central Bank of India | `https://centralbankofindia.co.in/` | `https://centralbankofindia.co.in/en/recruitments` | Public Sector Bank |
| 12 | `iob` | Indian Overseas Bank (IOB) | `https://www.iob.in/` | `https://www.iob.in/careers` | Public Sector Bank |
| 13 | `uco-bank` | UCO Bank | `https://www.uco.bank.in/en/careers` | `https://www.uco.bank.in/en/careers` | Public Sector Bank |
| 14 | `bank-of-maharashtra` | Bank of Maharashtra | `https://bankofmaharashtra.bank.in/` | `https://bankofmaharashtra.bank.in/current-openings` | Public Sector Bank |
| 15 | `punjab-and-sind-bank` | Punjab & Sind Bank | `https://punjabandsind.bank.in/` | `https://punjabandsind.bank.in/content/recruitment`, `https://punjabandsind.bank.in/content/public-notice` | Public Sector Bank |
| 16 | `idbi-bank` | IDBI Bank | `https://www.idbi.bank.in/` | `https://www.idbi.bank.in/idbi-bank-careers.aspx` | Public/Private Banking |
| 17 | `idbi-careers` | IDBI Careers | `https://www.idbi.bank.in/idbi-bank-careers-current-openings.aspx` | `https://www.idbi.bank.in/idbi-bank-careers-current-openings.aspx`, `https://www.idbi.bank.in/rs-instructions-online-application.aspx` | Public/Private Banking |
| 18 | `exim-bank` | EXIM Bank | `https://www.eximbankindia.in/` | `https://www.eximbankindia.in/careers` | Development Bank |
| 19 | `niacl` | New India Assurance (NIACL) | `https://www.newindia.co.in/` | `https://www.newindia.co.in/portal/readMore/Recruitment` | Public Sector Insurance |
| 20 | `lic` | Life Insurance Corporation (LIC) | `https://licindia.in/` | `https://licindia.in/careers` | Public Sector Insurance |
| 21 | `gic-re` | General Insurance Corporation (GIC Re) | `https://www.gicre.in/` | `https://www.gicre.in/en/careers` | Public Sector Insurance |
| 22 | `sebi` | Securities and Exchange Board of India (SEBI) | `https://www.sebi.gov.in/` | `https://www.sebi.gov.in/department/human-resources-department-37/career.html` | Financial Regulator |
| 23 | `fci` | Food Corporation of India (FCI) | `https://fci.gov.in/` | `https://fci.gov.in/current-vacancies.php` | Government Public Sector |

### Websites Excluded from Active Monitoring (8 Portals)
Based on live verification, the following 8 organizations have been removed from `config/websites.json` due to persistent datacenter IP geo-blocking, WAF `HTTP 403` blocks, or client-side JS-only rendering without server-side links:
- **NABARD** (`nabard`) — Connection timeout from non-IN cloud runner IPs
- **Bank of Baroda** (`bank-of-baroda`) — Connection timeout from cloud runner IPs
- **Union Bank of India** (`union-bank`) — Connection timeout from cloud runner IPs
- **Bank of India** (`bank-of-india`) — HTTP 403 WAF block on datacenter IPs
- **National Insurance Company (NICL)** (`nicl`) — Connection timeout from cloud runner IPs
- **Oriental Insurance Company (OICL)** (`oicl`) — Client-side JS bundle without static server-side notice anchors
- **United India Insurance (UIIC)** (`uiic`) — HTTP 403 WAF block on datacenter IPs
- **Employees' Provident Fund Organisation (EPFO)** (`epfo`) — HTTP 403 WAF block on datacenter IPs

---

## 4. Telegram Notification Format

When a new notice is detected, the bot sends one concise alert per notice using HTML mode:

```text
New Official Notice — IBPS

Title: CRP Recruitment Notification
Published: 18 October 2026
Link: https://www.ibps.in/example-notice
```

- When a publication date is present on the source page, `Published: <date>` is included.
- When no publication date is available, the `Published:` line is omitted (dates are never invented).
- All text fields are HTML-escaped to prevent formatting errors.

---

## 5. Exact Setup Instructions

### Step 1: Create Telegram Bot & Retrieve Chat ID
1. Open Telegram and message `@BotFather`.
2. Send `/newbot`, follow the prompts, and copy the **Bot API Token**.
3. Send a message to your bot (or add it as an Admin to your target channel/group and post a message).
4. Open `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in a browser and copy the numerical `"chat":{"id": ...}` value.

### Step 2: Create GitHub Repository & Upload Files
```bash
unzip government-notice-monitor.zip
cd government-notice-monitor
git init
git add .
git commit -m "feat: initial government and banking notice monitor (23 verified portals)"
git branch -M main
git remote add origin https://github.com/OWNER/REPO.git
git push -u origin main
```

### Step 3: Configure GitHub Actions Secrets & Permissions
1. Go to **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN`: Your Telegram Bot token.
   - `TELEGRAM_CHAT_ID`: Your numerical Telegram Chat ID.
2. Go to **Settings → Actions → General → Workflow permissions**:
   - Select **Read and write permissions** and click **Save**.

### Step 4: Run First Baseline Manually
1. Go to the **Actions** tab in your GitHub repository.
2. Select **Notice Monitor** on the left, click **Run workflow** on branch `main`.
3. Confirm the run logs `[BASELINE INITIALIZATION]` and commits `state/notices.json` with `"baseline_completed": true` without sending Telegram messages.

### Step 5: Configure cron-job.org (Every 10 Minutes)
1. **Create a Fine-Grained GitHub Personal Access Token:**
   - Go to GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   - **Repository access:** Select **Only select repositories** → choose your `government-notice-monitor` repository.
   - **Repository permissions:** Set **Actions** to **Read and write** (`Contents: Read-only` is automatically selected).
   - Generate and copy the token (`github_pat_...`). Never commit this token to code or put it in a URL.
2. **Create the Cronjob in cron-job.org:**
   - **URL:** `https://api.github.com/repos/OWNER/REPO/actions/workflows/monitor.yml/dispatches`
   - **Schedule:** Every 10 minutes (`*/10 * * * *`).
   - **Request Method:** `POST`
   - **Headers (Advanced tab):**
     - `Accept`: `application/vnd.github+json`
     - `Authorization`: `Bearer <YOUR_FINE_GRAINED_GITHUB_PAT>`
     - `X-GitHub-Api-Version`: `2022-11-28`
     - `Content-Type`: `application/json`
   - **Request Body:**
     ```json
     {"ref":"main"}
     ```
   - Use **Test run** in `cron-job.org` to verify a `204 No Content` response and confirm a new `workflow_dispatch` run starts under your repository's **Actions** tab.
