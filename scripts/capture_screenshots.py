"""Regenerate the README screenshots from the real UI.

This is a developer tool, not part of the installed package. It

1. seeds a temporary SQLite database with representative run history,
2. starts the real FastAPI dashboard with ``uvicorn`` and screenshots it
   with a locally installed Chromium-based browser,
3. invokes the real CLI commands with a recording Rich console and renders
   the captured output into a terminal-window mockup.

Usage::

    python scripts/capture_screenshots.py
    FLOWGUARD_BROWSER=/path/to/chrome python scripts/capture_screenshots.py

The four PNGs are written to ``docs/screenshots/`` and committed, so CI
only has to validate them (see ``tests/test_docs.py``).
"""

from __future__ import annotations

import io
import json
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console  # noqa: E402

import flowguard.cli.commands as commands  # noqa: E402
from flowguard.api.app import create_app  # noqa: E402
from flowguard.core.models import Status, StepResult  # noqa: E402
from flowguard.storage.database import Database  # noqa: E402

SHOTS = ROOT / "docs" / "screenshots"

DEMO_RUNS = [
    ("release-pipeline", "failed", 5, 41_200.0, ["install", "lint", "test", "package", "deploy"]),
    ("build-project", "succeeded", 5, 26_500.0, ["install", "lint", "test", "package", "publish"]),
    (
        "nightly-e2e",
        "succeeded",
        7,
        88_400.0,
        ["setup", "unit", "integration", "e2e", "report", "upload", "notify"],
    ),
    ("lint-and-format", "succeeded", 3, 9_100.0, ["ruff", "format", "typecheck"]),
    ("docs-preview", "timeout", 4, 60_000.0, ["install", "build", "screenshot", "deploy"]),
    ("smoke-test", "succeeded", 2, 4_300.0, ["start", "probe"]),
    ("build-project", "succeeded", 5, 25_800.0, ["install", "lint", "test", "package", "publish"]),
    ("package", "succeeded", 3, 12_700.0, ["clean", "wheel", "sdist"]),
]

CLI_FONTS = {
    # Rich counts East-Asian wide glyphs as two terminal cells. The HTML
    # mockup forces the same geometry with .cjk spans, so any monospace
    # face works and the box-drawing borders stay aligned.
    "en": '"Cascadia Mono", "Cascadia Code", Consolas, monospace',
    "zh": '"Cascadia Mono", "Cascadia Code", "Microsoft YaHei", monospace',
}

CLI_HTML = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 26px; background: #050912;
         background-image: radial-gradient(800px 400px at 20% -10%, rgba(34,211,238,.13), transparent 70%),
                           radial-gradient(700px 400px at 90% 0%, rgba(167,139,250,.12), transparent 70%);
         font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }}
  .window {{ max-width: 1140px; margin: 0 auto; border: 1px solid #24344f; border-radius: 14px;
            overflow: hidden; box-shadow: 0 30px 90px rgba(0,0,0,.65), 0 0 0 1px rgba(34,211,238,.07); }}
  .bar {{ height: 44px; display: flex; align-items: center; gap: 8px; padding: 0 18px;
          background: linear-gradient(180deg, #19263c, #101a2b); border-bottom: 1px solid #24344f; }}
  .dot {{ width: 11px; height: 11px; border-radius: 50%; display: block; }}
  .title {{ margin-left: 16px; color: #8296b8; font-size: 12px; font-weight: 600; letter-spacing: .3px; }}
  .badge {{ margin-left: auto; color: #4ade80; font-size: 10px; font-weight: 700;
            border: 1px solid rgba(74,222,128,.3); padding: 3px 9px; border-radius: 999px; }}
  .screen {{ background: #070b14; padding: 22px 28px 28px; }}
  .prompt {{ color: #4ade80; font-family: "Cascadia Mono", Consolas, monospace;
             font-size: 13px; margin-bottom: 10px; }}
  .prompt b {{ color: #22d3ee; font-weight: 600; }}
  .cjk {{ display: inline-block; width: 2ch; text-align: center; }}
  pre {{ margin: 0; white-space: pre; font-size: 13px; line-height: 1.34 !important;
         color: #dce6f7;
         font-family: {font} !important; }}
</style></head><body>
<div class="window">
  <div class="bar"><span class="dot" style="background:#f87171"></span>
    <span class="dot" style="background:#fbbf24"></span>
    <span class="dot" style="background:#4ade80"></span>
    <span class="title">flowguard — terminal</span><span class="badge">LOCAL</span></div>
  <div class="screen"><div class="prompt">$ <b>{command}</b></div>{body}</div>
</div></body></html>"""

CLI_WORKFLOW = """\
workflow:
  name: build-project
  environment:
    PYTHONUNBUFFERED: "1"
  steps:
    - name: install
      command: [{python}, -c, "import time; time.sleep(0.12)"]
    - name: lint
      command: [{python}, -c, "import time; time.sleep(0.22)"]
      depends_on: [install]
    - name: test
      command: [{python}, -c, "import time; time.sleep(0.48)"]
      depends_on: [install]
      retry:
        attempts: 2
        delay: 0
    - name: package
      command: [{python}, -c, "import time; time.sleep(0.18)"]
      depends_on: [lint, test]
      artifacts:
        - dist/*
"""


def browser_path() -> str:
    import os
    import shutil

    override = os.environ.get("FLOWGUARD_BROWSER")
    if override:
        return override
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    for name in ("chromium", "google-chrome", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("No Chromium-based browser found. Set FLOWGUARD_BROWSER.")


def seed_database(path: Path) -> None:
    """Populate a realistic run history for the screenshots."""
    db = Database(path)
    now = time.time()
    for index, (name, status, total, duration, steps) in enumerate(DEMO_RUNS):
        run_id = (f"{index + 1:02x}a7c9e{index + 1:02x}b1" + "0")[:12]
        started = now - (index + 1) * 1870
        db.create_run(
            run_id,
            name,
            config_path=f"~/projects/{name}/.flowguard.yml",
            total_steps=total,
            started_at=started,
        )
        db.add_event(run_id, f"run started: workflow '{name}'", timestamp=started)
        for position, step_name in enumerate(steps):
            step_status = Status.SUCCEEDED
            if status == "failed" and step_name == "test":
                step_status = Status.FAILED
            elif status == "failed" and step_name in {"package", "deploy"}:
                step_status = Status.SKIPPED
            elif status == "timeout" and step_name == "screenshot":
                step_status = Status.TIMEOUT
            elif status == "timeout" and step_name == "deploy":
                step_status = Status.SKIPPED
            db.add_step_result(
                run_id,
                StepResult(
                    step_name=step_name,
                    status=step_status,
                    exit_code=0
                    if step_status is Status.SUCCEEDED
                    else (1 if step_status is Status.FAILED else None),
                    stdout="ok\n" if step_status is Status.SUCCEEDED else "",
                    stderr="assertion failed\n" if step_status is Status.FAILED else "",
                    error="command exited with code 1" if step_status is Status.FAILED else None,
                    duration_ms=max(400.0, duration / total) * (0.6 + position * 0.15),
                    attempts=2 if step_name == "test" and status == "failed" else 1,
                    started_at=started + position,
                    finished_at=started + position + 1,
                ),
            )
        db.update_run(
            run_id,
            status=Status(status),
            finished_at=started + duration / 1000,
            duration_ms=duration,
        )
    db.close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.1)
    raise SystemExit(f"Dashboard did not start on port {port}")


def screenshot(browser: str, url: str, output: Path, *, width: int, height: int) -> None:
    with tempfile.TemporaryDirectory() as profile:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--no-first-run",
            "--disable-extensions",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            "--virtual-time-budget=6000",
            f"--screenshot={output}",
            url,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if not output.exists() or output.stat().st_size < 5000:
            raise SystemExit(f"Screenshot failed for {url}: {result.stderr[-800:]}")
    print(f"  wrote {output.relative_to(ROOT)} ({output.stat().st_size:,} bytes)")


def capture_dashboard(browser: str, db_path: Path) -> None:
    import uvicorn

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(db_path=str(db_path)), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        wait_for_port(port)
        for lang in ("en", "zh"):
            screenshot(
                browser,
                f"http://127.0.0.1:{port}/?lang={lang}",
                SHOTS / f"dashboard-{lang}.png",
                width=1440,
                height=1040,
            )
    finally:
        server.should_exit = True
        thread.join(timeout=10)


_CJK_CHAR = re.compile(r"[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef]")
_TEXT_NODE = re.compile(r">([^<]*)<")


def widen_cjk(html: str) -> str:
    """Force every East-Asian wide glyph to occupy exactly two terminal cells."""

    def replace(match: re.Match[str]) -> str:
        text = match.group(1)
        widened = _CJK_CHAR.sub(lambda c: f'<span class="cjk">{c.group(0)}</span>', text)
        return f">{widened}<"

    return _TEXT_NODE.sub(replace, html)


def capture_cli(lang: str, tmp: Path) -> None:
    import os

    project = tmp / f"project-{lang}"
    project.mkdir()
    (project / ".flowguard.yml").write_text(
        CLI_WORKFLOW.format(python=sys.executable), encoding="utf-8"
    )
    db_path = tmp / f"cli-{lang}.db"

    recording = Console(
        record=True,
        force_terminal=True,
        width=100,
        color_system="truecolor",
        file=io.StringIO(),
    )
    commands.console = recording
    commands.err_console = recording
    commands._set_language(lang)
    previous_cwd = Path.cwd()
    try:
        os.chdir(project)
        # path=None makes the CLI discover .flowguard.yml itself, so the
        # screenshot shows the real command a user would type.
        commands.run(path=None, db=db_path, no_artifacts=True)
    finally:
        os.chdir(previous_cwd)

    html = recording.export_html(inline_styles=True)
    start = html.index("<pre")
    end = html.index("</pre>", start) + len("</pre>")
    body = widen_cjk(html[start:end])
    command_line = "flowguard run" + (f" --lang {lang}" if lang != "en" else "")
    page = CLI_HTML.format(lang=lang, command=command_line, body=body, font=CLI_FONTS[lang])
    page_path = tmp / f"cli-{lang}.html"
    page_path.write_text(page, encoding="utf-8")
    screenshot(
        browser_path(),
        page_path.as_uri(),
        SHOTS / f"cli-{lang}.png",
        width=1000,
        height=650,
    )


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        tmp = Path(scratch)
        db_path = tmp / "demo.db"
        seed_database(db_path)
        print("Capturing dashboard screenshots…")
        browser = browser_path()
        capture_dashboard(browser, db_path)
        print("Capturing CLI screenshots…")
        for lang in ("en", "zh"):
            capture_cli(lang, tmp)
        # Keep a machine-readable manifest next to the images.
        (SHOTS / "manifest.json").write_text(
            json.dumps(
                {
                    "generated_by": "scripts/capture_screenshots.py",
                    "languages": ["en", "zh"],
                    "views": ["dashboard", "cli"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print("Done.")


if __name__ == "__main__":
    main()
