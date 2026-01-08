# Quick Start Guide - NPA Publisher Deployment

Get your Netskope Private Access Publisher deployed with multi-AZ redundancy in under 10 minutes.

## Prerequisites Checklist

Before you begin, ensure you have:

- [ ] AWS Account with CloudFormation permissions
- [ ] Netskope API v2 Token ([generate here](https://docs.netskope.com/en/rest-api-v2-overview-312207.html))
- [ ] NPA Publisher AMI ID for your region (see command below)
- [ ] EC2 Key Pair in target region
- [ ] S3 bucket for Lambda deployment package (must be in same region as deployment)
- [ ] **For existing VPC**: VPC ID and private subnet with NAT Gateway
- [ ] **For new VPC**: Desired CIDR ranges (or use defaults)

## Get NPA Publisher AMI ID

### Option 1: Use Helper Script (Recommended)

```bash
# Get AMI for your default region
bash scripts/get-ami.sh

# Get AMI for specific region
bash scripts/get-ami.sh us-east-1
bash scripts/get-ami.sh eu-west-1
bash scripts/get-ami.sh us-west-2
```

**Output:**
```
Searching for latest Netskope Private Access Publisher AMI in region: eu-west-1

✓ Found AMI:

  AMI ID:       ami-0123456789abcdef0
  Name:         Netskope Private Access Publisher-v99.0.0
  Created:      2024-01-15T10:30:00.000Z

ami-0123456789abcdef0
```

### Option 2: Manual AWS CLI Command

```bash
aws ec2 describe-images \
  --owners aws-marketplace \
  --filters "Name=name,Values=*Netskope Private Access Publisher*" \
  --region us-east-1 \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text
```

**For different regions**, change the `--region` parameter:
```bash
# EU (Ireland)
aws ec2 describe-images \
  --owners aws-marketplace \
  --filters "Name=name,Values=*Netskope Private Access Publisher*" \
  --region eu-west-1 \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text

# US West (Oregon)
aws ec2 describe-images \
  --owners aws-marketplace \
  --filters "Name=name,Values=*Netskope Private Access Publisher*" \
  --region us-west-2 \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text
```

Save the AMI ID for use in deployment parameters.

## Quick Deploy (3 Steps)

### Step 1: Upload Lambda Package to S3

**IMPORTANT**: The S3 bucket must be in the **same region** where you'll deploy the CloudFormation stack.

```bash
# Set your target region
REGION=us-east-1  # Change to your deployment region (e.g., eu-west-1, us-west-2)

# Create S3 bucket in the same region (skip if you already have one)
aws s3 mb s3://my-npa-lambda-bucket --region $REGION

# Upload pre-packaged Lambda function
aws s3 cp scripts/npa-publisher-lambda.zip s3://my-npa-lambda-bucket/
```

**Note**: If the bucket already exists in a different region, either:
- Create a new bucket in your target region, or
- Deploy to the region where your bucket exists

### Step 2: Deploy CloudFormation Stack

**Option A: Interactive Deployment (Recommended)**

```bash
./deploy.sh my-stack-name my-npa-lambda-bucket

# Follow the interactive prompts:
# 1. Choose VPC mode (create new or use existing)
# 2. Provide required parameters based on your choice
# 3. Confirm and deploy
```

**Option B: AWS Console**

1. Go to AWS CloudFormation Console
2. Click **Create Stack** → **With new resources**
3. Upload `templates/npa-publisher-single-instance.yaml`
4. Fill in the parameters (see below)
5. Check **I acknowledge that AWS CloudFormation might create IAM resources**
6. Click **Create Stack**

**Option C: AWS CLI - New VPC**

```bash
aws cloudformation create-stack \
  --stack-name netskope-npa-publisher \
  --template-body file://templates/npa-publisher-single-instance.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue=mytenant.goskope.com \
    ParameterKey=CreateNewVPC,ParameterValue=yes \
    ParameterKey=VPCCIDR,ParameterValue=10.0.0.0/16 \
    ParameterKey=PublicSubnetCIDR,ParameterValue=10.0.1.0/24 \
    ParameterKey=PrivateSubnetCIDR,ParameterValue=10.0.2.0/24 \
    ParameterKey=NPAPublisherGroupName,ParameterValue=MyPublisher \
    ParameterKey=NPAPublisherAMIId,ParameterValue=ami-xxxxx \
    ParameterKey=NPAPublisherKey,ParameterValue=my-keypair \
    ParameterKey=NetskopeAPIToken,ParameterValue=your-api-token \
    ParameterKey=LambdaS3Bucket,ParameterValue=my-npa-lambda-bucket \
    ParameterKey=LambdaS3Key,ParameterValue=npa-publisher-lambda.zip \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

**Option D: AWS CLI - Existing VPC**

```bash
aws cloudformation create-stack \
  --stack-name netskope-npa-publisher \
  --template-body file://templates/npa-publisher-single-instance.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue=mytenant.goskope.com \
    ParameterKey=CreateNewVPC,ParameterValue=no \
    ParameterKey=ExistingVPC,ParameterValue=vpc-xxxxx \
    ParameterKey=ExistingPrivateSubnet,ParameterValue=subnet-xxxxx \
    ParameterKey=NPAPublisherGroupName,ParameterValue=MyPublisher \
    ParameterKey=NPAPublisherAMIId,ParameterValue=ami-xxxxx \
    ParameterKey=NPAPublisherKey,ParameterValue=my-keypair \
    ParameterKey=NetskopeAPIToken,ParameterValue=your-api-token \
    ParameterKey=LambdaS3Bucket,ParameterValue=my-npa-lambda-bucket \
    ParameterKey=LambdaS3Key,ParameterValue=npa-publisher-lambda.zip \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

### Step 3: Monitor Deployment

**Watch CloudFormation Events:**
```bash
aws cloudformation describe-stack-events \
  --stack-name netskope-npa-publisher \
  --max-items 10 \
  --region us-east-1
```

**Watch Lambda Logs (replace with your publisher group name):**
```bash
aws logs tail /aws/lambda/MyPublisher-RegistrationHandler --follow
```

**Check Stack Status:**
```bash
aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].StackStatus' \
  --output text
```

## Parameter Reference

### Required Parameters (All Deployments)

| Parameter | Description | Example |
|-----------|-------------|---------|
| `NetskopeTenantFQDN` | Your Netskope tenant URL | `mytenant.goskope.com` |
| `NPAPublisherGroupName` | Unique name for this publisher group | `MyNPAPublisher` |
| `NPAPublisherAMIId` | NPA Publisher AMI ID | `ami-0123456789abcdef` |
| `NPAPublisherKey` | EC2 key pair name | `my-ec2-keypair` |
| `NetskopeAPIToken` | Netskope API v2 token | `xxxxxxxx` |
| `LambdaS3Bucket` | S3 bucket with Lambda package | `my-lambda-bucket` |
| `LambdaS3Key` | S3 key for Lambda package | `npa-publisher-lambda.zip` |

### VPC Mode: Create New VPC

| Parameter | Description | Default | Example |
|-----------|-------------|---------|---------|
| `CreateNewVPC` | Create new VPC? | - | `yes` |
| `VPCCIDR` | VPC CIDR block | `10.0.0.0/16` | `10.0.0.0/16` |
| `PublicSubnetCIDR` | Public subnet CIDR | `10.0.1.0/24` | `10.0.1.0/24` |
| `PrivateSubnetCIDR` | Private subnet CIDR | `10.0.2.0/24` | `10.0.2.0/24` |
| `AvailabilityZone` | AZ for subnets | Auto-selected | `us-east-1a` |

### VPC Mode: Use Existing VPC

| Parameter | Description | Example |
|-----------|-------------|---------|
| `CreateNewVPC` | Create new VPC? | `no` |
| `ExistingVPC` | VPC ID | `vpc-0123456789abcdef` |
| `ExistingPrivateSubnet` | Private subnet ID | `subnet-0123456789abcdef` |

### Optional Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `NPAPublisherInstanceType` | EC2 instance type | `t3.large` |
| `ProvisionNewAPIToken` | Store API token in Secrets Manager | `yes` |

## Deployment Timeline

Typical deployment time: **4-10 minutes** (varies with single-AZ vs multi-AZ)

### Single-AZ Deployment
```
t=0m    CloudFormation: CREATE_IN_PROGRESS
        ├─ VPC resources created (if new VPC, 1 AZ)
        ├─ Security group created
        ├─ IAM role created
        └─ Secrets Manager secret created

t=1m    EC2 instance launched (AZ1)
        └─ SSM Agent starts

t=2m    Lambda triggered
        ├─ Creates publisher in Netskope
        └─ Gets registration token

t=3m    Lambda waits for EC2 running
        └─ Instance state: running

t=4m    Lambda waits for SSM agent
        └─ SSM agent online

t=5m    Lambda sends registration command
        └─ npa_publisher_wizard executes

t=6m    Command completes
        └─ Publisher registered

t=7m    Lambda updates private apps
        └─ Assigns publisher to matching apps

t=8m    CloudFormation: CREATE_COMPLETE ✅
```

### Multi-AZ Deployment (Recommended)
```
t=0m    CloudFormation: CREATE_IN_PROGRESS
        ├─ VPC resources created (if new VPC, 2 AZs)
        ├─ NAT Gateways x2 created
        ├─ Security group created
        ├─ IAM role created
        └─ Secrets Manager secret created

t=1m    EC2 instances launched (AZ1 + AZ2)
        └─ SSM Agents start

t=2m    Lambda triggered for both instances
        ├─ Creates publishers in Netskope
        └─ Gets registration tokens

t=3-4m  Lambda processes both instances in parallel
        └─ Both instances register simultaneously

t=5-8m  Publishers registered in both AZs
        └─ Multi-AZ redundancy active

t=10m   CloudFormation: CREATE_COMPLETE ✅
```

## Verify Deployment

### 1. Check CloudFormation Stack

```bash
aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].[StackStatus,Outputs]' \
  --output table
```

**Expected output:**
- Stack Status: `CREATE_COMPLETE`
- Outputs include: Instance ID, Private IP, Publisher Name

### 2. Verify in Netskope UI

1. Log in to Netskope tenant
2. Go to **Settings → Security Cloud Platform → Publishers**
3. Look for publisher named: `<GroupName>-<AccountID>-<InstanceID>`
4. Status should be: **Connected** (may take 1-2 minutes)

### 3. Check EC2 Instance

```bash
# Get instance ID from stack outputs
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId`].OutputValue' \
  --output text)

# Check instance status
aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].[State.Name,PrivateIpAddress]' \
  --output table
```

### 4. Test SSM Access

```bash
# Connect via Session Manager (no SSH key needed!)
aws ssm start-session --target $INSTANCE_ID

# Once connected, check publisher status
systemctl status npa_publisher
```

## Create Private Applications

After deployment, create private applications in Netskope:

**IMPORTANT**: App names must start with your Publisher Group Name.

```
Publisher Group Name: MyPublisher

✅ Valid App Names:
  - MyPublisher-InternalApp
  - MyPublisher-WebServer
  - MyPublisher-Database

❌ Invalid App Names:
  - InternalApp
  - Web-MyPublisher
  - MyApp
```

The Lambda function automatically assigns the publisher to apps matching this naming convention.

## Troubleshooting

### Stack Stuck at CREATE_IN_PROGRESS

**Check Lambda logs:**
```bash
aws logs tail /aws/lambda/MyPublisher-RegistrationHandler --follow
```

**Common issues:**
- API token invalid → Check token in Secrets Manager
- SSM agent not starting → Check security group, NAT Gateway
- Command timeout → Check instance has internet access

### Instance Not Appearing in SSM

**Diagnose:**
```bash
# Check if instance is running
aws ec2 describe-instances --instance-ids $INSTANCE_ID

# Check SSM registration
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID"
```

**Common causes:**
- Security group blocks HTTPS (443) outbound
- No route to internet (NAT Gateway missing)
- IAM instance profile missing SSM policy

### Command Failed

**View command output:**
```bash
# Get command ID from Lambda logs, then:
aws ssm get-command-invocation \
  --command-id <command-id> \
  --instance-id $INSTANCE_ID \
  --query '[StandardOutputContent,StandardErrorContent]' \
  --output text
```

**Connect to instance and debug:**
```bash
# Start Session Manager session
aws ssm start-session --target $INSTANCE_ID

# Check wizard logs
sudo tail -f /var/log/amazon/ssm/amazon-ssm-agent.log

# Manually run wizard (test mode)
sudo /home/ubuntu/npa_publisher_wizard -token "test"
```

### API Token Issues

**Test token manually:**
```bash
# Get token from Secrets Manager
TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id NetskopeAPIToken-MyPublisher \
  --query SecretString \
  --output text | jq -r '.NetskopeAPIToken')

# Test API call
curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers
```

**Expected response:** JSON with publishers list

## Clean Up

```bash
# Delete CloudFormation stack (removes all resources)
aws cloudformation delete-stack --stack-name netskope-npa-publisher

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete \
  --stack-name netskope-npa-publisher

# Optionally, remove Lambda from S3
aws s3 rm s3://my-lambda-bucket/npa-publisher-lambda.zip
```

**Note:** Stack deletion automatically:
- Removes publisher from Netskope
- Unassigns publisher from private apps
- Terminates EC2 instance
- Deletes VPC resources (if created by stack)
- Removes Secrets Manager secret

## Next Steps

1. **Monitor Publisher Health**
   - Check Netskope UI for connection status
   - Monitor CloudWatch metrics for instance health

2. **Create Private Applications**
   - Name them: `<PublisherGroupName>-<AppName>`
   - They'll automatically use this publisher

3. **Set Up Monitoring**
   - Create CloudWatch alarms for instance health
   - Set up SNS notifications for failures

4. **Production Readiness**
   - Consider deploying multiple publishers across AZs
   - Set up automated backups
   - Configure instance monitoring and auto-recovery

## Additional Resources

- [Full Documentation](README.md) - Comprehensive deployment guide
- [DevOps Technical Notes](docs/DEVOPS-NOTES.md) - SSM and Lambda deep-dive
- [Netskope REST API v2](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- [AWS Systems Manager](https://docs.aws.amazon.com/systems-manager/)
- [CloudFormation Best Practices](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html)

## Getting Help

**Issues with deployment?**
1. Check Lambda logs: `/aws/lambda/<GroupName>-RegistrationHandler`
2. Review SSM command history: Systems Manager → Run Command
3. Read troubleshooting section in [DEVOPS-NOTES.md](docs/DEVOPS-NOTES.md)

**Need support?**
- File issues on GitHub repository
- Consult Netskope documentation
- Review AWS service health dashboard
