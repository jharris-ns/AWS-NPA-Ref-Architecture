# Architecture Overview

AWS reference architecture for deploying Netskope Private Access (NPA) Publishers using CloudFormation. This document explains each design decision through the lens of AWS best practices and the [AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html).

## Table of Contents

- [Architecture Diagram](#architecture-diagram)
- [Component Overview](#component-overview)
- [Network Architecture](#network-architecture)
- [High Availability Design](#high-availability-design)
- [Deployment Flow](#deployment-flow)
- [Additional Resources](#additional-resources)

## Architecture Diagram

![NPA Reference Architecture](images/npa_reference_architecture.png)

## Component Overview

### VPC and Subnet Design

- **VPC** (10.0.0.0/16, configurable): Isolated network environment with DNS support enabled. Only created when `CreateNewVPC=yes`; an existing VPC can be supplied instead.
- **Private Subnets** (10.0.2.0/24, 10.0.4.0/24): Host NPA Publisher EC2 instances. No public IP addresses. Egress via NAT Gateway.
- **Public Subnets** (10.0.1.0/24, 10.0.3.0/24): Host NAT Gateways only — no compute resources. Route to internet via Internet Gateway.

> *Well-Architected [VPC User Guide](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Scenario2.html): Place workloads in private subnets unless they require direct inbound internet access.*

### NAT Gateways

- Two NAT Gateways, one per AZ, each with a static Elastic IP
- Zone-isolated failure domains: an AZ1 NAT Gateway failure does not affect AZ2
- Managed service with automatic scaling and 99.99% SLA

> *Well-Architected [REL02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_planning_horizontal_scaling.html): Deploy one NAT Gateway per AZ so resources are not cross-AZ dependent.*

### EC2 Instances (NPA Publishers)

- **Instance type**: t3.large (default, configurable)
- **AMI**: Netskope Private Access Publisher (AWS Marketplace)
- **Deployment**: One instance per availability zone
- **Networking**: Private subnet placement (no public IP)
- **IAM**: Instance profile with Systems Manager permissions only — no access to API tokens or registration secrets
- **Monitoring**: Detailed CloudWatch monitoring enabled
- **User Data**: Empty — registration handled entirely via SSM Run Command (no secrets in user data)
- **Management**: AWS Systems Manager Session Manager — no SSH or bastion hosts required
- **Tags**: Includes CostCenter, Project, Environment, aws-apn-id

> *Well-Architected [SEC02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_identities_unique.html): Use instance profiles for credential management instead of long-lived keys.*

### Security Groups

- **Ingress**: None — publishers only initiate outbound connections, giving them zero inbound attack surface
- **Egress**: Restricted to Netskope NewEdge IPs and VPC endpoint ENIs (HTTPS 443 only) — no 0.0.0.0/0

> *Well-Architected [SEC05-BP02](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_network_protection_create_layers.html): Apply least privilege to network access.*

### VPC Endpoints

- Three Interface VPC Endpoints: `ssm`, `ssmmessages`, `ec2messages`
- SSM agent traffic stays within the AWS network, never traversing the NAT Gateway or public internet
- Enables Session Manager access to instances in private subnets without SSH or bastion hosts

> *Well-Architected [SEC05-BP03](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_network_protection_inspection.html): Use VPC endpoints to keep AWS service traffic on the private network.*

### IAM Roles

Two-role separation ensures least privilege:

| Role | Assumed By | Purpose |
|---|---|---|
| **EC2 Instance Role** | `ec2.amazonaws.com` | SSM agent communication only. No access to API tokens or registration secrets. |
| **Lambda Execution Role** | `lambda.amazonaws.com` | Retrieves API token from SSM Parameter Store, manages publishers via Netskope API, sends registration commands via SSM. Scoped to specific resources. |

The EC2 instance never handles API tokens — the Lambda function retrieves the token server-side and passes only the short-lived registration token via SSM Run Command. See `templates/deployment-iam-policy.json` for minimum deployer permissions.

> *Well-Architected [SEC03-BP01](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_permissions_define.html): Define access requirements. [SEC02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_identities_unique.html): Use temporary credentials.*

### Lambda Function (Registration Handler)

The Lambda function is a CloudFormation Custom Resource handler that manages the full publisher lifecycle — creation, registration, application assignment, and cleanup on stack deletion.

- **Runtime**: Python 3.12
- **Memory**: 128 MB
- **Timeout**: 600 seconds (10 minutes)
- **Triggers**: CloudFormation custom resource events (CREATE, UPDATE, DELETE)
- **Functions**:
  - Retrieve API token from SSM Parameter Store
  - Create publisher in Netskope via REST API v2
  - Wait for EC2 instance running state and SSM agent online
  - Send registration command via SSM Run Command
  - Assign publisher to private applications (based on `AppAssociations` parameter)
  - Clean up on stack deletion (remove publisher from apps, delete from Netskope)

### SSM Parameter Store

- **Purpose**: Secure storage for Netskope API v2 token
- **Type**: SecureString (encrypted with AWS KMS default key, AES-256)
- **Access**: Lambda function only (via IAM policy)
- **Lifecycle**: Created with stack, deleted with stack

### Token Handling Flow

```
1. User provides API token as CloudFormation parameter (marked NoEcho)
2. CloudFormation stores token in SSM Parameter Store (SecureString)
3. Lambda retrieves token from SSM Parameter Store (encrypted in transit)
4. Lambda calls Netskope API to get registration token (short-lived)
5. Lambda passes registration token via SSM Run Command (encrypted channel)
6. Publisher registers with Netskope (token consumed, expires)
7. No long-lived tokens stored on publisher instance
```

> *Well-Architected [SEC08-BP01](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_protect_data_rest.html): Implement secure key management. All transit is TLS 1.2+ (AWS SDK) / TLS 1.3 (Netskope).*

### CloudWatch

- **Log Group**: `/aws/lambda/<PublisherGroupName>-RegistrationHandler`
- **Retention**: 7 days (default)
- **Content**: Publisher creation events, SSM command status, API responses, application assignment results
- **EC2 Metrics**: Detailed monitoring enabled

## Network Architecture

### Traffic Flows

#### 1. Publisher to Netskope NewEdge
```
NPA Publisher → Security Group (egress) → NAT Gateway →
Internet Gateway → Internet → Netskope NewEdge Data Centers
```
- **Port**: HTTPS (443)
- **Purpose**: Publisher registration, management plane, tunnel establishment

#### 2. Publisher to Internal Applications
```
NPA Publisher → Security Group (egress) →
VPC Internal / Peered VPCs / On-Premises (via VPN/Direct Connect)
```
- **Ports**: Application-specific
- **Destination**: RFC1918 private IP ranges
- **Purpose**: Proxying user traffic to internal applications via Netskope tunnels

#### 3. AWS Service Traffic (via VPC Endpoints)
```
NPA Publisher → VPC Endpoint (Interface) →
AWS Systems Manager Service Endpoints
```
- **Port**: HTTPS (443)
- **Purpose**: SSM agent communication, Session Manager access, registration via SSM Run Command
- VPC endpoints keep this traffic on the AWS private network, avoiding NAT Gateway data processing costs and removing a dependency on outbound internet for management operations

#### 4. Lambda to Netskope API
```
Lambda Function → NAT Gateway → Internet Gateway →
Internet → Netskope API (api.netskope.com)
```
- **Port**: HTTPS (443)
- **Purpose**: Publisher CRUD operations, private application management

#### 5. Lambda to AWS Services
```
Lambda → AWS Service Endpoints (SSM Parameter Store, EC2, SSM, CloudWatch)
```
- **Protocol**: AWS SDK (HTTPS)
- **Network**: AWS internal network
- **Purpose**: Retrieve parameters, describe instances, send commands, write logs

### Network Segmentation

**AWS best practice**: Separate data, management, and control plane traffic using VPC constructs ([SEC05-BP01](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_network_protection_create_layers.html)).

| Plane | Traffic | AWS Mechanism |
|---|---|---|
| **Data Plane** | Publisher ↔ Netskope NewEdge, internal apps | Private subnets → NAT Gateway → Internet / VPC peering |
| **Management Plane** | Operator ↔ Publisher (shell access, diagnostics) | Systems Manager Session Manager via VPC endpoints — no SSH, no bastion |
| **Control Plane** | Lambda ↔ AWS APIs, Netskope APIs | CloudFormation Custom Resource triggers Lambda within AWS-managed network |

**Security Zones:**
- **Public Zone**: NAT Gateways only (no compute resources)
- **Private Zone**: NPA Publishers (compute resources)
- **AWS Service Zone**: Managed services (Lambda, SSM Parameter Store, CloudWatch)

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
- Independent NAT Gateways per AZ

#### Failure Scenarios and Recovery

**Scenario 1: Single Instance Failure**
- **Impact**: Reduced capacity (remaining instance continues serving)
- **Recovery**: Delete and recreate the stack, or use scale-in/scale-out to replace the individual publisher (see [OPERATIONS.md](OPERATIONS.md#replace-a-failing-publisher))
- **Automatic**: Remaining instance continues without intervention

**Scenario 2: Availability Zone Failure**
- **Impact**: Instances in affected AZ unavailable
- **Recovery**: Healthy AZ continues serving all traffic automatically
- **Manual**: None required (wait for AZ recovery)

**Scenario 3: NAT Gateway Failure**
- **Impact**: Single AZ loses outbound internet
- **Recovery**: AWS restores NAT Gateway (99.99% SLA)

**Scenario 4: Region-Wide Failure**
- **Impact**: Entire deployment unavailable
- **Recovery**: Deploy stack in different region using same CloudFormation template

### Capacity and Scalability

**Vertical Scaling (Instance Type):**

| Instance Type | vCPU | Memory | Approximate Capacity |
|---|---|---|---|
| t3.large | 2 | 8 GB | ~2,000 concurrent users |
| t3.xlarge | 4 | 16 GB | ~4,000 concurrent users |
| t3.2xlarge | 8 | 32 GB | ~8,000 concurrent users |

**Horizontal Scaling:**
- Current design: 2 instances (1 per AZ)
- Scale by editing the template to add/remove publisher resource blocks (see [OPERATIONS.md](OPERATIONS.md#scale-publisher-count))
- Not auto-scaling (deliberate design choice for predictable capacity)

### RPO and RTO

**Recovery Point Objective (RPO):**
- **Data Loss**: None (stateless publishers)
- **Configuration**: Stored in CloudFormation template (version controlled)
- **Netskope State**: Maintained by Netskope cloud

**Recovery Time Objective (RTO):**
- **Single Instance**: 5-10 minutes (scale-in/scale-out or stack recreate)
- **Availability Zone**: 0 seconds (automatic failover)
- **Entire Stack**: 15-30 minutes (recreate from template)

## Deployment Flow

### Stack Creation Sequence

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
   ├─ EC2 Instance Role + Instance Profile
   └─ Lambda Execution Role

4. SSM Parameter Store
   └─ SecureString (API Token)

5. Lambda Function + CloudWatch Log Group

6. EC2 Instances
   ├─ NPA Publisher Instance 1 (AZ1)
   └─ NPA Publisher Instance 2 (AZ2)

7. Custom Resources (triggers Lambda per instance)
   ├─ Retrieve API token from SSM Parameter Store
   ├─ Create publisher in Netskope via API
   ├─ Wait for EC2 running + SSM agent online
   ├─ Send registration command via SSM Run Command
   ├─ Assign publisher to private applications (per AppAssociations parameter)
   └─ Return SUCCESS to CloudFormation
```

**Total Deployment Time**: 10-15 minutes

### Stack Deletion Sequence

```
1. Custom Resources (DELETE event)
   ├─ Remove publisher from assigned apps
   └─ Delete publisher from Netskope

2. EC2 Instances Terminated
3. Lambda Function Deleted
4. SSM Parameter Deleted
5. IAM Resources Deleted
6. VPC Endpoints + Security Group Deleted
7. VPC Resources Deleted (if created by stack)
```

**Total Deletion Time**: 5-10 minutes

## Additional Resources

- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common issues and solutions
- [IAM-ROLE-SETUP.md](IAM-ROLE-SETUP.md) — IAM role configuration
- [OPERATIONS.md](OPERATIONS.md) — Day-2 operational procedures