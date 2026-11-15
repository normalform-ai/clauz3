data "aws_ami" "debian" {
  most_recent = true
  owners      = ["136693071363"] # Official Debian project

  filter {
    name   = "name"
    values = ["debian-12-amd64-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_Availability_zones" "available" {
  state = "available"
}

resource "random_password" "decision_token" {
  length  = 43
  special = false
}

resource "aws_security_group" "dot" {
  name_prefix = "${var.instance_name}-sg-"
  description = "Security group for single-tenant Clauz3 Dot personal assistant"

  ingress {
    description = "HTTPS workspace and live browser stream"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  ingress {
    description = "HTTP ACME challenge for Caddy automatic TLS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  egress {
    description = "Outbound internet access for LLM APIs and Chromium browsing"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = var.instance_name
  }
}

resource "aws_key_pair" "dot" {
  count      = var.ssh_public_key != "" ? 1 : 0
  key_name   = "${var.instance_name}-key"
  public_key = var.ssh_public_key
}

resource "aws_ebs_volume" "workspace" {
  availability_zone = data.aws_Availability_zones.available.names[0]
  size              = var.workspace_volume_size_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name = "${var.instance_name}-persistent-data"
  }
}

resource "aws_instance" "dot" {
  ami                         = data.aws_ami.debian.id
  instance_type               = var.instance_type
  availability_zone           = data.aws_Availability_zones.available.names[0]
  vpc_security_group_ids      = [aws_security_group.dot.id]
  key_name                    = var.ssh_public_key != "" ? aws_key_pair.dot[0].key_name : null
  associate_public_ip_address = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    domain_name       = var.domain_name
    decision_token    = random_password.decision_token.result
    anthropic_api_key = var.anthropic_api_key
    clauz3_repo_url   = var.clauz3_repo_url
    clauz3_git_ref    = var.clauz3_git_ref
  })

  tags = {
    Name = var.instance_name
  }
}

resource "aws_volume_attachment" "workspace" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.workspace.id
  instance_id = aws_instance.dot.id
}
