# Project Structure

```text
web3-news-intel/
  README.md
  pyproject.toml
  docker-compose.yml
  .env.example
  .gitignore
  alembic.ini
  sources.example.yaml

  app/
    __init__.py
    main.py                         # FastAPI entrypoint

    core/
      config.py                      # Pydantic settings
      logging.py                     # structlog setup
      time.py                        # UTC helpers
      security.py                    # secret handling, admin auth
      errors.py                      # shared exceptions

    db/
      base.py
      session.py
      models.py
      repositories/
        source_repo.py
        raw_document_repo.py
        event_repo.py
        delivery_repo.py

    schemas/
      source.py
      raw_document.py
      normalized_item.py
      event.py
      alert.py
      api.py

    scheduler/
      beat.py                        # periodic source polling
      planner.py                     # due source detection

    workers/
      celery_app.py                  # or dramatiq broker
      tasks_fetch.py
      tasks_parse.py
      tasks_publish.py

    fetch/
      client.py                      # httpx wrapper
      rate_limit.py
      robots.py
      retry.py
      user_agent.py

    adapters/
      base.py
      rss.py
      json_api.py
      graphql.py
      html.py
      github.py
      defillama.py
      coingecko.py
      snapshot.py
      exchange_announcement.py

    parsers/
      base.py
      registry.py
      generic_rss.py
      generic_html_listing.py
      binance.py
      okx.py
      ethereum_blog.py
      chinese_media.py

    pipeline/
      normalize.py
      language.py
      entities.py
      category.py
      dedupe.py
      scoring.py
      severity.py
      alert_rules.py

    publishers/
      base.py
      telegram.py
      discord.py
      slack.py
      webhook.py
      email.py

    api/
      routes/
        health.py
        events.py
        sources.py
        admin.py
        metrics.py

    observability/
      metrics.py
      tracing.py

  migrations/
    versions/

  tests/
    unit/
      test_retry.py
      test_rate_limit.py
      test_dedupe.py
      test_scoring.py
      test_normalize.py
    integration/
      test_rss_adapter.py
      test_pipeline_e2e.py
      test_publisher_idempotency.py
    fixtures/
      rss_sec.xml
      rss_coindesk.xml
      html_binance_listing.html

  scripts/
    init_db.py
    backfill_source.py
    replay_raw_document.py
    validate_sources.py
```
