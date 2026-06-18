# GitHub Handoff

Date: 2026-06-18

This workspace is not a Git repository and no `GITHUB_REPO_URL` was provided. The code is ready for repository handoff, but no push, pull request, or GitHub Actions evidence has been produced from this workspace.

## Before Pushing

Run:

```bash
python scripts/pre_push_acceptance.py
```

Confirm ignored files are not staged:

```bash
git status --ignored --short
```

Do not commit `.env`, tokens, virtual environments, caches, logs, local databases, or generated artifacts.

## New Empty GitHub Repository

Use this only for a new empty remote repository:

```bash
git init
git branch -M main
git add .
git status
git commit -m "feat: bootstrap web3 news intelligence service"
git remote add origin <GITHUB_REPO_URL>
git push -u origin main
```

Do not force push.

## Existing GitHub Repository With History

Use a clean clone so existing remote history is preserved:

```bash
git clone <GITHUB_REPO_URL> web3-news-intel-repo
cd web3-news-intel-repo
git switch -c ci/phase-12-production-acceptance
```

Copy the current workspace contents into the clone without copying or overwriting `.git`, then run:

```bash
python scripts/pre_push_acceptance.py
git add .
git status
git commit -m "ci: add real-service production acceptance"
git push -u origin ci/phase-12-production-acceptance
```

If `gh` is unavailable, open GitHub in a browser and create a pull request from `ci/phase-12-production-acceptance` into the repository's default branch.

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
- `postgres-integration`
- `redis-celery-integration`
- `compose-acceptance`

Release remains `BLOCKED` until all four jobs pass on the current commit with zero service-gated skips.
