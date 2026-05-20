"""Tests for config validation (dates, email, phone, placeholders)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vfs_helper import validate_user  # noqa: E402


def test_clean_config_has_no_warnings() -> None:
    user = {
        "firstName": "Timur",
        "lastName": "Kuznetsov",
        "passportNumber": "MP1234567",
        "dateOfBirth": "15/03/1995",
        "passportIssueDate": "01/01/2020",
        "passportExpiryDate": "01/01/2030",
        "email": "real-user@example.org",
        "phone": "+375331112233",
    }
    assert validate_user(user) == []


@pytest.mark.parametrize("bad_date", [
    "1995-03-15",
    "15.03.1995",
    "15-03-1995",
    "3/15/1995",
    "today",
    "1/1/2020",
])
def test_bad_date_format_is_flagged(bad_date: str) -> None:
    warnings = validate_user({"dateOfBirth": bad_date})
    assert any("DD/MM/YYYY" in w for w in warnings), warnings


@pytest.mark.parametrize("good_date", [
    "01/01/2020",
    "31/12/1999",
    "15/03/1995",
])
def test_good_date_format_passes(good_date: str) -> None:
    warnings = validate_user({"dateOfBirth": good_date})
    assert not any("DD/MM/YYYY" in w for w in warnings), warnings


@pytest.mark.parametrize("bad_email", [
    "not-an-email",
    "no@dot",
    "@example.com",
    "spaces in@here.com",
])
def test_bad_email_is_flagged(bad_email: str) -> None:
    warnings = validate_user({"email": bad_email})
    assert any("email" in w for w in warnings), warnings


def test_good_email_passes() -> None:
    assert validate_user({"email": "a@b.co"}) == []


@pytest.mark.parametrize("bad_phone", [
    "123",
    "phone-number",
    "++++",
])
def test_bad_phone_is_flagged(bad_phone: str) -> None:
    warnings = validate_user({"phone": bad_phone})
    assert any("phone" in w for w in warnings), warnings


@pytest.mark.parametrize("good_phone", [
    "+375291234567",
    "+48 123 456 789",
    "+1-800-555-0199",
])
def test_good_phone_passes(good_phone: str) -> None:
    # +375291234567 is also our placeholder example, so skip that special case
    # by also setting other fields so the placeholder detection is
    # specifically what we're testing for elsewhere.
    if good_phone == "+375291234567":
        warnings = validate_user({"phone": good_phone, "firstName": "Not-Ivan"})
        # +375291234567 is the placeholder example - it should warn for that
        # reason, but NOT for "looks-like-phone" reason.
        assert any("example value" in w for w in warnings)
        assert not any("does not look like a phone number" in w for w in warnings)
    else:
        assert validate_user({"phone": good_phone}) == []


def test_placeholder_values_are_flagged() -> None:
    user = {
        "firstName": "Ivan",
        "lastName": "Ivanov",
        "email": "ivan.ivanov@example.com",
    }
    warnings = validate_user(user)
    keys_flagged = {w.split(".")[1].split(" ")[0] for w in warnings if w.startswith("user.")}
    # All three placeholder fields should be flagged.
    assert "firstName" in keys_flagged
    assert "lastName" in keys_flagged
    assert "email" in keys_flagged


def test_empty_user_has_no_warnings() -> None:
    # Empty values are not validated - if the user opens the wizard and
    # leaves a field blank, that's their problem, not a warning.
    assert validate_user({}) == []
    assert validate_user({"email": "", "phone": "", "dateOfBirth": ""}) == []
