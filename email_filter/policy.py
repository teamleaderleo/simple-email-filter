from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import MatchRule, Policy, RetentionRule

_VALID_MODES = {"forever", "days", "latest", "days_and_latest"}
_VALID_GROUPS = {"policy", "sender"}


def _normalise_strings(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError("Policy match fields must be arrays of strings")
    return tuple(v.strip().lower() for v in values if v.strip())


def _parse_policy(raw: dict[str, Any]) -> Policy:
    policy_id = str(raw.get("id") or "").strip()
    if not policy_id:
        raise ValueError("Every policy requires a non-empty id")

    retention_raw = raw.get("retention") or {}
    mode = retention_raw.get("mode")
    if mode not in _VALID_MODES:
        raise ValueError(f"Policy {policy_id!r} has invalid retention mode {mode!r}")

    days = retention_raw.get("days")
    keep_latest = retention_raw.get("keepLatest")
    group_by = retention_raw.get("groupBy", "policy")

    if group_by not in _VALID_GROUPS:
        raise ValueError(
            f"Policy {policy_id!r} has invalid retention groupBy {group_by!r}"
        )

    if mode in {"days", "days_and_latest"}:
        if not isinstance(days, int) or days <= 0:
            raise ValueError(f"Policy {policy_id!r} requires a positive days value")
    elif days is not None:
        raise ValueError(f"Policy {policy_id!r} cannot set days for mode {mode!r}")

    if mode in {"latest", "days_and_latest"}:
        if not isinstance(keep_latest, int) or keep_latest < 0:
            raise ValueError(
                f"Policy {policy_id!r} requires a non-negative keepLatest value"
            )
    elif keep_latest is not None:
        raise ValueError(
            f"Policy {policy_id!r} cannot set keepLatest for mode {mode!r}"
        )

    if group_by != "policy" and mode not in {"latest", "days_and_latest"}:
        raise ValueError(
            f"Policy {policy_id!r} can set groupBy only for rolling latest modes"
        )

    match_raw = raw.get("match") or {}
    match = MatchRule(
        senders=_normalise_strings(match_raw.get("senders")),
        sender_domains=_normalise_strings(match_raw.get("senderDomains")),
        subject_contains=_normalise_strings(match_raw.get("subjectContains")),
        subject_excludes=_normalise_strings(match_raw.get("subjectExcludes")),
    )

    if not any((match.senders, match.sender_domains, match.subject_contains)):
        raise ValueError(
            f"Policy {policy_id!r} must constrain sender, domain, or subject"
        )

    on_expiry = raw.get("onExpiry", "deleteditems")
    if on_expiry != "deleteditems":
        raise ValueError(
            f"Policy {policy_id!r} uses unsupported expiry action {on_expiry!r}"
        )

    return Policy(
        id=policy_id,
        description=str(raw.get("description") or ""),
        enabled=bool(raw.get("enabled", True)),
        priority=int(raw.get("priority", 100)),
        match=match,
        retention=RetentionRule(
            mode=mode,
            days=days,
            keep_latest=keep_latest,
            group_by=group_by,
        ),
        on_expiry=on_expiry,
    )


def load_policies(path: str | Path) -> list[Policy]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_policies = payload.get("policies") if isinstance(payload, dict) else None
    if not isinstance(raw_policies, list):
        raise ValueError("Policy file must contain a top-level policies array")

    policies = [_parse_policy(item) for item in raw_policies]
    ids = [policy.id for policy in policies]
    if len(ids) != len(set(ids)):
        raise ValueError("Policy ids must be unique")

    return sorted(policies, key=lambda policy: (policy.priority, policy.id))
