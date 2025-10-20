import msal
import requests
import json
import os
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import ClientError

# Configuration from environment variables
CLIENT_ID = os.environ.get("CLIENT_ID")
TABLE_NAME = "email-filter-tokens"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]

# Use Lambda's region (automatically set by AWS)
dynamodb = boto3.resource(
    "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-2")
)
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
        "No valid cached token found. You need to authenticate locally first "
        "and upload the token cache to DynamoDB. Run setup_token.py again."
    )


def get_subscription_id():
    """Retrieve subscription ID from DynamoDB"""
    try:
        response = table.get_item(Key={"id": "webhook-subscription"})
        if "Item" in response:
            return response["Item"].get("subscription_id")
        return None
    except ClientError as e:
        print(f"Error reading subscription ID from DynamoDB: {e}")
        return None


def renew_subscription():
    """Renew the Microsoft Graph subscription"""

    # Get subscription ID
    subscription_id = get_subscription_id()
    if not subscription_id:
        raise Exception(
            "No subscription ID found in DynamoDB. Run setup_webhook.py first."
        )

    print(f"Renewing subscription: {subscription_id}")

    # Authenticate
    token = authenticate_microsoft()

    # Calculate new expiration (2.5 days from now to be safe)
    new_expiration = datetime.utcnow() + timedelta(days=2, hours=12)
    expiration_str = new_expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

    # Renew the subscription
    renew_url = f"https://graph.microsoft.com/v1.0/subscriptions/{subscription_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {"expirationDateTime": expiration_str}

    response = requests.patch(renew_url, headers=headers, json=payload)

    if response.status_code == 200:
        result = response.json()
        print(f"Subscription renewed successfully!")
        print(f"New expiration: {result.get('expirationDateTime')}")
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Subscription renewed",
                    "subscription_id": subscription_id,
                    "expires": result.get("expirationDateTime"),
                }
            ),
        }
    else:
        error_msg = f"Failed to renew subscription: HTTP {response.status_code}"
        print(error_msg)
        print(f"Response: {response.text}")
        raise Exception(error_msg)


def lambda_handler(event, context):
    """Lambda entry point for subscription renewal"""
    try:
        return renew_subscription()
    except Exception as e:
        print(f"Error: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
