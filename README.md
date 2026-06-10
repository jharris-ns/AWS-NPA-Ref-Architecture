# NPA Publisher - Multi-AZ Deployment

Automated deployment of Netskope Private Access (NPA) Publishers using CloudFormation with multi-AZ redundancy and CloudFormation Custom Resources for publisher registration.

## Overview

This solution provides a highly available deployment of NPA Publishers with automatic registration to your Netskope tenant. It supports multi-AZ deployment for production redundancy and uses CloudFormation Custom Resources and AWS Systems Manager to handle the publisher setup without requiring manual intervention or exposing secrets.

![Netskope Private Access](docs/images/npa_reference_architecture.png)

## Documentation

This project includes comprehensive documentation for deployment, operations, and troubleshooting:

- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Detailed architecture overview covering network design, security best practices, high availability, and AWS Well-Architected Framework compliance
- **[QUICKSTART.md](docs/QUICKSTART.md)** - Get started in 10 minutes with a guided quick deployment
- **[DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)** - Complete deployment instructions with all configuration options, prerequisites, and verification steps
- **[OPERATIONS.md](docs/OPERATIONS.md)** - Operational procedures for managing running deployments (replace publishers, update AMIs, scale instances, rotate tokens, monitoring)
- **[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Common issues and solutions with diagnostic commands
- **[DEVOPS-NOTES.md](docs/DEVOPS-NOTES.md)** - Technical deep-dive into SSM integration, Lambda internals, and architecture decisions

**Quick Links:**
- Want to understand the architecture? See **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**
- New to the project? Start with **[QUICKSTART.md](docs/QUICKSTART.md)**
- Need to deploy? See **[DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)**
- Already deployed? Check **[OPERATIONS.md](docs/OPERATIONS.md)**
- Having issues? See **[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**

## IAM Permissions Required

To deploy this CloudFormation template, the deploying user or role needs permissions to create and manage multiple AWS resources. A complete IAM policy with all required permissions is provided in **[templates/deployment-iam-policy.json](templates/deployment-iam-policy.json)**.

### Permission Summary

The deployment requires permissions for the following AWS services:

| Service | Key Permissions | Purpose |
|---------|----------------|---------|
| **CloudFormation** | Create/update/delete stacks | Deploy and manage infrastructure |
| **EC2** | VPC, subnets, NAT gateways, security groups, instances | Network infrastructure and compute |
| **VPC Endpoints** | Create/manage interface endpoints | Private Systems Manager connectivity |
| **IAM** | Create/manage roles and instance profiles | Lambda execution and EC2 instance roles |
| **Lambda** | Create/invoke functions | Publisher registration automation |
| **SSM Parameter Store** | Create/manage parameters | Secure API token storage |
| **S3** | Read objects | Lambda deployment package access |
| **Systems Manager** | Send commands, describe instances | Publisher registration via SSM |
| **CloudWatch Logs** | Create log groups/streams | Lambda function logging |

### Applying the Policy

**Option 1: Create a dedicated deployment user/role** (Recommended)

```bash
# Create IAM policy
aws iam create-policy \
  --policy-name NPAPublisherDeploymentPolicy \
  --policy-document file://templates/deployment-iam-policy.json

# Attach to a user or role
aws iam attach-user-policy \
  --user-name your-deployment-user \
  --policy-arn arn:aws:iam::YOUR-ACCOUNT-ID:policy/NPAPublisherDeploymentPolicy
```

**Option 2: Use existing IAM role with sufficient privileges**

If you already have the necessary AWS permissions, you can deploy directly. The provided policy document serves as reference documentation.

**Option 3: Create an IAM role to assume**

Create a dedicated role with deployment permissions that you can assume via CLI. See **[docs/IAM-ROLE-SETUP.md](docs/IAM-ROLE-SETUP.md)** for complete instructions.

**Option 4: Request permissions from your AWS administrator**

Provide the **[templates/deployment-iam-policy.json](templates/deployment-iam-policy.json)** file to your AWS administrator to create appropriate permissions.

### Least Privilege Considerations

The provided policy follows AWS least privilege best practices:
- IAM role permissions are scoped to resources with `NPAPublisher` or `RegistrationHandler` in the name
- SSM Parameter Store access is limited to the stack's API token parameter
- Lambda and CloudWatch Logs permissions are scoped to the registration handler function
- No `*:*` permissions are granted

## VPC Deployment Options

The template supports two deployment modes:

### Option 1: Create New VPC 

- **Automatically creates**: VPC, Internet Gateway, NAT Gateways (2), Public & Private Subnets (2 AZs)
- **Routing**: Configured automatically for redundant internet access
- **High Availability**: Multi-AZ deployment with redundant NAT Gateways

**When creating a new VPC** (CreateNewVPC: yes):
- ✅ VPC endpoints are automatically created for Systems Manager (ssm, ec2messages, ssmmessages)
- ⚠️ Security group allows all HTTPS egress (temporary workaround - see [Netskope IP Ranges](#netskope-ip-ranges))
- ✅ NAT Gateways remain in place for Netskope connectivity
- ✅ No internet traffic required for Systems Manager

**Parameters**:
```yaml
CreateNewVPC: yes
VPCCIDR: 10.0.0.0/16
PublicSubnetCIDR: 10.0.1.0/24
PrivateSubnetCIDR: 10.0.2.0/24
AvailabilityZone: (optional, auto-selected if not specified)
PublicSubnetCIDR2: 10.0.3.0/24
PrivateSubnetCIDR2: 10.0.4.0/24
AvailabilityZone2: (optional, auto-selected if not specified)
```

### Option 2: Use Existing VPC 

- **Requires**: Existing VPC with private subnets (2 AZs) that have NAT Gateways and relevant route tables
- **High Availability**: Deploy instances across multiple availability zones
- **DNS** VPC must have DNS hostnames and DNS support enabled
- **Security** Security group with the configuration described [here](#security-group-requirements)
  - Netskope NewEdge Data Centers ([see IP ranges below](#netskope-newedge-data-center-ip-ranges))

**Parameters**:
```yaml
CreateNewVPC: no
ExistingVPC: vpc-xxxxx
ExistingPrivateSubnet: subnet-xxxxx  # First AZ
ExistingPrivateSubnet2: subnet-yyyyy # Second AZ (optional but recommended)
```

**When using an existing VPC** (CreateNewVPC: no):
- ⚠️ Security group allows all HTTPS egress (temporary workaround - see [Netskope IP Ranges](#netskope-ip-ranges))
- ✅ Systems Manager connectivity works by default (either via VPC endpoints or NAT Gateway)
- ⚠️ Your VPC must have either:
  - **Option 1 (Recommended)**: VPC endpoints for Systems Manager
  - **Option 2**: NAT Gateway with route to internet (0.0.0.0/0)
- ⚠️ Previous note: If using restrictive rules, the code block below is no longer needed:
    ```yaml
    # NO LONGER NEEDED - Security group allows all HTTPS by default
    - IpProtocol: tcp
      FromPort: 443
      ToPort: 443
      CidrIp: 0.0.0.0/0
      Description: AWS Systems Manager (if no VPC endpoints)
    ```

## Architecture

```
CloudFormation Stack
    │
    ├─ SSM Parameter (stores Netskope API token)
    │
    ├─ EC2 Instance (with SSM Agent)
    │
    ├─ Custom Resource (triggers on CREATE/UPDATE/DELETE)
    │      │
    │      └─ Lambda Function
    │             │
    │             ├─ CREATE: Reads API token → registers publisher → runs wizard via SSM → assigns apps
    │             ├─ UPDATE: Detects if instance was replaced (AMI change) → re-registers new instance
    │             │          and deregisters old publisher; no-op if instance ID unchanged
    │             └─ DELETE: Removes publisher from apps → waits for disconnect → deletes from Netskope
    │
    └─ Outputs (Instance ID, Private IP, etc.)
```

## Security Group Requirements

The NPA Publisher instances require outbound HTTPS (443) access to the following destinations:

### AWS Systems Manager Endpoints
The security group must allow outbound traffic to AWS Systems Manager endpoints for SSM agent functionality. These endpoints are regional and automatically resolved via AWS DNS.

### Netskope IP Ranges

⚠️ **TEMPORARY WORKAROUND**: The security group currently allows all outbound HTTPS (443) traffic (`0.0.0.0/0`).

**Why?** Netskope NPA registration endpoints (`ns-*.{region}.npa.goskope.com`) are hosted on AWS infrastructure using **undocumented IP ranges** that are not included in Netskope's official documentation.

**Known Issue:**
- **Documented ranges** (from [NewEdge IP Ranges](https://docs.netskope.com/en/newedge-ip-ranges-for-allowlisting/)): `8.36.116.0/24`, `8.39.144.0/24`, `31.186.239.0/24`, `163.116.128.0/17`, `162.10.0.0/17`
- **Discovered ranges** (not documented): `18.116.0.0/16` (us-east-2) - registration endpoint `ns-26784.us-dfw3.npa.goskope.com` resolves to `18.116.181.47`
- **Missing**: Unknown IP ranges for registration endpoints in other AWS regions

**Action Required:**
1. Contact Netskope support to obtain the complete list of IP ranges for NPA registration endpoints across all regions
2. Update the security group egress rules with specific IP ranges once obtained
3. Remove the temporary `0.0.0.0/0` rule

**Documented NewEdge IP Ranges** (for reference):

| CIDR Block | IP Range | Description |
|------------|----------|-------------|
| `8.36.116.0/24` | 8.36.116.0 – 8.36.116.255 | NewEdge DC |
| `8.39.144.0/24` | 8.39.144.0 – 8.39.144.255 | NewEdge DC |
| `31.186.239.0/24` | 31.186.239.0 – 31.186.239.255 | NewEdge DC |
| `163.116.128.0/17` | 163.116.128.0 – 163.116.255.255 | NewEdge DC |
| `162.10.0.0/17` | 162.10.0.0 – 162.10.127.255 | NewEdge DC |

**Note**: The documented ranges cover NewEdge data centers but do NOT include AWS-hosted NPA registration endpoints.

### RFC1918 Private Address Ranges

The security group allows outbound traffic to all RFC1918 private address ranges to support **network segment discovery** for private applications. This enables the NPA Publisher to discover and communicate with private applications across your internal network segments.

**Configured Ranges:**
- `10.0.0.0/8` - Class A private addresses
- `172.16.0.0/12` - Class B private addresses
- `192.168.0.0/16` - Class C private addresses

For more information on configuring network segment discovery, see [Netskope documentation: Configure App Discovery for Private Apps](https://docs.netskope.com/en/configure-app-discovery-for-private-apps).

## How It Works

### On Stack Creation (CREATE)

1. **CloudFormation creates EC2 instance** with SSM Agent
2. **Custom Resource triggers Lambda** with instance ID
3. **Lambda requests publisher token** from Netskope API
4. **Lambda waits for EC2** to enter `running` state (up to 2 minutes)
5. **Lambda polls SSM** with exponential backoff until instance is online (up to 4 minutes)
6. **Lambda sends SSM command** to run `npa_publisher_wizard` with token
7. **Lambda waits for command completion** (up to 5 minutes)
8. **Lambda assigns publisher to private apps** based on the `AppAssociations` parameter (`None`, `All`, or a comma-separated list of app names)
9. **Custom Resource returns SUCCESS** to CloudFormation

### On AMI Update (UPDATE — instance replaced)

When you change `NPAPublisherAMIId`, CloudFormation replaces the EC2 instance and sends an UPDATE event to the Custom Resource with both the old and new instance IDs.

1. **Lambda detects instance ID change** (`OldResourceProperties.InstanceId` ≠ `ResourceProperties.InstanceId`)
2. **Lambda registers the new instance** — full CREATE flow (API call, SSM wizard, app assignment)
3. **Lambda deregisters the old publisher** — removes from apps and deletes from Netskope (best-effort; skipped if Lambda time budget < 90s)
4. **CloudFormation terminates the old instance** during the cleanup phase

No manual Netskope console cleanup is needed. Registrations are serialized via `DependsOn` to prevent race conditions when multiple publishers update shared app definitions simultaneously.

**Note:** Instance type changes (`NPAPublisherInstanceType`) are performed in-place (stop/start) and do **not** trigger re-registration — the instance ID does not change.

### On Stack Deletion (DELETE)

1. **Custom Resource triggers Lambda** for cleanup
2. **Lambda removes publisher** from all associated private applications
3. **Lambda deletes publisher** from Netskope
4. **Custom Resource returns SUCCESS**
5. **CloudFormation deletes EC2 instance**

**Note:** The Lambda function ensures clean removal by automatically unassigning the publisher from all associated apps before deletion.

## Lambda Function Key Features

### 1. Smart EC2 State Checking
- Verifies instance is `running` before polling SSM
- Reduces unnecessary polling during instance boot
- Faster failure detection and troubleshooting

### 2. Exponential Backoff for SSM
- Intelligent wait times: starts at 5s, gradually increases to 30s
- Typical SSM registration completes in 2-4 minutes

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

## Getting Started

For deployment instructions, see:
- **[QUICKSTART.md](docs/QUICKSTART.md)** - Quick 10-minute deployment guide
- **[DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)** - Complete deployment documentation with all options

## Project Structure

```
AWS-NPA-Ref-Architecture/
├── README.md                              # This file - Project overview
├── docs/
│   ├── ARCHITECTURE.md                    # Detailed architecture overview and AWS best practices
│   ├── QUICKSTART.md                      # Quick 10-minute deployment guide
│   ├── DEPLOYMENT_GUIDE.md                # Complete deployment documentation
│   ├── OPERATIONS.md                      # Operational procedures (replace publishers, updates, monitoring)
│   ├── TROUBLESHOOTING.md                 # Common issues and solutions
│   ├── DEVOPS-NOTES.md                    # Technical deep-dive (SSM, Lambda internals)
│   ├── IAM-ROLE-SETUP.md                  # Guide for creating and assuming IAM roles for deployment
│   └── images/
│       └── npa_reference_architecture.png # Architecture diagram
├── scripts/
│   ├── deploy.sh                          # Interactive deployment script
│   ├── deploy-example.sh                  # Example deployment script
│   ├── get-ami.sh                         # Helper script to find latest AMI
│   ├── lambda_function.py                 # Lambda function source
│   ├── npa-publisher-lambda.zip           # Pre-packaged Lambda
│   └── package-lambda.sh                  # Lambda packaging script
└── templates/
    ├── netskope-ref-architecture-npa.yaml # CloudFormation template
    └── deployment-iam-policy.json         # IAM policy for deployment permissions
```

## Lambda Function Details

The Lambda function (`scripts/lambda_function.py`) handles publisher lifecycle:

### Main Components
- `lambda_handler()` - Routes CloudFormation events (CREATE/UPDATE/DELETE)
- `handle_create()` - Full publisher registration workflow including automatic app assignment
- `handle_delete()` - Publisher deregistration and automatic app cleanup
- `wait_for_instance_running()` - EC2 state polling with timeout
- `wait_for_command_completion()` - SSM command status monitoring
- `call_netskope_api()` - Netskope REST API v2 wrapper
- `get_secret()` - SSM Parameter Store integration

### UPDATE Handler (AMI Re-Registration)

When a stack update replaces EC2 instances (e.g., AMI change), the Lambda UPDATE handler:
1. Compares `OldResourceProperties.InstanceId` with `ResourceProperties.InstanceId`
2. If different: runs the full CREATE flow for the new instance, then best-effort deregisters the old publisher
3. If same: returns SUCCESS immediately with no action (handles tag changes, parameter updates, etc.)

A 90-second time budget guard prevents the old publisher cleanup from causing a Lambda timeout. If cleanup is skipped, a warning is written to CloudWatch Logs.

### Automatic Application Management

The Lambda function automatically manages private application assignments:

**On CREATE:**
- Queries all private applications via Netskope API
- Identifies apps whose names contain the Publisher Group Name
- Automatically assigns the new publisher to matching apps
- Logs the number of apps updated

**On DELETE:**
- Queries all private applications via Netskope API
- Removes the publisher from all associated apps
- Ensures clean deletion without orphaned assignments

**API Endpoints Used:**
- `GET /api/v2/steering/apps/private` - List all private applications
- `PATCH /api/v2/steering/apps/private/{app_id}` - Update app publisher assignments

**For detailed technical documentation**, including SSM integration details, error handling, retry logic, and troubleshooting, see [DEVOPS-NOTES.md](docs/DEVOPS-NOTES.md).

## Cost Estimation

Approximate monthly costs for us-east-1 region:

| Resource | Monthly Cost |
|----------|-------------|
| EC2 t3.large x2 (24/7, 2 AZs) | ~$120 |
| NAT Gateway x2 (if created) | ~$64 + data transfer |
| VPC Endpoints x3 (ssm, ec2messages, ssmmessages, 2 AZs) | ~$44 |
| Lambda executions | < $1 |
| SSM Parameter Store | Free |
| **Total (new VPC, multi-AZ)** | **~$229/month** |
| **Total (existing VPC, multi-AZ)** | **~$121/month** |
| **Total (single AZ)** | **~$95/month** |

*Costs vary by region, instance type, and data transfer volume. Multi-AZ deployment recommended for production.*

**Note**: VPC endpoints reduce NAT Gateway data transfer costs for Systems Manager traffic. The VPC endpoint cost is offset by reduced NAT Gateway data transfer charges, making the actual increase smaller than shown above.

## Security Considerations

- ✅ **No secrets in user data** - Token passed via SSM only
- ✅ **No public IPs** - Instance in private subnet
- ⚠️ **Egress rules** - Currently allows all HTTPS (0.0.0.0/0) as temporary workaround (see [Netskope IP Ranges](#netskope-ip-ranges))
- ✅ **Port 80 egress** - Permitted to `0.0.0.0/0` for publisher auto-updates (`*.ubuntu.com`), per Netskope documentation
- ✅ **VPC endpoints for Systems Manager** - Private connectivity without internet routing
- ✅ **IAM least privilege** - Minimal permissions; use `AdditionalPolicyArn1/2/3` parameters to attach extra managed policies if needed
- ✅ **SSM Parameter Store** - Secure token storage (supports SecureString encryption)
- ✅ **SSM Session Manager** - No SSH keys needed
- ✅ **No inbound rules** - Publishers only initiate outbound connections

## Limitations

- ❌ **No auto scaling** - Fixed capacity (single instance per AZ)
- ❌ **Manual scaling** - Capacity adjustments require stack updates
- ❌ **Instance-level redundancy** - Instance failure requires manual intervention (stack re-creation)

## Use Cases

**✅ Ideal for:**
- Development and testing environments
- Proof-of-concept deployments
- Static/predictable workloads
- Cost-optimized deployments with multi-AZ redundancy
- Small to medium organizations
- Production workloads with predictable traffic patterns

**✅ Built-in redundancy:**
- Multi-AZ deployment support (2 availability zones)
- Automatic failover between zones
- Redundant NAT Gateways for high availability

**⚠️ Considerations for production:**
- Fixed capacity per AZ (monitor usage and scale manually if needed)
- Instance failures require stack re-creation (automated via CloudFormation)

## Private App Associations

The `AppAssociations` CloudFormation parameter controls which existing private apps are assigned to the publisher during deployment:

| Value | Behaviour |
|-------|-----------|
| `None` (default) | No automatic assignment — assign publishers manually in the Netskope UI |
| `All` | Assign the publisher to every existing private app in the tenant |
| `App1,App2` | Comma-separated list of exact app names to assign |

App names are matched exactly (case-sensitive) against the names shown in the Netskope UI. If you create new apps after deployment, assign publishers manually under **Settings → Security Cloud Platform → App Definition**.

## Additional Resources

### Project Documentation
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Detailed architecture overview and AWS best practices
- **[QUICKSTART.md](docs/QUICKSTART.md)** - Quick 10-minute deployment guide
- **[DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)** - Complete deployment instructions
- **[IAM-ROLE-SETUP.md](docs/IAM-ROLE-SETUP.md)** - Guide for creating and assuming IAM roles for deployment
- **[OPERATIONS.md](docs/OPERATIONS.md)** - Operational procedures (replace publishers, AMI updates, monitoring)
- **[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[DEVOPS-NOTES.md](docs/DEVOPS-NOTES.md)** - Technical deep-dive (SSM, Lambda internals)

### External Resources
- [Netskope REST API v2](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- [AWS Systems Manager](https://docs.aws.amazon.com/systems-manager/)
- [CloudFormation Custom Resources](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-custom-resources.html)
- [NewEdge IP Ranges](https://docs.netskope.com/en/newedge-ip-ranges-for-allowlisting)

## License

BSD 3-Clause License. See [LICENSE](LICENSE) for details.
