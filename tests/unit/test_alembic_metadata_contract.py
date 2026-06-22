from __future__ import annotations

from pathlib import Path

from app.db.models import Event, EventSource, SavedSearch


def _index_names(table) -> set[str]:  # noqa: ANN001
    return {index.name for index in table.indexes if index.name}


def test_event_search_indexes_are_declared_in_sqlalchemy_metadata() -> None:
    assert {
        "ix_events_first_seen_at",
        "ix_events_last_seen_at",
        "ix_events_trust_score",
        "ix_events_status_severity_first_seen",
        "ix_events_category_first_seen",
    }.issubset(_index_names(Event.__table__))
    assert "ix_event_sources_source_event" in _index_names(EventSource.__table__)
    assert "ix_saved_searches_updated_at" in _index_names(SavedSearch.__table__)


def test_postgres_handwritten_search_indexes_are_excluded_from_autogenerate() -> None:
    env_py = Path("migrations/env.py").read_text(encoding="utf-8")
    assert "include_object=include_object" in env_py
    assert {
        "ix_events_title_trgm",
        "ix_events_summary_trgm",
        "ix_events_symbols_gin",
        "ix_events_chains_gin",
        "ix_events_entities_gin",
    }.issubset(set(env_py.split('"')))
