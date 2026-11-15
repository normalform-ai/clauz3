output "instance_id" {
  description = "EC2 instance ID of the Clauz3 Dot"
  value       = aws_instance.dot.id
}

output "public_ip" {
  description = "Public IPv4 address of the Clauz3 Dot"
  value       = aws_instance.dot.public_ip
}

output "workspace_url" {
  description = "Public HTTPS workspace URL"
  value       = "https://${var.domain_name}/ui"
}

output "approver_login_url" {
  description = "Private approver login URL that mints the HttpOnly approver session cookie"
  value       = "https://${var.domain_name}/ui/login?key=${random_password.decision_token.result}"
  sensitive   = true
}
