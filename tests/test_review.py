from __future__ import annotations

import unittest
from datetime import datetime, timezone

from email_filter.models import MailMessage, MatchRule, Policy, RetentionRule
from email_filter.review import build_unmatched_review, redact_subject


class MailboxReviewTests(unittest.TestCase):
    def test_subject_redaction_removes_obvious_identifiers(self):
        result = redact_subject(
            "Order 123456 for leo@example.com https://example.com/orders/123456"
        )
        self.assertIn("<number>", result)
        self.assertIn("<email>", result)
        self.assertIn("<url>", result)
        self.assertNotIn("123456", result)
        self.assertNotIn("leo@example.com", result)

    def test_review_excludes_matched_mail_and_ranks_unmatched_senders(self):
        messages = [
            self.message("1", "keep@example.com", "Receipt 100", True),
            self.message("2", "promo@example.com", "Big sale 25% off", False),
            self.message("3", "promo@example.com", "Big sale 50% off", True),
            self.message("4", "alerts@example.net", "Security verification 999", False),
        ]
        policies = [
            Policy(
                id="keep",
                description="keep",
                match=MatchRule(senders=("keep@example.com",)),
                retention=RetentionRule(mode="forever"),
            )
        ]

        result = build_unmatched_review(messages, policies, top_senders=10)

        self.assertEqual(result["totalUnmatched"], 3)
        self.assertEqual(result["senders"][0]["sender"], "promo@example.com")
        self.assertEqual(result["senders"][0]["count"], 2)
        self.assertEqual(result["senders"][0]["subjectSignals"]["promotion"], 2)
        security = next(
            item for item in result["senders"] if item["sender"] == "alerts@example.net"
        )
        self.assertTrue(security["manualReviewRecommended"])
        self.assertFalse(result["privacy"]["messageIdsIncluded"])

    def test_sender_filter_returns_only_requested_sender(self):
        messages = [
            self.message("1", "one@example.com", "Newsletter 1", False),
            self.message("2", "two@example.com", "Newsletter 2", False),
        ]
        result = build_unmatched_review(
            messages,
            [],
            sender="two@example.com",
        )
        self.assertEqual(result["filteredUnmatched"], 1)
        self.assertEqual([item["sender"] for item in result["senders"]], ["two@example.com"])

    @staticmethod
    def message(message_id: str, sender: str, subject: str, is_read: bool) -> MailMessage:
        return MailMessage(
            id=message_id,
            sender=sender,
            subject=subject,
            received_at=datetime(2026, 1, int(message_id), tzinfo=timezone.utc),
            is_read=is_read,
        )


if __name__ == "__main__":
    unittest.main()
