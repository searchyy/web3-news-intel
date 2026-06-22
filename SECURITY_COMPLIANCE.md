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

Scope:
- collect only public or explicitly approved source data
- follow source-owner terms and configured rate limits
- keep URL safety checks enabled for external calls

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
