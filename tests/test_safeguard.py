"""Tests for the click safeguard.

The whole point of the helper is that the human - not the script - presses
submit / book / pay / confirm buttons. The safeguard MUST refuse such
targets, including the Russian variants the client uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vfs_helper import DANGEROUS_CLICK_PATTERN, is_dangerous_click_target  # noqa: E402


@pytest.mark.parametrize("text", [
    "Submit",
    "submit application",
    "Book Appointment",
    "BOOK NOW",
    "Pay",
    "Pay now",
    "Make Payment",
    "Confirm Appointment",
    "Finalize Booking",
    "Proceed to payment",
    "\u041f\u043e\u0434\u0430\u0442\u044c \u0437\u0430\u044f\u0432\u043b\u0435\u043d\u0438\u0435",
    "\u0417\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f",
    "\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c \u0437\u0430\u043f\u0438\u0441\u044c",
    "\u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c",
    "\u041e\u043f\u043b\u0430\u0442\u0430",
    "\u0411\u0440\u043e\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0441\u043b\u043e\u0442",
    "\u0413\u043e\u0442\u043e\u0432\u043e",
])
def test_dangerous_click_targets_are_blocked(text: str) -> None:
    assert is_dangerous_click_target(text), f"Should refuse to click: {text!r}"


@pytest.mark.parametrize("text", [
    "",
    "Cancel",
    "Back",
    "Continue editing",
    "\u041d\u0430\u0437\u0430\u0434",
    "\u041e\u0442\u043c\u0435\u043d\u0430",
    "Show password",
    "Choose visa category",
    "Open calendar",
    "\u0412\u044b\u0431\u0440\u0430\u0442\u044c \u0434\u0430\u0442\u0443",
])
def test_safe_click_targets_are_allowed(text: str) -> None:
    assert not is_dangerous_click_target(text), \
        f"Should be allowed to click: {text!r}"


def test_pattern_is_case_insensitive() -> None:
    assert DANGEROUS_CLICK_PATTERN.search("SUBMIT")
    assert DANGEROUS_CLICK_PATTERN.search("sUbMiT")


@pytest.mark.parametrize("selector", [
    'button:has-text("Submit")',
    'button:has-text("\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c")',
    'a:has-text("Pay now")',
    'div[role="button"]:has-text("Confirm")',
    '#book-appointment',
    '.btn-finalize-booking',
    'button[name="confirm"]',
    'input[value="\u0417\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f"]',
])
def test_dangerous_words_in_selector_blocked(selector: str) -> None:
    """The 'click' selector itself is scanned for dangerous keywords.

    This is the second line of defence after element-text inspection: when
    the actual button lives inside a cross-origin iframe (e.g. Cloudflare's
    challenge), Playwright can't see its inner_text, but the user's CSS
    selector still contains the suspicious word - and we refuse anyway.
    """
    assert is_dangerous_click_target(selector), \
        f"Selector should be refused: {selector!r}"


@pytest.mark.parametrize("selector", [
    'input[name="firstName"]',
    'mat-select[formcontrolname="nationality"]',
    'label',
    'h2',
    'div.example-class',
    '.field input',
])
def test_safe_selectors_not_blocked(selector: str) -> None:
    assert not is_dangerous_click_target(selector), \
        f"Selector should not be refused: {selector!r}"
