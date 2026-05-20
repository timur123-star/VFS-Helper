"""Tests for the autocomplete-search-box dropdown handler.

These tests run a tiny local HTML page that reproduces the
.autocomplete-search-box / .selected-wrapper pattern used by
vfsglobal.com (and many other Angular CMSes). We need a real
browser for the events to fire, so they need Playwright Chromium
installed.

If Playwright Chromium is not installed (CI without browser
download), they are skipped, not failed.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# Skip the whole module if Playwright can't launch a browser.
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
    reason="Playwright Chromium not installed; run 'python -m playwright install chromium'.",
)


from vfs_helper import (  # noqa: E402
    _find_autocomplete_container,
    _fill_autocomplete_dropdown,
    fill_one_field,
)


# Local fake page that mimics the vfsglobal.com country selector exactly.
# This is NOT a 'demo replacing the real site' - it's a unit-test fixture
# to exercise the helper's selector logic without hitting the network.
FIXTURE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>autocomplete-search-box fixture</title>
<style>
  .autocomplete-search-box {position: relative; width: 280px; margin: 80px;}
  .input-wrapper input.search-input {width: 100%; padding: 8px;}
  .selected-wrapper {position: absolute; top: 0; left: 0; right: 30px;
    background: #fff; padding: 8px; border: 1px solid #ccc; height: 18px;
    pointer-events: auto;}
  .selected {color: #888;}
  .selected.has-value {color: #000;}
  .toggle-button-wrapper {position: absolute; top: 0; right: 0; width: 30px;
    height: 34px; cursor: pointer; background: #eee; text-align: center;
    line-height: 34px;}
  .list-wrapper {position: absolute; top: 36px; left: 0; right: 0;
    max-height: 200px; overflow-y: auto; background: #fff; border: 1px solid #ccc;
    display: none;}
  .list-wrapper.open {display: block;}
  ul {list-style: none; padding: 0; margin: 0;}
  li {padding: 6px 8px; cursor: pointer;}
  li:hover, li.active {background: #eef;}
</style></head><body>
<h1>Fixture page</h1>
<div id="country" class="autocomplete-search-box">
  <div class="input-wrapper"><input class="search-input" placeholder="Select Country" autocomplete="off"></div>
  <div class="selected-wrapper"><div class="selected">Select Country</div></div>
  <div class="toggle-button-wrapper"><div class="toggle-button">v</div></div>
  <div class="list-wrapper">
    <ul>
      <li><div class="text">Afghanistan</div></li>
      <li><div class="text">Belarus</div></li>
      <li><div class="text">Belgium</div></li>
      <li><div class="text">Poland</div></li>
      <li><div class="text">Russia</div></li>
      <li><div class="text">Ukraine</div></li>
    </ul>
  </div>
</div>
<script>
  const box = document.getElementById('country');
  const input = box.querySelector('.search-input');
  const sel = box.querySelector('.selected');
  const list = box.querySelector('.list-wrapper');
  const toggle = box.querySelector('.toggle-button-wrapper');
  function open() { list.classList.add('open'); input.focus(); }
  function close() { list.classList.remove('open'); }
  toggle.addEventListener('click', () => list.classList.contains('open') ? close() : open());
  input.addEventListener('input', () => {
    const v = input.value.toLowerCase();
    for (const li of list.querySelectorAll('li')) {
      const t = li.querySelector('.text').textContent.toLowerCase();
      li.style.display = t.includes(v) ? '' : 'none';
    }
  });
  list.addEventListener('click', (ev) => {
    const li = ev.target.closest('li');
    if (!li) return;
    const t = li.querySelector('.text').textContent;
    sel.textContent = t;
    sel.classList.add('has-value');
    input.value = t;
    window.__SELECTED__ = t;
    close();
  });
</script>
</body></html>
"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    fix_dir = tmp_path_factory.mktemp("fixtures")
    fixture = fix_dir / "autocomplete.html"
    fixture.write_text(FIXTURE_HTML, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1024, "height": 700})
        p = ctx.new_page()
        p.goto(fixture.as_uri())
        yield p
        ctx.close()
        browser.close()


def test_find_autocomplete_container(page) -> None:
    locator = page.locator("input.search-input").first
    container = _find_autocomplete_container(locator)
    assert container is not None
    assert container.evaluate("el => el.id") == "country"


def test_fill_autocomplete_dropdown_selects_option(page) -> None:
    # Reset selection
    page.evaluate("() => { window.__SELECTED__ = null; "
                  "document.querySelector('.selected').textContent = 'Select Country'; "
                  "document.querySelector('.search-input').value = ''; }")
    locator = page.locator("input.search-input").first
    container = _find_autocomplete_container(locator)
    ok = _fill_autocomplete_dropdown(
        page, container, "Belarus",
        {"simulate_human": True, "keystroke_delay_ms": 10},
    )
    assert ok is True
    assert page.evaluate("() => window.__SELECTED__") == "Belarus"


def test_fill_one_field_routes_autocomplete(page) -> None:
    page.evaluate("() => { window.__SELECTED__ = null; "
                  "document.querySelector('.selected').textContent = 'Select Country'; "
                  "document.querySelector('.search-input').value = ''; }")
    logger = logging.getLogger("test")
    sel, kind = fill_one_field(
        page,
        ["input.search-input"],
        "Poland",
        {"simulate_human": True, "keystroke_delay_ms": 10},
        logger,
    )
    assert sel == "input.search-input"
    assert kind == "autocomplete-dropdown"
    assert page.evaluate("() => window.__SELECTED__") == "Poland"


def test_fill_autocomplete_returns_false_for_missing_option(page) -> None:
    page.evaluate("() => { window.__SELECTED__ = null; "
                  "document.querySelector('.selected').textContent = 'Select Country'; "
                  "document.querySelector('.search-input').value = ''; }")
    locator = page.locator("input.search-input").first
    container = _find_autocomplete_container(locator)
    ok = _fill_autocomplete_dropdown(
        page, container, "Atlantis",
        {"simulate_human": False, "keystroke_delay_ms": 0},
    )
    assert ok is False
    assert page.evaluate("() => window.__SELECTED__") is None
