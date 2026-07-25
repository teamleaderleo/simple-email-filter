"""
One-time setup script to create Microsoft Graph subscription for webhook notifications.
Run this after deploying the webhook Lambda and API Gateway.
"""

import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import boto3
import msal
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ.get("CLIENT_ID")
TABLE_NAME = os.environ.get("TOKEN_TABLE_NAME", "email-filter-tokens")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")


dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(TABLE_NAME)


def get_token_cache():
    try:
        response = table.get_item(Key={"id": "token"})
        if "Item" in response:
            return response["Item"].get("cache")
        return None
    except ClientError as e:
        print(f"Error reading from DynamoDB: {e}")
        return None


def save_token_cache(cache_data):
    try:
        table.put_item(Item={"id": "token", "cache": cache_data})
    except ClientError as e:
        print(f"Error writing to DynamoDB: {e}")


def authenticate_microsoft():
    cache = msal.SerializableTokenCache()
    cached_data = get_token_cache()
    if cached_data:
        cache.deserialize(cached_data)

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,
    )
    accounts = app.get_accounts()
    if accounts:
        print("Using cached credentials from DynamoDB...")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            if cache.has_state_changed:
                save_token_cache(cache.serialize())
            return result["access_token"]

    raise Exception(
        "No valid cached token found. Run setup_token_interactive.py first."
    )


def save_subscription_record(subscription_id, client_state):
    try:
        table.put_item(
            Item={
                "id": "webhook-subscription",
                "subscription_id": subscription_id,
                "client_state": client_state,
            }
        )
        print(f"Saved subscription ID to DynamoDB: {subscription_id}")
    except ClientError as e:
        raise RuntimeError("Could not save webhook subscription security record") from e


def ensure_seen_email_ttl():
    """Enable expiry for per-message idempotency records when possible."""
    try:
        response = dynamodb_client.describe_time_to_live(TableName=TABLE_NAME)
        description = response.get("TimeToLiveDescription") or {}
        status = description.get("TimeToLiveStatus")
        attribute = description.get("AttributeName")

        if status in {"ENABLED", "ENABLING"} and attribute == "expires_at":
            return
        if status in {"ENABLED", "ENABLING", "DISABLING"}:
            print(
                "Warning: DynamoDB TTL is already configured with a different "
                "attribute or is changing; seen-message records may accumulate."
            )
            return

        dynamodb_client.update_time_to_live(
            TableName=TABLE_NAME,
            TimeToLiveSpecification={
                "Enabled": True,
                "AttributeName": "expires_at",
            },
        )
        print("Enabled DynamoDB TTL for seen-message idempotency records.")
    except ClientError as e:
        print(
            "Warning: could not enable DynamoDB TTL for seen-message records: "
            f"{e}"
        )


def create_subscription(webhook_url):
    print(f"Creating subscription with webhook URL: {webhook_url}")
    token = authenticate_microsoft()

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": 'IdType="ImmutableId"',
        }
    )

    folder_response = session.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders",
        params={"$top": 100, "$select": "id,displayName"},
        timeout=30,
    )
    folder_response.raise_for_status()
    folders = folder_response.json().get("value", [])
    junk = next(
        (
            folder
            for folder in folders
            if folder.get("displayName", "").lower() in ("junk email", "junk")
        ),
        None,
    )
    if not junk:
        raise Exception("No Junk Email folder found")

    junk_id = junk["id"]
    print(f"Found Junk Email folder: {junk_id}")

    expiration = datetime.now(timezone.utc) + timedelta(days=2, hours=12)
    expiration_str = expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

    client_state = os.environ.get("WEBHOOK_CLIENT_STATE") or secrets.token_urlsafe(32)
    if len(client_state) > 255:
        raise ValueError("WEBHOOK_CLIENT_STATE must be at most 255 characters")

    payload = {
        "changeType": "created",
        "notificationUrl": webhook_url,
        "resource": f"me/mailFolders('{junk_id}')/messages",
        "expirationDateTime": expiration_str,
        "clientState": client_state,
    }

    print("\nCreating subscription with immutable message IDs.")
    response = session.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        json=payload,
        timeout=30,
    )
    if response.status_code != 201:
        raise Exception(
            f"Failed to create subscription: HTTP {response.status_code} "
            f"{response.text[:500]}"
        )

    result = response.json()
    subscription_id = result.get("id")
    if not subscription_id:
        raise RuntimeError("Graph created a subscription without returning its id")
    print("\nSubscription created successfully.")
    print(f"Subscription ID: {subscription_id}")
    print(f"Expires: {result.get('expirationDateTime')}")
    save_subscription_record(subscription_id, client_state)
    ensure_seen_email_ttl()
    return subscription_id


def main():
    print("=== Microsoft Graph Webhook Setup ===\n")
    print("Deploy the webhook Lambda and API Gateway first.\n")
    webhook_url = input("Enter your API Gateway webhook URL: ").strip()
    if not webhook_url.startswith("https://"):
        print("URL must start with https://")
        return

    try:
        subscription_id = create_subscription(webhook_url)
        print("\nSetup complete.")
        print("1. Renew the subscription at least every 2 days.")
        print("2. Test by sending a message that Outlook places in Junk.")
        print("3. Check Lambda logs for exact-message processing.")
        print(f"Subscription ID: {subscription_id}")
    except Exception as e:
        print(f"Setup failed: {str(e)}")


if __name__ == "__main__":
    main()
