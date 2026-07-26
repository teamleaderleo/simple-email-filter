from __future__ import annotations

import csv
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

from email_filter.analysis_export import export_mailbox_analysis
from email_filter.models import MailMessage, MatchRule, Policy, RetentionRule


class MailboxAnalysisExportTests(unittest.TestCase):
    def test_writes_privacy_minimised_analysis_package(self):
        now = datetime.now(timezone.utc)
        messages = [
            self.message(
                "msg-secret-receipt",
                "orders@example.com",
                "Receipt 123456 for leo@example.com",
                now - timedelta(days=500),
                True,
            ),
            self.message(
                "msg-secret-promo-old",
                "promo@example.com",
                "Big sale 50% off order 998877",
                now - timedelta(days=90),
                False,
            ),
            self.message(
                "msg-secret-promo-new",
                "promo@example.com",
                "New arrivals 25% off",
                now - timedelta(days=2),
                False,
            ),
            self.message(
                "msg-secret-alert",
                "alerts@example.net",
                "Security verification 999999 for leo@example.com",
                now - timedelta(days=3),
                False,
            ),
        ]
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
                priority=80,
                match=MatchRule(senders=("promo@example.com",)),
                retention=RetentionRule(mode="days", days=45),
            ),
        ]
        summary = {
            "folder": "inbox",
            "scanComplete": True,
            "scanned": 4,
            "read": 1,
            "unread": 3,
            "matched": 3,
            "unmatched": 1,
            "protectedForever": 1,
            "keptByRetention": 1,
            "selected": 1,
        }

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = export_mailbox_analysis(
                output,
                messages,
                policies,
                summary,
                policy_path="policies/test.json",
                samples_per_sender=4,
            )

            expected = {
                "mailbox-summary.json",
                "sender-summary.csv",
                "policy-impact.csv",
                "unmatched-senders.csv",
                "subject-patterns.csv",
                "unmatched-review.json",
                "mailbox-analysis.xlsx",
                "README.txt",
                "manifest.json",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            self.assertEqual(result["counts"]["policies"], 2)
            self.assertEqual(result["counts"]["senders"], 3)

            manifest = json.loads((output / "manifest.json").read_text())
            self.assertFalse(manifest["privacy"]["messageIdsIncluded"])
            self.assertFalse(manifest["privacy"]["rawSubjectsIncluded"])
            self.assertTrue(manifest["privacy"]["senderAddressesIncluded"])

            with (output / "policy-impact.csv").open(
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                policy_rows = {row["policyId"]: row for row in csv.DictReader(handle)}
            self.assertEqual(policy_rows["receipts"]["protectedForever"], "1")
            self.assertEqual(policy_rows["promotions"]["selected"], "1")
            self.assertEqual(policy_rows["promotions"]["keptByRetention"], "1")

            review_text = (output / "unmatched-review.json").read_text()
            self.assertIn("security verification", review_text)
            self.assertIn("<number>", review_text)
            self.assertIn("<email>", review_text)
            self.assertNotIn("999999", review_text)
            self.assertNotIn("msg-secret", review_text)

            for path in output.iterdir():
                if path.suffix in {".json", ".csv", ".txt"}:
                    self.assertNotIn("msg-secret", path.read_text(encoding="utf-8-sig"))

            workbook_path = output / "mailbox-analysis.xlsx"
            with ZipFile(workbook_path) as workbook:
                names = set(workbook.namelist())
                self.assertIn("xl/workbook.xml", names)
                self.assertIn("xl/styles.xml", names)
                self.assertEqual(
                    len(
                        [
                            name
                            for name in names
                            if name.startswith("xl/worksheets/sheet")
                            and name.endswith(".xml")
                        ]
                    ),
                    6,
                )
                for name in names:
                    if name.endswith(".xml"):
                        ET.fromstring(workbook.read(name))
                workbook_bytes = b"".join(
                    workbook.read(name)
                    for name in names
                    if name.endswith(".xml")
                )
                self.assertNotIn(b"msg-secret", workbook_bytes)
                self.assertNotIn(b"999999", workbook_bytes)
                self.assertIn(b"Redacted Subject Patterns", workbook_bytes)

    @staticmethod
    def message(
        message_id: str,
        sender: str,
        subject: str,
        received_at: datetime,
        is_read: bool,
    ) -> MailMessage:
        return MailMessage(
            id=message_id,
            sender=sender,
            subject=subject,
            received_at=received_at,
            is_read=is_read,
        )


if __name__ == "__main__":
    unittest.main()
