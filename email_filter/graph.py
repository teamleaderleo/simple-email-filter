from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from .models import MailMessage

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_SUCCESS_STATUSES = {200, 201, 202, 204}


def _graph_timestamp(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class GraphClient:
    def __init__(self, access_token: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Prefer": 'IdType="ImmutableId"',
            }
        )

    def get_well_known_folder_id(self, name: str) -> str:
        response = self.session.get(
            f"{GRAPH_ROOT}/me/mailFolders/{quote(name, safe='')}",
            params={"$select": "id"},
            timeout=30,
        )
        response.raise_for_status()
        return str(response.json()["id"])

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
            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()
            payload = response.json()

            for raw_message in payload.get("value", []):
                yield MailMessage.from_graph(raw_message)
                yielded += 1
                if yielded >= max_messages:
                    return

            next_link = payload.get("@odata.nextLink")
            url = str(next_link) if next_link else None
            params = None

    def move_messages(
        self,
        message_ids: Sequence[str],
        *,
        destination_folder_id: str,
        batch_size: int = 20,
        max_attempts: int = 4,
    ) -> dict[str, int]:
        """Move messages in Graph JSON batches and report every result."""
        result = {"moved": 0, "failed": 0}
        size = max(1, min(batch_size, 20))

        for offset in range(0, len(message_ids), size):
            pending = list(message_ids[offset : offset + size])

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
                response = self.session.post(
                    f"{GRAPH_ROOT}/$batch",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps({"requests": requests_payload}),
                    timeout=90,
                )
                response.raise_for_status()
                subresponses = response.json().get("responses", [])

                retry_ids: list[str] = []
                by_request_id = {
                    str(i): message_id for i, message_id in enumerate(pending)
                }
                seen_request_ids: set[str] = set()
                for subresponse in subresponses:
                    request_id = str(subresponse.get("id"))
                    message_id = by_request_id.get(request_id)
                    if not message_id:
                        continue
                    seen_request_ids.add(request_id)
                    status = int(subresponse.get("status", 0))
                    if status in _SUCCESS_STATUSES:
                        result["moved"] += 1
                    elif status == 429 or 500 <= status < 600:
                        retry_ids.append(message_id)
                    else:
                        result["failed"] += 1

                retry_ids.extend(
                    message_id
                    for request_id, message_id in by_request_id.items()
                    if request_id not in seen_request_ids
                )

                if not retry_ids:
                    break
                if attempt == max_attempts:
                    result["failed"] += len(retry_ids)
                    break

                pending = retry_ids
                time.sleep(min(2 ** (attempt - 1), 8))

        return result
