import json
import os
import re
from urllib.parse import quote

import boto3
import msal
import requests
from botocore.exceptions import ClientError

CLIENT_ID = os.environ.get("CLIENT_ID")
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_MODEL = os.environ.get("CLOUDFLARE_MODEL", "@cf/google/gemma-4-26b-a4b-it")

TABLE_NAME = "email-filter-tokens"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]

dynamodb = boto3.resource(
    "dynamodb",
    region_name=os.environ.get("AWS_REGION", "us-east-2"),
)
table = dynamodb.Table(TABLE_NAME)

OBVIOUS_DELETE_PATTERNS = [
    r"\bfree spins?\b",
    r"\bno deposit\b",
    r"\bcasino\b",
    r"\bwelcome bonus\b",
    r"\bpayout verification\b",
    r"\bpayment code\b",
    r"\baccount payout\b",
    r"\bfree for new casino players\b",
    r"\bclaim your free\b",
    r"\b100 balls\b",
    r"\b400 free\b",
    r"\b200 free\b",
]


def get_token_cache():
    try:
        response = table.get_item(Key={"id": "token"})
        if "Item" in response:
            return response["Item"].get("cache")
        return None
    except ClientError as e:
        print(f"Error reading token cache from DynamoDB: {e}")
        return None


def save_token_cache(cache_data):
    try:
        table.put_item(Item={"id": "token", "cache": cache_data})
    except ClientError as e:
        print(f"Error writing token cache to DynamoDB: {e}")


def authenticate_microsoft():
    cache = msal.SerializableTokenCache()

    cached_data = get_token_cache()
    if cached_data:
        cache.deserialize(cached_data)

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if accounts:
        print("Using cached Microsoft credentials from DynamoDB.")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            if cache.has_state_changed:
                save_token_cache(cache.serialize())
            return result["access_token"]

    raise Exception(
        "No valid cached Microsoft token found. Run setup_token_interactive.py locally."
    )


def obvious_rule_delete(sender, subject, preview):
    text = f"{sender}\n{subject}\n{preview}".lower()
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()

    if any(re.search(pattern, text, re.I) for pattern in OBVIOUS_DELETE_PATTERNS):
        return True

    if sender_l.endswith(".onmicrosoft.com") and any(
        word in subject_l
        for word in [
            "reward",
            "bonus",
            "spin",
            "spins",
            "casino",
            "jackpot",
            "payout",
            "payment code",
            "win big",
        ]
    ):
        return True

    return False


def extract_model_text(data):
    try:
        message = data["result"]["choices"][0]["message"]
    except Exception:
        return ""

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    return ""


def parse_decision(text):
    t = (text or "").strip()

    if t in ("1", "0"):
        return t

    matches = re.findall(
        r"(?:final answer|answer|decision)\s*[:\-]?\s*([01])\b",
        t,
        re.I,
    )
    if matches:
        return matches[-1]

    tokens = re.findall(r"\b[01]\b", t)
    if tokens:
        return tokens[-1]

    return None


def cloudflare_delete_decision(email):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        print("Missing Cloudflare environment variables. Keeping email.")
        return False, "CF_ENV_MISSING"

    sender = email.get("sender", "")
    subject = email.get("subject", "")
    preview = email.get("preview", "")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_MODEL}"
    )

    system = (
        "You classify emails already in the Junk folder. "
        "Return a final answer of exactly one character: 1 or 0. "
        "1 means DELETE. 0 means KEEP. "
        "Think as needed, then end with Final answer: 1 or Final answer: 0."
    )

    user = f"""Delete only obvious junk:
- phishing, scams, casino/gambling promos
- fake giveaways
- malware/fraud
- fake payout, reward, jackpot, or payment hooks
- deceptive account/security/storage alerts
- clearly fake senders
- fake reply-chain bait: messages pretending to be part of an existing conversation

Keep:
- real newsletters
- marketing from real games, ecommerce shops, creators, local businesses, or known brands
- job/recruiter mail
- service notices
- financial updates
- Microsoft Rewards promos when the sender matches Microsoft

When unsure, keep.

Email:
FROM: {sender}
SUBJECT: {subject}
PREVIEW: {preview[:500]}

End with Final answer: 1 or Final answer: 0."""

    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 2048,
    }

    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"},
            json=payload,
            timeout=90,
        )
    except Exception as e:
        print(f"Cloudflare request error: {e}")
        return False, "CF_REQUEST_ERROR"

    if response.status_code != 200:
        print(f"Cloudflare error HTTP {response.status_code}: {response.text[:500]}")
        return False, "CF_HTTP_ERROR"

    data = response.json()
    text = extract_model_text(data)
    decision = parse_decision(text)

    if decision is None:
        print("Could not parse Cloudflare response. Keeping email.")
        print(json.dumps(data)[:1000])
        return False, "PARSE_ERROR"

    return decision == "1", decision


def get_deletion_decision(email):
    sender = email.get("sender", "")
    subject = email.get("subject", "")
    preview = email.get("preview", "")

    if obvious_rule_delete(sender, subject, preview):
        return True, "RULE_DELETE"

    return cloudflare_delete_decision(email)


def get_junk_folder_id(session):
    response = session.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders",
        params={"$top": 100},
    )
    response.raise_for_status()

    folders = response.json().get("value", [])
    junk = next(
        (
            f
            for f in folders
            if f.get("displayName", "").lower() in ("junk email", "junk")
        ),
        None,
    )

    if not junk:
        return None

    return junk["id"]


def get_recent_junk_messages(session, junk_id, count=5):
    response = session.get(
        f"https://graph.microsoft.com/v1.0/me/mailFolders/{quote(junk_id, safe='')}/messages",
        params={
            "$top": count,
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,bodyPreview,receivedDateTime",
        },
    )
    response.raise_for_status()
    return response.json().get("value", [])


def load_seen_email_ids():
    try:
        response = table.get_item(Key={"id": "seen-emails"})
        if "Item" in response:
            return set(response["Item"].get("email_ids", []))
    except Exception as e:
        print(f"Seen-email cache read failed: {e}")

    return set()


def save_seen_email_ids(ids):
    try:
        capped = list(ids)[-100:]
        table.put_item(Item={"id": "seen-emails", "email_ids": capped})
    except Exception as e:
        print(f"Seen-email cache update failed: {e}")


def delete_message(session, message_id):
    response = session.delete(
        f"https://graph.microsoft.com/v1.0/me/messages/{quote(message_id, safe='')}"
    )
    return response.status_code == 204, response.status_code, response.text[:300]


def process_webhook_notification(notification):
    token = authenticate_microsoft()

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    resource = notification.get("resource")
    if not resource:
        print("No resource in notification.")
        return {"processed": 0, "deleted": 0}

    junk_id = get_junk_folder_id(session)
    if not junk_id:
        print("No Junk Email folder found.")
        return {"processed": 0, "deleted": 0}

    messages = get_recent_junk_messages(session, junk_id, count=5)
    if not messages:
        print("No recent junk messages found.")
        return {"processed": 0, "deleted": 0}

    previous_ids = load_seen_email_ids()
    current_ids = set(previous_ids)

    processed = 0
    deleted = 0

    for msg in messages:
        message_id = msg.get("id")
        if not message_id:
            continue

        current_ids.add(message_id)

        if message_id in previous_ids:
            print(f"Skipping already-processed email: {message_id}")
            continue

        email = {
            "id": message_id,
            "subject": msg.get("subject", ""),
            "sender": (msg.get("from") or {})
            .get("emailAddress", {})
            .get("address", ""),
            "preview": msg.get("bodyPreview", ""),
            "received": msg.get("receivedDateTime", ""),
        }

        processed += 1

        print(f"Processing: {email['sender']} - {email['subject']}")

        should_delete, decision = get_deletion_decision(email)
        print(f"Decision: {decision}")

        if should_delete:
            ok, status, body = delete_message(session, message_id)
            if ok:
                deleted += 1
                print("Deleted successfully.")
            else:
                print(f"Delete failed: HTTP {status} {body}")
        else:
            print("Keeping email.")

    save_seen_email_ids(current_ids)

    return {"processed": processed, "deleted": deleted}


def lambda_handler(event, context):
    print("=== RAW EVENT START ===")
    print(json.dumps(event))
    print("=== RAW EVENT END ===")

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

    try:
        body = json.loads(event.get("body", "{}"))
        notifications = body.get("value", [])

        if not notifications:
            print("No notifications in request body.")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No notifications"}),
            }

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
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
