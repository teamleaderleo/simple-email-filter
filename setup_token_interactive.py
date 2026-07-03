import os
import msal
import boto3
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]
TABLE_NAME = "email-filter-tokens"
AWS_REGION = "us-east-2"

if not CLIENT_ID:
    raise SystemExit("Missing CLIENT_ID in .env")

cache = msal.SerializableTokenCache()

app = msal.PublicClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    token_cache=cache,
)

print("Opening Microsoft login in your browser...")
result = app.acquire_token_interactive(scopes=SCOPES)

if "access_token" not in result:
    print("Auth failed:")
    print(result)
    raise SystemExit(1)

print("Microsoft authentication successful.")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(TABLE_NAME)

table.put_item(Item={"id": "token", "cache": cache.serialize()})

print(f"Saved token cache to DynamoDB table {TABLE_NAME} in {AWS_REGION}.")
