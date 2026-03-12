# NPA Publisher Deployment Guide

Complete deployment instructions for Netskope Private Access Publishers on AWS.

## Prerequisites

Before you begin, ensure you have:

- **AWS Account with appropriate permissions** - See [IAM Permissions Required](../README.md#iam-permissions-required) in README.md or review [templates/deployment-iam-policy.json](../templates/deployment-iam-policy.json)
- Amazon VPC with subnet that has internet connectivity (NAT Gateway) - **OR** choose to create new VPC
- AWS Systems Manager enabled
- Netskope REST API v2 Token with infrastructure and application management permissions (create via **Settings → Administration → Administrators & Roles** in Netskope UI; legacy tokens from **Settings → Tools → REST API v2** also work)
- NPA Publisher AMI ID for your region (see command below)
- S3 bucket for Lambda deployment package (**must be in same region as deployment**)

## Get NPA Publisher AMI ID

Use the helper script to find the latest AMI:

```bash
# Get AMI for your default region
bash scripts/get-ami.sh

# Get AMI for specific region
bash scripts/get-ami.sh eu-west-1
```

Or manually query AWS:

```bash
aws ec2 describe-images \
  --owners aws-marketplace \
  --filters "Name=name,Values=*Netskope Private Access Publisher*" \
  --region us-east-1 \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text
```

Change `--region` to match your deployment region (e.g., `eu-west-1`, `us-west-2`, `ap-southeast-1`).

## Deployment Options

### Option 1: AWS Console (Recommended)

1. Go to [AWS CloudFormation Console](https://console.aws.amazon.com/cloudformation/)
2. Click **Create Stack** → **With new resources (standard)**
3. Select **Upload a template file** and upload `templates/netskope-ref-architecture-npa.yaml`
4. Enter a **Stack name** (e.g., `netskope-npa-publisher`)
5. Fill in the parameters — the template groups them into sections with descriptions:
   - **Netskope tenant info**: Tenant FQDN, API token, token provisioning
   - **VPC Configuration**: Create new VPC or use existing (VPC ID, subnets)
   - **Publisher Configuration**: Group name, AMI ID, key pair, instance type, app associations
   - **Lambda Configuration**: S3 bucket and key for the Lambda deployment package
   - **Tags**: Cost center, project, environment
6. On the review page, check **I acknowledge that AWS CloudFormation might create IAM resources with custom names**
7. Click **Submit**

### Option 2: AWS CLI - Create New VPC

```bash
aws cloudformation create-stack \
  --stack-name netskope-npa-publisher \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue=mytenant.goskope.com \
    ParameterKey=CreateNewVPC,ParameterValue=yes \
    ParameterKey=VPCCIDR,ParameterValue=10.0.0.0/16 \
    ParameterKey=PublicSubnetCIDR,ParameterValue=10.0.1.0/24 \
    ParameterKey=PrivateSubnetCIDR,ParameterValue=10.0.2.0/24 \
    ParameterKey=PublicSubnetCIDR2,ParameterValue=10.0.3.0/24 \
    ParameterKey=PrivateSubnetCIDR2,ParameterValue=10.0.4.0/24 \
    ParameterKey=NPAPublisherGroupName,ParameterValue=MyPublisher \
    ParameterKey=NPAPublisherAMIId,ParameterValue=ami-xxxxx \
    ParameterKey=NPAPublisherKey,ParameterValue=my-keypair \
    ParameterKey=NetskopeAPIToken,ParameterValue=your-api-token \
    ParameterKey=LambdaS3Bucket,ParameterValue=my-npa-lambda-bucket \
    ParameterKey=LambdaS3Key,ParameterValue=npa-publisher-lambda.zip \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

### Option 3: AWS CLI - Use Existing VPC

```bash
aws cloudformation create-stack \
  --stack-name netskope-npa-publisher \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue=mytenant.goskope.com \
    ParameterKey=CreateNewVPC,ParameterValue=no \
    ParameterKey=ExistingVPC,ParameterValue=vpc-xxxxx \
    ParameterKey=ExistingPrivateSubnet,ParameterValue=subnet-xxxxx \
    ParameterKey=ExistingPrivateSubnet2,ParameterValue=subnet-yyyyy \
    ParameterKey=NPAPublisherGroupName,ParameterValue=MyPublisher \
    ParameterKey=NPAPublisherAMIId,ParameterValue=ami-xxxxx \
    ParameterKey=NPAPublisherKey,ParameterValue=my-keypair \
    ParameterKey=NetskopeAPIToken,ParameterValue=your-api-token \
    ParameterKey=LambdaS3Bucket,ParameterValue=my-npa-lambda-bucket \
    ParameterKey=LambdaS3Key,ParameterValue=npa-publisher-lambda.zip \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

## Lambda Packaging

The Lambda function requires Python dependencies (requests, urllib3, certifi). Two options:

### Option 1: Use Pre-packaged Lambda (Recommended)

A pre-built package is included at `scripts/npa-publisher-lambda.zip`:

```bash
# IMPORTANT: S3 bucket must be in the same region as your deployment
REGION=us-east-1  # Change to match your deployment region

# Upload to your S3 bucket
aws s3 cp scripts/npa-publisher-lambda.zip s3://my-bucket/

# Update template parameters:
# LambdaS3Bucket: my-bucket
# LambdaS3Key: npa-publisher-lambda.zip
```

**Note**: CloudFormation requires the S3 bucket to be in the same region where you're deploying the stack.

### Option 2: Build from Source

Use the included packaging script:

```bash
# Package Lambda with dependencies
./scripts/package-lambda.sh

# Upload to S3
aws s3 cp scripts/npa-publisher-lambda.zip s3://my-bucket/
```

Or manually:

```bash
# Create package directory
mkdir -p scripts/package

# Install dependencies
pip install requests urllib3 certifi -t scripts/package/

# Copy Lambda function
cp scripts/lambda_function.py scripts/package/

# Create deployment package
cd scripts/package
zip -r ../npa-publisher-lambda.zip .
cd ../..
```

## Monitoring Deployment

### Watch CloudFormation Events

```bash
aws cloudformation describe-stack-events \
  --stack-name netskope-npa-publisher \
  --max-items 10 \
  --region us-east-1
```

### Monitor Lambda Logs

```bash
# Replace with your publisher group name
aws logs tail /aws/lambda/MyPublisher-RegistrationHandler --follow
```

### Check Stack Status

```bash
aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].StackStatus' \
  --output text
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

## Monitoring Operations

### CloudWatch Logs

Lambda function logs are available in:
```
/aws/lambda/<PublisherGroupName>-RegistrationHandler
```

Key log messages to look for:
- `Creating a new publisher: <name>`
- `Successfully obtained registration token`
- `Instance is running, proceeding to SSM check`
- `Instance is online in SSM!`
- `Command completed successfully`
- `Publisher registration completed. Updated N private applications`

### SSM Command History

View SSM Run Command executions in AWS Console:
- **Systems Manager → Run Command → Command history**
- Look for commands with comment: "Registering NPA publisher with Netskope"

### Stack Events

Monitor CloudFormation events to track:
- EC2 instance creation
- Custom resource creation (publisher registration)
- Any failures or rollbacks

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
- Terminates EC2 instances
- Deletes VPC resources (if created by stack)
- Removes SSM parameter
- Deletes VPC endpoints (if created by stack)

## Next Steps

1. **Create Private Applications** - See [README.md](../README.md#naming-convention) for naming requirements
2. **Monitor Publisher Health** - Check Netskope UI and CloudWatch metrics
3. **Set Up Alarms** - Configure CloudWatch alarms for failures
4. **Review Security** - See [README.md](../README.md#security-considerations) for security features

## Additional Resources

- [QUICKSTART.md](QUICKSTART.md) - Quick deployment guide
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues and solutions
- [DEVOPS-NOTES.md](DEVOPS-NOTES.md) - Technical deep-dive
- [Netskope REST API v2](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- [AWS Systems Manager](https://docs.aws.amazon.com/systems-manager/)
