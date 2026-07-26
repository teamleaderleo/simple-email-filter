import unittest

from email_filter.graph import GraphClient


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

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
        self.posts.append((url, kwargs, dict(self.headers)))
        return self.responses.pop(0)


class GraphTokenRefreshTests(unittest.TestCase):
    def test_top_level_401_forces_refresh_and_retries_once(self):
        session = FakeSession(
            [
                FakeResponse(
                    {"error": {"code": "InvalidAuthenticationToken", "message": "expired"}},
                    status_code=401,
                ),
                FakeResponse({"responses": [{"id": "0", "status": 201}]}),
            ]
        )
        refreshes = []

        def refresh():
            refreshes.append(True)
            return "fresh-token"

        client = GraphClient(
            "stale-token",
            session=session,
            token_refresher=refresh,
        )
        outcomes = client.move_messages_detailed(
            ["message-a"],
            destination_folder_id="deleted",
        )

        self.assertEqual(outcomes, {"message-a": "moved"})
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(len(session.posts), 2)
        self.assertEqual(
            session.posts[0][2]["Authorization"],
            "Bearer stale-token",
        )
        self.assertEqual(
            session.posts[1][2]["Authorization"],
            "Bearer fresh-token",
        )

    def test_second_401_reports_graph_error_detail(self):
        session = FakeSession(
            [
                FakeResponse({}, status_code=401),
                FakeResponse(
                    {
                        "error": {
                            "code": "InvalidAuthenticationToken",
                            "message": "Access token validation failure.",
                        }
                    },
                    status_code=401,
                ),
            ]
        )
        client = GraphClient(
            "stale-token",
            session=session,
            token_refresher=lambda: "also-rejected",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "InvalidAuthenticationToken: Access token validation failure",
        ):
            client.move_messages_detailed(
                ["message-a"],
                destination_folder_id="deleted",
            )


if __name__ == "__main__":
    unittest.main()
