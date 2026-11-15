# Clauz3 Cloud & Personal Assistant ("Dot") Architecture

Clauz3 supports running as an always-on personal assistant modeled after OpenAI's **ChatGPT "Dot"** architecture, while replacing Dot's probabilistic LLM Auto-Review Agent with **deterministic Z3 formal verification and cryptographically signed approval receipts**.

## Core Concepts: ChatGPT "Dot" vs. Clauz3 Dot

| Capability | OpenAI ChatGPT "Dot" | Clauz3 Dot |
| --- | --- | --- |
| **Dedicated Compute** | Per-instance cloud Linux VM with persistent filesystem | Persistent workspace container/VM (`dot-workspace`) |
| **Isolated Cloud Browser** | Separate Chromium container with live view & **"Take Over"** mode | `dot-browser` sidecar (Chromium + CDP + noVNC) embedded in the Workspace UI (`#browser-panel`) with **View-Only / Take Over** toggle |
| **Action Gating** | LLM-based Auto-Review Agent + custom natural-language rules | Z3 formal contract proofs (`@clauz3.guarantee`) + operator policy / remembered profile verification (`ProfileStore`) |
| **Privilege Separation** | Approval service isolated from agent sandbox | Signing key (`CLAUZ3_APPROVAL_SIGNING_KEY`) and approver token (`CLAUZ3_APPROVAL_DECISION_TOKEN`) stay in the approval service and never enter the agent environment |

## Three Deployment Modes

### 1. Local Sandboxed Docker ("Local Dot")

Run a sandboxed Clauz3 Dot on your own machine with persistent workspace, conversation history, and an isolated Chromium sidecar:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
docker compose up --build
```

- **`dot-workspace`** (`docker/Dockerfile`) runs `clauz3 ui` bound inside the container.
- **`dot-browser`** (`docker/Dockerfile.browser`) runs Chromium inside an isolated Xvfb display with a persistent profile volume and streams live video/input via noVNC to the Workspace UI's **Browser** side panel.

### 2. Self-Hosted Cloud via Terraform ("BYOC Dot")

Deploy a single-tenant Clauz3 Dot VM with an encrypted persistent EBS volume (`/var/lib/clauz3-dot`) and automatic HTTPS via Caddy using `deploy/terraform/`:

```bash
cd deploy/terraform
terraform init
terraform apply -var="domain_name=dot.example.com" -var="anthropic_api_key=$ANTHROPIC_API_KEY"
```

Caddy enforces HSTS and gates `/browser/*` (including `/browser/websockify`) via `forward_auth` against the workspace service's `/api/auth/verify` endpoint so that only an authenticated approver session (`clauz3_approver_session`) can view or interact with the Cloud Browser stream.

### 3. Hosted Multi-Tenant Service (`clauz3-cloud`)

For a hosted multi-tenant deployment (maintained in the private [`normalform-ai/clauz3-cloud`](https://github.com/normalform-ai/clauz3-cloud) repository), the control plane provides:

- **OAuth 2.0 / OIDC Authentication & Role-Based Access (`user` / `admin`):** Google and GitHub OAuth login (plus an offline local OAuth simulator for development), encrypted per-user OAuth token storage, and fleet administration for managing tenant instances.
- **Per-User Dot Orchestrator:** Provisions an isolated `dot-workspace` + `dot-browser` pair per authenticated user on loopback with unique cryptographic approval tokens (`CLAUZ3_DOT_DECISION_TOKEN`).
- **Single-Origin HTTP + WebSocket Reverse Proxy (No Subdomains Required):** Everything is served from a single domain (e.g., `https://cloud.example.com`), supporting both:
  - **ChatGPT Dot / JupyterHub style (`https://cloud.example.com/dots/<dot-id>`):** Verifies tenant ownership (`403 Forbidden` for any other user), renders a top Cloud status bar showing the Dot ID and owner, and embeds the user's `clauz3 ui` workspace and `/dots/<dot-id>/browser` sidecar on the same origin.
  - **`claude.ai/code` style (`https://cloud.example.com/ui`):** Routes `/ui`, `/api/{chat,workspace,workspaces,profile,requests}*`, `/dots/<dot-id>/browser`, and the `/api/chat/acp` WebSocket directly to the signed-in user's isolated Dot by exchanging their `decision_token` for their `clauz3_approver_session` cookie behind the scenes.
