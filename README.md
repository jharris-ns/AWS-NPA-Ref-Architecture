# NPA Publisher - Single Instance Deployment

Automated deployment of Netskope Private Access (NPA) Publisher using CloudFormation with a single EC2 instance and CloudFormation Custom Resources for publisher registration.

## Overview

This solution provides a streamlined deployment of an NPA Publisher with automatic registration to your Netskope tenant. It uses CloudFormation Custom Resources and AWS Systems Manager to handle the publisher setup without requiring manual intervention or exposing secrets.

## VPC Deployment Options

The template supports two deployment modes:

### Option 1: Create New VPC (Recommended for Testing)

- **Automatically creates**: VPC, Internet Gateway, NAT Gateway, Public & Private Subnets
- **Routing**: Configured automatically for internet access


**Parameters**:
```yaml
CreateNewVPC: yes
VPCCIDR: 10.0.0.0/16
PublicSubnetCIDR: 10.0.1.0/24
PrivateSubnetCIDR: 10.0.2.0/24
AvailabilityZone: (optional, auto-selected if not specified)
```

### Option 2: Use Existing VPC (Recommended for Production)
Best for production deployments in your existing infrastructure:
- **Requires**: Existing VPC with private subnets that hava NAT Gateways and relevant route tables


**Parameters**:
```yaml
CreateNewVPC: no
ExistingVPC: vpc-xxxxx
ExistingPrivateSubnet: subnet-xxxxx
```

**Requirements for existing VPC**:
- Private subnets must have routes to dedicated NAT Gateways for redundant internet access
- VPC must have DNS hostnames and DNS support enabled
- Security group allows outbound traffic to internet (HTTPS/443)

## Architecture

```
CloudFormation Stack
    │
    ├─ EC2 Instance (with SSM Agent)
    │
    ├─ Custom Resource (triggers on CREATE/DELETE)
    │      │
    │      └─ Lambda Function
    │             │
    │             ├─ Retrieves API token from Secrets Manager
    │             ├─ Calls Netskope API to create publisher & get registration token
    │             ├─ Waits for instance to be running
    │             ├─ Waits for SSM Agent to be online
    │             ├─ Sends SSM command: npa_publisher_wizard -token "<token>"
    │             └─ Updates private applications
    │
    └─ Outputs (Instance ID, Private IP, etc.)
```

## How It Works

### On Stack Creation (CREATE)

1. **CloudFormation creates EC2 instance** with SSM Agent
2. **Custom Resource triggers Lambda** with instance ID
3. **Lambda requests publisher token** from Netskope API
4. **Lambda waits for EC2** to enter `running` state (up to 2 minutes)
5. **Lambda polls SSM** with exponential backoff until instance is online (up to 4 minutes)
6. **Lambda sends SSM command** to run `npa_publisher_wizard` with token
7. **Lambda waits for command completion** (up to 5 minutes)
8. **Lambda updates private apps** to use this publisher
9. **Custom Resource returns SUCCESS** to CloudFormation

### On Stack Deletion (DELETE)

1. **Custom Resource triggers Lambda** for cleanup
2. **Lambda removes publisher** from all private applications
3. **Lambda deletes publisher** from Netskope
4. **Custom Resource returns SUCCESS**
5. **CloudFormation deletes EC2 instance**

## Key Features

### 1. Smart EC2 State Checking
- Verifies instance is `running` before polling SSM
- Reduces unnecessary polling during instance boot
- Faster failure detection and troubleshooting

### 2. Exponential Backoff for SSM
- Intelligent wait times: starts at 5s, gradually increases to 30s
- Typical SSM registration completes in 2-4 minutes
- Reduces API calls while ensuring quick detection

```python
wait_times = [5, 10, 15, 20, 30, 30, 30, 30, 30, 30]  # seconds
```

### 3. Command Completion Monitoring
- Real-time polling of SSM command status every 5 seconds
- Waits for actual `Success` or `Failed` status
- Detailed stdout/stderr output for troubleshooting

### 4. Configurable Timeouts
Lambda environment variables allow tuning:
- `EC2_READY_TIMEOUT`: 120 seconds (instance boot)
- `SSM_READY_TIMEOUT`: 240 seconds (SSM agent online)
- `COMMAND_TIMEOUT`: 300 seconds (registration command)

## Prerequisites

- AWS Account with appropriate permissions
- Amazon VPC with subnet that has internet connectivity (NAT Gateway) - **OR** choose to create new VPC
- AWS Systems Manager enabled
- Netskope API v2 Token with infrastructure and application management permissions
- NPA Publisher AMI ID for your region
- S3 bucket for Lambda deployment package (any region)

## Deployment

### Option 1: AWS Console

1. Navigate to CloudFormation in AWS Console
2. Choose **Create Stack** → **With new resources**
3. Upload template: `npa-publisher-single-instance.yaml`
4. Fill in parameters:
   - **Netskope Tenant FQDN**: `mytenant.goskope.com`
   - **VPC**: Select your VPC
   - **Subnet**: Select single private subnet with NAT Gateway
   - **Publisher Group Name**: e.g., `MyNPAPublisher`
   - **AMI ID**: Get from AWS Marketplace or Netskope UI
   - **Key Pair**: Select existing EC2 key pair
   - **API Token**: Enter Netskope API v2 token
5. Acknowledge IAM resource creation
6. Click **Create Stack**

### Option 2: AWS CLI

```bash
aws cloudformation create-stack \
  --stack-name netskope-npa-publisher \
  --template-body file://npa-publisher-single-instance.yaml \
  --parameters \
    ParameterKey=NetskopeTenantFQDN,ParameterValue=mytenant.goskope.com \
    ParameterKey=VPC,ParameterValue=vpc-xxxxx \
    ParameterKey=NPAPublisherSubnet,ParameterValue=subnet-xxxxx \
    ParameterKey=NPAPublisherGroupName,ParameterValue=MyNPAPublisher \
    ParameterKey=NPAPublisherAMIId,ParameterValue=ami-xxxxx \
    ParameterKey=NPAPublisherKey,ParameterValue=my-keypair \
    ParameterKey=NetskopeAPIToken,ParameterValue=your-api-token \
    ParameterKey=ProvisionNewAPIToken,ParameterValue=yes \
  --capabilities CAPABILITY_NAMED_IAM
```

## Project Structure

```
AWS-NPA-Ref-Architecture/
├── README.md                              # This file
├── QUICKSTART.md                          # Quick deployment guide
├── deploy.sh                              # Interactive deployment script
├── deploy-example.sh                      # Example deployment script
├── package-lambda.sh                      # Lambda packaging script
├── scripts/
│   ├── lambda_function.py                 # Lambda function source
│   └── npa-publisher-lambda.zip           # Pre-packaged Lambda
└── templates/
    └── npa-publisher-single-instance.yaml # CloudFormation template
```

## Lambda Function Details

The Lambda function (`lambda_function.py`) includes:

### Main Handler
- `lambda_handler()` - Routes CloudFormation events (CREATE/UPDATE/DELETE)

### Create Flow
- `handle_create()` - Full publisher registration workflow
- `wait_for_instance_running()` - EC2 state polling
- `wait_for_command_completion()` - SSM command monitoring

### Delete Flow
- `handle_delete()` - Publisher deregistration workflow

### Utilities
- `call_netskope_api()` - Netskope REST API v2 wrapper
- `get_secret()` - Secrets Manager integration

## Lambda Packaging

The Lambda function requires Python dependencies (requests, urllib3, certifi). Two options:

### Option 1: Use Pre-packaged Lambda (Recommended)

A pre-built package is included at `scripts/npa-publisher-lambda.zip`:

```bash
# Upload to your S3 bucket
aws s3 cp scripts/npa-publisher-lambda.zip s3://my-bucket/

# Update template parameters:
# LambdaS3Bucket: my-bucket
# LambdaS3Key: npa-publisher-lambda.zip
```

### Option 2: Build from Source

Use the included packaging script:

```bash
# Package Lambda with dependencies
./package-lambda.sh

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

## Monitoring

### CloudWatch Logs

Lambda function logs are available in:
```
/aws/lambda/<PublisherGroupName>LambdaFunction
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

## Troubleshooting

### Issue: Stack stuck on "CREATE_IN_PROGRESS"

**Cause**: Custom resource waiting for Lambda response

**Solution**:
1. Check Lambda logs in CloudWatch
2. Look for errors or timeout messages
3. Verify instance has SSM agent running: `aws ssm describe-instance-information`

### Issue: "Instance did not become available in Systems Manager"

**Cause**: SSM agent not running or network connectivity issue

**Solution**:
1. Verify instance is in running state
2. Check security group allows outbound HTTPS (443)
3. Verify subnet has route to internet via NAT Gateway
4. Check IAM instance profile has `AmazonSSMManagedInstanceCore` policy
5. Connect via EC2 Instance Connect and check: `systemctl status amazon-ssm-agent`

### Issue: "Failed to get registration token"

**Cause**: Netskope API authentication or permissions issue

**Solution**:
1. Verify API token in Secrets Manager is correct
2. Check token has infrastructure management permissions
3. Verify tenant FQDN is correct (e.g., `mytenant.goskope.com`)
4. Test API manually: `curl -H "Netskope-Api-Token: <token>" https://<tenant>/api/v2/infrastructure/publishers`

### Issue: Command execution failed

**Cause**: `npa_publisher_wizard` script failed on instance

**Solution**:
1. Check Lambda logs for stderr output
2. Connect to instance via Session Manager
3. Check `/var/log/amazon/ssm/` for SSM agent logs
4. Manually run: `sudo /home/ubuntu/npa_publisher_wizard -token "test"`

## Cost Estimation

Approximate monthly costs for us-east-1 region:

| Resource | Monthly Cost |
|----------|-------------|
| EC2 t3.large (24/7) | ~$60 |
| NAT Gateway (if created) | ~$32 + data transfer |
| Lambda executions | < $1 |
| Secrets Manager | $0.40 |
| **Total (new VPC)** | **~$95/month** |
| **Total (existing VPC)** | **~$63/month** |

*Costs vary by region, instance type, and data transfer volume.*

## Security Considerations

✅ **No secrets in user data** - Token passed via SSM only
✅ **No public IPs** - Instance in private subnet
✅ **Egress-only security group** - No inbound rules
✅ **IAM least privilege** - Minimal permissions
✅ **Secrets Manager** - Encrypted token storage
✅ **SSM Session Manager** - No SSH keys needed

## Limitations

- ❌ **No high availability** - Single instance, single AZ
- ❌ **No auto scaling** - Manual scaling only
- ❌ **Single point of failure** - If instance fails, manual intervention needed
- ❌ **Fixed capacity** - Cannot handle traffic spikes automatically

## When to Use This Version

**✅ Use single instance version when:**
- Development or testing environments
- Static/predictable workloads
- Cost optimization is priority
- Simplicity is more important than HA

**❌ Use auto scaling version when:**
- Production workloads
- Variable traffic patterns
- High availability required
- Multiple availability zones needed

## Naming Convention

**CRITICAL**: Private Applications in Netskope must start their name with the Publisher Group Name.

Example:
```
Publisher Group Name: MyNPAPublisher
Valid App Names:
  - MyNPAPublisher-App1
  - MyNPAPublisher-WebServer
  - MyNPAPublisher-Database
```

This allows the Lambda function to identify which apps belong to this publisher.

## Support

For issues with:
- **This implementation**: Check CloudWatch Logs, SSM command history
- **Netskope API**: Consult [Netskope REST API v2 Documentation](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- **AWS Services**: Check AWS documentation for CloudFormation, Lambda, SSM

## License

Same as parent project - Apache License 2.0
