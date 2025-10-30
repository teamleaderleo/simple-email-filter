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

    # If we get here, we need manual authentication
    raise Exception(
        "No valid cached token found. You need to authenticate locally first "
        "and upload the token cache to DynamoDB. Run setup_token.py again."
    )


def get_deletion_decision(email):
    """
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
        "Keep legitimate service notices, real newsletters, job/recruiter mail, local marketing, "
        "financial updates, artist/creator updates, and Microsoft's Rewards promos."
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
            max_completion_tokens=1,  # enforce single-character output
            temperature=0,  # deterministic
        )
        raw = (resp.choices[0].message.content or "").strip()
        decision = raw[0] if raw else "0"  # default keep on empty
        return decision == "1"
    except Exception as e:
        print(f"OpenAI API error: {e}")
        return False  # Don't delete on error


def process_webhook_notification(notification):
    """Process a single webhook notification from Microsoft Graph"""

    # Authenticate
    token = authenticate_microsoft()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    # Get the resource URL from notification
    resource = notification.get("resource")
    if not resource:
        print("No resource in notification")
        return {"processed": 0, "deleted": 0}

    # Fetch the actual message
    # The resource is like: "me/mailFolders('AAMkAG...')/messages"
    # We need to get the new messages

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
        print("No Junk Email folder found")
        return {"processed": 0, "deleted": 0}

    junk_id = junk["id"]

    # Fetch most recent emails (webhook might batch notifications)
    msgs = (
        session.get(
            f"https://graph.microsoft.com/v1.0/me/mailFolders/{junk_id}/messages",
            params={"$top": 5, "$orderby": "receivedDateTime desc"},
        )
        .json()
        .get("value", [])
    )

    if not msgs:
        print("No messages found")
        return {"processed": 0, "deleted": 0}

    # Load cache of previously seen email IDs
    previous_ids = set()
    try:
        cache_response = table.get_item(Key={"id": "seen-emails"})
        if "Item" in cache_response:
            previous_ids = set(cache_response["Item"].get("email_ids", []))
    except Exception as e:
        print(f"Cache check failed: {e}")

    # Process each new email
    deleted_count = 0
    new_ids = set()

    for msg in msgs:
        email_id = msg.get("id")
        new_ids.add(email_id)

        # Skip if we've already processed this email
        if email_id in previous_ids:
            print(f"Skipping already-processed email: {email_id}")
            continue

        email = {
            "id": email_id,
            "subject": msg.get("subject", ""),
            "sender": (msg.get("from") or {})
            .get("emailAddress", {})
            .get("address", ""),
            "preview": msg.get("bodyPreview", ""),
        }

        print(f"Processing: {email['sender']} - {email['subject']}")

        # Ask OpenAI if we should delete (True if model returns '1')
        should_delete = get_deletion_decision(email)

        if should_delete:
            print(f"DELETING: {email['sender']} - {email['subject']}")
            delete_url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}"
            response = session.delete(delete_url)

            if response.status_code == 204:
                deleted_count += 1
                print("  Deleted successfully")
            else:
                print(f"  Failed to delete: HTTP {response.status_code}")
                # Still mark as seen even if delete failed
                new_ids.add(email_id)
        else:
            print(f"KEEPING: {email['sender']} - {email['subject']}")

    # Update cache with all current IDs
    all_current_ids = previous_ids | new_ids
    try:
        table.put_item(Item={"id": "seen-emails", "email_ids": list(all_current_ids)})
    except Exception as e:
        print(f"Cache update failed: {e}")

    return {"processed": len(new_ids - previous_ids), "deleted": deleted_count}


def lambda_handler(event, context):
    """Lambda entry point for webhook handling"""

    # Log the full event to see what Microsoft is sending
    print(f"=== RAW EVENT START ===")
    print(json.dumps(event))
    print(f"=== RAW EVENT END ===")

    # Handle validation request from Microsoft
    # When you first create a subscription, Microsoft sends a validation token
    query_params = event.get("queryStringParameters")
    print(f"Query params: {query_params}")

    if query_params and "validationToken" in query_params:
        validation_token = query_params["validationToken"]
        print(f"VALIDATION: Returning token: {validation_token}")
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": validation_token,
        }

    # Parse the webhook notification
    try:
        body = json.loads(event.get("body", "{}"))
        notifications = body.get("value", [])

        if not notifications:
            print("No notifications in request body")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No notifications"}),
            }

        # Process each notification
        total_processed = 0
        total_deleted = 0

        for notification in notifications:
            change_type = notification.get("changeType")
            print(f"Processing notification: changeType={change_type}")

            if change_type == "created":
                result = process_webhook_notification(notification)
                total_processed += result["processed"]
                total_deleted += result["deleted"]

        summary = f"Processed {total_processed} new emails, deleted {total_deleted}"
        print(summary)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": summary,
                    "processed": total_processed,
                    "deleted": total_deleted,
                }
            ),
        }

    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
