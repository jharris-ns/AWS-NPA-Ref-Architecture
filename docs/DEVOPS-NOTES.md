# DevOps Technical Notes

## Overview

This document explains the technical implementation details of the NPA Publisher deployment, focusing on AWS Systems Manager (SSM) integration and the Lambda function orchestration.

## Table of Contents

- [AWS Systems Manager Usage](#aws-systems-manager-usage)
- [Lambda Function Architecture](#lambda-function-architecture)
- [Integration Flow](#integration-flow)
- [Error Handling & Retries](#error-handling--retries)
- [Security Considerations](#security-considerations)
- [Troubleshooting Guide](#troubleshooting-guide)

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
Runtime: Python 3.11
Timeout: 900 seconds (15 minutes)
Memory: 256 MB
Environment Variables:
  - EC2_READY_TIMEOUT: 120    # EC2 state check timeout
  - SSM_READY_TIMEOUT: 240    # SSM registration timeout
  - COMMAND_TIMEOUT: 300      # Command execution timeout
  - NETSKOPE_TENANT: (from CloudFormation)
  - PUBLISHER_GROUP_NAME: (from CloudFormation)
  - API_TOKEN_SECRET_ARN: (from CloudFormation)
```

### IAM Permissions Required

```yaml
EC2:
  - ec2:DescribeInstances          # Check instance state

SSM:
  - ssm:DescribeInstanceInformation  # Check if SSM agent online
  - ssm:SendCommand                  # Execute registration command
  - ssm:GetCommandInvocation         # Monitor command status

Secrets Manager:
  - secretsmanager:GetSecretValue    # Retrieve Netskope API token

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
    "NetskopeAPITokenSecretArn": "arn:aws:secretsmanager:...",
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
def get_secret(secret_arn):
    """Retrieve Netskope API token from Secrets Manager"""
    response = secretsmanager_client.get_secret_value(SecretId=secret_arn)
    return json.loads(response['SecretString'])['NetskopeAPIToken']
```

**Step 2: Create Publisher in Netskope**
```python
def call_netskope_api(method, endpoint, data=None):
    """Make authenticated API call to Netskope"""
    url = f"https://{netskope_tenant}/api/v2/{endpoint}"
    headers = {
        'Netskope-Api-Token': api_token,
        'Content-Type': 'application/json'
    }
    response = requests.request(method, url, headers=headers, json=data)
    return response.json()

# Create publisher
publisher_data = {
    'publisher_name': f"{group_name}-{account_id}-{instance_id}",
    'publisher_type': 'on-prem'
}
result = call_netskope_api('POST', 'infrastructure/publishers', publisher_data)
registration_token = result['data']['registration_token']
```

**Step 3: Wait for EC2 Running State**
```python
def wait_for_instance_running(instance_id, timeout=120):
    """Poll EC2 until instance is running"""
    start_time = time.time()

    while time.time() - start_time < timeout:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        state = response['Reservations'][0]['Instances'][0]['State']['Name']

        if state == 'running':
            return True
        elif state in ['terminated', 'terminating']:
            raise Exception(f"Instance entered {state} state")

        time.sleep(10)  # Check every 10 seconds

    raise Exception(f"Instance did not reach running state within {timeout}s")
```

**Step 4: Wait for SSM Agent Online**
```python
def wait_for_ssm_ready(instance_id):
    """Poll SSM with exponential backoff"""
    wait_times = [5, 10, 15, 20, 30, 30, 30, 30, 30, 30]  # seconds

    for wait_time in wait_times:
        response = ssm_client.describe_instance_information(
            Filters=[{'Key': 'InstanceIds', 'Values': [instance_id]}]
        )

        if response['InstanceInformationList']:
            # Instance is registered with SSM
            return True

        time.sleep(wait_time)

    raise Exception("Instance did not become available in Systems Manager")
```

**Exponential backoff rationale:**
- Early checks: 5s intervals (catch fast registrations)
- Later checks: 30s intervals (reduce API calls)
- Total time: ~200 seconds max
- Typical success: 30-60 seconds

**Step 5: Execute Registration Command**
```python
def execute_registration_command(instance_id, token):
    """Send SSM command to run npa_publisher_wizard"""
    command = f'/home/ubuntu/npa_publisher_wizard -token "{token}"'

    response = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': [command]},
        Comment='Registering NPA publisher with Netskope',
        TimeoutSeconds=300
    )

    return response['Command']['CommandId']
```

**Step 6: Wait for Command Completion**
```python
def wait_for_command_completion(command_id, instance_id):
    """Poll command status until Success or Failed"""
    max_wait = 300  # 5 minutes
    start_time = time.time()

    while time.time() - start_time < max_wait:
        response = ssm_client.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id
        )

        status = response['Status']

        if status == 'Success':
            return response
        elif status in ['Failed', 'TimedOut', 'Cancelled']:
            stdout = response.get('StandardOutputContent', '')
            stderr = response.get('StandardErrorContent', '')
            raise Exception(f"Command failed: {stderr}\nOutput: {stdout}")

        time.sleep(5)  # Poll every 5 seconds

    raise Exception("Command did not complete within timeout")
```

**Step 7: Update Private Applications**
```python
def update_private_applications(publisher_id, group_name):
    """Assign publisher to matching private apps"""
    # Get all private apps
    apps = call_netskope_api('GET', 'steering/apps/private')

    # Filter apps that start with publisher group name
    matching_apps = [
        app for app in apps['data']['private_apps']
        if app['app_name'].startswith(group_name)
    ]

    # Update each app to use this publisher
    for app in matching_apps:
        app_data = {
            'publisher_id': publisher_id,
            # ... other app settings
        }
        call_netskope_api('PATCH', f'steering/apps/private/{app["id"]}', app_data)

    return len(matching_apps)
```

**Step 8: Send Success Response to CloudFormation**
```python
def send_response(event, context, status, data=None):
    """Send response to CloudFormation custom resource"""
    response_body = {
        'Status': status,  # 'SUCCESS' or 'FAILED'
        'Reason': f'See CloudWatch Log: {context.log_stream_name}',
        'PhysicalResourceId': data.get('publisher_id', 'NONE'),
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {}
    }

    # PUT response to pre-signed S3 URL
    requests.put(
        event['ResponseURL'],
        json=response_body,
        headers={'Content-Type': ''}
    )
```

#### 4. DELETE Flow (handle_delete)

**Step 1: Extract Publisher ID**
```python
publisher_id = event['PhysicalResourceId']
```

**Step 2: Remove Publisher from Apps**
```python
# Get all private apps using this publisher
apps = call_netskope_api('GET', 'steering/apps/private')

for app in apps['data']['private_apps']:
    if app.get('publisher_id') == publisher_id:
        # Remove publisher assignment
        update_data = {'publisher_id': None}
        call_netskope_api('PATCH', f'steering/apps/private/{app["id"]}', update_data)
```

**Step 3: Delete Publisher**
```python
call_netskope_api('DELETE', f'infrastructure/publishers/{publisher_id}')
```

**Step 4: Send Success Response**
```python
send_response(event, context, 'SUCCESS')
```

---

## Integration Flow

### Complete Timeline

```
t=0s    CloudFormation starts stack creation
        ├─ Creates VPC resources (if new VPC)
        ├─ Creates Security Group
        ├─ Creates IAM Role & Instance Profile
        └─ Creates Secrets Manager secret

t=30s   EC2 Instance launches
        └─ User data runs (minimal, no secrets)

t=35s   CloudFormation Custom Resource invokes Lambda

t=40s   Lambda: Get API token from Secrets Manager
        └─ Call Netskope API to create publisher
        └─ Receive registration token

t=45s   Lambda: Wait for EC2 running state
        └─ Poll ec2:DescribeInstances every 10s

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
2. Check token permissions in Netskope UI
3. Verify tenant FQDN matches exactly
```

---

## Error Handling & Retries

### Lambda Retry Strategy

CloudFormation **does not** automatically retry failed custom resources. Lambda must handle retries internally.

**Current implementation:**
- EC2 state: 10s polling, 120s timeout
- SSM ready: Exponential backoff, 240s timeout
- Command execution: 5s polling, 300s timeout
- API calls: No retries (fails immediately)

**Recommended enhancements:**
```python
from botocore.exceptions import ClientError
import time

def call_netskope_api_with_retry(method, endpoint, data=None, max_retries=3):
    """API calls with exponential backoff"""
    for attempt in range(max_retries):
        try:
            response = call_netskope_api(method, endpoint, data)
            return response
        except (requests.RequestException, ClientError) as e:
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

## Security Considerations

### Secret Management

**API Token Storage:**
```
AWS Secrets Manager
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
  - Secrets Manager: Read specific secret only
  - CloudWatch Logs: Write to own log group only
```

---

## Troubleshooting Guide

### CloudWatch Logs Analysis

**Lambda Logs Location:**
```
/aws/lambda/<PublisherGroupName>-RegistrationHandler
```

**Key log messages (successful flow):**
```
[INFO] Creating a new publisher: MyPublisher-123456789-i-abc123
[INFO] Successfully obtained registration token: tok_xxxxx
[INFO] Waiting for instance to be running...
[INFO] Instance is running, proceeding to SSM check
[INFO] Checking if instance is available in SSM (attempt 1/10)
[INFO] Checking if instance is available in SSM (attempt 2/10)
[INFO] Instance is online in SSM!
[INFO] Sending registration command to instance
[INFO] Waiting for command completion...
[INFO] Command completed successfully
[INFO] Updating 3 private applications
[INFO] Publisher registration completed. Updated 3 private applications
```

**Error indicators:**
```
[ERROR] Failed to get registration token: 401 Unauthorized
[ERROR] Instance did not become running within 120 seconds
[ERROR] Instance did not become available in Systems Manager within 240 seconds
[ERROR] Command failed with status: Failed
[ERROR] StandardErrorContent: /home/ubuntu/npa_publisher_wizard: not found
```

### SSM Command Debugging

**List recent commands:**
```bash
aws ssm list-commands \
  --instance-id i-0123456789abcdef \
  --max-results 10
```

**Get command details:**
```bash
aws ssm get-command-invocation \
  --command-id abc-123-def-456 \
  --instance-id i-0123456789abcdef \
  --output json
```

**View output:**
```bash
# See stdout
aws ssm get-command-invocation \
  --command-id abc-123-def-456 \
  --instance-id i-0123456789abcdef \
  --query 'StandardOutputContent' \
  --output text

# See stderr
aws ssm get-command-invocation \
  --command-id abc-123-def-456 \
  --instance-id i-0123456789abcdef \
  --query 'StandardErrorContent' \
  --output text
```

### Manual Testing

**Test SSM connectivity:**
```bash
# From your workstation
aws ssm start-session --target i-0123456789abcdef

# Once connected, check SSM agent
sudo systemctl status amazon-ssm-agent
sudo journalctl -u amazon-ssm-agent -n 50
```

**Test Netskope API manually:**
```bash
# Get API token
aws secretsmanager get-secret-value \
  --secret-id NetskopeAPIToken-MyPublisher \
  --query SecretString \
  --output text | jq -r '.NetskopeAPIToken'

# Test API call
curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers
```

**Manually run registration wizard:**
```bash
# Connect via Session Manager
aws ssm start-session --target i-0123456789abcdef

# Run wizard (replace token)
sudo /home/ubuntu/npa_publisher_wizard -token "your-registration-token"

# Check publisher status
systemctl status npa_publisher
```


## References

**AWS Documentation:**
- [Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/execute-remote-commands.html)
- [CloudFormation Custom Resources](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-custom-resources.html)
- [Lambda Function Handler](https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html)

**Netskope Documentation:**
- [REST API v2 Overview](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- [Publishers API](https://docs.netskope.com/en/netskope-help/integrations-439794/netskope-api-integration-365842/rest-api-v2-overview-312207/infrastructure-api/)

**Best Practices:**
- [Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [SSM Agent Troubleshooting](https://docs.aws.amazon.com/systems-manager/latest/userguide/troubleshooting-ssm-agent.html)
