from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import AdminAuditLog, NotificationDestination, ReportSchedule
from scripts.repair_display_name import repair_display_name


def test_repair_display_name_dry_run_does_not_update_destination(db_session) -> None:
    destination = NotificationDestination(
        key="feishu-display-test",
        name="?? Webhook ??",
        provider="feishu_webhook",
        enabled=True,
        status="active",
        config={},
    )
    db_session.add(destination)
    db_session.commit()

    result = repair_display_name(
        db_session,
        resource_type="destination",
        resource_id=str(destination.id),
        name="飞书 Webhook 主群",
    )

    db_session.refresh(destination)
    assert result["old_name"] == "?? Webhook ??"
    assert result["new_name"] == "飞书 Webhook 主群"
    assert result["applied"] is False
    assert destination.name == "?? Webhook ??"
    assert db_session.scalar(select(AdminAuditLog)) is None


def test_repair_display_name_apply_updates_schedule_and_writes_audit_log(db_session) -> None:
    destination = NotificationDestination(
        key="feishu-display-schedule",
        name="飞书测试群",
        provider="feishu_webhook",
        enabled=True,
        status="active",
        config={},
    )
    schedule = ReportSchedule(
        destination=destination,
        name="?? AI 15????????",
        enabled=True,
        report_type="digest_15m",
        timezone="Asia/Taipei",
        interval_minutes=15,
        source_groups=[],
        categories=[],
        severities=[],
        symbols=[],
        chains=[],
    )
    db_session.add_all([destination, schedule])
    db_session.commit()

    result = repair_display_name(
        db_session,
        resource_type="report-schedule",
        resource_id=str(schedule.id),
        name="AI 15 分钟快讯汇报",
        apply=True,
    )

    db_session.refresh(schedule)
    audit = db_session.scalar(
        select(AdminAuditLog).where(AdminAuditLog.resource_id == str(schedule.id))
    )
    assert result["applied"] is True
    assert schedule.name == "AI 15 分钟快讯汇报"
    assert audit is not None
    assert audit.admin_subject == "script:repair_display_name"
    assert audit.metadata_["old"] == "?? AI 15????????"
    assert audit.metadata_["new"] == "AI 15 分钟快讯汇报"


def test_repair_display_name_rejects_missing_record(db_session) -> None:
    missing_id = str(uuid.uuid4())
    try:
        repair_display_name(
            db_session,
            resource_type="notification_rule",
            resource_id=missing_id,
            name="新名称",
            apply=True,
        )
    except ValueError as exc:
        assert missing_id in str(exc)
    else:
        raise AssertionError("missing record should raise")
