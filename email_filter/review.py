from __future__ import annotations

import html
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import MailMessage, Policy

_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_LONG_TOKEN_RE = re.compile(
    r"\b(?=[A-Z0-9_-]{10,}\b)(?=[A-Z0-9_-]*\d)[A-Z0-9_-]+\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,:/-]\d+)*\b")
_SPACE_RE = re.compile(r"\s+")

_SIGNAL_TERMS: dict[str, tuple[str, ...]] = {
    "securityAccount": (
        "account alert",
        "login",
        "password",
        "security alert",
        "verification",
        "verify your email",
        "sign-in",
        "signin",
        "authentication",
        "one-time code",
        "new device",
    ),
    "financial": (
        "bank account",
        "brokerage",
        "account statement",
        "monthly statement",
        "trade confirmation",
        "dividend",
        "deposit completed",
        "deposit request",
        "withdrawal completed",
        "withdrawal request",
        "tax document",
        "tax form",
        "interest payment",
        "payment due",
        "payment failed",
        "payment received",
        "payment confirmation",
    ),
    "purchaseRecord": (
        "receipt",
        "invoice",
        "refund",
        "warranty",
        "order confirmation",
        "payment confirmation",
        "cancellation confirmation",
        "cancelled order",
        "canceled order",
    ),
    "delivery": (
        "shipped",
        "shipment",
        "tracking",
        "delivery update",
        "delivered",
        "package notification",
        "parcel notification",
    ),
    "propertyLegal": (
        "lease",
        "legal notice",
        "property management",
        "management notice",
        "building notice",
        "elevator",
        "amenity",
        "condo notice",
        "maintenance notice",
        "maintenance record",
        "water shutoff",
        "fire inspection",
    ),
    "jobApplication": (
        "application",
        "interview",
        "recruiter",
        "candidate",
        "job alert",
        "job opportunity",
        "hiring",
        "new roles",
        "new vacancies",
        "thank you for applying",
        "thanks for applying",
        "apply now",
    ),
    "promotion": (
        "sale",
        "deal",
        "offer",
        "promo",
        "discount",
        "save",
        "new arrivals",
        "newsletter",
        "shop now",
        "limited time",
    ),
}


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sender_domain(sender: str) -> str:
    return sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""


def first_matching_policy(
    message: MailMessage,
    policies: Iterable[Policy],
) -> Policy | None:
    ordered = sorted(
        (policy for policy in policies if policy.enabled),
        key=lambda policy: (policy.priority, policy.id),
    )
    for policy in ordered:
        if policy.match.matches(message):
            return policy
    return None


def redact_subject(subject: str, *, max_length: int = 160) -> str:
    """Return a stable subject pattern without obvious IDs, addresses, or URLs."""
    value = html.unescape(subject or "").strip().lower()
    value = _URL_RE.sub("<url>", value)
    value = _EMAIL_RE.sub("<email>", value)
    value = _UUID_RE.sub("<id>", value)
    value = _LONG_TOKEN_RE.sub("<id>", value)
    value = _NUMBER_RE.sub("<number>", value)
    value = _SPACE_RE.sub(" ", value).strip()
    if not value:
        return "(no subject)"
    if len(value) > max_length:
        return value[: max_length - 1].rstrip() + "…"
    return value


def _subject_signals(subject: str) -> set[str]:
    lowered = subject.lower()
    return {
        signal
        for signal, terms in _SIGNAL_TERMS.items()
        if any(term in lowered for term in terms)
    }


def _manual_review_signals(
    signals: Counter[str],
    message_count: int,
) -> dict[str, int]:
    """Return repeated non-promotional signals instead of one-off keyword noise."""
    threshold = 1 if message_count <= 20 else max(3, math.ceil(message_count * 0.01))
    return {
        key: count
        for key, count in sorted(signals.items())
        if key != "promotion" and count >= threshold
    }


def _sender_summary(
    sender: str,
    messages: list[MailMessage],
    *,
    samples_per_sender: int,
) -> dict[str, Any]:
    patterns = Counter(redact_subject(message.subject) for message in messages)
    signals: Counter[str] = Counter()
    years: Counter[str] = Counter()
    for message in messages:
        years[str(message.received_at.year)] += 1
        for signal in _subject_signals(message.subject):
            signals[signal] += 1

    received = [message.received_at for message in messages]
    review_signals = _manual_review_signals(signals, len(messages))
    return {
        "sender": sender,
        "domain": sender_domain(sender),
        "count": len(messages),
        "read": sum(message.is_read for message in messages),
        "unread": sum(not message.is_read for message in messages),
        "firstReceived": _iso(min(received)),
        "lastReceived": _iso(max(received)),
        "byYear": dict(sorted(years.items())),
        "subjectSignals": dict(sorted(signals.items())),
        "manualReviewSignals": review_signals,
        "manualReviewRecommended": bool(review_signals),
        "topSubjectPatterns": [
            {"pattern": pattern, "count": count}
            for pattern, count in patterns.most_common(max(1, samples_per_sender))
        ],
    }


def build_unmatched_review(
    messages: Iterable[MailMessage],
    policies: Iterable[Policy],
    *,
    top_senders: int = 25,
    samples_per_sender: int = 4,
    sender: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Summarise unmatched mail without exposing message IDs or full message content."""
    policy_list = list(policies)
    unmatched = [
        message
        for message in messages
        if first_matching_policy(message, policy_list) is None
    ]

    sender_filter = (sender or "").strip().lower()
    domain_filter = (domain or "").strip().lower()
    filtered = [
        message
        for message in unmatched
        if (not sender_filter or message.sender == sender_filter)
        and (not domain_filter or sender_domain(message.sender) == domain_filter)
    ]

    by_sender: dict[str, list[MailMessage]] = defaultdict(list)
    domain_counts: Counter[str] = Counter()
    for message in filtered:
        by_sender[message.sender].append(message)
        domain_counts[sender_domain(message.sender)] += 1

    ranked_senders = sorted(
        by_sender,
        key=lambda value: (-len(by_sender[value]), value),
    )
    if not sender_filter:
        ranked_senders = ranked_senders[: max(1, top_senders)]

    return {
        "version": 1,
        "totalUnmatched": len(unmatched),
        "filteredUnmatched": len(filtered),
        "senderFilter": sender_filter or None,
        "domainFilter": domain_filter or None,
        "topUnmatchedDomains": [
            {"domain": value, "count": count}
            for value, count in domain_counts.most_common(max(1, top_senders))
            if value
        ],
        "senders": [
            _sender_summary(
                value,
                by_sender[value],
                samples_per_sender=samples_per_sender,
            )
            for value in ranked_senders
        ],
        "privacy": {
            "messageIdsIncluded": False,
            "bodiesIncluded": False,
            "subjectsRedacted": True,
        },
    }
