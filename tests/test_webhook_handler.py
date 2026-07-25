import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")


class FakeTable:
    def get_item(self, **kwargs):
        return {}

    def put_item(self, **kwargs):
        return {}


class FakeDynamoResource:
    def Table(self, name):
        return FakeTable()


fake_boto3 = types.ModuleType("boto3")
fake_boto3.resource = lambda *args, **kwargs: FakeDynamoResource()
sys.modules["boto3"] = fake_boto3

fake_botocore = types.ModuleType("botocore")
fake_botocore_exceptions = types.ModuleType("botocore.exceptions")
fake_botocore_exceptions.ClientError = RuntimeError
fake_botocore.exceptions = fake_botocore_exceptions
sys.modules["botocore"] = fake_botocore
sys.modules["botocore.exceptions"] = fake_botocore_exceptions

fake_msal = types.ModuleType("msal")
fake_msal.SerializableTokenCache = object
fake_msal.PublicClientApplication = object
sys.modules["msal"] = fake_msal

MODULE_PATH = Path(__file__).parents[1] / "webhook_handler.py"
spec = importlib.util.spec_from_file_location("webhook_handler_under_test", MODULE_PATH)
webhook_handler = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(webhook_handler)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class WebhookSecurityTests(unittest.TestCase):
    def test_accepts_matching_client_state_and_subscription(self):
        notification = {
            "clientState": "secret",
            "subscriptionId": "subscription-1",
        }
        self.assertTrue(
            webhook_handler.is_notification_authentic(
                notification,
                "secret",
                "subscription-1",
            )
        )

    def test_rejects_wrong_client_state(self):
        notification = {
            "clientState": "wrong",
            "subscriptionId": "subscription-1",
        }
        self.assertFalse(
            webhook_handler.is_notification_authentic(
                notification,
                "secret",
                "subscription-1",
            )
        )

    def test_rejects_wrong_subscription(self):
        notification = {
            "clientState": "secret",
            "subscriptionId": "other",
        }
        self.assertFalse(
            webhook_handler.is_notification_authentic(
                notification,
                "secret",
                "subscription-1",
            )
        )

    def test_extracts_resource_data_message_id(self):
        self.assertEqual(
            webhook_handler.extract_message_id(
                {"resourceData": {"id": "immutable-message-id"}}
            ),
            "immutable-message-id",
        )

    def test_extracts_fallback_resource_message_id_case_insensitively(self):
        self.assertEqual(
            webhook_handler.extract_message_id(
                {"resource": "Users/u/Messages('abc%2B123')"}
            ),
            "abc+123",
        )

    @patch.object(webhook_handler, "authenticate_microsoft")
    @patch.object(webhook_handler, "load_subscription_record")
    def test_invalid_notification_is_rejected_before_authentication(
        self,
        load_subscription_record,
        authenticate_microsoft,
    ):
        load_subscription_record.return_value = {
            "client_state": "secret",
            "subscription_id": "subscription-1",
        }
        event = {
            "body": {
                "value": [
                    {
                        "clientState": "wrong",
                        "subscriptionId": "subscription-1",
                        "changeType": "created",
                    }
                ]
            }
        }
        response = webhook_handler.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 202)
        authenticate_microsoft.assert_not_called()


class ExactMessageProcessingTests(unittest.TestCase):
    @patch.object(webhook_handler, "mark_processed")
    @patch.object(webhook_handler, "get_deletion_decision")
    @patch.object(webhook_handler, "get_message")
    @patch.object(webhook_handler, "already_processed", return_value=False)
    def test_fetches_only_the_message_named_by_notification(
        self,
        already_processed,
        get_message,
        get_deletion_decision,
        mark_processed,
    ):
        session = Mock()
        get_message.return_value = {
            "id": "message-42",
            "parentFolderId": "junk-id",
            "subject": "Legitimate newsletter",
            "from": {"emailAddress": {"address": "news@example.com"}},
            "bodyPreview": "Hello",
            "receivedDateTime": "2026-07-26T00:00:00Z",
        }
        get_deletion_decision.return_value = (False, "0")

        result = webhook_handler.process_webhook_notification(
            {"resourceData": {"id": "message-42"}},
            session,
            "junk-id",
        )

        self.assertEqual(result, {"processed": 1, "deleted": 0, "failed": 0})
        get_message.assert_called_once_with(session, "message-42")
        get_deletion_decision.assert_called_once()
        mark_processed.assert_called_once_with("message-42", "kept")

    @patch.object(webhook_handler, "mark_processed")
    @patch.object(webhook_handler, "get_deletion_decision")
    @patch.object(webhook_handler, "get_message")
    @patch.object(webhook_handler, "already_processed", return_value=False)
    def test_message_moved_out_of_junk_is_left_alone(
        self,
        already_processed,
        get_message,
        get_deletion_decision,
        mark_processed,
    ):
        get_message.return_value = {
            "id": "message-42",
            "parentFolderId": "inbox-id",
        }

        result = webhook_handler.process_webhook_notification(
            {"resourceData": {"id": "message-42"}},
            Mock(),
            "junk-id",
        )

        self.assertEqual(result, {"processed": 0, "deleted": 0, "failed": 0})
        get_deletion_decision.assert_not_called()
        mark_processed.assert_called_once_with("message-42", "not_in_junk")

    @patch.object(webhook_handler.time, "sleep")
    def test_message_fetch_retries_transient_graph_error(self, sleep):
        session = Mock()
        session.get.side_effect = [
            FakeResponse(status_code=503),
            FakeResponse(status_code=200, payload={"id": "message-42"}),
        ]
        result = webhook_handler.get_message(session, "message-42", max_attempts=2)
        self.assertEqual(result, {"id": "message-42"})
        self.assertEqual(session.get.call_count, 2)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
