# Local One-Click Runtime

This project can be started after a Windows reboot with the root-level batch files or the GUI controller. For sustained local operation, use PostgreSQL. SQLite remains supported for lightweight smoke tests, but it should not be used for high-concurrency polling, reporting, pipeline work, or long-running local service use.

## Recommended Database

Use PostgreSQL for normal local operation:

```powershell
DATABASE_URL=postgresql+psycopg://web3_news:web3_news@127.0.0.1:15432/web3_news_intel
```

During startup, `scripts\local_runtime.ps1` checks that the PostgreSQL TCP endpoint in `DATABASE_URL` is reachable and runs:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Migration output is written to `.runtime\alembic.out.log` and `.runtime\alembic.err.log`. If PostgreSQL is unreachable or migrations fail, startup stops before API, worker, and frontend startup continue.

If you use the Compose PostgreSQL service from this repository, `docker-compose.yml` maps PostgreSQL to host port `15432` by default and the service is configured with `restart: unless-stopped`. When local `DATABASE_URL` points to `localhost:15432` or `127.0.0.1:15432`, one-click startup will try `docker compose up -d postgres` automatically if PostgreSQL is not reachable. For any other PostgreSQL host or port, start PostgreSQL yourself before running one-click startup.

SQLite is only for lightweight local tests:

```powershell
DATABASE_URL=sqlite+pysqlite:///C:/path/to/web3_news_site_run.sqlite3
```

When SQLite is detected, the runtime skips automatic migrations and the worker manager uses the low-concurrency profile to reduce lock contention.

## Worker Profiles

`scripts\dev_runtime.ps1` derives worker concurrency from `DATABASE_URL`:

| Database | Profile | AI | Report | Fetch | Pipeline |
| --- | --- | --- | --- | --- | --- |
| SQLite | `sqlite-low-concurrency` | `solo`, `1` | `solo`, `1` | `threads`, `2` | `threads`, `1` |
| PostgreSQL | `postgresql-standard` | `threads`, `2` | `solo`, `1` | `threads`, `8` | `threads`, `4` |
| Unknown | `unknown-db-low-concurrency` | `solo`, `1` | `solo`, `1` | `threads`, `1` | `threads`, `1` |

Every worker start now stops managed worker PIDs and any stale Celery process using this project's `.venv` and `app.workers.celery_app` before starting fresh workers. This prevents old workers from continuing to consume Redis queue jobs after a database or code change.

## GUI Controller

Double-click `dist\Web3NewsController.exe` to open the Windows controller. The controller has buttons for start, stop, status, opening the frontend, opening logs, and opening the project directory. It uses `scripts\local_runtime.ps1` internally, so it follows the same startup and stop rules as the batch files.

## Start

Double-click `start_all.bat`, or run:

```powershell
.\scripts\start_all.ps1
```

It starts or verifies these services in order:

1. Redis on `127.0.0.1:6379`
2. Database readiness from `DATABASE_URL`
3. PostgreSQL migrations when `DATABASE_URL` is PostgreSQL
4. API on `http://127.0.0.1:59134`
5. Celery `ai-worker`, `report-worker`, `fetch-worker`, `pipeline-worker`, and `scheduler`
6. Frontend on `http://127.0.0.1:5173/`

When startup succeeds, the script opens the frontend in the browser.

## Stop

Double-click `stop_all.bat`, or run:

```powershell
.\scripts\stop_all.ps1
```

The stop script stops processes recorded in `.runtime\*.pid` by the one-click runtime and asks `scripts\dev_runtime.ps1` to stop managed or stale same-project Celery worker processes. It does not stop PostgreSQL.

## Status

Double-click `status_all.bat`, or run:

```powershell
.\scripts\status_all.ps1
```

Status checks database readiness, Redis, API, frontend, worker/scheduler state, and Feishu report schedules including next run time and last result.

## Logs

Runtime logs are written to `.runtime\`:

- `.runtime\api-dev.out.log` / `.runtime\api-dev.err.log`
- `.runtime\frontend.out.log` / `.runtime\frontend.err.log`
- `.runtime\ai-worker.*.log`
- `.runtime\report-worker.*.log`
- `.runtime\fetch-worker.*.log`
- `.runtime\pipeline-worker.*.log`
- `.runtime\scheduler.*.log`
- `.runtime\redis.*.log`
- `.runtime\alembic.*.log`

## Notes

If API or frontend is already reachable, startup will reuse it and try to adopt the listening PID so `stop_all` can manage it. Worker processes are always restarted by the worker manager so the current database profile and code are used.
