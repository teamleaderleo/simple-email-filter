from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .graph import GraphClient
from .models import MailMessage, Policy, RetentionPlanItem
from .planner import build_retention_plan

_STATE_VERSION = 1
_APPLY_CONFIRMATION = "MOVE_TO_DELETED_ITEMS"
_RESET_CONFIRMATION = "RESET_LOCAL_STATE"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _private_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _private_file(temporary)
    temporary.replace(path)
    _private_file(path)


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    _private_dir(path.parent)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    _private_file(path)
    return count


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object in {path}")
    return payload


def _message_record(message: MailMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "sender": message.sender,
        "subject": message.subject,
        "receivedAt": _iso(message.received_at),
        "parentFolderId": message.parent_folder_id,
        "isRead": message.is_read,
        "categories": list(message.categories),
    }


def _message_from_record(record: dict[str, Any]) -> MailMessage:
    return MailMessage.from_graph(
        {
            "id": record["id"],
            "subject": record.get("subject", ""),
            "from": {
                "emailAddress": {
                    "address": record.get("sender", ""),
                }
            },
            "receivedDateTime": record["receivedAt"],
            "parentFolderId": record.get("parentFolderId"),
            "isRead": bool(record.get("isRead", False)),
            "categories": record.get("categories") or [],
        }
    )


def _plan_record(item: RetentionPlanItem) -> dict[str, Any]:
    return {
        "messageId": item.message_id,
        "policyId": item.policy_id,
        "receivedAt": _iso(item.received_at),
        "action": item.action,
        "reason": item.reason,
        "sender": item.sender,
        "subject": item.subject,
    }


def _domain(sender: str) -> str:
    return sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""


def _first_policy(message: MailMessage, policies: list[Policy]) -> Policy | None:
    for policy in sorted(
        (policy for policy in policies if policy.enabled),
        key=lambda policy: (policy.priority, policy.id),
    ):
        if policy.match.matches(message):
            return policy
    return None


def _top(counter: Counter[str], limit: int = 25) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in counter.most_common(limit)
        if value
    ]


class HistoricalMailboxStore:
    def __init__(self, state_dir: str | Path):
        self.root = Path(state_dir)
        self.snapshot_path = self.root / "messages.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.plan_path = self.root / "plan.jsonl"
        self.summary_path = self.root / "summary.json"
        self.apply_results_path = self.root / "apply-results.jsonl"
        _private_dir(self.root)

    def checkpoint(self) -> dict[str, Any] | None:
        if not self.checkpoint_path.exists():
            return None
        return _read_json(self.checkpoint_path)

    def reset(self, confirmation: str) -> None:
        if confirmation != _RESET_CONFIRMATION:
            raise RuntimeError(
                f"Reset requires confirmation {_RESET_CONFIRMATION}"
            )
        if self.root.exists():
            shutil.rmtree(self.root)

    def begin_scan(self, folder: str, restart: bool = False) -> dict[str, Any]:
        existing = self.checkpoint()
        if restart:
            if self.apply_results_path.exists():
                raise RuntimeError(
                    "This state contains apply results. Use the explicit reset command "
                    "before starting a different scan."
                )
            for path in (
                self.snapshot_path,
                self.checkpoint_path,
                self.plan_path,
                self.summary_path,
            ):
                path.unlink(missing_ok=True)
            existing = None

        if existing:
            if existing.get("folder") != folder:
                raise RuntimeError(
                    "The existing checkpoint belongs to a different folder. "
                    "Reset the local state or use the original folder."
                )
            return existing

        checkpoint = {
            "version": _STATE_VERSION,
            "folder": folder,
            "startedAt": _iso(_utc_now()),
            "updatedAt": _iso(_utc_now()),
            "scanned": 0,
            "pages": 0,
            "nextLink": None,
            "complete": False,
        }
        self.snapshot_path.unlink(missing_ok=True)
        _write_json(self.checkpoint_path, checkpoint)
        return checkpoint

    def append_page(
        self,
        messages: list[MailMessage],
        *,
        next_link: str | None,
    ) -> dict[str, Any]:
        checkpoint = self.checkpoint()
        if checkpoint is None:
            raise RuntimeError("Scan checkpoint is missing")
        _append_jsonl(self.snapshot_path, (_message_record(message) for message in messages))
        checkpoint.update(
            {
                "updatedAt": _iso(_utc_now()),
                "scanned": int(checkpoint.get("scanned", 0)) + len(messages),
                "pages": int(checkpoint.get("pages", 0)) + 1,
                "nextLink": next_link,
                "complete": next_link is None,
            }
        )
        _write_json(self.checkpoint_path, checkpoint)
        return checkpoint

    def load_messages(self) -> list[MailMessage]:
        if not self.snapshot_path.exists():
            return []
        by_id: dict[str, MailMessage] = {}
        with self.snapshot_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                message = _message_from_record(json.loads(line))
                by_id[message.id] = message
        return list(by_id.values())

    def write_plan(self, plan: list[RetentionPlanItem]) -> None:
        self.plan_path.unlink(missing_ok=True)
        _append_jsonl(self.plan_path, (_plan_record(item) for item in plan))

    def load_plan(self) -> list[dict[str, Any]]:
        if not self.plan_path.exists():
            raise RuntimeError("No cleanup plan exists. Run mailbox audit first.")
        rows: list[dict[str, Any]] = []
        with self.plan_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def write_summary(self, summary: dict[str, Any]) -> None:
        _write_json(self.summary_path, summary)

    def summary(self) -> dict[str, Any]:
        if not self.summary_path.exists():
            raise RuntimeError("No summary exists. Run mailbox audit first.")
        return _read_json(self.summary_path)

    def completed_apply_outcomes(self) -> dict[str, str]:
        outcomes: dict[str, str] = {}
        if not self.apply_results_path.exists():
            return outcomes
        with self.apply_results_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                message_id = str(row.get("messageId") or "")
                outcome = str(row.get("outcome") or "")
                if message_id and outcome:
                    outcomes[message_id] = outcome
        return outcomes

    def append_apply_outcomes(self, outcomes: dict[str, str]) -> None:
        now = _iso(_utc_now())
        _append_jsonl(
            self.apply_results_path,
            (
                {
                    "messageId": message_id,
                    "outcome": outcome,
                    "recordedAt": now,
                }
                for message_id, outcome in outcomes.items()
            ),
        )


def scan_folder(
    client: GraphClient,
    store: HistoricalMailboxStore,
    *,
    folder: str = "inbox",
    page_size: int = 999,
    max_pages: int | None = None,
    restart: bool = False,
) -> dict[str, Any]:
    checkpoint = store.begin_scan(folder, restart=restart)
    if checkpoint.get("complete"):
        return checkpoint

    start_url = checkpoint.get("nextLink")
    for messages, next_link in client.iter_folder_message_pages(
        folder=folder,
        start_url=str(start_url) if start_url else None,
        page_size=page_size,
        max_pages=max_pages,
    ):
        checkpoint = store.append_page(messages, next_link=next_link)

    return checkpoint


def build_audit(
    store: HistoricalMailboxStore,
    policies: list[Policy],
    *,
    policy_path: str,
    top_limit: int = 25,
) -> dict[str, Any]:
    messages = store.load_messages()
    checkpoint = store.checkpoint() or {}
    plan = build_retention_plan(messages, policies)
    store.write_plan(plan)

    selected_ids = {item.message_id for item in plan}
    policy_counts: Counter[str] = Counter()
    selected_policy_counts: Counter[str] = Counter(item.policy_id for item in plan)
    senders: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    unmatched_senders: Counter[str] = Counter()
    unmatched_domains: Counter[str] = Counter()
    years: Counter[str] = Counter()
    matched = 0
    protected_forever = 0

    policy_by_id = {policy.id: policy for policy in policies}
    for message in messages:
        senders[message.sender] += 1
        domains[_domain(message.sender)] += 1
        years[str(message.received_at.year)] += 1
        policy = _first_policy(message, policies)
        if policy is None:
            unmatched_senders[message.sender] += 1
            unmatched_domains[_domain(message.sender)] += 1
            continue
        matched += 1
        policy_counts[policy.id] += 1
        if policy.retention.mode == "forever":
            protected_forever += 1

    selected = len(selected_ids)
    kept_by_retention = matched - protected_forever - selected
    summary = {
        "version": _STATE_VERSION,
        "generatedAt": _iso(_utc_now()),
        "folder": checkpoint.get("folder", "inbox"),
        "policyPath": policy_path,
        "scanComplete": bool(checkpoint.get("complete")),
        "pages": int(checkpoint.get("pages", 0)),
        "scanned": len(messages),
        "read": sum(message.is_read for message in messages),
        "unread": sum(not message.is_read for message in messages),
        "matched": matched,
        "unmatched": len(messages) - matched,
        "protectedForever": protected_forever,
        "keptByRetention": max(0, kept_by_retention),
        "selected": selected,
        "byYear": dict(sorted(years.items())),
        "byPolicy": dict(sorted(policy_counts.items())),
        "selectedByPolicy": dict(sorted(selected_policy_counts.items())),
        "topSenders": _top(senders, top_limit),
        "topDomains": _top(domains, top_limit),
        "topUnmatchedSenders": _top(unmatched_senders, top_limit),
        "topUnmatchedDomains": _top(unmatched_domains, top_limit),
        "policies": {
            policy_id: {
                "description": policy.description,
                "retentionMode": policy.retention.mode,
            }
            for policy_id, policy in sorted(policy_by_id.items())
        },
    }
    store.write_summary(summary)
    return summary


def apply_plan(
    client: GraphClient,
    store: HistoricalMailboxStore,
    *,
    confirmation: str,
    limit: int = 500,
) -> dict[str, int]:
    if confirmation != _APPLY_CONFIRMATION:
        raise RuntimeError(
            f"Apply requires confirmation {_APPLY_CONFIRMATION}"
        )

    summary = store.summary()
    if not summary.get("scanComplete"):
        raise RuntimeError(
            "Apply refuses an incomplete scan because rolling retention rules need "
            "the complete folder history."
        )
    if str(summary.get("policyPath", "")).endswith(".example.json"):
        raise RuntimeError(
            "Apply refuses example policy files. Copy and review the policy as a "
            "private deployment file first."
        )

    plan = store.load_plan()
    previous = store.completed_apply_outcomes()
    finished = {
        message_id
        for message_id, outcome in previous.items()
        if outcome in {"moved", "missing"}
    }
    pending = [
        str(item["messageId"])
        for item in plan
        if str(item["messageId"]) not in finished
    ]
    if limit > 0:
        pending = pending[:limit]

    if not pending:
        return {"requested": 0, "moved": 0, "missing": 0, "failed": 0, "remaining": 0}

    deleted_items_id = client.get_well_known_folder_id("deleteditems")
    outcomes = client.move_messages_detailed(
        pending,
        destination_folder_id=deleted_items_id,
    )
    store.append_apply_outcomes(outcomes)

    combined = store.completed_apply_outcomes()
    completed = {
        message_id
        for message_id, outcome in combined.items()
        if outcome in {"moved", "missing"}
    }
    remaining = sum(
        str(item["messageId"]) not in completed
        for item in plan
    )
    return {
        "requested": len(pending),
        "moved": sum(outcome == "moved" for outcome in outcomes.values()),
        "missing": sum(outcome == "missing" for outcome in outcomes.values()),
        "failed": sum(outcome == "failed" for outcome in outcomes.values()),
        "remaining": remaining,
    }


APPLY_CONFIRMATION = _APPLY_CONFIRMATION
RESET_CONFIRMATION = _RESET_CONFIRMATION
