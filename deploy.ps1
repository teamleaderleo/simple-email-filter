# Lambda deployment script for Windows
# Usage: .\deploy.ps1

$ErrorActionPreference = "Stop"

# Disable AWS CLI pager
$env:AWS_PAGER = ""

$FunctionName = "email-junk-filter"
$Runtime = "python3.11"
$Handler = "lambda_function.lambda_handler"
$Region = "us-east-1"
$RoleName = "email-filter-lambda-role"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Deploying Email Filter to AWS Lambda" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Load environment variables from .env file
if (Test-Path .env) {
  Get-Content .env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
      Set-Item -Path "env:$($matches[1])" -Value $matches[2]
    }
  }
}

$ClientId = $env:CLIENT_ID
$OpenAiKey = $env:OPENAI_API_KEY

if (-not $ClientId -or -not $OpenAiKey) {
  Write-Host "ERROR: CLIENT_ID and OPENAI_API_KEY must be set in .env file" -ForegroundColor Red
  exit 1
}

# Create deployment package
Write-Host "Creating deployment package..." -ForegroundColor Yellow
if (Test-Path package) { 
  Remove-Item -Recurse -Force package 
}
if (Test-Path lambda-package.zip) { 
  Remove-Item lambda-package.zip 
}
New-Item -ItemType Directory -Path package | Out-Null

# Install dependencies
Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt -t package/ --quiet

# Copy lambda function
Copy-Item lambda_function.py package/

# Create zip
Write-Host "Creating deployment package..." -ForegroundColor Yellow
Compress-Archive -Path package\* -DestinationPath lambda-package.zip

Write-Host "✓ Package created (lambda-package.zip)" -ForegroundColor Green

# Get AWS account ID
$AccountId = (aws sts get-caller-identity --query Account --output text)

# Check if IAM role exists
Write-Host "Checking IAM role..." -ForegroundColor Yellow
aws iam get-role --role-name $RoleName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "✓ IAM role already exists" -ForegroundColor Green
}
else {
  Write-Host "Creating IAM role..." -ForegroundColor Yellow
    
  # Trust policy
  $trustPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
"@
  Set-Content -Path trust-policy.json -Value $trustPolicy -Encoding utf8
    
  aws iam create-role --role-name $RoleName --assume-role-policy-document file://trust-policy.json
  aws iam attach-role-policy --role-name $RoleName --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
    
  # Secrets Manager policy
  $secretsPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:UpdateSecret",
        "secretsmanager:CreateSecret"
      ],
      "Resource": "arn:aws:secretsmanager:$($Region):*:secret:email-filter-token-cache*"
    }
  ]
}
"@
  Set-Content -Path secrets-policy.json -Value $secretsPolicy -Encoding utf8
    
  aws iam put-role-policy --role-name $RoleName --policy-name SecretsManagerAccess --policy-document file://secrets-policy.json
    
  Write-Host "✓ IAM role created, waiting 10 seconds for propagation..." -ForegroundColor Green
  Start-Sleep -Seconds 10
    
  Remove-Item trust-policy.json, secrets-policy.json
}

# Get role ARN
$RoleArn = (aws iam get-role --role-name $RoleName --query 'Role.Arn' --output text)

# Check if function exists
Write-Host "Checking Lambda function..." -ForegroundColor Yellow
aws lambda get-function --function-name $FunctionName --region $Region 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "Updating existing Lambda function..." -ForegroundColor Yellow
  aws lambda update-function-code --function-name $FunctionName --zip-file fileb://lambda-package.zip --region $Region | Out-Null
    
  Write-Host "Updating environment variables..." -ForegroundColor Yellow
  aws lambda update-function-configuration `
    --function-name $FunctionName `
    --environment "Variables={CLIENT_ID=$ClientId,OPENAI_API_KEY=$OpenAiKey}" `
    --region $Region | Out-Null
}
else {
  Write-Host "Creating new Lambda function..." -ForegroundColor Yellow
  aws lambda create-function `
    --function-name $FunctionName `
    --runtime $Runtime `
    --role $RoleArn `
    --handler $Handler `
    --zip-file fileb://lambda-package.zip `
    --timeout 60 `
    --memory-size 256 `
    --environment "Variables={CLIENT_ID=$ClientId,OPENAI_API_KEY=$OpenAiKey}" `
    --region $Region | Out-Null
}

Write-Host "✓ Lambda function deployed" -ForegroundColor Green

# Create EventBridge schedule
Write-Host "Setting up scheduled execution (every 15 minutes)..." -ForegroundColor Yellow
$RuleName = "email-filter-schedule"

aws events describe-rule --name $RuleName --region $Region 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "✓ Schedule already exists" -ForegroundColor Green
}
else {
  aws events put-rule `
    --name $RuleName `
    --schedule-expression "rate(15 minutes)" `
    --state ENABLED `
    --region $Region | Out-Null
    
  # Add permission for EventBridge to invoke Lambda
  aws lambda add-permission `
    --function-name $FunctionName `
    --statement-id AllowEventBridgeInvoke `
    --action lambda:InvokeFunction `
    --principal events.amazonaws.com `
    --source-arn "arn:aws:events:$($Region):$($AccountId):rule/$RuleName" `
    --region $Region 2>$null | Out-Null
    
  # Add Lambda as target
  aws events put-targets `
    --rule $RuleName `
    --targets "Id=1,Arn=arn:aws:lambda:$($Region):$($AccountId):function:$FunctionName" `
    --region $Region | Out-Null
    
  Write-Host "✓ Scheduled to run every 15 minutes" -ForegroundColor Green
}

# Cleanup
Remove-Item -Recurse -Force package

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Deployment complete! 🎉" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Function: $FunctionName"
Write-Host "Region: $Region"
Write-Host "Schedule: Every 15 minutes"
Write-Host ""
Write-Host "Test it now:" -ForegroundColor Yellow
Write-Host "aws lambda invoke --function-name $FunctionName --region $Region output.json" -ForegroundColor Cyan
Write-Host "Get-Content output.json" -ForegroundColor Cyan