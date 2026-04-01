"""NPA Publisher registration with Netskope via CloudFormation Custom Resources."""

# pylint: disable=broad-exception-caught,too-many-branches,too-many-statements
# pylint: disable=too-many-locals,too-many-lines

import copy
import json
import logging
import os
import time
import urllib.error
import urllib.request

import boto3
import cfnresponse
from botocore.exceptions import ClientError

# Configuration from environment variables (use getenv to avoid crash at import time)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
tenant_fqdn = os.getenv("tenant_fqdn", "")
secret_name = os.getenv("api_token", "")
LOGLEVEL = os.getenv("LOGLEVEL", "INFO")

# Configurable timeouts
EC2_READY_TIMEOUT = int(os.getenv("EC2_READY_TIMEOUT", "120"))
SSM_READY_TIMEOUT = int(os.getenv("SSM_READY_TIMEOUT", "240"))
COMMAND_TIMEOUT = int(os.getenv("COMMAND_TIMEOUT", "300"))
PUBLISHER_DISCONNECT_TIMEOUT = int(os.getenv("PUBLISHER_DISCONNECT_TIMEOUT", "120"))

# Module-level boto3 clients (reused across warm invocations)
ec2_client = boto3.client("ec2", region_name=AWS_REGION)
ssm_client = boto3.client("ssm", region_name=AWS_REGION)

# Logging
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOGLEVEL, logging.INFO))


# ==============================================================
# CUSTOM EXCEPTIONS
# ==============================================================


class PublisherRegistrationError(Exception):
    """Base exception for publisher registration errors."""

    def __init__(self, phase, details):
        self.phase = phase
        self.details = details
        super().__init__(f"{phase}: {details}")


class SSMError(PublisherRegistrationError):
    """SSM-related errors."""


class EC2Error(PublisherRegistrationError):
    """EC2 instance errors."""


class NetskopeAPIError(PublisherRegistrationError):
    """Netskope API errors."""


class CommandExecutionError(PublisherRegistrationError):
    """Command execution errors."""


# ==============================================================
# MAIN HANDLER
# ==============================================================


def get_remaining_time_ms(context):
    """Safely get remaining execution time in milliseconds"""
    try:
        return context.get_remaining_time_in_millis()
    except Exception:
        return None


def check_timeout_budget(context, required_seconds=30):
    """Check if we have enough Lambda execution time remaining"""
    remaining_ms = get_remaining_time_ms(context)
    if remaining_ms is not None and remaining_ms < required_seconds * 1000:
        raise PublisherRegistrationError(
            "Lambda Timeout",
            f"Only {remaining_ms / 1000:.0f}s remaining, need {required_seconds}s",
        )


def sanitize_event_for_logging(event):
    """Remove sensitive fields from event before logging"""
    safe_event = copy.deepcopy(event)
    if "ResourceProperties" in safe_event:
        props = safe_event["ResourceProperties"]
        for key in ["ServiceToken"]:
            if key in props:
                props[key] = "***REDACTED***"
    return safe_event


def lambda_handler(event, context):
    """
    CloudFormation Custom Resource Lambda Handler
    Handles CREATE, UPDATE, DELETE events for NPA Publisher registration
    """
    logger.info(
        f"Lambda invoked with event: "
        f"{json.dumps(sanitize_event_for_logging(event), default=str)}"
    )

    try:
        # Check if this is a CloudFormation Custom Resource event
        if "RequestType" not in event:
            logger.error("Not a CloudFormation Custom Resource event")
            # Must send cfnresponse if ResponseURL is present to avoid CloudFormation hang
            if "ResponseURL" in event:
                cfnresponse.send(
                    event,
                    context,
                    cfnresponse.FAILED,
                    {"Error": "Not a CloudFormation Custom Resource event"},
                )
            return

        request_type = event["RequestType"]
        resource_properties = event.get("ResourceProperties", {})

        # Validate required environment variables
        if not tenant_fqdn:
            raise PublisherRegistrationError(
                "Configuration",
                "tenant_fqdn environment variable is not set",
            )
        if not secret_name:
            raise PublisherRegistrationError(
                "Configuration",
                "api_token environment variable is not set",
            )

        # Extract parameters with validation
        ec2_instance_id = resource_properties.get("InstanceId")
        publisher_name_prefix = resource_properties.get("PublisherNamePrefix")
        account_id = resource_properties.get("AccountId")
        app_associations = resource_properties.get("AppAssociations", "None")

        if not ec2_instance_id or not publisher_name_prefix or not account_id:
            missing = [
                k
                for k, v in {
                    "InstanceId": ec2_instance_id,
                    "PublisherNamePrefix": publisher_name_prefix,
                    "AccountId": account_id,
                }.items()
                if not v
            ]
            raise PublisherRegistrationError(
                "Parameter Validation",
                f'Missing required ResourceProperties: {", ".join(missing)}',
            )

        # Normalize app_associations to string
        if not isinstance(app_associations, str):
            app_associations = str(app_associations)

        # Get Netskope API token from SSM Parameter Store
        token = get_secret(secret_name)
        if not token:
            raise PublisherRegistrationError(
                "Secret Parsing",
                "API token parameter is empty",
            )

        # Generate publisher name
        publisher_name = (
            f"{publisher_name_prefix}-{account_id}-{ec2_instance_id}"
        )

        # Handle different request types
        if request_type == "Delete":
            logger.info("Processing CloudFormation DELETE event")
            handle_delete(publisher_name, token, context, instance_id=ec2_instance_id)
            cfnresponse.send(
                event,
                context,
                cfnresponse.SUCCESS,
                {"Message": "Publisher deregistered successfully"},
            )
            return

        if request_type == "Create":
            logger.info("Processing CloudFormation CREATE event")
            cw_config_param = resource_properties.get("CloudWatchConfigParam")
            result = handle_create(
                publisher_name, app_associations, ec2_instance_id, token, context,
                cw_config_param=cw_config_param,
            )
            cfnresponse.send(event, context, cfnresponse.SUCCESS, result)
            return

        if request_type == "Update":
            logger.info("Processing CloudFormation UPDATE event - no action needed")
            cfnresponse.send(
                event,
                context,
                cfnresponse.SUCCESS,
                {"Message": "Update completed (no changes required)"},
            )
            return

        logger.error(f"Unknown request type: {request_type}")
        cfnresponse.send(
            event,
            context,
            cfnresponse.FAILED,
            {"Error": f"Unknown request type: {request_type}"},
        )

    except PublisherRegistrationError as e:
        logger.error(f"[{e.phase}] {e.details}")
        cfnresponse.send(
            event,
            context,
            cfnresponse.FAILED,
            {"Error": str(e), "Phase": e.phase},
        )

    except Exception:
        logger.exception("Exception in lambda_handler")
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": "Internal error"})


# ==============================================================
# APP ASSOCIATION RESOLVER
# ==============================================================


def resolve_target_apps(app_associations, private_apps):
    """
    Resolve which private apps to target based on AppAssociations parameter.

    Modes:
        "All"                    - all private apps
        "tag:name1,name2"        - apps with ANY matching tag (OR, case-insensitive)
        "app1,app2"              - apps matching by name (with bracket normalization)

    Returns list of app dicts to associate with the publisher.
    """
    association_value = app_associations.strip()
    association_lower = association_value.lower()

    # Mode: ALL — return every private app
    if association_lower == "all":
        logger.info(f"Matching all {len(private_apps)} private apps")
        return list(private_apps)

    # Mode: TAG — match apps by tag name (OR logic, case-insensitive)
    if association_lower.startswith("tag:"):
        target_tag_names = {
            name.strip().lower()
            for name in association_value[4:].split(",")
            if name.strip()
        }

        if not target_tag_names:
            logger.warning(
                "tag: prefix used but no tag names specified - skipping"
            )
            return []

        def get_app_tag_names(app):
            """Extract lowercase tag names from a private app."""
            return {
                (tag.get("tag_name") or "").strip().lower()
                for tag in app.get("tags", [])
                if isinstance(tag, dict) and tag.get("tag_name")
            }

        target_apps = [
            app
            for app in private_apps
            if target_tag_names.intersection(get_app_tag_names(app))
        ]

        # Warn about any requested tag names not found on matched apps
        found_tags = set()
        for app in target_apps:
            found_tags.update(get_app_tag_names(app))
        missing = sorted(t for t in target_tag_names if t not in found_tags)
        if missing:
            logger.warning(
                f'Tags not found on any private app: {", ".join(missing)}'
            )

        logger.info(
            f"Tag matching (OR, case-insensitive) for "
            f"[{', '.join(sorted(target_tag_names))}]: "
            f"matched {len(target_apps)} of {len(private_apps)} private apps"
        )
        return target_apps

    # Mode: NAME — comma-separated list of app names
    target_app_names = [
        name.strip() for name in association_value.split(",") if name.strip()
    ]

    def app_name_matches(api_name, target_name):
        """Check if app name matches, ignoring optional brackets."""
        if api_name == target_name:
            return True
        stripped = api_name.strip("[]")
        return stripped == target_name or stripped == target_name.strip("[]")

    target_apps = [
        app
        for app in private_apps
        if any(
            app_name_matches(app.get("app_name", ""), name)
            for name in target_app_names
        )
    ]

    # Warn about any app names that weren't found
    found_names = {app.get("app_name", "").strip("[]") for app in target_apps}
    missing = [
        name
        for name in target_app_names
        if name.strip("[]") not in found_names
    ]
    if missing:
        logger.warning(f'Private apps not found: {", ".join(missing)}')

    return target_apps


# ==============================================================
# CREATE HANDLER
# ==============================================================


def handle_create(
    publisher_name, app_associations, ec2_instance_id, token, context=None,
    cw_config_param=None,
):
    """
    Handle CloudFormation CREATE event - register publisher with Netskope

    Args:
        publisher_name: Generated publisher name
        app_associations: App association mode - 'None', 'All', 'tag:name1,name2', or comma-separated app names
        ec2_instance_id: EC2 instance ID
        token: Netskope API token
        context: Lambda context for timeout awareness
        cw_config_param: SSM parameter name for CloudWatch agent config (None to skip)

    Steps:
    1. Create publisher and request registration token from Netskope API
    2. Wait for EC2 instance to be running
    3. Wait for host to become visible in Systems Manager
    4. Run npa_publisher_wizard command via SSM
    5. Update private applications
    6. Install and configure CloudWatch agent (if enabled)
    """

    # ==============================================================
    # STEP 1: Create Publisher and Request Registration Token
    # ==============================================================

    check_timeout_budget(context, required_seconds=300)
    logger.info(f"Creating a new publisher: {publisher_name}")

    # Create publisher in Netskope
    api_url = "/api/v2/infrastructure/publishers"
    payload = {"name": publisher_name}

    try:
        resp = call_netskope_api_with_retry("post", api_url, token, payload)
    except Exception as e:
        raise NetskopeAPIError("Publisher Creation", str(e)) from e

    # Handle case where publisher already exists
    if resp["status"] != "success":
        if resp.get("message", "").find("may exist already") != -1:
            logger.info("Publisher already exists. Getting existing publisher ID...")
            resp = call_netskope_api_with_retry("get", api_url, token, None)
            publishers = resp.get("data", {}).get("publishers", [])
            publisher_id = None
            for publisher in publishers:
                if publisher.get("publisher_name") == publisher_name:
                    publisher_id = publisher.get("publisher_id")
                    break
            if publisher_id is None:
                raise NetskopeAPIError(
                    "Publisher Lookup", "Publisher exists but could not find ID"
                )
            # Publisher already exists — skip re-registration
            logger.info(
                f"Publisher already registered with ID: {publisher_id}. "
                "Skipping registration."
            )
            return {
                "PublisherId": publisher_id,
                "PublisherName": publisher_name,
                "Status": "AlreadyExists",
                "AppsUpdated": 0,
                "Idempotent": True,
            }
        raise NetskopeAPIError(
            "Publisher Creation", resp.get("message", "Unknown error")
        )

    publisher_id = resp.get("data", {}).get("id")
    if publisher_id is None:
        raise NetskopeAPIError(
            "Publisher Creation",
            "API returned success but no publisher ID in response",
        )

    logger.info(f"Publisher ID is: {publisher_id}")

    # Request registration token from Netskope
    logger.info("Getting registration token from Netskope...")
    api_url = f"/api/v2/infrastructure/publishers/{publisher_id}/registration_token"

    try:
        resp = call_netskope_api_with_retry("post", api_url, token, None)
    except Exception as e:
        raise NetskopeAPIError("Registration Token", str(e)) from e

    if resp["status"] != "success":
        raise NetskopeAPIError(
            "Registration Token",
            resp.get("message", "Failed to get registration token"),
        )

    reg_token = resp.get("data", {}).get("token")
    if not reg_token:
        raise NetskopeAPIError(
            "Registration Token", "API returned success but no token in response"
        )
    logger.info("Successfully obtained registration token")

    # ==============================================================
    # STEP 2: Check EC2 Instance State
    # ==============================================================

    check_timeout_budget(context, required_seconds=240)
    logger.info("Checking EC2 instance state...")

    try:
        ec2_ready = wait_for_instance_running(
            ec2_client, ec2_instance_id, context, max_wait=EC2_READY_TIMEOUT
        )
        if not ec2_ready:
            raise EC2Error(
                "EC2 State Check",
                f"Instance did not enter running state within {EC2_READY_TIMEOUT}s",
            )
    except PublisherRegistrationError:
        raise
    except Exception as e:
        raise EC2Error("EC2 State Check", str(e)) from e

    logger.info("Instance is running, proceeding to SSM check")

    # ==============================================================
    # STEP 3: Wait for SSM Agent with Exponential Backoff
    # ==============================================================

    logger.info("Waiting for instance to register with Systems Manager...")

    # Use exponential backoff: 5s, 10s, 15s, 20s, 30s, 30s, 30s...
    wait_times = [5, 10, 15, 20, 30, 30, 30, 30, 30, 30]
    instance_ready = False

    for attempt, wait_time in enumerate(wait_times):
        remaining = get_remaining_time_ms(context)
        if remaining is not None and remaining < 30000:
            logger.warning("Aborting SSM wait - Lambda timeout approaching")
            break

        logger.info(f"SSM check attempt {attempt + 1}/{len(wait_times)}")

        try:
            response = ssm_client.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [ec2_instance_id]}]
            )

            # Check if instance is registered and online
            if len(response["InstanceInformationList"]) > 0:
                instance_info = response["InstanceInformationList"][0]
                ping_status = instance_info["PingStatus"]
                platform = instance_info.get("PlatformType", "Unknown")
                agent_version = instance_info.get("AgentVersion", "Unknown")

                logger.info(
                    f"Instance found in SSM - Status: {ping_status}, "
                    f"Platform: {platform}, Agent: {agent_version}"
                )

                if ping_status == "Online":
                    logger.info("Instance is online in SSM!")
                    instance_ready = True
                    break
                logger.info(
                    f"Instance registered but not online yet (Status: {ping_status})"
                )
            else:
                logger.info("Instance not yet visible in SSM")

        except Exception as e:
            logger.warning(f"Error checking SSM status: {e}")

        # Wait before next attempt (unless this was the last attempt)
        if attempt < len(wait_times) - 1:
            logger.info(f"Waiting {wait_time} seconds before retry...")
            time.sleep(wait_time)

    if not instance_ready:
        raise SSMError(
            "SSM Agent Registration",
            f"Instance did not become available in Systems Manager after {SSM_READY_TIMEOUT}s",
        )

    # ==============================================================
    # STEP 4: Send Command via SSM and Wait for Completion
    # ==============================================================

    check_timeout_budget(context, required_seconds=120)
    logger.info("Sending registration command to instance via SSM...")

    # Build the command with registration token
    command = f'sudo /home/ubuntu/npa_publisher_wizard -token "{reg_token}"'

    # Send command via SSM Run Command
    try:
        response = ssm_client.send_command(
            InstanceIds=[ec2_instance_id],
            DocumentName="AWS-RunShellScript",
            Comment="Registering NPA publisher with Netskope",
            Parameters={"commands": [command]},
            TimeoutSeconds=COMMAND_TIMEOUT,
        )
    except Exception as e:
        raise SSMError("Send Command", str(e)) from e

    command_id = response["Command"]["CommandId"]
    logger.info(f"SSM command sent. Command ID: {command_id}")

    # Wait for command to complete
    try:
        command_success = wait_for_command_completion(
            ssm_client, command_id, ec2_instance_id, context, max_wait=COMMAND_TIMEOUT
        )

        if not command_success:
            raise CommandExecutionError(
                "Command Execution", "Publisher registration command failed"
            )
    except PublisherRegistrationError:
        raise
    except Exception as e:
        raise CommandExecutionError("Command Execution", str(e)) from e

    logger.info("Publisher registration command completed successfully")

    # ==============================================================
    # STEP 5: Update Private Applications
    # ==============================================================

    check_timeout_budget(context, required_seconds=60)
    apps_updated = 0

    if app_associations.strip().lower() == "none":
        logger.info('AppAssociations is "None" - skipping private app updates')
    else:
        logger.info(
            f"Updating private applications (AppAssociations: {app_associations})..."
        )

        # Get all private apps
        api_url = "/api/v2/steering/apps/private"

        try:
            resp = call_netskope_api_with_retry("get", api_url, token, None)
        except Exception as e:
            raise NetskopeAPIError("Fetch Private Apps", str(e)) from e

        if resp["status"] != "success":
            raise NetskopeAPIError(
                "Fetch Private Apps",
                resp.get("message", "Failed to fetch private applications"),
            )

        private_apps = resp.get("data", {}).get("private_apps", [])
        target_apps = resolve_target_apps(app_associations, private_apps)

        for app in target_apps:
            private_app_id = app.get("app_id")
            if not private_app_id:
                logger.warning(
                    f'Skipping app with missing app_id: {app.get("app_name", "unknown")}'
                )
                continue
            service_publisher_assignments = app.get("service_publisher_assignments", [])

            # Check if publisher already assigned
            publisher_already_assigned = False
            for pub in service_publisher_assignments:
                if pub["publisher_id"] == publisher_id:
                    publisher_already_assigned = True
                    logger.info(f'Publisher already assigned to {app["app_name"]}')
                    break

            if publisher_already_assigned:
                continue

            # Add publisher to app
            service_publisher_assignments.append({"publisher_id": publisher_id})
            payload = {"publishers": service_publisher_assignments}

            app_api_url = f"/api/v2/steering/apps/private/{private_app_id}"

            try:
                resp = call_netskope_api_with_retry(
                    "patch", app_api_url, token, payload
                )
            except Exception as e:
                logger.warning(f'Error updating app {app["app_name"]}: {e}')
                continue

            if resp["status"] != "success":
                logger.warning(
                    f'Got error updating app {app["app_name"]}: {json.dumps(resp)}'
                )
                continue

            logger.info(f'Successfully added publisher to app: {app["app_name"]}')
            apps_updated += 1

        logger.info(
            f"Publisher registration completed. Updated {apps_updated} private applications"
        )

    # ==============================================================
    # STEP 6: Install and Configure CloudWatch Agent (if enabled)
    # ==============================================================

    cw_installed = False
    if cw_config_param:
        check_timeout_budget(context, required_seconds=120)
        logger.info("Installing CloudWatch agent via SSM Distributor...")

        try:
            install_success = False
            for install_attempt in range(3):
                if install_attempt > 0:
                    logger.info(
                        f"Retrying CW agent install (attempt {install_attempt + 1}/3)..."
                    )
                    time.sleep(5)

                response = ssm_client.send_command(
                    InstanceIds=[ec2_instance_id],
                    DocumentName="AWS-ConfigureAWSPackage",
                    Comment="Install CloudWatch agent",
                    Parameters={
                        "action": ["Install"],
                        "name": ["AmazonCloudWatchAgent"],
                    },
                    TimeoutSeconds=120,
                )
                install_cmd_id = response["Command"]["CommandId"]
                logger.info(
                    f"CW agent install command sent. Command ID: {install_cmd_id}"
                )

                install_success = wait_for_command_completion(
                    ssm_client, install_cmd_id, ec2_instance_id, context, max_wait=120
                )

                if install_success:
                    break

            if not install_success:
                logger.warning(
                    "CloudWatch agent install failed after 3 attempts - skipping configure"
                )
            else:
                logger.info("CloudWatch agent installed. Configuring...")

                response = ssm_client.send_command(
                    InstanceIds=[ec2_instance_id],
                    DocumentName="AmazonCloudWatch-ManageAgent",
                    Comment="Configure CloudWatch agent",
                    Parameters={
                        "action": ["configure"],
                        "mode": ["ec2"],
                        "optionalConfigurationSource": ["ssm"],
                        "optionalConfigurationLocation": [cw_config_param],
                        "optionalRestart": ["yes"],
                    },
                    TimeoutSeconds=60,
                )
                config_cmd_id = response["Command"]["CommandId"]
                logger.info(
                    f"CW agent configure command sent. Command ID: {config_cmd_id}"
                )

                config_success = wait_for_command_completion(
                    ssm_client, config_cmd_id, ec2_instance_id, context, max_wait=60
                )

                if config_success:
                    logger.info("CloudWatch agent configured and started")
                    cw_installed = True
                else:
                    logger.warning("CloudWatch agent configure failed")

        except Exception as e:
            logger.warning(f"CloudWatch agent setup failed (non-fatal): {e}")

    return {
        "PublisherId": publisher_id,
        "PublisherName": publisher_name,
        "Status": "Registered",
        "AppsUpdated": apps_updated,
        "CloudWatchAgent": cw_installed,
        "Idempotent": False,
    }


# ==============================================================
# HELPER FUNCTION - Remove Publisher from Apps
# ========================================================


def remove_publisher_from_apps(publisher_id, token):
    """
    Remove a publisher from all private applications it is assigned to

    Args:
        publisher_id: Publisher ID to remove
        token: Netskope API token

    Returns:
        Number of apps updated
    """
    logger.info(f"Removing publisher {publisher_id} from all private apps...")

    api_url = "/api/v2/steering/apps/private"

    try:
        resp = call_netskope_api_with_retry("get", api_url, token, None)
    except Exception as e:
        logger.error(f"Error fetching private apps: {e}")
        raise

    if resp["status"] != "success":
        logger.error(f"Got error while calling {api_url}")
        logger.error(f"Response: {json.dumps(resp)}")
        raise NetskopeAPIError(
            "Fetch Private Apps", resp.get("message", "Unknown error")
        )

    private_apps = resp.get("data", {}).get("private_apps", [])
    apps_updated = 0

    for app in private_apps:
        private_app_id = app.get("app_id")
        if not private_app_id:
            logger.warning(
                f'Skipping app with missing app_id: {app.get("app_name", "unknown")}'
            )
            continue
        service_publisher_assignments = app.get("service_publisher_assignments", [])

        # Find and remove publisher
        publisher_used = False
        updated_assignments = []

        for pub in service_publisher_assignments:
            if pub["publisher_id"] == publisher_id:
                logger.info(f'Removing publisher from app: {app["app_name"]}')
                publisher_used = True
            else:
                updated_assignments.append(pub)

        if not publisher_used:
            continue

        # Update app with publisher removed
        app_api_url = f"/api/v2/steering/apps/private/{private_app_id}"
        payload = {"publishers": updated_assignments}

        try:
            resp = call_netskope_api_with_retry("patch", app_api_url, token, payload)
        except Exception as e:
            logger.error(f'Error updating app {app["app_name"]}: {e}')
            continue

        if resp["status"] != "success":
            logger.error(
                f'Got error updating app {app["app_name"]}: {json.dumps(resp)}'
            )
        else:
            logger.info(f'Successfully removed publisher from app: {app["app_name"]}')
            apps_updated += 1

    logger.info(f"Removed publisher from {apps_updated} private applications")
    return apps_updated


# ==============================================================
# DELETE HANDLER
# ==============================================================


def handle_delete(publisher_name, token, context=None, instance_id=None):
    """
    Handle CloudFormation DELETE event - deregister publisher from Netskope

    Args:
        publisher_name: Name of publisher to delete
        token: Netskope API token
        context: Lambda context for timeout awareness
        instance_id: EC2 instance ID (optional, used to stop instance for disconnection)
    """
    check_timeout_budget(context, required_seconds=120)
    logger.info(f"Deregistering publisher: {publisher_name}")

    # Get publisher ID
    api_url = "/api/v2/infrastructure/publishers"
    publisher_id = 0

    try:
        resp = call_netskope_api_with_retry("get", api_url, token, None)
        publishers = resp.get("data", {}).get("publishers", [])

        for publisher in publishers:
            if publisher.get("publisher_name") == publisher_name:
                publisher_id = publisher.get("publisher_id", 0)
                break
    except Exception as e:
        logger.warning(f"Error looking up publisher: {e}")

    if publisher_id == 0:
        logger.warning(
            f"Publisher {publisher_name} not found. May have been already deleted."
        )
        return

    logger.info(f"Found publisher ID: {publisher_id}")

    # Stop EC2 instance to trigger publisher disconnect
    if instance_id:
        try:
            logger.info(
                f"Stopping EC2 instance {instance_id} to disconnect publisher..."
            )
            ec2_client.stop_instances(InstanceIds=[instance_id])
            logger.info(f"EC2 stop request sent for instance {instance_id}")
        except Exception as e:
            logger.warning(f"Could not stop EC2 instance: {e}")
            logger.warning("Continuing with deletion anyway...")

    # Remove publisher from all private apps using helper function
    try:
        remove_publisher_from_apps(publisher_id, token)
    except Exception as e:
        logger.warning(f"Error removing publisher from apps: {e}")
        logger.warning("Continuing with publisher deletion...")

    # Wait for publisher to disconnect BEFORE attempting deletion
    # The Netskope API will reject deletion if publisher is still connected
    logger.info("Waiting for publisher to disconnect before deletion...")
    disconnected = wait_for_publisher_disconnected(
        publisher_id, token, context, max_wait=PUBLISHER_DISCONNECT_TIMEOUT
    )

    if not disconnected:
        logger.warning(
            f"Publisher {publisher_name} did not disconnect "
            f"within {PUBLISHER_DISCONNECT_TIMEOUT} seconds"
        )
        logger.warning("Attempting deletion anyway - may fail if still connected")
    else:
        logger.info(f"Publisher {publisher_name} successfully disconnected")

    # Now delete publisher from Netskope with retry logic for eventual consistency.
    # The Netskope API may still report app associations even after PATCHes succeed,
    # so on each retry we re-remove the publisher from apps to force propagation.
    api_url = f"/api/v2/infrastructure/publishers/{publisher_id}"
    max_delete_attempts = 8
    delete_retry_interval = 10  # seconds between retries

    for attempt in range(max_delete_attempts):
        try:
            resp = call_netskope_api_with_retry("delete", api_url, token, None)
        except Exception as e:
            logger.error(f"Error deleting publisher: {e}")
            raise

        if resp["status"] == "success":
            logger.info(
                f"Successfully completed deletion of publisher: {publisher_name}"
            )
            return

        # Check if failure is due to app association (eventual consistency issue)
        error_message = resp.get("message", "")
        if "associated with" in error_message and "apps" in error_message:
            if attempt < max_delete_attempts - 1:
                logger.warning(
                    f"Publisher still associated with apps "
                    f"(attempt {attempt + 1}/{max_delete_attempts})"
                )
                # Re-remove publisher from apps to force propagation
                try:
                    remove_publisher_from_apps(publisher_id, token)
                except Exception as e:
                    logger.warning(f"Re-remove from apps failed: {e}")
                logger.info(
                    f"Waiting {delete_retry_interval}s for eventual consistency..."
                )
                time.sleep(delete_retry_interval)
                continue
            logger.error(
                "Publisher deletion failed after all retries - still associated with apps"
            )
            logger.error(f"Got error while deleting publisher: {json.dumps(resp)}")
            raise NetskopeAPIError(
                "Publisher Deletion",
                "Still associated with apps after retries",
            )

        # Different error - fail immediately
        logger.error(f"Got error while deleting publisher: {json.dumps(resp)}")
        raise NetskopeAPIError(
            "Publisher Deletion", resp.get("message", "Unknown error")
        )

    logger.info(f"Successfully completed deletion of publisher: {publisher_name}")


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
        except (urllib.error.URLError, urllib.error.HTTPError, ClientError, ConnectionError) as e:
            if attempt == max_retries - 1:
                # Last attempt failed
                logger.error(f"API call failed after {max_retries} attempts")
                raise

            # Calculate exponential backoff: 1s, 2s, 4s
            wait_time = 2**attempt
            logger.warning(f"API call attempt {attempt + 1} failed: {e}")
            logger.info(f"Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

    # Should never reach here, but just in case
    raise NetskopeAPIError("API Retry", "Unexpected error in API retry logic")


def wait_for_publisher_disconnected(publisher_id, token, context=None, max_wait=120):
    """
    Wait for publisher to disconnect after deletion request
    Polls publisher status until it's no longer 'connected'

    Args:
        publisher_id: Publisher ID to check
        token: Netskope API token
        context: Lambda context for timeout awareness
        max_wait: Maximum seconds to wait (default: 120)

    Returns:
        bool: True if disconnected, False if timeout
    """
    logger.info(f"Waiting for publisher {publisher_id} to disconnect...")

    api_url = f"/api/v2/infrastructure/publishers/{publisher_id}"
    elapsed = 0
    wait_interval = 5  # Check every 5 seconds

    while elapsed < max_wait:
        remaining = get_remaining_time_ms(context)
        if remaining is not None and remaining < 15000:
            logger.warning("Aborting disconnect wait - Lambda timeout approaching")
            return False

        try:
            resp = call_netskope_api_with_retry(
                "get", api_url, token, None, max_retries=2
            )

            if resp["status"] == "success" and "data" in resp:
                publisher_status = resp["data"].get("status", "")
                logger.info(
                    f'Publisher status: "{publisher_status}" (elapsed: {elapsed}s)'
                )

                # Success condition: any status that is NOT "connected"
                if publisher_status != "connected":
                    logger.info(
                        f"Publisher disconnected (status: "
                        f'"{publisher_status}") after {elapsed} seconds'
                    )
                    return True

                # Publisher is still connected - keep waiting
                logger.info(
                    f"Publisher still connected, waiting... ({elapsed}/{max_wait}s)"
                )
                time.sleep(wait_interval)
                elapsed += wait_interval

            else:
                # If we can't get publisher info, it may have been deleted
                logger.info("Publisher not found - may have been deleted successfully")
                return True

        except Exception as e:
            logger.warning(f"Error checking publisher status: {e}")
            # If we get an error, the publisher may no longer exist
            return True

    logger.warning(
        f"Timeout waiting for publisher to disconnect after {max_wait} seconds"
    )
    return False


def wait_for_publisher_apps_cleared(publisher_id, token, context=None, max_wait=60):
    """
    Wait for publisher to have no associated apps after removal.
    Verifies by checking the actual private apps API rather than relying on the
    publisher's apps_count field, which has eventual consistency issues.

    Args:
        publisher_id: Publisher ID to check
        token: Netskope API token
        context: Lambda context for timeout awareness
        max_wait: Maximum seconds to wait (default: 60)

    Returns:
        bool: True if apps cleared, False if timeout
    """
    logger.info(f"Waiting for publisher {publisher_id} app associations to clear...")

    apps_api_url = "/api/v2/steering/apps/private"
    elapsed = 0
    wait_interval = 3  # Check every 3 seconds

    while elapsed < max_wait:
        remaining = get_remaining_time_ms(context)
        if remaining is not None and remaining < 15000:
            logger.warning("Aborting apps-cleared wait - Lambda timeout approaching")
            return False

        try:
            # Check actual app assignments rather than publisher's apps_count
            resp = call_netskope_api_with_retry(
                "get", apps_api_url, token, None, max_retries=2
            )

            if resp["status"] == "success":
                private_apps = resp.get("data", {}).get("private_apps", [])
                associated_apps = []
                for app in private_apps:
                    for pub in app.get("service_publisher_assignments", []):
                        if pub.get("publisher_id") == publisher_id:
                            associated_apps.append(app.get("app_name", "unknown"))
                            break

                if not associated_apps:
                    logger.info(
                        f"Publisher app associations cleared after {elapsed} seconds"
                    )
                    return True

                logger.info(
                    f"Publisher still in {len(associated_apps)} app(s): "
                    f"{associated_apps}, waiting... ({elapsed}/{max_wait}s)"
                )
                time.sleep(wait_interval)
                elapsed += wait_interval
            else:
                logger.warning(f"Error fetching private apps: {json.dumps(resp)}")
                time.sleep(wait_interval)
                elapsed += wait_interval

        except Exception as e:
            logger.warning(f"Error checking publisher app associations: {e}")
            time.sleep(wait_interval)
            elapsed += wait_interval

    logger.warning(
        f"Timeout waiting for publisher app associations to clear after {max_wait} seconds"
    )
    return False


def wait_for_instance_running(ec2_client_ref, instance_id, context=None, max_wait=120):
    """
    Wait for EC2 instance to enter 'running' state

    Args:
        ec2_client_ref: boto3 EC2 client
        instance_id: EC2 instance ID
        context: Lambda context for timeout awareness
        max_wait: Maximum seconds to wait

    Returns:
        True if instance is running, False if timeout
    """
    logger.info(f"Waiting for instance {instance_id} to enter running state...")

    start_time = time.time()
    check_interval = 5

    while (time.time() - start_time) < max_wait:
        remaining = get_remaining_time_ms(context)
        if remaining is not None and remaining < 30000:
            logger.warning("Aborting instance wait - Lambda timeout approaching")
            return False

        try:
            response = ec2_client_ref.describe_instances(InstanceIds=[instance_id])

            if len(response["Reservations"]) > 0:
                instance = response["Reservations"][0]["Instances"][0]
                state = instance["State"]["Name"]

                logger.info(f"Instance state: {state}")

                if state == "running":
                    return True
                if state in ["terminated", "shutting-down", "stopped", "stopping"]:
                    logger.error(f"Instance entered unexpected state: {state}")
                    return False

        except Exception as e:
            logger.warning(f"Error checking instance state: {e}")

        time.sleep(check_interval)

    logger.error(f"Instance did not enter running state within {max_wait} seconds")
    return False


def wait_for_command_completion(  # pylint: disable=too-many-nested-blocks
    ssm_client_ref, command_id, instance_id, context=None, max_wait=300
):
    """
    Wait for SSM command to complete and check status

    Args:
        ssm_client_ref: boto3 SSM client
        command_id: SSM command ID
        instance_id: EC2 instance ID
        context: Lambda context for timeout awareness
        max_wait: Maximum seconds to wait

    Returns:
        True if command succeeded, False otherwise
    """
    logger.info(f"Waiting for command {command_id} to complete...")

    start_time = time.time()
    check_interval = 5

    while (time.time() - start_time) < max_wait:
        remaining = get_remaining_time_ms(context)
        if remaining is not None and remaining < 30000:
            logger.warning("Aborting command wait - Lambda timeout approaching")
            return False

        try:
            response = ssm_client_ref.get_command_invocation(
                CommandId=command_id, InstanceId=instance_id
            )

            status = response["Status"]
            logger.info(f"Command status: {status}")

            # Terminal states
            if status == "Success":
                logger.info("Command completed with Success status")
                stdout = response.get("StandardOutputContent", "")
                if stdout:
                    logger.info(f"Standard Output (first 500 chars): {stdout[:500]}")

                    # Check if registration actually succeeded by parsing output
                    # The npa_publisher_wizard command may return 0 exit code even on failure
                    if (
                        "Registration failed" in stdout
                        or "Error: Registration" in stdout
                    ):
                        logger.error(
                            "Publisher registration failed despite command Success status"
                        )
                        logger.error(f"Full output: {stdout}")
                        return False

                    # Additional failure patterns
                    failure_patterns = [
                        "context deadline exceeded",
                        "Timeout exceeded",
                        "admin call didn't succeed",
                        "Please generate a new token",
                    ]

                    for pattern in failure_patterns:
                        if pattern in stdout:
                            logger.error(
                                f'Registration failure detected: "{pattern}" found in output'
                            )
                            logger.error(f"Full output: {stdout}")
                            return False

                    logger.info("Publisher registration command completed successfully")
                return True

            if status in ["Failed", "Cancelled", "TimedOut"]:
                logger.error(f"Command failed with status: {status}")
                stderr = response.get("StandardErrorContent", "")
                stdout = response.get("StandardOutputContent", "")
                if stderr:
                    logger.error(f"Standard Error: {stderr}")
                if stdout:
                    logger.error(f"Standard Output: {stdout}")
                return False

            # Still running states: 'Pending', 'InProgress', 'Delayed'
            # Continue waiting

        except ssm_client_ref.exceptions.InvocationDoesNotExist:
            logger.info("Command invocation not yet available, waiting...")

        except Exception as e:
            logger.warning(f"Error checking command status: {e}")

        time.sleep(check_interval)

    logger.error(f"Command did not complete within {max_wait} seconds")
    return False


VALID_HTTP_METHODS = {"get", "post", "patch", "put", "delete"}


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
    if method not in VALID_HTTP_METHODS:
        raise ValueError(
            f'Invalid HTTP method: {method}. Must be one of: {", ".join(VALID_HTTP_METHODS)}'
        )

    url = f"https://{tenant_fqdn}{api_url}"
    headers = {
        "Netskope-Api-Token": token,
        "accept": "application/json",
        "Content-Type": "application/json",
    }
    data = json.dumps(req_payload).encode("utf-8") if req_payload else None

    logger.info(f"Calling Netskope API: {method.upper()} {api_url}")

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())

    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise NetskopeAPIError("api_call", f"Server error {e.code}") from e
        body = e.read().decode("utf-8")

    try:
        response = json.loads(body)
    except (ValueError, json.JSONDecodeError) as exc:
        raise NetskopeAPIError("api_call", f"Invalid JSON: {body[:200]}") from exc

    logger.info(f"API Response: {json.dumps(response)[:200]}...")

    return response


def get_secret(param_name):
    """
    Retrieve secret from AWS Systems Manager Parameter Store

    Args:
        param_name: SSM parameter name

    Returns:
        Parameter value as string
    """
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(f"Error retrieving parameter: {error_code}")
        raise

    return response["Parameter"]["Value"]
