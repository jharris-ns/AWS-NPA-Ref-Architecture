import boto3
from datetime import datetime, timezone
import json
import requests
import os
from botocore.exceptions import ClientError
import time
import urllib3
import cfnresponse

# Disable SSL warnings for development (remove in production)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration from environment variables
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
tenant_fqdn = os.environ['tenant_fqdn']
secret_name = os.environ['api_token']
LOGLEVEL = os.getenv('LOGLEVEL', 'INFO')

# Configurable timeouts
EC2_READY_TIMEOUT = int(os.getenv('EC2_READY_TIMEOUT', '120'))
SSM_READY_TIMEOUT = int(os.getenv('SSM_READY_TIMEOUT', '240'))
COMMAND_TIMEOUT = int(os.getenv('COMMAND_TIMEOUT', '300'))
PUBLISHER_DISCONNECT_TIMEOUT = int(os.getenv('PUBLISHER_DISCONNECT_TIMEOUT', '120'))


# ==============================================================
# CUSTOM EXCEPTIONS WITH TROUBLESHOOTING
# ==============================================================

class PublisherRegistrationError(Exception):
    """Base exception for publisher registration errors with troubleshooting guidance"""
    def __init__(self, phase, details, troubleshooting=None):
        self.phase = phase
        self.details = details
        self.troubleshooting = troubleshooting or self._get_default_troubleshooting()
        super().__init__(f"{phase}: {details}")

    def _get_default_troubleshooting(self):
        return ["Check CloudWatch logs for more details"]

    def format_error(self):
        """Format error with troubleshooting steps"""
        msg = f"\n{'='*60}\n"
        msg += f"ERROR PHASE: {self.phase}\n"
        msg += f"DETAILS: {self.details}\n"
        msg += f"\nTROUBLESHOOTING STEPS:\n"
        for i, step in enumerate(self.troubleshooting, 1):
            msg += f"  {i}. {step}\n"
        msg += f"{'='*60}\n"
        return msg


class SSMError(PublisherRegistrationError):
    """SSM-related errors"""
    def _get_default_troubleshooting(self):
        return [
            "Check security group allows HTTPS (443) outbound",
            "Verify NAT Gateway exists in route table",
            "Check IAM instance profile has AmazonSSMManagedInstanceCore policy",
            "Connect via EC2 Instance Connect: systemctl status amazon-ssm-agent"
        ]


class EC2Error(PublisherRegistrationError):
    """EC2 instance errors"""
    def _get_default_troubleshooting(self):
        return [
            "Check instance state in EC2 console",
            "Review EC2 instance logs",
            "Verify instance type is supported",
            "Check if instance was manually terminated"
        ]


class NetskopeAPIError(PublisherRegistrationError):
    """Netskope API errors"""
    def _get_default_troubleshooting(self):
        return [
            "Verify API token in Secrets Manager is correct",
            "Check token has infrastructure management permissions",
            "Verify tenant FQDN is correct",
            "Test API manually: curl -H 'Netskope-Api-Token: xxx' https://tenant.goskope.com/api/v2/infrastructure/publishers"
        ]


class CommandExecutionError(PublisherRegistrationError):
    """Command execution errors"""
    def _get_default_troubleshooting(self):
        return [
            "Check Lambda logs for stderr output",
            "Connect via Session Manager and check /var/log/amazon/ssm/",
            "Manually run: sudo /home/ubuntu/npa_publisher_wizard -token 'test'",
            "Verify publisher wizard exists at /home/ubuntu/"
        ]


# ==============================================================
# LOGGING
# ==============================================================

class SimpleLogger:
    """Simple logger for Lambda"""
    def __init__(self, level='INFO'):
        self.level = level

    def info(self, msg):
        print(f'[INFO] {msg}')

    def error(self, msg):
        print(f'[ERROR] {msg}')

    def warning(self, msg):
        print(f'[WARNING] {msg}')


logger = SimpleLogger(level=LOGLEVEL)


# ==============================================================
# MAIN HANDLER
# ==============================================================

def lambda_handler(event, context):
    """
    CloudFormation Custom Resource Lambda Handler
    Handles CREATE, UPDATE, DELETE events for NPA Publisher registration
    """
    # Log complete event payload for debugging
    logger.info('=' * 80)
    logger.info('Lambda invoked with event:')
    logger.info(json.dumps(event, indent=2, default=str))
    logger.info('=' * 80)

    try:
        # Check if this is a CloudFormation Custom Resource event
        if 'RequestType' not in event:
            logger.error('Not a CloudFormation Custom Resource event')
            return {'statusCode': 400, 'body': 'Invalid event type'}

        request_type = event['RequestType']
        resource_properties = event['ResourceProperties']

        # Extract parameters
        EC2InstanceId = resource_properties['InstanceId']
        publisher_group_name = resource_properties['PublisherGroupName']
        account_id = resource_properties['AccountId']

        # Get Netskope API token
        token = json.loads(get_secret(secret_name))['token']

        # Generate publisher name
        publisher_name = publisher_group_name + "-" + account_id + '-' + EC2InstanceId

        # Handle different request types
        if request_type == 'Delete':
            logger.info('Processing CloudFormation DELETE event')
            handle_delete(publisher_name, publisher_group_name, token, instance_id=EC2InstanceId)
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'Message': 'Publisher deregistered successfully'
            })
            return

        elif request_type == 'Create':
            logger.info('Processing CloudFormation CREATE event')
            result = handle_create_idempotent(publisher_name, publisher_group_name, EC2InstanceId, token)
            cfnresponse.send(event, context, cfnresponse.SUCCESS, result)
            return

        elif request_type == 'Update':
            # UPDATE events are rare but can occur if CloudFormation properties change
            # without triggering a replacement (e.g., tags or other non-critical properties)
            logger.info('Processing CloudFormation UPDATE event - no action needed')
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'Message': 'Update completed (no changes required)'
            })
            return

        else:
            logger.error(f'Unknown request type: {request_type}')
            cfnresponse.send(event, context, cfnresponse.FAILED, {
                'Error': f'Unknown request type: {request_type}'
            })

    except PublisherRegistrationError as e:
        logger.error(e.format_error())
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e),
            'Phase': e.phase,
            'Troubleshooting': e.troubleshooting
        })

    except Exception as e:
        logger.error(f'Exception in lambda_handler: {str(e)}')
        import traceback
        logger.error(traceback.format_exc())
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e)
        })


# ==============================================================
# IDEMPOTENT CREATE HANDLER
# ==============================================================

def handle_create_idempotent(publisher_name, publisher_group_name, EC2InstanceId, token):
    """
    Idempotent version of handle_create
    Checks if publisher already exists for this instance before creating

    Args:
        publisher_name: Generated publisher name
        publisher_group_name: Publisher group name
        EC2InstanceId: EC2 instance ID
        token: Netskope API token

    Returns:
        dict: Publisher registration results
    """
    logger.info('Checking if publisher already exists...')

    # Check if publisher already exists
    try:
        api_url = '/api/v2/infrastructure/publishers'
        resp = call_netskope_api_with_retry('get', api_url, token, None)

        if resp['status'] == 'success' and 'data' in resp:
            publishers = resp['data'].get('publishers', [])

            # Look for existing publisher with this name
            for pub in publishers:
                if pub.get('publisher_name') == publisher_name:
                    publisher_id = pub.get('publisher_id')
                    logger.info(f'Publisher already exists with ID: {publisher_id}')
                    logger.info('Returning existing publisher (idempotent operation)')

                    # Return existing publisher info
                    return {
                        'PublisherId': publisher_id,
                        'PublisherName': publisher_name,
                        'Status': 'AlreadyExists',
                        'AppsUpdated': 0,
                        'Idempotent': True
                    }

    except Exception as e:
        logger.warning(f'Error checking for existing publisher: {str(e)}')
        logger.info('Proceeding with creation anyway...')

    # Publisher doesn't exist - create new one
    logger.info('Publisher does not exist. Creating new publisher...')
    return handle_create(publisher_name, publisher_group_name, EC2InstanceId, token)


# ==============================================================
# CREATE HANDLER
# ==============================================================

def handle_create(publisher_name, publisher_group_name, EC2InstanceId, token):
    """
    Handle CloudFormation CREATE event - register publisher with Netskope

    Steps:
    1. Create publisher and request registration token from Netskope API
    2. Wait for EC2 instance to be running
    3. Wait for host to become visible in Systems Manager
    4. Run npa_publisher_wizard command via SSM
    5. Update private applications
    """

    # ==============================================================
    # STEP 1: Create Publisher and Request Registration Token
    # ==============================================================

    logger.info('Creating a new publisher: ' + publisher_name)

    # Create publisher in Netskope
    api_url = '/api/v2/infrastructure/publishers'
    payload = {'name': publisher_name}

    try:
        resp = call_netskope_api_with_retry('post', api_url, token, payload)
    except Exception as e:
        raise NetskopeAPIError('Publisher Creation', str(e))

    # Handle case where publisher already exists
    if resp['status'] != 'success':
        if resp.get('message', '').find('may exist already') != -1:
            logger.info('Publisher already exists. Getting existing publisher ID...')
            resp = call_netskope_api_with_retry('get', api_url, token, None)
            publishers = resp['data']['publishers']
            publisher_id = None
            for publisher in publishers:
                if publisher['publisher_name'] == publisher_name:
                    publisher_id = publisher['publisher_id']
                    break
            if publisher_id is None:
                raise NetskopeAPIError('Publisher Lookup', 'Publisher exists but could not find ID')
        else:
            raise NetskopeAPIError('Publisher Creation', resp.get('message', 'Unknown error'))
    else:
        publisher_id = resp['data']['id']

    logger.info('Publisher ID is: ' + str(publisher_id))

    # Request registration token from Netskope
    logger.info('Getting registration token from Netskope...')
    api_url = '/api/v2/infrastructure/publishers/' + str(publisher_id) + '/registration_token'

    try:
        resp = call_netskope_api_with_retry('post', api_url, token, None)
    except Exception as e:
        raise NetskopeAPIError('Registration Token', str(e))

    if resp['status'] != 'success':
        raise NetskopeAPIError('Registration Token', resp.get('message', 'Failed to get registration token'))

    reg_token = resp['data']['token']
    logger.info('Successfully obtained registration token')

    # ==============================================================
    # STEP 2: Check EC2 Instance State
    # ==============================================================

    logger.info('Checking EC2 instance state...')
    ec2_client = boto3.client('ec2')

    try:
        ec2_ready = wait_for_instance_running(ec2_client, EC2InstanceId, max_wait=EC2_READY_TIMEOUT)
        if not ec2_ready:
            raise EC2Error('EC2 State Check', f'Instance did not enter running state within {EC2_READY_TIMEOUT}s')
    except PublisherRegistrationError:
        raise
    except Exception as e:
        raise EC2Error('EC2 State Check', str(e))

    logger.info('Instance is running, proceeding to SSM check')

    # ==============================================================
    # STEP 3: Wait for SSM Agent with Exponential Backoff
    # ==============================================================

    logger.info('Waiting for instance to register with Systems Manager...')
    ssm_client = boto3.client('ssm')

    # Use exponential backoff: 5s, 10s, 15s, 20s, 30s, 30s, 30s...
    wait_times = [5, 10, 15, 20, 30, 30, 30, 30, 30, 30]
    instance_ready = False

    for attempt, wait_time in enumerate(wait_times):
        logger.info(f'SSM check attempt {attempt + 1}/{len(wait_times)}')

        try:
            response = ssm_client.describe_instance_information(
                Filters=[{'Key': 'InstanceIds', 'Values': [EC2InstanceId]}]
            )

            # Check if instance is registered and online
            if len(response['InstanceInformationList']) > 0:
                instance_info = response['InstanceInformationList'][0]
                ping_status = instance_info['PingStatus']
                platform = instance_info.get('PlatformType', 'Unknown')
                agent_version = instance_info.get('AgentVersion', 'Unknown')

                logger.info(f'Instance found in SSM - Status: {ping_status}, Platform: {platform}, Agent: {agent_version}')

                if ping_status == 'Online':
                    logger.info('Instance is online in SSM!')
                    instance_ready = True
                    break
                else:
                    logger.info(f'Instance registered but not online yet (Status: {ping_status})')
            else:
                logger.info('Instance not yet visible in SSM')

        except Exception as e:
            logger.warning(f'Error checking SSM status: {str(e)}')

        # Wait before next attempt (unless this was the last attempt)
        if attempt < len(wait_times) - 1:
            logger.info(f'Waiting {wait_time} seconds before retry...')
            time.sleep(wait_time)

    if not instance_ready:
        raise SSMError('SSM Agent Registration',
                      f'Instance did not become available in Systems Manager after {SSM_READY_TIMEOUT}s')

    # ==============================================================
    # STEP 4: Send Command via SSM and Wait for Completion
    # ==============================================================

    logger.info('Sending registration command to instance via SSM...')

    # Build the command with registration token
    command = "sudo /home/ubuntu/npa_publisher_wizard -token " + "\"" + reg_token + "\""

    # Send command via SSM Run Command
    try:
        response = ssm_client.send_command(
            InstanceIds=[EC2InstanceId],
            DocumentName="AWS-RunShellScript",
            Comment='Registering NPA publisher with Netskope',
            Parameters={'commands': [command]},
            TimeoutSeconds=COMMAND_TIMEOUT
        )
    except Exception as e:
        raise SSMError('Send Command', str(e))

    command_id = response['Command']['CommandId']
    logger.info(f'SSM command sent. Command ID: {command_id}')

    # Wait for command to complete
    try:
        command_success = wait_for_command_completion(
            ssm_client,
            command_id,
            EC2InstanceId,
            max_wait=COMMAND_TIMEOUT
        )

        if not command_success:
            raise CommandExecutionError('Command Execution',
                                       'Publisher registration command failed')
    except PublisherRegistrationError:
        raise
    except Exception as e:
        raise CommandExecutionError('Command Execution', str(e))

    logger.info('Publisher registration command completed successfully')

    # ==============================================================
    # STEP 5: Update Private Applications
    # ==============================================================

    logger.info('Updating private applications to use new publisher...')

    # Get all private apps
    api_url = '/api/v2/steering/apps/private'

    try:
        resp = call_netskope_api_with_retry('get', api_url, token, None)
    except Exception as e:
        raise NetskopeAPIError('Fetch Private Apps', str(e))

    if resp['status'] != 'success':
        raise NetskopeAPIError('Fetch Private Apps', resp.get('message', 'Failed to fetch private applications'))

    # Update apps matching the publisher group name
    private_apps = resp['data']['private_apps']
    apps_updated = 0

    for app in private_apps:
        # Skip apps that don't match the naming convention
        if app['app_name'].find(publisher_group_name) == -1:
            continue

        logger.info(f'Found matching private app: {app["app_name"]}')

        private_app_id = app['app_id']
        service_publisher_assignments = app['service_publisher_assignments']

        # Check if publisher already assigned
        publisher_already_assigned = False
        for pub in service_publisher_assignments:
            if pub['publisher_id'] == publisher_id:
                publisher_already_assigned = True
                logger.info(f'Publisher already assigned to {app["app_name"]}')
                break

        # Skip if already assigned
        if publisher_already_assigned:
            continue

        # Add publisher to app
        service_publisher_assignments.append({'publisher_id': publisher_id})
        payload = {'publishers': service_publisher_assignments}

        api_url = '/api/v2/steering/apps/private/' + str(private_app_id)

        try:
            resp = call_netskope_api_with_retry('patch', api_url, token, payload)
        except Exception as e:
            logger.warning(f'Error updating app {app["app_name"]}: {str(e)}')
            continue

        if resp['status'] != 'success':
            logger.warning(f'Got error updating app {app["app_name"]}: ' + json.dumps(resp))
            # Don't fail - continue with other apps
            continue

        logger.info(f'Successfully added publisher to app: {app["app_name"]}')
        apps_updated += 1

    logger.info(f'Publisher registration completed. Updated {apps_updated} private applications')

    return {
        'PublisherId': publisher_id,
        'PublisherName': publisher_name,
        'Status': 'Registered',
        'AppsUpdated': apps_updated,
        'Idempotent': False
    }


# ==============================================================
# HELPER FUNCTION - Remove Publisher from Apps
# ==============================================================

def remove_publisher_from_apps(publisher_id, publisher_group_name, token):
    """
    Remove a publisher from all private applications matching the naming convention

    Args:
        publisher_id: Publisher ID to remove
        publisher_group_name: Publisher group name for filtering apps
        token: Netskope API token

    Returns:
        Number of apps updated
    """
    logger.info(f'Removing publisher {publisher_id} from private apps...')

    api_url = '/api/v2/steering/apps/private'

    try:
        resp = call_netskope_api_with_retry('get', api_url, token, None)
    except Exception as e:
        logger.error(f'Error fetching private apps: {str(e)}')
        raise

    if resp['status'] != 'success':
        logger.error('Got error while calling ' + api_url)
        logger.error('Response: ' + json.dumps(resp))
        raise Exception('Failed to fetch private applications')

    private_apps = resp['data']['private_apps']
    apps_updated = 0

    for app in private_apps:
        # Skip apps that don't match the naming convention
        if app['app_name'].find(publisher_group_name) == -1:
            continue

        logger.info(f'Checking private app: {app["app_name"]}')

        private_app_id = app['app_id']
        api_url = '/api/v2/steering/apps/private/' + str(private_app_id)
        service_publisher_assignments = app['service_publisher_assignments']

        # Find and remove publisher
        publisher_used = False
        updated_assignments = []

        for pub in service_publisher_assignments:
            if pub['publisher_id'] == publisher_id:
                logger.info(f'Removing publisher from app: {app["app_name"]}')
                publisher_used = True
            else:
                updated_assignments.append(pub)

        if not publisher_used:
            logger.info(f'Publisher not in use by {app["app_name"]}')
            continue

        # Update app with publisher removed
        payload = {'publishers': updated_assignments}

        try:
            resp = call_netskope_api_with_retry('patch', api_url, token, payload)
        except Exception as e:
            logger.error(f'Error updating app {app["app_name"]}: {str(e)}')
            continue

        if resp['status'] != 'success':
            logger.error(f'Got error updating app {app["app_name"]}: ' + json.dumps(resp))
            # Continue even if app update fails
        else:
            logger.info(f'Successfully removed publisher from app: {app["app_name"]}')
            apps_updated += 1

    logger.info(f'Removed publisher from {apps_updated} private applications')
    return apps_updated


# ==============================================================
# DELETE HANDLER
# ==============================================================

def handle_delete(publisher_name, publisher_group_name, token, instance_id=None):
    """
    Handle CloudFormation DELETE event - deregister publisher from Netskope

    Args:
        publisher_name: Name of publisher to delete
        publisher_group_name: Publisher group name
        token: Netskope API token
        instance_id: EC2 instance ID (optional, used to stop instance for disconnection)
    """
    logger.info('Deregistering publisher: ' + publisher_name)

    # Get publisher ID
    api_url = '/api/v2/infrastructure/publishers'
    publisher_id = 0

    try:
        resp = call_netskope_api_with_retry('get', api_url, token, None)
        publishers = resp['data']['publishers']

        for publisher in publishers:
            if publisher['publisher_name'] == publisher_name:
                publisher_id = publisher['publisher_id']
                break
    except Exception as e:
        logger.warning(f'Error looking up publisher: {str(e)}')

    if publisher_id == 0:
        logger.warning(f'Publisher {publisher_name} not found. May have been already deleted.')
        return

    logger.info(f'Found publisher ID: {publisher_id}')

    # Stop EC2 instance to trigger publisher disconnect
    if instance_id:
        try:
            logger.info(f'Stopping EC2 instance {instance_id} to disconnect publisher...')
            ec2_client = boto3.client('ec2', region_name=AWS_REGION)
            ec2_client.stop_instances(InstanceIds=[instance_id])
            logger.info(f'EC2 stop request sent for instance {instance_id}')
        except Exception as e:
            logger.warning(f'Could not stop EC2 instance: {str(e)}')
            logger.warning('Continuing with deletion anyway...')

    # Remove publisher from all private apps using helper function
    try:
        apps_updated = remove_publisher_from_apps(publisher_id, publisher_group_name, token)

        # Wait for Netskope to propagate the app disassociation
        # This is critical - the API has eventual consistency and may still report
        # apps associated even after the PATCH succeeds
        if apps_updated > 0:
            logger.info('Waiting for app disassociation to propagate...')
            apps_cleared = wait_for_publisher_apps_cleared(publisher_id, token, max_wait=60)
            if not apps_cleared:
                logger.warning('App disassociation may not have fully propagated - will retry deletion if needed')
    except Exception as e:
        logger.warning(f'Error removing publisher from apps: {str(e)}')
        logger.warning('Continuing with publisher deletion...')

    # Wait for publisher to disconnect BEFORE attempting deletion
    # The Netskope API will reject deletion if publisher is still connected
    logger.info(f'Waiting for publisher to disconnect before deletion...')
    disconnected = wait_for_publisher_disconnected(publisher_id, token, max_wait=PUBLISHER_DISCONNECT_TIMEOUT)

    if not disconnected:
        logger.warning(f'Publisher {publisher_name} did not disconnect within {PUBLISHER_DISCONNECT_TIMEOUT} seconds')
        logger.warning('Attempting deletion anyway - may fail if still connected')
    else:
        logger.info(f'Publisher {publisher_name} successfully disconnected')

    # Now delete publisher from Netskope with retry logic for eventual consistency
    api_url = '/api/v2/infrastructure/publishers/' + str(publisher_id)
    max_delete_attempts = 5
    delete_retry_interval = 5  # seconds between retries

    for attempt in range(max_delete_attempts):
        try:
            resp = call_netskope_api_with_retry('delete', api_url, token, None)
        except Exception as e:
            logger.error(f'Error deleting publisher: {str(e)}')
            raise

        if resp['status'] == 'success':
            logger.info(f'Successfully completed deletion of publisher: {publisher_name}')
            return

        # Check if failure is due to app association (eventual consistency issue)
        error_message = resp.get('message', '')
        if 'associated with' in error_message and 'apps' in error_message:
            if attempt < max_delete_attempts - 1:
                logger.warning(f'Publisher still associated with apps (attempt {attempt + 1}/{max_delete_attempts})')
                logger.info(f'Waiting {delete_retry_interval}s for eventual consistency...')
                time.sleep(delete_retry_interval)
                continue
            else:
                logger.error('Publisher deletion failed after all retries - still associated with apps')
                logger.error('Got error while deleting publisher: ' + json.dumps(resp))
                raise Exception('Failed to delete publisher - still associated with apps after retries')
        else:
            # Different error - fail immediately
            logger.error('Got error while deleting publisher: ' + json.dumps(resp))
            raise Exception('Failed to delete publisher')

    logger.info(f'Successfully completed deletion of publisher: {publisher_name}')


# ==============================================================
# HELPER FUNCTIONS
# ==============================================================

def call_netskope_api_with_retry(method, api_url, token, req_payload, max_retries=3):
    """
    Call Netskope REST API v2 with exponential backoff retry logic

    Args:
        method: HTTP method (get, post, patch, delete)
        api_url: API endpoint path
        token: Netskope API token
        req_payload: Request payload (dict or None)
        max_retries: Maximum number of retry attempts

    Returns:
        API response as dict

    Raises:
        Exception: If all retries fail
    """
    for attempt in range(max_retries):
        try:
            return call_netskope_api(method, api_url, token, req_payload)
        except (requests.RequestException, ClientError, ConnectionError) as e:
            if attempt == max_retries - 1:
                # Last attempt failed
                logger.error(f'API call failed after {max_retries} attempts')
                raise

            # Calculate exponential backoff: 1s, 2s, 4s
            wait_time = 2 ** attempt
            logger.warning(f'API call attempt {attempt + 1} failed: {str(e)}')
            logger.info(f'Retrying in {wait_time} seconds...')
            time.sleep(wait_time)

    # Should never reach here, but just in case
    raise Exception('Unexpected error in API retry logic')


def wait_for_publisher_disconnected(publisher_id, token, max_wait=120):
    """
    Wait for publisher to disconnect after deletion request
    Polls publisher status until it's no longer 'connected'

    Args:
        publisher_id: Publisher ID to check
        token: Netskope API token
        max_wait: Maximum seconds to wait (default: 120)

    Returns:
        bool: True if disconnected, False if timeout
    """
    logger.info(f'Waiting for publisher {publisher_id} to disconnect...')

    api_url = f'/api/v2/infrastructure/publishers/{publisher_id}'
    elapsed = 0
    wait_interval = 5  # Check every 5 seconds

    while elapsed < max_wait:
        try:
            resp = call_netskope_api_with_retry('get', api_url, token, None, max_retries=2)

            if resp['status'] == 'success' and 'data' in resp:
                publisher_status = resp['data'].get('status', '')
                logger.info(f'Publisher status: "{publisher_status}" (elapsed: {elapsed}s)')

                # Success condition: any status that is NOT "connected"
                if publisher_status != 'connected':
                    logger.info(f'Publisher disconnected (status: "{publisher_status}") after {elapsed} seconds')
                    return True

                # Publisher is still connected - keep waiting
                logger.info(f'Publisher still connected, waiting... ({elapsed}/{max_wait}s)')
                time.sleep(wait_interval)
                elapsed += wait_interval

            else:
                # If we can't get publisher info, it may have been deleted
                logger.info('Publisher not found - may have been deleted successfully')
                return True

        except Exception as e:
            logger.warning(f'Error checking publisher status: {str(e)}')
            # If we get an error, the publisher may no longer exist
            return True

    logger.warning(f'Timeout waiting for publisher to disconnect after {max_wait} seconds')
    return False


def wait_for_publisher_apps_cleared(publisher_id, token, max_wait=60):
    """
    Wait for publisher to have no associated apps after removal
    Polls publisher status until apps_count is 0

    Args:
        publisher_id: Publisher ID to check
        token: Netskope API token
        max_wait: Maximum seconds to wait (default: 60)

    Returns:
        bool: True if apps cleared, False if timeout
    """
    logger.info(f'Waiting for publisher {publisher_id} app associations to clear...')

    api_url = f'/api/v2/infrastructure/publishers/{publisher_id}'
    elapsed = 0
    wait_interval = 3  # Check every 3 seconds

    while elapsed < max_wait:
        try:
            resp = call_netskope_api_with_retry('get', api_url, token, None, max_retries=2)

            if resp['status'] == 'success' and 'data' in resp:
                apps_count = resp['data'].get('apps_count', 0)
                logger.info(f'Publisher apps_count: {apps_count} (elapsed: {elapsed}s)')

                if apps_count == 0:
                    logger.info(f'Publisher app associations cleared after {elapsed} seconds')
                    return True

                # Still has apps associated - keep waiting
                logger.info(f'Publisher still has {apps_count} app(s) associated, waiting... ({elapsed}/{max_wait}s)')
                time.sleep(wait_interval)
                elapsed += wait_interval

            else:
                # If we can't get publisher info, it may have been deleted
                logger.info('Publisher not found - may have been deleted')
                return True

        except Exception as e:
            logger.warning(f'Error checking publisher apps_count: {str(e)}')
            # Continue trying
            time.sleep(wait_interval)
            elapsed += wait_interval

    logger.warning(f'Timeout waiting for publisher app associations to clear after {max_wait} seconds')
    return False


def wait_for_instance_running(ec2_client, instance_id, max_wait=120):
    """
    Wait for EC2 instance to enter 'running' state

    Args:
        ec2_client: boto3 EC2 client
        instance_id: EC2 instance ID
        max_wait: Maximum seconds to wait

    Returns:
        True if instance is running, False if timeout
    """
    logger.info(f'Waiting for instance {instance_id} to enter running state...')

    start_time = time.time()
    check_interval = 5

    while (time.time() - start_time) < max_wait:
        try:
            response = ec2_client.describe_instances(InstanceIds=[instance_id])

            if len(response['Reservations']) > 0:
                instance = response['Reservations'][0]['Instances'][0]
                state = instance['State']['Name']

                logger.info(f'Instance state: {state}')

                if state == 'running':
                    return True
                elif state in ['terminated', 'shutting-down', 'stopped', 'stopping']:
                    logger.error(f'Instance entered unexpected state: {state}')
                    return False

        except Exception as e:
            logger.warning(f'Error checking instance state: {str(e)}')

        time.sleep(check_interval)

    logger.error(f'Instance did not enter running state within {max_wait} seconds')
    return False


def wait_for_command_completion(ssm_client, command_id, instance_id, max_wait=300):
    """
    Wait for SSM command to complete and check status

    Args:
        ssm_client: boto3 SSM client
        command_id: SSM command ID
        instance_id: EC2 instance ID
        max_wait: Maximum seconds to wait

    Returns:
        True if command succeeded, False otherwise
    """
    logger.info(f'Waiting for command {command_id} to complete...')

    start_time = time.time()
    check_interval = 5

    while (time.time() - start_time) < max_wait:
        try:
            response = ssm_client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id
            )

            status = response['Status']
            logger.info(f'Command status: {status}')

            # Terminal states
            if status == 'Success':
                logger.info('Command completed with Success status')
                stdout = response.get('StandardOutputContent', '')
                if stdout:
                    logger.info(f'Standard Output (first 500 chars): {stdout[:500]}')

                    # Check if registration actually succeeded by parsing output
                    # The npa_publisher_wizard command may return 0 exit code even on failure
                    if 'Registration failed' in stdout or 'Error: Registration' in stdout:
                        logger.error('Publisher registration failed despite command Success status')
                        logger.error(f'Full output: {stdout}')
                        return False

                    # Additional failure patterns
                    failure_patterns = [
                        'context deadline exceeded',
                        'Timeout exceeded',
                        'admin call didn\'t succeed',
                        'Please generate a new token'
                    ]

                    for pattern in failure_patterns:
                        if pattern in stdout:
                            logger.error(f'Registration failure detected: "{pattern}" found in output')
                            logger.error(f'Full output: {stdout}')
                            return False

                    logger.info('Publisher registration command completed successfully')
                return True

            elif status in ['Failed', 'Cancelled', 'TimedOut']:
                logger.error(f'Command failed with status: {status}')
                stderr = response.get('StandardErrorContent', '')
                stdout = response.get('StandardOutputContent', '')
                if stderr:
                    logger.error(f'Standard Error: {stderr}')
                if stdout:
                    logger.error(f'Standard Output: {stdout}')
                return False

            # Still running states: 'Pending', 'InProgress', 'Delayed'
            # Continue waiting

        except ssm_client.exceptions.InvocationDoesNotExist:
            logger.info('Command invocation not yet available, waiting...')

        except Exception as e:
            logger.warning(f'Error checking command status: {str(e)}')

        time.sleep(check_interval)

    logger.error(f'Command did not complete within {max_wait} seconds')
    return False


def call_netskope_api(method, api_url, token, req_payload):
    """
    Call Netskope REST API v2

    Args:
        method: HTTP method (get, post, patch, delete)
        api_url: API endpoint path
        token: Netskope API token
        req_payload: Request payload (dict or None)

    Returns:
        API response as dict
    """
    get_url = 'https://' + tenant_fqdn + api_url
    req_headers = {'Netskope-Api-Token': token, 'accept': "application/json"}

    logger.info(f'Calling Netskope API: {method.upper()} {api_url}')

    action = getattr(requests, method)
    r = action(headers=req_headers, json=req_payload, url=get_url)

    response = r.json()
    logger.info(f'API Response: {json.dumps(response)[:200]}...')

    return response


def get_secret(secret_name):
    """
    Retrieve secret from AWS Secrets Manager

    Args:
        secret_name: Secret ARN or name

    Returns:
        Secret value as string
    """
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=AWS_REGION
    )

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f'Error retrieving secret: {error_code}')
        raise e
    else:
        secret = get_secret_value_response['SecretString']

    return secret
