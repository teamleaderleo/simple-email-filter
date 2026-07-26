from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

RetentionMode = Literal["forever", "days", "latest", "days_and_latest"]
RetentionGroup = Literal["policy", "sender"]
ExpiryAction = Literal["deleteditems"]


def parse_graph_datetime(value: str) -> datetime:
    """Parse a Microsoft Graph timestamp into an aware UTC datetime."""
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MailMessage:
    id: str
    sender: str
    subject: str
    received_at: datetime
    parent_folder_id: str | None = None
    is_read: bool = False
    categories: tuple[str, ...] = ()

    @classmethod
    def from_graph(cls, payload: dict[str, Any]) -> "MailMessage":
        sender = (
            (payload.get("from") or {})
            .get("emailAddress", {})
            .get("address", "")
        )
        return cls(
            id=str(payload["id"]),
            sender=sender.lower(),
            subject=str(payload.get("subject") or ""),
            received_at=parse_graph_datetime(str(payload["receivedDateTime"])),
            parent_folder_id=payload.get("parentFolderId"),
            is_read=bool(payload.get("isRead", False)),
            categories=tuple(payload.get("categories") or ()),
        )


@dataclass(frozen=True)
class MatchRule:
    senders: tuple[str, ...] = ()
    sender_domains: tuple[str, ...] = ()
    subject_contains: tuple[str, ...] = ()
    subject_excludes: tuple[str, ...] = ()

    def matches(self, message: MailMessage) -> bool:
        sender = message.sender.lower()
        subject = message.subject.lower()

        if self.senders and sender not in self.senders:
            return False

        if self.sender_domains:
            domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
            if domain not in self.sender_domains:
                return False

        if self.subject_contains and not any(
            needle in subject for needle in self.subject_contains
        ):
            return False

        if self.subject_excludes and any(
            needle in subject for needle in self.subject_excludes
        ):
            return False

        return True


@dataclass(frozen=True)
class RetentionRule:
    mode: RetentionMode
    days: int | None = None
    keep_latest: int | None = None
    group_by: RetentionGroup = "policy"


@dataclass(frozen=True)
class Policy:
    id: str
    description: str
    match: MatchRule
    retention: RetentionRule
    enabled: bool = True
    on_expiry: ExpiryAction = "deleteditems"
    priority: int = 100


@dataclass(frozen=True)
class RetentionPlanItem:
    message_id: str
    policy_id: str
    received_at: datetime
    action: ExpiryAction
    reason: str
    sender: str = field(repr=False)
    subject: str = field(repr=False)
