import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
api_token = os.getenv("CLOUDFLARE_API_TOKEN")
model = os.getenv("CLOUDFLARE_MODEL", "@cf/google/gemma-4-26b-a4b-it")

if not account_id:
    raise SystemExit("Missing CLOUDFLARE_ACCOUNT_ID in .env")
if not api_token:
    raise SystemExit("Missing CLOUDFLARE_API_TOKEN in .env")

url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

payload = {
    "messages": [
        {
            "role": "system",
            "content": "You are a binary classifier. Reply with exactly one character: 1 or 0.",
        },
        {
            "role": "user",
            "content": (
                "Email:\n"
                "FROM: scammer@example.com\n"
                "SUBJECT: 400 FREE SPINS NO DEPOSIT REQUIRED\n"
                "PREVIEW: Claim your casino bonus now.\n\n"
                "Return 1 to delete obvious junk, 0 to keep."
            ),
        },
    ]
}

resp = requests.post(
    url,
    headers={"Authorization": f"Bearer {api_token}"},
    json=payload,
    timeout=60,
)

print("Status:", resp.status_code)
print(json.dumps(resp.json(), indent=2))
