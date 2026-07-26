import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

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


class FakeDynamoClient:
    pass


fake_boto3 = types.ModuleType("boto3")
fake_boto3.resource = lambda *args, **kwargs: FakeDynamoResource()
fake_boto3.client = lambda *args, **kwargs: FakeDynamoClient()
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

MODULE_PATH = Path(__file__).parents[1] / "setup_webhook.py"
spec = importlib.util.spec_from_file_location("setup_webhook_under_test", MODULE_PATH)
setup_webhook = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(setup_webhook)


class FakeResponse:
    def __init__(self, status_code, body, content_type="text/plain; charset=utf-8"):
        self.status_code = status_code
        self.text = body
        self.headers = {"Content-Type": content_type}


class FakeHttpClient:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        token = kwargs["params"]["validationToken"]
        return self.response_factory(token)


class NotificationEndpointTests(unittest.TestCase):
    def test_accepts_exact_plain_text_echo(self):
        client = FakeHttpClient(lambda token: FakeResponse(200, token))

        setup_webhook.validate_notification_endpoint(
            "https://example.test/webhook",
            http_client=client,
        )

        self.assertEqual(len(client.calls), 1)
        _, kwargs = client.calls[0]
        self.assertEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["data"], "")

    def test_rejects_bad_gateway(self):
        client = FakeHttpClient(
            lambda token: FakeResponse(
                502,
                '{"message":"Internal server error"}',
                "application/json",
            )
        )

        with self.assertRaises(setup_webhook.NotificationEndpointError) as context:
            setup_webhook.validate_notification_endpoint(
                "https://example.test/webhook",
                http_client=client,
            )

        self.assertIn("HTTP 502", str(context.exception))

    def test_rejects_encoded_or_modified_token(self):
        client = FakeHttpClient(lambda token: FakeResponse(200, token + "%20"))

        with self.assertRaises(setup_webhook.NotificationEndpointError):
            setup_webhook.validate_notification_endpoint(
                "https://example.test/webhook",
                http_client=client,
            )

    def test_rejects_wrong_content_type(self):
        client = FakeHttpClient(
            lambda token: FakeResponse(200, token, "application/json")
        )

        with self.assertRaises(setup_webhook.NotificationEndpointError):
            setup_webhook.validate_notification_endpoint(
                "https://example.test/webhook",
                http_client=client,
            )


if __name__ == "__main__":
    unittest.main()
