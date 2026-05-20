"""Tests for checkbox / radio / file-upload helpers and cookies commands.

Pure unit tests where possible (no browser); for the input handlers we
exercise the high-level fill_one_field against an inline Playwright page,
just like tests/test_autocomplete.py.
"""

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


from vfs_helper import (  # noqa: E402
    _coerce_bool,
    _resolve_upload_path,
    fill_one_field,
)


# -------------------- pure unit tests --------------------


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    (1, True), (0, False),
    (2.5, True), (0.0, False),
    ("true", True), ("yes", True), ("YES", True), ("1", True), ("on", True),
    ("agree", True), ("accept", True), ("y", True), ("Checked", True),
    ("false", False), ("no", False), ("NO", False), ("0", False), ("off", False),
    ("disagree", False), ("decline", False), ("n", False), ("Unchecked", False),
    ("maybe", None), ("", None), ("Belarus", None),
])
def test_coerce_bool(value, expected) -> None:
    assert _coerce_bool(value) == expected


def test_resolve_upload_path_absolute(tmp_path: Path) -> None:
    f = tmp_path / "passport.jpg"
    f.write_bytes(b"jpeg-bytes-here")
    assert _resolve_upload_path(str(f)) == f


def test_resolve_upload_path_relative_to_project(tmp_path: Path, monkeypatch) -> None:
    # Place a file under project root, then ask with a relative path.
    from vfs_helper import HERE
    rel = "tests/_tmp_upload.bin"
    p = HERE / rel
    p.write_bytes(b"x")
    try:
        assert _resolve_upload_path(rel) == p
    finally:
        p.unlink(missing_ok=True)


def test_resolve_upload_path_missing() -> None:
    assert _resolve_upload_path("nope-does-not-exist.dat") is None


def test_resolve_upload_path_empty() -> None:
    assert _resolve_upload_path("") is None


# -------------------- browser tests --------------------

pytestmark_browser = pytest.mark.skipif(
    not _has_browser,
    reason="Playwright Chromium not installed; run 'python -m playwright install chromium'.",
)


CHECKBOX_RADIO_FILE_HTML = """<!doctype html>
<html><body>
<form id="f">
  <label>
    <input type="checkbox" id="agree" name="agree">
    I agree to the terms
  </label>

  <fieldset id="gender-group">
    <legend>Gender</legend>
    <label><input type="radio" name="gender" value="M" id="g_m"> Male</label>
    <label><input type="radio" name="gender" value="F" id="g_f"> Female</label>
    <label><input type="radio" name="gender" value="X" id="g_x"> Other</label>
  </fieldset>

  <fieldset id="visa-type-group">
    <legend>Visa type</legend>
    <label><input type="radio" name="visa" value="S" id="v_s"> Schengen short stay</label>
    <label><input type="radio" name="visa" value="L" id="v_l"> Long stay national</label>
  </fieldset>

  <input type="file" id="passport_photo" name="passport_photo" accept="image/*">
</form>
</body></html>
"""


@pytest.fixture(scope="module")
def crf_page(tmp_path_factory):
    fix_dir = tmp_path_factory.mktemp("crf")
    fixture = fix_dir / "form.html"
    fixture.write_text(CHECKBOX_RADIO_FILE_HTML, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 800, "height": 600})
        p = ctx.new_page()
        p.goto(fixture.as_uri())
        yield p
        ctx.close()
        browser.close()


@pytestmark_browser
def test_fill_checkbox_check(crf_page) -> None:
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["#agree"], True, {}, log,
    )
    assert sel == "#agree"
    assert kind == "checkbox"
    assert crf_page.locator("#agree").is_checked()


@pytestmark_browser
def test_fill_checkbox_uncheck(crf_page) -> None:
    crf_page.locator("#agree").check()
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["#agree"], False, {}, log,
    )
    assert kind == "checkbox"
    assert not crf_page.locator("#agree").is_checked()


@pytestmark_browser
def test_fill_checkbox_string_truthy(crf_page) -> None:
    crf_page.locator("#agree").uncheck()
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["#agree"], "yes", {}, log,
    )
    assert kind == "checkbox"
    assert crf_page.locator("#agree").is_checked()


@pytestmark_browser
def test_fill_radio_by_value(crf_page) -> None:
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["#g_m"], "F", {}, log,
    )
    assert kind == "radio"
    assert crf_page.locator("#g_f").is_checked()
    assert not crf_page.locator("#g_m").is_checked()


@pytestmark_browser
def test_fill_radio_by_label_text(crf_page) -> None:
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["input[name='visa']"], "Long stay", {}, log,
    )
    assert kind == "radio"
    assert crf_page.locator("#v_l").is_checked()


@pytestmark_browser
def test_fill_radio_missing_value(crf_page) -> None:
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["#g_m"], "ABCD-not-a-real-option", {}, log,
    )
    assert sel is None
    assert "matched value" in kind


@pytestmark_browser
def test_fill_file_upload_absolute(crf_page, tmp_path) -> None:
    photo = tmp_path / "passport.png"
    # Minimal PNG file (just a valid header so set_input_files accepts it)
    photo.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x00\x00\x00\x00\x3a\x7e\x9b\x55\x00\x00\x00\x0cIDAT\x08\x99c\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["#passport_photo"], str(photo), {}, log,
    )
    assert kind == "file"
    # Confirm Playwright sees the file
    name = crf_page.evaluate(
        "() => document.getElementById('passport_photo').files[0].name"
    )
    assert name == "passport.png"


@pytestmark_browser
def test_fill_file_upload_missing_file(crf_page) -> None:
    log = logging.getLogger("test")
    sel, kind = fill_one_field(
        crf_page, ["#passport_photo"], "/tmp/definitely-not-a-real-file.jpg", {}, log,
    )
    assert sel is None
    assert "file not found" in kind


# -------------------- cookies command --------------------


@pytestmark_browser
def test_cookies_save_load_clear(crf_page, tmp_path) -> None:
    """Use the shared browser context to avoid spinning a second
    sync_playwright session (which conflicts with pytest's event loop)."""
    from vfs_helper import cmd_cookies

    ctx = crf_page.context
    cookies_file = tmp_path / "saved.json"

    # Start clean
    ctx.clear_cookies()
    ctx.add_cookies([
        {"name": "session", "value": "abc123",
         "domain": "example.com", "path": "/"},
        {"name": "cf_clearance", "value": "xyz",
         "domain": "example.com", "path": "/"},
    ])

    cmd_cookies(ctx, f"save {cookies_file}")
    assert cookies_file.exists()
    data = json.loads(cookies_file.read_text(encoding="utf-8"))
    names = [c["name"] for c in data]
    assert "session" in names and "cf_clearance" in names

    cmd_cookies(ctx, "clear")
    assert ctx.cookies() == []

    cmd_cookies(ctx, f"load {cookies_file}")
    names_after = sorted(c["name"] for c in ctx.cookies())
    assert "cf_clearance" in names_after and "session" in names_after

    # Leave it clean for any later tests in the same fixture.
    ctx.clear_cookies()
