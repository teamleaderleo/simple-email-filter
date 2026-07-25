import unittest

from email_filter.graph import GraphClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.responses.pop(0)


class GraphBatchTests(unittest.TestCase):
    def test_missing_subresponse_is_retried(self):
        session = FakeSession(
            [
                FakeResponse({"responses": [{"id": "0", "status": 201}]}),
                FakeResponse({"responses": [{"id": "0", "status": 201}]}),
            ]
        )
        client = GraphClient("token", session=session)
        result = client.move_messages(
            ["message-a", "message-b"],
            destination_folder_id="deleted",
            max_attempts=2,
        )
        self.assertEqual(result, {"moved": 2, "failed": 0})
        self.assertEqual(len(session.posts), 2)

    def test_non_retryable_failure_is_counted(self):
        session = FakeSession(
            [FakeResponse({"responses": [{"id": "0", "status": 400}]})]
        )
        client = GraphClient("token", session=session)
        result = client.move_messages(
            ["message-a"],
            destination_folder_id="deleted",
        )
        self.assertEqual(result, {"moved": 0, "failed": 1})


if __name__ == "__main__":
    unittest.main()
