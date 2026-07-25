import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import quote, unquote

import boto3
import msal
import requests
from botocore.exceptions import ClientError

CLIENT_ID = os.environ.get("CLIENT_ID")
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_MODEL = os.environ.get("CLOUDFLARE_MODEL", "@cf/google/gemma-4-26b-a4b-it")

TABLE_NAME = os.environ.get("TOKEN_TABLE_NAME", "email-filter-tokens")
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
        "https://api.cloudflare.com/client/v4/accounts/"
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


def load_subscription_record():
    response = table.get_item(Key={"id": "webhook-subscription"})
    record = response.get("Item") or {}
    if not record.get("subscription_id") or not record.get("client_state"):
        raise RuntimeError(
            "Webhook subscription security record is missing or incomplete."
        )
    return record


def is_notification_authentic(
    notification,
    expected_client_state,
    expected_subscription_id=None,
):
    if not expected_client_state:
        return False

    received_state = str(notification.get("clientState") or "")
    if not hmac.compare_digest(received_state, str(expected_client_state)):
        return False

    if expected_subscription_id:
        received_subscription = str(notification.get("subscriptionId") or "")
        if not hmac.compare_digest(
            received_subscription,
            str(expected_subscription_id),
        ):
            return False

    return True


def extract_message_id(notification):
    resource_data = notification.get("resourceData") or {}
    message_id = resource_data.get("id")
    if message_id:
        return str(message_id)

    resource = str(notification.get("resource") or "")
    for pattern in (r"/messages/([^/?]+)", r"/messages\('([^']+)'\)"):
        match = re.search(pattern, resource, re.I)
        if match:
            return unquote(match.group(1))

    return None


def get_junk_folder_id(session):
    response = session.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders/junkemail",
        params={"$select": "id"},
        timeout=30,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json().get("id")


def get_message(session, message_id, max_attempts=3):
    url = (
        "https://graph.microsoft.com/v1.0/me/messages/"
        f"{quote(message_id, safe='')}"
    )
    for attempt in range(1, max_attempts + 1):
        response = session.get(
            url,
            params={
                "$select": (
                    "id,subject,from,bodyPreview,receivedDateTime,parentFolderId"
                )
            },
            timeout=30,
        )
        if response.status_code == 404:
            return None
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt < max_attempts:
                retry_after = response.headers.get("Retry-After")
                delay = (
                    int(retry_after)
                    if retry_after and retry_after.isdigit()
                    else 2 ** (attempt - 1)
                )
                time.sleep(min(delay, 8))
                continue
        response.raise_for_status()
        return response.json()
    return None


def _seen_key(message_id):
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    return f"seen-email#{digest}"


def already_processed(message_id):
    response = table.get_item(Key={"id": _seen_key(message_id)})
    return "Item" in response


def mark_processed(message_id, outcome):
    try:
        table.put_item(
            Item={
                "id": _seen_key(message_id),
                "outcome": outcome,
                "processed_at": int(time.time()),
                "expires_at": int(time.time()) + 30 * 24 * 60 * 60,
            }
        )
    except Exception as e:
        print(f"Seen-email write failed: {e}")


def delete_message(session, message_id, max_attempts=3):
    url = "https://graph.microsoft.com/v1.0/me/messages/" + quote(
        message_id,
        safe="",
    )
    last_response = None
    for attempt in range(1, max_attempts + 1):
        response = session.delete(url, timeout=30)
        last_response = response
        if response.status_code == 204:
            return True, response.status_code, ""
        if response.status_code != 429 and not 500 <= response.status_code < 600:
            break
        if attempt < max_attempts:
            retry_after = response.headers.get("Retry-After")
            delay = (
                int(retry_after)
                if retry_after and retry_after.isdigit()
                else 2 ** (attempt - 1)
            )
            time.sleep(min(delay, 8))

    return (
        False,
        last_response.status_code if last_response is not None else 0,
        (last_response.text[:300] if last_response is not None else "no response"),
    )


def process_webhook_notification(notification, session, junk_id):
    message_id = extract_message_id(notification)
    if not message_id:
        print("Notification did not contain a message id.")
        return {"processed": 0, "deleted": 0, "failed": 1}

    if already_processed(message_id):
        print("Skipping an already-processed notification.")
        return {"processed": 0, "deleted": 0, "failed": 0}

    message = get_message(session, message_id)
    if not message:
        print("The notified message no longer exists.")
        return {"processed": 0, "deleted": 0, "failed": 0}

    if message.get("parentFolderId") != junk_id:
        print("The notified message is no longer in Junk; leaving it alone.")
        mark_processed(message_id, "not_in_junk")
        return {"processed": 0, "deleted": 0, "failed": 0}

    email = {
        "id": message_id,
        "subject": message.get("subject", ""),
        "sender": (message.get("from") or {})
        .get("emailAddress", {})
        .get("address", ""),
        "preview": message.get("bodyPreview", ""),
        "received": message.get("receivedDateTime", ""),
    }

    print(f"Processing a Junk message from {email['sender']}.")
    should_delete, decision = get_deletion_decision(email)
    print(f"Decision: {decision}")

    if not should_delete:
        mark_processed(message_id, "kept")
        print("Keeping email.")
        return {"processed": 1, "deleted": 0, "failed": 0}

    ok, status, body = delete_message(session, message_id)
    if ok:
        mark_processed(message_id, "deleted")
        print("Deleted successfully.")
        return {"processed": 1, "deleted": 1, "failed": 0}

    print(f"Delete failed: HTTP {status} {body}")
    return {"processed": 1, "deleted": 0, "failed": 1}


def lambda_handler(event, context):
    query_params = event.get("queryStringParameters") or {}
    if "validationToken" in query_params:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": query_params["validationToken"],
        }

    try:
        raw_body = event.get("body") or {}
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        notifications = body.get("value", [])
        if not notifications:
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No notifications"}),
            }

        subscription = load_subscription_record()
        expected_client_state = subscription["client_state"]
        expected_subscription_id = subscription["subscription_id"]

        accepted = []
        rejected = 0
        for notification in notifications:
            if not is_notification_authentic(
                notification,
                expected_client_state,
                expected_subscription_id,
            ):
                rejected += 1
                print("Rejected notification with invalid subscription security values.")
                continue
            if notification.get("changeType") == "created":
                accepted.append(notification)

        if not accepted:
            return {
                "statusCode": 202,
                "body": json.dumps(
                    {
                        "message": "No accepted created notifications",
                        "rejected": rejected,
                    }
                ),
            }

        token = authenticate_microsoft()
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Prefer": 'IdType="ImmutableId"',
            }
        )
        junk_id = get_junk_folder_id(session)
        if not junk_id:
            raise RuntimeError("No Junk Email folder found")

        totals = {"processed": 0, "deleted": 0, "failed": 0}
        for notification in accepted:
            result = process_webhook_notification(notification, session, junk_id)
            for key in totals:
                totals[key] += result[key]

        summary = {
            **totals,
            "accepted": len(accepted),
            "rejected": rejected,
        }
        print(json.dumps(summary, sort_keys=True))
        return {"statusCode": 200, "body": json.dumps(summary)}

    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
