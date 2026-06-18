# Test Plan

## Unit Tests

- retry policy
- Retry-After parsing
- rate limiter
- robots behavior
- URL canonicalization
- title normalization
- symbol extraction
- dedupe key generation
- trust scoring
- alert decision
- publisher idempotency

## Integration Tests

- RSS fixture -> raw document -> normalized item -> event
- HTML fixture -> normalized item -> event
- GraphQL fixture -> governance event
- duplicate RSS entries -> single event
- publisher failure -> retry
- source disabled -> no jobs

## E2E Test

Use local fixture server:

1. serve sample RSS feed
2. seed source
3. run scheduler once
4. run worker
5. assert:
   - fetch_run success
   - raw_document exists
   - event exists
   - event_source exists
   - no duplicate on second run
   - alert decision created for high-severity category
