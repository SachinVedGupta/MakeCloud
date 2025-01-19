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

new_aws_trial = """
provider "aws" {
  region = "us-east-1"
  access_key = "YOUR_ACCESS_KEY"
  secret_key = "YOUR_SECRET_KEY"
}

EC2 instance resource
resource "aws_instance" "example" {
  ami           = "ami-097705bd69072a34d"
  instance_type = "t2.micro"

  root_block_device {
    volume_type = "gp2"
    volume_size = 8
  }

  vpc_security_group_ids = ["${aws_security_group.example.id}"]
  subnet_id             = "${aws_subnet.example.id}"

  key_name = "the_pair"
}

resource "aws_vpc" "example" {
  cidr_block = "10.0.0.0/16"
  tags = {
    Name = "example-vpc"
  }
}

resource "aws_subnet" "example" {
  vpc_id     = "${aws_vpc.example.id}"
  cidr_block = "10.0.0.0/24"
  tags = {
    Name = "example-subnet"
  }
}

resource "aws_security_group" "example" {
  name        = "example-security-group"
  description = "Allow all inbound traffic"


  ingress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "example-security-group"
  }
}
"""

run_terraform(valid_aws_provision_string)