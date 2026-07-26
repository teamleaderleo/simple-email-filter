from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mailbox_export
from email_filter.historical import HistoricalMailboxStore
from email_filter.models import MailMessage, RetentionPlanItem


class MailboxExportProgressTests(unittest.TestCase):
    def test_export_remains_available_after_apply_starts(self):
        now = datetime.now(timezone.utc)
        message = MailMessage(
            id="private-message-id",
            sender="promo@example.com",
            subject="Private sale subject 123456",
            received_at=now - timedelta(days=90),
            is_read=False,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            output_dir = root / "export"
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "policies": [
                            {
                                "id": "marketing-promotions",
                                "description": "Expire reviewed promotions",
                                "priority": 80,
                                "match": {"senders": ["promo@example.com"]},
                                "retention": {"mode": "days", "days": 45},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            store = HistoricalMailboxStore(state_dir)
            store.begin_scan("inbox")
            store.append_page([message], next_link=None)
            store.write_plan(
                [
                    RetentionPlanItem(
                        message_id=message.id,
                        policy_id="marketing-promotions",
                        received_at=message.received_at,
                        action="deleteditems",
                        reason="older than 45 days",
                        sender=message.sender,
                        subject=message.subject,
                    )
                ]
            )
            store.write_summary(
                {
                    "folder": "inbox",
                    "scanComplete": True,
                    "scanned": 1,
                    "read": 0,
                    "unread": 1,
                    "matched": 1,
                    "unmatched": 0,
                    "protectedForever": 0,
                    "keptByRetention": 0,
                    "selected": 1,
                    "policyPath": str(policy_path),
                    "policies": {"marketing-promotions": {"matched": 1}},
                }
            )
            store.append_apply_outcomes({message.id: "moved"})

            result = mailbox_export.run(
                Namespace(
                    state_dir=str(state_dir),
                    output_dir=str(output_dir),
                    policy=None,
                    samples=4,
                    top=25,
                )
            )

            self.assertTrue(result["applyProgress"]["applyStarted"])
            self.assertEqual(result["applyProgress"]["moved"], 1)
            self.assertEqual(result["applyProgress"]["pending"], 0)
            self.assertTrue((output_dir / "apply-progress.json").exists())
            self.assertTrue((output_dir / "apply-progress.csv").exists())

            summary = json.loads(
                (output_dir / "mailbox-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["applyProgress"]["allPlan"]["moved"], 1)
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["files"]["applyProgressJson"],
                "apply-progress.json",
            )

            progress_text = (output_dir / "apply-progress.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(message.id, progress_text)
            self.assertNotIn(message.sender, progress_text)
            self.assertNotIn(message.subject, progress_text)

    def test_apply_started_export_rejects_a_different_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            original = root / "original.json"
            different = root / "different.json"
            empty_policy = {"version": 1, "policies": []}
            original.write_text(json.dumps(empty_policy), encoding="utf-8")
            different.write_text(json.dumps(empty_policy), encoding="utf-8")

            store = HistoricalMailboxStore(state_dir)
            store.begin_scan("inbox")
            store.append_page([], next_link=None)
            store.write_plan([])
            store.write_summary(
                {
                    "folder": "inbox",
                    "scanComplete": True,
                    "scanned": 0,
                    "policyPath": str(original),
                    "policies": {},
                }
            )
            store.append_apply_outcomes({"some-id": "moved"})

            with self.assertRaisesRegex(RuntimeError, "policy recorded by the saved plan"):
                mailbox_export.run(
                    Namespace(
                        state_dir=str(state_dir),
                        output_dir=str(root / "export"),
                        policy=str(different),
                        samples=4,
                        top=25,
                    )
                )


if __name__ == "__main__":
    unittest.main()
