provider "aws" {
  profile = "default"
  region = "us-east-1"
}

resource "aws_instance" "app_server" {
  ami = "ami-097705bd69072a34d"
  instance_type = "t2.micro"

  tags = {
    Name = "MyTerraformInstance"
  }

}