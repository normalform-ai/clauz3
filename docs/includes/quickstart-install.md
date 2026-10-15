These guides use the current repository checkout, including the browser
workspace. You need [uv](https://docs.astral.sh/uv/getting-started/installation/),
Python 3.11 or later, and an installed, authenticated Claude Code on macOS or
Linux.

```bash
git clone https://github.com/normalform-ai/clauz3.git
cd clauz3
uv tool install --editable .
claude auth login
```

An editable installation runs the code in this checkout. Keep the directory;
`git pull` updates the code you run. If you already installed another copy of
ClauZ3, use `uv tool install --force --editable .` to switch to this checkout.
If your shell cannot find `clauz3`, run `uv tool update-shell` and open a new
terminal.

For the browser workspace, also install the reviewed Claude ACP adapter
(requires Node.js 22 or later):

```bash
npm install -g @agentclientprotocol/claude-agent-acp@0.89.1
```

The terminal alternative at the end of each guide does not need this adapter.
