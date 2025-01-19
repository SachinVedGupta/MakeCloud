import requests
import json
import time

class TerraformCloudAutomator:
    def __init__(self, api_token, org_name, workspace_id, terraform_cloud_url):
        self.api_token = api_token
        self.org_name = org_name
        self.workspace_id = workspace_id
        self.base_url = terraform_cloud_url
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/vnd.api+json"
        }

    def set_variables(self, aws_access_key, aws_secret_key):
        """Set AWS credentials as workspace variables"""
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
            response.raise_for_status()

    def update_configuration(self, terraform_script):
        """Update the Terraform configuration in the workspace"""
        # Create a new configuration version
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
        
        # Upload the configuration content
        with requests.put(
            upload_url,
            data=terraform_script.encode(),
            headers={"Content-Type": "application/octet-stream"}
        ) as response:
            response.raise_for_status()

        return cv_data["data"]["id"]

    def create_and_apply_run(self):
        """Create and apply a new run"""
        # Create a new run
        payload = {
            "data": {
                "type": "runs",
                "attributes": {
                    "is-destroy": False
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
        
        # Wait for plan to complete
        while True:
            response = requests.get(
                f"{self.base_url}/runs/{run_id}",
                headers=self.headers
            )
            response.raise_for_status()
            
            status = response.json()["data"]["attributes"]["status"]
            if status == "planned":
                break
            elif status in ["errored", "canceled", "force-canceled"]:
                raise Exception(f"Run failed with status: {status}")
            
            time.sleep(10)
        
        # Apply the run
        payload = {
            "comment": "Applied via API"
        }
        
        response = requests.post(
            f"{self.base_url}/runs/{run_id}/actions/apply",
            headers=self.headers,
            json=payload
        )
        response.raise_for_status()
        
        return run_id

    def monitor_run(self, run_id):
        """Monitor the status of a run"""
        while True:
            response = requests.get(
                f"{self.base_url}/runs/{run_id}",
                headers=self.headers
            )
            response.raise_for_status()
            
            status = response.json()["data"]["attributes"]["status"]
            if status in ["applied", "errored", "canceled", "force-canceled"]:
                return status
            
            time.sleep(10)

# Example usage
def main():
    # Initialize with your credentials and workspace information
    automator = TerraformCloudAutomator(
        api_token="dVlNetUaHyfjyA.atlasv1.6zuS64DM5pRk8lsYTkQdaV5ML04yGpeBj62QkyvgaiY0g5pImivGESHSmDsY4jck3t4",
        org_name="for_hack_uoft",
        workspace_id="ws-BCRCj4Vc54sT33SF",
        terraform_cloud_url=terraform_cloud_url
    )

    try:
        # Set the AWS credentials as workspace variables
        automator.set_variables(user_aws_access_key, user_aws_secret_key)
        
        # Update the configuration
        automator.update_configuration(terraform_script)
        
        # Create and apply the run
        run_id = automator.create_and_apply_run()
        
        # Monitor the run until completion
        final_status = automator.monitor_run(run_id)
        print(f"Run completed with status: {final_status}")
        
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()