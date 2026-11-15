variable "aws_region" {
  description = "AWS region for the Clauz3 Dot instance"
  type        = string
  default     = "us-west-2"
}

variable "instance_name" {
  description = "Name tag for the Clauz3 Dot instance and volumes"
  type        = string
  default     = "clauz3-dot"
}

variable "instance_type" {
  description = "EC2 instance type (at least 4 GB RAM recommended for workspace + Chromium)"
  type        = string
  default     = "t3.medium"
}

variable "workspace_volume_size_gb" {
  description = "Size (GiB) of the encrypted persistent EBS volume for workspace, SQLite history, and browser profile"
  type        = number
  default     = 30
}

variable "domain_name" {
  description = "Public or Tailscale domain name for HTTPS via Caddy (e.g., dot.example.com)"
  type        = string
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks permitted to reach HTTPS (443) on the Dot instance"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ssh_public_key" {
  description = "Optional SSH public key for administrative access"
  type        = string
  default     = ""
}

variable "anthropic_api_key" {
  description = "Anthropic API key for Claude agent"
  type        = string
  sensitive   = true
  default     = ""
}

variable "clauz3_repo_url" {
  description = "Git repository URL to build the Clauz3 Dot stack from"
  type        = string
  default     = "https://github.com/normalform-ai/clauz3.git"
}

variable "clauz3_git_ref" {
  description = "Git branch or tag to check out"
  type        = string
  default     = "main"
}
