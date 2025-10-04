import msal, requests

CLIENT_ID = "YOUR_CLIENT_ID"  # from the app you set to Personal-only
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite", "offline_access"]

app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
flow = app.initiate_device_flow(scopes=SCOPES)
print(flow["message"])
result = app.acquire_token_by_device_flow(flow)
token = result["access_token"]

s = requests.Session()
s.headers.update({"Authorization": f"Bearer {token}"})

# Find Junk Email folder
folders = s.get("https://graph.microsoft.com/v1.0/me/mailFolders?$top=100").json()["value"]
junk = next((f for f in folders if f.get("displayName","").lower() in ("junk email","junk")), None)
if not junk: raise SystemExit("No Junk Email folder found")
junk_id = junk["id"]

# List recent messages
msgs = s.get(
    f"https://graph.microsoft.com/v1.0/me/mailFolders/{junk_id}/messages",
    params={"$top": 10, "$orderby": "receivedDateTime desc"}
).json()["value"]

for m in msgs:
    subj = m.get("subject","")
    sender = (m.get("from") or {}).get("emailAddress", {}).get("address","")
    print(f"{m.get('receivedDateTime')} | {sender} | {subj}")
