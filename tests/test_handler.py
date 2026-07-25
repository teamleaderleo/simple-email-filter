import os
import unittest
from unittest.mock import patch

from handlers.retention_sweeper import lambda_handler


class RetentionHandlerSafetyTests(unittest.TestCase):
    def test_apply_refuses_example_policy(self):
        env = {
            "RETENTION_MODE": "apply",
            "RETENTION_APPLY_CONFIRMATION": "MOVE_TO_DELETED_ITEMS",
            "RETENTION_POLICY_PATH": "policies/personal.example.json",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "refuses example policy"):
                lambda_handler({}, None)

    def test_apply_requires_confirmation(self):
        env = {
            "RETENTION_MODE": "apply",
            "RETENTION_POLICY_PATH": "policies/personal.json",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("RETENTION_APPLY_CONFIRMATION", None)
            with self.assertRaisesRegex(RuntimeError, "requires"):
                lambda_handler({}, None)


if __name__ == "__main__":
    unittest.main()
