import json
import tempfile
import unittest
from datetime import timezone

from email_filter.junk_backfill import (
    DELETE_CONFIRMATION,
    JunkBackfillStore,
    apply_plan,
    build_audit_plan,
    graph_request,
    parse_timestamp,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {"Authorization": "Bearer old"}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self.responses.pop(0)


class TimestampTests(unittest.TestCase):
    def test_timestamp_requires_offset_and_normalizes_to_utc(self):
        parsed = parse_timestamp("2026-07-25T08:00:00-07:00")
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.hour, 15)
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            parse_timestamp("2026-07-25T08:00:00")


class GraphRequestTests(unittest.TestCase):
    def test_401_forces_one_refresh_and_retries_same_request(self):
        session = FakeSession(
            [
                FakeResponse(401, {"error": {"code": "InvalidAuthenticationToken"}}),
                FakeResponse(200, {"value": []}),
            ]
        )
        refreshes = []
        response = graph_request(
            session,
            "get",
            "https://graph.example/messages",
            token_refresher=lambda: refreshes.append(True) or "new",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(refreshes, [True])
        self.assertEqual(session.headers["Authorization"], "Bearer new")


class AuditPlanTests(unittest.TestCase):
    def test_plan_is_private_minimized_and_skips_processed_messages(self):
        messages = [
            {
                "id": "delete-me",
                "subject": "Casino reward",
                "bodyPreview": "Secret preview",
                "receivedDateTime": "2026-07-25T15:10:00Z",
                "from": {"emailAddress": {"address": "spam@example.com"}},
            },
            {
                "id": "already-done",
                "subject": "Ignored",
                "bodyPreview": "Ignored preview",
                "receivedDateTime": "2026-07-25T15:20:00Z",
                "from": {"emailAddress": {"address": "old@example.com"}},
            },
        ]
        plan, summary = build_audit_plan(
            messages,
            start=parse_timestamp("2026-07-25T08:00:00-07:00"),
            end=parse_timestamp("2026-07-25T11:00:00-07:00"),
            model="gemma-test",
            classifier=lambda email: (True, "MODEL_DELETE"),
            already_processed=lambda message_id: message_id == "already-done",
        )
        self.assertEqual(summary["deleteCandidates"], 1)
        self.assertEqual(summary["alreadyProcessed"], 1)
        self.assertEqual(len(plan["messages"]), 1)
        serialized = json.dumps(plan)
        self.assertNotIn("Casino reward", serialized)
        self.assertNotIn("Secret preview", serialized)
        self.assertIn("delete-me", serialized)
        self.assertTrue(plan["privacy"]["subjectsIncluded"] is False)


class ApplyPlanTests(unittest.TestCase):
    def _store(self, directory):
        store = JunkBackfillStore(directory)
        store.write_plan(
            {
                "version": 1,
                "start": "2026-07-25T15:00:00Z",
                "end": "2026-07-25T18:00:00Z",
                "model": "gemma-test",
                "truncated": False,
                "messages": [
                    {"messageId": "delete-a", "shouldDelete": True},
                    {"messageId": "keep-a", "shouldDelete": False},
                    {"messageId": "delete-b", "shouldDelete": True},
                ],
            }
        )
        return store

    def test_apply_deletes_only_saved_delete_decisions_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            deleted = []
            marked = []
            first = apply_plan(
                store,
                confirmation=DELETE_CONFIRMATION,
                junk_folder_id="junk-id",
                get_message=lambda message_id: {
                    "id": message_id,
                    "parentFolderId": "junk-id",
                },
                delete=lambda message_id: deleted.append(message_id) or "deleted",
                already_processed=lambda message_id: False,
                mark_processed=lambda message_id, outcome: marked.append(
                    (message_id, outcome)
                ),
                limit=1,
            )
            self.assertEqual(first["deleted"], 1)
            self.assertEqual(first["remaining"], 1)
            self.assertEqual(deleted, ["delete-a"])
            self.assertNotIn("keep-a", deleted)

            second = apply_plan(
                store,
                confirmation=DELETE_CONFIRMATION,
                junk_folder_id="junk-id",
                get_message=lambda message_id: {
                    "id": message_id,
                    "parentFolderId": "junk-id",
                },
                delete=lambda message_id: deleted.append(message_id) or "deleted",
                already_processed=lambda message_id: False,
                mark_processed=lambda message_id, outcome: marked.append(
                    (message_id, outcome)
                ),
                limit=1,
            )
            self.assertEqual(second["remaining"], 0)
            self.assertEqual(deleted, ["delete-a", "delete-b"])
            self.assertEqual(
                marked,
                [
                    ("delete-a", "backfill_deleted"),
                    ("delete-b", "backfill_deleted"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
