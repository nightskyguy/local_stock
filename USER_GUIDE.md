# Stock Quote System - User Guide

Everything is driven by one script, `stock_system.py`. It keeps daily closing prices in a local
SQLite database, serves them over a small local web API (for LibreOffice Calc `WEBSERVICE()`
and your browser), and has a CLI for maintenance, money-market funds, fundamentals and the
watch list.

This guide describes version **1.2.1** (`python stock_system.py --version`). For a feature
overview see [README.md](README.md). For the planned Linux support see
[LINUX_PLAN.md](LINUX_PLAN.md).

**Contents**
1. [Quick start](#1-quick-start)
2. [How the CLI works (read this once)](#2-how-the-cli-works-read-this-once)
3. [Daily use](#3-daily-use)
4. [Watch list: example walkthrough](#4-watch-list-example-walkthrough)
5. [Command reference](#5-command-reference)
6. [Web server and API](#6-web-server-and-api)
7. [Using it from LibreOffice Calc](#7-using-it-from-libreoffice-calc)
8. [Money market funds](#8-money-market-funds)
9. [Fundamentals (P/E, dividend yield, expense ratio)](#9-fundamentals-pe-dividend-yield-expense-ratio)
10. [Charts](#10-charts)
11. [Configuration and API keys](#11-configuration-and-api-keys)
12. [Scheduling and automation](#12-scheduling-and-automation)
13. [Maintenance: backup, export, SQL](#13-maintenance-backup-export-sql)
14. [Troubleshooting](#14-troubleshooting)
15. [Files and locations](#15-files-and-locations)

---

## 1. Quick start

Requirements: Windows 10/11, Python **3.9+** (the code uses `zoneinfo`), internet access.
Linux is not officially supported yet; see [LINUX_PLAN.md](LINUX_PLAN.md).

```bat
install.bat
```

`install.bat` installs the Python packages (`flask apscheduler yfinance pandas numpy`), runs
`setup.bat` (creates the database, adds the portfolio symbols and downloads 3 years of
history, about 5-10 minutes) and starts the server on port 5000.

`setup.bat` contains a specific portfolio. To use your own symbols, edit its `--add` line or do
the steps by hand:

```bat
python -m pip install flask apscheduler yfinance pandas numpy
python stock_system.py --init
python stock_system.py --add AAPL --add VOO --add VTI
python stock_system.py --update --years 3
python stock_system.py --stats
```

`--init` creates the database and adds two reference symbols (QQQ and CSCO) that are used to
detect market closures. Leave them in place.

---

## 2. How the CLI works (read this once)

**Each run does one thing.** `stock_system.py` runs the first matching action and exits, so
flags cannot be combined into a pipeline:

```bat
:: WRONG - only --update runs, --watchlist is ignored
python stock_system.py --update --watchlist

:: RIGHT - two commands
python stock_system.py --update
python stock_system.py --watchlist
```

Order of precedence if you do pass several: `--stop`, `--version`, `--init`, `--add`,
`--remove`, `--update`, `--update-fundamentals`, `--add-fund`, `--reclassify`, `--list-funds`,
`--rebuild-fund`, `--verify-fund`, `--watchlist`, `--export`, `--config`, `--stats`,
`--prices`, `--closures`, `--import-closures`, `--dbpath`, `--server`.

Modifier flags (`--years`, `--since`, `--db`, `--loss-pct`, `--gain-pct`, `--filename`,
`--symbol`, `--expense-ratio`, `--port`, `--secondary`) go *with* an action and are not
actions themselves. With no arguments the script prints its help.

**Which database?** Default is `stock_quotes.db` in your user folder. Use `--db PATH` on any
command to work on another file. Check with `python stock_system.py --dbpath`.

---

## 3. Daily use

### Update prices

```bat
python stock_system.py --update
```

Smart and incremental: new symbols get full history (default 3 years); existing symbols only
fetch from the day after their last stored date; known market-closure dates are skipped. Run it
after the US market closes. A running server also does this once a day, and makes up a run it
missed while the PC slept (see [section 12](#12-scheduling-and-automation)). An update run during
market hours stops at the previous day: today's unfinished candle is never stored.

Force-refresh one symbol, for example to repair rows with missing open/high/low:

```bat
python stock_system.py --update AAPL                      :: re-fetch last 7 days
python stock_system.py --update AAPL --years 2            :: re-fetch last 2 years
python stock_system.py --update AAPL --since 2024-01-01   :: re-fetch from a date
```

### Add and remove symbols

```bat
python stock_system.py --add NVDA --add AMD               :: add (no history yet)
python stock_system.py --update                           :: ...download history now
python stock_system.py --add NVDA --since 2022-01-01      :: add AND seed history from a date
python stock_system.py --remove AMD                       :: delete symbol + all its quotes
python stock_system.py --remove                           :: delete every symbol with 0 quotes
```

`--remove SYMBOL` asks for **no confirmation** and deletes the data permanently. Back up first
if unsure ([section 13](#13-maintenance-backup-export-sql)).

### See what you have

```bat
python stock_system.py --stats      :: per-symbol type, quote count, date range, last price
python stock_system.py --prices     :: current price per symbol (live if market open, else last close)
python stock_system.py --closures   :: market-closure dates the system knows about
```

`--stats` reads only the database (last stored close). `--prices` may contact the data source
for a live quote while the market is open (9:30-16:00 ET, Mon-Fri); results are cached for 15
minutes.

---

## 4. Watch list: example walkthrough

The watch list answers: *"Has anything I track dropped (or jumped) a lot lately?"* It compares
each symbol's **latest stored close** against three baselines and prints only the rows that
cross your threshold.

| Period | Baseline used |
|---|---|
| `1wk` | close on or just before 7 calendar days ago |
| `1mo` | close on or just before 30 calendar days ago |
| `ytd` | close on or just before Jan 1 of this year (i.e. last close of the prior year) |

`% change = (latest - baseline) / baseline x 100`. Weekends and holidays are handled
automatically by using the nearest earlier trading day.

There is no separate "watch list" to maintain: by default it checks **every tracked symbol**.
To watch fewer, pass a comma-separated list.

### Step 1 - refresh prices first

"Latest" means the last close in the database, not a live quote. Update first or you will be
comparing stale data:

```bat
python stock_system.py --update
```

### Step 2 - run the default check (losses of 12% or more)

```bat
python stock_system.py --watchlist
```

```
==========================================================================================
Watch List Alert Report (loss >=12.0%)
==========================================================================================
Symbol     Period   Dir    Baseline Date     Baseline    Current  % Change
------------------------------------------------------------------------------------------
ACHR       ytd      loss   2026-01-01            7.52      4.929   -34.45%
IBIT       ytd      loss   2026-01-01           49.65      42.04   -15.33%
NVDA       1mo      loss   2026-10-15          217.23     175.66   -19.14%
TSLA       ytd      loss   2026-01-01          449.72    392.275   -12.77%
==========================================================================================
```

Read it as: NVDA is down 19.14% from its close on 2026-10-15 (the 1-month baseline). A symbol
can appear on several rows, one per period that breached. When nothing breaches you get
`No symbols breached the threshold(s).`

### Step 3 - tune the thresholds

```bat
:: more sensitive: flag drops of 5% or more
python stock_system.py --watchlist --loss-pct 5

:: gains only: flag rises of 8% or more (no loss check)
python stock_system.py --watchlist --gain-pct 8

:: both sides at once
python stock_system.py --watchlist --loss-pct 10 --gain-pct 25
```

Rules for the two threshold flags:
- Neither given: losses of 12% (the default).
- `--loss-pct` only: that loss threshold, no upside check.
- `--gain-pct` only: gains only, **no** loss check.
- Both: both sides checked.

### Step 4 - restrict to a few symbols

```bat
python stock_system.py --watchlist NVDA,TSLA,AAPL --loss-pct 10 --gain-pct 25
```

```
==========================================================================================
Watch List Alert Report (loss >=10.0%, gain >=25.0%)
==========================================================================================
Symbol     Period   Dir    Baseline Date     Baseline    Current  % Change
------------------------------------------------------------------------------------------
NVDA       1mo      loss   2026-10-15          217.23     175.66   -19.14%
TSLA       ytd      loss   2026-01-01          449.72    392.275   -12.77%
AAPL       ytd      gain   2026-01-01          271.356    353.985    30.45%
==========================================================================================
```

Symbols that are not tracked, or have no data for a period, are skipped silently (no error). If
a symbol you expected is missing, check it with `--stats`.

### Step 5 - get the same thing as JSON from the server

With the server running ([section 6](#6-web-server-and-api)), the same check is an HTTP call, handy
for scripts and dashboards:

```bat
curl "http://localhost:5000/watchlist?symbols=NVDA,TSLA,AAPL&loss_pct=10&gain_pct=25"
```

```json
{"alerts":[{"baseline_close":217.23,"baseline_date":"2026-10-15","current_close":175.66,
"direction":"loss","percent_change":-19.1364,"period":"1mo","symbol":"NVDA"}, ...],
"gain_pct":25.0,"loss_pct":10.0}
```

`symbols`, `loss_pct` and `gain_pct` are all optional and follow the same rules as the CLI.
Open `http://localhost:5000/chart/NVDA,TSLA,AAPL?span=3mo` to see the symbols side by side
(see [section 10](#10-charts)).

### Step 6 - run it on a schedule

A weekly check on Tuesday and Friday mornings, as two chained commands in a small script, e.g.
`run_watchlist.bat`:

```bat
@echo off
cd /d C:\path\to\local_stock
python stock_system.py --update       >> watchlist.log 2>&1
python stock_system.py --watchlist    >> watchlist.log 2>&1
```

```bat
schtasks /Create /TN "Watchlist" /SC WEEKLY /D TUE,FRI /ST 08:00 /TR "C:\path\to\local_stock\run_watchlist.bat"
```

Things to know:
- **It only reports; it does not notify.** The command always exits with code 0, whether or
  not anything matched, so a script cannot tell "no alerts" from "alerts" without reading the
  output. Email/phone notifications and a match-aware exit code are planned in
  [task_plan.md](task_plan.md).
- Money-market funds are included in "all tracked symbols" but almost never trigger, because
  their synthetic price moves slowly.
- Percentages come from stored closes. If `--update` could not fetch a symbol (check
  `stock_system.log`), its row reflects the last successful date.

---

## 5. Command reference

Run as `python stock_system.py <flags>`. Aliases are shown with `/`.

### Database and symbols
| Flag | What it does |
|---|---|
| `--db PATH` | Use this database file instead of the default (works with every action). |
| `--init` | Create tables/config/data sources and add QQQ + CSCO reference symbols. |
| `--add SYMBOL` | Track a symbol; repeat the flag for several. Add `--since DATE` to download history immediately. |
| `--remove [SYMBOL]` | Delete a symbol and all its quotes (no confirmation). With no symbol, delete all symbols that have 0 quotes. |
| `--dbpath` | Print the database path in use. |
| `--version` | Print the version and the latest changelog entry. |

### Updating data
| Flag | What it does |
|---|---|
| `--update` | Incrementally update all symbols. |
| `--update SYMBOL` | Force-refresh one symbol (default window: last 7 days). |
| `--years N` | History to fetch for new symbols; with `--update SYMBOL`, the force-refresh window. |
| `--since YYYY-MM-DD` | Absolute start date; overrides `--years`. Backfills gaps on existing symbols. |
| `--update-fundamentals [LIST]` | Store P/E, dividend yield, expense ratio (all symbols, or `AAPL,MSFT`). |

### Information and checks
| Flag | What it does |
|---|---|
| `--stats` / `--statistics` | Database summary and per-symbol table with last price. |
| `--prices` | Current price for each tracked symbol. |
| `--watchlist [LIST]` | Threshold report vs 1wk / 1mo / YTD (see [section 4](#4-watch-list-example-walkthrough)). |
| `--loss-pct N`, `--gain-pct N` | Thresholds for `--watchlist`. |
| `--closures` | List market-closure dates. |
| `--import-closures [FILE]` | Load closures from a CSV (`DATE[,REASON]` per line, `#` comments). No file: seed the built-in holiday list. |

### Export
| Flag | What it does |
|---|---|
| `--export` / `--exportcsv` | Write quotes to CSV. |
| `--filename FILE` | Output file (default `stock_system_quotes.csv`). |
| `--symbol SYM` | Export one symbol only (default all). |

```bat
python stock_system.py --export --symbol QQQ --filename qqq.csv
```

### Money-market funds
| Flag | What it does |
|---|---|
| `--add-fund SYMBOL` | Add a money-market fund and seed its yield history ([section 8](#8-money-market-funds)). |
| `--expense-ratio FRACTION` | Override the fund's expense ratio for `--add-fund` (e.g. `0.0011`). |
| `--list-funds` | List money-market funds and how many synthetic rows each has. |
| `--rebuild-fund [SYMBOL]` | Rebuild synthetic prices from stored yields, then verify (no symbol = all). |
| `--verify-fund [SYMBOL]` | Verify stored synthetic prices against a recomputed series. |
| `--reclassify` | Re-detect each symbol's type (equity, ETF, mutual fund, money market) via yfinance. |

### Configuration
| Flag | What it does |
|---|---|
| `--config list` | Show config values and data sources (**prints API keys in full**; do not paste the output anywhere). |
| `--config get KEY` | Show one value. |
| `--config set KEY VALUE` | Set a value (`update_time`, `cache_duration`, `default_years`). |
| `--config set apikey SOURCE KEY` | Store an API key for `alphavantage`, `fmp` or `finnhub`. |
| `--config set priority SOURCE RANK` | Set fetch priority (lower number = tried first). |

### Server
| Flag | What it does |
|---|---|
| `--server` / `--start` / `--startserver` | Run the web server (and the daily auto-update). |
| `--port N` | Port to listen on (default 5000; 5050 with `--secondary`). |
| `--secondary` | Run or stop a second, independent instance (own lock/port/log files, port 5050, no auto-update) so you can test alongside the main one. |
| `--stop` / `--shutdown` / `--stopserver` | Stop the running server (add `--secondary` for the second one). |

---

## 6. Web server and API

Start it (the batch files wrap these commands):

```bat
start_server.bat                          :: background window, port 5000
python stock_system.py --server           :: foreground; Ctrl+C to stop
stop_server.bat                           :: or: python stock_system.py --stop
```

The server listens on `127.0.0.1` only, so it is reachable from this PC but not from the
network. Open `http://localhost:5000/` for the built-in endpoint list.

### Quotes
| Endpoint | Returns |
|---|---|
| `/quote/AAPL` | Latest close (plain number). |
| `/quote/AAPL/now` | Live quote when the market is open (cached 15 min), else last close. |
| `/quote/AAPL/2025-01-15` | Close on that date (nearest earlier day if closed). |
| `/quote/AAPL/field/high` | Latest `open`, `high`, `low`, `close` or `volume`. |
| `/quote/AAPL/field/pe` | Fundamentals: `pe`, `pe_forward`, `xr` (expense ratio), `divyield`. |
| `/quote/AAPL,MSFT,NVDA` | **Comma list** gives a CSV row, in order: `353.985,520.01,175.66`. Missing symbol gives `#N/A`. Works for `/now`, a date, and `/field/...` too. |
| `/latest_date/AAPL` | Most recent date with data. |
| `/yield/VMFXX` | Latest annualised yield of a money-market fund (e.g. `0.039577`). |
| `/name/AAPL,MSFT` | Names (CSV). |
| `/fund_type/AAPL,VMFXX` | Asset type (EQUITY, ETF, MUTUALFUND, MONEY_MARKET). |

### Analysis, data and admin
| Endpoint | Returns |
|---|---|
| `/watchlist?symbols=&loss_pct=&gain_pct=` | JSON alerts ([section 4](#4-watch-list-example-walkthrough)). |
| `/chart/AAPL,MSFT?span=1yr` | Line chart page ([section 10](#10-charts)). |
| `/stats` | Database statistics page. |
| `/cache/html` | Contents of the live-quote cache. |
| `/funds` | JSON list of money-market funds. |
| `/export/QQQ` | HTML table of all quotes for a symbol (can be large). |
| `/closures` | JSON list of closure dates. |
| `/update/AAPL` | Force-refresh one symbol. |
| `/config`, `/health`, `/version` | Configuration (read-only), health check, version + changelog. |
| `/shutdown` | Stop the server. |

Requesting a symbol you have never added (`/quote/NEWSYM`) auto-fetches about 30 days of data
and starts tracking it. Batch (comma) requests do **not** auto-fetch.

---

## 7. Using it from LibreOffice Calc

Start the server, then use `WEBSERVICE()`:

```
Cell A1: AAPL
=WEBSERVICE("http://localhost:5000/quote/"&A1)               -> latest close
=WEBSERVICE("http://localhost:5000/quote/"&A1&"/now")         -> live quote
=WEBSERVICE("http://localhost:5000/quote/"&A1&"/field/high")  -> latest high
=WEBSERVICE("http://localhost:5000/quote/"&A1&"/2025-01-15")  -> close on a date
=WEBSERVICE("http://localhost:5000/latest_date/"&A1)          -> last update date
=WEBSERVICE("http://localhost:5000/quote/"&A1&"/field/pe")    -> trailing P/E
=WEBSERVICE("http://localhost:5000/yield/VMFXX")              -> money-market yield
```

Wrap with `VALUE()` if you need a number: `=VALUE(WEBSERVICE(...))`.

A portfolio sheet is then ordinary spreadsheet math. Columns A = symbol, B = shares:

```
C2: =VALUE(WEBSERVICE("http://localhost:5000/quote/"&A2))    price
D2: =B2*C2                                                    value
E2: =WEBSERVICE("http://localhost:5000/name/"&A2)             name
```

Fill the formulas down. Calc makes one web request per cell, so a long list is slower than one
batch call: `/quote/AAPL,VTI,VOO` returns a single CSV string in one request.

**Error 540 / `#VALUE!`:** LibreOffice may block localhost. Check the server is running
(`http://localhost:5000/health`) and, if it is, that Calc is allowed to make web requests.

---

## 8. Money market funds

Money-market funds always quote $1.00, so there are no price moves to store. The system builds
a **synthetic price** that accrues the fund's yield day by day, which makes the fund chart and
compare like any other holding.

```bat
python stock_system.py --add-fund SPAXX                      :: add + seed history from FRED
python stock_system.py --add-fund VMFXX --expense-ratio 0.0011   :: override the fee
python stock_system.py --list-funds                          :: coverage per fund
python stock_system.py --verify-fund                         :: check all funds
python stock_system.py --rebuild-fund VMFXX                  :: rebuild one fund's prices
```

How the yield is chosen, in priority order: real fund distributions from FMP (only if your FMP
plan covers funds; the free plan does not), then the fund's own yield from yfinance combined
with the Federal Funds Rate history from FRED (no key needed), then FRED minus the expense
ratio as a last resort. Re-running `--add-fund` on an existing fund re-fetches and rebuilds it.

`--update` keeps these funds current along with everything else.

---

## 9. Fundamentals (P/E, dividend yield, expense ratio)

```bat
python stock_system.py --update-fundamentals                 :: all tracked symbols
python stock_system.py --update-fundamentals AAPL,MSFT,VOO   :: just these
```

Then read them from the API or Calc:

```
/quote/AAPL/field/pe                       39.053   (trailing P/E)
/quote/AAPL/field/pe_forward               forward P/E
/quote/VOO/field/xr                        0.03   (expense ratio, in PERCENT: 0.03 = 0.03%)
/quote/AAPL,MSFT,VOO/field/divyield        0.003079,0.007052,0.007669   (FRACTIONS)
```

**The two scales differ.** `divyield` is a fraction (`0.0077` = 0.77%), but `xr` is stored
exactly as yfinance reports it, which is a percent (`0.03` = 0.03%). Multiply `divyield` by 100
to show a percent; use `xr` as is. Values are snapshots from yfinance as of the day you ran the
command; refresh periodically. A symbol with no stored fundamentals returns `#N/A`.

---

## 10. Charts

```
http://localhost:5000/chart/AAPL,MSFT,VOO?span=1yr
http://localhost:5000/chart/AAPL,VMFXX?span=6mo&prices
http://localhost:5000/chart?span=ytd
```

- `span`: `1mo`, `3mo`, `6mo` (default), `1yr`, `ytd`, `5yr`.
- No symbols given: charts all tracked symbols.
- Two or more symbols are plotted as **% change from the start of the span**, so a $1 money
  market fund and a $700 ETF share one axis. Add `&prices` for raw prices.
- One symbol is plotted as price; add `&comparePct` to force percent.
- Symbols with no data in range are listed at the top of the page instead of silently dropped.
- The page loads Chart.js from a CDN, so the browser needs internet access.

---

## 11. Configuration and API keys

```bat
python stock_system.py --config list
python stock_system.py --config get update_time
python stock_system.py --config set update_time 17:30
```

| Key | Default | Meaning |
|---|---|---|
| `update_time` | `18:30` | Time of the automatic daily update (local time). Restart the server after changing it. |
| `cache_duration` | `900` | Live-quote cache lifetime in seconds. |
| `default_years` | `3` | History fetched for new symbols. |
| `last_update_run` | (set automatically) | When the last full update finished. The server uses it to decide on a start-up catch-up; do not edit it. |

**Data sources.** `yfinance` is the default and needs no key. Others act as fallbacks:

| Source | Key needed | Free limit | Note |
|---|---|---|---|
| yfinance | no | generous | Primary. Priority 1. |
| finnhub | yes | 60 calls/min | Fallback. |
| alphavantage | yes | 25 calls/day | Throttled to 5 calls/min; rarely has mutual funds. |
| fmp | yes | 250 calls/day | Does not cover funds on the free plan. |

```bat
python stock_system.py --config set apikey finnhub YOUR_KEY
python stock_system.py --config set apikey alphavantage YOUR_KEY
python stock_system.py --config set priority finnhub 2
```

Keys are stored in the database in plain text. **Never commit a file containing keys** (for
example a batch file of `--config set apikey ...` lines) to a public repository.

To set several keys at once, the repo ships `my_api_keys.bat` as a **sample** with fake keys.
Copy it to `api_keys_donotdisclose.bat` (that name is git-ignored), put your real keys in the
copy, and run the copy from the project folder. The sample refuses to run under its own name,
so it cannot overwrite your real keys with the fake ones.

---

## 12. Scheduling and automation

**Built in:** while the server is running, it updates all symbols at `update_time` (default
18:30 local time), **Monday to Saturday**, in the background.

**Missed runs are made up (v1.2.1 and later).** A PC that is asleep or hibernating at update
time, or a server that was not running, no longer skips the day:
- If the PC wakes up late, the missed run fires as soon as the scheduler wakes (the log shows
  `Run time of job "scheduled_update ..." was missed by ...`, then `Starting scheduled update`).
- When the server starts, it compares the time of the last finished update (stored as config key
  `last_update_run`, shown by `--config list`) with the most recent Mon-Sat slot. If no update
  has finished since that slot, it runs a catch-up update about 5 seconds after start.
  A manual `--update` counts as an update.

Still not covered: the PC being off and the server not started at all. Start the server at login
for that. If every data source is unreachable when the catch-up runs, the run still counts as
done and the next try is the next slot. Check the history with:

```bat
powershell -Command "Select-String 'scheduled update|was missed|Catch-up' $env:USERPROFILE\stock_system.log | Select-Object -Last 10"
```

**Updates never store an unfinished day.** On a weekday before 16:30 ET (13:30 Pacific), while
the market is open or has not yet opened, an update only fetches up to the previous day. Before
v1.2.1 a mid-session update saved the half-formed candle as that day's close, and because updates
continue from the last stored date, it was never corrected. The evening run now picks up the
finished day. To repair a day that was already stored mid-session (it shows an unusually low
volume), run this **after** 16:30 ET, with that day's date:

```bat
python stock_system.py --update --since 2026-11-16
```

**Start the server at login (Windows):** put a shortcut to `start_server.bat` in the Startup
folder (`Win+R`, then `shell:startup`).

**Without the server:** schedule the update as a task. Task Scheduler, GUI route:
Create Basic Task, trigger Daily 6:30 PM, action `python`, arguments
`C:\path\to\stock_system.py --update`, "Start in" = the script's folder. Or one line:

```bat
schtasks /Create /TN "Stock Update" /SC DAILY /ST 18:30 /TR "python C:\path\to\local_stock\stock_system.py --update"
```

For the watch list on a schedule see [section 4, step 6](#step-6---run-it-on-a-schedule).

---

## 13. Maintenance: backup, export, SQL

**Backup:** stop the server, copy `stock_quotes.db`, start it again. (A plain file copy while
a write is in progress can be inconsistent.)

```bat
python stock_system.py --stop
copy "%USERPROFILE%\stock_quotes.db" "%USERPROFILE%\stock_quotes.backup.db"
```

**CSV export:** see [section 5](#export).

**Direct SQL.** The database is standard SQLite. Main tables: `daily_quotes` (symbol,
quote_date, open, high, low, close, volume, dividends, stock_splits, data_source,
last_updated), `symbols`, `config`, `data_sources`, `market_closures`, `fund_metrics`,
`fund_distributions`, `symbol_fundamentals`.

```python
import os, sqlite3
import pandas as pd

conn = sqlite3.connect(os.path.expanduser('~/stock_quotes.db'))
latest = pd.read_sql_query('''
    SELECT symbol, quote_date, close FROM daily_quotes d
    WHERE quote_date = (SELECT MAX(quote_date) FROM daily_quotes WHERE symbol = d.symbol)
    ORDER BY symbol
''', conn)
print(latest.to_string(index=False))
conn.close()
```

Reading is safe while the server runs. Do not write to the tables by hand; use the CLI.

---

## 14. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError` (yfinance, flask, apscheduler...) | `python -m pip install flask apscheduler yfinance pandas numpy` |
| `Error: Database not found` when starting the server | `python stock_system.py --init`, or point at the right file with `--db`. |
| `Server is already running (PID: ...)` | `python stock_system.py --stop`. If it says "stale PID file", it cleans up the lock files for you. |
| Port 5000 busy | `python stock_system.py --server --port 5001` (and use that port in your URLs). |
| A symbol shows `#N/A` or "No data" | `python stock_system.py --add SYM`, then `--update`. Check spelling and that the ticker exists on Yahoo. |
| Prices are a few days old | Run `python stock_system.py --update`; the watch list and `--stats` use stored closes. Then look in the log. |
| Rows missing open/high/low | `python stock_system.py --update SYM` (or `/update/SYM`). |
| Gap in history | `python stock_system.py --update SYM --since YYYY-MM-DD` |
| Chart is blank | Reload; Chart.js comes from a CDN. A symbol with no data in the span is listed in red text at the top of the page. |
| Money-market fund price looks wrong | `--verify-fund SYM`, then `--rebuild-fund SYM`, or `--add-fund SYM` to reseed. |
| Calc shows Error 540 / `#VALUE!` | Server not running, wrong port, or Calc blocking web requests. Test `http://localhost:5000/health` in a browser. |
| "database is locked" | Another process is writing. Wait, or stop the server and retry. |

**Log file:** `%USERPROFILE%\stock_system.log` (secondary server: `stock_system_secondary.log`).

```bat
powershell -Command "Get-Content $env:USERPROFILE\stock_system.log -Tail 50"
```

---

## 15. Files and locations

In the project folder:

| File | Purpose |
|---|---|
| `stock_system.py` | The whole system. |
| `tests/` | Network-free unit tests: `python -m unittest discover -s tests -v`. |
| `install.bat` | One-command install: packages, portfolio, start server. |
| `setup.bat` | Create DB, add the portfolio, fetch 3 years (edit to change symbols). |
| `start_server.bat` / `stop_server.bat` | Start the server in the background / stop it. |
| `my_api_keys.bat` | SAMPLE only (fake keys). Copy to `api_keys_donotdisclose.bat` for real keys. |
| `README.md` | Feature overview and schema. |
| `LINUX_PLAN.md` | Plan for Linux support. |
| `task_plan.md` | Plan for automated watch-list notifications. |

In your user folder (`%USERPROFILE%`, e.g. `C:\Users\YourName`):

| File | Purpose |
|---|---|
| `stock_quotes.db` | The database. **Back this up.** |
| `stock_system.log` | Log. |
| `stock_system.lock`, `stock_system.port` | Present while the server runs (PID and port). |
| `stock_system_secondary.*` | Same, for the `--secondary` instance. |
