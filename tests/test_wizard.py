"""Tests for the interactive setup wizard.

Drives the wizard via stdin (monkeypatched input) and checks that:
- the config it writes is valid JSON
- the values it captures actually land in config.user
- aborting (Ctrl+C / EOF) does not overwrite an existing config
- pressing Enter on a known field keeps the existing value
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Iterable

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vfs_helper import WIZARD_FIELDS, run_setup_wizard  # noqa: E402


def make_inputs(values: Iterable[str]) -> io.StringIO:
    return io.StringIO("\n".join(values) + "\n")


def test_wizard_writes_full_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.json"
    answers = [
        "Timur", "Kuznetsov", "",                       # name fields
        "MP9999999", "15/03/1995",
        "01/01/2020", "01/01/2030",
        "timur@example.org", "+375291112233",
        "Belarusian", "Male",
        "timur@example.org", "secret",
    ]
    assert len(answers) == len(WIZARD_FIELDS)

    monkeypatch.setattr("sys.stdin", make_inputs(answers))
    rc = run_setup_wizard(cfg_path)

    assert rc == 0
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["user"]["firstName"] == "Timur"
    assert data["user"]["lastName"] == "Kuznetsov"
    assert data["user"]["middleName"] == ""
    assert data["user"]["passportNumber"] == "MP9999999"
    assert data["user"]["dateOfBirth"] == "15/03/1995"
    assert data["user"]["loginPassword"] == "secret"
    # The wizard should preserve example fields / profiles untouched.
    assert "profiles" in data
    assert "fields" in data


def test_wizard_empty_answer_keeps_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For fields with a non-empty default (like gender=Male), pressing Enter
    must keep that default in the output."""
    cfg_path = tmp_path / "config.json"
    # All Enter-only - so every field uses the WIZARD default.
    answers = [""] * len(WIZARD_FIELDS)
    monkeypatch.setattr("sys.stdin", make_inputs(answers))

    rc = run_setup_wizard(cfg_path)
    assert rc == 0

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Fields with empty default stay empty.
    assert data["user"]["firstName"] == ""
    # Fields with a non-empty default keep that default.
    assert data["user"]["gender"] == "Male"
    assert data["user"]["nationality"] == "Belarusian"


def test_wizard_abort_via_eof_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "config.json"
    # Pre-existing config with real data.
    cfg_path.write_text(
        json.dumps({"start_url": "X", "user": {"firstName": "PreExisting"}}),
        encoding="utf-8",
    )

    # Empty stdin -> EOFError on first input() call -> wizard aborts.
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = run_setup_wizard(cfg_path)
    assert rc == 1

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["user"]["firstName"] == "PreExisting"


def test_wizard_preserves_existing_value_on_enter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({
            "user": {
                "firstName": "Existing",
                "lastName": "User",
                "email": "existing@example.org",
            }
        }),
        encoding="utf-8",
    )

    # Just hit Enter on every field - should keep existing values where present
    # and use WIZARD_FIELDS defaults otherwise.
    answers = [""] * len(WIZARD_FIELDS)
    monkeypatch.setattr("sys.stdin", make_inputs(answers))

    rc = run_setup_wizard(cfg_path)
    assert rc == 0

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["user"]["firstName"] == "Existing"
    assert data["user"]["lastName"] == "User"
    assert data["user"]["email"] == "existing@example.org"
    # Untouched fields with default still get the default.
    assert data["user"]["gender"] == "Male"
