# Production Acceptance

Date: 2026-06-23

Evaluated commit: `64685c1247ca33d90bfb72acc06cb608c233112f`

Status: local acceptance gates have passed for the evaluated commit. Production
release remains `BLOCKED` until the same commit has GitHub Actions evidence and
real-service Compose evidence for PostgreSQL, Redis/Celery, API health, and
fixture E2E.

## Verified Local Evidence

| Gate | Command | Result | Scope |
| --- | --- | --- | --- |
| Git repository | `git rev-parse HEAD` | PASS | Current workspace is a Git repository at `64685c1247ca33d90bfb72acc06cb608c233112f` |
| Ruff | `python scripts/pre_push_acceptance.py` | PASS | Backend lint gate |
| Mypy | `python scripts/pre_push_acceptance.py` | PASS | Backend type gate for `app` and `scripts` |
| Unit tests | `python scripts/pre_push_acceptance.py` | PASS | Deterministic backend unit tests |
| Fixture integration | `python scripts/pre_push_acceptance.py` | PASS | Integration tests excluding PostgreSQL, Redis, Celery, Compose, and live sources |
| Source validation | `python scripts/pre_push_acceptance.py` | PASS | Runtime source validation |
| Source strict contract | `python scripts/validate_sources.py sources.yaml --strict-contract --catalog-dir source_catalog` | PASS | Source catalog contract and security defaults |
| Frontend typecheck | `cd frontend && npm run typecheck` | PASS | TypeScript compile check |
| Frontend tests | `cd frontend && npm test` | PASS | Vitest frontend suite |
| Frontend build | `cd frontend && npm run build` | PASS | Production frontend bundle |
| Compose config | `docker compose config --quiet` | PASS | Static Compose configuration validation only |

## Still Required For Production Release

| Gate | Required Evidence | Current Status | Reason |
| --- | --- | --- | --- |
| GitHub Actions for current commit | Successful run URL for `64685c1247ca33d90bfb72acc06cb608c233112f` | MISSING | Local passes do not replace CI evidence |
| `quality` job | GitHub Actions pass on the evaluated commit | MISSING | Must confirm clean runner environment |
| `frontend-quality` job | GitHub Actions pass on the evaluated commit | MISSING | Must confirm frontend install/test/build in CI |
| PostgreSQL migration cycle | `postgres-integration` Actions job or equivalent service log | MISSING | Requires real PostgreSQL service |
| PostgreSQL integration | `postgres-integration` Actions job with zero service-gated skips | MISSING | Local fixture tests do not cover PostgreSQL behavior |
| Redis/Celery real task execution | `redis-celery-integration` Actions job with zero service-gated skips | MISSING | Requires real Redis broker and Celery worker |
| Retry and worker recovery | `redis-celery-integration` Actions job logs | MISSING | Must prove transient retry and worker-loss recovery against services |
| Compose stack startup | `compose-acceptance` Actions job or captured production-like run | MISSING | `docker compose config` does not start containers |
| API health | Compose-backed health check evidence | MISSING | Requires running API service |
| Compose fixture E2E | Compose-backed fixture E2E output | MISSING | Requires running API, worker, database, and broker services |
| Event/delivery idempotency | Compose-backed test evidence | MISSING | Fixture-only local coverage is not enough for release |

## Local Commands

The current local gate evidence includes:

```text
python scripts/pre_push_acceptance.py
python scripts/validate_sources.py sources.yaml --strict-contract --catalog-dir source_catalog
cd frontend && npm run typecheck
cd frontend && npm test
cd frontend && npm run build
docker compose config --quiet
```

`docker compose config --quiet` is a configuration parser check. It does not
prove image builds, container startup, service health, migrations, worker task
execution, or Compose-backed E2E behavior.

## Git And CI Status

| Item | Result | Evidence |
| --- | --- | --- |
| Git repository | PASS | `git rev-parse HEAD` returns `64685c1247ca33d90bfb72acc06cb608c233112f` |
| GitHub remote | PASS | `origin` is configured as `https://searchyy@github.com/searchyy/web3-news-intel.git` |
| Commit SHA | PASS | Evaluated commit is `64685c1247ca33d90bfb72acc06cb608c233112f` |
| Pull request | NOT VERIFIED | No PR URL is recorded in this document |
| CI run | NOT VERIFIED | No GitHub Actions run ID or URL is recorded for the evaluated commit |
| Production-like services | NOT VERIFIED | No current evidence for running PostgreSQL, Redis/Celery, or Compose fixture E2E |

## Dependency Reproducibility

Frontend dependencies are reproducible through `frontend/package-lock.json`.

Backend dependencies are declared in `pyproject.toml` with version ranges, but no
backend lock file is currently recorded in this repository. That leaves CI and
production installs open to different transitive dependency resolutions over
time.

Recommendation:

- Adopt one backend lock mechanism for Python 3.12 and commit the lock file.
- Acceptable options include `uv.lock`, a pip-tools generated
  `requirements.lock`, or another single project-standard lock format.
- CI and Docker images should install backend dependencies from the committed
  lock in frozen mode, preferably with hashes when supported.
- Dependency updates should be deliberate PRs that regenerate the lock and rerun
  the full local and CI acceptance gates.

## Release Recommendation

Keep release `BLOCKED` until the evaluated commit has successful GitHub Actions
evidence for:

- `quality`
- `frontend-quality`
- `postgres-integration`
- `redis-celery-integration`
- `compose-acceptance`

All service-gated tests must report zero skips. After those runs are available,
record the run URLs and any relevant Compose service logs in this document.
