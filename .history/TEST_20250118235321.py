import requests
import json

# Terraform Cloud API details
terraform_cloud_url = "https://app.terraform.io/api/v2"
org_name = "your-organization-name"  # Replace with your Terraform Cloud organization name
workspace_name = "for_workspace_uoft_hacks"  # Replace with your Terraform Cloud workspace name
api_token = "dVlNetUaHyfjyA.atlasv1.6zuS64DM5pRk8lsYTkQdaV5ML04yGpeBj62QkyvgaiY0g5pImivGESHSmDsY4jck3t4"  # Terraform Cloud API token (use a secure method to store)

# User credentials and terraform script (received from frontend)
user_aws_access_key = "AKIA2RP6H7VO5IJS57PT"
user_aws_secret_key = "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"
terraform_script = """
provider "aws" {
  region = "us-east-1"
  access_key = "${var.AWS_ACCESS_KEY}"
  secret_key = "${var.AWS_SECRET_KEY}"
}

resource "aws_instance" "example" {
  ami           = "ami-097705bd69072a34d"
  instance_type = "t2.micro"
  key_name      = "the_pair"
}
"""

# Get the workspace ID from the Terraform Cloud workspace name
def get_workspace_id(workspace_name):
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/vnd.api+json"
    }

    # Get the workspace list
    workspaces_response = requests.get(
        f"{terraform_cloud_url}/organizations/{org_name}/workspaces",
        headers=headers
    )

    if workspaces_response.status_code != 200:
        print("Error fetching workspaces:", workspaces_response.status_code)
        print(workspaces_response.json())
        return None

    workspaces_data = workspaces_response.json()

    # Search for the workspace by name and get its ID
    for workspace in workspaces_data['data']:
        if workspace['attributes']['name'] == workspace_name:
            return workspace['id']
    
    print(f"Workspace {workspace_name} not found.")
    return None

# Create environment variables for AWS credentials
def set_environment_variables(workspace_id):
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/vnd.api+json"
    }

    # Set AWS Access Key
    aws_access_key_data = {
        "data": {
            "type": "vars",
            "attributes": {
                "key": "AWS_ACCESS_KEY",
                "value": user_aws_access_key,
                "category": "env",
                "hcl": False,
                "sensitive": True
            }
        }
    }

    response = requests.post(
        f"{terraform_cloud_url}/workspaces/{workspace_id}/vars",
        headers=headers,
        data=json.dumps(aws_access_key_data)
    )
    if response.status_code != 201:
        print(f"Error setting AWS_ACCESS_KEY: {response.status_code}")
        print(response.json())

    # Set AWS Secret Key
    aws_secret_key_data = aws_access_key_data.copy()
    aws_secret_key_data["data"]["attributes"]["key"] = "AWS_SECRET_KEY"
    aws_secret_key_data["data"]["attributes"]["value"] = user_aws_secret_key

    response = requests.post(
        f"{terraform_cloud_url}/workspaces/{workspace_id}/vars",
        headers=headers,
        data=json.dumps(aws_secret_key_data)
    )
    if response.status_code != 201:
        print(f"Error setting AWS_SECRET_KEY: {response.status_code}")
        print(response.json())

# Create a new configuration version in the workspace
def create_terraform_plan(terraform_script, workspace_id):
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/vnd.api+json"
    }

    # Create configuration version data
    data = {
        "data": {
            "type": "configuration-version",
            "attributes": {
                "terraform_version": "1.5.0",  # Specify Terraform version
            },
            "relationships": {
                "workspace": {
                    "data": {
                        "type": "workspaces",
                        "id": f"workspaces/{workspace_id}"
                    }
                }
            }
        }
    }

    # Upload the script to Terraform Cloud as a configuration version
    config_version_response = requests.post(
        f"{terraform_cloud_url}/configuration-versions",
        headers=headers,
        data=json.dumps(data)
    )

    if config_version_response.status_code != 201:
        print("Error creating configuration version:", config_version_response.status_code)
        print(config_version_response.json())
        return None

    config_version_data = config_version_response.json()
    if "data" not in config_version_data:
        print("Error: 'data' not found in the response.")
        print(config_version_data)
        return None

    config_version_id = config_version_data["data"]["id"]
    return config_version_id

# Trigger the plan and apply run
def trigger_terraform_run(config_version_id, workspace_id):
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/vnd.api+json"
    }

    data = {
        "data": {
            "type": "runs",
            "attributes": {
                "message": "Provision infrastructure via Terraform Cloud",
                "is-destroy": False
            },
            "relationships": {
                "workspace": {
                    "data": {
                        "type": "workspaces",
                        "id": f"workspaces/{workspace_id}"
                    }
                },
                "configuration-version": {
                    "data": {
                        "type": "configuration-versions",
                        "id": config_version_id
                    }
                }
            }
        }
    }

    # Trigger run in Terraform Cloud
    run_response = requests.post(
        f"{terraform_cloud_url}/runs",
        headers=headers,
        data=json.dumps(data)
    )

    if run_response.status_code != 201:
        print("Error triggering Terraform run:", run_response.status_code)
        print(run_response.json())
        return None

    run_data = run_response.json()
    return run_data["data"]["id"]

# Main execution flow
def run_terraform_provisioning():
    # Fetch workspace ID
    workspace_id = get_workspace_id(workspace_name)
    if not workspace_id:
        return

    # Set the environment variables for AWS credentials
    set_environment_variables(workspace_id)

    # Create Terraform configuration version in Terraform Cloud
    config_version_id = create_terraform_plan(terraform_script, workspace_id)
    if not config_version_id:
        return

    # Trigger the Terraform run to apply the changes
    run_id = trigger_terraform_run(config_version_id, workspace_id)
    if not run_id:
        return

    # Poll the run status (optional, but helpful to know when it's done)
    print(f"Terraform run started. Run ID: {run_id}")

# Execute the provisioning
run_terraform_provisioning()
