from __future__ import annotations

import os
from typing import Any

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]


def acquire_access_token() -> str:
    """Acquire a Graph token from an override or the existing DynamoDB cache."""
    override = os.environ.get("MICROSOFT_GRAPH_ACCESS_TOKEN")
    if override:
        return override

    client_id = os.environ.get("CLIENT_ID")
    if not client_id:
        raise RuntimeError("CLIENT_ID is required")

    import boto3
    import msal

    table_name = os.environ.get("TOKEN_TABLE_NAME", "email-filter-tokens")
    region = os.environ.get("AWS_REGION", "us-east-2")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    cache = msal.SerializableTokenCache()
    response: dict[str, Any] = table.get_item(Key={"id": "token"})
    cached = (response.get("Item") or {}).get("cache")
    if cached:
        cache.deserialize(cached)

    app = msal.PublicClientApplication(
        client_id,
        authority=AUTHORITY,
        token_cache=cache,
    )
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(
            "No cached Microsoft account. Run setup_token_interactive.py locally."
        )

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(
            "Microsoft token refresh failed. Run setup_token_interactive.py locally."
        )

    if cache.has_state_changed:
        table.put_item(Item={"id": "token", "cache": cache.serialize()})

    return str(result["access_token"])
