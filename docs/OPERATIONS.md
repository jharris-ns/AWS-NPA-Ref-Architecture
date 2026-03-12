# NPA Publisher Operational Procedures

Operational procedures for managing Netskope Private Access (NPA) Publisher deployments on AWS.

## What is an NPA Publisher?

An NPA Publisher is a gateway appliance that runs as an EC2 instance in your VPC. It creates an outbound tunnel to Netskope's cloud (NewEdge), allowing remote users to securely access private applications (databases, web servers, SSH hosts, etc.) inside your VPC — without exposing those resources to the internet. Each publisher registers with your Netskope tenant and can be assigned to one or more **private apps**, which define the internal hosts and ports that users can reach through the publisher.

This CloudFormation stack deploys publishers across two availability zones for redundancy. A Lambda function handles registration (creating the publisher in Netskope, obtaining a token, and running the registration wizard on the EC2 instance via SSM) and cleanup on deletion.

## Table of Contents

- [Check Publisher Status](#check-publisher-status)
- [Monitoring and Alerts](#monitoring-and-alerts)
- [Update Publisher AMI Version](#update-publisher-ami-version)
- [Scale Up/Down Instance Type](#scale-updown-instance-type)
- [Scale Publisher Count](#scale-publisher-count)
- [Replace a Failing Publisher](#replace-a-failing-publisher)
- [Publisher Deletion Workflow](#publisher-deletion-workflow)
- [Publisher Upgrades](#publisher-upgrades)
- [Additional Resources](#additional-resources)

## Check Publisher Status

Before performing any operation, check the current state of your publishers.

### From the Netskope UI

1. Log in to your Netskope tenant
2. Go to **Settings → Security Cloud Platform → Publishers**
3. Each publisher shows: name, status (Connected/Disconnected), version, and assigned apps

### From the CLI

```bash
STACK_NAME="netskope-npa-publisher"

# Get API token from SSM Parameter Store
TOKEN=$(aws ssm get-parameter \
  --name "${STACK_NAME}-netskope-api-token" \
  --with-decryption \
  --query Parameter.Value \
  --output text)

# List all publishers and their status
curl -s -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers \
     | jq '.data.publishers[] | {name: .publisher_name, status: .status, apps: .apps_count}'
```

Replace `mytenant.goskope.com` with your actual tenant FQDN throughout this guide.

### Check EC2 Instance Health

```bash
# Get instance IDs from stack outputs
INSTANCE_ID_1=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId`].OutputValue' \
  --output text)

INSTANCE_ID_2=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId2`].OutputValue' \
  --output text)

# Check instance states
aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID_1 $INSTANCE_ID_2 \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress]' \
  --output table

# Check SSM connectivity
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID_1,$INSTANCE_ID_2" \
  --query 'InstanceInformationList[].[InstanceId,PingStatus,LastPingDateTime]' \
  --output table
```

## Monitoring and Alerts

### CloudWatch Alarms

```bash
STACK_NAME="netskope-npa-publisher"

INSTANCE_ID_1=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId`].OutputValue' \
  --output text)

INSTANCE_ID_2=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId2`].OutputValue' \
  --output text)

# CPU alarm (repeat for each instance, changing name and instance ID)
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

# Status check alarm (detects underlying host or network issues)
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

### Lambda Error Alarm

```bash
# Replace <PublisherGroupName> with your actual group name
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

### Lambda Logs

```bash
# Replace <PublisherGroupName> with your actual group name
aws logs tail /aws/lambda/<PublisherGroupName>-RegistrationHandler --follow
```

## Update Publisher AMI Version

Updating the AMI triggers CloudFormation to replace both EC2 instances (the old publishers are deregistered and new ones are registered automatically).

### Step 1: Get new AMI ID

```bash
# Find latest AMI in your region
bash scripts/get-ami.sh us-east-1
```

### Step 2: Update the stack

```bash
STACK_NAME="netskope-npa-publisher"
NEW_AMI="ami-xxxxxxxxx"   # <-- replace with AMI ID from step 1

aws cloudformation update-stack \
  --stack-name $STACK_NAME \
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
    ParameterKey=AppAssociations,UsePreviousValue=true \
    ParameterKey=ProvisionNewAPIToken,UsePreviousValue=true \
  --capabilities CAPABILITY_NAMED_IAM
```

**Impact**: Both instances are replaced. In a multi-AZ deployment, CloudFormation replaces them one at a time so connectivity is maintained.

## Scale Up/Down Instance Type

Change the EC2 instance type for performance tuning. This replaces both instances (same process as an AMI update).

### Supported Instance Types

| Instance Type | Workload | Concurrent Connections |
|---------------|----------|----------------------|
| `t3.medium` | Light | Up to 100 |
| `t3.large` | Standard **(default)** | 100–500 |
| `t3.xlarge` | Heavy | 500–1,000 |
| `t3.2xlarge` | Very heavy | 1,000+ |

### Update Procedure

```bash
STACK_NAME="netskope-npa-publisher"
NEW_INSTANCE_TYPE="t3.xlarge"   # <-- replace with desired type

aws cloudformation update-stack \
  --stack-name $STACK_NAME \
  --use-previous-template \
  --parameters \
    ParameterKey=NPAPublisherInstanceType,ParameterValue=$NEW_INSTANCE_TYPE \
    ParameterKey=NetskopeTenantFQDN,UsePreviousValue=true \
    ParameterKey=CreateNewVPC,UsePreviousValue=true \
    ParameterKey=NPAPublisherGroupName,UsePreviousValue=true \
    ParameterKey=NPAPublisherKey,UsePreviousValue=true \
    ParameterKey=NPAPublisherAMIId,UsePreviousValue=true \
    ParameterKey=NetskopeAPIToken,UsePreviousValue=true \
    ParameterKey=LambdaS3Bucket,UsePreviousValue=true \
    ParameterKey=LambdaS3Key,UsePreviousValue=true \
    ParameterKey=AppAssociations,UsePreviousValue=true \
    ParameterKey=ProvisionNewAPIToken,UsePreviousValue=true \
  --capabilities CAPABILITY_NAMED_IAM
```

## Scale Publisher Count

The template ships with 2 publishers (one per AZ). You can add more by copying a resource block in the template. Each publisher requires two CloudFormation resources: an `AWS::EC2::Instance` and a `Custom::NPAPublisher` (registration).

### Scale Out (Add a Publisher)

1. Open `templates/netskope-ref-architecture-npa.yaml`
2. Find the Publisher 2 section (between `# Publisher 2` and `# SHARED INFRASTRUCTURE`)
3. Copy both resources, paste directly above the `# SHARED INFRASTRUCTURE` line
4. Make the changes shown below, then deploy

**What to change in the copy (Publisher 2 → Publisher 3):**

| Field | Publisher 2 (original) | Publisher 3 (your copy) |
|-------|----------------------|------------------------|
| Instance resource name | `NPAPublisherInstance2` | `NPAPublisherInstance3` |
| Registration resource name | `NPAPublisherRegistration2` | `NPAPublisherRegistration3` |
| Registration `DependsOn` | `NPAPublisherRegistration` | `NPAPublisherRegistration2` |
| Registration `InstanceId` | `!Ref NPAPublisherInstance2` | `!Ref NPAPublisherInstance3` |
| SubnetId (new VPC) | `!Ref PrivateSubnet2` | `!Ref PrivateSubnet` (alternate AZ) |
| SubnetId (existing VPC) | `!Ref ExistingPrivateSubnet2` | `!Ref ExistingPrivateSubnet` (alternate AZ) |
| Name tag | `${NPAPublisherGroupName}-2` | `${NPAPublisherGroupName}-3` |

The `DependsOn` chain is critical: each Registration must depend on the **previous** Registration to avoid a race condition when assigning publishers to private apps. See [DEVOPS-NOTES.md](DEVOPS-NOTES.md#private-app-publisher-assignment) for details.

**Result — Publisher 3 resources:**

```yaml
  # ---------------------------------------------------------------
  # Publisher 3 — AZ1 (PrivateSubnet / ExistingPrivateSubnet)
  # ---------------------------------------------------------------
  NPAPublisherInstance3:
    Type: AWS::EC2::Instance
    Properties:
      ImageId: !Ref NPAPublisherAMIId
      InstanceType: !Ref NPAPublisherInstanceType
      KeyName: !Ref NPAPublisherKey
      IamInstanceProfile: !Ref NPAPublisherInstanceProfile
      SecurityGroupIds:
        - !Ref NPAPublisherSecurityGroup
      SubnetId:
        Fn::If:
          - CreateVPC
          - !Ref PrivateSubnet
          - !Ref ExistingPrivateSubnet
      Monitoring: true
      MetadataOptions:
        HttpTokens: required
        HttpEndpoint: enabled
        HttpPutResponseHopLimit: 2
      Tags:
        - Key: Name
          Value: !Sub '${NPAPublisherGroupName}-3'
        - Key: CostCenter
          Value: !Ref CostCenterTag
        - Key: Project
          Value: !Ref ProjectTag
        - Key: Environment
          Value: !Ref EnvironmentTag
        - Key: aws-apn-id
          Value: 2477fb49-b2ca-409e-9b2b-87322e6008c2

  NPAPublisherRegistration3:
    Type: Custom::NPAPublisher
    DependsOn:
      - NPAPublisherInstance3
      - NPAPublisherRegistration2        # previous registration in chain
    Properties:
      ServiceToken: !GetAtt NPAPublisherRegistrationFunction.Arn
      InstanceId: !Ref NPAPublisherInstance3
      PublisherNamePrefix: !Ref NPAPublisherGroupName
      AppAssociations: !Ref AppAssociations
      AccountId: !Ref AWS::AccountId
```

**Add matching Outputs at the bottom of the template:**

```yaml
  # Publisher 3
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

**Deploy the updated template:**

```bash
STACK_NAME="netskope-npa-publisher"

aws cloudformation update-stack \
  --stack-name $STACK_NAME \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,UsePreviousValue=true \
    ParameterKey=CreateNewVPC,UsePreviousValue=true \
    ParameterKey=NPAPublisherGroupName,UsePreviousValue=true \
    ParameterKey=NPAPublisherAMIId,UsePreviousValue=true \
    ParameterKey=NPAPublisherKey,UsePreviousValue=true \
    ParameterKey=NPAPublisherInstanceType,UsePreviousValue=true \
    ParameterKey=NetskopeAPIToken,UsePreviousValue=true \
    ParameterKey=LambdaS3Bucket,UsePreviousValue=true \
    ParameterKey=LambdaS3Key,UsePreviousValue=true \
    ParameterKey=AppAssociations,UsePreviousValue=true \
    ParameterKey=ProvisionNewAPIToken,UsePreviousValue=true \
  --capabilities CAPABILITY_NAMED_IAM
```

### Scale In (Remove a Publisher)

To remove Publisher 3 (the last in the chain):

1. Delete the `NPAPublisherInstance3` and `NPAPublisherRegistration3` resource blocks from the template
2. Delete the matching `PublisherInstanceId3` and `PublisherPrivateIP3` Outputs
3. Deploy using the same `aws cloudformation update-stack` command above

**If removing from the middle of the chain** (e.g., removing Publisher 2 when Publisher 3 exists), update Publisher 3's `DependsOn` to skip the removed publisher:

```yaml
  # Before: Publisher 3 depends on Publisher 2 (being removed)
  NPAPublisherRegistration3:
    DependsOn:
      - NPAPublisherInstance3
      - NPAPublisherRegistration2          # ← being deleted

  # After: Publisher 3 depends on Publisher 1
  NPAPublisherRegistration3:
    DependsOn:
      - NPAPublisherInstance3
      - NPAPublisherRegistration           # ← now points to Publisher 1
```

CloudFormation automatically triggers the deletion workflow, which deregisters the publisher from Netskope and removes it from all private apps. No manual cleanup is required. See [Publisher Deletion Workflow](#publisher-deletion-workflow) for details.

## Replace a Failing Publisher

CloudFormation does **not** automatically recreate terminated EC2 instances (unlike an Auto Scaling Group). If you terminate an instance directly, the stack will show drift but take no action.

### Replace a Single Publisher

If only one of multiple publishers has failed, use [Scale In](#scale-in-remove-a-publisher) to remove it, then [Scale Out](#scale-out-add-a-publisher) to re-add it.

### Replace All Publishers (Delete and Recreate Stack)

If the stack is in a failed state or you need a complete refresh:

```bash
STACK_NAME="netskope-npa-publisher"

# Delete stack (automated cleanup removes publishers from Netskope)
aws cloudformation delete-stack --stack-name $STACK_NAME
aws cloudformation wait stack-delete-complete --stack-name $STACK_NAME

# Recreate with your original parameters
aws cloudformation create-stack \
  --stack-name $STACK_NAME \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue=mytenant.goskope.com \
    ParameterKey=NPAPublisherGroupName,ParameterValue=MyPublisher \
    ParameterKey=NPAPublisherAMIId,ParameterValue=ami-xxxxxxxxx \
    ParameterKey=NPAPublisherKey,ParameterValue=my-keypair \
    ParameterKey=NetskopeAPIToken,ParameterValue=your-api-token \
    ParameterKey=LambdaS3Bucket,ParameterValue=my-lambda-bucket \
    ParameterKey=LambdaS3Key,ParameterValue=npa-publisher-lambda.zip \
    ParameterKey=CreateNewVPC,ParameterValue=no \
    ParameterKey=ExistingVPC,ParameterValue=vpc-xxxxxxxxx \
    ParameterKey=ExistingPrivateSubnet,ParameterValue=subnet-xxxxxxxxx \
    ParameterKey=ExistingPrivateSubnet2,ParameterValue=subnet-yyyyyyyyy \
  --capabilities CAPABILITY_NAMED_IAM
```

Replace all placeholder values (`mytenant.goskope.com`, `ami-xxxxxxxxx`, etc.) with your actual configuration.

## Publisher Deletion Workflow

When CloudFormation processes a DELETE for a publisher (during scale-in or full stack deletion), the Lambda function automatically:

1. **Stops the EC2 instance** to force the publisher to disconnect
2. **Removes the publisher from all private apps** it was assigned to
3. **Waits for the publisher to disconnect** from Netskope (up to 120s)
4. **Deletes the publisher** from Netskope, retrying up to 8 times at 10s intervals

No manual cleanup in Netskope is required. The entire process is automated and handles retries for API consistency.

### Verifying Deletion

After stack deletion completes, confirm the publisher is gone:

**Netskope UI:** Go to **Settings → Security Cloud Platform → Publishers** and verify no orphaned publishers remain.

**CLI:**
```bash
# Only works if the SSM parameter still exists (e.g., during scale-in, not full stack delete)
curl -s -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers \
     | jq '.data.publishers[] | select(.publisher_name | contains("MyPublisher"))'
```

### Manual Cleanup (if Automated Deletion Fails)

If the stack deletion fails or times out:

1. In the Netskope UI, go to each private app using the publisher and remove the publisher assignment
2. Wait for the publisher to show as **Disconnected** (happens automatically after the EC2 instance stops)
3. Delete the publisher under **Settings → Security Cloud Platform → Publishers**

For timeout tuning, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md#timeout-reference). For internal details on the delete flow, see [DEVOPS-NOTES.md](DEVOPS-NOTES.md#delete-flow).

## Publisher Upgrades

### Auto-Updates (Recommended)

Netskope publishers support automatic software upgrades managed through the Netskope console. This keeps publishers up to date without replacing EC2 infrastructure.

1. Go to **Settings → Security Cloud Platform → Publishers**
2. Select your publisher
3. Enable **Auto-Update** and configure the maintenance window

Benefits: no manual intervention, minimal downtime, automatic rollback on failure, controlled maintenance windows.

See [Configure Publisher Auto-Updates](https://docs.netskope.com/en/configure-publisher-auto-updates) in the Netskope documentation.

### Infrastructure Replacement

If you need to replace the underlying EC2 instance (not just update publisher software), see [Update Publisher AMI Version](#update-publisher-ami-version) or [Replace a Failing Publisher](#replace-a-failing-publisher).

## Additional Resources

- [QUICKSTART.md](QUICKSTART.md) — Quick deployment guide
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — Full deployment instructions
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common issues, solutions, and timeout tuning
- [DEVOPS-NOTES.md](DEVOPS-NOTES.md) — Technical deep-dive (Lambda internals, timer architecture, race conditions)
- [Netskope REST API v2](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- [Netskope Service Accounts](https://docs.netskope.com/en/administration)
