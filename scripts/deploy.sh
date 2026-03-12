#!/bin/bash
# Interactive deployment script for NPA Publisher Multi-AZ
#
# This script packages the Lambda function and deploys the CloudFormation stack
#
# Usage: ./deploy.sh [stack-name] [s3-bucket]

set -e

# ==============================================================
# CONFIGURATION
# ==============================================================

# Stack name (can be overridden as first argument)
STACK_NAME="${1:-netskope-npa-publisher}"

# S3 bucket for Lambda code (REQUIRED - provide as second argument or set here)
S3_BUCKET="${2}"

# AWS Region (uses default from AWS CLI config if not set)
AWS_REGION="${AWS_REGION:-us-east-1}"

# CloudFormation template
TEMPLATE_FILE="templates/netskope-ref-architecture-npa.yaml"

# Lambda package
LAMBDA_ZIP="npa-publisher-lambda.zip"
LAMBDA_ZIP_PATH="scripts/$LAMBDA_ZIP"

# ==============================================================
# VALIDATION
# ==============================================================

echo "=========================================="
echo "NPA Publisher Deployment (Multi-AZ)"
echo "=========================================="
echo ""

# Check if S3 bucket is provided
if [ -z "$S3_BUCKET" ]; then
    echo "ERROR: S3 bucket not specified"
    echo ""
    echo "Usage: $0 <stack-name> <s3-bucket>"
    echo ""
    echo "Example:"
    echo "  $0 my-npa-publisher my-lambda-bucket"
    echo ""
    echo "The S3 bucket must exist and be in the same region as deployment: $AWS_REGION"
    exit 1
fi

# Check if template exists
if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "ERROR: Template file not found: $TEMPLATE_FILE"
    echo "Make sure you run this script from the project root directory."
    exit 1
fi

echo "Configuration:"
echo "  Stack Name: $STACK_NAME"
echo "  S3 Bucket:  $S3_BUCKET"
echo "  AWS Region: $AWS_REGION"
echo "  Template:   $TEMPLATE_FILE"
echo ""

# ==============================================================
# PACKAGE LAMBDA
# ==============================================================

echo "Step 1: Packaging Lambda function..."
echo "--------------------------------------"

if [ -f "scripts/package-lambda.sh" ]; then
    (cd scripts && ./package-lambda.sh)
elif [ -f "$LAMBDA_ZIP_PATH" ]; then
    echo "Using existing Lambda package: $LAMBDA_ZIP_PATH"
else
    echo "ERROR: Neither package-lambda.sh nor $LAMBDA_ZIP_PATH found"
    exit 1
fi

if [ ! -f "$LAMBDA_ZIP_PATH" ]; then
    echo "ERROR: Lambda package not created at $LAMBDA_ZIP_PATH"
    exit 1
fi

echo "✓ Lambda package ready: $LAMBDA_ZIP_PATH"
echo ""

# ==============================================================
# UPLOAD TO S3
# ==============================================================

echo "Step 2: Uploading Lambda to S3..."
echo "--------------------------------------"

# Check if bucket exists
if ! aws s3 ls "s3://$S3_BUCKET" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "ERROR: S3 bucket does not exist or is not accessible: $S3_BUCKET"
    echo ""
    echo "Create bucket with:"
    echo "  aws s3 mb s3://$S3_BUCKET --region $AWS_REGION"
    exit 1
fi

# Upload Lambda package
aws s3 cp "$LAMBDA_ZIP_PATH" "s3://$S3_BUCKET/$LAMBDA_ZIP" --region "$AWS_REGION"

echo "✓ Lambda uploaded to: s3://$S3_BUCKET/$LAMBDA_ZIP"
echo ""

# ==============================================================
# DEPLOY CLOUDFORMATION STACK
# ==============================================================

echo "Step 3: Deploying CloudFormation stack..."
echo "--------------------------------------"
echo ""

# Check if stack already exists
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "Stack '$STACK_NAME' already exists."
    read -p "Update existing stack? (yes/no): " UPDATE_STACK

    if [ "$UPDATE_STACK" != "yes" ]; then
        echo "Deployment cancelled."
        exit 0
    fi

    OPERATION="update"
else
    OPERATION="create"
fi

echo ""
echo "VPC Configuration"
echo "================="
read -p "Create new VPC with NAT Gateway? (yes/no) [no]: " CREATE_VPC
CREATE_VPC=${CREATE_VPC:-no}
echo ""

if [ "$CREATE_VPC" = "yes" ]; then
    echo "New VPC will be created with:"
    echo "  - Public subnets for NAT Gateways (2 AZs)"
    echo "  - Private subnets for NPA Publishers (2 AZs)"
    echo "  - Internet Gateway and NAT Gateway"
    echo ""
    read -p "VPC CIDR [10.0.0.0/16]: " VPC_CIDR
    VPC_CIDR=${VPC_CIDR:-10.0.0.0/16}
    read -p "Public Subnet CIDR (AZ1) [10.0.1.0/24]: " PUBLIC_SUBNET_CIDR
    PUBLIC_SUBNET_CIDR=${PUBLIC_SUBNET_CIDR:-10.0.1.0/24}
    read -p "Private Subnet CIDR (AZ1) [10.0.2.0/24]: " PRIVATE_SUBNET_CIDR
    PRIVATE_SUBNET_CIDR=${PRIVATE_SUBNET_CIDR:-10.0.2.0/24}
    read -p "Public Subnet CIDR (AZ2) [10.0.3.0/24]: " PUBLIC_SUBNET_CIDR2
    PUBLIC_SUBNET_CIDR2=${PUBLIC_SUBNET_CIDR2:-10.0.3.0/24}
    read -p "Private Subnet CIDR (AZ2) [10.0.4.0/24]: " PRIVATE_SUBNET_CIDR2
    PRIVATE_SUBNET_CIDR2=${PRIVATE_SUBNET_CIDR2:-10.0.4.0/24}

    VPC_PARAMS="ParameterKey=CreateNewVPC,ParameterValue=yes"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=VPCCIDR,ParameterValue=$VPC_CIDR"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=PublicSubnetCIDR,ParameterValue=$PUBLIC_SUBNET_CIDR"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=PrivateSubnetCIDR,ParameterValue=$PRIVATE_SUBNET_CIDR"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=PublicSubnetCIDR2,ParameterValue=$PUBLIC_SUBNET_CIDR2"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=PrivateSubnetCIDR2,ParameterValue=$PRIVATE_SUBNET_CIDR2"
else
    echo "Using existing VPC"
    echo "Both private subnets must have a route to a NAT Gateway for internet access."
    echo ""
    read -p "VPC ID: " VPC_ID
    read -p "Private Subnet ID (AZ1): " SUBNET_ID_1
    read -p "Private Subnet ID (AZ2): " SUBNET_ID_2

    VPC_PARAMS="ParameterKey=CreateNewVPC,ParameterValue=no"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=ExistingVPC,ParameterValue=$VPC_ID"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=ExistingPrivateSubnet,ParameterValue=$SUBNET_ID_1"
    VPC_PARAMS="$VPC_PARAMS ParameterKey=ExistingPrivateSubnet2,ParameterValue=$SUBNET_ID_2"
fi

echo ""
echo "NPA Publisher Configuration"
echo "==========================="
read -p "Netskope Tenant FQDN: " NETSKOPE_TENANT
read -p "Publisher Group Name: " PUBLISHER_NAME
read -p "NPA Publisher AMI ID: " AMI_ID
read -p "EC2 Key Pair Name: " KEY_PAIR
read -p "Instance Type [t3.large]: " INSTANCE_TYPE
INSTANCE_TYPE=${INSTANCE_TYPE:-t3.large}
echo ""
echo "App Associations: which private apps to assign publishers to."
echo "  Enter 'None' to skip, 'All' for all apps, or a comma-separated list of app names."
read -p "App Associations [None]: " APP_ASSOCIATIONS
APP_ASSOCIATIONS=${APP_ASSOCIATIONS:-None}
echo ""
read -sp "Netskope API Token: " API_TOKEN
echo ""

# Build parameters
PARAMETERS="ParameterKey=NetskopeTenantFQDN,ParameterValue=$NETSKOPE_TENANT"
PARAMETERS="$PARAMETERS ParameterKey=NPAPublisherGroupName,ParameterValue=$PUBLISHER_NAME"
PARAMETERS="$PARAMETERS ParameterKey=NPAPublisherAMIId,ParameterValue=$AMI_ID"
PARAMETERS="$PARAMETERS ParameterKey=NPAPublisherKey,ParameterValue=$KEY_PAIR"
PARAMETERS="$PARAMETERS ParameterKey=NPAPublisherInstanceType,ParameterValue=$INSTANCE_TYPE"
PARAMETERS="$PARAMETERS ParameterKey=NetskopeAPIToken,ParameterValue=$API_TOKEN"
PARAMETERS="$PARAMETERS ParameterKey=ProvisionNewAPIToken,ParameterValue=yes"
PARAMETERS="$PARAMETERS ParameterKey=AppAssociations,ParameterValue=$APP_ASSOCIATIONS"
PARAMETERS="$PARAMETERS ParameterKey=LambdaS3Bucket,ParameterValue=$S3_BUCKET"
PARAMETERS="$PARAMETERS ParameterKey=LambdaS3Key,ParameterValue=$LAMBDA_ZIP"
PARAMETERS="$PARAMETERS $VPC_PARAMS"

echo ""
echo "Deploying stack with parameters..."
echo ""

if [ "$OPERATION" = "create" ]; then
    aws cloudformation create-stack \
        --stack-name "$STACK_NAME" \
        --template-body file://"$TEMPLATE_FILE" \
        --parameters $PARAMETERS \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$AWS_REGION"

    echo ""
    echo "✓ Stack creation initiated"
    echo ""
    echo "Waiting for stack to complete..."
    aws cloudformation wait stack-create-complete \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION"
else
    aws cloudformation update-stack \
        --stack-name "$STACK_NAME" \
        --template-body file://"$TEMPLATE_FILE" \
        --parameters $PARAMETERS \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$AWS_REGION"

    echo ""
    echo "✓ Stack update initiated"
    echo ""
    echo "Waiting for stack to complete..."
    aws cloudformation wait stack-update-complete \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION"
fi

# ==============================================================
# DISPLAY OUTPUTS
# ==============================================================

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""

# Get stack outputs
aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
    --output table

echo ""
echo "CloudWatch Logs:"
echo "  aws logs tail /aws/lambda/${STACK_NAME}-${PUBLISHER_NAME}-RegistrationHandler --follow --region $AWS_REGION"
echo ""
echo "To connect to instance via SSM:"
echo "  INSTANCE_ID=\$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION --query 'Stacks[0].Outputs[?OutputKey==\`PublisherInstanceId\`].OutputValue' --output text)"
echo "  aws ssm start-session --target \$INSTANCE_ID"
echo ""
