from __future__ import annotations

from collections.abc import Iterable


STAGES: dict[str, tuple[str, ...] | None] = {
    "bulk": (
        "amazon-review-requests",
        "artstation-digests",
        "job-alerts",
        "linkedin-social-notifications",
        "marketing-promotions",
        "short-lived-digests",
        "uber-promotions",
    ),
    "newsletters": (
        "career-network-notifications",
        "entertainment-community-notifications",
        "financial-marketing",
        "political-campaign-updates",
        "technical-and-cultural-newsletters",
    ),
    "operations": (
        "building-announcements",
        "building-package-notices",
        "deployment-alerts",
        "shipment-tracking",
        "uber-order-notifications",
    ),
    "all": None,
}


def stage_names() -> tuple[str, ...]:
    return tuple(STAGES)


def resolve_stage(name: str | None) -> set[str] | None:
    if name is None:
        return None
    if name not in STAGES:
        available = ", ".join(stage_names())
        raise ValueError(f"Unknown apply stage {name!r}. Available stages: {available}")
    policy_ids = STAGES[name]
    return None if policy_ids is None else set(policy_ids)


def merge_policy_selection(
    *,
    stage: str | None = None,
    policy_ids: Iterable[str] | None = None,
) -> set[str] | None:
    explicit = {value.strip() for value in (policy_ids or ()) if value.strip()}
    if stage and explicit:
        raise ValueError("Choose either an apply stage or explicit policy ids, not both")
    if explicit:
        return explicit
    return resolve_stage(stage)
