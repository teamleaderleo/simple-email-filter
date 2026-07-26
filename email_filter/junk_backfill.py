from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote


STATE_VERSION = 1
DELETE_CONFIRMATION = "DELETE_JUNK_WINDOW"
RESET_CONFIRMATION = "RESET_JUNK_BACKFILL"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_COMPLETED_OUTCOMES = {"deleted", "missing", "not_in_junk", "already_processed"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a UTC offset or Z suffix")
    return parsed.astimezone(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _private_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _private_file(temporary)
    temporary.replace(path)
    _private_file(path)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object in {path}")
    return payload


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    _private_dir(path.parent)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    _private_file(path)
    return count


def _graph_error_detail(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or "").strip()
            if code and message:
                return f"{code}: {message}"
            return code or message
    return str(getattr(response, "text", "") or "").strip()[:500]


def graph_request(
    session: Any,
    method: str,
    url: str,
    *,
    token_refresher: Callable[[], str],
    max_attempts: int = 4,
    allowed_statuses: set[int] | None = None,
    **kwargs: Any,
) -> Any:
    sender = getattr(session, method.lower())
    refreshed = False
    last_response = None
    for attempt in range(1, max_attempts + 1):
        response = sender(url, **kwargs)
        last_response = response
        if response.status_code == 401 and not refreshed:
            token = token_refresher()
            session.headers.update({"Authorization": f"Bearer {token}"})
            refreshed = True
            continue
        if allowed_statuses and response.status_code in allowed_statuses:
            return response
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt < max_attempts:
                retry_after = getattr(response, "headers", {}).get("Retry-After")
                delay = (
                    int(retry_after)
                    if retry_after and str(retry_after).isdigit()
                    else 2 ** (attempt - 1)
                )
                time.sleep(min(delay, 8))
                continue
        if response.status_code >= 400:
            detail = _graph_error_detail(response)
            raise RuntimeError(
                f"Microsoft Graph request failed with HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )
        return response

    status = getattr(last_response, "status_code", 0)
    detail = _graph_error_detail(last_response) if last_response is not None else ""
    raise RuntimeError(
        f"Microsoft Graph request failed after {max_attempts} attempts with HTTP {status}"
        + (f": {detail}" if detail else "")
    )


class JunkBackfillStore:
    def __init__(self, state_dir: str | Path):
        self.root = Path(state_dir)
        self.plan_path = self.root / "plan.json"
        self.summary_path = self.root / "summary.json"
        self.apply_results_path = self.root / "apply-results.jsonl"
        _private_dir(self.root)

    def write_plan(self, plan: dict[str, Any]) -> None:
        if self.apply_results_path.exists():
            raise RuntimeError(
                "Apply has already started for this Junk backfill state. Finish it or "
                "reset the local state before replacing the plan."
            )
        _write_json(self.plan_path, plan)

    def plan(self) -> dict[str, Any]:
        if not self.plan_path.exists():
            raise RuntimeError("No Junk backfill plan exists. Run the audit first.")
        return _read_json(self.plan_path)

    def write_summary(self, summary: dict[str, Any]) -> None:
        _write_json(self.summary_path, summary)

    def summary(self) -> dict[str, Any]:
        if not self.summary_path.exists():
            raise RuntimeError("No Junk backfill summary exists. Run the audit first.")
        return _read_json(self.summary_path)

    def outcomes(self) -> dict[str, str]:
        outcomes: dict[str, str] = {}
        if not self.apply_results_path.exists():
            return outcomes
        with self.apply_results_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                message_id = str(row.get("messageId") or "")
                outcome = str(row.get("outcome") or "")
                if message_id and outcome:
                    outcomes[message_id] = outcome
        return outcomes

    def append_outcome(self, message_id: str, outcome: str) -> None:
        _append_jsonl(
            self.apply_results_path,
            [
                {
                    "messageId": message_id,
                    "outcome": outcome,
                    "recordedAt": iso_timestamp(_utc_now()),
                }
            ],
        )

    def reset(self, confirmation: str) -> None:
        if confirmation != RESET_CONFIRMATION:
            raise RuntimeError(f"Reset requires confirmation {RESET_CONFIRMATION}")
        if self.root.exists():
            shutil.rmtree(self.root)


def get_junk_folder_id(
    session: Any,
    *,
    token_refresher: Callable[[], str],
) -> str:
    response = graph_request(
        session,
        "get",
        f"{GRAPH_ROOT}/me/mailFolders/junkemail",
        token_refresher=token_refresher,
        params={"$select": "id"},
        timeout=30,
    )
    folder_id = str(response.json().get("id") or "")
    if not folder_id:
        raise RuntimeError("Microsoft Graph did not return a Junk Email folder id")
    return folder_id


def fetch_junk_window(
    session: Any,
    junk_folder_id: str,
    *,
    start: datetime,
    end: datetime,
    token_refresher: Callable[[], str],
    max_messages: int = 500,
    page_size: int = 100,
) -> tuple[list[dict[str, Any]], bool]:
    if end <= start:
        raise ValueError("Backfill end must be later than start")
    if not 1 <= max_messages <= 5000:
        raise ValueError("max_messages must be between 1 and 5000")

    url: str | None = (
        f"{GRAPH_ROOT}/me/mailFolders/{quote(junk_folder_id, safe='')}/messages"
    )
    params: dict[str, Any] | None = {
        "$top": max(1, min(page_size, 999)),
        "$orderby": "receivedDateTime asc",
        "$filter": (
            f"receivedDateTime ge {iso_timestamp(start)} and "
            f"receivedDateTime lt {iso_timestamp(end)}"
        ),
        "$select": (
            "id,subject,from,bodyPreview,receivedDateTime,parentFolderId"
        ),
    }
    messages: list[dict[str, Any]] = []
    truncated = False

    while url:
        response = graph_request(
            session,
            "get",
            url,
            token_refresher=token_refresher,
            params=params,
            timeout=60,
        )
        payload = response.json()
        for raw in payload.get("value", []):
            if len(messages) >= max_messages:
                truncated = True
                return messages, truncated
            if isinstance(raw, dict):
                messages.append(raw)
        next_link = payload.get("@odata.nextLink")
        url = str(next_link) if next_link else None
        params = None

    return messages, truncated


def build_audit_plan(
    messages: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
    model: str,
    classifier: Callable[[dict[str, str]], tuple[bool, str]],
    already_processed: Callable[[str], bool],
    truncated: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    decisions: Counter[str] = Counter()
    sender_counts: Counter[str] = Counter()
    planned: list[dict[str, Any]] = []
    skipped_processed = 0
    invalid = 0

    for raw in messages:
        message_id = str(raw.get("id") or "")
        if not message_id:
            invalid += 1
            continue
        if already_processed(message_id):
            skipped_processed += 1
            continue

        sender = str(
            ((raw.get("from") or {}).get("emailAddress") or {}).get("address")
            or ""
        ).lower()
        email = {
            "id": message_id,
            "sender": sender,
            "subject": str(raw.get("subject") or ""),
            "preview": str(raw.get("bodyPreview") or ""),
            "received": str(raw.get("receivedDateTime") or ""),
        }
        should_delete, decision = classifier(email)
        decisions[str(decision)] += 1
        sender_counts[sender] += 1
        record = {
            "messageId": message_id,
            "receivedAt": email["received"],
            "sender": sender,
            "decision": str(decision),
            "shouldDelete": bool(should_delete),
        }
        planned.append(record)
        if progress is not None:
            progress(record)

    plan = {
        "version": STATE_VERSION,
        "generatedAt": iso_timestamp(_utc_now()),
        "start": iso_timestamp(start),
        "end": iso_timestamp(end),
        "model": model,
        "privacy": {
            "messageIdsIncluded": True,
            "senderAddressesIncluded": True,
            "subjectsIncluded": False,
            "previewsIncluded": False,
            "bodiesIncluded": False,
            "attachmentsIncluded": False,
        },
        "messages": planned,
    }
    summary = {
        "version": STATE_VERSION,
        "generatedAt": plan["generatedAt"],
        "start": plan["start"],
        "end": plan["end"],
        "model": model,
        "fetched": len(messages),
        "classified": len(planned),
        "deleteCandidates": sum(item["shouldDelete"] for item in planned),
        "kept": sum(not item["shouldDelete"] for item in planned),
        "alreadyProcessed": skipped_processed,
        "invalid": invalid,
        "truncated": truncated,
        "decisionCounts": dict(sorted(decisions.items())),
        "topSenders": [
            {"value": sender, "count": count}
            for sender, count in sender_counts.most_common(20)
            if sender
        ],
    }
    return plan, summary


def get_message_record(
    session: Any,
    message_id: str,
    *,
    token_refresher: Callable[[], str],
) -> dict[str, Any] | None:
    response = graph_request(
        session,
        "get",
        f"{GRAPH_ROOT}/me/messages/{quote(message_id, safe='')}",
        token_refresher=token_refresher,
        allowed_statuses={404},
        params={"$select": "id,parentFolderId"},
        timeout=30,
    )
    if response.status_code == 404:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def delete_message(
    session: Any,
    message_id: str,
    *,
    token_refresher: Callable[[], str],
) -> str:
    response = graph_request(
        session,
        "delete",
        f"{GRAPH_ROOT}/me/messages/{quote(message_id, safe='')}",
        token_refresher=token_refresher,
        allowed_statuses={404},
        timeout=30,
    )
    if response.status_code == 404:
        return "missing"
    return "deleted"


def plan_status(store: JunkBackfillStore) -> dict[str, Any]:
    plan = store.plan()
    outcomes = store.outcomes()
    delete_items = [
        item for item in plan.get("messages", []) if item.get("shouldDelete") is True
    ]
    counts: Counter[str] = Counter()
    for item in delete_items:
        outcome = outcomes.get(str(item.get("messageId") or ""), "pending")
        counts[outcome] += 1
    completed = sum(counts[outcome] for outcome in _COMPLETED_OUTCOMES)
    return {
        "start": plan.get("start"),
        "end": plan.get("end"),
        "model": plan.get("model"),
        "deleteCandidates": len(delete_items),
        "pending": max(0, len(delete_items) - completed),
        "outcomes": dict(sorted(counts.items())),
    }


def apply_plan(
    store: JunkBackfillStore,
    *,
    confirmation: str,
    junk_folder_id: str,
    get_message: Callable[[str], dict[str, Any] | None],
    delete: Callable[[str], str],
    already_processed: Callable[[str], bool],
    mark_processed: Callable[[str, str], None],
    limit: int = 250,
) -> dict[str, Any]:
    if confirmation != DELETE_CONFIRMATION:
        raise RuntimeError(f"Apply requires confirmation {DELETE_CONFIRMATION}")
    if not 1 <= limit <= 1000:
        raise ValueError("apply limit must be between 1 and 1000")

    plan = store.plan()
    outcomes = store.outcomes()
    pending = [
        item
        for item in plan.get("messages", [])
        if item.get("shouldDelete") is True
        and outcomes.get(str(item.get("messageId") or "")) not in _COMPLETED_OUTCOMES
    ][:limit]

    current: Counter[str] = Counter()
    for item in pending:
        message_id = str(item.get("messageId") or "")
        if not message_id:
            continue
        if already_processed(message_id):
            outcome = "already_processed"
        else:
            message = get_message(message_id)
            if message is None:
                outcome = "missing"
            elif str(message.get("parentFolderId") or "") != junk_folder_id:
                outcome = "not_in_junk"
            else:
                try:
                    outcome = delete(message_id)
                except Exception:
                    outcome = "failed"
                if outcome == "deleted":
                    mark_processed(message_id, "backfill_deleted")
        store.append_outcome(message_id, outcome)
        current[outcome] += 1

    status = plan_status(store)
    return {
        "requested": len(pending),
        "deleted": current["deleted"],
        "missing": current["missing"],
        "notInJunk": current["not_in_junk"],
        "alreadyProcessed": current["already_processed"],
        "failed": current["failed"],
        "remaining": status["pending"],
        "start": status["start"],
        "end": status["end"],
        "model": status["model"],
    }
