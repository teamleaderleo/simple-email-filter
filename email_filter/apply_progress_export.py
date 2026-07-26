from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .apply_stages import resolve_stage
from .historical import HistoricalMailboxStore
from .staged_apply import plan_status

_PROGRESS_VERSION = 1
_STAGE_ORDER = ("bulk", "newsletters", "operations", "all")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _private_file(path)


def _known_policy_ids(store: HistoricalMailboxStore) -> set[str]:
    planned = {
        str(item.get("policyId") or "")
        for item in store.load_plan()
        if item.get("policyId")
    }
    summary_policies = set((store.summary().get("policies") or {}).keys())
    return planned.union(summary_policies)


def build_apply_progress(store: HistoricalMailboxStore) -> dict[str, Any]:
    """Build aggregate apply progress without exposing message-level identifiers."""
    known = _known_policy_ids(store)
    stages: dict[str, Any] = {}

    for stage in _STAGE_ORDER:
        requested = resolve_stage(stage)
        policy_ids = None if requested is None else requested.intersection(known)
        status = plan_status(store, policy_ids=policy_ids)
        stages[stage] = status["selection"]

    return {
        "version": _PROGRESS_VERSION,
        "generatedAt": _iso_now(),
        "applyStarted": store.apply_results_path.exists(),
        "allPlan": stages["all"],
        "stages": stages,
        "privacy": {
            "messageIdsIncluded": False,
            "subjectsIncluded": False,
            "sendersIncluded": False,
            "aggregateCountsOnly": True,
        },
    }


def _progress_rows(progress: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage in _STAGE_ORDER:
        selection = (progress.get("stages") or {}).get(stage) or {}
        rows.append(
            {
                "rowType": "stage",
                "stage": stage,
                "policyId": "",
                "total": selection.get("total", 0),
                "pending": selection.get("pending", 0),
                "moved": selection.get("moved", 0),
                "missing": selection.get("missing", 0),
                "failedLastAttempt": selection.get("failedLastAttempt", 0),
            }
        )
        for policy_id, counts in sorted((selection.get("byPolicy") or {}).items()):
            rows.append(
                {
                    "rowType": "policy",
                    "stage": stage,
                    "policyId": policy_id,
                    "total": counts.get("total", 0),
                    "pending": counts.get("pending", 0),
                    "moved": counts.get("moved", 0),
                    "missing": counts.get("missing", 0),
                    "failedLastAttempt": counts.get("failedLastAttempt", 0),
                }
            )
    return rows


def write_apply_progress(
    output_dir: str | Path,
    progress: dict[str, Any],
) -> dict[str, str]:
    """Write progress sidecars and enrich the existing aggregate export package."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "apply-progress.json"
    csv_path = destination / "apply-progress.csv"

    _write_json(json_path, progress)
    fields = (
        "rowType",
        "stage",
        "policyId",
        "total",
        "pending",
        "moved",
        "missing",
        "failedLastAttempt",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(_progress_rows(progress))
    _private_file(csv_path)

    summary_path = destination / "mailbox-summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["applyProgress"] = progress
        _write_json(summary_path, summary)

    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.setdefault("files", {})
        files["applyProgressJson"] = json_path.name
        files["applyProgressCsv"] = csv_path.name
        manifest["applyProgress"] = {
            "applyStarted": bool(progress.get("applyStarted")),
            "pending": int((progress.get("allPlan") or {}).get("pending", 0)),
            "moved": int((progress.get("allPlan") or {}).get("moved", 0)),
            "failedLastAttempt": int(
                (progress.get("allPlan") or {}).get("failedLastAttempt", 0)
            ),
        }
        _write_json(manifest_path, manifest)

    readme_path = destination / "README.txt"
    if readme_path.exists():
        existing = readme_path.read_text(encoding="utf-8").rstrip()
        all_plan = progress.get("allPlan") or {}
        section = f"""

Apply progress
--------------
Apply started: {bool(progress.get("applyStarted"))}
Moved: {int(all_plan.get("moved", 0))}
Pending: {int(all_plan.get("pending", 0))}
Missing: {int(all_plan.get("missing", 0))}
Failed on latest attempt: {int(all_plan.get("failedLastAttempt", 0))}

apply-progress.json contains nested aggregate stage and policy counts.
apply-progress.csv contains the same counts in a sortable flat format.
Neither file contains message IDs, senders, subjects, bodies or previews.
"""
        readme_path.write_text(existing + section, encoding="utf-8")
        _private_file(readme_path)

    return {
        "applyProgressJson": json_path.name,
        "applyProgressCsv": csv_path.name,
    }
