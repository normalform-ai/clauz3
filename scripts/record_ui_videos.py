"""Record the email approvals tutorial for the docs.

Runs the real `clauz3 ui --tutorial` with the default agent, in an isolated
data directory, and drives it with Playwright while recording video. The
agent's thinking time is fast-forwarded with ffmpeg. Writes:

- docs/assets/videos/tutorial.mp4 (mkdocs)
- docs/assets/tutorial.gif (README)
- docs/assets/workspace-ui.png (a still of the approval request)

Needs a working agent login, ffmpeg, and Chromium:

    uv run --with playwright playwright install chromium
    just docs-videos
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MP4 = ROOT / "docs/assets/videos/tutorial.mp4"
GIF = ROOT / "docs/assets/tutorial.gif"
STILL = ROOT / "docs/assets/workspace-ui.png"
SIZE = {"width": 1280, "height": 800}
FAST_FORWARD = 12

# Playwright's video omits the pointer, so draw one that follows the mouse.
CURSOR = """
addEventListener("DOMContentLoaded", () => {
  const dot = document.createElement("div");
  dot.id = "demo-cursor";
  dot.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;"
    + "width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;"
    + "background:rgba(37,102,78,.35);border:2px solid #25664e;"
    + "transition:transform .12s;left:-40px;top:-40px";
  document.body.append(dot);
  addEventListener("mousemove", e => {
    dot.style.left = e.clientX + "px";
    dot.style.top = e.clientY + "px";
  }, true);
  addEventListener("mousedown", () => dot.style.transform = "scale(.7)", true);
  addEventListener("mouseup", () => dot.style.transform = "", true);
});
"""


def launch(base: Path) -> tuple[subprocess.Popen[bytes], str]:
    """Start the UI with its own data directory and return the login URL."""
    project, log = base / "project", base / "ui.log"
    project.mkdir()
    with log.open("wb") as output:
        process = subprocess.Popen(
            [str(ROOT / ".venv/bin/clauz3"), "ui", "--tutorial", "--no-browser"],
            cwd=project,
            env={**os.environ, "CLAUZ3_HOME": str(base / "data")},
            stdout=output,
            stderr=output,
        )
    while process.poll() is None:
        if match := re.search(r"http://\S+/ui/login\?key=\S+", log.read_text()):
            return process, match.group(0)
        time.sleep(0.2)
    raise RuntimeError(f"clauz3 ui exited early:\n{log.read_text()}")


def record(login: str, videos: Path) -> tuple[Path, list[tuple[float, float]]]:
    """Drive the tutorial; return the video and the spans to fast-forward."""
    from playwright.sync_api import Locator, expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport=SIZE, record_video_dir=str(videos), record_video_size=SIZE
        )
        context.add_init_script(CURSOR)
        page = context.new_page()
        start = time.monotonic()

        def now() -> float:
            return time.monotonic() - start

        def click(target: Locator) -> None:
            target.scroll_into_view_if_needed()
            box = target.bounding_box()
            assert box is not None
            page.mouse.move(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25
            )
            page.wait_for_timeout(350)
            target.click()

        page.goto(login)
        expect(page.locator("#send")).to_be_enabled(timeout=60000)
        expect(page.locator("#tutorial-help")).to_be_visible()
        page.wait_for_timeout(1500)
        click(page.locator('#tutorial-prompts [data-prompt="send"]'))
        page.wait_for_timeout(2000)
        click(page.locator("#send"))
        working = now() + 3
        approve = page.get_by_role("button", name="Approve", exact=True)
        expect(approve).to_be_visible(timeout=300000)
        spans = [(working, now() - 0.5)]
        # Let the viewer read the verified limits, then scroll to the decision.
        page.locator(".run").last.evaluate("el => el.scrollIntoView()")
        page.screenshot(path=STILL, style="#demo-cursor { display: none }")
        page.wait_for_timeout(4500)
        for _ in range(8):
            page.mouse.wheel(0, 90)
            page.wait_for_timeout(90)
        page.wait_for_timeout(1500)
        click(approve)
        working = now() + 2
        expect(page.locator(".phase").last).to_have_text("Completed", timeout=120000)
        expect(page.locator("#status")).to_have_text("Ready", timeout=300000)
        spans.append((working, now() - 0.5))
        page.evaluate("scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3500)
        click(page.locator('.sidebar nav a[href="/ui/profile"]'))
        page.wait_for_timeout(1000)
        click(page.get_by_role("tab", name="Decision history"))
        page.wait_for_timeout(3000)
        click(page.locator("#workspace-picker"))
        page.wait_for_timeout(3000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        video = page.video
        assert video is not None
        context.close()
        browser.close()
        return Path(video.path()), spans


def encode(source: Path, spans: list[tuple[float, float]]) -> None:
    """Fast-forward the given spans and write the MP4 and GIF."""
    cuts = [0.0]
    for begin, end in spans:
        cuts += [begin, end]
    parts, labels = [], []
    for index, begin in enumerate(cuts):
        end = cuts[index + 1] if index + 1 < len(cuts) else None
        trim = f"trim=start={begin}" + (f":end={end}" if end is not None else "")
        speed = FAST_FORWARD if index % 2 else 1
        parts.append(f"[0:v]{trim},setpts=(PTS-STARTPTS)/{speed}[v{index}]")
        labels.append(f"[v{index}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=1[out]"
    MP4.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source)]
        + ["-filter_complex", graph, "-map", "[out]", "-an"]
        + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28"]
        + ["-movflags", "+faststart", str(MP4)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(MP4), "-vf"]
        + [
            "fps=8,scale=880:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=none"
        ]
        + [str(GIF)],
        check=True,
    )


def main() -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    with tempfile.TemporaryDirectory(prefix="clauz3-video-") as directory:
        base = Path(directory)
        process, login = launch(base)
        try:
            video, spans = record(login, base / "videos")
        finally:
            process.terminate()
            process.wait(timeout=30)
        encode(video, spans)
    for path in (MP4, GIF, STILL):
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
