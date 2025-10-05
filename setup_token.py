"""
Run this script ONCE locally to authenticate and upload token to AWS SSM Parameter Store
"""

import msal
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]
PARAMETER_NAME = "/email-filter/token-cache"
AWS_REGION = "us-east-1"  # Change to your preferred region


def authenticate_and_upload():
    """Authenticate locally and upload token cache to AWS SSM Parameter Store"""

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

    # Upload to AWS SSM Parameter Store
    print(f"\nUploading token cache to AWS Parameter Store ({PARAMETER_NAME})...")

    ssm_client = boto3.client("ssm", region_name=AWS_REGION)
    cache_data = cache.serialize()

    try:
        # Try to create/update the parameter (advanced tier for larger size)
        ssm_client.put_parameter(
            Name=PARAMETER_NAME,
            Value=cache_data,
            Type="SecureString",
            Description="MSAL token cache for email filter Lambda",
            Tier="Advanced",  # Supports up to 8KB instead of 4KB
            Overwrite=True,
        )
        print(f"✓ Parameter updated successfully in {AWS_REGION}")
        print("Note: Using Advanced tier ($0.05/month - cheaper than Secrets Manager)")
    except Exception as e:
        print(f"Error: {e}")
        return

    print("\n" + "=" * 60)
    print("Setup complete! Your Lambda function can now authenticate.")
    print("=" * 60)


if __name__ == "__main__":
    print("AWS Parameter Store Setup for Lambda Email Filter")
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
