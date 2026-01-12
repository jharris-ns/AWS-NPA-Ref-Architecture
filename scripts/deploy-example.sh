#!/bin/bash
# Example deployment script for NPA Publisher Single Instance
#
# Usage: ./deploy-example.sh
#
# Prerequisites:
# - AWS CLI configured with appropriate credentials
# - Netskope API v2 token
# - VPC with private subnet and NAT Gateway

set -e

# ==============================================================
# CONFIGURATION - UPDATE THESE VALUES
# ==============================================================

STACK_NAME="netskope-npa-publisher"
NETSKOPE_TENANT="mytenant.goskope.com"
VPC_ID="vpc-xxxxx"
SUBNET_ID="subnet-xxxxx"
PUBLISHER_GROUP_NAME="MyNPAPublisher"
AMI_ID="ami-xxxxx"  # Get from AWS Marketplace or Netskope UI
KEY_PAIR_NAME="my-keypair"
NETSKOPE_API_TOKEN="your-api-token-here"

# Optional parameters
INSTANCE_TYPE="t3.large"
ENVIRONMENT="Development"
COST_CENTER="IT-Operations"
PROJECT="NPA-Publisher"

# ==============================================================
# DEPLOYMENT
# ==============================================================

echo "=========================================="
echo "Deploying NPA Publisher Single Instance"
echo "=========================================="
echo ""
echo "Stack Name: ${STACK_NAME}"
echo "Netskope Tenant: ${NETSKOPE_TENANT}"
echo "VPC: ${VPC_ID}"
echo "Subnet: ${SUBNET_ID}"
echo "Publisher Group: ${PUBLISHER_GROUP_NAME}"
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
  --template-body file://netskope-ref-architecture-npa.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue="${NETSKOPE_TENANT}" \
    ParameterKey=VPC,ParameterValue="${VPC_ID}" \
    ParameterKey=NPAPublisherSubnet,ParameterValue="${SUBNET_ID}" \
    ParameterKey=NPAPublisherGroupName,ParameterValue="${PUBLISHER_GROUP_NAME}" \
    ParameterKey=NPAPublisherAMIId,ParameterValue="${AMI_ID}" \
    ParameterKey=NPAPublisherKey,ParameterValue="${KEY_PAIR_NAME}" \
    ParameterKey=NPAPublisherInstanceType,ParameterValue="${INSTANCE_TYPE}" \
    ParameterKey=NetskopeAPIToken,ParameterValue="${NETSKOPE_API_TOKEN}" \
    ParameterKey=ProvisionNewAPIToken,ParameterValue=yes \
    ParameterKey=EnvironmentTag,ParameterValue="${ENVIRONMENT}" \
    ParameterKey=CostCenterTag,ParameterValue="${COST_CENTER}" \
    ParameterKey=ProjectTag,ParameterValue="${PROJECT}" \
  --capabilities CAPABILITY_NAMED_IAM

echo ""
echo "Stack creation initiated!"
echo ""
echo "Monitor progress with:"
echo "  aws cloudformation describe-stack-events --stack-name ${STACK_NAME}"
echo ""
echo "Wait for completion with:"
echo "  aws cloudformation wait stack-create-complete --stack-name ${STACK_NAME}"
echo ""
echo "Check Lambda logs at:"
echo "  /aws/lambda/${PUBLISHER_GROUP_NAME}LambdaFunction"
echo ""
echo "Once complete, get outputs with:"
echo "  aws cloudformation describe-stacks --stack-name ${STACK_NAME} --query 'Stacks[0].Outputs'"
