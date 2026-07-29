# CLAUDE.md — NPA Publisher Reference Architecture

This file gives Claude Code context for working in this repository. Read it before answering questions or making changes.

## What This Project Does

Deploys Netskope Private Access (NPA) Publishers on AWS using CloudFormation with multi-AZ redundancy. A Lambda-backed Custom Resource handles publisher registration automatically — no manual steps in the Netskope UI required after stack creation.

## Project Structure

```
AWS-NPA-Ref-Architecture/
├── CLAUDE.md                              # This file
├── README.md                              # Project overview and reference
├── docs/
│   ├── QUICKSTART.md                      # 10-minute deployment guide
│   ├── DEPLOYMENT_GUIDE.md                # Full deployment docs
│   ├── ARCHITECTURE.md                    # Architecture and design decisions
│   ├── OPERATIONS.md                      # Day-2 operations (AMI updates, scaling)
│   ├── TROUBLESHOOTING.md                 # Common issues and diagnostics
│   ├── DEVOPS-NOTES.md                    # SSM + Lambda internals deep-dive
│   ├── IAM-ROLE-SETUP.md                  # IAM role setup for deployment
│   ├── TRANSIT-GATEWAY-INTEGRATION.md     # Post-deployment TGW connectivity guide
│   └── images/
│       └── npa_reference_architecture.png
├── scripts/
│   ├── deploy.sh                          # Interactive deployment script (primary)
│   ├── get-ami.sh                         # Find latest NPA Publisher AMI
│   ├── lambda_function.py                 # Lambda source (edit this, then repackage)
│   ├── npa-publisher-lambda.zip           # Pre-packaged Lambda (ready to upload)
│   └── package-lambda.sh                  # Repackage Lambda after editing
├── templates/
│   ├── netskope-ref-architecture-npa.yaml # Primary CloudFormation template (dual-AZ)
│   └── deployment-iam-policy.json         # IAM policy for deploying the stack
└── examples/
    └── netskope-ref-architecture-npa-3az.yaml  # 3-AZ variant
```

## Deploying the Project

### Prerequisites

Before deploying, the user needs:
- AWS credentials configured (`aws configure`)
- An S3 bucket in the **same region** as the target deployment
- A Netskope REST API v2 token (create via **Settings → Administration → Administrators & Roles** in the Netskope UI)
- An NPA Publisher AMI ID for the target region

### Step 1 — Find the AMI ID

```bash
bash scripts/get-ami.sh                  # default region from AWS CLI config
bash scripts/get-ami.sh us-east-1        # specific region
```

### Step 2 — Upload Lambda to S3

```bash
REGION=us-east-1
BUCKET=my-npa-lambda-bucket

# Create bucket if it doesn't exist (must be same region as stack)
aws s3 mb s3://$BUCKET --region $REGION

# Upload the pre-packaged Lambda
aws s3 cp scripts/npa-publisher-lambda.zip s3://$BUCKET/ --region $REGION
```

### Step 3 — Deploy the Stack

**Option A: Interactive script (easiest)**
```bash
bash scripts/deploy.sh <stack-name> <s3-bucket>
# Example:
bash scripts/deploy.sh netskope-npa-publisher my-npa-lambda-bucket
```
The script prompts for all required values and handles both create and update operations.

**Option B: AWS CLI — new VPC**
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

**Option C: AWS CLI — existing VPC**
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

**Option D: AWS Console**
1. CloudFormation → Create Stack → Upload `templates/netskope-ref-architecture-npa.yaml`
2. Fill in parameters (grouped by section in the console)
3. Acknowledge IAM resource creation → Submit

### Step 4 — Verify Deployment

```bash
# Watch stack events
aws cloudformation describe-stack-events \
  --stack-name netskope-npa-publisher --max-items 10

# Check stack status
aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].StackStatus' --output text

# Watch Lambda logs (replace MyPublisher with your NPAPublisherGroupName)
aws logs tail /aws/lambda/MyPublisher-RegistrationHandler --follow
```

Deployment typically completes in **4–10 minutes**. Verify the publisher appears as **Connected** in the Netskope UI under **Settings → Security Cloud Platform → Publishers**.

## Key Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `NetskopeTenantFQDN` | Yes | e.g. `mytenant.goskope.com` |
| `NPAPublisherGroupName` | Yes | Unique name; used to name all resources |
| `NPAPublisherAMIId` | Yes | From `scripts/get-ami.sh` |
| `NPAPublisherKey` | Yes | EC2 key pair name |
| `NetskopeAPIToken` | Yes | REST API v2 token |
| `LambdaS3Bucket` | Yes | S3 bucket (same region as stack) |
| `LambdaS3Key` | Yes | Default: `npa-publisher-lambda.zip` |
| `CreateNewVPC` | Yes | `yes` or `no` |
| `AppAssociations` | No | `None` / `All` / `tag:name` / `App1,App2` |
| `NPAPublisherInstanceType` | No | Default: `t3.large` |

## Common Operations

### Update AMI (re-registration handled automatically)
```bash
aws cloudformation update-stack \
  --stack-name netskope-npa-publisher \
  --use-previous-template \
  --parameters \
    ParameterKey=NPAPublisherAMIId,ParameterValue=ami-new \
    ParameterKey=<all other params>,UsePreviousValue=true \
  --capabilities CAPABILITY_NAMED_IAM
```

### Connect to Publisher via SSM (no SSH needed)
```bash
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId`].OutputValue' \
  --output text)
aws ssm start-session --target $INSTANCE_ID
```

### Repackage Lambda after editing `lambda_function.py`
```bash
cd scripts && bash package-lambda.sh
# Then re-upload to S3 and update the stack
```

### Delete the stack (also cleans up Netskope registration)
```bash
aws cloudformation delete-stack --stack-name netskope-npa-publisher
aws cloudformation wait stack-delete-complete --stack-name netskope-npa-publisher
```

## Architecture Notes

- Publishers deploy into **private subnets** — no public IPs
- Lambda registers publishers via the Netskope REST API v2, then runs `npa_publisher_wizard` over SSM
- The Custom Resource handles CREATE / UPDATE (AMI change) / DELETE lifecycle automatically
- Multi-AZ deployment creates two publishers; `DependsOn` prevents race conditions on shared app assignments
- The security group allows all RFC1918 egress (for private app discovery) and HTTPS to `0.0.0.0/0` as a temporary workaround — see README for details

## CloudFormation Template Notes

- Primary template: `templates/netskope-ref-architecture-npa.yaml` (dual-AZ, production)
- 3-AZ variant in `examples/` — not the default deployment path
- Template uses inline Lambda (under the 3,500-char limit for the registration handler)
- All resources are named with the stack name as a prefix for easy identification

## Known Constraints

- No auto-scaling; capacity is fixed per AZ
- The NPA registration endpoint (`ns-*.npa.goskope.com`) resolves to undocumented AWS IP ranges — HTTPS egress is currently `0.0.0.0/0` as a workaround
- API tokens are created via admin service accounts in **Settings → Administration → Administrators & Roles** (not the legacy REST API v2 page)
