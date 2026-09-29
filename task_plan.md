# Task Plan: Automated Watchlist Notifications

## Goal
Run the watchlist check (`python stock_system.py --watchlist`) twice a week and notify a
list of recipients when it finds symbols matching the policy (`--loss-pct` / `--gain-pct`
breaches vs 1wk / 1mo / YTD). Send nothing when there are no matches.

Three candidate options, A, B and C below. They are not mutually exclusive: the shared
prerequisites feed all three, and A and B can be combined.

## Status
| Phase | State |
|---|---|
| 0. Shared prerequisites | pending |
| 1. Option A: phone push (ntfy) | pending |
| 2. Option B: Cloudflare Worker + email | pending |
| 3. Option C: runs while PC is off | pending, decision needed |

## Known facts (from code review)
- `--watchlist` prints via `_print_watchlist_report()` (`stock_system.py:2521`) and computes
  alerts via `_compute_watchlist_alerts()` (`:2648`). It returns a list; empty = no matches.
- CLI handler is at `stock_system.py:3939`. It currently has **no exit code** distinguishing
  "matches" from "none", so a wrapper would have to parse text.
- Data lives in a **local SQLite DB**: `DEFAULT_DB_PATH = ~/stock_quotes.db` (`:121`).
  `--update` fetches fresh prices into it. This is the key constraint for option C.
- Prices come from yfinance / AlphaVantage / FRED (see memory: MM fund yield sources), so a
  cloud runner needs those API keys too.
- retirement_optimizer's feedback Worker (`C:\Users\starc\source\retirement_assets\.feedback-worker`)
  already sends mail via a Cloudflare `send_email` binding. It gates on Turnstile (browser only),
  so a scheduled script can't call it as is. Sending is limited to **verified Email Routing
  destination addresses**.

---

## Phase 0: Shared prerequisites (all options)
- [ ] 0.1 Add exit code to `--watchlist`: `0` = no matches, `1` = matches, `>=2` = error.
      About 3 lines in the `main()` block at `:3939`.
- [ ] 0.2 Add `--format text|json` (or `--quiet`) so notifiers get a compact body: symbol,
      period, direction, % change. Not the 90-column table.
- [ ] 0.3 Decide the policy values: `--loss-pct` (default 12), `--gain-pct` (none by default),
      and which symbols (all tracked vs a list).
- [ ] 0.4 Dedupe design: store last-alerted `(symbol, period, direction)` with a date, and
      alert only on **new** breaches, or re-alert weekly. Choose one.
      Options: a small table in the DB, or a JSON file next to it.
- [ ] 0.5 Failure alerts: if `--update` or the notify step fails, send a "watchlist job failed"
      message, so silence never means "no matches".
- [ ] 0.6 Wrapper script `run_watchlist.ps1`: `--update`, `--watchlist`, act on the exit code,
      log to a file.
- [ ] 0.7 Tests for the exit code and the dedupe logic.

**Exit criteria:** `run_watchlist.ps1` runs by hand, prints a compact report, and exits with the
right code.

---

## Option A: Phone push notification (ntfy.sh)
**Effort:** lowest, about 1 hour. **Cost:** free. **Runs when:** the PC is on.

- [ ] A.1 Pick a long random topic name (it acts as the shared secret). Store it in a user
      env var `WATCHLIST_NTFY_TOPIC`, not in the repo.
- [ ] A.2 Install the ntfy app on the phone(s) and subscribe to the topic. Each recipient does
      the same.
- [ ] A.3 In `run_watchlist.ps1`, when the exit code is 1, do
      `Invoke-RestMethod https://ntfy.sh/$topic -Method Post -Body $report`.
      Set the Title and Priority headers.
- [ ] A.4 Register the task: `schtasks /Create /TN Watchlist /SC WEEKLY /D TUE,FRI /ST 08:00 ...`.
      Enable "run as soon as possible after a missed start" and "wake to run".
- [ ] A.5 Test with a forced match (`--loss-pct 0.1`) and confirm the push arrives.

**Pros:** no accounts, no SMTP, no domain work, instant.
**Cons:** the topic is public-by-obscurity (anyone who guesses the name can read it), it depends
on a third-party service, and it only works when the PC is awake.
**Variants:** self-hosted ntfy, or Pushover ($5 one-time) for private topics.

---

## Option B: Cloudflare Worker + email
**Effort:** about half a day. **Cost:** free. **Runs when:** the PC is on. The Worker is always up.

Reuses the `.feedback-worker` design, replacing Turnstile with a bearer token.

- [ ] B.1 Confirm each recipient is a **verified** Email Routing destination in the Cloudflare
      account that holds `netcitizen.us` (verification email + link). SMS gateway addresses are
      not practical here.
- [ ] B.2 Create a sibling Worker `tools-watchlist` in the `retirement_assets` repo. That is a
      **separate repo**, so open a session there. Copy `src/logic.cjs` and `src/index.js` and:
  - remove the Turnstile and browser-origin checks
  - require `Authorization: Bearer <WATCHLIST_TOKEN>`, compared in constant time
  - accept `{subject, body}` only. The recipient is fixed from a secret, or a list secret
    with one send per address
  - keep the rate limit, cap `DAILY_LIMIT` at about 5, keep the plain-text MIME build
- [ ] B.3 Secrets via `wrangler secret put`: `WATCHLIST_TOKEN`, `WATCHLIST_TO` (list),
      `WATCHLIST_FROM` (address on `netcitizen.us`). No secrets in the public repo.
- [ ] B.4 Deploy to e.g. `watchlist.netcitizen.us`. Check that the hostname is not already a CNAME.
- [ ] B.5 Tests in the style of `feedback.tests.js`: token missing or wrong, oversized body,
      cap reached, header injection in subject.
- [ ] B.6 In `run_watchlist.ps1`, POST the report to the Worker when the exit code is 1.
      The token comes from user env var `WATCHLIST_TOKEN`.
- [ ] B.7 Register the schedule, same as A.4. Test with a forced match.

**Pros:** no mail password on the PC, mail from your own domain, real email for a recipient list.
**Cons:** a second Worker to maintain, verified-recipients-only, no SMS, and a separate repo.
**Simpler fallback:** the same `.ps1` calling Gmail SMTP with an app password, with no Worker.

---

## Option C: Runs in the cloud while the PC is off
**Effort:** highest, 1 to 2 days. **Runs when:** always.

The blocker is that the data and the price fetching live on the PC: a local SQLite DB and
API keys. Choose an approach first.

### C.0 Decision: where does the check run?
| Approach | What runs in the cloud | DB | Notes |
|---|---|---|---|
| **C1. GitHub Actions cron** | full `--update` + `--watchlist` on a schedule | rebuilt each run, or cached/artifact | free for public repos, API keys as repo secrets. Needs history backfill or a cached DB. |
| **C2. Cloudflare Worker cron + D1** | Worker fetches prices, computes alerts in JS/TS | D1 (SQLite) | rewrites the logic in JS, biggest rewrite, all-Cloudflare |
| **C3. Cloud DB sync** | PC pushes the DB to R2/S3, a cloud cron reads it and checks | copy of the DB | still needs the PC on to refresh prices, so it does not solve the problem |
| **C4. Claude scheduled routine (`/schedule`)** | an agent runs the check on a cron | needs access to the data | needs the repo, keys and an accessible DB. Least deterministic. |

**Recommendation to evaluate first: C1 (GitHub Actions).**
The repo is already on GitHub (PRs #11 to #13). The schedule is `cron: '0 13 * * 2,5'`.
The job checks out the repo, installs Python deps, runs `--update`, then `--watchlist`,
then notifies through A (ntfy) or B (the Worker) by `curl`, so those options become the last step.

- [ ] C.1 Answer the open questions below.
- [ ] C.2 Measure what `--watchlist` needs from the DB. It needs at least 1 month of daily
      closes and the YTD baseline per symbol. Decide whether to (a) run `--update` with
      history backfill on every run (slow, and rate-limited on AlphaVantage), or
      (b) persist the DB between runs with `actions/cache` or a release asset.
- [ ] C.3 Confirm the data providers work from GitHub runner IPs (yfinance is sometimes throttled).
- [ ] C.4 Workflow `.github/workflows/watchlist.yml`: `schedule` + `workflow_dispatch`, Python
      setup, pip install, secrets (`ALPHAVANTAGE_KEY`, `FRED_KEY`, ...), DB restore, update,
      watchlist, notify, DB save.
- [ ] C.5 Notify step: `curl` to ntfy (A) or the Worker (B). Failure notification via the
      `if: failure()` step.
- [ ] C.6 The symbol list and thresholds live in the repo as config, so the cloud copy does not
      depend on the PC's DB contents. Confirm nothing personal is committed in a public repo:
      **is the repo public? Symbol holdings may be sensitive.** If so, use a private repo or
      keep the symbol list in a secret.
- [ ] C.7 Dry run with `workflow_dispatch`, then enable the schedule.
- [ ] C.8 Decide the source of truth: PC DB and cloud DB can drift. Cloud is used for alerts
      only, or the PC syncs the symbol list up.

**Pros:** works with the PC off, free, no new server, reuses the GitHub repo.
**Cons:** cold-start data problem (C.2), API keys in the cloud, GitHub disables scheduled
workflows after 60 days of repo inactivity, and cron runs can lag by several minutes.

---

## Open questions (need user decision)
1. Channel: push only, email only, or both? Is SMS required? (Affects A vs B.)
2. Who are the recipients? Only you, or several? Anyone else needs their own ntfy subscription
   (A) or a verified address (B).
3. Policy: `--loss-pct`, `--gain-pct`, symbol scope?
4. Dedupe: alert once per new breach, or repeat every run while it holds?
5. Is the `local_stock` GitHub repo public or private? (Decides whether C1 is safe.)
6. Is a PC-must-be-on scheduler acceptable as an interim step before C?

## Suggested order
1. Phase 0 (shared), then **A** for a working end-to-end notifier quickly.
2. **C1** so it survives PC sleep, reusing A as the notify step.
3. **B** only if you want email from your own domain with no Gmail password.

## Errors / decisions log
| Date | Item | Note |
|---|---|---|
| 2026-09-29 | Plan created | Options A, B, C scoped. No code changed yet. |
