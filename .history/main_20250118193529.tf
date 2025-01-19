provider "aws" {
  profile = "default"
  region = "us-east-1"
}

resource "aws_instance" "app_server" {
  ami = ""
  instance_type = "t2.micro"

  tags = {
    Name = "MyTerraformInstance
  }

}