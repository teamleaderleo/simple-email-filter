from __future__ import annotations

from collections import Counter
from typing import Any

from .graph import GraphClient
from .historical import APPLY_CONFIRMATION, HistoricalMailboxStore


def _selected_plan(
    store: HistoricalMailboxStore,
    policy_ids: set[str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    plan = store.load_plan()
    available = sorted({str(item.get("policyId") or "") for item in plan if item.get("policyId")})
    if policy_ids is None:
        return plan, available

    unknown = sorted(policy_ids.difference(available))
    if unknown:
        raise RuntimeError(
            "Unknown policy ids: "
            + ", ".join(unknown)
            + ". Available planned policies: "
            + ", ".join(available)
        )
    selected = [item for item in plan if str(item.get("policyId")) in policy_ids]
    return selected, sorted(policy_ids)


def plan_status(
    store: HistoricalMailboxStore,
    *,
    policy_ids: set[str] | None = None,
) -> dict[str, Any]:
    selected, selected_policy_ids = _selected_plan(store, policy_ids)
    all_plan = store.load_plan()
    outcomes = store.completed_apply_outcomes()

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        by_policy: dict[str, Counter[str]] = {}
        totals: Counter[str] = Counter()
        for item in items:
            policy_id = str(item.get("policyId") or "")
            message_id = str(item.get("messageId") or "")
            outcome = outcomes.get(message_id)
            status = "pending"
            if outcome in {"moved", "missing"}:
                status = outcome
            elif outcome == "failed":
                status = "failedLastAttempt"

            counts = by_policy.setdefault(policy_id, Counter())
            counts["total"] += 1
            counts[status] += 1
            totals["total"] += 1
            totals[status] += 1

        return {
            "total": totals["total"],
            "pending": totals["pending"] + totals["failedLastAttempt"],
            "moved": totals["moved"],
            "missing": totals["missing"],
            "failedLastAttempt": totals["failedLastAttempt"],
            "byPolicy": {
                policy_id: {
                    "total": counts["total"],
                    "pending": counts["pending"] + counts["failedLastAttempt"],
                    "moved": counts["moved"],
                    "missing": counts["missing"],
                    "failedLastAttempt": counts["failedLastAttempt"],
                }
                for policy_id, counts in sorted(by_policy.items())
            },
        }

    return {
        "selectedPolicies": selected_policy_ids,
        "selection": summarize(selected),
        "allPlan": summarize(all_plan),
    }


def apply_plan_selection(
    client: GraphClient,
    store: HistoricalMailboxStore,
    *,
    confirmation: str,
    limit: int = 500,
    policy_ids: set[str] | None = None,
) -> dict[str, Any]:
    if confirmation != APPLY_CONFIRMATION:
        raise RuntimeError(f"Apply requires confirmation {APPLY_CONFIRMATION}")

    summary = store.summary()
    if not summary.get("scanComplete"):
        raise RuntimeError(
            "Apply refuses an incomplete scan because rolling retention rules need "
            "the complete folder history."
        )
    if str(summary.get("policyPath", "")).endswith(".example.json"):
        raise RuntimeError(
            "Apply refuses example policy files. Run make mailbox-prepare-apply first."
        )

    selected, _ = _selected_plan(store, policy_ids)
    previous = store.completed_apply_outcomes()
    finished = {
        message_id
        for message_id, outcome in previous.items()
        if outcome in {"moved", "missing"}
    }
    pending_items = [
        item
        for item in selected
        if str(item.get("messageId") or "") not in finished
    ]
    pending = [str(item["messageId"]) for item in pending_items[:limit]]

    if not pending:
        status = plan_status(store, policy_ids=policy_ids)
        return {
            "requested": 0,
            "moved": 0,
            "missing": 0,
            "failed": 0,
            "remaining": status["selection"]["pending"],
            "remainingAll": status["allPlan"]["pending"],
            "byPolicy": status["selection"]["byPolicy"],
        }

    deleted_items_id = client.get_well_known_folder_id("deleteditems")
    outcomes = client.move_messages_detailed(
        pending,
        destination_folder_id=deleted_items_id,
    )
    store.append_apply_outcomes(outcomes)
    status = plan_status(store, policy_ids=policy_ids)

    return {
        "requested": len(pending),
        "moved": sum(outcome == "moved" for outcome in outcomes.values()),
        "missing": sum(outcome == "missing" for outcome in outcomes.values()),
        "failed": sum(outcome == "failed" for outcome in outcomes.values()),
        "remaining": status["selection"]["pending"],
        "remainingAll": status["allPlan"]["pending"],
        "byPolicy": status["selection"]["byPolicy"],
    }
