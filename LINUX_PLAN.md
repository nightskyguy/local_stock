# Plan: Linux Usage and Scripts

## Goal
Run the Stock Quote System on Linux with the same workflow as on Windows: install, set up the
portfolio, run the server, update daily, run the watch list on a schedule. Windows keeps
working unchanged.

This is a plan only. No code has been changed for it yet. Companion plan:
[task_plan.md](task_plan.md) (automated watch-list notifications), which this work feeds.

## Status
| Phase | State |
|---|---|
| 0. Decisions | pending, **decisions needed** |
| 1. Portability fixes in `stock_system.py` | pending |
| 2. Linux scripts and packaging | pending |
| 3. Documentation | pending |
| 4. CI on Linux (and Windows) | pending |
| 5. Real-machine validation | pending, no Linux available locally |
| 6. Optional extras (Docker, macOS) | not started |

## Known facts (from code review of `stock_system.py` v1.2.0)

**Already portable**
- All paths fall back to `~` when `USERPROFILE` is unset (`:79-85`, `:121-123`), so on Linux the
  database, log, lock and port files all land in the home folder, e.g. `~/stock_quotes.db`.
- `is_process_running()` and `stop_server()` already have Unix branches (`os.kill(pid, 0)` and
  `SIGKILL`; `:3543`, `:3642`). `subprocess.CREATE_NO_WINDOW` is only used inside the Windows
  branch.
- The code is pure Python (Flask, APScheduler, yfinance, pandas). Nothing imports a
  Windows-only module. CSV reads and writes already pass `encoding='utf-8'`.
- The server binds `127.0.0.1` only (`:4083`). LibreOffice `WEBSERVICE()` works the same way on
  Linux if Calc runs on the same machine.

**Not portable or not yet Linux-ready**
- Every entry point is a `.bat` file: `install.bat`, `setup.bat`, `start_server.bat`,
  `stop_server.bat`. There are no shell equivalents.
- No `requirements.txt`. `install.bat` hard-codes `flask apscheduler yfinance pandas numpy`.
- Modern Debian/Ubuntu (PEP 668) refuses `pip install` into the system Python, so a virtualenv
  is required.
- `is_process_running()` treats `PermissionError` as "not running". On Linux, `os.kill(pid, 0)`
  raises `PermissionError` for a live process owned by another user, so a lock owned by another
  user would be deleted as stale and a second server could start.
- `stop_server()` relies on the HTTP `/shutdown` route first, then `SIGKILL`. There is no
  `SIGTERM` handler, so `systemctl stop` / `kill` ends the process without running the `atexit`
  hook that removes the lock and port files. The next start recovers from the stale lock, but
  it is untidy and exposed to PID reuse.
- `ZoneInfo('America/New_York')` (`:2359`) needs a time-zone database. Linux normally has one at
  `/usr/share/zoneinfo`; minimal containers do not.
- The scheduler and `update_time` use the **machine's local time** (`:3770`). A server running
  in UTC would fire 18:30 UTC, not 18:30 US Eastern.
- `logging.basicConfig(filename=...)` has no explicit `encoding`. Under a `C`/`POSIX` locale
  (typical for cron and systemd) non-ASCII fund names can raise logging errors.
- `zoneinfo` needs **Python 3.9+**. `README.md` says 3.8+, which is wrong on every OS.
- This PC cannot test Linux directly: WSL reports "WSL 1 is not supported with your current
  machine configuration" and Docker is not installed.

**API keys (affects Linux scripts too)**
- `my_api_keys.bat` is a SAMPLE with fake keys and a guard that stops it running under that
  name. Real keys go in the git-ignored `api_keys_donotdisclose.bat`. The Linux scripts follow
  the same pattern (see 2.8).

---

## Phase 0: Decisions
- [x] 0.1 Keep real keys out of the tracked tree: `my_api_keys.bat` is a SAMPLE (banner, fake
      keys, and a guard that refuses to run under that name so it cannot overwrite real keys in
      the database). Real keys go in `api_keys_donotdisclose.bat`, covered by `.gitignore`
      (`api_keys_donotdisclose.*`).
- [ ] 0.2 Answer the open questions at the bottom.

**Exit criteria:** decisions recorded below.

---

## Phase 1: Portability fixes in `stock_system.py`
Small, behaviour-preserving on Windows. Each item gets a test (see Phase 4).

- [ ] 1.1 One home-directory constant. Replace the seven copies of
      `os.environ.get('USERPROFILE', os.path.expanduser('~'))` with a single `_HOME`, and let
      an env var `STOCK_HOME` override it. Default stays `~`, so existing databases are found.
      (Optional, see Q4: XDG paths `~/.local/share/stock_system/`.)
- [ ] 1.2 `is_process_running()`: catch `PermissionError` separately and return `True`
      (the process exists, we just may not signal it).
- [ ] 1.3 `SIGTERM` (and `SIGINT`) handler for `--server` that stops the scheduler and removes
      the lock and port files, so `systemctl stop` and `kill` shut down cleanly.
- [ ] 1.4 Logging: pass `encoding='utf-8'` to `basicConfig` and to the secondary
      `FileHandler`. Make the wrapper scripts export `PYTHONUTF8=1` as belt-and-braces.
- [ ] 1.5 Time zone: keep `update_time` as local time but **log the zone**, and document it. Add
      `tzdata` to `requirements.txt` so `ZoneInfo` works in slim images and on Windows.
      Decide Q5 on whether to add an `update_timezone` config key.
- [ ] 1.6 Optional (Q1): `--host` flag, default `127.0.0.1`, for reaching a headless server.
      Default stays loopback. The simpler alternative is an SSH tunnel (no code change).
- [ ] 1.7 `--help` / docs: no `%USERPROFILE%` wording where the path is really "your home folder".

**Exit criteria:** `python stock_system.py --version`, `--dbpath`, `--stats`, `--watchlist` and
`--server` / `--stop` all work on Ubuntu with an empty `USERPROFILE`, and Windows behaviour is
unchanged.

---

## Phase 2: Linux scripts and packaging
Layout proposal (Q3): a `linux/` folder, leaving the `.bat` files at the root.

- [ ] 2.1 `requirements.txt`: `flask`, `apscheduler`, `yfinance`, `pandas`, `numpy`, `tzdata`.
      Minimum versions only; no hard pins. `install.bat` switches to
      `pip install -r requirements.txt` so both OSes share one list.
- [ ] 2.2 `linux/install.sh`: check `python3 >= 3.9`, create `.venv`, install requirements,
      then call `setup.sh`. Safe to re-run.
- [ ] 2.3 `linux/setup.sh`: `--init`, `--add ...`, `--update --years 3`. To avoid the symbol
      list drifting between `setup.bat` and `setup.sh`, move the list to a shared
      `symbols.txt` that both read (Q3).
- [ ] 2.4 `linux/start_server.sh` and `linux/stop_server.sh`: start with `nohup` and a log
      file, wait, then check `curl -fsS http://localhost:5000/health`; stop via `--stop`.
      Always run the venv's Python by absolute path, and `cd` to the project folder.
- [ ] 2.5 systemd **user** service `linux/stock-system.service`:
      `ExecStart=%h/local_stock/.venv/bin/python %h/local_stock/stock_system.py --server`,
      `Restart=on-failure`, `WantedBy=default.target`. Document
      `systemctl --user enable --now stock-system` and `loginctl enable-linger $USER` so it
      keeps running when logged out. The built-in scheduler then handles the daily update.
- [ ] 2.6 For people who do not run the server: a `stock-update.service` + `.timer`
      (`OnCalendar=Mon..Sat 18:30`, `Persistent=true`) **or** a cron line
      `30 18 * * 1-6 /path/.venv/bin/python /path/stock_system.py --update`. Document both;
      cron needs absolute paths because of its minimal `PATH`.
- [ ] 2.7 `linux/run_watchlist.sh`: the Linux twin of `run_watchlist.ps1` from
      `task_plan.md` Phase 0.6: `--update`, then `--watchlist`, log to a file. Build it **after**
      the exit-code change (task_plan 0.1) so the script can act on "matches" vs "none".
      Cron example: `0 8 * * 2,5`.
- [ ] 2.8 `linux/my_api_keys.sh` as a SAMPLE with fake keys and the same run-under-this-name
      guard as the `.bat`; the real copy is `api_keys_donotdisclose.sh`, already covered by the
      `api_keys_donotdisclose.*` rule in `.gitignore`.
- [ ] 2.9 `chmod +x` the scripts and commit them with the executable bit
      (`git update-index --chmod=+x`), and make sure they use `LF` line endings
      (`.gitattributes`: `*.sh text eol=lf`; `*.bat text eol=crlf`).

**Exit criteria:** on a fresh Ubuntu, `linux/install.sh` followed by `linux/start_server.sh`
gives a working `http://localhost:5000/quote/AAPL`, and a reboot (with linger enabled) brings
the service back.

---

## Phase 3: Documentation
- [ ] 3.1 `README.md`: Requirements say "Windows 10/11" and "Python 3.8+". Change to
      "Windows or Linux" and "Python 3.9+". Add a Linux quick start. Fix the stale
      scheduler text while there: README says default `update_time` is `15:30` and runs Mon-Fri,
      but the code default is `18:30` and the job runs **Mon-Sat**.
- [ ] 3.2 `USER_GUIDE.md`: add a Linux section (install, service, cron, log location,
      `STOCK_HOME`). Give the Windows and Linux form of the few shell-specific commands
      (`copy` vs `cp`, `type` vs `tail`, `start_server.bat` vs `linux/start_server.sh`).
      Update [section 1](USER_GUIDE.md#1-quick-start)'s "Linux is not officially supported yet" line.
- [ ] 3.3 `install.bat` prints "Daily updates happen automatically at 3:30 PM": make it match
      the real default (18:30).
- [ ] 3.4 Note for LibreOffice-on-Linux users: `WEBSERVICE()` behaviour, and reaching a
      headless server from another machine (SSH tunnel `ssh -L 5000:localhost:5000 host`).

---

## Phase 4: CI on Linux (and Windows)
This is also the only way to test Linux from this PC.

- [ ] 4.1 `.github/workflows/ci.yml`: matrix `ubuntu-latest` + `windows-latest`, Python
      `3.9` and `3.12`. Steps: install `requirements.txt`, `python -m py_compile
      stock_system.py`, run tests.
- [ ] 4.2 `tests/`: **network-free** tests. `--init` and `--add` call yfinance
      (`add_symbol` classifies the symbol), so the tests build a temp database directly
      (`setup_database()` plus inserted rows) and then run through the real CLI with `--db`:
      `--version`, `--dbpath`, `--stats`, `--watchlist` (known thresholds, expected rows),
      `--export`.
- [ ] 4.3 Server smoke test: start `--server --secondary --port <free>` against the temp DB,
      `curl` `/health`, `/quote/X`, `/watchlist?...`, then `--stop --secondary`; assert the
      lock and port files are gone. This exercises the Unix process-check and kill paths for
      real. Add a `SIGTERM` case once 1.3 lands.
- [ ] 4.4 Unit tests for 1.2 (permission case, mocked `os.kill`) and 1.1 (`STOCK_HOME`).
- [ ] 4.5 Keep any live-network smoke test (`--update` for one symbol) optional and allowed to
      fail, since Yahoo throttles shared CI IPs.
- [ ] 4.6 Shared with `task_plan.md`: these tests also satisfy its Phase 0.7, and its
      Option C1 (GitHub Actions cron) depends on the app already running on `ubuntu-latest`.

**Exit criteria:** green matrix on both OSes for every push and PR.

---

## Phase 5: Real-machine validation
CI covers the code paths; it does not cover cron/systemd behaviour or LibreOffice on Linux.
Options (Q6): a cloud VM or Raspberry Pi, VirtualBox (this PC is Windows 11 Home, so no
Hyper-V), or WSL2 if virtualization can be enabled (WSL1 is reported unsupported here).

- [ ] 5.1 Fresh Ubuntu LTS: `install.sh`, `start_server.sh`, the three `curl` checks from the
      USER_GUIDE.
- [ ] 5.2 systemd user service survives reboot with linger on.
- [ ] 5.3 Cron / timer fires `--update` under the minimal environment (no `PATH`, no locale).
- [ ] 5.4 If Calc is available: the `WEBSERVICE()` formulas from USER_GUIDE section 7.

---

## Phase 6: Optional extras (only if wanted)
- [ ] 6.1 `Dockerfile` + `docker-compose.yml`: volume for the database, `STOCK_HOME=/data`,
      `tzdata` installed, published as `127.0.0.1:5000`.
- [ ] 6.2 macOS notes: the `.sh` scripts mostly work; scheduling uses `launchd` instead of
      systemd. Document only, unless someone needs it.

---

## Open questions (need user decision)
1. **Target:** a headless server (then consider `--host` / SSH tunnel and systemd), a desktop
   Linux with LibreOffice on the same box, or both? Drives 1.6 and 3.4.
2. **Distros:** Ubuntu/Debian only, or also Fedora, Arch, Raspberry Pi OS?
3. **Layout:** a `linux/` subfolder with a shared `symbols.txt`, or `.sh` files next to the
   `.bat` files at the root?
4. **State location:** keep `~/stock_quotes.db` and friends (same as Windows, no migration), or
   move Linux to XDG paths? Recommendation: keep `~` and add the `STOCK_HOME` override.
5. **Update time zone:** is "machine local time" acceptable, or should the daily update be
   pinned to US Eastern regardless of where the server runs?
6. **Validation:** is CI alone enough, or do you want a real VM / Pi test pass (Phase 5)?
7. **Docker:** wanted (Phase 6.1) or skip?

## Suggested order
1. Phase 0: answer the open questions above.
2. Phase 4 skeleton first (CI + network-free tests on both OSes): it gives a safety net for
   Phase 1 and shows the real Linux behaviour before any scripts are written.
3. Phase 1, then Phase 2, then Phase 3.
4. Phase 5 once scripts exist. Phase 6 only on request.
5. Hand over to `task_plan.md`: Option C1 (cloud schedule) and `run_watchlist.sh` both depend on
   this.

## Errors / decisions log
| Date | Item | Note |
|---|---|---|
| 2026-11-14 | Plan created | Based on code review of v1.2.0. No code changed. WSL1/Docker unavailable locally, so CI is the test route. |
| 2026-11-16 | Change | `my_api_keys.bat` is now a sample with fake keys; real keys go in the git-ignored `api_keys_donotdisclose.bat`. |
