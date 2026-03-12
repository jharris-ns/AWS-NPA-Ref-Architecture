# IAM Role Setup for Testing Deployment Permissions

Guide for creating an IAM role with deployment permissions and assuming it via AWS CLI.

## Overview

This guide helps you create a dedicated IAM role with the minimum permissions required to deploy the NPA Publisher CloudFormation stack. This is useful for:
- Testing deployment permissions
- Delegating deployment access to team members
- Following least privilege principles
- Separating deployment permissions from day-to-day permissions

## Prerequisites

- AWS CLI installed and configured
- Administrative access to create IAM roles and policies
- Your AWS account ID

## Step 1: Get Your AWS Account ID

```bash
# Get your account ID
aws sts get-caller-identity --query Account --output text
```

Save this account ID - you'll need it later.

## Step 2: Create the IAM Policy

Create the deployment policy from the template:

```bash
# Navigate to project directory
cd /path/to/AWS-NPA-Ref-Architecture

# Create the IAM policy
aws iam create-policy \
  --policy-name NPAPublisherDeploymentPolicy \
  --policy-document file://templates/deployment-iam-policy.json \
  --description "Permissions required to deploy NPA Publisher CloudFormation stack"
```

**Expected output:**
```json
{
    "Policy": {
        "PolicyName": "NPAPublisherDeploymentPolicy",
        "PolicyId": "ANPAXXX...",
        "Arn": "arn:aws:iam::123456789012:policy/NPAPublisherDeploymentPolicy",
        ...
    }
}
```

**Save the ARN** - you'll use it in the next step.

## Step 3: Create the IAM Role

### Option A: Create Role for Your User (Self-Assume)

If you want to assume the role yourself:

```bash
# Get your user ARN
USER_ARN=$(aws sts get-caller-identity --query Arn --output text)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create trust policy document
cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "${USER_ARN}"
      },
      "Action": "sts:AssumeRole",
      "Condition": {}
    }
  ]
}
EOF

# Create the role
aws iam create-role \
  --role-name NPAPublisherDeploymentRole \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --description "Role for deploying NPA Publisher CloudFormation stacks"

# Clean up temp file
rm /tmp/trust-policy.json
```

### Option B: Create Role for Specific Users/Groups

If you want specific IAM users or groups to assume the role:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create trust policy for specific users
cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": [
          "arn:aws:iam::${ACCOUNT_ID}:user/alice",
          "arn:aws:iam::${ACCOUNT_ID}:user/bob"
        ]
      },
      "Action": "sts:AssumeRole",
      "Condition": {}
    }
  ]
}
EOF

# Create the role
aws iam create-role \
  --role-name NPAPublisherDeploymentRole \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --description "Role for deploying NPA Publisher CloudFormation stacks"

rm /tmp/trust-policy.json
```

### Option C: Create Role for EC2/Lambda/Service

If you want EC2 instances or Lambda functions to use this role:

```bash
cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
  --role-name NPAPublisherDeploymentRole \
  --assume-role-policy-document file:///tmp/trust-policy.json

rm /tmp/trust-policy.json
```

**Expected output:**
```json
{
    "Role": {
        "RoleName": "NPAPublisherDeploymentRole",
        "Arn": "arn:aws:iam::123456789012:role/NPAPublisherDeploymentRole",
        ...
    }
}
```

## Step 4: Attach Policy to Role

```bash
# Attach the deployment policy to the role
aws iam attach-role-policy \
  --role-name NPAPublisherDeploymentRole \
  --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/NPAPublisherDeploymentPolicy
```

Replace `YOUR-ACCOUNT-ID` with your actual AWS account ID.

## Step 5: Grant Your User Permission to Assume the Role

Your IAM user needs permission to assume the role:

```bash
# Create a policy allowing role assumption
cat > /tmp/assume-role-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::YOUR-ACCOUNT-ID:role/NPAPublisherDeploymentRole"
    }
  ]
}
EOF

# Create the policy
aws iam create-policy \
  --policy-name AssumeNPADeploymentRole \
  --policy-document file:///tmp/assume-role-policy.json

# Attach to your user (replace with your username)
aws iam attach-user-policy \
  --user-name YOUR-USERNAME \
  --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/AssumeNPADeploymentRole

rm /tmp/assume-role-policy.json
```

## Step 6: Assume the Role via CLI

### Method 1: Using aws sts assume-role (Temporary)

```bash
# Assume the role and get temporary credentials
CREDENTIALS=$(aws sts assume-role \
  --role-arn arn:aws:iam::YOUR-ACCOUNT-ID:role/NPAPublisherDeploymentRole \
  --role-session-name npa-deployment-session \
  --duration-seconds 3600)

# Extract credentials
export AWS_ACCESS_KEY_ID=$(echo $CREDENTIALS | jq -r '.Credentials.AccessKeyId')
export AWS_SECRET_ACCESS_KEY=$(echo $CREDENTIALS | jq -r '.Credentials.SecretAccessKey')
export AWS_SESSION_TOKEN=$(echo $CREDENTIALS | jq -r '.Credentials.SessionToken')

# Verify you're using the role
aws sts get-caller-identity
```

**Expected output:**
```json
{
    "UserId": "AROAXXXXXXXXX:npa-deployment-session",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/NPAPublisherDeploymentRole/npa-deployment-session"
}
```

### Method 2: Using AWS CLI Profiles (Persistent)

Add the role to your AWS CLI config:

```bash
# Edit your AWS config file
nano ~/.aws/config
```

Add this profile:

```ini
[profile npa-deployer]
role_arn = arn:aws:iam::YOUR-ACCOUNT-ID:role/NPAPublisherDeploymentRole
source_profile = default
region = us-east-1
```

Replace:
- `YOUR-ACCOUNT-ID` with your account ID
- `source_profile = default` with your actual profile name
- `region` with your deployment region

**Use the profile:**

```bash
# Use the profile for a single command
aws cloudformation describe-stacks --profile npa-deployer

# Or set it as default for the session
export AWS_PROFILE=npa-deployer

# Verify
aws sts get-caller-identity
```

### Method 3: Using a Helper Script

Create a convenience script:

```bash
cat > assume-npa-role.sh <<'EOF'
#!/bin/bash
set -e

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/NPAPublisherDeploymentRole"

echo "Assuming role: $ROLE_ARN"

CREDENTIALS=$(aws sts assume-role \
  --role-arn "$ROLE_ARN" \
  --role-session-name npa-deployment-$(date +%s) \
  --duration-seconds 3600 \
  --query 'Credentials' \
  --output json)

export AWS_ACCESS_KEY_ID=$(echo $CREDENTIALS | jq -r '.AccessKeyId')
export AWS_SECRET_ACCESS_KEY=$(echo $CREDENTIALS | jq -r '.SecretAccessKey')
export AWS_SESSION_TOKEN=$(echo $CREDENTIALS | jq -r '.SessionToken')

echo ""
echo "Role assumed successfully!"
echo "Credentials are valid for 1 hour."
echo ""
echo "Run these commands in your shell:"
echo ""
echo "export AWS_ACCESS_KEY_ID='${AWS_ACCESS_KEY_ID}'"
echo "export AWS_SECRET_ACCESS_KEY='${AWS_SECRET_ACCESS_KEY}'"
echo "export AWS_SESSION_TOKEN='${AWS_SESSION_TOKEN}'"
echo ""
echo "Or source this script: source assume-npa-role.sh"
EOF

chmod +x assume-npa-role.sh
```

**Usage:**

```bash
# Run and copy the export commands
./assume-npa-role.sh

# Or source it directly
source assume-npa-role.sh
```

## Step 7: Test the Deployment

With the role assumed, test deploying a stack:

```bash
# Verify you have the role
aws sts get-caller-identity

# Test CloudFormation validation
aws cloudformation validate-template \
  --template-body file://templates/netskope-ref-architecture-npa.yaml

# Test stack creation (dry run)
aws cloudformation create-stack \
  --stack-name test-npa-publisher \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue=test.goskope.com \
    ParameterKey=CreateNewVPC,ParameterValue=yes \
    ... \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-execute-changeset
```

## Step 8: Revoke Temporary Credentials

When you're done:

```bash
# Unset the temporary credentials
unset AWS_ACCESS_KEY_ID
unset AWS_SECRET_ACCESS_KEY
unset AWS_SESSION_TOKEN

# Verify you're back to your normal identity
aws sts get-caller-identity
```

## Troubleshooting

### Error: "User is not authorized to perform: sts:AssumeRole"

**Cause:** Your IAM user lacks permission to assume the role.

**Fix:**
```bash
# Attach the assume role policy to your user
aws iam attach-user-policy \
  --user-name YOUR-USERNAME \
  --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/AssumeNPADeploymentRole
```

### Error: "An error occurred (AccessDenied) when calling AssumeRole"

**Cause:** The role's trust policy doesn't allow your user.

**Fix:**
```bash
# Update the trust policy to include your user
aws iam update-assume-role-policy \
  --role-name NPAPublisherDeploymentRole \
  --policy-document file:///tmp/updated-trust-policy.json
```

### Error: "Role session duration must be between 900 and 43200 seconds"

**Cause:** Invalid duration specified.

**Fix:**
```bash
# Use valid duration (15 minutes to 12 hours)
aws sts assume-role \
  --role-arn arn:aws:iam::YOUR-ACCOUNT-ID:role/NPAPublisherDeploymentRole \
  --role-session-name session \
  --duration-seconds 3600  # 1 hour
```

### Permission Denied During Deployment

**Cause:** Missing permission in the policy.

**Steps:**
1. Check CloudFormation events for specific denied action
2. Update `deployment-iam-policy.json` with missing permission
3. Update the policy:
   ```bash
   aws iam create-policy-version \
     --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/NPAPublisherDeploymentPolicy \
     --policy-document file://templates/deployment-iam-policy.json \
     --set-as-default
   ```

## Security Best Practices

### 1. Enable MFA for Role Assumption

Add MFA requirement to the trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::YOUR-ACCOUNT-ID:user/YOUR-USERNAME"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "Bool": {
          "aws:MultiFactorAuthPresent": "true"
        }
      }
    }
  ]
}
```

**Assume role with MFA:**
```bash
aws sts assume-role \
  --role-arn arn:aws:iam::YOUR-ACCOUNT-ID:role/NPAPublisherDeploymentRole \
  --role-session-name session \
  --serial-number arn:aws:iam::YOUR-ACCOUNT-ID:mfa/YOUR-USERNAME \
  --token-code 123456
```

### 2. Limit Role Session Duration

Set maximum session duration on the role:

```bash
aws iam update-role \
  --role-name NPAPublisherDeploymentRole \
  --max-session-duration 3600  # 1 hour
```

### 3. Add External ID (for Cross-Account)

If assuming role from another account:

```json
{
  "Effect": "Allow",
  "Principal": {
    "AWS": "arn:aws:iam::OTHER-ACCOUNT-ID:root"
  },
  "Action": "sts:AssumeRole",
  "Condition": {
    "StringEquals": {
      "sts:ExternalId": "unique-external-id-here"
    }
  }
}
```

### 4. Restrict Source IP (Optional)

Add IP restriction to trust policy:

```json
{
  "Condition": {
    "IpAddress": {
      "aws:SourceIp": ["203.0.113.0/24", "198.51.100.0/24"]
    }
  }
}
```

### 5. Enable CloudTrail Logging

Monitor role usage:

```bash
# Query CloudTrail for role assumptions
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue=NPAPublisherDeploymentRole \
  --max-items 50
```

## Cleanup

When no longer needed:

```bash
# Detach policy from role
aws iam detach-role-policy \
  --role-name NPAPublisherDeploymentRole \
  --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/NPAPublisherDeploymentPolicy

# Delete the role
aws iam delete-role \
  --role-name NPAPublisherDeploymentRole

# Delete the policy
aws iam delete-policy \
  --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/NPAPublisherDeploymentPolicy

# Delete the assume role policy (if created)
aws iam delete-policy \
  --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/AssumeNPADeploymentRole
```

## Reference

### Quick Commands Cheat Sheet

```bash
# Get account ID
aws sts get-caller-identity --query Account --output text

# Assume role
aws sts assume-role \
  --role-arn arn:aws:iam::ACCOUNT:role/NPAPublisherDeploymentRole \
  --role-session-name session

# Check current identity
aws sts get-caller-identity

# List policies attached to role
aws iam list-attached-role-policies --role-name NPAPublisherDeploymentRole

# Get role details
aws iam get-role --role-name NPAPublisherDeploymentRole

# Test permissions
aws cloudformation validate-template \
  --template-body file://templates/netskope-ref-architecture-npa.yaml
```

## Additional Resources

- [AWS IAM Roles Documentation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles.html)
- [AWS STS AssumeRole API](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html)
- [AWS CLI Configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)
- [IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
