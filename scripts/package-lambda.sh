#!/bin/bash
# Package Lambda function with dependencies for NPA Publisher
#
# This script creates a deployment package with all required dependencies

set -e

echo "=========================================="
echo "Packaging NPA Publisher Lambda Function"
echo "=========================================="

# Clean up old package
echo ""
echo "Cleaning up old package..."
rm -rf package
rm -f npa-publisher-lambda.zip

# Create package directory
echo "Creating package directory..."
mkdir -p package

# Install dependencies
echo "Installing Python dependencies..."
pip install \
  requests \
  urllib3 \
  certifi \
  charset-normalizer \
  idna \
  -t package/ \
  --quiet

# Copy Lambda function
echo "Copying Lambda function..."
cp lambda_function.py package/

# Add cfnresponse module (required for CloudFormation custom resources)
echo "Adding cfnresponse module..."
cat > package/cfnresponse.py << 'EOF'
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import urllib3

http = urllib3.PoolManager()

SUCCESS = "SUCCESS"
FAILED = "FAILED"


def send(event, context, responseStatus, responseData, physicalResourceId=None, noEcho=False, reason=None):
    """
    Send response to CloudFormation for custom resource
    """
    responseUrl = event['ResponseURL']

    print(f"ResponseURL: {responseUrl}")

    responseBody = {
        'Status': responseStatus,
        'Reason': reason or f'See the details in CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': physicalResourceId or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'NoEcho': noEcho,
        'Data': responseData
    }

    json_responseBody = json.dumps(responseBody)

    print(f"Response body: {json_responseBody}")

    headers = {
        'content-type': '',
        'content-length': str(len(json_responseBody))
    }

    try:
        response = http.request(
            'PUT',
            responseUrl,
            headers=headers,
            body=json_responseBody
        )
        print(f"Status code: {response.status}")
    except Exception as e:
        print(f"send(..) failed executing http.request(..): {e}")
EOF

# Create ZIP file
echo "Creating deployment package..."
cd package
zip -r ../npa-publisher-lambda.zip . -q
cd ..

# Show package info
echo ""
echo "=========================================="
echo "Package created successfully!"
echo "=========================================="
echo ""
echo "File: npa-publisher-lambda.zip"
echo "Size: $(du -h npa-publisher-lambda.zip | cut -f1)"
echo ""
echo "Contents:"
unzip -l npa-publisher-lambda.zip | grep -E "\.(py|so)$" | head -20
echo "..."
echo ""
echo "To upload to S3:"
echo "  aws s3 cp npa-publisher-lambda.zip s3://YOUR-BUCKET/npa-publisher-lambda.zip"
echo ""
echo "Or use inline in CloudFormation with ZipFile (if < 4KB, not recommended for this size)"
