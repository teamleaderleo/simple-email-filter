# Deploy webhook-based email filter system to AWS
# This script creates:
# 1. API Gateway for receiving webhooks
# 2. Webhook handler Lambda
# 3. Subscription manager Lambda (runs every 2 days)
# 4. EventBridge rule for subscription renewal

$ErrorActionPreference = "Stop"

# Load environment variables
if (Test-Path .env) {
  Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*?)\s*=\s*(.*?)\s*$') {
      $name = $matches[1]
      $value = $matches[2]
      [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
  }
}

$CLIENT_ID = $env:CLIENT_ID
$OPENAI_API_KEY = $env:OPENAI_API_KEY
$REGION = "us-east-1"

if (-not $CLIENT_ID -or -not $OPENAI_API_KEY) {
  Write-Host "❌ Error: CLIENT_ID and OPENAI_API_KEY must be set in .env file"
  exit 1
}

Write-Host "=== Deploying Webhook-Based Email Filter System ===" -ForegroundColor Cyan
Write-Host ""

# Check if IAM role exists, if not create it
Write-Host "Checking IAM role..." -ForegroundColor Yellow
$roleName = "email-filter-lambda-role"

aws iam get-role --role-name $roleName --region $REGION 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "✅ IAM role exists: $roleName" -ForegroundColor Green
}
else {
  Write-Host "Creating IAM role..." -ForegroundColor Yellow
    
  $trustPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
"@
    
  $trustPolicy | Out-File -FilePath trust-policy.json -Encoding utf8
  aws iam create-role --role-name $roleName --assume-role-policy-document file://trust-policy.json --region $REGION
    
  # Attach basic Lambda execution role
  aws iam attach-role-policy --role-name $roleName --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole --region $REGION
    
  # Add DynamoDB access
  $dynamoPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": "arn:aws:dynamodb:$($REGION):*:table/email-filter-tokens"
    }
  ]
}
"@
    
  $dynamoPolicy | Out-File -FilePath dynamo-policy.json -Encoding utf8
  aws iam put-role-policy --role-name $roleName --policy-name DynamoDBAccess --policy-document file://dynamo-policy.json --region $REGION
    
  Write-Host "✅ Created IAM role: $roleName" -ForegroundColor Green
  Write-Host "Waiting 10 seconds for IAM role to propagate..." -ForegroundColor Yellow
  Start-Sleep -Seconds 10
}

# Get account ID for ARN
$accountId = aws sts get-caller-identity --query Account --output text
$roleArn = "arn:aws:iam::$($accountId):role/$roleName"

# Package webhook handler Lambda
Write-Host ""
Write-Host "Packaging webhook handler Lambda..." -ForegroundColor Yellow

# Check if we need to repackage
$needsRepackage = $true
if (Test-Path "webhook-lambda.zip") {
  $zipTime = (Get-Item "webhook-lambda.zip").LastWriteTime
  $pyTime = (Get-Item "webhook_handler.py").LastWriteTime
  if ($zipTime -gt $pyTime) {
    Write-Host "✅ Using existing webhook-lambda.zip (up to date)" -ForegroundColor Green
    $needsRepackage = $false
  }
}

if ($needsRepackage) {
  if (-not (Test-Path "webhook-package")) {
    New-Item -ItemType Directory -Path "webhook-package" | Out-Null
  }

  Copy-Item webhook_handler.py webhook-package/lambda_function.py -Force

  # Copy existing package dependencies
  if (Test-Path "package") {
    Copy-Item -Recurse package/* webhook-package/ -Force
  }
  else {
    Write-Host "❌ No package directory found. Run the original deploy script first to install dependencies."
    exit 1
  }

  Write-Host "Compressing (~30MB, this takes a minute)..." -ForegroundColor Yellow
  Compress-Archive -Path webhook-package\* -DestinationPath webhook-lambda.zip -Force
  Write-Host "✅ Webhook handler packaged" -ForegroundColor Green
}

# Deploy webhook handler Lambda
Write-Host ""
Write-Host "Deploying webhook handler Lambda..." -ForegroundColor Yellow

aws lambda get-function --function-name email-webhook-handler --region $REGION 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "Updating existing Lambda function..." -ForegroundColor Yellow
  aws lambda update-function-code --function-name email-webhook-handler --zip-file fileb://webhook-lambda.zip --region $REGION
  aws lambda update-function-configuration --function-name email-webhook-handler --timeout 60 --memory-size 256 --environment "Variables={CLIENT_ID=$CLIENT_ID,OPENAI_API_KEY=$OPENAI_API_KEY}" --region $REGION
}
else {
  Write-Host "Creating new Lambda function..." -ForegroundColor Yellow
  aws lambda create-function --function-name email-webhook-handler --runtime python3.11 --role $roleArn --handler lambda_function.lambda_handler --zip-file fileb://webhook-lambda.zip --timeout 60 --memory-size 256 --environment "Variables={CLIENT_ID=$CLIENT_ID,OPENAI_API_KEY=$OPENAI_API_KEY}" --region $REGION
}

Write-Host "✅ Webhook handler Lambda deployed" -ForegroundColor Green

# Create API Gateway
Write-Host ""
Write-Host "Setting up API Gateway..." -ForegroundColor Yellow

# Check if API exists
$apiId = (aws apigateway get-rest-apis --region $REGION --query "items[?name=='email-webhook-api'].id" --output text).Trim()

if ([string]::IsNullOrWhiteSpace($apiId)) {
  Write-Host "Creating new API Gateway..." -ForegroundColor Yellow
  $apiId = (aws apigateway create-rest-api --name email-webhook-api --description "Webhook endpoint for email filter" --region $REGION --query 'id' --output text).Trim()
    
  # Get root resource ID
  $rootId = (aws apigateway get-resources --rest-api-id $apiId --region $REGION --query 'items[0].id' --output text).Trim()
    
  # Create POST method
  aws apigateway put-method --rest-api-id $apiId --resource-id $rootId --http-method POST --authorization-type NONE --region $REGION | Out-Null
  aws apigateway put-method --rest-api-id $apiId --resource-id $rootId --http-method GET --authorization-type NONE --region $REGION | Out-Null
    
  # Set up Lambda integration
  $lambdaArn = "arn:aws:lambda:$($REGION):$($accountId):function:email-webhook-handler"
  $integrationUri = "arn:aws:apigateway:$($REGION):lambda:path/2015-03-31/functions/$lambdaArn/invocations"
    
  aws apigateway put-integration --rest-api-id $apiId --resource-id $rootId --http-method POST --type AWS_PROXY --integration-http-method POST --uri $integrationUri --region $REGION | Out-Null
  aws apigateway put-integration --rest-api-id $apiId --resource-id $rootId --http-method GET --type AWS_PROXY --integration-http-method POST --uri $integrationUri --region $REGION | Out-Null
    
  # Deploy API
  aws apigateway create-deployment --rest-api-id $apiId --stage-name prod --region $REGION | Out-Null
    
  # Grant API Gateway permission to invoke Lambda
  aws lambda add-permission --function-name email-webhook-handler --statement-id apigateway-webhook --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:$($REGION):$($accountId):$($apiId)/*/*" --region $REGION 2>$null | Out-Null
    
  Write-Host "✅ API Gateway created" -ForegroundColor Green
}
else {
  Write-Host "✅ API Gateway already exists: $apiId" -ForegroundColor Green
}

$webhookUrl = "https://$apiId.execute-api.$REGION.amazonaws.com/prod"
Write-Host ""
Write-Host "🌐 Webhook URL: $webhookUrl" -ForegroundColor Cyan

# Package subscription manager Lambda
Write-Host ""
Write-Host "Packaging subscription manager Lambda..." -ForegroundColor Yellow

# Check if we need to repackage
$needsRepackage = $true
if (Test-Path "subscription-lambda.zip") {
  $zipTime = (Get-Item "subscription-lambda.zip").LastWriteTime
  $pyTime = (Get-Item "subscription_manager.py").LastWriteTime
  if ($zipTime -gt $pyTime) {
    Write-Host "✅ Using existing subscription-lambda.zip (up to date)" -ForegroundColor Green
    $needsRepackage = $false
  }
}

if ($needsRepackage) {
  if (-not (Test-Path "subscription-package")) {
    New-Item -ItemType Directory -Path "subscription-package" | Out-Null
  }

  Copy-Item subscription_manager.py subscription-package/lambda_function.py -Force
  Copy-Item -Recurse package/* subscription-package/ -Force

  Write-Host "Compressing (~30MB, this takes a minute)..." -ForegroundColor Yellow
  Compress-Archive -Path subscription-package\* -DestinationPath subscription-lambda.zip -Force
  Write-Host "✅ Subscription manager packaged" -ForegroundColor Green
}

# Deploy subscription manager Lambda
Write-Host ""
Write-Host "Deploying subscription manager Lambda..." -ForegroundColor Yellow

aws lambda get-function --function-name email-subscription-manager --region $REGION 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "Updating existing Lambda function..." -ForegroundColor Yellow
  aws lambda update-function-code --function-name email-subscription-manager --zip-file fileb://subscription-lambda.zip --region $REGION
  aws lambda update-function-configuration --function-name email-subscription-manager --timeout 30 --memory-size 128 --environment "Variables={CLIENT_ID=$CLIENT_ID}" --region $REGION
}
else {
  Write-Host "Creating new Lambda function..." -ForegroundColor Yellow
  aws lambda create-function --function-name email-subscription-manager --runtime python3.11 --role $roleArn --handler lambda_function.lambda_handler --zip-file fileb://subscription-lambda.zip --timeout 30 --memory-size 128 --environment "Variables={CLIENT_ID=$CLIENT_ID}" --region $REGION
}

Write-Host "✅ Subscription manager Lambda deployed" -ForegroundColor Green

# Create EventBridge rule for every 2 days
Write-Host ""
Write-Host "Setting up EventBridge rule (every 2 days)..." -ForegroundColor Yellow

aws events put-rule --name email-subscription-renewal --schedule-expression "rate(2 days)" --region $REGION | Out-Null

$lambdaArn = "arn:aws:lambda:$($REGION):$($accountId):function:email-subscription-manager"
aws events put-targets --rule email-subscription-renewal --targets "Id=1,Arn=$lambdaArn" --region $REGION | Out-Null

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission --function-name email-subscription-manager --statement-id eventbridge-renewal --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn "arn:aws:events:$($REGION):$($accountId):rule/email-subscription-renewal" --region $REGION 2>$null | Out-Null

Write-Host "✅ EventBridge rule created (runs every 2 days)" -ForegroundColor Green

# Clean up temporary files
Remove-Item webhook-lambda.zip -ErrorAction SilentlyContinue
Remove-Item subscription-lambda.zip -ErrorAction SilentlyContinue
Remove-Item trust-policy.json -ErrorAction SilentlyContinue
Remove-Item dynamo-policy.json -ErrorAction SilentlyContinue
Remove-Item -Recurse webhook-package -ErrorAction SilentlyContinue
Remove-Item -Recurse subscription-package -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Deployment Complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Run: python setup_webhook.py" -ForegroundColor White
Write-Host "   When prompted, enter this URL: $webhookUrl" -ForegroundColor White
Write-Host ""
Write-Host "2. Test by sending spam to your Outlook junk folder" -ForegroundColor White
Write-Host ""
Write-Host "3. Monitor logs:" -ForegroundColor White
Write-Host "   aws logs tail /aws/lambda/email-webhook-handler --follow --region $REGION" -ForegroundColor Gray
Write-Host ""
Write-Host "Architecture:" -ForegroundColor Cyan
Write-Host "  • Webhook handler: email-webhook-handler (triggered by emails)" -ForegroundColor White
Write-Host "  • Subscription renewal: email-subscription-manager (every 2 days)" -ForegroundColor White
Write-Host "  • API Gateway endpoint: $webhookUrl" -ForegroundColor White
Write-Host ""