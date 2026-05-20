"""Integration: fill every field type on the full-application HTML fixture."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright

try:
    _pw = sync_playwright().start()
    try:
        _browser = _pw.chromium.launch(headless=True)
        _browser.close()
    finally:
        _pw.stop()
    _has_browser = True
except Exception:
    _has_browser = False

pytestmark = pytest.mark.skipif(
    not _has_browser,
    reason="Playwright Chromium not installed.",
)

from vfs_helper import Config, cmd_fill  # noqa: E402

FIXTURE = ROOT / "fixtures" / "vfs-applicant-form.html"
DEMO_CFG = ROOT / "config.demo-full.json"


@pytest.fixture(scope="module")
def full_form_page():
    assert FIXTURE.is_file(), f"missing fixture: {FIXTURE}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 1200})
        page.goto(FIXTURE.as_uri())
        yield page
        browser.close()


def test_demo_config_fills_most_fields_on_fixture(full_form_page, capsys) -> None:
    assert DEMO_CFG.is_file()
    cfg = Config.load(DEMO_CFG)
    logger = logging.getLogger("test_full_form")
    cmd_fill(full_form_page, cfg, None, logger)
    out = capsys.readouterr().out
    assert "Filled:" in out
    filled_lines = [ln for ln in out.splitlines() if ln.strip().startswith("- ")]
    # login + applicant fields + travel + docs — expect a broad fill
    assert len(filled_lines) >= 12, f"expected many fills, got:\n{out}"
