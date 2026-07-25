import json
import tempfile
import unittest
from pathlib import Path

from email_filter.policy import load_policies


class PolicyLoadingTests(unittest.TestCase):
    def _write(self, payload):
        temp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(payload, temp)
        temp.close()
        return Path(temp.name)

    def test_sorts_by_priority(self):
        path = self._write(
            {
                "policies": [
                    {
                        "id": "late",
                        "priority": 50,
                        "match": {"senders": ["x@example.com"]},
                        "retention": {"mode": "forever"},
                    },
                    {
                        "id": "early",
                        "priority": 10,
                        "match": {"senders": ["x@example.com"]},
                        "retention": {"mode": "days", "days": 30},
                    },
                ]
            }
        )
        self.assertEqual([p.id for p in load_policies(path)], ["early", "late"])

    def test_rejects_unbounded_policy(self):
        path = self._write(
            {
                "policies": [
                    {
                        "id": "dangerous",
                        "match": {},
                        "retention": {"mode": "days", "days": 1},
                    }
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "must constrain"):
            load_policies(path)

    def test_requires_keep_latest(self):
        path = self._write(
            {
                "policies": [
                    {
                        "id": "rolling",
                        "match": {"senders": ["x@example.com"]},
                        "retention": {"mode": "latest"},
                    }
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "keepLatest"):
            load_policies(path)


if __name__ == "__main__":
    unittest.main()
