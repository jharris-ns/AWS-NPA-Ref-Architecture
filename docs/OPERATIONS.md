# NPA Publisher Operational Procedures

Operational procedures for managing NPA Publisher deployments on AWS.

## Table of Contents

- [Publisher Upgrades and Maintenance](#publisher-upgrades-and-maintenance)
- [Update Publisher AMI Version](#update-publisher-ami-version)
- [Scale Up/Down Instance Type](#scale-updown-instance-type)
- [Rotate API Token](#rotate-api-token)
- [Manual Publisher Registration](#manual-publisher-registration)
- [Scale Publisher Count](#scale-publisher-count)
- [Publisher Deletion Workflow](#publisher-deletion-workflow)
- [Backup and Restore](#backup-and-restore)
- [Monitoring and Alerts](#monitoring-and-alerts)

## Publisher Upgrades and Maintenance

### Publisher Auto-Updates (Recommended)

Netskope publishers support automatic upgrades managed through the Netskope console. This is the recommended method for keeping your publishers up-to-date with the latest features and security patches.

**Configure auto-updates in Netskope UI:**

1. Log in to your Netskope tenant
2. Go to **Settings → Security Cloud Platform → Publishers**
3. Select your publisher group
4. Enable **Auto-Update** and configure the maintenance window
5. Choose update schedule (e.g., weekly, monthly)

**Benefits:**
- No manual intervention required
- Minimal downtime during updates
- Automatic rollback on failure
- Controlled maintenance windows
- No infrastructure replacement needed

**Documentation:**
- [Configure Publisher Auto-Updates](https://docs.netskope.com/en/configure-publisher-auto-updates)

### Manual Publisher Replacement

If you need to replace a publisher instance for troubleshooting or to apply infrastructure changes (e.g., new AMI, different instance type), you can delete and recreate the stack or specific resources.

**Important:** This approach replaces the EC2 infrastructure. For software updates only, use Netskope auto-updates instead.

**Option 1: Replace via Stack Update (AMI or Instance Type Change)**

Changing the AMI or instance type will trigger CloudFormation to replace the EC2 instances:

```bash
# Example: Update to new AMI
NEW_AMI="ami-xxxxxxxxx"

aws cloudformation update-stack \
  --stack-name netskope-npa-publisher \
  --use-previous-template \
  --parameters \
    ParameterKey=NPAPublisherAMIId,ParameterValue=$NEW_AMI \
    ParameterKey=NetskopeTenantFQDN,UsePreviousValue=true \
    ParameterKey=NPAPublisherGroupName,UsePreviousValue=true \
    # ... (other parameters with UsePreviousValue=true)
  --capabilities CAPABILITY_NAMED_IAM
```

**Option 2: Terminate and Replace Individual Instance**

If you need to replace a single failing instance:

```bash
# Terminate the instance
aws ec2 terminate-instances --instance-ids i-xxxxxxxxx

# CloudFormation will automatically detect the termination and recreate the instance
# Monitor stack events to track the replacement
aws cloudformation describe-stack-events \
  --stack-name netskope-npa-publisher \
  --max-items 20
```

**Option 3: Delete and Recreate Stack**

For complete infrastructure refresh:

```bash
# Delete stack
aws cloudformation delete-stack --stack-name netskope-npa-publisher

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete --stack-name netskope-npa-publisher

# Recreate stack using original parameters
aws cloudformation create-stack \
  --stack-name netskope-npa-publisher \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters file://deployment-parameters.json \
  --capabilities CAPABILITY_NAMED_IAM
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

## Scale Publisher Count

The CloudFormation template is designed for copy-paste scaling. Each publisher requires two resources: an EC2 Instance and a Custom Resource (registration). Keep at least 2 publishers for high availability.

### Scale Out (Add a Publisher)

1. Open `templates/netskope-ref-architecture-npa.yaml`
2. Copy an entire "Publisher 2" block (both `NPAPublisherInstance2` and `NPAPublisherRegistration2`)
3. Rename the resources by incrementing the suffix number:
   - `NPAPublisherInstance2` -> `NPAPublisherInstance3`
   - `NPAPublisherRegistration2` -> `NPAPublisherRegistration3`
4. Update `DependsOn` in the Registration resource to include **both** the new Instance name **and** the previous Registration:
   ```yaml
   NPAPublisherRegistration3:
     Type: Custom::NPAPublisher
     DependsOn:
       - NPAPublisherInstance3
       - NPAPublisherRegistration2   # Serializes registration to avoid race conditions
   ```
   **Important:** Registrations must run sequentially because each one reads and writes the same private app definitions via the Netskope API. Without the `DependsOn` chain, concurrent registrations cause a read-modify-write race condition where one publisher's app assignment overwrites another's.
5. Set `SubnetId` to the desired subnet:
   - **New VPC:** `!Ref PrivateSubnet` or `!Ref PrivateSubnet2`
   - **Existing VPC:** `!Ref ExistingPrivateSubnet` or `!Ref ExistingPrivateSubnet2`
   - For a third AZ, add a new subnet parameter and reference it here
6. Update the `Name` tag to a unique value (e.g., `${NPAPublisherGroupName}-3`)
7. Add matching Outputs at the bottom of the template:
   ```yaml
   PublisherInstanceId3:
     Description: EC2 Instance ID of NPA Publisher 3
     Value: !Ref NPAPublisherInstance3
     Export:
       Name: !Sub '${AWS::StackName}-InstanceId3'

   PublisherPrivateIP3:
     Description: Private IP address of NPA Publisher 3
     Value: !GetAtt NPAPublisherInstance3.PrivateIp
     Export:
       Name: !Sub '${AWS::StackName}-PrivateIP3'
   ```
8. Deploy the updated template:

```bash
aws cloudformation update-stack \
  --stack-name netskope-npa-publisher \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,UsePreviousValue=true \
    ParameterKey=NPAPublisherGroupName,UsePreviousValue=true \
    ParameterKey=NPAPublisherAMIId,UsePreviousValue=true \
    ParameterKey=NPAPublisherKey,UsePreviousValue=true \
    ParameterKey=NPAPublisherInstanceType,UsePreviousValue=true \
    ParameterKey=LambdaS3Bucket,UsePreviousValue=true \
    ParameterKey=LambdaS3Key,UsePreviousValue=true \
    # ... (other parameters with UsePreviousValue=true)
  --capabilities CAPABILITY_NAMED_IAM
```

### Scale In (Remove a Publisher)

1. Delete the publisher's Instance + Registration resource block from the template
2. Update the `DependsOn` chain: if you remove a publisher in the middle of the chain (e.g., Publisher 2 of 3), update Publisher 3's Registration `DependsOn` to point to the previous Registration in the chain
3. Delete the matching Outputs entries
4. Deploy the updated template using the same `aws cloudformation update-stack` command above

CloudFormation triggers the Custom Resource DELETE event, which automatically:
- Stops the EC2 instance to force disconnection
- Removes the publisher from all associated private app definitions
- Waits for the publisher to disconnect
- Deletes the publisher from Netskope (retries with re-removal from apps to handle eventual consistency)

No manual cleanup in Netskope is required. See [Publisher Deletion Workflow](#publisher-deletion-workflow) for details on what happens during deletion, and [TROUBLESHOOTING.md](TROUBLESHOOTING.md#timeout-reference) if timeouts occur.

## Publisher Deletion Workflow

When CloudFormation processes a DELETE for a publisher Custom Resource (either during scale-in or full stack deletion), the Lambda function performs the following sequence:

### Automated Delete Flow

1. **Find publisher by name** -- GET all publishers, match by `publisher_name`
2. **Stop EC2 instance** -- Non-blocking, best-effort `ec2:StopInstances` call to force publisher disconnection
3. **Remove publisher from all app definitions** -- GET all private apps, filter for apps using this publisher, PATCH each app to remove the publisher from its `service_publisher_assignments`
4. **Wait for publisher to disconnect** -- Poll publisher status every 5s until not "connected" (max `PUBLISHER_DISCONNECT_TIMEOUT`, default 120s)
5. **Delete publisher with retry** -- DELETE the publisher, retrying up to 8 times at 10s intervals. On each retry, the Lambda re-removes the publisher from apps (re-PATCHes) to handle the Netskope API's eventual consistency

### Why This Order Matters

The Netskope API rejects publisher deletion if the publisher is still associated with private apps. The Lambda removes all app associations before attempting deletion. However, the Netskope API has eventual consistency — reads may still report associations even after a successful PATCH. To handle this, the delete retry loop re-PATCHes apps on each attempt, which is especially important when multiple publishers are being deleted concurrently (e.g., during full stack deletion) and one PATCH can overwrite another's changes.

### Verifying Deletion

Check that the publisher was removed from Netskope:

```bash
# Via API
STACK_NAME="netskope-npa-publisher"
TOKEN=$(aws ssm get-parameter \
  --name "${STACK_NAME}-netskope-api-token" \
  --with-decryption \
  --query Parameter.Value \
  --output text)

curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers \
     | jq '.data.publishers[] | select(.publisher_name | contains("<PublisherGroupName>"))'
```

Or check the Netskope UI under **Settings > Security Cloud Platform > Publishers**.

### Manual Cleanup (if Automated Deletion Fails)

If the Lambda times out or encounters an error during deletion:

1. **Remove from apps manually** -- In Netskope UI, go to each private app using the publisher and remove the publisher assignment
2. **Wait for disconnection** -- The publisher should disconnect after the EC2 instance stops (CloudFormation terminates it during stack deletion)
3. **Delete publisher** -- In Netskope UI under Publishers, select the orphaned publisher and delete it

See [DEVOPS-NOTES.md](DEVOPS-NOTES.md#timer--polling-architecture) for internal timer details and [TROUBLESHOOTING.md](TROUBLESHOOTING.md#timeout-reference) for timeout tuning.

## Rotate API Token

Rotate the Netskope API token stored in SSM Parameter Store.

### Step 1: Generate new token in Netskope UI

1. Log in to Netskope tenant
2. Go to **Settings → Administration → Administrators & Roles**
3. Create a new service account (or edit an existing one) with a REST API v2 token
4. Name: `NPA-Publisher-Rotated-<Date>`
5. Assign scopes: **Infrastructure Management**, **Private Applications**
6. Copy the token

> **Note:** Netskope now requires admin service accounts for new REST API v2 tokens. The legacy **Settings → Tools → REST API v2** page can still manage previously created tokens but cannot create new ones. See [Netskope Service Accounts documentation](https://docs.netskope.com/en/netskope-help/admin/administration/service-accounts/) for details.

### Step 2: Update SSM Parameter

```bash
STACK_NAME="netskope-npa-publisher"
PARAM_NAME="${STACK_NAME}-netskope-api-token"

# Update parameter with new token
NEW_TOKEN="your-new-api-token-here"

aws ssm put-parameter \
  --name $PARAM_NAME \
  --value "$NEW_TOKEN" \
  --type SecureString \
  --overwrite
```

### Step 3: Test token

```bash
# Verify token works
TOKEN=$(aws ssm get-parameter \
  --name $PARAM_NAME \
  --with-decryption \
  --query Parameter.Value \
  --output text)

curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers
```

**Note**: Existing publishers continue working with the new token. No stack update needed unless you want to force re-registration.

### Step 4: Revoke old token (optional)

1. Go to **Settings → Administration → Administrators & Roles** in Netskope UI (or **Settings → Tools → REST API v2** for legacy tokens)
2. Find the old token or service account
3. Revoke or delete it

## Manual Publisher Registration

Manually register a publisher if automatic registration failed.

### Step 1: Get publisher registration token

```bash
# Get API token
STACK_NAME="netskope-npa-publisher"
TOKEN=$(aws ssm get-parameter \
  --name "${STACK_NAME}-netskope-api-token" \
  --with-decryption \
  --query Parameter.Value \
  --output text)

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

# Export API token (secure location!)
STACK_NAME="netskope-npa-publisher"
aws ssm get-parameter \
  --name "${STACK_NAME}-netskope-api-token" \
  --with-decryption \
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
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues, solutions, and timeout tuning
- [DEVOPS-NOTES.md](DEVOPS-NOTES.md) - Technical deep-dive (timer architecture, polling internals)
- [Netskope REST API v2](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
