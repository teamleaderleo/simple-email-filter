from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from email_filter.models import MailMessage
from email_filter.policy import load_policies


class ReviewedPersonalPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policies = load_policies(Path("policies/personal.example.json"))

    def first_policy(self, sender: str, subject: str) -> str | None:
        message = MailMessage(
            id="message",
            sender=sender,
            subject=subject,
            received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for policy in self.policies:
            if policy.match.matches(message):
                return policy.id
        return None

    def test_global_purchase_records_are_protected(self):
        self.assertEqual(
            self.first_policy("orders@example.com", "Your refund was approved"),
            "purchase-receipts",
        )

    def test_crypto_and_brokerage_records_are_protected(self):
        self.assertEqual(
            self.first_policy("hello@crypto.com", "Withdrawal completed"),
            "crypto-account-records",
        )
        self.assertEqual(
            self.first_policy("notice@interactivebrokers.com", "Monthly activity"),
            "brokerage-account-records",
        )

    def test_application_records_precede_job_alerts(self):
        self.assertEqual(
            self.first_policy(
                "jobs-noreply@linkedin.com",
                "Leo, you have new application updates this week",
            ),
            "linkedin-job-application-records",
        )
        self.assertEqual(
            self.first_policy(
                "jobs-noreply@linkedin.com",
                "Leo, looking for a new job?",
            ),
            "job-alerts",
        )
        self.assertEqual(
            self.first_policy(
                "no-reply@us.greenhouse-mail.io",
                "Thank you for applying",
            ),
            "job-application-platform-records",
        )

    def test_property_records_precede_general_announcements(self):
        self.assertEqual(
            self.first_policy("wayne@iconpm.ca", "Maintenance notice - water shutoff"),
            "building-records",
        )
        self.assertEqual(
            self.first_policy("wayne@iconpm.ca", "Balcony safety reminder"),
            "building-announcements",
        )

    def test_mixed_uber_sender_uses_subject_specific_rules(self):
        self.assertEqual(
            self.first_policy("noreply@uber.com", "Security alert: new login"),
            "uber-security-records",
        )
        self.assertEqual(
            self.first_policy(
                "noreply@uber.com",
                "Your Sunday afternoon order with Uber Eats",
            ),
            "uber-order-notifications",
        )
        self.assertEqual(
            self.first_policy("noreply@uber.com", "Save with Uber One"),
            "uber-promotions",
        )

    def test_reviewed_retail_senders_use_marketing_policy(self):
        for sender in (
            "info@n.myprotein.com",
            "newsletter@enews.uniqlo.ca",
            "store-news@amazon.ca",
            "microsoftstore@microsoftstoreemail.com",
        ):
            with self.subTest(sender=sender):
                self.assertEqual(
                    self.first_policy(sender, "New deals just dropped"),
                    "marketing-promotions",
                )


if __name__ == "__main__":
    unittest.main()
