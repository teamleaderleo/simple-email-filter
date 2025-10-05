# Simple Automated Junk Mail Filter for Outlook

Outlook inbox rules don't affect Junk, so I had to make this!!!

AI-powered email filtering for Microsoft Outlook/Hotmail that runs in AWS Lambda! Uses GPT-5-mini to intelligently identify and delete only the worst spam while keeping legitimate emails.

## Note:

The setup instructions aren't as granular/accurate as I'd like; if you are having trouble replicating this and LLMs aren't helping enough, I can rewrite this for you. 

Just add an issue in https://github.com/teamleaderleo/simple-email-filter/issues and I'll get around to it.

## What It Does

- Scans your junk folder every 15 minutes
- Uses OpenAI's GPT-5-mini to analyze emails
- Deletes only obvious phishing/scams/malware
- Keeps legitimate newsletters, job alerts, and marketing emails
- Runs automatically in the cloud (no manual intervention)

## Cost

- **Lambda**: Free (well under 1M requests/month)
- **DynamoDB**: Free (25GB storage in free tier)
- **OpenAI API**: Free (with data sharing enabled, 10M tokens/day)
- **Total**: $0/month

## Prerequisites

- Microsoft personal account (Outlook, Hotmail, Live)
- Azure app registration with Mail.ReadWrite permissions
- AWS account with CLI configured
- OpenAI API key with data sharing enabled
- Python 3.11+
- Docker Desktop

## Project Structure

```
simple-email-filter/
├── lambda_function.py      # Main Lambda code
├── setup_token.py          # One-time token setup
├── deploy.ps1              # Deployment script (Windows)
├── requirements.txt        # Python dependencies
├── .env                    # Environment variables
└── README.md
```

## Setup

### 1. Azure App Registration

1. Go to https://portal.azure.com
2. Navigate to "App registrations" → "New registration"
3. Name: "simple email filter"
4. Supported account types: "Personal Microsoft accounts only"
5. Redirect URI: Leave blank
6. After creation, note the **Application (client) ID**
7. Go to "API permissions" → "Add a permission" → "Microsoft Graph" → "Delegated permissions"
8. Add: `Mail.ReadWrite` and `User.Read`
9. Click "Grant admin consent"

### 2. OpenAI Data Sharing (for free tokens)

1. Go to https://platform.openai.com/settings/organization/data-sharing
2. Enable data sharing for all projects
3. Verify you see "You're eligible for free daily usage"

### 3. Environment Variables

Create `.env` file:

```env
CLIENT_ID=your-azure-client-id
OPENAI_API_KEY=sk-proj-...
```

### 4. AWS Configuration

```powershell
# Install AWS CLI if needed
# Then configure credentials
aws configure
# Enter your Access Key ID, Secret Key, and region (us-east-1)
```

### 5. Initial Authentication

```powershell
python setup_token.py
```

Follow the device code prompt to authenticate. This uploads your token cache to AWS DynamoDB.

### 6. Deploy to Lambda

```powershell
# Build Linux-compatible packages with Docker
docker run --rm -v ${PWD}:/var/task python:3.11-slim pip install -r /var/task/requirements.txt -t /var/task/package/

# Package and deploy
Copy-Item lambda_function.py package/
Compress-Archive -Path package\* -DestinationPath lambda-package.zip -Force
.\deploy.ps1
```

### 7. Test

```powershell
aws lambda invoke --function-name email-junk-filter --region us-east-1 output.json
Get-Content output.json
```

## Management

### View Logs

```powershell
aws logs tail /aws/lambda/email-junk-filter --follow --region us-east-1
```

### Change Schedule

```powershell
# Every 30 minutes
aws events put-rule --name email-filter-schedule --schedule-expression "rate(30 minutes)" --region us-east-1

# Every hour
aws events put-rule --name email-filter-schedule --schedule-expression "rate(1 hour)" --region us-east-1

# Every 5 minutes
aws events put-rule --name email-filter-schedule --schedule-expression "rate(5 minutes)" --region us-east-1
```

### Manual Invocation

```powershell
aws lambda invoke --function-name email-junk-filter --region us-east-1 output.json
Get-Content output.json
```

### Update Code

After modifying `lambda_function.py`:

```powershell
Copy-Item lambda_function.py package/
Compress-Archive -Path package\* -DestinationPath lambda-package.zip -Force
aws lambda update-function-code --function-name email-junk-filter --zip-file fileb://lambda-package.zip --region us-east-1
```

## Troubleshooting

### "No valid cached token found"

The authentication token expired. Run:

```powershell
python setup_token.py
```

### "pydantic_core._pydantic_core" error

Packages were built for Windows instead of Linux. Rebuild with Docker:

```powershell
Remove-Item -Recurse -Force package
docker run --rm -v ${PWD}:/var/task python:3.11-slim pip install -r /var/task/requirements.txt -t /var/task/package/
Copy-Item lambda_function.py package/
Compress-Archive -Path package\* -DestinationPath lambda-package.zip -Force
aws lambda update-function-code --function-name email-junk-filter --zip-file fileb://lambda-package.zip --region us-east-1
```

### Microsoft "new sign-in detected" emails

Normal for the first few runs. Microsoft learns the pattern and stops sending them after a few executions.

## Uninstall

```powershell
# Remove EventBridge schedule
aws events remove-targets --rule email-filter-schedule --ids 1 --region us-east-1
aws events delete-rule --name email-filter-schedule --region us-east-1

# Delete Lambda function
aws lambda delete-function --function-name email-junk-filter --region us-east-1

# Delete DynamoDB table
aws dynamodb delete-table --table-name email-filter-tokens --region us-east-1

# Delete IAM role
aws iam delete-role-policy --role-name email-filter-lambda-role --policy-name DynamoDBAccess
aws iam detach-role-policy --role-name email-filter-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name email-filter-lambda-role
```

## How It Works

1. EventBridge triggers Lambda function every 15 minutes
2. Lambda retrieves cached auth token from DynamoDB
3. Authenticates with Microsoft Graph API
4. Fetches recent emails from junk folder
5. Sends email list to GPT-5-mini for classification
6. Deletes only the most heinous spam
7. Updates token cache in DynamoDB if refreshed

## Customizing Deletion Criteria

Edit the prompt in `lambda_function.py` function `get_deletion_decisions()` to adjust what gets deleted vs kept.
