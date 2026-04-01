# NPA Publisher Troubleshooting Guide

Common issues and solutions for NPA Publisher deployments on AWS.

## Table of Contents

- [Stack Deployment Issues](#stack-deployment-issues)
- [Systems Manager Issues](#systems-manager-issues)
- [Netskope API Issues](#netskope-api-issues)
- [Command Execution Issues](#command-execution-issues)
- [Timeout Reference](#timeout-reference)
- [Network Connectivity Issues](#network-connectivity-issues)
- [Diagnostic Commands](#diagnostic-commands)

## Stack Deployment Issues

### Issue: Stack Stuck at CREATE_IN_PROGRESS

**Cause**: Custom resource waiting for Lambda response

**Solution**:
1. Check Lambda logs in CloudWatch
2. Look for errors or timeout messages
3. Verify instance has SSM agent running: `aws ssm describe-instance-information`

**CloudWatch Logs Location**:
```bash
aws logs tail /aws/lambda/<PublisherGroupName>-RegistrationHandler --follow
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

### Issue: Stack Rollback with "ROLLBACK_IN_PROGRESS"

**Cause**: Resource creation failed during deployment

**Solution**:
1. Check CloudFormation events for the specific resource that failed:
```bash
aws cloudformation describe-stack-events \
  --stack-name netskope-npa-publisher \
  --max-items 20 \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`]'
```
2. Review the `ResourceStatusReason` field for error details
3. Fix the issue (e.g., invalid AMI ID, missing permissions, etc.)
4. Delete the failed stack and redeploy

## Systems Manager Issues

### Issue: "Instance did not become available in Systems Manager"

**Cause**: SSM agent not running or network connectivity issue

**Solution**:
1. Verify instance is in running state:
```bash
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].State.Name'
```

2. Check security group allows outbound HTTPS (443):
```bash
aws ec2 describe-security-groups \
  --group-ids <security-group-id> \
  --query 'SecurityGroups[0].IpPermissionsEgress'
```

3. Verify subnet has route to internet via NAT Gateway:
```bash
aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=<subnet-id>" \
  --query 'RouteTables[0].Routes'
```

4. Check IAM instance profile has `AmazonSSMManagedInstanceCore` policy:
```bash
aws iam get-instance-profile --instance-profile-name <profile-name>
```

5. Connect via EC2 Instance Connect and check SSM agent:
```bash
systemctl status amazon-ssm-agent
journalctl -u amazon-ssm-agent -n 50
```

### Issue: SSM Session Manager Connection Fails

**Cause**: VPC endpoint configuration or network issue

**Solution**:

**For new VPC deployments** (with VPC endpoints):
1. Verify VPC endpoints are created and available:
```bash
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<vpc-id>" \
  --query 'VpcEndpoints[*].[ServiceName,State]' \
  --output table
```

2. Check VPC endpoint security group allows inbound HTTPS from publisher security group

3. Verify private DNS is enabled on VPC endpoints:
```bash
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<vpc-id>" \
  --query 'VpcEndpoints[*].[ServiceName,PrivateDnsEnabled]'
```

**For existing VPC deployments**:
1. Verify either VPC endpoints exist or security group allows 0.0.0.0/0 for HTTPS
2. Check NAT Gateway is operational

## Netskope API Issues

### Issue: "Failed to get registration token"

**Cause**: Netskope API authentication or permissions issue

**Solution**:
1. Verify API token in SSM Parameter Store is correct:
```bash
TOKEN=$(aws ssm get-parameter \
  --name <StackName>-netskope-api-token \
  --with-decryption \
  --query Parameter.Value \
  --output text)
echo $TOKEN
```

2. Check token has infrastructure management permissions in Netskope UI:
   - Log in to Netskope tenant
   - Go to **Settings → Administration → Administrators & Roles** (or **Settings → Tools → REST API v2** for legacy tokens)
   - Verify the token's service account has **Infrastructure Management** and **Private Applications** scopes

3. Verify tenant FQDN is correct (e.g., `mytenant.goskope.com`, not `mytenant.eu.goskope.com`)

4. Test API manually:
```bash
curl -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers
```

**Expected response**: JSON with publishers list

### Issue: "API call failed after 3 attempts"

**Cause**: Transient API issues or rate limiting

**Solution**:
1. Check Lambda logs for specific error details
2. Verify Netskope tenant is accessible:
```bash
curl -I https://mytenant.goskope.com
```
3. Wait 5-10 minutes and retry the deployment
4. If persistent, contact Netskope support

## Command Execution Issues

### Issue: Command execution failed

**Cause**: `npa_publisher_wizard` script failed on instance

**Solution**:
1. Check Lambda logs for stderr output:
```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/<PublisherGroupName>-RegistrationHandler \
  --filter-pattern "stderr"
```

2. Connect to instance via Session Manager:
```bash
aws ssm start-session --target $INSTANCE_ID
```

3. Check SSM agent logs:
```bash
sudo tail -f /var/log/amazon/ssm/amazon-ssm-agent.log
```

4. Manually run the wizard (test mode):
```bash
sudo /home/ubuntu/npa_publisher_wizard -token "test"
```

### Issue: SSM Command Timeout

**Cause**: Command took longer than configured timeout (default 300 seconds)

**Solution**:
1. Check if the command is still running on the instance:
```bash
ps aux | grep npa_publisher_wizard
```

2. Review Lambda environment variables and increase `COMMAND_TIMEOUT` if needed:
```bash
aws lambda update-function-configuration \
  --function-name <PublisherGroupName>-RegistrationHandler \
  --environment Variables={COMMAND_TIMEOUT=600}
```

3. Redeploy the stack or trigger the Lambda manually

## Timeout Reference

The Lambda function uses a chain of timeouts. Each polling loop runs within the Lambda execution timeout and includes a safety buffer to leave time for remaining work and the `cfnresponse` callback.

### Timeout Chain

| Timeout | Default | Env Var | Controls |
|---------|---------|---------|----------|
| Lambda execution | 600s | CFT `Timeout` property | Overall execution limit for the Lambda function |
| EC2 ready | 120s | `EC2_READY_TIMEOUT` | Wait for instance to enter `running` state |
| SSM ready | 240s | `SSM_READY_TIMEOUT` | Wait for SSM agent to register and report `Online` |
| Command execution | 300s | `COMMAND_TIMEOUT` | Wait for `npa_publisher_wizard` SSM command to complete |
| Publisher disconnect | 120s | `PUBLISHER_DISCONNECT_TIMEOUT` | Wait for publisher to disconnect during delete (polled before deletion) |
| Delete retry | 8 attempts, 10s intervals (~80s) | (hardcoded) | Retry publisher deletion; re-removes publisher from apps on each attempt to handle eventual consistency |

### Safety Buffers

Each polling loop aborts early when the Lambda timeout approaches:
- **Create-path loops** (EC2 ready, SSM ready, command execution): abort with 30s remaining, leaving time for subsequent steps and `cfnresponse`
- **Delete-path loops** (publisher disconnect): abort with 15s remaining, leaving time only for `cfnresponse`

### Total Time Budget

The 600s Lambda timeout must cover the full chain. Worst-case times for the CREATE path:

```
EC2 ready (120s) + SSM ready (240s) + Command execution (300s) = 660s
```

This exceeds the 600s Lambda timeout, but in practice the safety buffers cause early exit. If you experience timeouts in all three stages, consider increasing the Lambda timeout.

### How to Adjust Timeouts

Update individual timeout environment variables:

```bash
aws lambda update-function-configuration \
  --function-name <PublisherGroupName>-RegistrationHandler \
  --environment "Variables={
    tenant_fqdn=mytenant.goskope.com,
    api_token=NetskopeAPIToken-<PublisherGroupName>,
    EC2_READY_TIMEOUT=180,
    SSM_READY_TIMEOUT=300,
    COMMAND_TIMEOUT=400,
    PUBLISHER_DISCONNECT_TIMEOUT=180
  }"
```

**Important:** The Lambda `Timeout` property is set in the CloudFormation template. If you increase sub-timeouts, ensure the Lambda timeout covers the sum. Update the `Timeout` value in the template and run `aws cloudformation update-stack`.

### Symptom to Timeout Mapping

| Symptom | Timeout to Adjust |
|---------|-------------------|
| Stack stuck at `CREATE_IN_PROGRESS` for 10+ minutes | Lambda `Timeout` (in CFT) |
| "Instance did not enter running state" | `EC2_READY_TIMEOUT` |
| "Instance did not become available in Systems Manager" | `SSM_READY_TIMEOUT` |
| "Command did not complete" / command timeout | `COMMAND_TIMEOUT` |
| "Publisher did not disconnect" during stack deletion | `PUBLISHER_DISCONNECT_TIMEOUT` |
| "Aborting ... wait - Lambda timeout approaching" in logs | Lambda `Timeout` is too low for the sub-timeout chain |

See [DEVOPS-NOTES.md](DEVOPS-NOTES.md#timer--polling-architecture) for the internal timer and polling architecture.

## Network Connectivity Issues

### Issue: Publisher Not Connecting to Netskope

**Cause**: Security group or NAT Gateway issue blocking Netskope NewEdge connectivity

**Solution**:

1. Verify security group allows outbound HTTPS to Netskope NewEdge IPs:
```bash
aws ec2 describe-security-groups \
  --group-ids <security-group-id> \
  --query 'SecurityGroups[0].IpPermissionsEgress[?ToPort==`443`]'
```

Expected egress rules should include:
- 8.36.116.0/24
- 8.39.144.0/24
- 31.186.239.0/24
- 163.116.128.0/17
- 162.10.0.0/17

2. Test connectivity from instance to Netskope:
```bash
# Connect via SSM Session Manager
aws ssm start-session --target $INSTANCE_ID

# Test connectivity
curl -I https://mytenant.goskope.com
telnet 8.36.116.1 443
```

3. Verify NAT Gateway is operational:
```bash
aws ec2 describe-nat-gateways \
  --filter "Name=vpc-id,Values=<vpc-id>" \
  --query 'NatGateways[*].[NatGatewayId,State]'
```

4. Check route table has route to NAT Gateway:
```bash
aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=<private-subnet-id>" \
  --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`]'
```

### Issue: VPC Endpoint Connection Failures

**Cause**: VPC endpoint configuration issue

**Solution**:

1. Verify VPC has DNS support enabled:
```bash
aws ec2 describe-vpc-attribute \
  --vpc-id <vpc-id> \
  --attribute enableDnsSupport

aws ec2 describe-vpc-attribute \
  --vpc-id <vpc-id> \
  --attribute enableDnsHostnames
```

Both should return `"Value": true`

2. Test DNS resolution inside instance:
```bash
# Connect via SSM
aws ssm start-session --target $INSTANCE_ID

# Test DNS resolution
nslookup ssm.us-east-1.amazonaws.com
nslookup ec2messages.us-east-1.amazonaws.com
```

3. Verify VPC endpoints are in the same subnets as instances:
```bash
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<vpc-id>" \
  --query 'VpcEndpoints[*].[ServiceName,SubnetIds]'
```

## Diagnostic Commands

### Get Full Stack Information

```bash
aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --output json > stack-info.json
```

### Get Instance Details

```bash
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name netskope-npa-publisher \
  --query 'Stacks[0].Outputs[?OutputKey==`PublisherInstanceId`].OutputValue' \
  --output text)

aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID \
  --output json > instance-details.json
```

### Get Lambda Function Logs

```bash
aws logs tail /aws/lambda/<PublisherGroupName>-RegistrationHandler \
  --since 1h \
  --format short > lambda-logs.txt
```

### Get SSM Command History

```bash
aws ssm list-commands \
  --instance-id $INSTANCE_ID \
  --max-results 10 \
  --output json > ssm-commands.json
```

### Get Specific SSM Command Output

```bash
COMMAND_ID="<command-id-from-lambda-logs>"

aws ssm get-command-invocation \
  --command-id $COMMAND_ID \
  --instance-id $INSTANCE_ID \
  --query '[StandardOutputContent,StandardErrorContent]' \
  --output text
```

### Test Netskope API Connectivity

```bash
# Get API token from SSM Parameter Store
TOKEN=$(aws ssm get-parameter \
  --name <StackName>-netskope-api-token \
  --with-decryption \
  --query Parameter.Value \
  --output text)

# Test publishers API
curl -v -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/infrastructure/publishers

# Test private apps API
curl -v -H "Netskope-Api-Token: $TOKEN" \
     https://mytenant.goskope.com/api/v2/steering/apps/private
```

## Getting Help

If you're still experiencing issues after trying these troubleshooting steps:

1. **Collect diagnostic information** using the commands above
2. **Review Lambda logs** for detailed error messages with troubleshooting steps
3. **Check AWS Service Health Dashboard** for regional outages
4. **Consult Netskope documentation** at https://docs.netskope.com
5. **File an issue** on the GitHub repository with:
   - CloudFormation template version
   - Deployment mode (new VPC or existing VPC)
   - Error messages from Lambda logs
   - Stack events showing failures
   - Relevant diagnostic command outputs

## Additional Resources

- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) - Deployment instructions
- [OPERATIONS.md](OPERATIONS.md) - Operational procedures (scaling, deletion, maintenance)
- [DEVOPS-NOTES.md](DEVOPS-NOTES.md) - Technical deep-dive (timer architecture, polling internals)
- [README.md](../README.md) - Architecture overview
- [Netskope REST API v2](https://docs.netskope.com/en/rest-api-v2-overview-312207.html)
- [AWS Systems Manager Troubleshooting](https://docs.aws.amazon.com/systems-manager/latest/userguide/troubleshooting-remote-commands.html)
