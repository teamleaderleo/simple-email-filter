"""
Run this script ONCE locally to authenticate and upload token to DynamoDB
"""

import msal
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]
TABLE_NAME = "email-filter-tokens"
AWS_REGION = "us-east-2"  # Change to your preferred region


def authenticate_and_upload():
    """Authenticate locally and upload token cache to DynamoDB"""

    # Create MSAL app with cache
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )

    # Do device flow authentication
    flow = app.initiate_device_flow(scopes=SCOPES)

    if not flow or "user_code" not in flow:
        print("Failed to start device code flow.")
        print(flow)
        return

    print("=" * 60)
    print("Open this URL and enter the code below:")
    print(flow["verification_uri"])
    print("Code:", flow["user_code"])
    print("=" * 60)

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print("Auth failed:", result)
        return

    print("\n✓ Authentication successful!")

    # Upload to DynamoDB
    print(f"\nUploading token cache to DynamoDB ({TABLE_NAME})...")

    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dynamodb.Table(TABLE_NAME)
    cache_data = cache.serialize()

    try:
        table.put_item(Item={"id": "token", "cache": cache_data})
        print(f"✓ Token cache saved successfully in {AWS_REGION}")
        print("Note: Using DynamoDB Free Tier (25GB storage - completely free)")
    except Exception as e:
        print(f"Error: {e}")
        return

    print("\n" + "=" * 60)
    print("Setup complete! Your Lambda function can now authenticate.")
    print("=" * 60)


if __name__ == "__main__":
    print("DynamoDB Setup for Lambda Email Filter")
    print("=" * 60)

    # Check AWS credentials
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        print(f"AWS Account: {identity['Account']}")
        print(f"AWS Region: {AWS_REGION}")
        print()
    except Exception as e:
        print(f"ERROR: AWS credentials not configured properly: {e}")
        print("\nPlease run: aws configure")
        exit(1)

    authenticate_and_upload()
