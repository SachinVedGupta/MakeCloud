import requests
import json
import time
from datetime import datetime

class TerraformCloudAutomator:
    def __init__(self, api_token, org_name, workspace_id, terraform_cloud_url, timeout=120):
        self.api_token = api_token
        self.org_name = org_name
        self.workspace_id = workspace_id
        self.base_url = terraform_cloud_url
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/vnd.api+json"
        }

    def delete_variable(self, var_id):
        """Delete an existing variable"""
        print(f"Deleting variable with ID: {var_id}")
        response = requests.delete(
            f"{self.base_url}/workspaces/{self.workspace_id}/vars/{var_id}",
            headers=self.headers
        )
        response.raise_for_status()

    def set_variables(self, aws_access_key, aws_secret_key):
        """Set AWS credentials as workspace variables with improved error handling"""
        print("Setting workspace variables...")
        variables = [
            {
                "key": "AWS_ACCESS_KEY",
                "value": aws_access_key,
                "category": "terraform",
                "sensitive": True
            },
            {
                "key": "AWS_SECRET_KEY",
                "value": aws_secret_key,
                "category": "terraform",
                "sensitive": True
            }
        ]

        # First, get all existing variables
        print("Checking existing variables...")
        response = requests.get(
            f"{self.base_url}/workspaces/{self.workspace_id}/vars",
            headers=self.headers
        )
        response.raise_for_status()
        existing_vars = response.json()

        # Create a mapping of variable names to their IDs
        var_id_map = {
            var['attributes']['key']: var['id']
            for var in existing_vars['data']
        }

        for var in variables:
            print(f"Processing variable: {var['key']}")
            
            # If variable exists, delete it first
            if var['key'] in var_id_map:
                print(f"Variable {var['key']} exists, deleting it first...")
                try:
                    self.delete_variable(var_id_map[var['key']])
                except Exception as e:
                    print(f"Warning: Failed to delete variable {var['key']}: {str(e)}")

            # Create new variable
            print(f"Creating new variable: {var['key']}")
            payload = {
                "data": {
                    "type": "vars",
                    "attributes": {
                        "key": var["key"],
                        "value": var["value"],
                        "category": var["category"],
                        "sensitive": var["sensitive"]
                    }
                }
            }
            
            try:
                response = requests.post(
                    f"{self.base_url}/workspaces/{self.workspace_id}/vars",
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                print(f"Successfully created variable: {var['key']}")
            except requests.exceptions.HTTPError as e:
                print(f"Error response content: {e.response.text}")
                raise

def main():
    print("Starting Terraform Cloud automation...")
    
    automator = TerraformCloudAutomator(
        api_token="dVlNetUaHyfjyA.atlasv1.6zuS64DM5pRk8lsYTkQdaV5ML04yGpeBj62QkyvgaiY0g5pImivGESHSmDsY4jck3t4",
        org_name="for_hack_uoft",
        workspace_id="ws-BCRCj4Vc54sT33SF",
        terraform_cloud_url="https://app.terraform.io/api/v2",
        timeout=120
    )

    try:
        # Set the AWS credentials as workspace variables with improved error handling
        automator.set_variables("AKIA2RP6H7VO5IJS57PT", "3gEFpZNFcyJEvZh/NqbMMsWfb9RrBimW0Z4AyO8q")
        print("Variables set successfully!")
        
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        print("Please check the Terraform Cloud console for more details")

if __name__ == "__main__":
    main()