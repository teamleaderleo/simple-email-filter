import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from email_filter.historical import (
    APPLY_CONFIRMATION,
    HistoricalMailboxStore,
    apply_plan,
    build_audit,
    scan_folder,
)
from email_filter.models import (
    MailMessage,
    MatchRule,
    Policy,
    RetentionPlanItem,
    RetentionRule,
)


UTC = timezone.utc


def message(message_id, sender, subject, year=2020, is_read=True):
    return MailMessage(
        id=message_id,
        sender=sender,
        subject=subject,
        received_at=datetime(year, 1, 1, tzinfo=UTC),
        parent_folder_id="inbox-id",
        is_read=is_read,
    )


class FakeScanClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def iter_folder_message_pages(self, **kwargs):
        self.calls.append(kwargs)
        yield from self.pages


class FakeApplyClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.moved_batches = []

    def get_well_known_folder_id(self, name):
        self.folder_name = name
        return "deleted-id"

    def move_messages_detailed(self, message_ids, **kwargs):
        self.moved_batches.append((list(message_ids), kwargs))
        return self.outcomes.pop(0)


class HistoricalScanTests(unittest.TestCase):
    def test_scan_resumes_and_snapshot_deduplicates_by_immutable_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoricalMailboxStore(directory)
            first = FakeScanClient(
                [([message("a", "a@example.com", "A"), message("b", "b@example.com", "B")], "next-1")]
            )
            checkpoint = scan_folder(
                first,
                store,
                folder="inbox",
                page_size=999,
                max_pages=1,
            )
            self.assertFalse(checkpoint["complete"])
            self.assertEqual(checkpoint["scanned"], 2)

            second = FakeScanClient(
                [([message("b", "b@example.com", "B"), message("c", "c@example.com", "C")], None)]
            )
            checkpoint = scan_folder(second, store, folder="inbox")
            self.assertTrue(checkpoint["complete"])
            self.assertEqual(second.calls[0]["start_url"], "next-1")
            self.assertEqual({item.id for item in store.load_messages()}, {"a", "b", "c"})

    def test_audit_reports_protected_selected_and_unmatched(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoricalMailboxStore(directory)
            store.begin_scan("inbox")
            store.append_page(
                [
                    message("receipt", "shop@example.com", "Receipt"),
                    message("promo", "promo@example.com", "Sale"),
                    message("other", "person@example.net", "Hello", is_read=False),
                ],
                next_link=None,
            )
            policies = [
                Policy(
                    id="receipts",
                    description="Keep receipts",
                    priority=10,
                    match=MatchRule(subject_contains=("receipt",)),
                    retention=RetentionRule(mode="forever"),
                ),
                Policy(
                    id="promotions",
                    description="Expire promotions",
                    priority=20,
                    match=MatchRule(senders=("promo@example.com",)),
                    retention=RetentionRule(mode="days", days=45),
                ),
            ]
            summary = build_audit(
                store,
                policies,
                policy_path="policies/personal.json",
            )
            self.assertEqual(summary["scanned"], 3)
            self.assertEqual(summary["matched"], 2)
            self.assertEqual(summary["unmatched"], 1)
            self.assertEqual(summary["protectedForever"], 1)
            self.assertEqual(summary["selected"], 1)
            self.assertEqual(summary["unread"], 1)
            self.assertEqual(summary["selectedByPolicy"], {"promotions": 1})
            self.assertEqual(
                summary["topUnmatchedSenders"][0],
                {"value": "person@example.net", "count": 1},
            )


class HistoricalApplyTests(unittest.TestCase):
    def _prepared_store(self, directory):
        store = HistoricalMailboxStore(directory)
        store.write_summary(
            {
                "scanComplete": True,
                "policyPath": "policies/personal.json",
            }
        )
        store.write_plan(
            [
                RetentionPlanItem(
                    message_id=message_id,
                    policy_id="promotions",
                    received_at=datetime(2020, 1, 1, tzinfo=UTC),
                    action="deleteditems",
                    reason="old",
                    sender="promo@example.com",
                    subject="Sale",
                )
                for message_id in ("a", "b", "c")
            ]
        )
        return store

    def test_apply_is_bounded_and_resumes_completed_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._prepared_store(directory)
            client = FakeApplyClient(
                [
                    {"a": "moved", "b": "missing"},
                    {"c": "moved"},
                ]
            )
            first = apply_plan(
                client,
                store,
                confirmation=APPLY_CONFIRMATION,
                limit=2,
            )
            self.assertEqual(first["requested"], 2)
            self.assertEqual(first["moved"], 1)
            self.assertEqual(first["missing"], 1)
            self.assertEqual(first["remaining"], 1)

            second = apply_plan(
                client,
                store,
                confirmation=APPLY_CONFIRMATION,
                limit=2,
            )
            self.assertEqual(second["requested"], 1)
            self.assertEqual(second["moved"], 1)
            self.assertEqual(second["remaining"], 0)
            self.assertEqual(client.moved_batches[1][0], ["c"])

    def test_apply_refuses_example_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._prepared_store(directory)
            store.write_summary(
                {
                    "scanComplete": True,
                    "policyPath": "policies/personal.example.json",
                }
            )
            with self.assertRaisesRegex(RuntimeError, "example policy"):
                apply_plan(
                    FakeApplyClient([]),
                    store,
                    confirmation=APPLY_CONFIRMATION,
                )

    def test_apply_refuses_incomplete_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._prepared_store(directory)
            store.write_summary(
                {
                    "scanComplete": False,
                    "policyPath": "policies/personal.json",
                }
            )
            with self.assertRaisesRegex(RuntimeError, "incomplete scan"):
                apply_plan(
                    FakeApplyClient([]),
                    store,
                    confirmation=APPLY_CONFIRMATION,
                )


if __name__ == "__main__":
    unittest.main()
