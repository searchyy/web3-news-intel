# Summary

- Add Feishu enterprise application bot integration
- Add outbound-only Feishu custom webhook support
- Add Feishu event and card-action callbacks
- Add notification destinations and routing rules
- Add delivery idempotency, quiet hours, anti-flood controls and digest support
- Add secure administrator session authentication and CSRF protection
- Add React/Vite visual administration panel
- Add frontend Docker/Nginx service
- Add manual Feishu test-send workflow
- Add Feishu and administration documentation

## Security controls

- Real Feishu sends default to disabled
- Secrets remain environment-only
- Custom webhook URLs are encrypted at rest
- Full webhook URLs are never returned by APIs
- New groups begin in pending state
- Administrators must explicitly approve and enable groups
- Historical events are blocked by destination activation time
- No real Feishu message is sent during deterministic CI
- Administrator sessions use HttpOnly cookies
- Mutating administrator requests use CSRF protection

## Local verification

- Backend unit tests: 120 passed
- Deterministic integration tests: 12 passed
- Frontend lint: passed
- Frontend typecheck: passed
- Frontend tests: passed
- Frontend build: passed
- Ruff: passed
- Mypy: passed
- Source validation: loaded=7 enabled=5

## Required CI jobs

- quality
- frontend-quality
- postgres-integration
- redis-celery-integration
- compose-acceptance

## Real Feishu send

Not executed. A real send must be run manually against FEISHU_TEST_CHAT_ID after merge and deployment approval.
