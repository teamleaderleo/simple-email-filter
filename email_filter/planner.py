from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import MailMessage, Policy, RetentionPlanItem


def build_retention_plan(
    messages: Iterable[MailMessage],
    policies: Iterable[Policy],
    *,
    now: datetime | None = None,
    excluded_folder_ids: set[str] | None = None,
) -> list[RetentionPlanItem]:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    excluded = excluded_folder_ids or set()
    ordered_policies = [policy for policy in policies if policy.enabled]
    matched: dict[str, list[MailMessage]] = defaultdict(list)

    for message in messages:
        if message.parent_folder_id in excluded:
            continue
        for policy in ordered_policies:
            if policy.match.matches(message):
                matched[policy.id].append(message)
                break

    policy_by_id = {policy.id: policy for policy in ordered_policies}
    plan: list[RetentionPlanItem] = []

    for policy_id, policy_messages in matched.items():
        policy = policy_by_id[policy_id]
        retention = policy.retention
        newest_first = sorted(
            policy_messages,
            key=lambda message: message.received_at,
            reverse=True,
        )
        if retention.mode == "forever":
            continue

        keep_ids: set[str] = set()
        if retention.mode in {"latest", "days_and_latest"}:
            keep_ids = {
                message.id
                for message in newest_first[: retention.keep_latest or 0]
            }

        cutoff = None
        if retention.mode in {"days", "days_and_latest"}:
            cutoff = current_time - timedelta(days=retention.days or 0)

        for message in newest_first:
            if message.id in keep_ids:
                continue
            if cutoff is not None and message.received_at >= cutoff:
                continue
            if retention.mode == "latest":
                reason = f"outside latest {retention.keep_latest} messages"
            elif retention.mode == "days":
                reason = f"older than {retention.days} days"
            else:
                reason = (
                    f"older than {retention.days} days and outside latest "
                    f"{retention.keep_latest} messages"
                )
            plan.append(
                RetentionPlanItem(
                    message_id=message.id,
                    policy_id=policy.id,
                    received_at=message.received_at,
                    action=policy.on_expiry,
                    reason=reason,
                    sender=message.sender,
                    subject=message.subject,
                )
            )

    return sorted(plan, key=lambda item: (item.policy_id, item.received_at))
