import requests
import json
import time
from datetime import datetime

class TerraformCloudAutomator:
    def __init__(self, api_token, org_name, workspace_id, terraform_cloud_url, timeout=120):  # 2 minute timeout
        self.api_token = api_token
        self.org_name = org_name
        self.workspace_id = workspace_id
        self.base_url = terraform_cloud_url
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/vnd.api+json"
        }

    def set_variables(self, aws_access_key, aws_secret_key):
        """Set AWS credentials as workspace variables"""
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

        for var in variables:
            print(f"Setting variable: {var['key']}")
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
            
            response = requests.post(
                f"{self.base_url}/workspaces/{self.workspace_id}/vars",
                headers=self.headers,
                json=payload
            )
            
            if response.status_code == 422:  # Variable already exists
                print(f"Variable {var['key']} already exists, updating...")
                existing_vars = requests.get(
                    f"{self.base_url}/workspaces/{self.workspace_id}/vars",
                    headers=self.headers
                ).json()
                
                for existing_var in existing_vars['data']:
                    if existing_var['attributes']['key'] == var['key']:
                        var_id = existing_var['id']
                        response = requests.patch(
                            f"{self.base_url}/workspaces/{self.workspace_id}/vars/{var_id}",
                            headers=self.headers,
                            json=payload
                        )
                        response.raise_for_status()
            else:
                response.raise_for_status()

    def update_configuration(self, terraform_script):
        """Update the Terraform configuration in the workspace"""
        print("Updating Terraform configuration...")
        payload = {
            "data": {
                "type": "configuration-versions",
                "attributes": {
                    "auto-queue-runs": False
                }
            }
        }

        response = requests.post(
            f"{self.base_url}/workspaces/{self.workspace_id}/configuration-versions",
            headers=self.headers,
            json=payload
        )
        response.raise_for_status()
        
        cv_data = response.json()
        upload_url = cv_data["data"]["attributes"]["upload-url"]
        
        print("Uploading Terraform configuration...")
        with requests.put(
            upload_url,
            data=terraform_script.encode(),
            headers={"Content-Type": "application/octet-stream"}
        ) as response:
            response.raise_for_status()

        return cv_data["data"]["id"]

    def create_and_apply_run(self):
        """Create and apply a new run"""
        print("Creating new run...")
        payload = {
            "data": {
                "type": "runs",
                "attributes": {
                    "is-destroy": False,
                    "auto-apply": True
                },
                "relationships": {
                    "workspace": {
                        "data": {
                            "type": "workspaces",
                            "id": self.workspace_id
                        }
                    }
                }
            }
        }

        response = requests.post(
            f"{self.base_url}/runs",
            headers=self.headers,
            json=payload
        )
        response.raise_for_status()
        
        run_id = response.json()["data"]["id"]
        print(f"Run created with ID: {run_id}")
        return run_id

    def monitor_run(self, run_id):
        """Monitor the status of a run with shorter timeout"""
        print("Monitoring run status...")
        start_time = time.time()
        last_status = None
        
        while True:
            if time.time() - start_time > self.timeout:
                raise TimeoutError(f"Run timed out after {self.timeout} seconds")
            
            response = requests.get(
                f"{self.base_url}/runs/{run_id}",
                headers=self.headers
            )
            response.raise_for_status()
            
            run_data = response.json()["data"]["attributes"]
            current_status = run_data["status"]
            
            if current_status != last_status:
                print(f"Current status: {current_status} at {datetime.now().strftime('%H:%M:%S')}")
                last_status = current_status
            
            if current_status in ["applied", "errored", "canceled", "force-canceled"]:
                return current_status
            
            time.sleep(5)  # Reduced sleep time for more frequent status checks

def main():
    print("Starting Terraform Cloud automation...")
    
    automator = TerraformCloudAutomator(
        api_token="dVlNetUaHyfjyA.atlasv1.6zuS64DM5pRk8lsYTkQdaV5ML04yGpeBj62QkyvgaiY0g5pImivGESHSmDsY4jck3t4",
        org_name="for_hack_uoft",
        workspace_id="ws-BCRCj4Vc54sT33SF",
        terraform_cloud_url="https://app.terraform.io/api/v2",
        timeout=120  # 2 minute timeout
    )

    try:
        # Set the AWS credentials as workspace variables
        automator.set_variables("AKIA2RP6H7VO5IJS57PT", "3gEFpZNFcyJEvZh/NqbMMsWfb9RrBimW0Z4AyO8q")
        
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
        
        # Update the configuration
        automator.update_configuration(terraform_script)
        
        # Create and apply the run
        run_id = automator.create_and_apply_run()
        
        # Monitor the run until completion
        final_status = automator.monitor_run(run_id)
        print(f"Run completed with status: {final_status}")
        
    except TimeoutError as e:
        print(f"Timeout error: {str(e)}")
        print("Please check the Terraform Cloud console for more details")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        print("Please check the Terraform Cloud console for more details")

if __name__ == "__main__":
    main()