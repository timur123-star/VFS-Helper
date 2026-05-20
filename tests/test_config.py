"""Tests for Config loading and selector resolution.

These tests don't touch Playwright at all - they just verify the pure-Python
logic so they run in any environment (CI, dev laptop without Chromium, etc.).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vfs_helper import (  # noqa: E402
    AUTOCOMPLETE_CONTAINER_XPATH,
    Config,
    load_config_or_die,
)


def write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_config_load_minimal(tmp_path: Path) -> None:
    p = write_config(tmp_path, {"start_url": "https://example.com"})
    cfg = Config.load(p)
    assert cfg.start_url == "https://example.com"
    assert cfg.user == {}
    assert cfg.fields == {}
    assert cfg.profiles == {}
    assert cfg.browser == {}
    assert cfg.typing == {}


def test_config_load_full(tmp_path: Path) -> None:
    p = write_config(tmp_path, {
        "start_url": "https://visa.vfsglobal.com/blr/ru/pol/login",
        "user": {"firstName": "Ivan"},
        "fields": {"firstName": ["input[name='firstName']"]},
        "profiles": {
            "login": {"loginEmail": ["input[type='email']"]},
        },
        "browser": {"channel": "chrome", "headless": False},
        "typing": {"simulate_human": True},
    })
    cfg = Config.load(p)
    assert cfg.user["firstName"] == "Ivan"
    assert cfg.fields["firstName"] == ["input[name='firstName']"]
    assert cfg.profiles["login"]["loginEmail"] == ["input[type='email']"]
    assert cfg.browser["channel"] == "chrome"
    assert cfg.typing["simulate_human"] is True


def test_selectors_for_top_level_only(tmp_path: Path) -> None:
    p = write_config(tmp_path, {
        "fields": {"firstName": ["a", "b"]},
    })
    cfg = Config.load(p)
    assert cfg.selectors_for("firstName", None) == ["a", "b"]
    assert cfg.selectors_for("firstName", "nonexistent") == ["a", "b"]
    assert cfg.selectors_for("nope", None) == []


def test_selectors_for_profile_prepended(tmp_path: Path) -> None:
    p = write_config(tmp_path, {
        "fields": {"email": ["x", "y"]},
        "profiles": {
            "login": {"email": ["p", "q"]},
        },
    })
    cfg = Config.load(p)
    # Profile-specific selectors come first, then fall back to top-level.
    assert cfg.selectors_for("email", "login") == ["p", "q", "x", "y"]


def test_selectors_for_dedup(tmp_path: Path) -> None:
    p = write_config(tmp_path, {
        "fields": {"email": ["x", "y"]},
        "profiles": {
            "login": {"email": ["p", "x"]},
        },
    })
    cfg = Config.load(p)
    assert cfg.selectors_for("email", "login") == ["p", "x", "y"]


def test_example_config_is_valid_json() -> None:
    """The shipped example config must parse cleanly via Config.load."""
    example = ROOT / "config.example.json"
    cfg = Config.load(example)
    assert cfg.start_url.startswith("https://visa.vfsglobal.com")
    # The example should define at least a basic set of fields.
    for key in ("firstName", "lastName", "email", "phone"):
        assert key in cfg.fields, f"{key} missing from example fields"
    # And a couple of profiles for multi-step flows.
    assert "login" in cfg.profiles
    # Selectors should use formControlName where possible (Angular).
    first_name_sel = cfg.fields["firstName"]
    assert any("formcontrolname" in s for s in first_name_sel), \
        "Expected Angular formControlName selector in firstName fallbacks"


def test_load_config_or_die_missing(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(SystemExit) as exc:
        load_config_or_die(missing)
    assert exc.value.code == 2
    assert "Config file not found" in capsys.readouterr().err


def test_load_config_or_die_bad_json(tmp_path: Path, capsys) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        load_config_or_die(p)
    assert exc.value.code == 2
    assert "Invalid JSON" in capsys.readouterr().err


def test_autocomplete_container_xpath_covers_known_classes() -> None:
    """The xpath must match all three class variants the helper claims to support."""
    xp = AUTOCOMPLETE_CONTAINER_XPATH
    # Must reference each class our helper documents.
    assert "autocomplete-search-box" in xp
    assert "search_dropdown" in xp
    assert "search-dropdown" in xp
    # Must be an ancestor lookup (so inputs nested inside the wrapper are found).
    assert "ancestor-or-self" in xp
