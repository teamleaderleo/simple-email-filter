import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import mailbox_cleanup


class MailboxCliTests(unittest.TestCase):
    def test_apply_limit_is_bounded(self):
        parser = mailbox_cleanup.parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--limit", "0", "--confirm", "x"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--limit", "5001", "--confirm", "x"])

    def test_audit_refuses_to_replace_plan_after_apply_started(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "apply-results.jsonl").write_text(
                '{"messageId":"a","outcome":"moved"}\n',
                encoding="utf-8",
            )
            args = Namespace(
                state_dir=directory,
                folder="inbox",
                page_size=999,
                max_pages=None,
                restart=False,
                policy="policies/personal.example.json",
                top=25,
            )
            with self.assertRaisesRegex(RuntimeError, "Apply has already started"):
                mailbox_cleanup.audit(args)


if __name__ == "__main__":
    unittest.main()
