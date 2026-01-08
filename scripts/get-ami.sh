#!/bin/bash
#
# Get the latest Netskope Private Access Publisher AMI for a region
#
# Usage:
#   ./scripts/get-ami.sh [region]
#
# If region is not provided, uses AWS CLI default region or us-east-1
#

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Determine region
if [ -n "$1" ]; then
    REGION="$1"
else
    # Try to get default region from AWS CLI config
    REGION=$(aws configure get region 2>/dev/null || echo "us-east-1")
fi

echo -e "${YELLOW}Searching for latest Netskope Private Access Publisher AMI in region: ${REGION}${NC}" >&2
echo "" >&2

# Get the AMI ID
AMI_ID=$(aws ec2 describe-images \
    --owners aws-marketplace \
    --filters "Name=name,Values=*Netskope Private Access Publisher*" \
    --region "$REGION" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text 2>&1)

# Check if command was successful
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to query AMIs in region ${REGION}${NC}" >&2
    echo -e "${YELLOW}Make sure you have AWS credentials configured and access to the region.${NC}" >&2
    exit 1
fi

# Check if AMI was found
if [ -z "$AMI_ID" ] || [ "$AMI_ID" = "None" ]; then
    echo -e "${RED}Error: No Netskope Private Access Publisher AMI found in region ${REGION}${NC}" >&2
    echo "" >&2
    echo -e "${YELLOW}This could mean:${NC}" >&2
    echo "  1. The AMI is not available in this region" >&2
    echo "  2. You need to subscribe to the AMI in AWS Marketplace first" >&2
    echo "  3. There may be a temporary AWS API issue" >&2
    echo "" >&2
    echo -e "${YELLOW}To subscribe to the AMI:${NC}" >&2
    echo "  1. Go to AWS Marketplace" >&2
    echo "  2. Search for 'Netskope Private Access Publisher'" >&2
    echo "  3. Click 'Continue to Subscribe'" >&2
    exit 1
fi

# Get additional details for confirmation
AMI_DETAILS=$(aws ec2 describe-images \
    --owners aws-marketplace \
    --filters "Name=name,Values=*Netskope Private Access Publisher*" \
    --region "$REGION" \
    --query 'sort_by(Images, &CreationDate)[-1].[ImageId,Name,CreationDate]' \
    --output text)

echo -e "${GREEN}✓ Found AMI:${NC}" >&2
echo "" >&2
echo "$AMI_DETAILS" | while IFS=$'\t' read -r id name date; do
    echo -e "  ${GREEN}AMI ID:${NC}       $id" >&2
    echo -e "  ${GREEN}Name:${NC}         $name" >&2
    echo -e "  ${GREEN}Created:${NC}      $date" >&2
done
echo "" >&2

# Output just the AMI ID to stdout (for scripting)
echo "$AMI_ID"
