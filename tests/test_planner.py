import unittest
from datetime import datetime, timedelta, timezone

from email_filter.models import MailMessage, MatchRule, Policy, RetentionRule
from email_filter.planner import build_retention_plan

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


def message(
    identifier,
    age_days,
    *,
    sender="alerts@example.com",
    subject="Alert",
    folder=None,
):
    return MailMessage(
        id=identifier,
        sender=sender,
        subject=subject,
        received_at=NOW - timedelta(days=age_days),
        parent_folder_id=folder,
    )


class RetentionPlannerTests(unittest.TestCase):
    def test_forever_never_expires(self):
        policy = Policy(
            id="archive",
            description="",
            match=MatchRule(senders=("alerts@example.com",)),
            retention=RetentionRule(mode="forever"),
        )
        self.assertEqual(
            build_retention_plan([message("1", 1000)], [policy], now=NOW),
            [],
        )

    def test_days_expires_only_old_messages(self):
        policy = Policy(
            id="temporary",
            description="",
            match=MatchRule(senders=("alerts@example.com",)),
            retention=RetentionRule(mode="days", days=30),
        )
        plan = build_retention_plan(
            [message("new", 10), message("old", 31)],
            [policy],
            now=NOW,
        )
        self.assertEqual([item.message_id for item in plan], ["old"])

    def test_days_and_latest_protects_latest_even_when_old(self):
        policy = Policy(
            id="prints",
            description="",
            match=MatchRule(senders=("alerts@example.com",)),
            retention=RetentionRule(
                mode="days_and_latest",
                days=30,
                keep_latest=2,
            ),
        )
        plan = build_retention_plan(
            [
                message("newest", 31),
                message("second", 40),
                message("third", 50),
            ],
            [policy],
            now=NOW,
        )
        self.assertEqual([item.message_id for item in plan], ["third"])

    def test_first_matching_policy_wins(self):
        protected = Policy(
            id="protected",
            description="",
            priority=10,
            match=MatchRule(
                senders=("alerts@example.com",),
                subject_contains=("receipt",),
            ),
            retention=RetentionRule(mode="forever"),
        )
        broad = Policy(
            id="broad",
            description="",
            priority=20,
            match=MatchRule(senders=("alerts@example.com",)),
            retention=RetentionRule(mode="days", days=1),
        )
        plan = build_retention_plan(
            [message("receipt", 100, subject="Your receipt")],
            [protected, broad],
            now=NOW,
        )
        self.assertEqual(plan, [])

    def test_excluded_folder_is_skipped(self):
        policy = Policy(
            id="temporary",
            description="",
            match=MatchRule(senders=("alerts@example.com",)),
            retention=RetentionRule(mode="days", days=1),
        )
        plan = build_retention_plan(
            [message("deleted", 10, folder="deleted-id")],
            [policy],
            now=NOW,
            excluded_folder_ids={"deleted-id"},
        )
        self.assertEqual(plan, [])


if __name__ == "__main__":
    unittest.main()
