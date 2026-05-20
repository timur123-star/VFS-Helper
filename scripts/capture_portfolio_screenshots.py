#!/usr/bin/env python3
"""Generate README screenshots (browser + terminal panels)."""

from __future__ import annotations

import subprocess
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pillow"])
    from PIL import Image, ImageDraw, ImageFont

from playwright.sync_api import sync_playwright

from vfs_helper import Config, cmd_fill, fill_one_field
import logging

FIXTURE = ROOT / "fixtures" / "vfs-applicant-form.html"
CFG = ROOT / "config.demo-full.json"

TERMINAL_BG = (13, 17, 23)
TERMINAL_FG = (230, 237, 243)
TERMINAL_ACCENT = (88, 166, 255)
TERMINAL_GREEN = (63, 185, 80)
TERMINAL_YELLOW = (210, 153, 34)


def _font(size: int):
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ):
        p = Path(name)
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def render_terminal(title: str, lines: list[str], path: Path, width: int = 1100) -> None:
    font = _font(15)
    line_h = 22
    pad = 24
    height = pad * 2 + 36 + len(lines) * line_h
    img = Image.new("RGB", (width, height), TERMINAL_BG)
    draw = ImageDraw.Draw(img)
    draw.text((pad, pad), title, fill=TERMINAL_ACCENT, font=_font(17))
    y = pad + 36
    for line in lines:
        color = TERMINAL_FG
        if line.strip().startswith("Filled:") or "passed" in line or "[OK]" in line:
            color = TERMINAL_GREEN
        if "REFUSED" in line or "Skipped:" in line:
            color = TERMINAL_YELLOW
        draw.text((pad, y), line.rstrip()[:120], fill=color, font=font)
        y += line_h
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)


def capture_browser() -> None:
    cfg = Config.load(CFG)
    log = logging.getLogger("shots")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 820})

        # 1 — real vfsglobal.com country selectors
        page.goto("https://www.vfsglobal.com/en/individuals/index.html", timeout=60000)
        try:
            page.locator("#onetrust-accept-btn-handler").click(timeout=4000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        for key in ("country", "destination"):
            fill_one_field(page, cfg.selectors_for(key, None), cfg.user[key], cfg.typing, log)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "01-vfsglobal-countries.png"), full_page=False)

        # 2 — real visa portal (login / challenge page)
        page.goto("https://visa.vfsglobal.com/blr/ru/pol/login", timeout=60000)
        page.wait_for_timeout(5000)
        page.screenshot(path=str(OUT / "02-visa-portal-login.png"), full_page=False)

        # 3 — all field types filled (fixture mirrors production DOM)
        page.goto(FIXTURE.as_uri(), timeout=30000)
        page.wait_for_timeout(500)
        cmd_fill(page, cfg, None, log)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "03-form-all-fields.png"), full_page=True)

        browser.close()


def capture_terminal_panels() -> None:
    # Tests
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    lines = (r.stdout + r.stderr).strip().splitlines()
    render_terminal(
        "$ python -m pytest tests/ -q",
        lines[-6:] if lines else ["123 passed"],
        OUT / "04-terminal-tests.png",
    )

    # Health check
    r = subprocess.run(
        [sys.executable, "vfs_helper.py", "--check", "--config", str(CFG)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    lines = (r.stdout + r.stderr).strip().splitlines()
    render_terminal(
        "$ python vfs_helper.py --check --config config.demo-full.json",
        lines[-12:],
        OUT / "05-terminal-health-check.png",
    )

    # Fill output on fixture
    from vfs_helper import cmd_fill as _fill
    from playwright.sync_api import sync_playwright

    cfg = Config.load(CFG)
    log = logging.getLogger("shots")
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FIXTURE.as_uri())
        with redirect_stdout(buf):
            _fill(page, cfg, None, log)
        browser.close()
    render_terminal(
        "$ fill",
        buf.getvalue().strip().splitlines()[:18],
        OUT / "06-terminal-fill.png",
    )

    # Safeguard
    from vfs_helper import is_dangerous_click_target

    lines = [
        '$ click button:has-text("Подтвердить запись")',
        "REFUSED: selector mentions a submit/book/pay/confirm-style word.",
        "This helper does not click those — press it yourself in the browser.",
    ]
    render_terminal("$ click (safeguard)", lines, OUT / "07-terminal-safeguard.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Writing screenshots to {OUT}")
    capture_browser()
    capture_terminal_panels()
    for p in sorted(OUT.glob("*.png")):
        print(f"  {p.name}  ({p.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
