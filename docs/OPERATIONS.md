# NPA Publisher Operational Procedures

Operational procedures for managing NPA Publisher deployments on AWS.

## Table of Contents

- [Replace Publisher in Specific AZ](#replace-publisher-in-specific-az)
- [Update Publisher AMI Version](#update-publisher-ami-version)
- [Scale Up/Down Instance Type](#scale-updown-instance-type)
- [Rotate API Token](#rotate-api-token)
- [Manual Publisher Registration](#manual-publisher-registration)
- [Backup and Restore](#backup-and-restore)
- [Monitoring and Alerts](#monitoring-and-alerts)

## Replace Publisher in Specific AZ

Use this procedure when you need to replace a publisher instance in a specific availability zone (e.g., for maintenance, troubleshooting, or instance corruption).

### Method 1: Using PublisherReplacementTrigger Parameter (Recommended)

This is the cleanest method as CloudFormation manages the entire lifecycle.

**Step 1: Increment the PublisherReplacementTrigger parameter**

```bash
# Get current parameter value
CURRENT_TRIGGER=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Parameters[?ParameterKey==`PublisherReplacementTrigger`].ParameterValue' \
  --output text)

# Calculate new trigger value (increment by 1)
NEW_TRIGGER=$((CURRENT_TRIGGER + 1))

echo "Current trigger value: $CURRENT_TRIGGER"
echo "New trigger value: $NEW_TRIGGER"

# Update stack with new trigger value
aws cloudformation update-stack \
  --stack-name netskope-npa-publisher \
  --use-previous-template \
  --parameters \
    ParameterKey=PublisherReplacementTrigger,ParameterValue=$NEW_TRIGGER \
    ParameterKey=NetskopeTenantFQDN,UsePreviousValue=true \
    ParameterKey=CreateNewVPC,UsePreviousValue=true \
    ParameterKey=VPCCIDR,UsePreviousValue=true \
    ParameterKey=PublicSubnetCIDR,UsePreviousValue=true \
    ParameterKey=PrivateSubnetCIDR,UsePreviousValue=true \
    ParameterKey=PublicSubnetCIDR2,UsePreviousValue=true \
    ParameterKey=PrivateSubnetCIDR2,UsePreviousValue=true \
    ParameterKey=AvailabilityZone,UsePreviousValue=true \
    ParameterKey=AvailabilityZone2,UsePreviousValue=true \
    ParameterKey=ExistingVPC,UsePreviousValue=true \
    ParameterKey=ExistingPrivateSubnet,UsePreviousValue=true \
    ParameterKey=ExistingPrivateSubnet2,UsePreviousValue=true \
    ParameterKey=NPAPublisherGroupName,UsePreviousValue=true \
    ParameterKey=NPAPublisherAMIId,UsePreviousValue=true \
    ParameterKey=NPAPublisherKey,UsePreviousValue=true \
    ParameterKey=NPAPublisherInstanceType,UsePreviousValue=true \
    ParameterKey=NetskopeAPIToken,UsePreviousValue=true \
    ParameterKey=ProvisionNewAPIToken,UsePreviousValue=true \
    ParameterKey=ExistingNetskopeAPITokenARN,UsePreviousValue=true \
    ParameterKey=LambdaS3Bucket,UsePreviousValue=true \
    ParameterKey=LambdaS3Key,UsePreviousValue=true \
    ParameterKey=CostCenterTag,UsePreviousValue=true \
    ParameterKey=ProjectTag,UsePreviousValue=true \
    ParameterKey=EnvironmentTag,UsePreviousValue=true \
  --capabilities CAPABILITY_NAMED_IAM
```

**What happens:**
1. CloudFormation detects the parameter change
2. Custom resources are marked for replacement
3. Lambda executes and deletes old publishers from Netskope
4. New EC2 instances are created
5. Lambda registers new publishers with Netskope
6. Lambda updates private apps to use new publishers
7. Old instances are terminated

**Step 2: Monitor the replacement**

```bash
# Watch stack events
aws cloudformation describe-stack-events \
  --stack-name netskope-npa-publisher \
  --max-items 20 \
  --query 'StackEvents[*].[Timestamp,ResourceStatus,ResourceType,ResourceStatusReason]' \
  --output table

# Watch Lambda logs
aws logs tail /aws/lambda/<PublisherGroupName>-RegistrationHandler --follow
```

**Step 3: Verify new publishers**

```bash
# Check in Netskope UI
# Settings → Security Cloud Platform → Publishers
# You should see new publishers with updated instance IDs

# Or via API
TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id NetskopeAPIToken-<PublisherGroupName> \
  --query SecretString \
  --output text | jq -r '.NetskopeAPIToken')

curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers \
     | jq '.data.publishers[] | {publisher_name, registered_hostname, status}'
```

### Method 2: Using AWS Console

1. Go to AWS CloudFormation Console
2. Select your stack: `netskope-npa-publisher`
3. Click **Update**
4. Choose **Use current template**
5. Find **PublisherReplacementTrigger** parameter
6. Change the value (e.g., from `1` to `2`)
7. Click **Next** through remaining steps
8. Check **I acknowledge that AWS CloudFormation might create IAM resources**
9. Click **Update stack**
10. Monitor the **Events** tab for progress

### Replacement Timeline

```
t=0m    Stack update initiated
        └─ Parameter change detected

t=1m    Custom resources marked for replacement
        └─ Lambda triggered for DELETE on old publishers

t=2m    Old publishers deleted from Netskope
        └─ Private apps unassigned from old publishers

t=3m    New EC2 instances launched
        └─ SSM Agent starts

t=4m    Lambda triggered for CREATE on new publishers
        └─ Publisher created in Netskope
        └─ Registration token obtained

t=5-7m  Lambda waits for SSM agent online
        └─ Registration command sent

t=8m    Publishers registered successfully
        └─ Private apps updated

t=9m    Old instances terminated
        └─ Stack update complete ✅
```

## Update Publisher AMI Version

Update to a new NPA Publisher AMI version.

### Step 1: Get new AMI ID

```bash
# Find latest AMI in your region
bash scripts/get-ami.sh us-east-1
```

### Step 2: Update stack with new AMI

```bash
NEW_AMI="ami-xxxxxxxxx"  # From step 1

aws cloudformation update-stack \
  --stack-name netskope-npa-publisher \
  --use-previous-template \
  --parameters \
    ParameterKey=NPAPublisherAMIId,ParameterValue=$NEW_AMI \
    ParameterKey=NetskopeTenantFQDN,UsePreviousValue=true \
    ParameterKey=CreateNewVPC,UsePreviousValue=true \
    ParameterKey=NPAPublisherGroupName,UsePreviousValue=true \
    ParameterKey=NPAPublisherKey,UsePreviousValue=true \
    ParameterKey=NPAPublisherInstanceType,UsePreviousValue=true \
    ParameterKey=NetskopeAPIToken,UsePreviousValue=true \
    ParameterKey=LambdaS3Bucket,UsePreviousValue=true \
    ParameterKey=LambdaS3Key,UsePreviousValue=true \
    ParameterKey=PublisherReplacementTrigger,UsePreviousValue=true \
    # ... (other parameters with UsePreviousValue=true)
  --capabilities CAPABILITY_NAMED_IAM
```

**Note**: Changing the AMI ID will trigger replacement of both instances.

## Scale Up/Down Instance Type

Change the EC2 instance type for performance tuning.

### Supported Instance Types

- `t3.medium` - Light workloads (up to 100 concurrent connections)
- `t3.large` - Standard workloads (100-500 connections) **[Default]**
- `t3.xlarge` - Heavy workloads (500-1000 connections)
- `t3.2xlarge` - Very heavy workloads (1000+ connections)

### Update Procedure

```bash
NEW_INSTANCE_TYPE="t3.xlarge"

aws cloudformation update-stack \
  --stack-name netskope-npa-publisher \
  --use-previous-template \
  --parameters \
    ParameterKey=NPAPublisherInstanceType,ParameterValue=$NEW_INSTANCE_TYPE \
    ParameterKey=NetskopeTenantFQDN,UsePreviousValue=true \
    ParameterKey=NPAPublisherGroupName,UsePreviousValue=true \
    # ... (other parameters with UsePreviousValue=true)
  --capabilities CAPABILITY_NAMED_IAM
```

**Impact**: Both instances will be replaced with new instance type. Minimal downtime as replacement happens one at a time in multi-AZ deployments.

## Rotate API Token

Rotate the Netskope API token stored in Secrets Manager.

### Step 1: Generate new token in Netskope UI

1. Log in to Netskope tenant
2. Go to **Settings → Tools → REST API v2**
3. Click **New Token**
4. Name: `NPA-Publisher-Rotated-<Date>`
5. Select scopes: **Infrastructure Management**, **Private Applications**
6. Copy the token

### Step 2: Update Secrets Manager

```bash
# Get secret name
SECRET_NAME=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Parameters[?ParameterKey==`NPAPublisherGroupName`].ParameterValue' \
  --output text)

SECRET_NAME="NetskopeAPIToken-${SECRET_NAME}"

# Update secret with new token
NEW_TOKEN="your-new-api-token-here"

aws secretsmanager update-secret \
  --secret-id $SECRET_NAME \
  --secret-string "{\"NetskopeAPIToken\":\"$NEW_TOKEN\"}"
```

### Step 3: Test token

```bash
# Verify token works
TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id $SECRET_NAME \
  --query SecretString \
  --output text | jq -r '.NetskopeAPIToken')

curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers
```

**Note**: Existing publishers continue working with the new token. No stack update needed unless you want to force re-registration.

### Step 4: Revoke old token (optional)

1. Go to **Settings → Tools → REST API v2** in Netskope UI
2. Find the old token
3. Click **Revoke**

## Manual Publisher Registration

Manually register a publisher if automatic registration failed.

### Step 1: Get publisher registration token

```bash
# Get API token
TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id NetskopeAPIToken-<PublisherGroupName> \
  --query SecretString \
  --output text | jq -r '.NetskopeAPIToken')

# Get instance ID
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId`].OutputValue' \
  --output text)

# Get account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create publisher name
PUBLISHER_NAME="<PublisherGroupName>-${ACCOUNT_ID}-${INSTANCE_ID}"

# Create publisher and get registration token
RESPONSE=$(curl -X POST \
  -H "Netskope-Api-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"publisher_name\":\"$PUBLISHER_NAME\"}" \
  https://mytenant.goskope.com/api/v2/infrastructure/publishers)

# Extract registration token
REG_TOKEN=$(echo $RESPONSE | jq -r '.data.registration_token')
echo "Registration token: $REG_TOKEN"
```

### Step 2: Register on instance via SSM

```bash
# Send registration command
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --comment "Manual NPA publisher registration" \
  --parameters "commands=['/home/ubuntu/npa_publisher_wizard -token \"$REG_TOKEN\"']" \
  --timeout-seconds 300

# Get command ID from output
COMMAND_ID="<command-id-from-above>"

# Wait and check status
sleep 60

aws ssm get-command-invocation \
  --command-id $COMMAND_ID \
  --instance-id $INSTANCE_ID \
  --query '[StandardOutputContent,StandardErrorContent]' \
  --output text
```

### Step 3: Verify registration

```bash
# Check publisher status
curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers \
     | jq ".data.publishers[] | select(.publisher_name==\"$PUBLISHER_NAME\")"
```

## Backup and Restore

### Backup Configuration

```bash
# Export stack configuration
aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --output json > stack-backup-$(date +%Y%m%d).json

# Export template
aws cloudformation get-template \
  --stack-name netskope-npa-publisher \
  --query TemplateBody \
  --output text > template-backup-$(date +%Y%m%d).yaml

# Export secrets (secure location!)
aws secretsmanager get-secret-value \
  --secret-id NetskopeAPIToken-<PublisherGroupName> \
  --output json > secrets-backup-$(date +%Y%m%d).json
```

### Restore from Backup

```bash
# Restore stack from backup (creates new stack)
aws cloudformation create-stack \
  --stack-name netskope-npa-publisher-restored \
  --template-body file://template-backup-20260109.yaml \
  --parameters file://stack-backup-20260109.json \
  --capabilities CAPABILITY_NAMED_IAM
```

## Monitoring and Alerts

### CloudWatch Alarms

Create alarms for publisher health monitoring:

```bash
# Get instance IDs
INSTANCE_ID_1=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId`].OutputValue' \
  --output text)

INSTANCE_ID_2=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId2`].OutputValue' \
  --output text)

# Create CPU alarm for instance 1
aws cloudwatch put-metric-alarm \
  --alarm-name "NPA-Publisher-1-HighCPU" \
  --alarm-description "Alert when CPU exceeds 80%" \
  --metric-name CPUUtilization \
  --namespace AWS/EC2 \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --dimensions Name=InstanceId,Value=$INSTANCE_ID_1

# Create status check alarm
aws cloudwatch put-metric-alarm \
  --alarm-name "NPA-Publisher-1-StatusCheck" \
  --alarm-description "Alert when status check fails" \
  --metric-name StatusCheckFailed \
  --namespace AWS/EC2 \
  --statistic Maximum \
  --period 60 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 2 \
  --dimensions Name=InstanceId,Value=$INSTANCE_ID_1
```

### Lambda Monitoring

```bash
# Create alarm for Lambda errors
aws cloudwatch put-metric-alarm \
  --alarm-name "NPA-Lambda-Errors" \
  --alarm-description "Alert on Lambda function errors" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --dimensions Name=FunctionName,Value=<PublisherGroupName>-RegistrationHandler
```

### Dashboard

Create a CloudWatch dashboard for visibility:

```bash
cat > dashboard.json <<'EOF'
{
  "widgets": [
    {
      "type": "metric",
      "properties": {
        "metrics": [
          ["AWS/EC2", "CPUUtilization", {"stat": "Average"}]
        ],
        "period": 300,
        "stat": "Average",
        "region": "us-east-1",
        "title": "Publisher CPU Usage"
      }
    },
    {
      "type": "metric",
      "properties": {
        "metrics": [
          ["AWS/Lambda", "Invocations", {"stat": "Sum"}],
          [".", "Errors", {"stat": "Sum"}]
        ],
        "period": 300,
        "stat": "Sum",
        "region": "us-east-1",
        "title": "Lambda Metrics"
      }
    }
  ]
}
EOF

aws cloudwatch put-dashboard \
  --dashboard-name NPA-Publisher-Dashboard \
  --dashboard-body file://dashboard.json
```

## Additional Resources

- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) - Deployment instructions
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues and solutions
- [DEVOPS-NOTES.md](DEVOPS-NOTES.md) - Technical deep-dive
- [Netskope REST API v2](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
