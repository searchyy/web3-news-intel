from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AdminAuditLog, NotificationDestination, NotificationRule, ReportSchedule
from app.db.session import SessionLocal

RESOURCE_MODELS = {
    "destination": NotificationDestination,
    "report_schedule": ReportSchedule,
    "notification_rule": NotificationRule,
}

RESOURCE_ALIASES = {
    "destination": "destination",
    "report-schedule": "report_schedule",
    "report_schedule": "report_schedule",
    "notification-rule": "notification_rule",
    "notification_rule": "notification_rule",
}


def repair_display_name(
    session: Session,
    *,
    resource_type: str,
    resource_id: str,
    name: str,
    apply: bool = False,
) -> dict[str, Any]:
    normalized_type = _normalize_resource_type(resource_type)
    model = RESOURCE_MODELS[normalized_type]
    primary_key = _parse_resource_id(normalized_type, resource_id)
    item = session.get(model, primary_key)
    if item is None:
        raise ValueError(f"未找到记录：{normalized_type} {resource_id}")

    old_name = item.name
    result = {
        "resource_type": normalized_type,
        "resource_id": str(primary_key),
        "old_name": old_name,
        "new_name": name,
        "applied": apply,
    }
    if not apply:
        session.rollback()
        return result

    item.name = name
    session.add(
        AdminAuditLog(
            admin_subject="script:repair_display_name",
            action="repair_display_name",
            resource_type=normalized_type,
            resource_id=str(primary_key),
            metadata_={"field": "name", "old": old_name, "new": name},
            request_id=uuid.uuid4().hex,
        )
    )
    session.commit()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="定点修复管理后台显示名称乱码")
    parser.add_argument("--resource-type", required=True, choices=sorted(RESOURCE_ALIASES))
    parser.add_argument("--resource-id", required=True)
    parser.add_argument("--name", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只显示将要修改的值，不写数据库")
    mode.add_argument("--apply", action="store_true", help="写入指定记录并生成审计日志")
    args = parser.parse_args()

    with SessionLocal() as session:
        result = repair_display_name(
            session,
            resource_type=args.resource_type,
            resource_id=args.resource_id,
            name=args.name,
            apply=bool(args.apply),
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print("当前为 dry-run，未修改数据库；确认无误后加 --apply 执行。")
    return 0


def _normalize_resource_type(value: str) -> str:
    try:
        return RESOURCE_ALIASES[value]
    except KeyError as exc:
        raise ValueError(f"不支持的资源类型：{value}") from exc


def _parse_resource_id(resource_type: str, value: str) -> uuid.UUID | int:
    if resource_type == "report_schedule":
        return int(value)
    return uuid.UUID(value)


if __name__ == "__main__":
    raise SystemExit(main())
