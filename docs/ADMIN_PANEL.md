# Administration Panel

The visual administration panel is served by the `frontend` service. It uses server-side sessions with an HttpOnly cookie and CSRF protection. Browser localStorage is not used for admin tokens.

## Login

Configure:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD_HASH` using Argon2
- `ADMIN_SESSION_SECRET`

In production, HTTPS deployments must keep `ADMIN_SECURE_COOKIE=true`.

## Dashboard

The dashboard shows event volume, critical/high event count, source health, delivery health, and pending Feishu groups.

## Sources

Administrators can enable or disable sources, run one source manually, and inspect recent fetch runs.

## Feishu Groups

Groups added through the Feishu enterprise bot appear as pending destinations. An administrator must approve and enable a group before it can receive alerts.

Webhook input is write-only. After submission, only a masked fingerprint is displayed.

## Alert Rules

Rules support severity, category, source, symbol, chain, delivery mode, digest interval, quiet hours, timezone, hourly limits, and an explicit critical bypass toggle. The bypass toggle defaults to false.

## Deliveries

Deliveries show state, attempts, response status class, provider message ID, sanitized failures, and retry actions.

## Canary Monitoring

Live-source canary results are informational and separate from deterministic release gates. Pull requests should rely on fixture-backed tests.

## Audit Logs

Mutating administrator actions write audit records with request IDs and sanitized metadata. Passwords, tokens, webhook URLs, cookies, authorization headers, and full message bodies must not be stored in audit logs.

## Troubleshooting

- If login fails, check `ADMIN_PASSWORD_HASH` and the session cookie security setting.
- If a Feishu group is pending, approve and enable it before creating routing rules.
- If a delivery is dry-run, verify `FEISHU_ENABLED=true` and `FEISHU_SEND_ENABLED=true`.
- If custom webhook creation fails, confirm `FIELD_ENCRYPTION_KEY` is valid and the URL is a Feishu/Lark public webhook URL.
- Initial synchronization and historical backfills must never send automatically to newly enabled groups.
