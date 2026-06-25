# GitHub Handoff

Date: 2026-06-23

Current workspace status:

- Git repository: present
- Evaluated commit: `64685c1247ca33d90bfb72acc06cb608c233112f`
- Remote: `origin` -> `https://searchyy@github.com/searchyy/web3-news-intel.git`

Local acceptance gates have passed, but production release remains `BLOCKED`
until GitHub Actions and real-service Compose evidence are collected for the
same commit.

## Before Pushing

Run or confirm the local gates:

```bash
python scripts/pre_push_acceptance.py
python scripts/validate_sources.py sources.yaml --strict-contract --catalog-dir source_catalog
cd frontend && npm run typecheck
cd frontend && npm test
cd frontend && npm run build
docker compose config --quiet
```

Confirm ignored files are not staged:

```bash
git status --ignored --short
```

Do not commit `.env`, tokens, virtual environments, caches, logs, local
databases, or generated artifacts.

## Push Current Repository

Use the current repository history and remote. Do not reinitialize the
repository and do not force push.

```bash
git status --short
git rev-parse HEAD
git push origin HEAD
```

If work is being prepared on a feature branch, push that branch and open a pull
request into the repository's default branch.

## CI Evidence Collection

After pushing, collect evidence only for the pushed commit SHA:

```bash
git rev-parse HEAD
gh run list --limit 10
gh run watch <RUN_ID> --exit-status
gh run view <RUN_ID> --log-failed
```

Required jobs:

- `quality`
- `frontend-quality`
- `postgres-integration`
- `redis-celery-integration`
- `compose-acceptance`

Release remains `BLOCKED` until all required jobs pass on the current commit
with zero service-gated skips and the evidence is recorded in
`docs/PRODUCTION_ACCEPTANCE.md`.
