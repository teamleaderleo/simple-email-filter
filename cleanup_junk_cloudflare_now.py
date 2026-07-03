import argparse
import os
import re
from urllib.parse import quote

import boto3
import msal
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/google/gemma-4-26b-a4b-it")

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Mail.ReadWrite"]

TABLE_NAME = "email-filter-tokens"
AWS_REGION = "us-east-2"

if not CLIENT_ID:
    raise SystemExit("Missing CLIENT_ID in .env")
if not CLOUDFLARE_ACCOUNT_ID:
    raise SystemExit("Missing CLOUDFLARE_ACCOUNT_ID in .env")
if not CLOUDFLARE_API_TOKEN:
    raise SystemExit("Missing CLOUDFLARE_API_TOKEN in .env")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
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
    resp = table.get_item(Key={"id": "token"})
    item = resp.get("Item")
    if not item:
        raise SystemExit("No Microsoft token found in DynamoDB. Run setup_token_interactive.py first.")
    return item["cache"]


def save_token_cache(cache_data):
    table.put_item(Item={"id": "token", "cache": cache_data})


def authenticate_microsoft():
    cache = msal.SerializableTokenCache()
    cache.deserialize(get_token_cache())

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if not accounts:
        raise SystemExit("No Microsoft account found in cached token.")

    result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result or "access_token" not in result:
        print("Silent Microsoft auth failed:")
        print(result)
        raise SystemExit("Re-run setup_token_interactive.py.")

    if cache.has_state_changed:
        save_token_cache(cache.serialize())

    return result["access_token"]


def get_junk_folder_id(session):
    resp = session.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders",
        params={"$top": 100},
    )
    resp.raise_for_status()

    folders = resp.json().get("value", [])
    junk = next(
        (
            f for f in folders
            if f.get("displayName", "").lower() in ("junk email", "junk")
        ),
        None,
    )

    if not junk:
        raise SystemExit("No Junk Email folder found.")

    return junk["id"]


def fetch_latest_junk(session, junk_id, count):
    messages = []

    url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{quote(junk_id, safe='')}/messages"
    params = {
        "$top": min(count, 100),
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,bodyPreview,receivedDateTime",
    }

    while url and len(messages) < count:
        resp = session.get(url, params=params)
        resp.raise_for_status()

        data = resp.json()
        messages.extend(data.get("value", []))

        url = data.get("@odata.nextLink")
        params = None

    return messages[:count]


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

    # Prefer an explicit final answer if the model rambles.
    matches = re.findall(r"(?:final answer|answer|decision)\s*[:\-]?\s*([01])\b", t, re.I)
    if matches:
        return matches[-1]

    # Last-resort parse: if the entire response contains isolated 1/0 tokens.
    tokens = re.findall(r"\b[01]\b", t)
    if tokens:
        return tokens[-1]

    return None


def cloudflare_delete_decision(sender, subject, preview):
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

    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"},
        json=payload,
        timeout=90,
    )

    if resp.status_code != 200:
        print(f"Cloudflare error HTTP {resp.status_code}: {resp.text[:500]}")
        return False, "CF_ERROR"

    data = resp.json()
    text = extract_model_text(data)
    decision = parse_decision(text)

    if decision is None:
        print("Could not parse Cloudflare response. Keeping email.")
        print(data)
        return False, "PARSE_ERROR"

    return decision == "1", decision


def save_seen_email_ids(ids):
    table.put_item(Item={"id": "seen-emails", "email_ids": list(ids)[-100:]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("count", nargs="?", type=int, default=100)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--ai-all", action="store_true", help="Use AI even for obvious rule hits.")
    args = parser.parse_args()

    token = authenticate_microsoft()

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    junk_id = get_junk_folder_id(session)
    messages = fetch_latest_junk(session, junk_id, args.count)

    print(f"Processing {len(messages)} junk emails.")
    print("Mode:", "DELETE" if args.delete else "DRY RUN")
    print(f"Model: {CLOUDFLARE_MODEL}")
    print()

    deleted = 0
    would_delete = 0
    kept = 0
    processed_ids = []

    for i, m in enumerate(messages, start=1):
        msg_id = m["id"]
        processed_ids.append(msg_id)

        sender = (
            (m.get("from") or {})
            .get("emailAddress", {})
            .get("address", "")
        )
        subject = m.get("subject", "")
        preview = m.get("bodyPreview", "")
        received = m.get("receivedDateTime", "")

        if not args.ai_all and obvious_rule_delete(sender, subject, preview):
            should_delete = True
            raw = "RULE_DELETE"
        else:
            should_delete, raw = cloudflare_delete_decision(sender, subject, preview)

        print("=" * 80)
        print(f"{i}/{len(messages)}")
        print(f"Received: {received}")
        print(f"From: {sender}")
        print(f"Subject: {subject}")
        print(f"Decision: {raw!r}")

        if should_delete:
            if args.delete:
                delete_url = f"https://graph.microsoft.com/v1.0/me/messages/{quote(msg_id, safe='')}"
                resp = session.delete(delete_url)

                if resp.status_code == 204:
                    deleted += 1
                    print("DELETED")
                else:
                    print(f"DELETE FAILED: HTTP {resp.status_code} {resp.text[:300]}")
            else:
                would_delete += 1
                print("WOULD DELETE")
        else:
            kept += 1
            print("KEEP")

    if args.delete:
        save_seen_email_ids(processed_ids)

    print()
    print("=" * 80)
    if args.delete:
        print(f"SUMMARY: {deleted} deleted, {kept} kept, {len(messages)} processed")
        print("Updated seen-emails cache, capped at 100.")
    else:
        print(f"DRY RUN SUMMARY: {would_delete} would delete, {kept} would keep, {len(messages)} processed")
    print("=" * 80)


if __name__ == "__main__":
    main()
