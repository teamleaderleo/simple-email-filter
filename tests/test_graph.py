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
        self.gets = []
        self.posts = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.responses.pop(0)


class FakeParallelGraphClient(GraphClient):
    def __init__(self, *, failing_chunk_first_id=None):
        self.access_token = "token"
        self.token_refresher = lambda: "refreshed"
        self.parallel_chunks = []
        self.failing_chunk_first_id = failing_chunk_first_id

    def _parallel_move_batch(
        self,
        message_ids,
        *,
        destination_folder_id,
        max_attempts,
    ):
        chunk = list(message_ids)
        self.parallel_chunks.append(chunk)
        if chunk and chunk[0] == self.failing_chunk_first_id:
            raise RuntimeError("simulated batch failure")
        return {message_id: "moved" for message_id in chunk}


class GraphPageTests(unittest.TestCase):
    def test_folder_pages_return_continuation_without_truncation(self):
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "value": [
                            {
                                "id": "message-a",
                                "subject": "Hello",
                                "from": {"emailAddress": {"address": "a@example.com"}},
                                "receivedDateTime": "2026-01-01T00:00:00Z",
                                "parentFolderId": "inbox-id",
                                "isRead": True,
                                "categories": [],
                            }
                        ],
                        "@odata.nextLink": "https://graph.example/next",
                    }
                )
            ]
        )
        client = GraphClient("token", session=session)
        pages = list(
            client.iter_folder_message_pages(
                folder="inbox",
                page_size=999,
                max_pages=1,
            )
        )
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0][0][0].id, "message-a")
        self.assertEqual(pages[0][1], "https://graph.example/next")
        self.assertIn("/mailFolders/inbox/messages", session.gets[0][0])
        self.assertEqual(session.gets[0][1]["params"]["$top"], 999)

    def test_resume_url_is_used_without_rebuilding_query_parameters(self):
        session = FakeSession([FakeResponse({"value": []})])
        client = GraphClient("token", session=session)
        list(
            client.iter_folder_message_pages(
                folder="ignored",
                start_url="https://graph.example/resume",
            )
        )
        self.assertEqual(session.gets[0][0], "https://graph.example/resume")
        self.assertIsNone(session.gets[0][1]["params"])


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

    def test_detailed_moves_distinguish_missing_and_failed(self):
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "responses": [
                            {"id": "0", "status": 201},
                            {"id": "1", "status": 404},
                            {"id": "2", "status": 400},
                        ]
                    }
                )
            ]
        )
        client = GraphClient("token", session=session)
        outcomes = client.move_messages_detailed(
            ["moved", "missing", "failed"],
            destination_folder_id="deleted",
        )
        self.assertEqual(
            outcomes,
            {"moved": "moved", "missing": "missing", "failed": "failed"},
        )

    def test_parallel_workers_split_graph_batches_at_twenty(self):
        client = FakeParallelGraphClient()
        message_ids = [f"message-{index}" for index in range(45)]
        outcomes = client.move_messages_detailed(
            message_ids,
            destination_folder_id="deleted",
            max_workers=4,
        )
        self.assertEqual(len(outcomes), 45)
        self.assertEqual(
            sorted(len(chunk) for chunk in client.parallel_chunks),
            [5, 20, 20],
        )
        self.assertTrue(all(outcome == "moved" for outcome in outcomes.values()))

    def test_parallel_worker_failure_is_bounded_to_its_batch(self):
        client = FakeParallelGraphClient(failing_chunk_first_id="message-20")
        message_ids = [f"message-{index}" for index in range(45)]
        outcomes = client.move_messages_detailed(
            message_ids,
            destination_folder_id="deleted",
            max_workers=4,
        )
        failed = sorted(
            message_id
            for message_id, outcome in outcomes.items()
            if outcome == "failed"
        )
        self.assertEqual(
            failed,
            sorted(f"message-{index}" for index in range(20, 40)),
        )
        self.assertEqual(sum(outcome == "moved" for outcome in outcomes.values()), 25)


if __name__ == "__main__":
    unittest.main()
