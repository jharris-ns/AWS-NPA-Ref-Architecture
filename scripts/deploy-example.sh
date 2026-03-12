#!/bin/bash
# Example deployment script for NPA Publisher Multi-AZ
#
# Usage: ./deploy-example.sh
#
# Prerequisites:
# - AWS CLI configured with appropriate credentials
# - Netskope REST API v2 token (create via admin service account)
# - S3 bucket for Lambda package (same region as deployment)
# - VPC with private subnets and NAT Gateway (or use CreateNewVPC=yes)

set -e

# ==============================================================
# CONFIGURATION - UPDATE THESE VALUES
# ==============================================================

STACK_NAME="netskope-npa-publisher"
NETSKOPE_TENANT="mytenant.goskope.com"
NETSKOPE_API_TOKEN="your-api-token-here"

# VPC - existing VPC mode
VPC_ID="vpc-xxxxx"
PRIVATE_SUBNET_1="subnet-xxxxx"     # AZ1 - must have NAT Gateway route
PRIVATE_SUBNET_2="subnet-yyyyy"     # AZ2 - must have NAT Gateway route

# Publisher config
PUBLISHER_GROUP_NAME="MyNPAPublisher"
AMI_ID="ami-xxxxx"  # Get from: bash scripts/get-ami.sh <region>
KEY_PAIR_NAME="my-keypair"

# Lambda package
LAMBDA_S3_BUCKET="my-npa-lambda-bucket"   # Must be in same region as deployment
LAMBDA_S3_KEY="npa-publisher-lambda.zip"

# App associations: "None", "All", or comma-separated app names
APP_ASSOCIATIONS="None"

# Optional parameters
INSTANCE_TYPE="t3.large"
ENVIRONMENT="Development"
COST_CENTER="IT-Operations"
PROJECT="NPA-Publisher"
AWS_REGION="${AWS_REGION:-us-east-1}"

# ==============================================================
# DEPLOYMENT
# ==============================================================

echo "=========================================="
echo "Deploying NPA Publisher (Multi-AZ)"
echo "=========================================="
echo ""
echo "Stack Name:      ${STACK_NAME}"
echo "Region:          ${AWS_REGION}"
echo "Netskope Tenant: ${NETSKOPE_TENANT}"
echo "VPC:             ${VPC_ID}"
echo "Private Subnet 1: ${PRIVATE_SUBNET_1}"
echo "Private Subnet 2: ${PRIVATE_SUBNET_2}"
echo "Publisher Group: ${PUBLISHER_GROUP_NAME}"
echo "App Associations: ${APP_ASSOCIATIONS}"
echo ""
read -p "Proceed with deployment? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Deployment cancelled."
    exit 0
fi

echo ""
echo "Creating CloudFormation stack..."

aws cloudformation create-stack \
  --stack-name "${STACK_NAME}" \
  --template-body file://templates/netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue="${NETSKOPE_TENANT}" \
    ParameterKey=CreateNewVPC,ParameterValue=no \
    ParameterKey=ExistingVPC,ParameterValue="${VPC_ID}" \
    ParameterKey=ExistingPrivateSubnet,ParameterValue="${PRIVATE_SUBNET_1}" \
    ParameterKey=ExistingPrivateSubnet2,ParameterValue="${PRIVATE_SUBNET_2}" \
    ParameterKey=NPAPublisherGroupName,ParameterValue="${PUBLISHER_GROUP_NAME}" \
    ParameterKey=NPAPublisherAMIId,ParameterValue="${AMI_ID}" \
    ParameterKey=NPAPublisherKey,ParameterValue="${KEY_PAIR_NAME}" \
    ParameterKey=NPAPublisherInstanceType,ParameterValue="${INSTANCE_TYPE}" \
    ParameterKey=NetskopeAPIToken,ParameterValue="${NETSKOPE_API_TOKEN}" \
    ParameterKey=ProvisionNewAPIToken,ParameterValue=yes \
    ParameterKey=LambdaS3Bucket,ParameterValue="${LAMBDA_S3_BUCKET}" \
    ParameterKey=LambdaS3Key,ParameterValue="${LAMBDA_S3_KEY}" \
    ParameterKey=AppAssociations,ParameterValue="${APP_ASSOCIATIONS}" \
    ParameterKey=EnvironmentTag,ParameterValue="${ENVIRONMENT}" \
    ParameterKey=CostCenterTag,ParameterValue="${COST_CENTER}" \
    ParameterKey=ProjectTag,ParameterValue="${PROJECT}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}"

echo ""
echo "Stack creation initiated!"
echo ""
echo "Monitor progress with:"
echo "  aws cloudformation describe-stack-events --stack-name ${STACK_NAME} --region ${AWS_REGION}"
echo ""
echo "Wait for completion with:"
echo "  aws cloudformation wait stack-create-complete --stack-name ${STACK_NAME} --region ${AWS_REGION}"
echo ""
echo "Check Lambda logs at:"
echo "  aws logs tail /aws/lambda/${STACK_NAME}-${PUBLISHER_GROUP_NAME}-RegistrationHandler --follow --region ${AWS_REGION}"
echo ""
echo "Once complete, get outputs with:"
echo "  aws cloudformation describe-stacks --stack-name ${STACK_NAME} --query 'Stacks[0].Outputs' --region ${AWS_REGION}"
