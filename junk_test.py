import msal
import requests
import sys
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


def get_deletion_decision(email):
    """
    EXACT SAME FUNCTION AS IN WEBHOOK_HANDLER.PY
    Send email to OpenAI and get back whether to delete it.
    The model MUST reply with a single character:
      '1' = DELETE, '0' = KEEP
    """
    email_text = f"""FROM: {email['sender']}
SUBJECT: {email['subject']}
PREVIEW: {email['preview'][:200]}"""

    system = (
        "You are a binary classifier for junk mail in the Junk folder. "
        "Reply with a SINGLE character only: '1' to delete, '0' to keep. No other text."
    )

    # criteria summary to keep prompt compact (token-saving)
    criteria = (
        "Delete only obvious phishing/scams, casino promos (e.g., 'Free Spins'), "
        "malware/fraud, clearly fake senders, fake giveaway bait (e.g., 'Free car repair kit', "
        "'Free Yeti Tumbler'), money-eyes emoji bait, and sketchy 'You've got $$$' hooks. "
        "Also delete brandless account/storage/security 'Action Required' emails (e.g., 'Cloud Service Notification', "
        "'Set Up Your Account', 'Running out of storage? — Upgrade') when there is a sender-brand mismatch or they lack a real brand; "
        "mojibake in the subject (e.g., 'Ã¢', 'Â', 'â€'), typos, or weird inline timestamps strengthen deletion. "
        "Keep legitimate service notices, real newsletters, job/recruiter mail, local marketing, financial updates, "
        "artist/creator updates, and Microsoft's Rewards promos when the brand matches the sender and no phishing language is used."
    )

    user = (
        f"{criteria}\n\nEmail to classify:\n\n{email_text}\n\nReturn ONLY '1' or '0'."
    )

    try:
        resp = openai_client.chat.completions.create(
            model="gpt-5-mini",  # This exists as of August 2025! Look it up if you don't believe me!!!
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # don't limit the completion tokens!
            # no temperature parameter is allowed! gpt-5 is smart enough
        )
        raw = (resp.choices[0].message.content or "").strip()
        decision = raw[0] if raw else "0"  # default keep on empty
        print(f"\nRaw API response: '{raw}'")
        print(f"Decision: {decision} ({'DELETE' if decision == '1' else 'KEEP'})")
        return decision == "1"
    except Exception as e:
        print(f"\nOpenAI API error: {e}")
        return False  # Don't delete on error


def process_latest_junk(count=1, delete=False):
    """Process the latest N junk emails and delete if GPT-5 says so"""

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

    # Get latest N junk messages
    msgs = (
        session.get(
            f"https://graph.microsoft.com/v1.0/me/mailFolders/{junk_id}/messages",
            params={"$top": count, "$orderby": "receivedDateTime desc"},
        )
        .json()
        .get("value", [])
    )

    if not msgs:
        print("No junk emails found.")
        return

    print(f"\nProcessing {len(msgs)} latest junk email(s)\n")

    deleted_count = 0
    kept_count = 0

    for i, m in enumerate(msgs):
        email = {
            "id": m.get("id"),
            "subject": m.get("subject", ""),
            "sender": (m.get("from") or {}).get("emailAddress", {}).get("address", ""),
            "preview": m.get("bodyPreview", ""),
            "received": m.get("receivedDateTime", ""),
        }

        # Display email info
        print("\n" + "=" * 80)
        print(f"EMAIL {i+1}/{len(msgs)}:")
        print("=" * 80)
        print(f"Received: {email['received']}")
        print(f"From: {email['sender']}")
        print(f"Subject: {email['subject']}")
        print(f"Preview: {email['preview'][:200]}")
        print("=" * 80)

        # Get AI decision
        should_delete = get_deletion_decision(email)

        if should_delete:
            if delete:
                print("\n🗑️  DELETING EMAIL...")
                delete_url = (
                    f"https://graph.microsoft.com/v1.0/me/messages/{email['id']}"
                )
                response = session.delete(delete_url)

                if response.status_code == 204:
                    print("✅ Successfully deleted!")
                    deleted_count += 1
                else:
                    print(f"❌ Failed to delete: HTTP {response.status_code}")
            else:
                print("\n🗑️  WOULD DELETE (dry-run mode)")
                deleted_count += 1
        else:
            print("\n✅ KEEP")
            kept_count += 1

    print("\n" + "=" * 80)
    mode = "" if delete else "[DRY-RUN] "
    print(f"{mode}SUMMARY: {deleted_count} deleted, {kept_count} kept")
    print("=" * 80)


if __name__ == "__main__":
    # Parse arguments
    count = 1
    delete = False

    for arg in sys.argv[1:]:
        if arg == "--delete":
            delete = True
        else:
            try:
                count = int(arg)
                if count < 1:
                    print("Count must be at least 1")
                    sys.exit(1)
            except ValueError:
                print(f"Invalid argument: {arg}")
                print("Usage: python junk_test.py [count] [--delete]")
                print("Examples:")
                print("  python junk_test.py           # Dry-run on latest 1 email")
                print("  python junk_test.py 5         # Dry-run on latest 5 emails")
                print(
                    "  python junk_test.py --delete  # Actually delete latest 1 email"
                )
                print(
                    "  python junk_test.py 10 --delete  # Actually delete latest 10 emails"
                )
                sys.exit(1)

    print("=" * 80)
    mode = "DELETING" if delete else "DRY-RUN"
    print(f"{mode}: LATEST {count} JUNK EMAIL(S)")
    print("=" * 80)
    process_latest_junk(count, delete)
