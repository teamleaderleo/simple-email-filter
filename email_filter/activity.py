from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from .models import RetentionPlanItem


def write_run_activity(
    *,
    mode: str,
    scanned: int,
    plan: Iterable[RetentionPlanItem],
    moved: int,
    failed: int,
) -> None:
    """Write privacy-minimised aggregate activity when a table is configured."""
    table_name = os.environ.get("ACTIVITY_TABLE_NAME")
    if not table_name:
        return

    import boto3

    region = os.environ.get("AWS_REGION", "us-east-2")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    timestamp = datetime.now(timezone.utc).isoformat()
    plan_items = list(plan)

    counts: dict[str, int] = {}
    for item in plan_items:
        counts[item.policy_id] = counts.get(item.policy_id, 0) + 1

    table.put_item(
        Item={
            "id": str(uuid4()),
            "created_at": timestamp,
            "event_type": "retention_run",
            "mode": mode,
            "scanned": scanned,
            "selected": len(plan_items),
            "moved": moved,
            "failed": failed,
            "policy_counts": counts,
        }
    )
