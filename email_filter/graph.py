from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from .models import MailMessage

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_SUCCESS_STATUSES = {200, 201, 202, 204}


class GraphRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = status_code
        suffix = f": {detail}" if detail else ""
        super().__init__(
            f"Microsoft Graph request failed with HTTP {status_code}{suffix}"
        )


def _graph_timestamp(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    text = str(getattr(response, "text", "") or "").strip()
    return text[:500]


def _header_value(headers: Any, name: str) -> str:
    if not isinstance(headers, dict):
        return ""
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return ""


def _retry_delay(headers: Any, attempt: int) -> float:
    retry_after = _header_value(headers, "Retry-After").strip()
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return float(min(2 ** max(0, attempt - 1), 8))


def _default_token_refresher() -> str:
    from .auth import acquire_access_token

    return acquire_access_token(force_refresh=True)


class GraphClient:
    def __init__(
        self,
        access_token: str,
        session: requests.Session | None = None,
        token_refresher: Callable[[], str] | None = None,
    ):
        self.session = session or requests.Session()
        self.token_refresher = token_refresher or _default_token_refresher
        self._request_diagnostics: Counter[str] = Counter()
        self.last_move_diagnostics: dict[str, int] = {}
        self._set_access_token(access_token)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Prefer": 'IdType="ImmutableId"',
            }
        )

    def _set_access_token(self, access_token: str) -> None:
        self.access_token = access_token
        self.session.headers.update({"Authorization": f"Bearer {access_token}"})

    def _request(
        self,
        method: str,
        url: str,
        *,
        request_attempts: int = 4,
        **kwargs: Any,
    ) -> Any:
        sender = getattr(self.session, method)
        refreshed = False
        last_response = None

        for attempt in range(1, max(1, request_attempts) + 1):
            self._request_diagnostics["topLevelRequests"] += 1
            response = sender(url, **kwargs)
            last_response = response
            status = int(response.status_code)

            if status == 401 and not refreshed:
                self._request_diagnostics["topLevelHttp401"] += 1
                refreshed_token = self.token_refresher()
                self._set_access_token(refreshed_token)
                self._request_diagnostics["tokenRefreshes"] += 1
                refreshed = True
                continue

            if status == 429 or 500 <= status < 600:
                self._request_diagnostics[f"topLevelHttp{status}"] += 1
                if attempt < request_attempts:
                    self._request_diagnostics["topLevelRetries"] += 1
                    time.sleep(_retry_delay(getattr(response, "headers", {}), attempt))
                    continue

            if status >= 400:
                raise GraphRequestError(status, _graph_error_detail(response))
            return response

        if last_response is None:
            raise RuntimeError("Microsoft Graph request did not return a response")
        raise GraphRequestError(
            int(last_response.status_code),
            _graph_error_detail(last_response),
        )

    def get_well_known_folder_id(self, name: str) -> str:
        response = self._request(
            "get",
            f"{GRAPH_ROOT}/me/mailFolders/{quote(name, safe='')}",
            params={"$select": "id"},
            timeout=30,
        )
        return str(response.json()["id"])

    def iter_folder_message_pages(
        self,
        *,
        folder: str = "inbox",
        start_url: str | None = None,
        page_size: int = 500,
        max_pages: int | None = None,
        received_after: datetime | None = None,
        received_before: datetime | None = None,
    ) -> Iterator[tuple[list[MailMessage], str | None]]:
        """Yield complete Graph pages and their continuation URL.

        The continuation URL is safe to persist locally and pass back as start_url.
        Pages are never truncated, which keeps checkpoint resume semantics exact.
        """
        url: str | None = start_url or (
            f"{GRAPH_ROOT}/me/mailFolders/{quote(folder, safe='')}/messages"
        )
        params: dict[str, str | int] | None = None
        if start_url is None:
            params = {
                "$top": max(1, min(page_size, 999)),
                "$orderby": "receivedDateTime desc",
                "$select": (
                    "id,subject,from,receivedDateTime,parentFolderId,isRead,categories"
                ),
            }
            filters: list[str] = []
            if received_after is not None:
                filters.append(
                    f"receivedDateTime ge {_graph_timestamp(received_after)}"
                )
            if received_before is not None:
                filters.append(
                    f"receivedDateTime lt {_graph_timestamp(received_before)}"
                )
            if filters:
                params["$filter"] = " and ".join(filters)

        pages = 0
        while url:
            response = self._request("get", url, params=params, timeout=60)
            payload = response.json()
            messages = [
                MailMessage.from_graph(raw_message)
                for raw_message in payload.get("value", [])
            ]
            next_link = payload.get("@odata.nextLink")
            continuation = str(next_link) if next_link else None
            yield messages, continuation

            pages += 1
            if max_pages is not None and pages >= max(1, max_pages):
                return
            url = continuation
            params = None

    def iter_messages(
        self,
        *,
        received_after: datetime,
        page_size: int = 250,
        max_messages: int = 50_000,
    ) -> Iterator[MailMessage]:
        url: str | None = f"{GRAPH_ROOT}/me/messages"
        params: dict[str, str | int] | None = {
            "$top": max(1, min(page_size, 999)),
            "$orderby": "receivedDateTime desc",
            "$filter": f"receivedDateTime ge {_graph_timestamp(received_after)}",
            "$select": (
                "id,subject,from,receivedDateTime,parentFolderId,isRead,categories"
            ),
        }
        yielded = 0

        while url and yielded < max_messages:
            response = self._request("get", url, params=params, timeout=60)
            payload = response.json()

            for raw_message in payload.get("value", []):
                yield MailMessage.from_graph(raw_message)
                yielded += 1
                if yielded >= max_messages:
                    return

            next_link = payload.get("@odata.nextLink")
            url = str(next_link) if next_link else None
            params = None

    def _move_message_batch(
        self,
        message_ids: Sequence[str],
        *,
        destination_folder_id: str,
        max_attempts: int,
    ) -> tuple[dict[str, str], Counter[str]]:
        pending = list(message_ids)
        outcomes: dict[str, str] = {}
        diagnostics: Counter[str] = Counter()

        for attempt in range(1, max_attempts + 1):
            requests_payload = [
                {
                    "id": str(index),
                    "method": "POST",
                    "url": f"/me/messages/{quote(message_id, safe='')}/move",
                    "headers": {"Content-Type": "application/json"},
                    "body": {"destinationId": destination_folder_id},
                }
                for index, message_id in enumerate(pending)
            ]
            response = self._request(
                "post",
                f"{GRAPH_ROOT}/$batch",
                request_attempts=max_attempts,
                headers={"Content-Type": "application/json"},
                data=json.dumps({"requests": requests_payload}),
                timeout=90,
            )
            diagnostics["graphBatchResponses"] += 1
            subresponses = response.json().get("responses", [])

            retry_ids: list[str] = []
            retry_delays: list[float] = []
            refresh_needed = False
            by_request_id = {
                str(index): message_id
                for index, message_id in enumerate(pending)
            }
            seen_request_ids: set[str] = set()
            for subresponse in subresponses:
                request_id = str(subresponse.get("id"))
                message_id = by_request_id.get(request_id)
                if not message_id:
                    continue
                seen_request_ids.add(request_id)
                status = int(subresponse.get("status", 0))
                diagnostics[f"subresponseHttp{status}"] += 1
                if status in _SUCCESS_STATUSES:
                    outcomes[message_id] = "moved"
                elif status == 404:
                    outcomes[message_id] = "missing"
                elif status == 401:
                    refresh_needed = True
                    retry_ids.append(message_id)
                elif status == 429 or 500 <= status < 600:
                    retry_ids.append(message_id)
                    retry_delays.append(
                        _retry_delay(subresponse.get("headers") or {}, attempt)
                    )
                else:
                    outcomes[message_id] = "failed"

            missing_ids = [
                message_id
                for request_id, message_id in by_request_id.items()
                if request_id not in seen_request_ids
            ]
            if missing_ids:
                diagnostics["missingSubresponses"] += len(missing_ids)
                retry_ids.extend(missing_ids)

            retry_ids = list(dict.fromkeys(retry_ids))
            if not retry_ids:
                break
            if attempt == max_attempts:
                diagnostics["exhaustedRetryMessages"] += len(retry_ids)
                for message_id in retry_ids:
                    outcomes[message_id] = "failed"
                break

            diagnostics["retriedMessages"] += len(retry_ids)
            if refresh_needed:
                self._set_access_token(self.token_refresher())
                diagnostics["subresponseTokenRefreshes"] += 1
            pending = retry_ids
            retry_delays.append(float(min(2 ** (attempt - 1), 8)))
            time.sleep(max(retry_delays))

        return outcomes, diagnostics

    def _parallel_move_batch(
        self,
        message_ids: Sequence[str],
        *,
        destination_folder_id: str,
        max_attempts: int,
    ) -> tuple[dict[str, str], Counter[str]]:
        worker = GraphClient(
            self.access_token,
            token_refresher=self.token_refresher,
        )
        outcomes, diagnostics = worker._move_message_batch(
            message_ids,
            destination_folder_id=destination_folder_id,
            max_attempts=max_attempts,
        )
        diagnostics.update(worker._request_diagnostics)
        return outcomes, diagnostics

    def move_messages_detailed(
        self,
        message_ids: Sequence[str],
        *,
        destination_folder_id: str,
        batch_size: int = 20,
        max_attempts: int = 4,
        max_workers: int = 1,
    ) -> dict[str, str]:
        """Move messages in Graph JSON batches and return an outcome per id.

        A Graph JSON batch contains at most 20 move requests. ``max_workers`` controls
        how many independent batch requests may run concurrently and is bounded to
        eight to limit throttling pressure.
        """
        size = max(1, min(batch_size, 20))
        chunks = [
            list(message_ids[offset : offset + size])
            for offset in range(0, len(message_ids), size)
        ]
        workers = max(1, min(max_workers, 8, len(chunks) or 1))
        outcomes: dict[str, str] = {}
        diagnostics: Counter[str] = Counter()
        self._request_diagnostics = Counter()
        self.last_move_diagnostics = {}

        if workers == 1:
            for chunk in chunks:
                try:
                    chunk_outcomes, chunk_diagnostics = self._move_message_batch(
                        chunk,
                        destination_folder_id=destination_folder_id,
                        max_attempts=max_attempts,
                    )
                    outcomes.update(chunk_outcomes)
                    diagnostics.update(chunk_diagnostics)
                except GraphRequestError as exc:
                    outcomes.update({message_id: "failed" for message_id in chunk})
                    diagnostics["workerExceptions"] += 1
                    diagnostics[f"workerHttp{exc.status_code}Messages"] += len(chunk)
                except Exception as exc:
                    outcomes.update({message_id: "failed" for message_id in chunk})
                    diagnostics["workerExceptions"] += 1
                    diagnostics[f"worker{type(exc).__name__}Messages"] += len(chunk)
            diagnostics.update(self._request_diagnostics)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        self._parallel_move_batch,
                        chunk,
                        destination_folder_id=destination_folder_id,
                        max_attempts=max_attempts,
                    ): chunk
                    for chunk in chunks
                }
                for future in as_completed(futures):
                    chunk = futures[future]
                    try:
                        chunk_outcomes, chunk_diagnostics = future.result()
                        outcomes.update(chunk_outcomes)
                        diagnostics.update(chunk_diagnostics)
                    except GraphRequestError as exc:
                        outcomes.update({message_id: "failed" for message_id in chunk})
                        diagnostics["workerExceptions"] += 1
                        diagnostics[f"workerHttp{exc.status_code}Messages"] += len(chunk)
                    except Exception as exc:
                        outcomes.update({message_id: "failed" for message_id in chunk})
                        diagnostics["workerExceptions"] += 1
                        diagnostics[f"worker{type(exc).__name__}Messages"] += len(chunk)

        diagnostics["movedMessages"] = sum(
            outcome == "moved" for outcome in outcomes.values()
        )
        diagnostics["missingMessages"] = sum(
            outcome == "missing" for outcome in outcomes.values()
        )
        diagnostics["failedMessages"] = sum(
            outcome == "failed" for outcome in outcomes.values()
        )
        self.last_move_diagnostics = {
            key: int(value) for key, value in sorted(diagnostics.items()) if value
        }
        return outcomes

    def move_messages(
        self,
        message_ids: Sequence[str],
        *,
        destination_folder_id: str,
        batch_size: int = 20,
        max_attempts: int = 4,
        max_workers: int = 1,
    ) -> dict[str, int]:
        """Move messages in Graph JSON batches and report aggregate results."""
        outcomes = self.move_messages_detailed(
            message_ids,
            destination_folder_id=destination_folder_id,
            batch_size=batch_size,
            max_attempts=max_attempts,
            max_workers=max_workers,
        )
        moved = sum(outcome == "moved" for outcome in outcomes.values())
        return {"moved": moved, "failed": len(outcomes) - moved}
