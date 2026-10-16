"""Shared approval workspace component, usable with or without an agent chat."""

from pathlib import Path

WEB = Path(__file__).parent / "web"


def profile_component() -> str:
    return (WEB / "profile.html").read_text()


def profile_page() -> str:
    component = profile_component().replace(
        'id="approvals-view" hidden', 'id="approvals-view"'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<meta name="referrer" content="no-referrer" /><title>Clauz3 · Approvals</title>
<link rel="icon" href="/ui/assets/favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="/ui/assets/workspace.css" />
<link rel="stylesheet" href="/ui/approval-assets/profile.css" /></head>
<body class="profile-standalone"><aside class="sidebar">
<a class="brand" href="/ui"><img src="/ui/assets/logo.svg" alt="ClauZ3" /></a>
<div class="eyebrow">WORKSPACE</div><h2>Approvals</h2>
<nav><a href="/ui">Live requests</a><a class="selected" href="/ui/profile">
Approval profile &amp; history</a></nav>
<div class="sidebar-bottom">Proof before execution</div></aside>
<main>{component}</main>
<script src="/ui/approval-assets/guarantees.js" defer></script>
<script src="/ui/approval-assets/profile.js" defer></script>
</body></html>"""
