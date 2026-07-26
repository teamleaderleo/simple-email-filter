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
        cls.by_id = {policy.id: policy for policy in cls.policies}

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

    def test_crypto_brokerage_and_reviewed_financial_records_are_protected(self):
        self.assertEqual(
            self.first_policy("hello@crypto.com", "Withdrawal completed"),
            "crypto-account-records",
        )
        self.assertEqual(
            self.first_policy("notice@interactivebrokers.com", "Monthly activity"),
            "brokerage-account-records",
        )
        for sender in (
            "bmoalerts@bmo.com",
            "support@questrade.com",
            "noreply@newton.co",
            "notifications@mail.shakepay.com",
            "notify@payments.interac.ca",
            "service@intl.paypal.com",
        ):
            with self.subTest(sender=sender):
                self.assertEqual(
                    self.first_policy(sender, "Account activity"),
                    "financial-account-records",
                )

    def test_reviewed_security_records_are_protected(self):
        for sender in (
            "no-reply@accounts.google.com",
            "noreply@github.com",
            "noreply@email.apple.com",
        ):
            with self.subTest(sender=sender):
                self.assertEqual(
                    self.first_policy(sender, "Security alert"),
                    "account-security-records",
                )
        self.assertEqual(
            self.first_policy("no-reply@pixiv.net", "New login detected"),
            "pixiv-security-records",
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
        for sender in (
            "no-reply@ashbyhq.com",
            "no-reply@hire.lever.co",
            "noreply@mail.amazon.jobs",
        ):
            with self.subTest(sender=sender):
                self.assertEqual(
                    self.first_policy(sender, "Thank you for applying"),
                    "reviewed-job-application-records",
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
                "Your Sunday afternoon trip with Uber",
            ),
            "uber-trip-records",
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

    def test_reviewed_job_alerts_and_newsletters_use_sender_grouping(self):
        self.assertEqual(
            self.first_policy("alerts@ziprecruiter.com", "New jobs for you"),
            "job-alerts",
        )
        self.assertEqual(
            self.first_policy("bytebytego@substack.com", "A guide to caching"),
            "technical-and-cultural-newsletters",
        )
        self.assertEqual(
            self.by_id["job-alerts"].retention.group_by,
            "sender",
        )
        self.assertEqual(
            self.by_id["technical-and-cultural-newsletters"].retention.group_by,
            "sender",
        )

    def test_reviewed_retail_senders_use_marketing_policy(self):
        for sender in (
            "info@n.myprotein.com",
            "newsletter@enews.uniqlo.ca",
            "store-news@amazon.ca",
            "microsoftstore@microsoftstoreemail.com",
            "email@email.salomon.com",
            "shop@beauty.sephora.com",
            "starbucks@e.starbucks.com",
        ):
            with self.subTest(sender=sender):
                self.assertEqual(
                    self.first_policy(sender, "New deals just dropped"),
                    "marketing-promotions",
                )

    def test_amazon_review_requests_do_not_catch_mixed_subscription_sender(self):
        self.assertEqual(
            self.first_policy(
                "marketplace-messages@amazon.ca",
                "Will you rate your transaction?",
            ),
            "amazon-review-requests",
        )
        self.assertIsNone(
            self.first_policy(
                "no-reply@amazon.ca",
                "Your new subscription",
            )
        )


if __name__ == "__main__":
    unittest.main()
