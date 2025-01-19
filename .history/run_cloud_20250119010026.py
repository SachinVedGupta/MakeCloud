import subprocess


def string_to_tf_file(terraform_string):
    
    with open("terraform_code.tf", "w") as file:
        file.write(terraform_string)
    

def run_terraform(terraform_string):
    string_to_tf_file(terraform_string)
    terraform_dir = "./"

    try:
        # Initialize Terraform
        subprocess.run(["terraform", "init"], cwd=terraform_dir, check=True)
        
        # Plan Terraform execution
        subprocess.run(["terraform", "plan", "-out=tfplan"], cwd=terraform_dir, check=True)

        # Apply Terraform script
        subprocess.run(["terraform", "apply", "-auto-approve"], cwd=terraform_dir, check=True)

        print("Terraform script executed successfully.")

    except subprocess.CalledProcessError as e:
        print(f"Error executing Terraform: {e}")

# Run Terraform
# run_terraform()

the_string_gives_error = """
Line 1
  Line 2
    Line 3
"""

valid_aws_provision_string = """
provider "aws" {
  profile = "default"
  region = "us-east-1"
}

resource "aws_instance" "app_server" {
  ami = "ami-097705bd69072a34d"
  instance_type = "t2.micro"

  tags = {
    Name = "NEWMyTerraformInstance"
  }

}
"""


run_terraform(valid_aws_provision_string)

#put a function call at the seccond endpoint