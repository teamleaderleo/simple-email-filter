import msal
import requests
import sys
import json
from dotenv import load_dotenv
import os
from openai import OpenAI

load_dotenv()

# Configuration
CLIENT_ID = os.getenv("CLIENT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]

openai_client = OpenAI(api_key=OPENAI_API_KEY)


def authenticate_microsoft():
    """Authenticate with Microsoft Graph API using cached tokens"""
    # Create a token cache file
    cache = msal.SerializableTokenCache()
    cache_file = "token_cache.bin"

    # Load existing cache if it exists
    if os.path.exists(cache_file):
        cache.deserialize(open(cache_file, "r").read())

    app = msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )

    # Try to get token silently from cache first
    accounts = app.get_accounts()
    if accounts:
        print("Using cached credentials...")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    # If no cached token, do device flow
    print("No cached token found, initiating device flow...")
    flow = app.initiate_device_flow(scopes=SCOPES)

    if not flow or "user_code" not in flow:
        print("Failed to start device code flow.")
        print(flow)
        sys.exit(1)

    print("Open this URL and enter the code below:")
    print(flow["verification_uri"])
    print("Code:", flow["user_code"])

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print("Auth failed:", result)
        sys.exit(1)

    # Save the cache
    if cache.has_state_changed:
        with open(cache_file, "w") as f:
            f.write(cache.serialize())

    return result["access_token"]


def get_deletion_decisions(emails):
    """Send all emails to OpenAI and get back which indices to delete"""

    # Format emails for the prompt
    email_list = ""
    for i, email in enumerate(emails):
        email_list += f"{i}. FROM: {email['sender']} | SUBJECT: {email['subject']}\n"
        if email["preview"]:
            email_list += f"   PREVIEW: {email['preview'][:100]}\n"

    prompt = f"""You are filtering junk mail. Only delete the ABSOLUTE most heinous spam. Don't mistake simple marketing from local businesses or legitimate services.

DELETE only:
- Obvious phishing/scams (fake verification, fake cloud storage warnings)
- Basically ALL casino related stuff
- Dangerous malware/fraud attempts
- Clearly fake sender addresses

KEEP things like:
- Legitimate service notifications (AWS, Google, etc.)
- Newsletters from real companies (Koyeb, Fantuan, etc.)
- Job alerts and recruitment emails
- Local business marketing (Rendezvous, etc.)
- Financial service updates (Interactive Brokers)
- Artist/creator updates
- Microsoft's reward promotions (they are real though cringe)

Here are the emails (numbered):

{email_list}

Respond with ONLY a JSON array of indices to delete, nothing else.
Example: [0, 2, 5] or [] if nothing should be deleted.
"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",  # 10M free tokens/day with data sharing
            messages=[{"role": "user", "content": prompt}],
            temperature=1,
        )

        result = response.choices[0].message.content.strip()
        # Parse the JSON array
        indices = json.loads(result)
        return indices
    except Exception as e:
        print(f"OpenAI API error: {e}")
        print(f"Response was: {result if 'result' in locals() else 'N/A'}")
        return []


def process_junk_mail(dry_run=True):
    """Main function to process and clean junk mail"""

    # Authenticate
    token = authenticate_microsoft()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    # Find Junk Email folder
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
        sys.exit("No Junk Email folder found")

    junk_id = junk["id"]

    # Get recent junk messages
    msgs = (
        session.get(
            f"https://graph.microsoft.com/v1.0/me/mailFolders/{junk_id}/messages",
            params={"$top": 20, "$orderby": "receivedDateTime desc"},
        )
        .json()
        .get("value", [])
    )

    if not msgs:
        print("No junk emails to process.")
        return

    print(f"\nFound {len(msgs)} junk emails\n")
    print("=" * 80)

    # Prepare email data
    emails = []
    for i, m in enumerate(msgs):
        emails.append(
            {
                "id": m.get("id"),
                "subject": m.get("subject", ""),
                "sender": (m.get("from") or {})
                .get("emailAddress", {})
                .get("address", ""),
                "preview": m.get("bodyPreview", ""),
                "received": m.get("receivedDateTime", ""),
            }
        )
        # Display for user
        print(f"{i}. {emails[i]['received']} | {emails[i]['sender']}")
        print(f"   {emails[i]['subject']}")
        print()

    print("=" * 80)
    print("\nAsking OpenAI which ones to delete...\n")

    # Get AI decision
    indices_to_delete = get_deletion_decisions(emails)

    print(f"OpenAI recommends deleting indices: {indices_to_delete}")
    print()

    # Process deletions
    deleted_count = 0
    for idx in indices_to_delete:
        if idx < 0 or idx >= len(emails):
            print(f"⚠️  Invalid index {idx}, skipping")
            continue

        email = emails[idx]
        print(f"🗑️  DELETING [{idx}]: {email['sender']} - {email['subject']}")

        if not dry_run:
            # Actually delete via Graph API
            delete_url = f"https://graph.microsoft.com/v1.0/me/messages/{email['id']}"
            response = session.delete(delete_url)

            if response.status_code == 204:
                deleted_count += 1
                print(f"   ✓ Deleted successfully")
            else:
                print(f"   ✗ Failed to delete: HTTP {response.status_code}")
        else:
            deleted_count += 1

    kept_count = len(emails) - deleted_count
    mode = "[DRY RUN] " if dry_run else ""
    print(f"\n{mode}Summary: {deleted_count} deleted, {kept_count} kept")


if __name__ == "__main__":
    # Run in dry-run mode first
    print("=" * 80)
    print("DRY RUN MODE - No emails will actually be deleted")
    print("=" * 80)
    process_junk_mail(dry_run=True)

    # Uncomment below to actually delete emails
    # print("\n" + "=" * 80)
    # print("LIVE MODE - Actually deleting emails")
    # print("=" * 80)
    # process_junk_mail(dry_run=False)
