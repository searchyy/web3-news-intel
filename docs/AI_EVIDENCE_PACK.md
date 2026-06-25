# AI EvidencePack

EvidencePack is the bounded evidence envelope used before building the existing
`AIEventInput` payload. It is constructed locally from stored event data only; it
does not refetch URLs or call DeepSeek, Feishu, or external sources.

## Existing Pipeline Audit

- Fetch workers store fetched responses in `raw_documents` with `url`,
  `canonical_url`, `content_type`, `body`, and parser metadata.
- Parser adapters convert `RawDocumentPayload` into normalized items and keep
  safe item text in metadata fields such as `summary`, `description`, `snippet`,
  or `content_excerpt` when available.
- Dedupe links normalized items to events through `event_sources`, including
  `event_sources.url` and optional `event_sources.raw_document_id`.
- EvidencePack reads URLs only from `event_sources.url`. `events.primary_url`
  and `raw_documents.url` are not evidence source URLs.

## Evidence Constraints

- Maximum sources: 3 unique `event_sources.url` values in event source order.
- Maximum excerpts: 3, aligned to the selected event sources.
- Maximum excerpt size: 2000 characters.
- Maximum EvidencePack serialized input: 8000 characters.
- HTML is converted to text and `script`, `style`, `nav`, `header`, `footer`,
  `aside`, forms, iframes, SVG, and similar non-content nodes are removed.
- Raw document bodies are used only when `raw_document.metadata.ai_excerpt_allowed`
  is exactly `true`. Safe metadata excerpts are preferred.
- Secret-like metadata keys and secret-like excerpt values are redacted.
- `input_quality` is one of `title_only`, `summary`, `excerpt`, or `multi_source`.

## Known Gaps

- Full article bodies are usually not available per event unless a parser stores
  a safe excerpt in raw document metadata or explicitly marks the raw body as
  allowed for AI excerpting.
- EvidencePack does not resolve article URLs or fetch canonical pages after
  parsing. This avoids external calls but limits evidence depth for title-only
  feeds.
- The source URL allowlist is intentionally event-level. Feed URLs and raw
  document URLs are excluded even when they differ from the event URL.
