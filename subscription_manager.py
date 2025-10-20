"""
One-time setup script to create Microsoft Graph subscription for webhook notifications.
Run this after deploying the webhook Lambda and API Gateway.
"""

import msal
import requests
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import boto3
from botocore.exceptions import ClientError

# Load environment variables
load_dotenv()

CLIENT_ID = os.environ.get("CLIENT_ID")
TABLE_NAME = "email-filter-tokens"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


def get_token_cache():
    """Retrieve token cache from DynamoDB"""
    try:
        response = table.get_item(Key={"id": "token"})
        if "Item" in response:
            return response["Item"].get("cache")
        return None
    except ClientError as e:
        print(f"Error reading from DynamoDB: {e}")
        return None


def save_token_cache(cache_data):
    """Save token cache to DynamoDB"""
    try:
        table.put_item(Item={"id": "token", "cache": cache_data})
    except ClientError as e:
        print(f"Error writing to DynamoDB: {e}")


def authenticate_microsoft():
    """Authenticate with Microsoft Graph API using cached tokens from DynamoDB"""
    cache = msal.SerializableTokenCache()

    # Load existing cache from DynamoDB
    cached_data = get_token_cache()
    if cached_data:
        cache.deserialize(cached_data)

    app = msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )

    # Try to get token silently from cache first
    accounts = app.get_accounts()
    if accounts:
        print("Using cached credentials from DynamoDB...")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            # Save updated cache if it changed
            if cache.has_state_changed:
                save_token_cache(cache.serialize())
            return result["access_token"]

    raise Exception(
        "No valid cached token found. Run setup_token.py first to authenticate."
    )


def save_subscription_id(subscription_id):
    """Save subscription ID to DynamoDB"""
    try:
        table.put_item(
            Item={"id": "webhook-subscription", "subscription_id": subscription_id}
        )
        print(f"Saved subscription ID to DynamoDB: {subscription_id}")
    except ClientError as e:
        print(f"Error saving subscription ID: {e}")


def create_subscription(webhook_url):
    """Create a new Microsoft Graph subscription"""

    print(f"Creating subscription with webhook URL: {webhook_url}")

    # Authenticate
    token = authenticate_microsoft()

    # Get user info to find junk folder
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    folders = (
        session.get("https://graph.microsoft.com/v1.0/me/mailFolders?$top=100")
        .json()
        .get("value", [])
    )
    junk = next(
        (
            f
            for f in folders
            if f.get("displayName", "").lower() in ("junk email", "junk")
        ),
        None,
    )

    if not junk:
        raise Exception("No Junk Email folder found")

    junk_id = junk["id"]
    print(f"Found Junk Email folder: {junk_id}")

    # Calculate expiration (2.5 days from now)
    expiration = datetime.utcnow() + timedelta(days=2, hours=12)
    expiration_str = expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

    # Create subscription
    subscription_url = "https://graph.microsoft.com/v1.0/subscriptions"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "changeType": "created",
        "notificationUrl": webhook_url,
        "resource": f"me/mailFolders('{junk_id}')/messages",
        "expirationDateTime": expiration_str,
        "clientState": "SecretClientState",  # Used to verify notifications
    }

    print(f"\nCreating subscription with payload:")
    print(json.dumps(payload, indent=2))

    response = requests.post(subscription_url, headers=headers, json=payload)

    if response.status_code == 201:
        result = response.json()
        subscription_id = result.get("id")

        print(f"\n✅ Subscription created successfully!")
        print(f"Subscription ID: {subscription_id}")
        print(f"Expires: {result.get('expirationDateTime')}")

        # Save to DynamoDB
        save_subscription_id(subscription_id)

        return subscription_id
    else:
        error_msg = f"Failed to create subscription: HTTP {response.status_code}"
        print(f"\n❌ {error_msg}")
        print(f"Response: {response.text}")
        raise Exception(error_msg)


def main():
    """Main setup flow"""
    print("=== Microsoft Graph Webhook Setup ===\n")

    # Get webhook URL from user
    print("First, you need to deploy the webhook Lambda and API Gateway.")
    print("Then come back here with the API Gateway URL.\n")

    webhook_url = input("Enter your API Gateway webhook URL: ").strip()

    if not webhook_url.startswith("https://"):
        print("❌ URL must start with https://")
        return

    print("\nProceeding with subscription creation...")

    try:
        subscription_id = create_subscription(webhook_url)

        print("\n" + "=" * 50)
        print("✅ Setup complete!")
        print("=" * 50)
        print("\nNext steps:")
        print("1. The subscription will be automatically renewed every 2 days")
        print("2. Test by sending spam to your Outlook junk folder")
        print("3. Check Lambda logs to see webhook processing")
        print(f"\nSubscription ID (saved to DynamoDB): {subscription_id}")

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")


if __name__ == "__main__":
    main()
