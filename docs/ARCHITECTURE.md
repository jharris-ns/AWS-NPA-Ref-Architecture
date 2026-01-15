# Architecture Overview

Comprehensive architecture documentation for the Netskope Private Access (NPA) Publisher deployment on AWS.

## Table of Contents

- [Architecture Diagram](#architecture-diagram)
- [Component Overview](#component-overview)
- [Network Architecture](#network-architecture)
- [Security Architecture](#security-architecture)
- [High Availability Design](#high-availability-design)
- [AWS Best Practices](#aws-best-practices)
- [Deployment Flow](#deployment-flow)
- [Resource Dependencies](#resource-dependencies)

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    VPC (10.0.0.0/16)                                   │ │
│  │                                                                        │ │
│  │  ┌──────────────────────┐         ┌──────────────────────┐          │ │
│  │  │  Availability Zone 1 │         │  Availability Zone 2 │          │ │
│  │  │                      │         │                      │          │ │
│  │  │  ┌────────────────┐ │         │  ┌────────────────┐ │          │ │
│  │  │  │ Public Subnet  │ │         │  │ Public Subnet  │ │          │ │
│  │  │  │ 10.0.1.0/24    │ │         │  │ 10.0.3.0/24    │ │          │ │
│  │  │  │                │ │         │  │                │ │          │ │
│  │  │  │  NAT Gateway   │ │         │  │  NAT Gateway   │ │          │ │
│  │  │  └────────┬───────┘ │         │  └────────┬───────┘ │          │ │
│  │  │           │         │         │           │         │          │ │
│  │  │  ┌────────▼───────┐ │         │  ┌────────▼───────┐ │          │ │
│  │  │  │ Private Subnet │ │         │  │ Private Subnet │ │          │ │
│  │  │  │ 10.0.2.0/24    │ │         │  │ 10.0.4.0/24    │ │          │ │
│  │  │  │                │ │         │  │                │ │          │ │
│  │  │  │ ┌────────────┐ │ │         │  │ ┌────────────┐ │ │          │ │
│  │  │  │ │    NPA     │ │ │         │  │ │    NPA     │ │ │          │ │
│  │  │  │ │ Publisher  │ │ │         │  │ │ Publisher  │ │ │          │ │
│  │  │  │ │ Instance 1 │ │ │         │  │ │ Instance 2 │ │ │          │ │
│  │  │  │ └────────────┘ │ │         │  │ └────────────┘ │ │          │ │
│  │  │  │                │ │         │  │                │ │          │ │
│  │  │  │ VPC Endpoints: │ │         │  │ VPC Endpoints: │ │          │ │
│  │  │  │ - ssm          │ │         │  │ - ssm          │ │          │ │
│  │  │  │ - ec2messages  │ │         │  │ - ec2messages  │ │          │ │
│  │  │  │ - ssmmessages  │ │         │  │ - ssmmessages  │ │          │ │
│  │  │  └────────────────┘ │         │  └────────────────┘ │          │ │
│  │  └──────────────────────┘         └──────────────────────┘          │ │
│  │           │                                    │                     │ │
│  │           └────────────────┬───────────────────┘                     │ │
│  │                            │                                         │ │
│  └────────────────────────────┼─────────────────────────────────────────┘ │
│                               │                                           │
│                               │ Internet Gateway                          │
│                               ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                    Control Plane (AWS Services)                     │ │
│  │                                                                     │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │ │
│  │  │   Lambda     │  │   Secrets    │  │  CloudWatch  │            │ │
│  │  │  Function    │  │   Manager    │  │    Logs      │            │ │
│  │  │ (Registration)│  │              │  │              │            │ │
│  │  └──────┬───────┘  └──────┬───────┘  └──────────────┘            │ │
│  │         │                 │                                        │ │
│  │         ▼                 ▼                                        │ │
│  │  ┌──────────────────────────────────┐                             │ │
│  │  │   AWS Systems Manager (SSM)      │                             │ │
│  │  │   - Send Command                 │                             │ │
│  │  │   - Session Manager              │                             │ │
│  │  └──────────────────────────────────┘                             │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   │ HTTPS 443
                                   ▼
                    ┌───────────────────────────────┐
                    │  Netskope NewEdge Network     │
                    │  (Publisher Management)       │
                    └───────────────────────────────┘
```

## Component Overview

### Core Infrastructure Components

#### 1. **VPC (Virtual Private Cloud)**
- **Purpose**: Isolated network environment for NPA Publishers
- **CIDR**: 10.0.0.0/16 (default, configurable)
- **DNS**: Enabled for hostname resolution and VPC endpoint functionality
- **Features**:
  - Multi-AZ design for high availability
  - Public and private subnet segregation
  - Redundant internet connectivity via NAT Gateways

#### 2. **Subnets**
- **Public Subnets** (10.0.1.0/24, 10.0.3.0/24):
  - Host NAT Gateways for outbound internet access
  - Route table: 0.0.0.0/0 → Internet Gateway

- **Private Subnets** (10.0.2.0/24, 10.0.4.0/24):
  - Host NPA Publisher EC2 instances
  - No direct internet access (egress via NAT Gateway)
  - Route table: 0.0.0.0/0 → NAT Gateway (in same AZ)

#### 3. **Internet Gateway**
- **Purpose**: Provides internet connectivity for the VPC
- **Attached to**: VPC
- **Used by**: NAT Gateways for outbound traffic

#### 4. **NAT Gateways (2 instances)**
- **Purpose**: Enable outbound internet access from private subnets
- **Deployment**: One per availability zone for redundancy
- **Benefits**:
  - High availability (zone-isolated failure domains)
  - Managed service (automatic scaling, patching)
  - Static Elastic IP for consistent egress IP

#### 5. **VPC Endpoints (Interface Type)**
- **Services**: ssm, ec2messages, ssmmessages
- **Purpose**: Private connectivity to AWS Systems Manager without internet routing
- **Deployment**: Multi-AZ (one endpoint per service in each AZ)
- **Benefits**:
  - Enhanced security (traffic stays on AWS backbone)
  - Reduced NAT Gateway data transfer costs
  - Lower latency

#### 6. **EC2 Instances (NPA Publishers)**
- **Type**: t3.large (default, configurable)
- **AMI**: Netskope Private Access Publisher (AWS Marketplace)
- **Deployment**: One instance per availability zone
- **Networking**:
  - Private subnet placement (no public IP)
  - Elastic Network Interface (ENI) with private IP
  - Security group attached
- **IAM**: Instance profile with Systems Manager permissions
- **Monitoring**: Detailed CloudWatch monitoring enabled
- **Tags**: Includes CostCenter, Project, Environment, aws-apn-id

#### 7. **Security Groups**
- **NPAPublisherSecurityGroup**:
  - Ingress: None (publishers only initiate outbound connections)
  - Egress: Restricted to Netskope NewEdge IPs + VPC endpoints

#### 8. **Lambda Function**
- **Purpose**: Custom resource handler for publisher lifecycle management
- **Runtime**: Python 3.11
- **Triggers**: CloudFormation custom resource events (CREATE, UPDATE, DELETE)
- **Functions**:
  - Retrieve API token from Secrets Manager
  - Create publisher in Netskope via REST API v2
  - Wait for EC2 instance running state
  - Poll SSM until agent is online
  - Send registration command via SSM Run Command
  - Assign publisher to matching private applications
  - Clean up on stack deletion
- **Timeout**: 15 minutes
- **IAM Role**: Scoped permissions for EC2, SSM, Secrets Manager, CloudWatch Logs

#### 9. **Secrets Manager Secret**
- **Purpose**: Secure storage for Netskope API v2 token
- **Encryption**: AWS KMS (default key)
- **Access**: Lambda function only (via IAM policy)
- **Lifecycle**: Created with stack, deleted with stack

#### 10. **CloudWatch Logs**
- **Log Groups**:
  - `/aws/lambda/<PublisherGroupName>-RegistrationHandler`: Lambda function logs
- **Retention**: 7 days (default)
- **Content**:
  - Publisher creation events
  - SSM command execution status
  - API responses from Netskope
  - Private application assignment results

## Network Architecture

### Traffic Flows

#### 1. **Publisher to Netskope NewEdge**
```
NPA Publisher → Security Group (egress rules) → NAT Gateway →
Internet Gateway → Internet → Netskope NewEdge Data Centers
```
- **Port**: HTTPS (443)
- **Destination**: Netskope NewEdge IP ranges (see README.md)
- **Purpose**: Publisher registration, management, tunnel establishment

#### 2. **Systems Manager Communication**
```
NPA Publisher → VPC Endpoint (ssm/ec2messages/ssmmessages) →
AWS PrivateLink → AWS Systems Manager Service
```
- **Port**: HTTPS (443)
- **Destination**: VPC endpoint ENIs in private subnet
- **Purpose**: SSM agent communication, Run Command, Session Manager
- **Security**: Traffic never leaves AWS network

#### 3. **Lambda to Netskope API**
```
Lambda Function → NAT Gateway → Internet Gateway →
Internet → Netskope API (api.netskope.com)
```
- **Port**: HTTPS (443)
- **Method**: REST API v2 calls
- **Purpose**: Publisher CRUD operations, private app management

#### 4. **Lambda to AWS Services**
```
Lambda → AWS Service Endpoint (Secrets Manager, EC2, SSM, CloudWatch)
```
- **Protocol**: AWS SDK (HTTPS)
- **Network**: AWS internal network
- **Purpose**: Retrieve secrets, describe instances, send commands, write logs

### Network Segmentation

#### Isolation Strategy
1. **Control Plane Isolation**: Lambda and AWS services operate in AWS-managed networks
2. **Data Plane Isolation**: Publishers in private subnets with no direct internet access
3. **Management Plane Access**: Via Systems Manager Session Manager (no SSH/bastion required)

#### Security Zones
- **Public Zone**: NAT Gateways only (no compute resources)
- **Private Zone**: NPA Publishers (compute resources)
- **AWS Service Zone**: Managed services (Lambda, Secrets Manager, etc.)

## Security Architecture

### Defense in Depth - Multi-Layer Security

#### Layer 1: Network Security

**VPC-Level Controls:**
- Private subnet placement for all compute resources
- No public IP addresses assigned to publishers
- Network ACLs (default: allow all, customizable)
- Internet Gateway only accessible via NAT Gateways

**Security Group Configuration:**
```yaml
Ingress Rules: NONE
  - Publishers never accept inbound connections
  - Zero attack surface from internet

Egress Rules (Restrictive):
  1. HTTPS (443) → Netskope NewEdge IPs (5 CIDR blocks)
     Purpose: Publisher management and tunneling

  2. HTTPS (443) → VPC Endpoint ENIs (conditional)
     Purpose: Systems Manager communication
     Security: Automatic, uses AWS internal routing
```

**Why This Works:**
- **Principle of Least Privilege**: Only necessary outbound connections allowed
- **No 0.0.0.0/0 egress**: Prevents data exfiltration or C2 communication
- **IP Allowlisting**: Netskope NewEdge IPs are documented and stable
- **Defense Against Compromise**: Even if instance compromised, attacker cannot reach arbitrary internet destinations

#### Layer 2: IAM Least Privilege

**EC2 Instance Role (NPAPublisherRole):**
```yaml
Permissions:
  - ssm:UpdateInstanceInformation      # Required for SSM agent
  - ssmmessages:CreateControlChannel   # Required for Session Manager
  - ssmmessages:CreateDataChannel      # Required for Session Manager
  - ssmmessages:OpenControlChannel     # Required for Session Manager
  - ssmmessages:OpenDataChannel        # Required for Session Manager
  - ec2messages:GetMessages            # Required for Run Command

Restrictions:
  - No EC2 control plane permissions (cannot create/modify/delete resources)
  - No access to Secrets Manager
  - No access to other AWS services
  - No PassRole permissions
```

**Lambda Execution Role (NPAPublisherRegistrationHandlerRole):**
```yaml
Permissions:
  EC2:
    - ec2:DescribeInstances             # Query instance state
    - ec2:DescribeInstanceStatus        # Verify instance health
    Resource: '*' (read-only, scoped by region)

  SSM:
    - ssm:SendCommand                   # Execute registration command
    - ssm:GetCommandInvocation          # Poll command status
    - ssm:DescribeInstanceInformation   # Check SSM agent status
    Resource: Scoped to NPA Publisher instances only

  Secrets Manager:
    - secretsmanager:GetSecretValue     # Retrieve API token
    Resource: Specific secret ARN only (NetskopeAPIToken-*)

  CloudWatch Logs:
    - logs:CreateLogGroup               # Create log group
    - logs:CreateLogStream              # Create log stream
    - logs:PutLogEvents                 # Write logs
    Resource: Specific log group only (/aws/lambda/*RegistrationHandler*)

Restrictions:
  - Cannot create/modify/delete infrastructure
  - Cannot assume other roles (no PassRole)
  - Cannot access other secrets
  - Time-limited (15-minute Lambda timeout)
```

**Deployment IAM Policy (see templates/deployment-iam-policy.json):**
- Scoped resource names: `*NPAPublisher*`, `*RegistrationHandler*`
- No wildcard permissions (`*:*`)
- Minimal privilege for CloudFormation deployment

#### Layer 3: Secrets Management

**Netskope API Token Storage:**
- **Service**: AWS Secrets Manager
- **Encryption**: AWS KMS default key (AES-256)
- **Access**: Lambda function only (IAM policy enforcement)
- **Lifecycle**: Automatic deletion when stack deleted
- **Rotation**: Manual (user responsibility)

**No Secrets in User Data:**
- EC2 user data is empty (no bootstrap scripts)
- Registration token passed via SSM Run Command (encrypted in transit)
- Token never written to disk on publisher instance

**Token Handling Flow:**
```
1. User provides API token as CloudFormation parameter (marked NoEcho)
2. CloudFormation stores token in Secrets Manager
3. Lambda retrieves token from Secrets Manager (encrypted in transit)
4. Lambda calls Netskope API to get registration token (short-lived)
5. Lambda passes registration token via SSM (encrypted channel)
6. Publisher registers with Netskope (token consumed, expires)
7. No long-lived tokens stored on publisher instance
```

#### Layer 4: Data Encryption

**Encryption in Transit:**
- All API calls: TLS 1.2+ (AWS SDK enforced)
- SSM communication: TLS 1.2+ to VPC endpoints
- Netskope communication: TLS 1.3 (Netskope enforced)
- Lambda to AWS services: TLS (AWS internal network)

**Encryption at Rest:**
- Secrets Manager: AES-256 (KMS encrypted)
- CloudWatch Logs: AES-256 (default encryption)
- EBS volumes: Optional (user can enable via AMI or parameter)

#### Layer 5: Access Control

**Management Access:**
- **No SSH Required**: Systems Manager Session Manager for shell access
- **No Bastion Hosts**: Direct SSM connection from AWS Console or CLI
- **MFA Enforcement**: Via AWS IAM policies (user responsibility)
- **Audit Trail**: All SSM sessions logged to CloudWatch

**API Access:**
- **Netskope API**: Token-based authentication (stored in Secrets Manager)
- **AWS API**: IAM-based authentication (SigV4)

#### Layer 6: Monitoring and Logging

**CloudWatch Logs:**
- Lambda execution logs (all API calls, decisions, errors)
- SSM command output (registration success/failure)
- Retention: 7 days (configurable)

**CloudWatch Metrics:**
- EC2 detailed monitoring enabled
- Custom metrics available via CloudWatch agent (user can add)

**VPC Flow Logs:**
- Optional (not created by template)
- User can enable for traffic analysis

**AWS CloudTrail:**
- Recommended (not created by template)
- Captures all API calls (CloudFormation, Lambda, EC2, etc.)

### Security Best Practices Implemented

#### OWASP Top 10 for Infrastructure

1. **A01: Broken Access Control**
   - ✅ IAM least privilege enforced
   - ✅ No public access to resources
   - ✅ Security group ingress blocked

2. **A02: Cryptographic Failures**
   - ✅ Secrets Manager encryption (KMS)
   - ✅ TLS enforced for all communication
   - ✅ No plaintext credentials in templates

3. **A07: Identification and Authentication Failures**
   - ✅ IAM role-based authentication
   - ✅ No hardcoded credentials
   - ✅ Token-based API authentication

4. **A09: Security Logging and Monitoring Failures**
   - ✅ CloudWatch Logs for all components
   - ✅ SSM session logging
   - ✅ CloudFormation events tracked

#### CIS AWS Foundations Benchmark

- ✅ **2.1**: CloudWatch Logs enabled
- ✅ **4.1**: No unrestricted ingress (0.0.0.0/0) on port 22
- ✅ **4.2**: No unrestricted ingress (0.0.0.0/0) on port 3389
- ✅ **4.3**: Security group egress restricted (not 0.0.0.0/0)
- ✅ **5.1**: VPC flow logs available (user-enabled)

#### AWS Well-Architected Framework - Security Pillar

1. **Identity and Access Management**
   - ✅ IAM roles for all components (no IAM users)
   - ✅ Least privilege policies
   - ✅ No long-term credentials

2. **Detective Controls**
   - ✅ CloudWatch Logs
   - ✅ CloudWatch Metrics
   - ✅ Optional: VPC Flow Logs, CloudTrail

3. **Infrastructure Protection**
   - ✅ VPC isolation
   - ✅ Private subnets
   - ✅ Security groups (restrictive)
   - ✅ Network segmentation

4. **Data Protection**
   - ✅ Encryption in transit (TLS)
   - ✅ Encryption at rest (Secrets Manager)
   - ✅ No sensitive data in logs

5. **Incident Response**
   - ✅ CloudWatch Logs for forensics
   - ✅ SSM session logs
   - ✅ CloudFormation events

## High Availability Design

### Multi-AZ Architecture

#### Availability Zone Distribution

**Active-Active Design:**
- NPA Publisher Instance 1: Availability Zone 1
- NPA Publisher Instance 2: Availability Zone 2
- Each instance handles traffic independently
- No active-passive failover required

**Zone-Isolated Failure Domains:**
- AZ1 failure: AZ2 continues serving traffic
- AZ2 failure: AZ1 continues serving traffic
- Independent NAT Gateways per AZ (no cross-AZ dependency)

#### High Availability Components

**1. NAT Gateways (99.99% SLA per AZ)**
```
AZ1: NAT Gateway 1 → Internet Gateway → Internet
AZ2: NAT Gateway 2 → Internet Gateway → Internet

Benefits:
- Independent failure domains
- Automatic failover within AZ (AWS-managed)
- No single point of failure
```

**2. VPC Endpoints (99.99% SLA)**
```
Each service has endpoints in both AZs:
- ssm.us-east-1.amazonaws.com → ENI in AZ1 + ENI in AZ2
- ec2messages.us-east-1.amazonaws.com → ENI in AZ1 + ENI in AZ2
- ssmmessages.us-east-1.amazonaws.com → ENI in AZ1 + ENI in AZ2

Benefits:
- Zone-local connectivity (low latency)
- Automatic failover to healthy ENI
- No cross-AZ data transfer charges
```

**3. EC2 Instances**
```
Instance 1 (AZ1):
  - Private Subnet 1 (10.0.2.0/24)
  - Routes to NAT Gateway 1
  - Uses VPC Endpoints in AZ1 (automatic)

Instance 2 (AZ2):
  - Private Subnet 2 (10.0.4.0/24)
  - Routes to NAT Gateway 2
  - Uses VPC Endpoints in AZ2 (automatic)

Benefits:
- No shared infrastructure between AZs
- Independent failure and maintenance domains
- Geographic diversity (miles apart)
```

#### Failure Scenarios and Recovery

**Scenario 1: Single Instance Failure**
- **Cause**: EC2 instance crash, OS failure, NPA software crash
- **Impact**: 50% capacity reduction
- **Recovery**:
  - Automatic: Remaining instance continues serving
  - Manual: Recreate failed instance via CloudFormation stack update
  - Time: 5-10 minutes (stack update)

**Scenario 2: Availability Zone Failure**
- **Cause**: AWS AZ-wide outage (rare, <0.01% annually)
- **Impact**: 50% capacity reduction (entire AZ unavailable)
- **Recovery**:
  - Automatic: Healthy AZ continues serving all traffic
  - Manual: None required (wait for AZ recovery)
  - Time: AWS resolves AZ issues (typically <1 hour)

**Scenario 3: NAT Gateway Failure**
- **Cause**: NAT Gateway service disruption
- **Impact**: Single AZ loses outbound internet (cannot reach Netskope)
- **Recovery**:
  - Automatic: AWS restores NAT Gateway (99.99% SLA)
  - Manual: None required
  - Time: Typically minutes (AWS-managed)

**Scenario 4: VPC Endpoint Failure**
- **Cause**: Interface endpoint disruption
- **Impact**: SSM communication affected in one AZ
- **Recovery**:
  - Automatic: DNS resolves to healthy endpoint ENI in other AZ
  - Manual: None required
  - Time: Seconds (DNS TTL)

**Scenario 5: Region-Wide Failure**
- **Cause**: AWS region-wide outage (extremely rare)
- **Impact**: Entire deployment unavailable
- **Recovery**:
  - Manual: Deploy stack in different region (requires multi-region strategy)
  - Time: 15-30 minutes (new stack creation)

### Capacity and Scalability

#### Current Capacity
- **2 instances** (t3.large): ~2,000 concurrent users per instance (Netskope guidance)
- **Total capacity**: ~4,000 concurrent users
- **Overhead**: 20% reserved for bursts and failover

#### Scaling Considerations

**Vertical Scaling (Instance Type):**
```bash
# Increase instance size for more capacity per instance
t3.large   → 2 vCPU, 8 GB RAM  → ~2,000 users
t3.xlarge  → 4 vCPU, 16 GB RAM → ~4,000 users
t3.2xlarge → 8 vCPU, 32 GB RAM → ~8,000 users
```

**Horizontal Scaling (More Instances):**
- Current design: 2 instances (1 per AZ)
- Manual scaling: Add more instances via stack update
- Not auto-scaling (deliberate design choice for predictable capacity)

**Limitations:**
- No automatic scaling based on load
- Capacity changes require stack updates
- Instance replacement requires manual intervention

### RPO and RTO

**Recovery Point Objective (RPO):**
- **Data Loss**: None (stateless publishers)
- **Configuration**: Stored in CloudFormation (version controlled)
- **Netskope State**: Maintained by Netskope cloud (not in AWS)

**Recovery Time Objective (RTO):**
- **Single Instance**: 5-10 minutes (stack update to recreate)
- **Availability Zone**: 0 seconds (automatic failover to healthy AZ)
- **Entire Stack**: 15-30 minutes (recreate from template)

### Monitoring for High Availability

**Key Metrics to Monitor:**
1. EC2 Instance Status Checks (StatusCheckFailed)
2. EC2 CPU Utilization (high CPU may indicate capacity issues)
3. NAT Gateway Active Connections
4. VPC Endpoint Availability (requires custom monitoring)
5. SSM Agent Online Status
6. Netskope Publisher Status (via Netskope UI/API)

**Recommended Alarms:**
```yaml
- StatusCheckFailed_Instance (≥1 for 2 min) → SNS notification
- CPUUtilization (≥80% for 5 min) → SNS notification (capacity warning)
- Lambda Function Errors → SNS notification (registration failures)
```

## AWS Best Practices

### 1. Reliability

✅ **Multi-AZ Deployment**
- Resources distributed across 2 availability zones
- Independent failure domains
- Geographic diversity

✅ **Managed Services**
- NAT Gateway: AWS-managed, automatic scaling
- VPC Endpoints: AWS-managed, multi-AZ by default
- Secrets Manager: AWS-managed, encrypted storage
- Lambda: AWS-managed, automatic scaling

✅ **Infrastructure as Code**
- CloudFormation for repeatable deployments
- Version-controlled templates
- Automated resource provisioning

❌ **Limitations**
- No auto-scaling (manual capacity management)
- No automatic instance recovery (requires stack update)

### 2. Security

✅ **Defense in Depth**
- Network isolation (VPC, private subnets)
- IAM least privilege
- Security group restrictions
- Secrets encryption
- No public IPs

✅ **Encryption**
- Secrets Manager: KMS encryption
- TLS for all communication
- Optional EBS encryption

✅ **Access Control**
- IAM role-based authentication
- No SSH keys required (Systems Manager)
- MFA-ready (user responsibility)

✅ **Audit and Compliance**
- CloudWatch Logs for all components
- SSM session logging
- CloudFormation event tracking

### 3. Performance Efficiency

✅ **Compute Selection**
- Right-sized instances (t3.large default)
- Burstable instances for variable workloads
- Easy vertical scaling (change instance type)

✅ **Network Optimization**
- VPC endpoints reduce latency (no internet routing for SSM)
- Zone-local NAT Gateways (no cross-AZ traffic)
- Multiple AZs reduce distance to users

✅ **Monitoring**
- CloudWatch detailed monitoring enabled
- Custom metrics available

### 4. Cost Optimization

✅ **Resource Efficiency**
- Private subnets only (no bastion hosts)
- VPC endpoints reduce NAT Gateway data transfer costs
- Serverless Lambda (pay-per-use for registration)

✅ **Right-Sizing**
- Configurable instance types
- Single AZ option available for non-production

💡 **Cost Optimization Opportunities**
- Use Reserved Instances (40-60% discount for 1-3 year commit)
- Use Savings Plans for flexible commitment
- Enable EBS volume optimization (gp3 vs gp2)

### 5. Operational Excellence

✅ **Infrastructure as Code**
- CloudFormation templates (version controlled)
- Repeatable deployments
- Documented parameters

✅ **Monitoring and Logging**
- CloudWatch Logs for all components
- Centralized logging
- Searchable logs for troubleshooting

✅ **Documentation**
- Comprehensive README.md
- Deployment guide
- Troubleshooting guide
- Operations guide

✅ **Automation**
- Lambda-based lifecycle management
- Automatic publisher registration
- Automatic application assignment

## Deployment Flow

### CloudFormation Stack Creation Sequence

```
1. VPC Resources (if CreateNewVPC=yes)
   ├─ VPC
   ├─ Internet Gateway
   ├─ Public Subnets (2)
   ├─ Private Subnets (2)
   ├─ NAT Gateways (2)
   └─ Route Tables (4)

2. Security Resources
   ├─ Security Group
   └─ VPC Endpoints (ssm, ec2messages, ssmmessages)

3. IAM Resources
   ├─ EC2 Instance Role
   ├─ EC2 Instance Profile
   └─ Lambda Execution Role

4. Secrets Management
   └─ Secrets Manager Secret (API Token)

5. Lambda Function
   ├─ Lambda Function
   └─ CloudWatch Log Group

6. EC2 Instances
   ├─ NPA Publisher Instance 1 (AZ1)
   └─ NPA Publisher Instance 2 (AZ2)

7. Custom Resources
   ├─ Custom Resource 1 (triggers Lambda for Instance 1)
   └─ Custom Resource 2 (triggers Lambda for Instance 2)

8. Lambda Execution Flow (per Custom Resource)
   ├─ Retrieve API token from Secrets Manager
   ├─ Call Netskope API to create publisher
   ├─ Receive registration token
   ├─ Wait for EC2 instance to reach "running" state (up to 2 min)
   ├─ Poll SSM until agent online (up to 4 min)
   ├─ Send SSM command: npa_publisher_wizard -token $TOKEN
   ├─ Wait for command completion (up to 5 min)
   ├─ Query private applications from Netskope API
   ├─ Filter apps matching publisher group name
   ├─ Assign publisher to matching apps
   └─ Return SUCCESS to CloudFormation

9. Stack Outputs
   ├─ Publisher Instance IDs
   ├─ Private IPs
   └─ Publisher Names
```

**Total Deployment Time**: 10-15 minutes
- VPC creation: 2-3 minutes
- EC2 instance launch: 1-2 minutes
- SSM agent online: 2-4 minutes
- Registration command: 1-2 minutes
- App assignment: 1-2 minutes

### Stack Deletion Sequence

```
1. Custom Resources (DELETE event)
   ├─ Lambda queries private apps from Netskope
   ├─ Removes publisher from all assigned apps
   └─ Deletes publisher from Netskope

2. EC2 Instances Terminated

3. Lambda Function Deleted

4. Secrets Manager Secret Deleted

5. IAM Resources Deleted

6. VPC Endpoints Deleted

7. Security Group Deleted

8. VPC Resources Deleted (if created by stack)
   ├─ NAT Gateways Released
   ├─ Subnets Deleted
   ├─ Route Tables Deleted
   ├─ Internet Gateway Detached
   └─ VPC Deleted
```

**Total Deletion Time**: 5-10 minutes

## Resource Dependencies

### Critical Dependency Chains

**For EC2 Instance Launch:**
```
VPC → Subnet → Security Group → IAM Instance Profile → EC2 Instance
                     ↓
               VPC Endpoints (for SSM)
```

**For Publisher Registration:**
```
EC2 Instance (running) → SSM Agent (online) → Lambda Function
                                                    ↓
                                          Secrets Manager Secret
                                                    ↓
                                            Netskope API (external)
```

**For High Availability:**
```
AZ1: NAT Gateway 1 → VPC Endpoint ENIs → Private Subnet 1 → Instance 1
AZ2: NAT Gateway 2 → VPC Endpoint ENIs → Private Subnet 2 → Instance 2
```

### External Dependencies

1. **Netskope Cloud**: Publisher management API must be reachable
2. **AWS Marketplace**: NPA Publisher AMI must be subscribed
3. **AWS Services**: Systems Manager, Secrets Manager, Lambda must be available in region
4. **Internet Connectivity**: Required for Netskope communication

## Conclusion

This architecture implements AWS best practices for security, reliability, and operational excellence. The multi-AZ design provides high availability, while the defense-in-depth security model protects against common attack vectors. The use of managed services (NAT Gateway, VPC Endpoints, Lambda, Secrets Manager) reduces operational overhead and improves reliability.

Key strengths:
- ✅ Zero Trust network access (no inbound connections)
- ✅ Multi-AZ high availability
- ✅ IAM least privilege enforced throughout
- ✅ Encrypted secrets storage
- ✅ Infrastructure as Code
- ✅ Comprehensive logging and monitoring

Considerations for production:
- Monitor capacity and scale manually as needed
- Enable CloudTrail for API audit logging
- Implement CloudWatch alarms for proactive monitoring
- Use Reserved Instances or Savings Plans for cost optimization
- Test failover scenarios regularly

For detailed operational procedures, see [OPERATIONS.md](OPERATIONS.md).
