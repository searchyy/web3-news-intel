from __future__ import annotations

import math
import re
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Text,
    and_,
    case,
    cast,
    column,
    false,
    func,
    inspect,
    or_,
    select,
    table,
)
from sqlalchemy.orm import Session, selectinload

from app.db.models import Event, EventSource, SavedSearch, Source
from app.schemas.event import EventRead
from app.schemas.event_search import (
    EventFacets,
    EventSearchItem,
    EventSearchPage,
    EventSearchParams,
    FacetBucket,
    SavedSearchCreate,
    SavedSearchPatch,
)

ai_insights_table = table(
    "event_ai_insights",
    column("event_id", BigInteger()),
    column("headline_zh", Text()),
    column("summary_zh", Text()),
    column("key_facts", Text()),
    column("entities", Text()),
    column("symbols", Text()),
    column("chains", Text()),
    column("event_type", Text()),
    column("risk_level", Text()),
)


class EventSearchRepository:
    def __init__(self, session: Session):
        self.session = session
        self.dialect = session.get_bind().dialect.name
        self._has_ai_table_cache: bool | None = None

    def search(self, params: EventSearchParams) -> EventSearchPage:
        base = self._filtered_event_ids(params)
        total = int(self.session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        pages = math.ceil(total / params.page_size) if total else 0
        id_stmt = (
            self._apply_order(base, params)
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
        event_ids = list(self.session.scalars(id_stmt))
        if not event_ids:
            return EventSearchPage(
                items=[],
                total=total,
                page=params.page,
                page_size=params.page_size,
                pages=pages,
                sort=params.sort,
                direction=params.direction,
            )

        events = list(
            self.session.scalars(
                select(Event)
                .options(
                    selectinload(Event.sources).selectinload(EventSource.source),
                    selectinload(Event.ai_insights),
                )
                .where(Event.id.in_(event_ids))
            )
        )
        order = {event_id: index for index, event_id in enumerate(event_ids)}
        events.sort(key=lambda event: order[event.id])
        return EventSearchPage(
            items=[_search_item(event) for event in events],
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=pages,
            sort=params.sort,
            direction=params.direction,
        )

    def facets(self, params: EventSearchParams) -> EventFacets:
        filtered = self._filtered_event_ids(params).subquery()
        return EventFacets(
            categories=self._event_column_buckets(filtered, Event.category),
            severities=self._event_column_buckets(filtered, Event.severity),
            statuses=self._event_column_buckets(filtered, Event.status),
            languages=self._event_column_buckets(filtered, Event.language),
            source_keys=self._source_buckets(filtered, Source.key),
            source_groups=self._source_buckets(filtered, self._source_group_expr()),
            symbols=self._array_buckets(filtered, Event.symbols),
            chains=self._array_buckets(filtered, Event.chains),
        )

    def _filtered_event_ids(self, params: EventSearchParams):
        stmt = select(Event.id).select_from(Event)
        source_joined = False
        ai_joined = False

        def join_source(*, outer: bool = False) -> None:
            nonlocal stmt, source_joined
            if source_joined:
                return
            if outer:
                stmt = stmt.outerjoin(EventSource, EventSource.event_id == Event.id).outerjoin(
                    Source, Source.id == EventSource.source_id
                )
            else:
                stmt = stmt.join(EventSource, EventSource.event_id == Event.id).join(
                    Source, Source.id == EventSource.source_id
                )
            source_joined = True

        def join_ai() -> None:
            nonlocal stmt, ai_joined
            if not ai_joined and self._has_ai_insights_table():
                stmt = stmt.outerjoin(ai_insights_table, ai_insights_table.c.event_id == Event.id)
                ai_joined = True

        if params.source_keys or params.source_groups or params.official_only is True:
            join_source()
        elif params.q:
            join_source(outer=True)
        if params.has_ai_summary is not None or params.q:
            join_ai()

        conditions = []
        if params.categories:
            conditions.append(Event.category.in_(params.categories))
        if params.severities:
            conditions.append(Event.severity.in_(params.severities))
        if params.statuses:
            conditions.append(Event.status.in_(params.statuses))
        if params.languages:
            conditions.append(Event.language.in_(params.languages))
        if params.symbols:
            conditions.append(self._array_overlap_condition(Event.symbols, params.symbols))
        if params.chains:
            conditions.append(self._array_overlap_condition(Event.chains, params.chains))
        if params.minimum_trust_score is not None:
            conditions.append(Event.trust_score >= params.minimum_trust_score)
        if params.published_from is not None:
            conditions.append(Event.published_at >= params.published_from)
        if params.published_to is not None:
            conditions.append(Event.published_at <= params.published_to)
        if params.first_seen_from is not None:
            conditions.append(Event.first_seen_at >= params.first_seen_from)
        if params.first_seen_to is not None:
            conditions.append(Event.first_seen_at <= params.first_seen_to)
        if params.source_keys:
            conditions.append(Source.key.in_(params.source_keys))
        if params.source_groups:
            conditions.append(
                or_(
                    self._source_group_expr().in_(params.source_groups),
                    Source.source_type.in_(params.source_groups),
                )
            )
        if params.official_only is True:
            conditions.append(self._official_source_condition())
        if params.has_ai_summary is True:
            conditions.append(ai_insights_table.c.event_id.is_not(None) if ai_joined else false())
        if params.has_ai_summary is False and ai_joined:
            conditions.append(ai_insights_table.c.event_id.is_(None))
        if params.q:
            conditions.append(self._keyword_condition(params))

        for condition in conditions:
            stmt = stmt.where(condition)
        return stmt.group_by(Event.id)

    def _keyword_condition(self, params: EventSearchParams):
        terms = [params.q] if params.q_mode == "phrase" else _split_terms(params.q or "")
        if not terms:
            return Event.id.is_not(None)
        per_term = [or_(*self._keyword_fields(term)) for term in terms]
        return or_(*per_term) if params.q_mode == "any" else and_(*per_term)

    def _keyword_fields(self, term: str):
        pattern = _like_pattern(term)
        lowered_pattern = pattern.lower()
        fields = [
            self._lower_text_condition(Event.title, lowered_pattern),
            self._lower_text_condition(Event.summary, lowered_pattern),
            self._array_text_condition(Event.symbols, pattern),
            self._array_text_condition(Event.chains, pattern),
            self._array_text_condition(Event.entities, pattern),
            self._lower_text_condition(Source.name, lowered_pattern),
            self._lower_text_condition(Source.key, lowered_pattern),
        ]
        if self._has_ai_insights_table():
            fields.extend(
                [
                    self._lower_text_condition(
                        ai_insights_table.c.headline_zh, lowered_pattern
                    ),
                    self._lower_text_condition(
                        ai_insights_table.c.summary_zh, lowered_pattern
                    ),
                    self._lower_text_condition(
                        cast(ai_insights_table.c.key_facts, Text), lowered_pattern
                    ),
                    self._lower_text_condition(
                        cast(ai_insights_table.c.entities, Text), lowered_pattern
                    ),
                ]
            )
        return fields

    def _apply_order(self, stmt, params: EventSearchParams):
        sort_map = {
            "published_at": Event.published_at,
            "first_seen_at": Event.first_seen_at,
            "last_seen_at": Event.last_seen_at,
            "trust_score": Event.trust_score,
            "confirmation_count": Event.confirmation_count,
            "id": Event.id,
        }
        if params.sort == "severity":
            order_expr = case(
                {"low": 1, "normal": 2, "medium": 3, "high": 4, "critical": 5},
                value=Event.severity,
                else_=0,
            )
        else:
            order_expr = sort_map[params.sort]
        order_expr = order_expr.asc() if params.direction == "asc" else order_expr.desc()
        if params.sort in {"published_at", "first_seen_at", "last_seen_at"}:
            order_expr = order_expr.nullslast()
        tie_breaker = Event.id.asc() if params.direction == "asc" else Event.id.desc()
        return stmt.order_by(order_expr, tie_breaker)

    def _array_overlap_condition(self, column_value, values: list[str]):
        if self.dialect == "postgresql":
            return column_value.overlap(values)
        lowered = [value.lower() for value in values]
        haystack = func.lower(cast(column_value, Text))
        return or_(*[haystack.like(_like_pattern(f'"{value}"'), escape="\\") for value in lowered])

    def _array_text_condition(self, column_value, pattern: str):
        return cast(column_value, Text).ilike(pattern, escape="\\")

    def _lower_text_condition(self, column_value, lowered_pattern: str):
        return func.lower(func.coalesce(column_value, "")).like(lowered_pattern, escape="\\")

    def _source_group_expr(self):
        configured = Source.config["source_group"].as_string()
        return case(
            (Source.source_group.in_(["", "legacy"]), configured),
            else_=Source.source_group,
        )

    def _official_source_condition(self):
        return or_(
            Source.official.is_(True),
            Source.config["official"].as_boolean().is_(True),
            Source.source_type.in_(["exchange_official", "regulator_official"]),
        )

    def _event_column_buckets(self, filtered, column_value) -> list[FacetBucket]:
        rows = self.session.execute(
            select(column_value, func.count(Event.id))
            .join(filtered, filtered.c.id == Event.id)
            .where(column_value.is_not(None))
            .group_by(column_value)
            .order_by(func.count(Event.id).desc(), column_value.asc())
            .limit(100)
        )
        return [FacetBucket(key=str(key), count=int(count)) for key, count in rows if key]

    def _source_buckets(self, filtered, column_value) -> list[FacetBucket]:
        rows = self.session.execute(
            select(column_value, func.count(func.distinct(Event.id)))
            .select_from(Event)
            .join(filtered, filtered.c.id == Event.id)
            .join(EventSource, EventSource.event_id == Event.id)
            .join(Source, Source.id == EventSource.source_id)
            .where(column_value.is_not(None))
            .group_by(column_value)
            .order_by(func.count(func.distinct(Event.id)).desc(), column_value.asc())
            .limit(100)
        )
        return [FacetBucket(key=str(key), count=int(count)) for key, count in rows if key]

    def _array_buckets(self, filtered, column_value) -> list[FacetBucket]:
        rows = self.session.execute(
            select(column_value).select_from(Event).join(filtered, filtered.c.id == Event.id)
        )
        counter: Counter[str] = Counter()
        for (values,) in rows:
            if isinstance(values, list):
                counter.update(str(value) for value in values if value)
        return [
            FacetBucket(key=key, count=count)
            for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:100]
        ]

    def _has_ai_insights_table(self) -> bool:
        if self._has_ai_table_cache is None:
            self._has_ai_table_cache = inspect(self.session.get_bind()).has_table(
                "event_ai_insights"
            )
        return self._has_ai_table_cache


class SavedSearchRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self, owner_subject: str) -> list[SavedSearch]:
        return list(
            self.session.scalars(
                select(SavedSearch)
                .where(SavedSearch.owner_subject == owner_subject)
                .order_by(SavedSearch.updated_at.desc(), SavedSearch.id.desc())
            )
        )

    def create(self, owner_subject: str, payload: SavedSearchCreate) -> SavedSearch:
        saved = SavedSearch(
            owner_subject=owner_subject,
            name=payload.name,
            description=payload.description,
            filters=payload.filters.model_dump(mode="json", exclude_none=True),
        )
        self.session.add(saved)
        self.session.flush()
        return saved

    def get_for_owner(self, saved_search_id: int, owner_subject: str) -> SavedSearch | None:
        return self.session.scalar(
            select(SavedSearch).where(
                SavedSearch.id == saved_search_id,
                SavedSearch.owner_subject == owner_subject,
            )
        )

    def update(self, saved: SavedSearch, payload: SavedSearchPatch) -> SavedSearch:
        update = payload.model_dump(exclude_unset=True)
        if "name" in update:
            saved.name = update["name"]
        if "description" in update:
            saved.description = update["description"]
        if payload.filters is not None:
            saved.filters = payload.filters.model_dump(mode="json", exclude_none=True)
        saved.updated_at = datetime.now(UTC)
        self.session.flush()
        return saved

    def delete(self, saved: SavedSearch) -> None:
        self.session.delete(saved)
        self.session.flush()


def _split_terms(q: str) -> list[str]:
    matches = re.findall(r'"([^"]+)"|(\S+)', q.strip())
    terms = [(quoted or plain).strip() for quoted, plain in matches]
    return [term for term in terms if term]


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_item(event: Event) -> EventSearchItem:
    payload = EventRead.model_validate(event).model_dump()
    primary_source = None
    for event_source in sorted(event.sources, key=lambda item: item.id or 0):
        if event_source.source is not None:
            primary_source = event_source.source
            break
    if primary_source is not None:
        payload.update(
            {
                "source_key": primary_source.key,
                "source_name": primary_source.name,
                "source_group": primary_source.source_group,
                "official": primary_source.official,
            }
        )
    insight = _latest_successful_ai_insight(event)
    if insight is not None:
        payload.update(
            {
                "ai_summary_status": insight.status,
                "ai_headline_zh": insight.headline_zh,
                "ai_summary_zh": insight.summary_zh,
                "ai_importance_score": insight.importance_score,
                "ai_risk_level": insight.risk_level,
                "ai_tags": _ai_tags(insight),
                "has_ai_summary": bool(insight.summary_zh or insight.headline_zh),
            }
        )
    return EventSearchItem.model_validate(payload)


def _latest_successful_ai_insight(event: Event):
    insights = [
        insight for insight in event.ai_insights if getattr(insight, "status", None) == "success"
    ]
    if not insights:
        return None
    return sorted(
        insights,
        key=lambda item: (item.generated_at or datetime.min.replace(tzinfo=UTC), item.id or 0),
        reverse=True,
    )[0]


def _ai_tags(insight) -> list[str]:
    tags: list[str] = []
    if insight.event_type:
        tags.append(str(insight.event_type))
    tags.extend(str(symbol) for symbol in (insight.symbols or [])[:5])
    tags.extend(str(chain) for chain in (insight.chains or [])[:3])
    return tags[:10]
