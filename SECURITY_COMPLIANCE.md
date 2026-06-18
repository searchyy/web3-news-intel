# Security and Compliance

## Secrets

- Use environment variables
- Never commit API keys
- Provide `.env.example` only
- Support secret rotation

## Network Policy

Allowed:
- public RSS
- public official APIs
- public HTML pages allowed by robots.txt
- authorized API keys
- fixed authorized egress

Disallowed:
- CAPTCHA solving
- Cloudflare/anti-bot bypass
- stealth fingerprinting
- unauthorized session cookies
- proxy rotation to evade limits
- private/paywalled/login-only content

## Data Retention

- Store raw RSS/API metadata
- Store raw HTML only for debugging and configured retention period
- Do not store full copyrighted articles unless license/API permits
- Store source URL and summary instead

## Audit

Every fetch_run must include:
- source
- URL
- status
- HTTP status
- retry count
- error code
- trace id
- timestamp
