from __future__ import annotations

import argparse
import os

import boto3
import msal
from dotenv import load_dotenv

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]


def settings() -> tuple[str, str, str]:
    load_dotenv()
    client_id = os.environ.get("CLIENT_ID", "").strip()
    table_name = os.environ.get("TOKEN_TABLE_NAME", "email-filter-tokens")
    region = os.environ.get("AWS_REGION", "us-east-2")

    if not client_id:
        raise RuntimeError(
            "Missing CLIENT_ID. Run make doctor so it can recover the non-secret "
            "client ID from the deployed Lambda, or add CLIENT_ID to .env."
        )

    return client_id, table_name, region


def check_cached_token() -> bool:
    """Return whether the DynamoDB token cache can produce a Graph token."""
    load_dotenv()
    try:
        from email_filter.auth import acquire_access_token

        acquire_access_token()
    except Exception as exc:
        print(f"Cached Microsoft token is unavailable: {exc}")
        return False

    print("Cached Microsoft token is valid.")
    return True


def authenticate_interactively() -> None:
    client_id, table_name, region = settings()
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        client_id,
        authority=AUTHORITY,
        token_cache=cache,
    )

    print("Opening Microsoft login in your browser...")
    result = app.acquire_token_interactive(scopes=SCOPES)
    if "access_token" not in result:
        error = result.get("error_description") or result.get("error") or "unknown error"
        raise RuntimeError(f"Microsoft authentication failed: {error}")

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    table.put_item(Item={"id": "token", "cache": cache.serialize()})

    print("Microsoft authentication successful.")
    print(f"Saved the token cache to DynamoDB table {table_name} in {region}.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check or refresh the Microsoft Graph token cache."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check the cached token without opening a browser.",
    )
    args = parser.parse_args()

    if args.check:
        return 0 if check_cached_token() else 1

    try:
        authenticate_interactively()
    except Exception as exc:
        print(f"Microsoft setup failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
