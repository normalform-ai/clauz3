"""Optional browser regression for saved contract descriptions.

Run this script with ``uv run --with playwright python`` after installing
Chromium with ``playwright install chromium``.
"""

from urllib.parse import urlparse

from playwright.sync_api import Route, expect, sync_playwright

from clauz3.approvals.service import _render_card
from clauz3.approvals.workspace import WEB, profile_page


def check() -> None:
    expression = 'rules.only_read_under("<img src=x onerror=alert(1)>")'
    sentence = 'Every read path must start with "<img src=x onerror=alert(1)>".'
    documentation = 'Trusted documentation.\n\n<script>alert("doc")</script>'
    events = [
        {
            "id": "described",
            "created": 1,
            "decision": "approved_once",
            "record": {
                "guarantees": [expression],
                "contract_descriptions": {expression: {"text": sentence}},
                "contract_documentation": {expression: documentation},
            },
        },
        {
            "id": "legacy",
            "created": 2,
            "decision": "approved_once",
            "record": {"guarantees": ["rules.legacy()"]},
        },
        {
            "id": "fallback",
            "created": 3,
            "decision": "approved_once",
            "record": {
                "guarantees": ["rules.fallback()"],
                "contract_descriptions": {},
                "contract_documentation": {"rules.fallback()": "Fallback docs."},
            },
        },
    ]
    snapshot = {
        "history": events,
        "rules": [
            {
                "id": "rule",
                "history_id": "described",
                "enabled": True,
                "decision": "allow",
            }
        ],
    }

    def respond(route: Route) -> None:
        path = urlparse(route.request.url).path
        if path == "/api/profile":
            route.fulfill(json=snapshot)
        elif path == "/ui/profile":
            route.fulfill(content_type="text/html", body=profile_page())
        elif path == "/ui/review":
            record = {
                "request_id": "preview",
                "target": "main",
                "guarantees": [
                    "rules.read_only()",
                    "rules.only_read_under('ontology/go-edit.obo')",
                ],
                "contract_descriptions": {},
                "contract_documentation": {
                    "rules.read_only()": (
                        "Guarantee ``write_file`` is never called.\n\n"
                        "This layer's write effect, and only that one. "
                        "A program can still write through another layer."
                    ),
                    "rules.only_read_under('ontology/go-edit.obo')": (
                        "Guarantee every ``read_file``/``read_lines`` path is "
                        "under ``root``.\n\nA read through another layer is "
                        "that layer's to state."
                    ),
                },
            }
            route.fulfill(
                content_type="text/html",
                body=(
                    '<!doctype html><meta name="viewport" '
                    'content="width=device-width,initial-scale=1">'
                    '<link rel="stylesheet" href="/ui/assets/workspace.css">'
                    '<link rel="stylesheet" href="/ui/approval-assets/profile.css">'
                    '<main style="margin:0;max-width:800px"><h1>Action review</h1>'
                    + _render_card(record, single=True)
                    + "</main>"
                ),
            )
        elif path == "/ui/assets/workspace.css":
            route.fulfill(
                content_type="text/css",
                body=(WEB.parent.parent / "sessions/web/workspace.css").read_text(),
            )
        elif path.startswith("/ui/approval-assets/"):
            name = path.rsplit("/", 1)[1]
            assert name in {"guarantees.js", "profile.js", "profile.css"}
            route.fulfill(
                content_type="text/css" if name.endswith(".css") else "text/javascript",
                body=(WEB / name).read_text(encoding="utf-8"),
            )
        else:
            route.fulfill(content_type="text/css", body="")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def dismiss_dialog(dialog):
            errors.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", dismiss_dialog)
        page.route("**/*", respond)
        page.goto("http://contract-fixture.test/ui/profile")
        expect(page.locator(".profile-card")).to_have_count(1)
        expect(page.locator(".contract-description")).to_have_text(sentence)
        expect(page.locator(".contract-documentation")).not_to_be_visible()
        expect(page.locator(".profile-guarantees code")).not_to_be_visible()
        page.locator(".contract-details summary").click()
        expect(page.locator(".contract-documentation")).to_be_visible()
        expect(page.locator(".contract-documentation")).to_have_text(documentation)
        expect(page.locator(".profile-guarantees code")).to_have_text(expression)
        expect(
            page.locator(".profile-guarantees img, .profile-guarantees script")
        ).to_have_count(0)
        page.get_by_role("tab", name="Decision history").click()
        expect(page.locator(".profile-card")).to_have_count(3)
        legacy = page.locator(".profile-card").filter(has_text="rules.legacy()")
        expect(legacy.locator(".contract-description")).to_have_count(0)
        fallback = page.locator(".profile-card").filter(has_text="rules.fallback()")
        expect(fallback.locator(".contract-description")).to_have_count(0)
        expect(fallback.locator("code")).to_be_visible()
        expect(fallback.locator(".contract-documentation")).not_to_be_visible()
        fallback.locator(".contract-details summary").focus()
        page.keyboard.press("Enter")
        expect(fallback.locator(".contract-documentation")).to_be_visible()
        expect(fallback.locator(".contract-documentation")).to_have_text(
            "Fallback docs."
        )
        page.locator("#profile-search").fill("Trusted documentation")
        expect(page.locator(".profile-card")).to_have_count(1)
        # The live server-rendered card must have the same compact fallback.
        page.goto("http://contract-fixture.test/ui/review")
        expect(page.locator(".contract-description")).to_have_count(0)
        expect(page.get_by_text("Description unavailable.", exact=True)).to_have_count(
            0
        )
        expect(page.get_by_text("Pending user decision.", exact=True)).to_have_count(0)
        expect(
            page.get_by_text("No domain policies applied.", exact=True)
        ).not_to_be_visible()
        for item in page.locator(".contract-details").all():
            expect(item.locator(".contract-documentation")).not_to_be_visible()
        page.screenshot(path="/tmp/clauz3-compact-action-review.png", full_page=True)
        page.locator(".contract-details summary").first.click()
        expect(page.locator(".contract-documentation").first).to_be_visible()
        expect(page.locator(".contract-documentation").first).to_contain_text(
            "only that one"
        )
        page.locator('[data-panel="record"] summary').click()
        expect(
            page.get_by_text("No domain policies applied.", exact=True)
        ).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        browser.close()
    print("Saved descriptions, documentation, escaping, fallback and search passed.")


if __name__ == "__main__":
    check()
