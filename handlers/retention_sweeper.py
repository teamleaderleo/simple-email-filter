from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from email_filter.activity import write_run_activity
from email_filter.auth import acquire_access_token
from email_filter.graph import GraphClient
from email_filter.planner import build_retention_plan
from email_filter.policy import load_policies

_APPLY_CONFIRMATION = "MOVE_TO_DELETED_ITEMS"


def _policy_path() -> Path:
    configured = os.environ.get(
        "RETENTION_POLICY_PATH",
        "policies/personal.example.json",
    )
    return Path(configured)


def lambda_handler(event, context):
    mode = os.environ.get("RETENTION_MODE", "audit").lower()
    if mode not in {"audit", "apply"}:
        raise ValueError("RETENTION_MODE must be audit or apply")

    policy_path = _policy_path()
    if (
        mode == "apply"
        and os.environ.get("RETENTION_APPLY_CONFIRMATION") != _APPLY_CONFIRMATION
    ):
        raise RuntimeError(
            "Apply mode requires RETENTION_APPLY_CONFIRMATION=MOVE_TO_DELETED_ITEMS"
        )
    if mode == "apply" and policy_path.name.endswith(".example.json"):
        raise RuntimeError(
            "Apply mode refuses example policy files. Provide RETENTION_POLICY_PATH "
            "for a deployment-specific policy."
        )

    lookback_days = int(os.environ.get("RETENTION_LOOKBACK_DAYS", "730"))
    max_messages = int(os.environ.get("RETENTION_MAX_MESSAGES", "50000"))
    page_size = int(os.environ.get("RETENTION_PAGE_SIZE", "500"))

    policies = load_policies(policy_path)
    client = GraphClient(acquire_access_token())
    deleted_items_id = client.get_well_known_folder_id("deleteditems")
    received_after = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    messages = list(
        client.iter_messages(
            received_after=received_after,
            page_size=page_size,
            max_messages=max_messages,
        )
    )
    plan = build_retention_plan(
        messages,
        policies,
        excluded_folder_ids={deleted_items_id},
    )

    result = {"moved": 0, "failed": 0}
    if mode == "apply" and plan:
        result = client.move_messages(
            [item.message_id for item in plan],
            destination_folder_id=deleted_items_id,
        )

    policy_counts = Counter(item.policy_id for item in plan)
    summary = {
        "mode": mode,
        "scanned": len(messages),
        "selected": len(plan),
        "moved": result["moved"],
        "failed": result["failed"],
        "policies": dict(sorted(policy_counts.items())),
    }
    print(json.dumps(summary, sort_keys=True))

    write_run_activity(
        mode=mode,
        scanned=len(messages),
        plan=plan,
        moved=result["moved"],
        failed=result["failed"],
    )

    return {"statusCode": 200, "body": json.dumps(summary)}
