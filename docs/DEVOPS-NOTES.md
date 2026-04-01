# DevOps Technical Notes

## Overview

This document explains the technical implementation details of the NPA Publisher deployment, focusing on AWS Systems Manager (SSM) integration and the Lambda function orchestration.

## Table of Contents

- [AWS Systems Manager Usage](#aws-systems-manager-usage)
- [Lambda Function Architecture](#lambda-function-architecture)
  - [Scaling to Additional Availability Zones](#scaling-to-additional-availability-zones)
  - [Private App Publisher Assignment](#private-app-publisher-assignment)
- [Integration Flow](#integration-flow)
- [Error Handling & Retries](#error-handling--retries)
- [Timer & Polling Architecture](#timer--polling-architecture)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)

---

## AWS Systems Manager Usage

### Systems Manager

**Key Advantages:**
1. **No SSH keys required** - Session Manager provides secure shell access
2. **No public IPs needed** - Works entirely through AWS endpoints
3. **Audit trail** - All commands logged in CloudTrail and CloudWatch
4. **Secure command execution** - No secrets exposed in user data or logs
5. **IAM-based access control** - Fine-grained permissions

### SSM Agent Requirements

The EC2 instance must have:
```yaml
- SSM Agent installed and running (pre-installed on Amazon Linux 2, Ubuntu)
- IAM Instance Profile with AmazonSSMManagedInstanceCore policy
- Outbound HTTPS (443) access to SSM endpoints
- Proper VPC routing to internet (NAT Gateway)
```

### SSM Components Used

#### 1. Instance Information API

Used to check if instance is registered with SSM:

```python
ssm_client.describe_instance_information(
    Filters=[{'Key': 'InstanceIds', 'Values': [instance_id]}]
)
```

**What this checks:**
- Is SSM agent running and healthy?
- Has the instance registered with SSM service?
- What's the agent version and platform type?

**Typical registration time:** 30-90 seconds after instance boot

#### 2. Run Command (SendCommand API)

Executes commands remotely on the instance:

```python
ssm_client.send_command(
    InstanceIds=[instance_id],
    DocumentName='AWS-RunShellScript',
    Parameters={
        'commands': [
            f'/home/ubuntu/npa_publisher_wizard -token "{registration_token}"'
        ]
    },
    Comment='Registering NPA publisher with Netskope',
    TimeoutSeconds=300  # 5 minutes
)
```

**Key parameters:**
- `DocumentName`: Uses built-in `AWS-RunShellScript` document
- `TimeoutSeconds`: Maximum execution time (5 minutes)
- `Comment`: Helps identify commands in console

**Command output locations:**
- CloudWatch Logs (if configured)
- S3 bucket (if configured)
- GetCommandInvocation API response

#### 3. Command Status Polling

Monitors command execution:

```python
ssm_client.get_command_invocation(
    CommandId=command_id,
    InstanceId=instance_id
)
```

**Possible statuses:**
- `Pending` - Queued, not started
- `InProgress` - Currently executing
- `Success` - Completed successfully (exit code 0)
- `Failed` - Command failed (non-zero exit code)
- `TimedOut` - Exceeded TimeoutSeconds
- `Cancelled` - Manually cancelled

### SSM Endpoint Requirements

The instance needs access to these VPC endpoints (via NAT Gateway or VPC endpoints):

```
ssm.{region}.amazonaws.com          # Core SSM service
ssmmessages.{region}.amazonaws.com  # Session Manager
ec2messages.{region}.amazonaws.com  # Agent communication
```

---

## Lambda Function Architecture

### Function Configuration

```yaml
Runtime: Python 3.12
Timeout: 600 seconds (10 minutes)
Memory: 128 MB
Environment Variables:
  - tenant_fqdn: (from CloudFormation - NetskopeTenantFQDN)
  - api_token: (from CloudFormation - SSM Parameter Store parameter name)
  - EC2_READY_TIMEOUT: 120        # EC2 state check timeout
  - SSM_READY_TIMEOUT: 240        # SSM registration timeout
  - COMMAND_TIMEOUT: 300           # Command execution timeout
  - PUBLISHER_DISCONNECT_TIMEOUT: 120  # Publisher disconnect wait during delete
  - LOGLEVEL: INFO                 # Logging level (DEBUG, INFO, WARNING, ERROR)
```

### IAM Permissions Required

```yaml
EC2:
  - ec2:DescribeInstances          # Check instance state

SSM:
  - ssm:DescribeInstanceInformation  # Check if SSM agent online
  - ssm:SendCommand                  # Execute registration command
  - ssm:GetCommandInvocation         # Monitor command status

SSM Parameter Store:
  - ssm:GetParameter                   # Retrieve Netskope API token

CloudWatch Logs:
  - logs:CreateLogGroup              # Create log groups
  - logs:CreateLogStream             # Create log streams
  - logs:PutLogEvents               # Write logs
```

### Function Workflow

#### 1. CloudFormation Custom Resource Trigger

Lambda receives events from CloudFormation:

```json
{
  "RequestType": "Create",  // or "Update", "Delete"
  "ResourceProperties": {
    "InstanceId": "i-0123456789abcdef",
    "PublisherGroupName": "MyNPAPublisher",
    "NetskopeAPITokenParamName": "/netskope/api-token/MyNPAPublisher",
    "NetskopeTenant": "mytenant.goskope.com"
  },
  "ResponseURL": "https://cloudformation-custom-resource-response..."
}
```

#### 2. Handler Routing

```python
def lambda_handler(event, context):
    request_type = event['RequestType']

    if request_type == 'Create':
        return handle_create(event, context)
    elif request_type == 'Delete':
        return handle_delete(event, context)
    elif request_type == 'Update':
        # Updates handled as no-op
        return send_response(event, context, 'SUCCESS')
```

#### 3. CREATE Flow (handle_create)

**Step 1: Retrieve API Token**
```python
def get_secret(param_name):
    """Retrieve Netskope API token from SSM Parameter Store"""
    response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
    return response['Parameter']['Value']
```

**Step 2: Create Publisher in Netskope**
```python
# Uses stdlib urllib.request (no requests dependency)
resp = call_netskope_api_with_retry("post", "/api/v2/infrastructure/publishers", token, {"name": publisher_name})
publisher_id = resp["data"]["id"]

# Request registration token separately
resp = call_netskope_api_with_retry("post", f"/api/v2/infrastructure/publishers/{publisher_id}/registration_token", token, None)
reg_token = resp["data"]["token"]
```

**Step 3: Wait for EC2 Running State**
```python
def wait_for_instance_running(ec2_client_ref, instance_id, context=None, max_wait=120):
    """Poll EC2 every 5s until instance is running. 30s safety buffer."""
    # Aborts early if Lambda has < 30s remaining
    # Returns True/False instead of raising
```

**Step 4: Wait for SSM Agent Online**
```python
# Inline in handle_create — exponential backoff [5, 10, 15, 20, 30, 30, 30, 30, 30, 30]
# 30s safety buffer, checks describe_instance_information PingStatus == "Online"
# Total max ~230s, typical success: 30-60s
```

**Exponential backoff rationale:**
- Early checks: 5s intervals (catch fast registrations)
- Later checks: 30s intervals (reduce API calls)
- Total time: ~230 seconds max
- Typical success: 30-60 seconds

**Step 5: Execute Registration Command**
```python
# SSM send_command with AWS-RunShellScript
# Command: /home/ubuntu/npa_publisher_wizard -token "{reg_token}"
# TimeoutSeconds=300
```

**Step 6: Wait for Command Completion**
```python
def wait_for_command_completion(ssm_client_ref, command_id, instance_id, context=None, max_wait=300):
    """Poll every 5s with 30s safety buffer. Checks output for false-positive
    Success — npa_publisher_wizard may exit 0 even on failure.
    Scans stdout for failure patterns like 'Registration failed',
    'context deadline exceeded', 'admin call didn't succeed'."""
```

**Step 7: Update Private Applications**
```python
# Uses app_associations parameter to control behavior:
# - "None": skip app assignment
# - "All": assign publisher to all private apps
# - "app1,app2": assign to named apps only
# Uses call_netskope_api_with_retry for each PATCH
```

See [Private App Publisher Assignment](#private-app-publisher-assignment) below for the full read-modify-write pattern and bracket handling.

**Step 8: Send Response to CloudFormation**
```python
# Uses cfnresponse module (not requests) to send SUCCESS/FAILED
# cfnresponse.send(event, context, status, response_data)
```

#### 4. DELETE Flow (handle_delete)

The delete flow is more complex than a simple API call because the Netskope API rejects publisher deletion if the publisher is still associated with private apps.

**Step 1: Find Publisher by Name**
```python
# GET /api/v2/infrastructure/publishers, iterate to match publisher_name
# Unlike CREATE, DELETE uses publisher_name (not PhysicalResourceId) to find the publisher
```

**Step 2: Stop EC2 Instance**
```python
# Non-blocking, best-effort ec2:StopInstances
# Forces publisher to disconnect from Netskope
# Continues even if stop fails (instance may already be terminating)
```

**Step 3: Remove Publisher from All App Definitions**

Uses `remove_publisher_from_apps()` — see [Private App Publisher Assignment](#private-app-publisher-assignment) for the full read-modify-write pattern.

**Step 4: Wait for Publisher to Disconnect**
```python
# wait_for_publisher_disconnected(): poll status every 5s, max PUBLISHER_DISCONNECT_TIMEOUT (120s)
# 15s safety buffer for Lambda timeout
# Checks publisher status != "connected"
```

**Step 5: Delete Publisher with Retry and Re-Removal**
```python
# DELETE /api/v2/infrastructure/publishers/{publisher_id}
# Up to 8 attempts, 10s interval between retries
# On each retry (when error is "associated with apps"):
#   1. Re-call remove_publisher_from_apps() — re-reads current state and re-PATCHes
#   2. Wait 10s for eventual consistency
#   3. Retry DELETE
# Different errors fail immediately
```

See [OPERATIONS.md](OPERATIONS.md#publisher-deletion-workflow) for the operator-facing view of this workflow.

---

### Scaling to Additional Availability Zones

The template ships with two publishers across two AZs. Adding a third (or Nth) publisher in a new AZ requires changes in several sections of the template. The inline comments in the template cover the publisher-specific steps; this section covers the full picture including the VPC infrastructure that a new AZ requires.

#### Publisher Resources (covered in template comments)

1. Copy an entire Publisher block (Instance + Registration)
2. Increment all resource name suffixes (e.g., `NPAPublisherInstance3`, `NPAPublisherRegistration3`)
3. Update `DependsOn` in the Registration to chain to the **previous** Registration (e.g., `NPAPublisherRegistration2`) — this serializes registrations to prevent the race condition described in [The Race Condition](#the-race-condition)
4. Set `SubnetId` to reference the new AZ's subnet (e.g., `PrivateSubnet3` / `ExistingPrivateSubnet3`)
5. Update the `Name` tag (e.g., `${NPAPublisherGroupName}-3`)
6. Add matching Outputs at the bottom of the template

#### New AZ Infrastructure (not covered in template comments)

Adding a publisher in a **new** AZ (beyond the existing two) also requires:

**Parameters** — add four new parameters:
- `AvailabilityZone3` — AZ selection (same pattern as `AvailabilityZone2`)
- `PublicSubnetCIDR3` — public subnet CIDR (default: next available, e.g., `10.0.5.0/24`)
- `PrivateSubnetCIDR3` — private subnet CIDR (default: next available, e.g., `10.0.6.0/24`)
- `ExistingPrivateSubnet3` — existing subnet ID for use when `CreateNewVPC=no`

**Metadata** — add the new parameters to `AWS::CloudFormation::Interface` → `ParameterGroups` under "VPC Configuration"

**Conditions** — add `UseSpecificAZ3` (same pattern as `UseSpecificAZ2`)

**VPC Resources** (conditional on `CreateVPC`) — add eight resources following the AZ2 pattern:
- `PublicSubnet3` — uses `!Select [2, !GetAZs '']` for auto-select
- `PrivateSubnet3` — same AZ as PublicSubnet3
- `NATGatewayEIP3` — Elastic IP for the NAT Gateway
- `NATGateway3` — in PublicSubnet3
- `PublicSubnetRouteTableAssociation3` — associates PublicSubnet3 with the shared public route table
- `PrivateRouteTable3` — dedicated route table for the private subnet
- `PrivateRoute3` — default route through NATGateway3
- `PrivateSubnetRouteTableAssociation3` — associates PrivateSubnet3 with PrivateRouteTable3

**Outputs** — add VPC outputs for the new AZ:
- `PublicSubnetId3`, `PrivateSubnetId3`, `NATGatewayId3` (all conditional on `CreateVPC`)

#### Reference

A validated 3-AZ variant of the template is available at [`examples/netskope-ref-architecture-npa-3az.yaml`](../examples/netskope-ref-architecture-npa-3az.yaml) and can be used as a worked example.

---

### Private App Publisher Assignment

This section explains how the Lambda function adds and removes publishers from private app definitions. Understanding this pattern is critical because it is the source of the race condition that drives both the `DependsOn` serialization in the template and the re-removal retry logic in the delete flow.

#### Netskope Private Apps API

The Netskope API represents publisher-to-app associations on the **app** side. Each private app has a `service_publisher_assignments` array containing the publishers assigned to it:

```
GET /api/v2/steering/apps/private
```

```json
{
  "status": "success",
  "data": {
    "private_apps": [
      {
        "app_id": 381,
        "app_name": "[SSH Application1]",
        "service_publisher_assignments": [
          {
            "publisher_id": 6549,
            "publisher_name": "NPA-Test-106808901653-i-0643802560c1d2635",
            "reachability": { "reachable": true, "error_code": 0 },
            "service_id": 381
          },
          {
            "publisher_id": 6553,
            "publisher_name": "NPA-Test-106808901653-i-08293e313355485d9",
            "reachability": { "reachable": true, "error_code": 0 },
            "service_id": 381
          }
        ]
      }
    ]
  }
}
```

Key points:
- **`app_name` is wrapped in square brackets** (e.g., `[SSH Application1]`). Users provide names without brackets. The Lambda uses `app_name_matches()` to strip brackets during comparison.
- **There is no "add publisher" or "remove publisher" endpoint.** To modify assignments, you PATCH the entire `publishers` list. This is a **replace** operation, not an append/remove.
- The publisher's own `apps_count` field is **eventually consistent** and may lag behind PATCH operations by over 60 seconds. Do not rely on it for verification.

#### App Name Bracket Matching

The Netskope API wraps app names in square brackets (`[App Name]`), but users provide names without brackets in the CloudFormation `AppAssociations` parameter. The Lambda normalizes both sides during comparison:

```python
def app_name_matches(api_name, target_name):
    """Check if app name matches, ignoring optional brackets."""
    if api_name == target_name:
        return True
    stripped = api_name.strip("[]")
    return stripped == target_name or stripped == target_name.strip("[]")
```

This handles all combinations:
- User provides `SSH Application1`, API returns `[SSH Application1]` — match
- User provides `[SSH Application1]`, API returns `[SSH Application1]` — match
- User provides `SSH Application1`, API returns `SSH Application1` — match

The same bracket stripping is used when reporting missing apps:
```python
found_names = {app.get("app_name", "").strip("[]") for app in target_apps}
missing = [name for name in target_app_names if name.strip("[]") not in found_names]
```

#### Read-Modify-Write Pattern (Adding a Publisher)

During CREATE, the Lambda adds the new publisher to each target app:

```
1. GET /api/v2/steering/apps/private
   → Returns all apps with their current service_publisher_assignments

2. For each target app:
   a. Read current assignments: [{ publisher_id: 6549 }]
   b. Check if publisher already present (skip if so)
   c. Append new publisher:    [{ publisher_id: 6549 }, { publisher_id: 6553 }]
   d. PATCH /api/v2/steering/apps/private/{app_id}
      Body: { "publishers": [{ "publisher_id": 6549 }, { "publisher_id": 6553 }] }
```

The response confirms the new state:
```json
{
  "status": "success",
  "data": {
    "service_publisher_assignments": [
      { "publisher_id": 6549, "publisher_name": "...", "reachability": { ... } },
      { "publisher_id": 6553, "publisher_name": "...", "reachability": { ... } }
    ]
  }
}
```

#### Read-Modify-Write Pattern (Removing a Publisher)

During DELETE, `remove_publisher_from_apps()` removes the publisher from all apps:

```
1. GET /api/v2/steering/apps/private
   → Returns all apps with their current service_publisher_assignments

2. For each app containing this publisher_id:
   a. Read current assignments: [{ publisher_id: 6549 }, { publisher_id: 6553 }]
   b. Filter out this publisher:  [{ publisher_id: 6549 }]
   c. PATCH /api/v2/steering/apps/private/{app_id}
      Body: { "publishers": [{ "publisher_id": 6549 }] }
```

If the publisher is the only one assigned, the PATCH sends an empty list:
```json
{ "publishers": [] }
```

#### The Race Condition

Because the PATCH **replaces the entire publishers list**, concurrent read-modify-write operations on the same app will overwrite each other. This is the core issue:

```
Timeline — Two Lambdas adding publishers concurrently (without DependsOn):

  Lambda 1 (publisher 6549)              Lambda 2 (publisher 6553)
  ─────────────────────────              ─────────────────────────
  GET app 381                            GET app 381
  → assignments: [4]                     → assignments: [4]

  append 6549 → [4, 6549]               append 6553 → [4, 6553]

  PATCH app 381                          PATCH app 381
  body: [4, 6549]                        body: [4, 6553]
      │                                      │
      ▼                                      ▼
  Whichever PATCH lands second wins.
  Result: app 381 has [4, 6553] — publisher 6549 is lost.
```

The same race occurs during DELETE when both Lambdas try to remove their publisher from the same app simultaneously. Each reads the full list, removes only its own publisher, and PATCHes back. The second PATCH re-adds the publisher that the first PATCH just removed.

#### How It's Solved

**1. DependsOn serialization (primary fix):**

The template chains Registration resources so they execute sequentially:

```yaml
NPAPublisherRegistration2:
  Type: Custom::NPAPublisher
  DependsOn:
    - NPAPublisherInstance2
    - NPAPublisherRegistration   # Ensures sequential execution
```

On CREATE, CloudFormation runs Registration 1 to completion before starting Registration 2. On DELETE, CloudFormation reverses the dependency order — Registration 2 deletes first, then Registration 1. This eliminates the race for most operations.

**2. Re-removal retry on DELETE (safety net):**

Even with `DependsOn`, edge cases can occur (e.g., CloudFormation retries, manual deletions). The delete retry loop re-reads and re-PATCHes on each attempt:

```
Delete attempt 1:
  DELETE publisher 6553 → Error: "associated with 2 apps"
  → Re-call remove_publisher_from_apps(6553)
    → GET apps → find 6553 still in assignments → PATCH to remove
  → Wait 10s

Delete attempt 2:
  DELETE publisher 6553 → Success
```

This makes the delete robust against both eventual consistency (stale reads) and concurrent modifications (overwritten PATCHes).

---

## Integration Flow

### Complete Timeline

```
t=0s    CloudFormation starts stack creation
        ├─ Creates VPC resources (if new VPC)
        ├─ Creates Security Group
        ├─ Creates IAM Role & Instance Profile
        └─ Creates SSM parameter

t=30s   EC2 Instance launches
        └─ User data runs (minimal, no secrets)

t=35s   CloudFormation Custom Resource invokes Lambda

t=40s   Lambda: Get API token from SSM Parameter Store
        └─ Call Netskope API to create publisher
        └─ Receive registration token

t=45s   Lambda: Wait for EC2 running state
        └─ Poll ec2:DescribeInstances every 5s

t=60s   EC2 Instance state = "running"
        └─ SSM Agent starts initialization

t=65s   Lambda: Start SSM readiness polling
        └─ First check (5s wait)
        └─ Second check (10s wait)
        └─ Third check (15s wait)

t=90s   SSM Agent registers with SSM service
        └─ Instance appears in DescribeInstanceInformation

t=95s   Lambda: Send registration command via SSM

t=100s  SSM Agent receives command
        └─ Executes: npa_publisher_wizard -token "..."
        └─ Wizard connects to Netskope
        └─ Downloads publisher software
        └─ Configures and starts publisher service

t=180s  Command completes successfully
        └─ SSM reports Status = "Success"

t=185s  Lambda: Update private applications
        └─ Assign publisher to matching apps

t=190s  Lambda: Send SUCCESS to CloudFormation

t=195s  CloudFormation: Stack CREATE_COMPLETE
```

### Failure Scenarios

#### Scenario 1: SSM Agent Never Registers

```
Symptom: Lambda times out or fails with "Instance did not become available"

Causes:
- No internet access (NAT Gateway missing/misconfigured)
- Security group blocks HTTPS (443) outbound
- IAM instance profile missing SSMManagedInstanceCore policy
- SSM agent crashed during startup

Debug Steps:
1. Check instance security group outbound rules
2. Verify subnet route table has NAT Gateway route
3. Connect via EC2 Instance Connect (if possible)
4. Check: systemctl status amazon-ssm-agent
5. Check: /var/log/amazon/ssm/amazon-ssm-agent.log
```

#### Scenario 2: Command Fails

```
Symptom: SSM command status = "Failed"

Causes:
- npa_publisher_wizard not found at /home/ubuntu/
- Invalid registration token
- Network connectivity issues from instance
- Insufficient disk space or memory

Debug Steps:
1. Check Lambda logs for stderr output
2. Connect via Session Manager
3. Manually run: sudo /home/ubuntu/npa_publisher_wizard -token "test"
4. Check wizard logs (location varies by AMI)
5. Verify Netskope tenant is reachable: curl https://tenant.goskope.com
```

#### Scenario 3: Netskope API Errors

```
Symptom: Lambda fails with API error

Causes:
- Invalid API token
- Insufficient token permissions
- Rate limiting
- Tenant FQDN incorrect

Debug Steps:
1. Test token manually:
   curl -H "Netskope-Api-Token: xxx" \
        https://tenant.goskope.com/api/v2/infrastructure/publishers
2. Check token permissions: Settings → Administration → Administrators & Roles
   (or Settings → Tools → REST API v2 for legacy tokens)
   Required scopes: Infrastructure Management, Private Applications
3. Verify tenant FQDN matches exactly
```

---

## Error Handling & Retries

### Lambda Retry Strategy

CloudFormation **does not** automatically retry failed custom resources. Lambda must handle retries internally.

**Current implementation:**
- EC2 state: 5s polling, 120s timeout, 30s safety buffer
- SSM ready: Exponential backoff [5,10,15,20,30,30...], 30s safety buffer
- Command execution: 5s polling, 300s timeout, 30s safety buffer
- Publisher disconnect: 5s polling, 120s timeout, 15s safety buffer
- Delete retry: 10s interval, 8 attempts, re-removes from apps on each retry
- API calls: Exponential backoff retry via `call_netskope_api_with_retry()` (2^attempt: 1s, 2s, 4s), max 3 retries (reduced to 2 in polling loops)

```python
def call_netskope_api_with_retry(method, api_url, token, req_payload, max_retries=3):
    """API calls with exponential backoff. Uses stdlib urllib.request/urllib.error."""
    for attempt in range(max_retries):
        try:
            return call_netskope_api(method, api_url, token, req_payload)
        except (urllib.error.URLError, urllib.error.HTTPError, ClientError, ConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            time.sleep(wait_time)
```

### CloudFormation Response Handling

**Critical:** Lambda MUST always send a response to CloudFormation, even on failure:

```python
try:
    result = handle_create(event, context)
    send_response(event, context, 'SUCCESS', result)
except Exception as e:
    logging.error(f"Failed: {str(e)}")
    send_response(event, context, 'FAILED', {'Error': str(e)})
    # DO NOT re-raise - CloudFormation needs the FAILED response
```

**If Lambda crashes without sending response:**
- CloudFormation waits for 1 hour
- Stack gets stuck in CREATE_IN_PROGRESS
- Manual intervention required (delete stack)

---

## Timer & Polling Architecture

### Timeout Hierarchy

The Lambda execution timeout (600s) is the outer boundary. All sub-timeouts must fit within it:

```
Lambda Timeout (600s)
├── CREATE path
│   ├── check_timeout_budget(300s) ← pre-flight check
│   ├── wait_for_instance_running     (EC2_READY_TIMEOUT=120s, 30s buffer)
│   ├── check_timeout_budget(240s) ← pre-flight check
│   ├── SSM readiness polling         (SSM_READY_TIMEOUT=240s, 30s buffer)
│   ├── wait_for_command_completion   (COMMAND_TIMEOUT=300s, 30s buffer)
│   └── cfnresponse.send()
│
└── DELETE path
    ├── check_timeout_budget(120s) ← pre-flight check
    ├── remove_publisher_from_apps    (API calls with retry)
    ├── wait_for_publisher_disconnected (PUBLISHER_DISCONNECT_TIMEOUT=120s, 15s buffer)
    ├── delete publisher with retry   (8 attempts, 10s intervals, re-removes from apps on each retry)
    └── cfnresponse.send()
```

### Polling Loop Details

| Loop | Interval | Max Wait | Safety Buffer | Checks |
|------|----------|----------|---------------|--------|
| `wait_for_instance_running` | 5s | `EC2_READY_TIMEOUT` (120s) | 30s | `describe_instances` State == "running" |
| SSM readiness (inline in `handle_create`) | [5,10,15,20,30,30...] | `SSM_READY_TIMEOUT` (240s) | 30s | `describe_instance_information` PingStatus == "Online" |
| `wait_for_command_completion` | 5s | `COMMAND_TIMEOUT` (300s) | 30s | `get_command_invocation` Status + stdout pattern matching for false-positive "Success" |
| `wait_for_publisher_disconnected` | 5s | `PUBLISHER_DISCONNECT_TIMEOUT` (120s) | 15s | Publisher status != "connected" |
| Delete retry (inline in `handle_delete`) | 10s | 8 attempts (~80s) | N/A | DELETE API succeeds; re-PATCHes apps on each retry |

### Safety Buffer Rationale

- **30s for create path**: After each polling loop completes, subsequent steps still need execution time. The 30s buffer ensures the Lambda doesn't time out mid-operation.
- **15s for delete path**: Only `cfnresponse` remains after the delete polling loops, so a smaller buffer is sufficient.
- **Pre-flight budget checks**: `check_timeout_budget()` is called before expensive operations. If the Lambda has fewer seconds remaining than `required_seconds`, it raises immediately rather than starting work it can't finish.

### API Retry Internals

`call_netskope_api_with_retry()` uses exponential backoff:
- Default: 3 retries (waits of 1s, 2s, 4s)
- In polling loops: reduced to 2 retries (`max_retries=2`) to avoid compounding delays when the outer loop is already retrying
- Catches: `urllib.error.URLError`, `urllib.error.HTTPError`, `botocore.exceptions.ClientError`, `ConnectionError`

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#timeout-reference) for operator-facing timeout tuning guidance.

---

## Security Considerations

### Secret Management

**API Token Storage:**
```
AWS Systems Manager Parameter Store (SecureString)
├─ Encrypted at rest (KMS)
├─ Encrypted in transit (TLS)
├─ Access controlled via IAM
└─ Rotation supported (manual)
```

**Never logged:**
- API token (masked in logs)
- Registration token (passed via SSM parameter, not visible in CloudWatch)

### Network Security

**EC2 Instance:**
- No public IP address
- Private subnet only
- Security group: egress-only (no inbound rules)
- Access via Session Manager (no SSH keys)

**Lambda Function:**
- Runs in AWS-managed VPC (not customer VPC)
- Communicates with AWS services via AWS PrivateLink
- API calls to Netskope over TLS

### IAM Least Privilege

**Instance Role:**
```yaml
Policies:
  - AmazonSSMManagedInstanceCore  # SSM access only
  - NO S3, NO DynamoDB, NO other services
```

**Lambda Role:**
```yaml
Policies:
  - EC2: Describe only (read-only)
  - SSM: Command execution (specific instance only)
  - SSM Parameter Store: Read specific parameter only
  - CloudWatch Logs: Write to own log group only
```

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the full troubleshooting guide, including diagnostic commands, log analysis, SSM debugging, and manual testing procedures.

---

## References

**AWS Documentation:**
- [Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/execute-remote-commands.html)
- [CloudFormation Custom Resources](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-custom-resources.html)
- [Lambda Function Handler](https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html)

**Netskope Documentation:**
- [REST API v2 Overview](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- [Publishers API](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)

**Best Practices:**
- [Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [SSM Agent Troubleshooting](https://docs.aws.amazon.com/systems-manager/latest/userguide/troubleshooting-ssm-agent.html)
