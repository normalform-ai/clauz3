"""Optional real-browser check for the unified workspace.

Uses a deterministic ACP fixture and the real MCP/prover/approval flow. No model
credentials or API calls. Install Chromium with `playwright install chromium`.
"""

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
EMAIL = ROOT / "examples/email"


def serve(port: int, directory: str, missing_session: bool = False) -> None:
    import os

    import uvicorn

    from clauz3.approvals.authority import ApprovedLayer
    from clauz3.approvals.client import APPROVAL_SERVICE_ENV, APPROVAL_TOKEN_ENV
    from clauz3.approvals.profiles.store import ProfileStore
    from clauz3.approvals.receipts import (
        SIGNING_KEY_ENV,
        VERIFY_KEY_ENV,
        generate_keypair,
    )
    from clauz3.approvals.service import create_approval_app
    from clauz3.sessions.chat import ChatConfig

    workspace = Path(directory) / "email"
    (workspace / "cases").mkdir(parents=True, exist_ok=True)
    (workspace / ".claude").mkdir(exist_ok=True)
    (workspace / "cases/only_bob_pass.py").write_text(
        (EMAIL / "cases/only_bob_pass.py").read_text()
    )
    (workspace / "preview.html").write_text(
        "<script>window.fileExecuted = true</script>"
    )
    shutil.copytree(EMAIL / "tools", workspace / "tools", dirs_exist_ok=True)
    (workspace / "binary.dat").write_bytes(b"\x00\xff")
    signing, verifying = generate_keypair()
    os.environ[SIGNING_KEY_ENV] = signing
    config = ChatConfig(
        command=[
            sys.executable,
            str(ROOT / "tests/fixtures/workspace_agent.py"),
            *(["--missing-session"] if missing_session else []),
        ],
        cwd=str(workspace),
        bridge=[
            sys.executable,
            "-P",
            "-m",
            "clauz3.sessions.tools",
            str(workspace),
            str(workspace / "tools/email/trusted"),
        ],
        runner_env={
            APPROVAL_SERVICE_ENV: f"http://127.0.0.1:{port}",
            APPROVAL_TOKEN_ENV: "fixture-submit",
            VERIFY_KEY_ENV: verifying,
        },
        status_file=str(Path(directory) / "runs.json"),
        libraries=["email"],
        manage_libraries=True,
    )
    app = create_approval_app(
        auth_token="fixture-submit",
        decision_token="fixture-decide",
        chat_config=config,
        profile=ProfileStore(Path(directory) / "profile.sqlite", project=workspace),
        allowed_hosts=["127.0.0.1"],
        authorized_layers=(
            ApprovedLayer(
                trusted_roots=(EMAIL / "tools/email/trusted",), import_roots=(EMAIL,)
            ),
        ),
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def check() -> None:
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory(prefix="clauz3-browser-") as directory:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), __file__, "--serve", str(port), directory]
        )
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(150):
                try:
                    urlopen(url + "/health", timeout=0.2).close()
                    break
                except (URLError, OSError):
                    if process.poll() is not None:
                        raise RuntimeError("Fixture service failed") from None
                    time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1380, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url + "/ui/login?key=fixture-decide")
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                page.screenshot(
                    path="/tmp/clauz3-workspace-welcome.png", full_page=True
                )
                page.locator("#files-toggle").click()
                expect(page.locator("#files-panel")).to_be_visible()
                expect(
                    page.locator(".file-name").filter(has_text=".claude")
                ).to_have_count(0)
                page.locator("#files-hidden").check()
                expect(
                    page.locator(".file-name").filter(has_text=".claude")
                ).to_have_count(1)
                page.locator(".file-entry").filter(has_text="cases").click()
                page.locator(".file-entry").filter(has_text="only_bob_pass.py").click()
                expect(page.locator("#file-content")).to_contain_text(
                    "@clauz3.guarantee"
                )
                page.screenshot(path="/tmp/clauz3-workspace-files.png", full_page=True)
                page.locator("#prompt").fill("Please inspect")
                page.locator("#file-reference").click()
                expect(page.locator("#prompt")).to_have_value(
                    'Please inspect "cases/only_bob_pass.py"'
                )
                page.locator("#files-refresh").click()
                expect(page.locator("#file-content")).to_contain_text(
                    "@clauz3.guarantee"
                )
                page.get_by_role("button", name="Workspace", exact=True).click()
                page.locator(".file-entry").filter(has_text="preview.html").click()
                expect(page.locator("#file-content")).to_have_text(
                    "<script>window.fileExecuted = true</script>"
                )
                assert page.locator("#file-content script").count() == 0
                assert page.evaluate("window.fileExecuted") is None
                (Path(directory) / "email/preview.html").write_text("Updated on disk")
                page.locator("#files-refresh").click()
                expect(page.locator("#file-content")).to_have_text("Updated on disk")
                (Path(directory) / "email/preview.html").unlink()
                page.locator("#files-refresh").click()
                expect(page.locator("#files-status")).to_contain_text(
                    "no longer exists"
                )
                expect(page.locator("#file-content")).to_be_hidden()
                page.get_by_role("button", name="Workspace", exact=True).click()
                page.locator(".file-entry").filter(has_text="binary.dat").click()
                expect(page.locator("#file-preview-note")).to_contain_text(
                    "Preview unavailable"
                )
                page.locator("#files-close").click()
                expect(page.locator("#files-toggle")).to_be_focused()
                page.reload()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                expect(page.locator("#prompt")).to_have_value(
                    'Please inspect "cases/only_bob_pass.py"'
                )
                page.locator("#prompt").fill("hello")
                page.locator("#send").click()
                expect(page.locator(".assistant-message")).to_contain_text(
                    "Hello from the fixture"
                )
                assert page.locator(".assistant-message script").count() == 0
                expect(page.locator("#send")).to_be_enabled()
                page.locator("#prompt").fill("Keep this unsent draft")
                page.reload()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                expect(page.locator("#prompt")).to_have_value("Keep this unsent draft")
                expect(page.locator(".user-message")).to_have_count(1)
                page.locator("#prompt").fill(
                    "run:" + str(EMAIL / "cases/only_bob_pass.py")
                )
                page.locator("#send").click()
                approve = page.get_by_role("button", name="Approve", exact=True)
                try:
                    expect(approve).to_be_visible(timeout=30000)
                except AssertionError:
                    print(page.locator(".tool pre").all_text_contents())
                    raise
                expect(
                    page.locator(".request-card .mock-declarations")
                ).to_contain_text("Includes simulated tools")
                page.set_viewport_size({"width": 1380, "height": 1800})
                page.locator(".request-card").screenshot(
                    path="/tmp/clauz3-mock-declarations.png"
                )
                page.set_viewport_size({"width": 1380, "height": 1000})
                page.locator(".request-card textarea").fill("A review note")
                page.locator(".request-card summary").filter(
                    has_text="Show source"
                ).click()
                page.screenshot(
                    path="/tmp/clauz3-workspace-approval.png", full_page=True
                )
                page.reload()
                expect(approve).to_be_visible(timeout=20000)
                approve.click()
                expect(page.locator(".phase")).to_have_text("Completed", timeout=20000)
                expect(page.locator("#send")).to_be_enabled()
                result = page.request.get(url + "/api/chat/transcript").json()
                assert "receipt=clz2." in json.dumps(result)
                page.locator("#prompt").fill("Draft for the first conversation")
                page.get_by_role("link", name="≡ Approval profile & history").click()
                expect(page.locator("#approvals-view")).to_be_visible()
                page.get_by_role("tab", name="Decision history").click()
                expect(page.locator(".profile-card")).to_have_count(1)
                expect(page.locator(".profile-card h3")).to_have_text("Approved")
                page.get_by_role("button", name="Include in profile").click()
                expect(page.locator("#rules-count")).to_have_text("1")
                page.get_by_role("tab", name="Remembered rules").click()
                expect(page.locator(".profile-card h3")).to_have_text(
                    "Approve matching contracts"
                )
                page.screenshot(
                    path="/tmp/clauz3-workspace-profile.png", full_page=True
                )
                page.get_by_role("button", name="Disable rule").click()
                expect(page.locator("#rules-count")).to_have_text("0")
                page.locator("#profile-search").fill("does not exist")
                expect(page.locator(".profile-empty")).to_contain_text(
                    "No matching decisions"
                )
                page.locator("#profile-search").fill("")
                page.get_by_role("link", name="◉ Conversation").click()
                expect(page.locator("#prompt")).to_have_value(
                    "Draft for the first conversation"
                )
                page.locator("#new").click()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                expect(page.locator("#welcome")).to_be_visible()
                page.locator("#prompt").fill("Second conversation")
                page.locator("#send").click()
                expect(page.locator("#send")).to_be_enabled()
                page.locator(".conversation-link").filter(has_text="hello").click()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                expect(page.locator(".user-message")).to_have_count(2)
                expect(page.locator("#prompt")).to_have_value(
                    "Draft for the first conversation"
                )
                expect(page.locator(".phase")).to_have_text("Completed")
                page.get_by_role("button", name="Rename hello", exact=True).click()
                page.get_by_role("textbox", name="Conversation title").fill(
                    "Email planning"
                )
                page.get_by_role("button", name="Save", exact=True).click()
                expect(
                    page.locator(".conversation-link").filter(has_text="Email planning")
                ).to_have_count(1)
                page.locator("#history-search").fill("Email")
                expect(page.locator(".conversation-link")).to_have_count(1)
                page.locator("#history-search").fill("")
                page.locator("#prompt").fill("Continue this task")
                page.locator("#send").click()
                expect(page.locator("#send")).to_be_enabled()
                expect(page.locator(".user-message")).to_have_count(3)
                page.screenshot(
                    path="/tmp/clauz3-workspace-history.png", full_page=True
                )
                page.locator("#new").click()
                expect(page.locator("#welcome")).to_be_visible()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                page.locator("#prompt").fill("markdown")
                page.locator("#send").click()
                answer = page.locator(".assistant-message").last
                expect(answer.locator("strong")).to_have_text("both forms:")
                expect(answer.locator("li > p > code")).to_have_text("a")
                expect(answer.locator("li > pre")).to_have_text("x <b>y</b>")
                assert answer.locator("b").count() == 0
                expect(page.locator("#send")).to_be_enabled()
                page.screenshot(path="/tmp/clauz3-workspace-markdown.png")
                page.locator("#prompt").fill("wait")
                page.locator("#send").click()
                expect(page.locator("#send")).to_be_disabled()
                page.locator("#stop").click()
                expect(page.locator("#status")).to_contain_text("Conversation stopped")
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path="/tmp/clauz3-workspace-mobile.png", full_page=True)
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                page.locator("#files-toggle").click()
                expect(page.locator("#files-panel")).to_be_visible()
                page.locator(".file-entry").filter(has_text="cases").click()
                page.locator(".file-entry").filter(has_text="only_bob_pass.py").click()
                expect(page.locator("#file-content")).to_contain_text(
                    "@clauz3.guarantee"
                )
                page.screenshot(
                    path="/tmp/clauz3-workspace-files-mobile.png", full_page=True
                )
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                page.locator("#files-close").press("Escape")
                expect(page.locator("#files-panel")).to_be_hidden()
                page.get_by_role("link", name="≡ Approval profile & history").click()
                expect(page.locator("#approvals-view")).to_be_visible()
                page.screenshot(
                    path="/tmp/clauz3-workspace-profile-mobile.png", full_page=True
                )
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                page.reload()
                expect(page.locator("#approvals-view")).to_be_visible()
                expect(page.locator("#rules-count")).to_have_text("0")
                process.terminate()
                process.wait(timeout=10)
                process = subprocess.Popen(
                    [
                        str(ROOT / ".venv/bin/python"),
                        __file__,
                        "--serve",
                        str(port),
                        directory,
                    ]
                )
                for _ in range(150):
                    try:
                        urlopen(url + "/health", timeout=0.2).close()
                        break
                    except (URLError, OSError):
                        if process.poll() is not None:
                            raise RuntimeError("Restarted service failed") from None
                        time.sleep(0.1)
                page.goto(url + "/ui/login?key=fixture-decide")
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                page.locator(".conversation-link").filter(
                    has_text="Email planning"
                ).click()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                expect(page.locator(".user-message")).to_have_count(3)
                expect(page.locator(".request-card")).to_contain_text(
                    "Saved approval request"
                )
                expect(page.locator(".request-card")).to_contain_text("bob@example.com")
                page.get_by_role("link", name="▦ Libraries", exact=True).click()
                expect(page.locator("#libraries-view")).to_be_visible()
                page.locator("#library-search").fill("stdlib:find")
                page.get_by_role(
                    "button", name="Review install stdlib:find", exact=True
                ).click()
                expect(page.locator("#library-review-apply")).to_be_enabled(
                    timeout=20000
                )
                expect(page.locator("#library-review-content")).to_contain_text(
                    "filesystem"
                )
                page.get_by_role("button", name="Install library", exact=True).click()
                expect(page.locator("#library-review")).not_to_be_visible()
                expect(page.locator("#libraries-status")).to_contain_text("installed")
                page.locator("#library-search").fill("")
                expect(page.locator("#library-list")).not_to_contain_text(
                    "No mock declaration"
                )
                expect(page.locator("#library-list")).to_contain_text("Simulated")
                # The toggle sets the default for new conversations only.
                page.get_by_role("switch", name="Use find in new conversations").check()
                expect(page.locator("#libraries-status")).to_contain_text(
                    "email, filesystem, find"
                )
                expect(
                    page.get_by_role(
                        "switch", name="Use filesystem in new conversations"
                    )
                ).to_be_checked()
                assert page.request.get(url + "/api/chat/transcript").json()[
                    "libraries"
                ] == ["email"]
                page.locator("#library-filter-installed").click()
                expect(page.locator(".library-card.not-installed")).to_have_count(0)
                page.set_viewport_size({"width": 1380, "height": 1000})
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path="/tmp/clauz3-workspace-libraries.png", full_page=True
                )
                page.locator("#library-filter-all").click()
                expect(
                    page.locator(".library-card.not-installed").first
                ).to_be_visible()

                # Adding a library to a started conversation asks first.
                page.get_by_role("link", name="◉ Conversation").click()
                expect(page.locator("#libraries")).to_have_text("email")
                page.locator("#library-menu-toggle").click()
                page.locator("#library-menu-list label").filter(
                    has_text="find"
                ).locator("input").check()
                page.locator("#library-menu-apply").click()
                expect(page.locator("#library-confirm-title")).to_have_text(
                    "Add filesystem, find to this conversation?"
                )
                page.screenshot(path="/tmp/clauz3-workspace-library-confirm.png")
                page.get_by_role("button", name="Add and continue").click()
                expect(page.locator("#library-menu")).to_be_hidden(timeout=20000)
                expect(page.locator(".library-marker")).to_contain_text(
                    "+ find · automatic approvals paused"
                )
                assert page.evaluate(
                    "document.querySelector('#conversation .library-marker') !== null"
                )
                expect(page.locator("#approvals-paused")).to_be_visible()
                expect(page.locator("#libraries li")).to_have_count(3)
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                page.screenshot(
                    path="/tmp/clauz3-workspace-library-added.png", full_page=True
                )

                page.locator("#new").click()
                expect(page.locator("#welcome")).to_be_visible()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                expect(page.locator("#approvals-paused")).to_be_hidden()
                assert page.request.get(url + "/api/chat/transcript").json()[
                    "libraries"
                ] == ["email", "filesystem", "find"]
                page.locator("#prompt").fill(
                    "run:" + str(EMAIL / "cases/only_bob_pass.py")
                )
                page.locator("#send").click()
                expect(approve).to_be_visible(timeout=30000)
                approve.click()
                expect(page.locator(".phase")).to_have_text("Completed", timeout=20000)
                expect(page.locator("#send")).to_be_enabled()
                page.get_by_role("link", name="▦ Libraries", exact=True).click()
                # Identical-source request IDs recur: each fresh submission
                # must have working controls after both approval and rejection.
                page.get_by_role("link", name="◉ Conversation", exact=True).click()
                for decision, phase in (
                    ("rejected", "Did not execute"),
                    ("approved_once", "Completed"),
                ):
                    page.locator("#prompt").fill(
                        "run:" + str(EMAIL / "cases/only_bob_pass.py")
                    )
                    page.locator("#send").click()
                    button = page.locator(
                        f'.request-card [data-decision="{decision}"]'
                        ":not([data-remember]):enabled"
                    )
                    expect(button).to_have_count(1, timeout=30000)
                    button.click()
                    expect(page.locator(".phase").last).to_have_text(
                        phase, timeout=20000
                    )
                    expect(page.locator("#send")).to_be_enabled()
                page.get_by_role("link", name="▦ Libraries", exact=True).click()
                page.get_by_role(
                    "button", name="Review update stdlib:find", exact=True
                ).click()
                expect(page.locator("#library-review-status")).to_contain_text(
                    "Stop the current conversation"
                )
                expect(page.locator("#library-review-apply")).to_be_disabled()
                page.get_by_role("button", name="Cancel", exact=True).click()
                page.get_by_role("link", name="◉ Conversation", exact=True).click()
                page.locator("#stop").click()
                expect(page.locator("#status")).to_contain_text("Conversation stopped")
                page.get_by_role("link", name="▦ Libraries", exact=True).click()
                page.get_by_role("button", name="Remove find", exact=True).click()
                expect(page.locator("#library-review-apply")).to_be_enabled()
                page.get_by_role("button", name="Remove library", exact=True).click()
                expect(
                    page.get_by_role("switch", name="Use find in new conversations")
                ).to_have_count(0)
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#library-search").fill("")
                page.screenshot(
                    path="/tmp/clauz3-workspace-libraries-mobile.png", full_page=False
                )
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                # Reproduce an adapter that has lost a saved session after restart.
                page.locator("#new").click()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                page.locator("#prompt").fill("Keep this transcript after restart")
                page.locator("#send").click()
                expect(page.locator("#status")).to_have_text("Ready", timeout=20000)
                old_id = page.request.get(url + "/api/chat/transcript").json()[
                    "conversationId"
                ]
                process.terminate()
                process.wait(timeout=10)
                process = subprocess.Popen(
                    [
                        str(ROOT / ".venv/bin/python"),
                        __file__,
                        "--serve",
                        str(port),
                        "--missing-session",
                        directory,
                    ]
                )
                for _ in range(150):
                    try:
                        urlopen(url + "/health", timeout=0.2).close()
                        break
                    except (URLError, OSError):
                        if process.poll() is not None:
                            raise RuntimeError("Restarted service failed") from None
                        time.sleep(0.1)
                page.goto(url + "/ui/login?key=fixture-decide")
                expect(page.locator("#notice")).to_contain_text(
                    "Your transcript is saved", timeout=20000
                )
                expect(page.locator("#notice")).not_to_contain_text(
                    "Resource not found"
                )
                expect(page.locator("#resume-detail")).not_to_be_visible()
                expect(page.locator(".user-message")).to_contain_text(
                    "Keep this transcript"
                )
                page.set_viewport_size({"width": 1380, "height": 1000})
                page.screenshot(
                    path="/tmp/clauz3-workspace-resume-recovery.png", full_page=True
                )
                page.locator("#resume-new").click()
                expect(page.locator("#send")).to_be_enabled(timeout=20000)
                expect(page.locator("#notice")).not_to_be_visible()
                expect(page.locator("#resume-recovery")).not_to_be_visible()
                assert (
                    page.request.get(url + "/api/chat/transcript").json()[
                        "conversationId"
                    ]
                    != old_id
                )
                assert not errors, errors
                browser.close()
            print("Browser checks passed; screenshots: /tmp/clauz3-workspace-*.png")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int)
    parser.add_argument("directory", nargs="?")
    parser.add_argument("--missing-session", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.directory, args.missing_session)
    else:
        check()
