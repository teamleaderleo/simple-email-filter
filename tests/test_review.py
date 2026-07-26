from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

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
            self.message("4", "alerts@example.net", "Security alert 999", False),
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
        self.assertEqual(security["manualReviewSignals"]["securityAccount"], 1)
        self.assertFalse(result["privacy"]["messageIdsIncluded"])

    def test_large_promotional_sender_ignores_sparse_keyword_noise(self):
        messages = [
            self.dated_message(
                str(index),
                "promo@example.com",
                "Save on new arrivals",
                False,
                index,
            )
            for index in range(1, 201)
        ]
        messages.append(
            self.dated_message(
                "201",
                "promo@example.com",
                "Delivery update for one promotion",
                False,
                201,
            )
        )

        result = build_unmatched_review(messages, [], top_senders=10)
        sender = result["senders"][0]

        self.assertFalse(sender["manualReviewRecommended"])
        self.assertEqual(sender["manualReviewSignals"], {})
        self.assertEqual(sender["subjectSignals"]["delivery"], 1)

    def test_large_mixed_sender_flags_repeated_security_signals(self):
        messages = [
            self.dated_message(
                str(index),
                "mixed@example.com",
                "Save on new arrivals",
                False,
                index,
            )
            for index in range(1, 198)
        ]
        messages.extend(
            [
                self.dated_message(
                    "198",
                    "mixed@example.com",
                    "Security alert: new login",
                    False,
                    198,
                ),
                self.dated_message(
                    "199",
                    "mixed@example.com",
                    "Security alert: new device",
                    False,
                    199,
                ),
                self.dated_message(
                    "200",
                    "mixed@example.com",
                    "Security alert: password changed",
                    False,
                    200,
                ),
            ]
        )

        result = build_unmatched_review(messages, [], top_senders=10)
        sender = result["senders"][0]

        self.assertTrue(sender["manualReviewRecommended"])
        self.assertEqual(sender["manualReviewSignals"]["securityAccount"], 3)

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
        self.assertEqual(
            [item["sender"] for item in result["senders"]],
            ["two@example.com"],
        )

    @staticmethod
    def message(message_id: str, sender: str, subject: str, is_read: bool) -> MailMessage:
        return MailMessage(
            id=message_id,
            sender=sender,
            subject=subject,
            received_at=datetime(2026, 1, int(message_id), tzinfo=timezone.utc),
            is_read=is_read,
        )

    @staticmethod
    def dated_message(
        message_id: str,
        sender: str,
        subject: str,
        is_read: bool,
        offset: int,
    ) -> MailMessage:
        return MailMessage(
            id=message_id,
            sender=sender,
            subject=subject,
            received_at=datetime(2025, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=offset),
            is_read=is_read,
        )


if __name__ == "__main__":
    unittest.main()
