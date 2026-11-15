# Self-Hosted Clauz3 Dot on AWS (Terraform)

This Terraform module deploys a single-tenant **Clauz3 Dot** personal assistant in your own AWS account, modeled after OpenAI's ChatGPT "Dot" architecture:

- **Dedicated Linux VM (`aws_instance.dot`)** running `dot-workspace` and `dot-browser` in isolated containers.
- **Encrypted Persistent Storage (`aws_ebs_volume.workspace`)** preserving your project workspace, SQLite conversation history, remembered approval rules, and authenticated Chromium profile across instance replacements.
- **Isolated Cloud Browser with "Take Over" Mode** streamed via noVNC over HTTPS (`https://<domain>/browser/vnc.html`) directly inside the Clauz3 Workspace UI.
- **Automatic TLS & Reverse Proxy (Caddy)** terminating HTTPS for your domain and forwarding traffic to the local workspace and browser sidecar.

## Quick Start

```bash
cd deploy/terraform
terraform init
terraform apply \
  -var="domain_name=dot.example.com" \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY"
```

After provisioning completes, retrieve your private approver login link:

```bash
terraform output -raw approver_login_url
```
