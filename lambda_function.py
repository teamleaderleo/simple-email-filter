import msal
import requests
import json
import os
from openai import OpenAI
import boto3
from botocore.exceptions import ClientError

# Configuration from environment variables
CLIENT_ID = os.environ.get("CLIENT_ID")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TABLE_NAME = "email-filter-tokens"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]

openai_client = OpenAI(api_key=OPENAI_API_KEY)
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

    # If we get here, we need manual authentication
    # Lambda can't do device flow interactively!
    raise Exception(
        "No valid cached token found. You need to authenticate locally first "
        "and upload the token cache to DynamoDB. Run setup_token.py again."
    )


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
- "Free car repair kit" type giveaways (it's not really CAA!)
- "Free Yeti Tumbler" type giveaways (not really Tim Hortons!)
- Stuff like "You’ve got $1,538 — why wait to spend it? Re: MtOWzw"
- Stuff like "Congrats! You've Scored $50 Off + FREE Gift."

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
            model="gpt-5-mini",
            messages=[{"role": "user", "content": prompt}]
        )

        result = response.choices[0].message.content.strip()
        indices = json.loads(result)
        return indices
    except Exception as e:
        print(f"OpenAI API error: {e}")
        return []


def process_junk_mail():
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
        (f for f in folders if f.get("displayName", "").lower() in ("junk email", "junk")),
        None,
    )
    if not junk:
        print("No Junk Email folder found")
        return {"statusCode": 200, "body": "No junk folder found"}

    junk_id = junk["id"]

    # Fetch 20 most recent emails
    msgs = (
        session.get(
            f"https://graph.microsoft.com/v1.0/me/mailFolders/{junk_id}/messages",
            params={"$top": 20, "$orderby": "receivedDateTime desc"},
        )
        .json()
        .get("value", [])
    )

    if not msgs:
        print("No junk emails to process")
        return {"statusCode": 200, "body": "No emails to process"}

    print(f"Found {len(msgs)} junk emails")

    # Prepare email data and collect IDs
    emails = []
    current_ids = set()
    for m in msgs:
        email_id = m.get('id')
        current_ids.add(email_id)
        emails.append({
            "id": email_id,
            "subject": m.get("subject", ""),
            "sender": (m.get("from") or {}).get("emailAddress", {}).get("address", ""),
            "preview": m.get("bodyPreview", ""),
            "received": m.get("receivedDateTime", "")
        })

    # Load cache of previously seen email IDs
    previous_ids = set()
    try:
        cache_response = table.get_item(Key={'id': 'seen-emails'})
        if 'Item' in cache_response:
            previous_ids = set(cache_response['Item'].get('email_ids', []))
    except Exception as e:
        print(f"Cache check failed: {e}")

    # Find NEW emails only
    new_ids = current_ids - previous_ids
    
    if not new_ids:
        print("No new emails since last run - skipping AI classification")
        return {"statusCode": 200, "body": json.dumps({
            "message": "No new emails to process",
            "deleted": 0,
            "kept": len(emails)
        })}

    print(f"Found {len(new_ids)} new emails")

    # Filter to only new emails, keep them in order
    new_emails = [email for email in emails if email['id'] in new_ids]
    
    # Send up to 10 newest to OpenAI
    emails_to_classify = new_emails[:10]
    print(f"Classifying {len(emails_to_classify)} new emails with OpenAI")

    # Get AI decision
    indices_to_delete = get_deletion_decisions(emails_to_classify)
    print(f"OpenAI recommends deleting indices: {indices_to_delete}")

    # Process deletions
    deleted_count = 0
    for idx in indices_to_delete:
        if idx < 0 or idx >= len(emails_to_classify):
            print(f"Invalid index {idx}, skipping")
            continue

        email = emails_to_classify[idx]
        print(f"DELETING [{idx}]: {email['sender']} - {email['subject']}")

        # Actually delete via Graph API
        delete_url = f"https://graph.microsoft.com/v1.0/me/messages/{email['id']}"
        response = session.delete(delete_url)

        if response.status_code == 204:
            deleted_count += 1
            print(f"  Deleted successfully")
        else:
            print(f"  Failed to delete: HTTP {response.status_code}")

    # Update cache with ALL current email IDs (not just processed ones)
    try:
        table.put_item(Item={
            'id': 'seen-emails',
            'email_ids': list(current_ids)
        })
    except Exception as e:
        print(f"Cache update failed: {e}")

    kept_count = len(emails_to_classify) - deleted_count
    summary = f"Summary: {deleted_count} deleted, {kept_count} kept ({len(emails) - len(new_ids)} already seen)"
    print(summary)
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": summary,
            "deleted": deleted_count,
            "kept": kept_count,
            "already_seen": len(emails) - len(new_ids)
        })
    }


def lambda_handler(event, context):
    """Lambda entry point"""
    try:
        return process_junk_mail()
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
