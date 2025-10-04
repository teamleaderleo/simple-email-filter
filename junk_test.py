import msal, requests, sys

from dotenv import load_dotenv
import os

load_dotenv()  # reads .env
CLIENT_ID = os.getenv("CLIENT_ID")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]


app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
flow = app.initiate_device_flow(scopes=SCOPES)

# If device flow wasn't created, show the error and exit
if not flow or "user_code" not in flow:
    print("Failed to start device code flow.")
    print(flow)  # will contain 'error' and 'error_description'
    sys.exit(1)

print("Open this URL and enter the code below:")
print(flow["verification_uri"])
print("Code:", flow["user_code"])

result = app.acquire_token_by_device_flow(flow)
if "access_token" not in result:
    print("Auth failed:", result)
    sys.exit(1)

token = result["access_token"]
s = requests.Session()
s.headers.update({"Authorization": f"Bearer {token}"})

# Find Junk Email folder
folders = (
    s.get("https://graph.microsoft.com/v1.0/me/mailFolders?$top=100")
    .json()
    .get("value", [])
)
junk = next(
    (f for f in folders if f.get("displayName", "").lower() in ("junk email", "junk")),
    None,
)
if not junk:
    sys.exit("No Junk Email folder found")
junk_id = junk["id"]

# List recent messages
msgs = (
    s.get(
        f"https://graph.microsoft.com/v1.0/me/mailFolders/{junk_id}/messages",
        params={"$top": 10, "$orderby": "receivedDateTime desc"},
    )
    .json()
    .get("value", [])
)

for m in msgs:
    subj = m.get("subject", "")
    sender = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
    print(f"{m.get('receivedDateTime')} | {sender} | {subj}")
