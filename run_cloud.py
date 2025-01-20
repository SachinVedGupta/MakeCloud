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
  ami = "ami-0cxzc"
  instance_type = "t2.micro"

  tags = {
    Name = "NEWMyTerraformInstance"
  }

}
"""

new = """
provider "google" {
  project     = "gassxax"
  region      = "us-west1"
  access_token = "5xczc"
}

resource "google_compute_instance" "try_this_gcp" {
  name         = "try-this-gcp"
  machine_type = "e2-micro"
  zone         = "us-west1-a"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"  # Change this if needed
    }
  }

  network_interface {
    network = "default"
    access_config {}  # Assigns an external IP
  }
}
"""

# run_terraform(new)


# run_terraform(valid_aws_provision_string)

#put a function call at the end of the seccond endpoint