import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from email_filter.apply_stages import STAGES, merge_policy_selection, resolve_stage
from email_filter.historical import APPLY_CONFIRMATION, HistoricalMailboxStore
from email_filter.models import RetentionPlanItem
from email_filter.policy import load_policies
from email_filter.staged_apply import apply_plan_selection, plan_status

UTC = timezone.utc


class FakeApplyClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.moved_batches = []

    def get_well_known_folder_id(self, name):
        self.folder_name = name
        return "deleted-id"

    def move_messages_detailed(self, message_ids, **kwargs):
        self.moved_batches.append((list(message_ids), kwargs))
        return self.outcomes.pop(0)


def plan_item(message_id: str, policy_id: str) -> RetentionPlanItem:
    return RetentionPlanItem(
        message_id=message_id,
        policy_id=policy_id,
        received_at=datetime(2020, 1, 1, tzinfo=UTC),
        action="deleteditems",
        reason="old",
        sender="sender@example.com",
        subject="Subject",
    )


class ApplyStageDefinitionTests(unittest.TestCase):
    def test_named_stage_policy_ids_exist_in_reviewed_policy(self):
        available = {
            policy.id for policy in load_policies(Path("policies/personal.example.json"))
        }
        for stage, policy_ids in STAGES.items():
            if policy_ids is None:
                continue
            with self.subTest(stage=stage):
                self.assertTrue(set(policy_ids).issubset(available))

    def test_explicit_policy_selection_and_stage_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "either an apply stage"):
            merge_policy_selection(stage="bulk", policy_ids=["job-alerts"])
        self.assertEqual(resolve_stage("all"), None)
        self.assertIn("marketing-promotions", resolve_stage("bulk"))


class StagedApplyTests(unittest.TestCase):
    def _prepared_store(self, directory: str) -> HistoricalMailboxStore:
        store = HistoricalMailboxStore(directory)
        store.write_summary(
            {"scanComplete": True, "policyPath": "policies/personal.json"}
        )
        store.write_plan(
            [
                plan_item("bulk-a", "marketing-promotions"),
                plan_item("bulk-b", "marketing-promotions"),
                plan_item("news-a", "technical-and-cultural-newsletters"),
            ]
        )
        return store

    def test_plan_status_reports_selection_and_whole_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._prepared_store(directory)
            store.append_apply_outcomes({"bulk-a": "moved", "bulk-b": "failed"})
            status = plan_status(
                store,
                policy_ids={"marketing-promotions"},
            )
            self.assertEqual(status["selection"]["total"], 2)
            self.assertEqual(status["selection"]["pending"], 1)
            self.assertEqual(status["selection"]["moved"], 1)
            self.assertEqual(status["selection"]["failedLastAttempt"], 1)
            self.assertEqual(status["allPlan"]["pending"], 2)

    def test_apply_moves_only_selected_policies_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._prepared_store(directory)
            client = FakeApplyClient(
                [
                    {"bulk-a": "moved"},
                    {"bulk-b": "moved"},
                ]
            )
            selected = {"marketing-promotions"}
            first = apply_plan_selection(
                client,
                store,
                confirmation=APPLY_CONFIRMATION,
                limit=1,
                policy_ids=selected,
            )
            self.assertEqual(client.moved_batches[0][0], ["bulk-a"])
            self.assertEqual(first["remaining"], 1)
            self.assertEqual(first["remainingAll"], 2)

            second = apply_plan_selection(
                client,
                store,
                confirmation=APPLY_CONFIRMATION,
                limit=5,
                policy_ids=selected,
            )
            self.assertEqual(client.moved_batches[1][0], ["bulk-b"])
            self.assertEqual(second["remaining"], 0)
            self.assertEqual(second["remainingAll"], 1)

    def test_unknown_policy_is_rejected_before_graph_move(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._prepared_store(directory)
            with self.assertRaisesRegex(RuntimeError, "Unknown policy ids"):
                apply_plan_selection(
                    FakeApplyClient([]),
                    store,
                    confirmation=APPLY_CONFIRMATION,
                    policy_ids={"not-a-policy"},
                )


if __name__ == "__main__":
    unittest.main()
