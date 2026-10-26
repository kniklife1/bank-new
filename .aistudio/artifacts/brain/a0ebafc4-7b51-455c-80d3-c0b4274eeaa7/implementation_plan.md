# Audit, Verification & Fix Plan: `kniklife1/bank-new` & `government-notice-monitor`

## 1. Root-Cause Diagnosis & Fix for `cron-job.org` HTTP 404 (`kniklife1/bank-new`)

### Why GitHub Returns `404 Not Found` on Workflow Dispatch
When `cron-job.org` calls the GitHub Actions dispatch endpoint for `kniklife1/bank-new`, GitHub's API returns `404 Not Found` under any of these specific conditions:

1. **Endpoint URL or HTTP Method Mismatch**:
   - The request must be an HTTP **`POST`** (not `GET`) to the exact REST API URL:
     `https://api.github.com/repos/kniklife1/bank-new/actions/workflows/monitor.yml/dispatches`
   - Calling `https://github.com/kniklife1/bank-new/...` or using `GET` returns `404 Not Found`.
2. **Fine-Grained PAT Repository Scope or Permission Mapping**:
   - GitHub intentionally masks `401 Unauthorized` and `403 Forbidden` responses as `404 Not Found` so unauthorized callers cannot probe repository or workflow existence.
   - For a Fine-Grained Personal Access Token (`github_pat_...`), two settings must be active simultaneously:
     - **Resource owner / Repository access**: Must explicitly include **`kniklife1/bank-new`** (tokens scoped only to another repo or missing repo selection return `404`).
     - **Repository permissions**: **`Actions: Read and write`** (plus automatic **`Metadata: Read-only`**).
   - The `Authorization` header in `cron-job.org` must be formatted as `Bearer github_pat_...` with no trailing spaces or line breaks.
3. **Hidden `.github` Dot-Folder Omission During Web Upload**:
   - When uploading unzipped files through the GitHub Web UI ("Add file -> Upload files"), OS file pickers often skip hidden dot-directories (`.github`) or strip the leading dot (`github/workflows/monitor.yml`). If `.github/workflows/monitor.yml` is missing the leading dot or not committed on `main`, `/actions/workflows/monitor.yml/dispatches` returns `404 Not Found`.
4. **Branch Reference Mismatch**:
   - The request body must be raw JSON `{"ref":"main"}` matching your `main` default branch, accompanied by `Content-Type: application/json`.

### Exact Corrected `cron-job.org` Settings
- **Title**: `Government Notice Monitor — kniklife1/bank-new`
- **URL**: `https://api.github.com/repos/kniklife1/bank-new/actions/workflows/monitor.yml/dispatches`
- **Request Method** (*Advanced tab*): `POST`
- **Request Timeout**: `30 seconds`
- **Headers** (*Advanced tab*):
  - `Accept`: `application/vnd.github+json`
  - `Authorization`: `Bearer <YOUR_FINE_GRAINED_GITHUB_PAT>`
  - `Content-Type`: `application/json`
  - `X-GitHub-Api-Version`: `2022-11-28`
- **Request Body** (*Advanced tab*):
  ```json
  {"ref":"main"}
  ```
- **Expected Success Status**: HTTP **`204 No Content`** (empty response body).

---

## 2. Complete Audit & Verification Scope (`bank-new` & `government-notice-monitor`)

### A. All 23 Websites & Keyword Filtering
- **Preserve all 23 official portals** in both `config/websites.json` and `government-notice-monitor/config/websites.json` (SSC, UPSC, IBPS, SBI, RBI, NABARD, SEBI, IRDAI, PFRDA, LIC, NIACL, GIC Re, NICL, OICL, UIIC, SIDBI, EXIM Bank, NHB, NaBFID, IIFCL, REC, PFC, IREDA).
- **Preserve all 5 categories & 32 recruitment/exam keywords** (`Recruitment`, `Examination`, `Results`, `Answer Keys`, `Next Stages`) with normalized compound matching (`e-admitcard`, `cutoff`, `phase-ii`, `skilltest`), strict word boundaries for short acronyms (`DV`, `PET`, `PST`, `posts`), and corporate/tender/SARFAESI noise filtering.

### B. Baseline Preservation, Multi-Layer Deduplication & Date Validation
- **Preserve Existing State**: Keep all **3,556 baselined notices** in `state/notices.json` and `government-notice-monitor/state/notices.json` (`baseline_completed: true`, `baseline_timestamp: "2026-10-18T00:00:00+00:00"`, all 23 `baselined_websites`) so deployment never resets state or floods Telegram with historical notices.
- **5-Layer Persistent + Same-Run Deduplication**:
  1. Legacy `seen_hashes` backward compatibility.
  2. Exact `id` match (`SHA-256(org_id|canonical_url|normalized_title)`).
  3. Canonical URL match (strips `utm_*`, `fbclid`, `gclid`, `sessionid`, ` timestamp` query params, fragments, trailing slashes, and normalizes `.bank.in` / official mirror domains).
  4. Official Notice / Advertisement number match (`CEN 01/2026`, `CRP PO/MT-XV`, `Advt. No. ...`) scoped by stage/title.
  5. Normalized core title match and same-run `batch_canonical_urls` deduplication across entry and candidate pages.
- **Publication Date Verification**:
  - Parse explicit dates (`DD/MM/YYYY`, `DD-MM-YYYY`, `YYYY-MM-DD`, textual month formats, compact `YYYYMMDD` URL segments, and archive year markers).
  - Suppress any notice whose verified publication date or archive year is older than the baseline cutoff (`2026-10-18`).
  - Never invent today's date when a notice lacks a verifiable publication date.

### C. Telegram Message Format & 10-Notification-Per-Run Cap
- **Strict 4-Field Telegram Format**:
  ```text
  <b>New Official Notice — {Organization Name}</b>

  <b>Title:</b> {Notice Title}
  <b>Published:</b> {Verified Date OR "Publication date unavailable"}
  <b>Link:</b> <a href="{Direct Official URL}">{Direct Official URL}</a>
  ```
- **Delivery Cap & Queue Persistence**:
  - Enforce `DEFAULT_MAX_NOTIFICATIONS_PER_RUN = 10` based on **successful** Telegram deliveries (`self.telegram_sent < self.max_notifications_per_run`).
  - Queue excess eligible notices and failed deliveries with `delivered: false` inside `state/notices.json` for automatic retry on the next run, while never resending notices marked `delivered: true`.

### D. GitHub Actions Workflow Hardening & ZIP Archives
- **Dual-Layout Workflow Path Resilience**: Update `.github/workflows/monitor.yml` in both `bank-new` (root) and `government-notice-monitor/` so that whether files are located at the repository root or inside `government-notice-monitor/`, the workflow automatically resolves paths, runs `monitor.py`, and safely union-merges `state/notices.json` on push conflicts.
- **Interactive `cron-job.org` 404 Troubleshooter & Config Copy Panel in Web Dashboard**: Ensure the dashboard (`src/App.tsx`) prominently surfaces the exact `kniklife1/bank-new` `cron-job.org` configuration, copyable headers/payload, and pre-flight verification checklist.
- **Automated Verification & Archive Packaging**:
  - Run all unit and integration tests (`tests/test_monitor.py`, `government-notice-monitor/tests/test_monitor.py`, `src/utils/keywordMatcher.test.ts`).
  - Rebuild `bank-new.zip` (root-ready archive with `.github/workflows/monitor.yml` at the archive root) and `government-notice-monitor.zip`, plus their downloadable copies in `public/`.
  - Verify the application compiles cleanly via `compile_applet`.
