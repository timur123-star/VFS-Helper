"""VFS Global form-fill helper (human-in-the-loop).

A semi-automated desktop assistant for the VFS Global visa portal.

Scope (intentionally limited):
- Opens a real Chromium / Chrome window (headful) using Playwright.
- Navigates to the configured start URL.
- Waits for the user to handle login, Cloudflare/CAPTCHA and slot selection
  manually.
- On user command from the terminal, autofills personal data fields from
  config.json into whatever form is currently visible (with proper Angular
  event dispatch so the framework's FormControl picks the values up).

Explicit non-goals:
- Does NOT solve CAPTCHA or bypass any anti-bot protection.
- Does NOT auto-click any submit / book / pay / confirm button. The `click`
  command has a hard block-list on these.
- Does NOT poll, retry or run slot-catching loops.

The script is a typing-saver for the human operator, nothing more.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue, Empty
from typing import Any

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


HERE = Path(__file__).resolve().parent

# Anything on a page whose visible text matches this pattern will be refused
# by the `click` command. The whole point of this tool is that the human
# presses these buttons, not the bot.
DANGEROUS_CLICK_PATTERN = re.compile(
    r"\b("
    r"submit|book|booking|pay|payment|confirm|finalize|finish|proceed|"
    r"\u043f\u043e\u0434\u0430\u0442\u044c|\u043f\u043e\u0434\u0430\u0447\u0430|"
    r"\u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f|"
    r"\u0437\u0430\u043f\u0438\u0441\u044c|"
    r"\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c|"
    r"\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u044e|"
    r"\u043e\u043f\u043b\u0430\u0442\u0430|\u043e\u043f\u043b\u0430\u0442\u0438\u0442\u044c|"
    r"\u0431\u0440\u043e\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c|"
    r"\u0431\u0440\u043e\u043d\u044c|\u0433\u043e\u0442\u043e\u0432\u043e"
    r")\b",
    re.IGNORECASE,
)


# Init script that hides the most obvious "this is automation" markers.
# This does NOT defeat Cloudflare Turnstile (the user still solves it
# manually as agreed) — it just keeps the session from looking weird to
# Angular validators and analytics on the subsequent steps.
STEALTH_INIT_JS = """
(() => {
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    } catch (e) {}
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ru-RU', 'ru', 'en-US', 'en'],
        });
    } catch (e) {}
    try {
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
    } catch (e) {}
    try {
        const origQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (origQuery) {
            window.navigator.permissions.query = (p) => (
                p && p.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : origQuery(p)
            );
        }
    } catch (e) {}
    try {
        if (window.chrome === undefined) {
            window.chrome = { runtime: {} };
        }
    } catch (e) {}
})();
"""


# JavaScript helpers run in page context.
INSPECT_JS = r"""
() => {
    function getLabel(el) {
        // 1. <label for="id">
        if (el.id) {
            const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (lab) return (lab.textContent || '').trim().slice(0, 80);
        }
        // 2. aria-labelledby
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const labEl = document.getElementById(labelledBy);
            if (labEl) return (labEl.textContent || '').trim().slice(0, 80);
        }
        // 3. aria-label
        const aria = el.getAttribute('aria-label');
        if (aria) return aria.trim().slice(0, 80);
        // 4. Closest Angular Material <mat-form-field> with <mat-label>
        const mff = el.closest && el.closest('mat-form-field');
        if (mff) {
            const matLab = mff.querySelector('mat-label, label');
            if (matLab) return (matLab.textContent || '').trim().slice(0, 80);
        }
        // 5. Parent <label>
        const parentLab = el.closest && el.closest('label');
        if (parentLab) {
            return (parentLab.textContent || '').trim().slice(0, 80);
        }
        return '';
    }

    function isVisible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;
        const st = window.getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') {
            return false;
        }
        return true;
    }

    const out = [];
    const selector = [
        'input', 'select', 'textarea',
        'mat-select', '[role="combobox"]',
        '[contenteditable="true"]',
    ].join(',');
    const nodes = document.querySelectorAll(selector);
    for (const el of nodes) {
        if (!isVisible(el)) continue;
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (type === 'hidden') continue;
        out.push({
            tag: el.tagName.toLowerCase(),
            type: type,
            name: el.getAttribute('name') || '',
            id: el.id || '',
            placeholder: el.getAttribute('placeholder') || '',
            formControlName: el.getAttribute('formcontrolname') || '',
            ngReflectName: el.getAttribute('ng-reflect-name') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            role: el.getAttribute('role') || '',
            label: getLabel(el),
            disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
            readonly: el.readOnly === true || el.getAttribute('readonly') !== null,
        });
    }
    return out;
}
"""

# After .fill() Angular's reactive forms do not always update because the
# DOM `input` event is not always dispatched in a way Angular's zone catches.
# Dispatching both `input` and `change` explicitly with bubbles=true makes
# FormControl pick up the value reliably.
DISPATCH_EVENTS_JS = r"""
(el) => {
    if (!el) return false;
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true, composed: true }));
    return true;
}
"""


@dataclass
class Config:
    start_url: str = ""
    user: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, list[str]] = field(default_factory=dict)
    profiles: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    browser: dict[str, Any] = field(default_factory=dict)
    typing: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Config":
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            start_url=raw.get("start_url", ""),
            user=raw.get("user", {}) or {},
            fields=raw.get("fields", {}) or {},
            profiles=raw.get("profiles", {}) or {},
            browser=raw.get("browser", {}) or {},
            typing=raw.get("typing", {}) or {},
        )

    def selectors_for(self, key: str, profile: str | None) -> list[str]:
        """Return selector list for the given field key, considering profile.

        Profile-specific selectors come first (more specific), then fall back
        to the top-level `fields`.
        """
        out: list[str] = []
        if profile and profile in self.profiles:
            out.extend(self.profiles[profile].get(key, []) or [])
        out.extend(self.fields.get(key, []) or [])
        # de-dup while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for sel in out:
            if sel not in seen:
                seen.add(sel)
                unique.append(sel)
        return unique


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VFS Global form-fill helper (human-in-the-loop).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=HERE / "config.json",
        help="Path to config JSON (default: ./config.json).",
    )
    parser.add_argument(
        "--example-config",
        action="store_true",
        help="Print the example config to stdout and exit.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Interactive wizard: ask for your data and write config.json. "
             "No browser is launched.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Diagnostics: verify Python, Playwright, Chrome, config. "
             "No browser is launched.",
    )
    parser.add_argument(
        "--no-stealth",
        action="store_true",
        help="Disable the stealth init script (for debugging).",
    )
    return parser.parse_args()


# Keys we explicitly know about and validate / wizard.
# (key, label, default, validator-or-None)
WIZARD_FIELDS: list[tuple[str, str, str]] = [
    ("firstName", "Имя (как в паспорте, латиницей)", ""),
    ("lastName", "Фамилия (как в паспорте, латиницей)", ""),
    ("middleName", "Отчество (если есть, иначе пусто)", ""),
    ("passportNumber", "Номер паспорта (например, MP1234567)", ""),
    ("dateOfBirth", "Дата рождения в формате DD/MM/YYYY", ""),
    ("passportIssueDate", "Дата выдачи паспорта DD/MM/YYYY", ""),
    ("passportExpiryDate", "Срок действия паспорта DD/MM/YYYY", ""),
    ("email", "Email (контактный)", ""),
    ("phone", "Телефон с кодом страны (например, +375291234567)", ""),
    ("nationality", "Гражданство (English, как в выпадающем меню VFS)", "Belarusian"),
    ("gender", "Пол: Male / Female", "Male"),
    ("loginEmail", "Email для логина на visa.vfsglobal.com", ""),
    ("loginPassword", "Пароль от аккаунта VFS", ""),
]


DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{6,}$")


def validate_user(user: dict[str, Any]) -> list[str]:
    """Return a list of warnings about the user config.

    Empty list == clean. The script doesn't fail on warnings, just prints them.
    """
    warnings: list[str] = []
    placeholders = {
        "Ivan", "Ivanov", "Ivanovich", "AB1234567",
        "ivan.ivanov@example.com", "REPLACE_WITH_YOUR_PASSWORD",
        "+375291234567",
    }
    for k, v in user.items():
        if v in placeholders:
            warnings.append(f"user.{k} is still set to the example value {v!r}")

    for date_key in ("dateOfBirth", "passportIssueDate", "passportExpiryDate"):
        val = user.get(date_key)
        if val and not DATE_RE.match(str(val)):
            warnings.append(
                f"user.{date_key}={val!r} does not look like DD/MM/YYYY "
                f"(VFS expects this format)."
            )

    email = user.get("email") or user.get("loginEmail")
    if email and not EMAIL_RE.match(str(email)):
        warnings.append(f"email {email!r} does not look like a valid address.")

    phone = user.get("phone")
    if phone and not PHONE_RE.match(str(phone)):
        warnings.append(
            f"phone {phone!r} does not look like a phone number "
            f"(expected something like +375291234567)."
        )

    return warnings


def load_config_or_die(path: Path) -> Config:
    if not path.exists():
        example = HERE / "config.example.json"
        sys.stderr.write(
            f"Config file not found: {path}\n"
            f"\n"
            f"First-time setup - run the wizard:\n"
            f"    python vfs_helper.py --setup\n"
            f"\n"
            f"Or copy the example and edit by hand:\n"
            f"    copy {example.name} config.json   (Windows)\n"
            f"    cp   {example.name} config.json   (macOS / Linux)\n"
        )
        sys.exit(2)
    try:
        return Config.load(path)
    except json.JSONDecodeError as exc:
        sys.stderr.write(
            f"Invalid JSON in {path}: {exc}\n"
            f"Common causes: missing comma, trailing comma, smart quotes.\n"
            f"Re-run 'python vfs_helper.py --setup' to regenerate the file.\n"
        )
        sys.exit(2)


def run_setup_wizard(config_path: Path) -> int:
    """Interactive config builder.

    Walks through WIZARD_FIELDS, asks the user for each value (with current
    value as default if config.json already exists), validates inline, then
    writes config.json next to config.example.json defaults.
    """
    example_path = HERE / "config.example.json"
    if not example_path.exists():
        sys.stderr.write(f"Example config missing: {example_path}\n")
        return 2

    with example_path.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as fh:
                existing = json.load(fh).get("user", {}) or {}
        except Exception:
            existing = {}

    print()
    print("=" * 60)
    print(" VFS Helper - setup wizard")
    print("=" * 60)
    print(" Enter your data. Press Enter to keep the current value.")
    print(" Press Ctrl+C at any time to abort without saving.")
    print()

    user: dict[str, str] = dict(cfg.get("user", {}))

    try:
        for key, prompt, default in WIZARD_FIELDS:
            current = existing.get(key, default)
            display = current if current else "(empty)"
            value = input(f"  {prompt}\n    [{display}]: ").strip()
            if not value:
                value = str(current) if current else ""
            user[key] = value
    except (KeyboardInterrupt, EOFError):
        print("\nAborted, config.json was NOT written.")
        return 1

    cfg["user"] = user

    warnings = validate_user(user)
    if warnings:
        print("\nValidation warnings (config will still be saved):")
        for w in warnings:
            print(f"  ! {w}")
        print()

    # Pretty-write the JSON with stable key order.
    config_path.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nSaved {config_path}")
    print("You can now run:")
    print("    python vfs_helper.py")
    return 0


def run_health_check(config_path: Path) -> int:
    """Diagnostics: verify Python, Playwright, Chrome, config validity.

    Returns 0 if everything looks OK, 1 if anything is missing.
    """
    ok = True

    def line(label: str, value: str, good: bool) -> None:
        nonlocal ok
        mark = "OK " if good else "FAIL"
        if not good:
            ok = False
        print(f"  [{mark}] {label:30s} {value}")

    print()
    print("VFS Helper - health check")
    print("-" * 60)

    line("Python version", sys.version.split()[0],
         sys.version_info >= (3, 10))

    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright as _sp  # noqa: F401
        line("playwright module", "importable", True)
    except Exception as exc:
        line("playwright module", f"NOT importable ({exc})", False)
        print("\nFix: pip install -r requirements.txt")
        return 1

    # Try to actually launch a context to check Chrome / Chromium availability.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
                line("Google Chrome (channel)", "found", True)
                browser.close()
            except Exception as exc_chrome:
                line("Google Chrome (channel)", f"not found ({exc_chrome.__class__.__name__})", False)
                try:
                    browser = pw.chromium.launch(headless=True)
                    line("Bundled Chromium", "found (fallback)", True)
                    browser.close()
                except Exception as exc_chromium:
                    line("Bundled Chromium", f"not installed ({exc_chromium})", False)
                    print("\nFix: python -m playwright install chromium")
                    return 1
    except Exception as exc:
        line("Playwright launch", f"FAILED ({exc})", False)
        return 1

    if config_path.exists():
        try:
            cfg = Config.load(config_path)
            line("config.json", f"loaded ({config_path.name})", True)
            warnings = validate_user(cfg.user)
            if warnings:
                line("config validation", f"{len(warnings)} warning(s)", False)
                for w in warnings:
                    print(f"        ! {w}")
            else:
                line("config validation", "clean", True)
        except Exception as exc:
            line("config.json", f"INVALID ({exc})", False)
    else:
        line("config.json", "missing (run --setup)", False)

    print("-" * 60)
    print("Status:", "READY" if ok else "NOT READY - see FAIL lines above")
    return 0 if ok else 1


def setup_logging() -> logging.Logger:
    """Configure file logging with rotation.

    Each session writes to logs/vfs-helper-<timestamp>.log, but we also keep
    logs/vfs-helper.log as a rolling file (10 MB cap, 5 backups) so we can
    deploy the tool and have stable log paths without leaking disk over time.
    """
    from logging.handlers import RotatingFileHandler

    logs_dir = HERE / "logs"
    logs_dir.mkdir(exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    session_path = logs_dir / f"vfs-helper-{stamp}.log"
    rolling_path = logs_dir / "vfs-helper.log"

    logger = logging.getLogger("vfs_helper")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Per-session log file (always fresh).
    session_fh = logging.FileHandler(session_path, encoding="utf-8")
    session_fh.setLevel(logging.DEBUG)
    session_fh.setFormatter(fmt)
    logger.addHandler(session_fh)

    # Rolling log file (10 MB cap, 5 backups) for long-term debugging.
    rolling_fh = RotatingFileHandler(
        rolling_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    rolling_fh.setLevel(logging.INFO)
    rolling_fh.setFormatter(fmt)
    logger.addHandler(rolling_fh)

    logger.info("=== vfs_helper started, log=%s ===", session_path)
    return logger


def launch_context(
    pw: Playwright,
    cfg: Config,
    stealth: bool,
    logger: logging.Logger,
) -> BrowserContext:
    browser_cfg = cfg.browser or {}
    user_data_dir = HERE / browser_cfg.get("user_data_dir", "browser_profile")
    user_data_dir.mkdir(parents=True, exist_ok=True)

    viewport = browser_cfg.get("viewport") or {"width": 1366, "height": 900}
    locale = browser_cfg.get("locale", "ru-RU")
    headless = bool(browser_cfg.get("headless", False))
    channel = browser_cfg.get("channel", "chrome")

    launch_kwargs: dict[str, Any] = dict(
        user_data_dir=str(user_data_dir),
        headless=headless,
        viewport=viewport,
        locale=locale,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )

    # Prefer real Google Chrome (less detectable than bundled Chromium).
    # Fall back to bundled Chromium if Chrome is not installed.
    try:
        if channel:
            launch_kwargs["channel"] = channel
        context = pw.chromium.launch_persistent_context(**launch_kwargs)
        logger.info("Launched with channel=%r", channel)
    except PlaywrightError as exc:
        logger.warning("channel=%r failed (%s), falling back to bundled chromium", channel, exc)
        launch_kwargs.pop("channel", None)
        context = pw.chromium.launch_persistent_context(**launch_kwargs)

    if stealth:
        context.add_init_script(STEALTH_INIT_JS)
        logger.info("Stealth init script installed.")

    return context


def get_active_page(context: BrowserContext) -> Page:
    """Return the page the user is currently looking at.

    Picks the most recently opened page so the user can switch tabs naturally.
    """
    pages = context.pages
    if not pages:
        return context.new_page()
    return pages[-1]


def _dispatch_input_change(locator: Locator) -> None:
    """Make Angular / Vue / React form bindings register the new value.

    Without this many SPA forms keep their FormControl as "untouched / pristine"
    and silently discard the typed value on submit.
    """
    try:
        locator.evaluate(DISPATCH_EVENTS_JS)
    except Exception:
        pass


def _fill_text_input(
    locator: Locator,
    value: str,
    typing_cfg: dict[str, Any],
) -> None:
    """Fill a regular text-like input, robustly for SPA frameworks.

    Strategy:
      1. Click into it (so the field is focused, like a human).
      2. Triple-click to select existing content, then press Delete.
      3. Either bulk-fill or type character-by-character if the form needs it
         (e.g. masked passport inputs that validate on keystroke).
      4. Dispatch input/change/blur events for Angular FormControl.
    """
    use_typing = bool(typing_cfg.get("simulate_human", False))
    delay_ms = int(typing_cfg.get("keystroke_delay_ms", 30))

    locator.click(timeout=2000)
    try:
        locator.press("Control+A", timeout=500)
        locator.press("Delete", timeout=500)
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    if use_typing:
        locator.press_sequentially(value, delay=delay_ms, timeout=10000)
    else:
        locator.fill(value, timeout=3000)

    _dispatch_input_change(locator)


def _fill_mat_select(page: Page, locator: Locator, value: str) -> bool:
    """Open an Angular Material <mat-select> and pick the option by text."""
    try:
        locator.scroll_into_view_if_needed(timeout=2000)
        locator.click(timeout=2000)
    except Exception:
        return False

    # Wait for the CDK overlay to appear with mat-option entries.
    option = page.locator(
        f"mat-option:has-text({value!r}), "
        f"[role='option']:has-text({value!r})"
    ).first
    try:
        option.wait_for(state="visible", timeout=3000)
        option.click(timeout=2000)
        return True
    except PlaywrightTimeoutError:
        # Close the dropdown so we don't leave the UI in a weird state.
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False
    except Exception:
        return False


def _fill_native_select(locator: Locator, value: str) -> bool:
    """Try selecting by label, then by value, on a <select>."""
    for fn in ("label", "value"):
        try:
            if fn == "label":
                locator.select_option(label=value, timeout=2000)
            else:
                locator.select_option(value=value, timeout=2000)
            _dispatch_input_change(locator)
            return True
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return False


# Strings that count as "true" when filling a checkbox.
_TRUTHY = {"true", "yes", "y", "1", "on", "checked", "agree", "accept"}
_FALSY = {"false", "no", "n", "0", "off", "unchecked", "disagree", "decline"}


def _coerce_bool(value: Any) -> bool | None:
    """Map user values to True/False for checkbox state. None if ambiguous."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in _TRUTHY:
            return True
        if norm in _FALSY:
            return False
    return None


def _fill_checkbox(locator: Locator, value: Any) -> bool:
    """Set a checkbox to the desired state.

    Accepts bool, int, or string ('true'/'yes'/'1'/'agree' etc.). For
    unrecognized values, default to True (checking the box, which is what
    most VFS 'I agree' checkboxes need).
    """
    desired = _coerce_bool(value)
    if desired is None:
        # Unknown truthy/falsy spelling - assume the user means "check it".
        desired = True
    try:
        if desired:
            locator.check(timeout=2000)
        else:
            locator.uncheck(timeout=2000)
        return True
    except Exception:
        return False


def _fill_radio_group(page: Page, locator: Locator, value: str) -> bool:
    """Select a radio button by value, id-substring, or sibling label text.

    The locator may point to the first radio of a group - we walk to all
    radios with the same `name`, score each by value/id/aria-label/sibling
    label text against the desired value, and click the best match.
    """
    try:
        name = locator.get_attribute("name", timeout=300)
    except Exception:
        name = None

    if name:
        group = page.locator(f"input[type='radio'][name={name!r}]")
    else:
        # No name attribute - just try this single radio.
        group = locator

    needle = str(value).strip().lower()
    n = group.count()
    if n == 0:
        return False

    candidates: list[tuple[int, int]] = []  # (score, index)
    for i in range(n):
        item = group.nth(i)
        try:
            val = (item.get_attribute("value", timeout=200) or "").lower()
        except Exception:
            val = ""
        try:
            ident = (item.get_attribute("id", timeout=200) or "").lower()
        except Exception:
            ident = ""
        try:
            aria = (item.get_attribute("aria-label", timeout=200) or "").lower()
        except Exception:
            aria = ""

        label_text = ""
        # 1. Try <label for="id"> association
        if ident:
            try:
                lab = page.locator(f"label[for={ident!r}]").first
                label_text = (lab.inner_text(timeout=300) or "").lower()
            except Exception:
                label_text = ""
        # 2. Try the closest ancestor <label> (wrapping-label pattern).
        #    VFS forms and many UI kits use both styles.
        if not label_text:
            try:
                wrap = item.locator("xpath=ancestor::label[1]").first
                wrap.wait_for(state="attached", timeout=200)
                label_text = (wrap.inner_text(timeout=300) or "").lower()
            except Exception:
                pass

        score = 0
        if val == needle:
            score = 100
        elif label_text == needle or aria == needle:
            score = 95
        elif val and needle in val:
            score = 60
        elif label_text and needle in label_text:
            score = 50
        elif aria and needle in aria:
            score = 45
        elif ident and needle in ident:
            score = 30

        if score > 0:
            candidates.append((score, i))

    if not candidates:
        return False
    candidates.sort(reverse=True)
    idx = candidates[0][1]
    try:
        group.nth(idx).check(timeout=2000)
        return True
    except Exception:
        # Some radio implementations need a click on the parent label.
        try:
            ident = (group.nth(idx).get_attribute("id", timeout=200) or "")
            if ident:
                page.locator(f"label[for={ident!r}]").first.click(timeout=2000)
                return True
        except Exception:
            pass
        return False


def _resolve_upload_path(raw: str | Path) -> Path | None:
    """Resolve an upload path relative to HERE (project root), then absolute."""
    if not raw:
        return None
    p = Path(str(raw))
    if not p.is_absolute():
        p = HERE / p
    return p if p.exists() else None


def _fill_file_upload(locator: Locator, value: str) -> tuple[bool, str]:
    """Set the file on an <input type='file'>.

    `value` is treated as a path relative to the project root (or absolute).
    Returns (ok, message_or_path_used).
    """
    resolved = _resolve_upload_path(value)
    if resolved is None:
        return False, f"file not found: {value!r}"
    try:
        locator.set_input_files(str(resolved), timeout=5000)
        return True, str(resolved)
    except Exception as exc:
        return False, f"set_input_files failed: {exc}"


# Class names of the autocomplete-search-box wrapper used by vfsglobal.com
# (and many other corporate Angular CMSes). The pattern is:
#
#   <div class="autocomplete-search-box">   (or "search_dropdown")
#     <div class="input-wrapper"><input class="search-input"></div>
#     <div class="selected-wrapper"><div class="selected">...</div></div>
#     <div class="toggle-button-wrapper"><div class="toggle-button"></div></div>
#     <div class="list-wrapper">
#       <ul><li><div class="text">Belarus</div></li>...</ul>
#     </div>
#   </div>
#
# The .selected-wrapper covers the real <input>, so a plain locator.click()
# fails with "subtree intercepts pointer events". The cure is to click the
# toggle first, which removes the overlay and exposes the input + list.
AUTOCOMPLETE_CONTAINER_XPATH = (
    "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), "
    "' autocomplete-search-box ') or contains(concat(' ', normalize-space(@class), ' '), "
    "' search_dropdown ') or contains(concat(' ', normalize-space(@class), ' '), "
    "' search-dropdown ')][1]"
)


def _find_autocomplete_container(locator: Locator) -> Locator | None:
    """If the input lives inside a .autocomplete-search-box, return that container."""
    try:
        cand = locator.locator(AUTOCOMPLETE_CONTAINER_XPATH).first
        cand.wait_for(state="attached", timeout=300)
        return cand
    except PlaywrightTimeoutError:
        return None
    except Exception:
        return None


def _fill_autocomplete_dropdown(
    page: Page,
    container: Locator,
    value: str,
    typing_cfg: dict[str, Any],
) -> bool:
    """Drive a custom autocomplete-search-box dropdown end-to-end.

      1. Click .toggle-button (or container) to open the panel.
      2. Type the value into .search-input - the list filters live.
      3. Click the option whose text matches (case-insensitive).

    Returns True on success.
    """
    # Step 1: open the panel
    opened = False
    for opener in [
        ".toggle-button-wrapper", ".toggle-button",
        ".icon-arrow-point-down", ".input-wrapper",
    ]:
        try:
            opener_loc = container.locator(opener).first
            opener_loc.wait_for(state="visible", timeout=800)
            opener_loc.click(timeout=1500)
            opened = True
            break
        except Exception:
            continue
    if not opened:
        # Fall back: click the container itself.
        try:
            container.click(timeout=1500, position={"x": 10, "y": 10})
            opened = True
        except Exception:
            return False

    # Step 2: type into the now-visible search input
    try:
        input_field = container.locator("input.search-input, input[type='text']").first
        input_field.wait_for(state="visible", timeout=1500)
        # Clear and type.
        try:
            input_field.click(timeout=1000)
            input_field.press("Control+A", timeout=400)
            input_field.press("Delete", timeout=400)
        except Exception:
            pass
        delay = int(typing_cfg.get("keystroke_delay_ms", 30))
        input_field.press_sequentially(value, delay=delay, timeout=8000)
        _dispatch_input_change(input_field)
    except Exception:
        return False

    # Step 3: click the matching option. The dropdown filters live, so
    # waiting ~600ms lets the filter settle before we click.
    page.wait_for_timeout(500)
    val_quoted = value
    for opt_sel in [
        f"li:has-text({val_quoted!r}) >> nth=0",
        f"li .text:has-text({val_quoted!r}) >> nth=0",
        f"li[data-index]:has-text({val_quoted!r}) >> nth=0",
        f"[role='option']:has-text({val_quoted!r}) >> nth=0",
    ]:
        try:
            opt = container.locator(opt_sel).first
            opt.wait_for(state="visible", timeout=1500)
            opt.click(timeout=1500)
            return True
        except Exception:
            continue

    # No option clicked - close the panel to leave the UI sane.
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def fill_one_field(
    page: Page,
    selectors: list[str],
    value: Any,
    typing_cfg: dict[str, Any],
    logger: logging.Logger,
) -> tuple[str | None, str]:
    """Try each selector in order; fill the first visible & editable match.

    Returns (selector_that_worked, kind) or (None, reason).

    `kind` is one of:
      'text', 'select', 'mat-select', 'autocomplete-dropdown',
      'checkbox', 'radio', 'file'
    """
    # Empty string skip rule - but allow `False` for unchecking checkboxes
    # and bools generally.
    if value is None:
        return None, "empty value"
    if isinstance(value, str) and value == "":
        return None, "empty value"

    last_reason = "no selectors matched"
    for selector in selectors:
        try:
            locator: Locator = page.locator(selector).first
            try:
                locator.wait_for(state="attached", timeout=750)
            except PlaywrightTimeoutError:
                last_reason = f"{selector}: not attached within 750ms"
                continue

            tag = (locator.evaluate("el => el.tagName.toLowerCase()") or "").lower()

            input_type = ""
            if tag == "input":
                try:
                    input_type = (
                        locator.get_attribute("type", timeout=200) or "text"
                    ).lower()
                except Exception:
                    input_type = "text"

            # File upload: <input type="file">
            if tag == "input" and input_type == "file":
                ok, msg = _fill_file_upload(locator, str(value))
                if ok:
                    return selector, "file"
                last_reason = f"{selector}: {msg}"
                continue

            # Checkbox: <input type="checkbox">
            if tag == "input" and input_type == "checkbox":
                if _fill_checkbox(locator, value):
                    return selector, "checkbox"
                last_reason = f"{selector}: checkbox could not be toggled"
                continue

            # Radio: <input type="radio"> - walk the group, pick the right one
            if tag == "input" and input_type == "radio":
                if _fill_radio_group(page, locator, str(value)):
                    return selector, "radio"
                last_reason = (
                    f"{selector}: no radio in the group matched value {value!r}"
                )
                continue

            if tag == "select":
                if _fill_native_select(locator, str(value)):
                    return selector, "select"
                last_reason = f"{selector}: <select> could not match label/value {value!r}"
                continue

            if tag == "mat-select" or (
                locator.get_attribute("role", timeout=200) == "combobox"
            ):
                if _fill_mat_select(page, locator, str(value)):
                    return selector, "mat-select"
                last_reason = f"{selector}: option {value!r} not found in dropdown"
                continue

            # Detect custom autocomplete-search-box dropdowns (vfsglobal.com
            # country selectors, many other Angular CMS dropdowns). The bare
            # <input> is covered by a .selected-wrapper overlay; a plain
            # click() throws 'intercepts pointer events'. The dedicated
            # handler clicks the toggle, types into the now-visible input,
            # and clicks the matching <li>.
            container = _find_autocomplete_container(locator)
            if container is not None:
                if _fill_autocomplete_dropdown(page, container, str(value), typing_cfg):
                    return selector, "autocomplete-dropdown"
                last_reason = (
                    f"{selector}: autocomplete-search-box detected but option "
                    f"{value!r} could not be selected"
                )
                continue

            # input / textarea / contenteditable / generic.
            try:
                locator.wait_for(state="visible", timeout=750)
            except PlaywrightTimeoutError:
                last_reason = f"{selector}: not visible within 750ms"
                continue
            try:
                if not locator.is_editable(timeout=500):
                    last_reason = f"{selector}: not editable (disabled/readonly?)"
                    continue
            except PlaywrightTimeoutError:
                last_reason = f"{selector}: editable check timed out"
                continue

            _fill_text_input(locator, str(value), typing_cfg)
            return selector, "text"

        except PlaywrightTimeoutError as exc:
            last_reason = f"{selector}: timeout ({exc})"
            logger.debug("fill timeout on %s: %s", selector, exc)
            continue
        except Exception as exc:
            last_reason = f"{selector}: {exc}"
            logger.debug("fill error on %s: %s", selector, exc)
            continue

    return None, last_reason


def cmd_fill(
    page: Page,
    cfg: Config,
    profile: str | None,
    logger: logging.Logger,
) -> None:
    user = cfg.user
    filled: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str]] = []

    if profile:
        print(f"Using profile {profile!r} (with fallback to top-level fields).")

    for key, value in user.items():
        selectors = cfg.selectors_for(key, profile)
        if not selectors:
            skipped.append((key, "no selectors in config (fields / profiles)"))
            continue
        matched, reason = fill_one_field(page, selectors, str(value), cfg.typing, logger)
        if matched:
            filled.append((key, matched, reason))
            logger.info("filled %s via %s (%s)", key, matched, reason)
        else:
            skipped.append((key, reason))
            logger.info("skipped %s: %s", key, reason)

    print()
    if filled:
        print("Filled:")
        for key, selector, kind in filled:
            print(f"  - {key:20s} via {selector}   [{kind}]")
    if skipped:
        print("Skipped:")
        for key, reason in skipped:
            print(f"  - {key:20s}  ({reason})")
    if not filled and not skipped:
        print("Nothing to fill - config.user is empty.")
    print()


def cmd_inspect(page: Page) -> None:
    try:
        fields = page.evaluate(INSPECT_JS)
    except Exception as exc:
        print(f"Inspect failed: {exc}")
        return

    if not fields:
        print("No visible input/select/textarea/mat-select found on this page.")
        return

    print()
    print(f"Visible form fields on {page.url}:")
    for i, f in enumerate(fields, 1):
        bits = [f"<{f['tag']}"]
        if f.get("type"):
            bits.append(f"type={f['type']!r}")
        if f.get("name"):
            bits.append(f"name={f['name']!r}")
        if f.get("id"):
            bits.append(f"id={f['id']!r}")
        if f.get("formControlName"):
            bits.append(f"formControlName={f['formControlName']!r}")
        if f.get("ngReflectName") and f.get("ngReflectName") != f.get("formControlName"):
            bits.append(f"ngReflectName={f['ngReflectName']!r}")
        if f.get("placeholder"):
            bits.append(f"placeholder={f['placeholder']!r}")
        if f.get("ariaLabel"):
            bits.append(f"aria-label={f['ariaLabel']!r}")
        if f.get("role"):
            bits.append(f"role={f['role']!r}")
        if f.get("label"):
            bits.append(f"label={f['label']!r}")
        flags = []
        if f.get("disabled"):
            flags.append("disabled")
        if f.get("readonly"):
            flags.append("readonly")
        if flags:
            bits.append(f"[{','.join(flags)}]")
        print(f"  [{i:2d}] " + " ".join(bits) + ">")

    print()
    print("Tip: prefer formControlName (Angular VFS forms) > id > name > label.")
    print("     Example selector: input[formcontrolname='firstName']")
    print()


def cmd_screenshot(page: Page) -> None:
    out_dir = HERE / "screenshots"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"shot-{int(time.time())}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"Saved {path}")


def cmd_dump_html(page: Page) -> None:
    out_dir = HERE / "dumps"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"page-{int(time.time())}.html"
    try:
        html = page.content()
    except Exception as exc:
        print(f"dump failed: {exc}")
        return
    path.write_text(html, encoding="utf-8")
    print(f"Saved {path}  ({len(html)} bytes)")


def cmd_scroll(page: Page, args: str) -> None:
    """Scroll the page so fields below the fold become visible.

    Usage:
      scroll           - one viewport down
      scroll down      - same as default
      scroll up        - one viewport up
      scroll top       - jump to top
      scroll <pixels>  - scroll by N pixels (positive = down)
    """
    arg = (args or "down").strip().lower()
    try:
        if arg in ("", "down"):
            page.evaluate(
                "() => window.scrollBy(0, Math.round(window.innerHeight * 0.85))"
            )
            print("Scrolled down one viewport.")
        elif arg == "up":
            page.evaluate(
                "() => window.scrollBy(0, -Math.round(window.innerHeight * 0.85))"
            )
            print("Scrolled up one viewport.")
        elif arg == "top":
            page.evaluate("() => window.scrollTo(0, 0)")
            print("Scrolled to top.")
        elif arg.lstrip("-").isdigit():
            px = int(arg)
            page.evaluate(f"() => window.scrollBy(0, {px})")
            print(f"Scrolled by {px}px.")
        else:
            print("Usage: scroll [down|up|top|<pixels>]")
            return
    except Exception as exc:
        print(f"scroll failed: {exc}")


def cmd_goto(page: Page, url: str) -> None:
    if not url:
        print("Usage: goto <url>")
        return
    page.goto(url)
    print(f"Navigated to {page.url}")


def cmd_wait(page: Page, args: str) -> None:
    parts = args.rsplit(" ", 1)
    selector = args
    timeout_ms = 10000
    if len(parts) == 2 and parts[1].isdigit():
        selector, timeout_ms = parts[0], int(parts[1])
    if not selector:
        print("Usage: wait <selector> [timeout_ms]")
        return
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        print(f"OK: {selector} is visible.")
    except PlaywrightTimeoutError:
        print(f"TIMEOUT: {selector} not visible within {timeout_ms}ms.")
    except Exception as exc:
        print(f"wait failed: {exc}")


def cmd_upload(page: Page, args: str, logger: logging.Logger) -> None:
    """`upload <selector> <path>` - attach a file to <input type='file'>.

    Path may be absolute or relative to the project root.
    """
    if not args:
        print("Usage: upload <selector> <path>")
        return
    # Selector and path are separated by the FIRST space - tolerant of
    # selectors that contain quotes / colons / commas, less so of paths
    # with spaces. For paths with spaces, wrap them in quotes.
    sel, _, path = args.partition(" ")
    sel = sel.strip()
    path = path.strip().strip('"').strip("'")
    if not sel or not path:
        print("Usage: upload <selector> <path>")
        return
    try:
        locator = page.locator(sel).first
        locator.wait_for(state="attached", timeout=3000)
    except PlaywrightTimeoutError:
        print(f"upload: {sel} not found.")
        return
    except Exception as exc:
        print(f"upload failed: {exc}")
        return
    ok, msg = _fill_file_upload(locator, path)
    if ok:
        print(f"uploaded {sel}  <-  {msg}")
        logger.info("uploaded %s <- %s", sel, msg)
    else:
        print(f"upload failed: {msg}")
        logger.warning("upload %s failed: %s", sel, msg)


def cmd_cookies(context: BrowserContext, args: str) -> None:
    """`cookies` subcommands: show | save [path] | load [path] | clear.

    - `cookies show`            - dump current cookies to stdout
    - `cookies save [path]`     - export to ./cookies/cookies-<ts>.json (or path)
    - `cookies load <path>`     - import cookies from JSON into the context
    - `cookies clear`           - delete all cookies in the context

    This is useful for transferring a Cloudflare-passed session from one
    machine to another: solve the checkbox on your laptop, `cookies save`,
    move the JSON to another box, `cookies load` there.
    """
    sub, _, rest = (args or "").strip().partition(" ")
    sub = sub.lower()
    rest = rest.strip()

    if sub in ("", "show"):
        cookies = context.cookies()
        print(f"  {len(cookies)} cookie(s) in context")
        for c in cookies[:50]:
            print(
                f"    {c.get('domain','?')}  {c.get('name','?')}={str(c.get('value',''))[:30]}..."
                f"  path={c.get('path','/')}  secure={c.get('secure',False)}  "
                f"httpOnly={c.get('httpOnly',False)}"
            )
        if len(cookies) > 50:
            print(f"    ... and {len(cookies) - 50} more")
        return

    if sub == "save":
        out_dir = HERE / "cookies"
        out_dir.mkdir(exist_ok=True)
        out_path = Path(rest) if rest else (out_dir / f"cookies-{int(time.time())}.json")
        if not out_path.is_absolute():
            out_path = HERE / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cookies = context.cookies()
        out_path.write_text(
            json.dumps(cookies, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Saved {len(cookies)} cookie(s) to {out_path}")
        return

    if sub == "load":
        if not rest:
            print("Usage: cookies load <path>")
            return
        in_path = Path(rest)
        if not in_path.is_absolute():
            in_path = HERE / in_path
        if not in_path.exists():
            print(f"cookies file not found: {in_path}")
            return
        try:
            cookies = json.loads(in_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"failed to parse cookies file: {exc}")
            return
        if not isinstance(cookies, list):
            print(f"cookies file must be a JSON array, got {type(cookies).__name__}")
            return
        try:
            context.add_cookies(cookies)
            print(f"Loaded {len(cookies)} cookie(s) into context.")
        except Exception as exc:
            print(f"add_cookies failed: {exc}")
        return

    if sub == "clear":
        try:
            context.clear_cookies()
            print("Cleared all cookies in context.")
        except Exception as exc:
            print(f"clear_cookies failed: {exc}")
        return

    print(f"Unknown cookies subcommand {sub!r}. Use: show / save [path] / load <path> / clear.")


def is_dangerous_click_target(text: str) -> bool:
    """Return True if the clickable element looks like a submit/book/pay button.

    The whole point of the helper is that the human presses these buttons,
    not the script. Refuse to click them no matter what the user types.
    """
    if not text:
        return False
    return bool(DANGEROUS_CLICK_PATTERN.search(text))


def cmd_click(page: Page, selector: str, logger: logging.Logger) -> None:
    if not selector:
        print("Usage: click <selector>")
        return

    # First line of defence: if the *selector itself* mentions a dangerous
    # word (e.g. button:has-text("Подтвердить")), refuse before we even
    # touch the page. This catches the case where the actual element is
    # in a cross-origin iframe (like Cloudflare's challenge) and the
    # post-locate text check can't see it.
    if is_dangerous_click_target(selector):
        msg = (
            f"REFUSED: selector {selector!r} mentions a submit/book/pay/"
            f"confirm-style word. This helper does not press those — "
            f"do it yourself in the browser."
        )
        print(msg)
        logger.warning(msg)
        return

    try:
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=3000)
    except PlaywrightTimeoutError:
        print(f"click: {selector} not visible.")
        return
    except Exception as exc:
        print(f"click failed: {exc}")
        return

    try:
        text = (locator.inner_text(timeout=1000) or "").strip()
    except Exception:
        text = ""

    try:
        node_type = (locator.get_attribute("type", timeout=500) or "").lower()
    except Exception:
        node_type = ""

    if node_type == "submit" or is_dangerous_click_target(text):
        msg = (
            f"REFUSED: {selector!r} looks like a submit/book/pay/confirm button "
            f"(text={text!r}, type={node_type!r}). "
            f"This helper does not click those — press it yourself in the browser."
        )
        print(msg)
        logger.warning(msg)
        return

    try:
        locator.click(timeout=3000)
        print(f"clicked {selector}  (text={text!r})")
        logger.info("clicked %s (text=%r)", selector, text)
    except Exception as exc:
        print(f"click failed: {exc}")


def print_help() -> None:
    print(
        """
Commands (type and press Enter in this terminal):

  fill, f [profile]            Autofill personal data into the current page's
                               form (text, select, mat-select, autocomplete
                               dropdowns, checkboxes, radios, file uploads).
                               If <profile> is given, selectors from
                               config.profiles override config.fields.
  inspect, i                   List visible form fields with their Angular
                               formcontrolname / id / placeholder / label.
  upload <selector> <path>     Attach a file to <input type='file'>.
                               Path relative to repo root or absolute.
  goto <url>                   Navigate the active tab to <url>.
  scroll [down|up|top|pixels]  Scroll the page (use before fill on long forms).
  wait <selector> [ms]         Wait until <selector> is visible.
  click <selector>             Click an element. REFUSED for submit / book /
                               pay / confirm / podtverdit' / zapisat'sya /
                               oplata / etc. - press those yourself.
  cookies show                 Dump cookies of the current context.
  cookies save [path]          Export cookies to JSON (default: cookies/...).
  cookies load <path>          Import cookies from a JSON file.
  cookies clear                Delete all cookies in the context.
  screenshot, s                Save a full-page screenshot to ./screenshots/.
  dump                         Save current HTML to ./dumps/.
  reload, r                    Reload the current page.
  url                          Print the current page URL.
  tabs                         List all open tabs/pages with their URLs.
  help, h, ?                   Show this help.
  quit, q, exit                Close the browser and exit.

The script never presses submit/book/confirm. That is on you.
"""
    )


def cmd_tabs(context: BrowserContext) -> None:
    pages = context.pages
    if not pages:
        print("No open pages.")
        return
    for i, p in enumerate(pages):
        marker = "*" if p is pages[-1] else " "
        try:
            print(f"  {marker} [{i}] {p.url}")
        except Exception:
            print(f"  {marker} [{i}] (url unavailable)")


def stdin_reader(q: "Queue[str]") -> None:
    """Read lines from stdin in a background thread and push to a queue."""
    try:
        for line in sys.stdin:
            q.put(line.rstrip("\n"))
    except Exception:
        pass
    q.put("__EOF__")


def run_repl(
    context: BrowserContext,
    cfg: Config,
    logger: logging.Logger,
) -> None:
    print_help()
    q: "Queue[str]" = Queue()
    t = threading.Thread(target=stdin_reader, args=(q,), daemon=True)
    t.start()

    while True:
        try:
            line = q.get(timeout=0.5)
        except Empty:
            # Pump the browser so events fire while we wait for input.
            try:
                page = get_active_page(context)
                page.wait_for_timeout(100)
            except Exception:
                pass
            continue

        if line == "__EOF__":
            print("\nstdin closed, exiting.")
            return

        cmd, _, rest = line.strip().partition(" ")
        cmd = cmd.lower()
        rest = rest.strip()

        if cmd == "":
            continue
        if cmd in ("quit", "q", "exit"):
            return
        if cmd in ("help", "h", "?"):
            print_help()
            continue
        if cmd == "tabs":
            cmd_tabs(context)
            continue

        try:
            page = get_active_page(context)
        except Exception as exc:
            print(f"No active page: {exc}")
            continue

        try:
            if cmd in ("fill", "f"):
                profile = rest or None
                cmd_fill(page, cfg, profile, logger)
            elif cmd in ("inspect", "i"):
                cmd_inspect(page)
            elif cmd in ("screenshot", "s"):
                cmd_screenshot(page)
            elif cmd == "dump":
                cmd_dump_html(page)
            elif cmd in ("reload", "r"):
                page.reload()
                print(f"Reloaded {page.url}")
            elif cmd == "url":
                print(page.url)
            elif cmd == "goto":
                cmd_goto(page, rest)
            elif cmd == "scroll":
                cmd_scroll(page, rest)
            elif cmd == "wait":
                cmd_wait(page, rest)
            elif cmd == "click":
                cmd_click(page, rest, logger)
            elif cmd == "upload":
                cmd_upload(page, rest, logger)
            elif cmd == "cookies":
                cmd_cookies(context, rest)
            else:
                print(f"Unknown command: {cmd!r}. Type 'help' for the list.")
        except Exception as exc:
            logger.exception("command %r failed", cmd)
            print(f"command failed: {exc}")


def main() -> int:
    args = parse_args()

    if args.example_config:
        example = HERE / "config.example.json"
        sys.stdout.write(example.read_text(encoding="utf-8"))
        return 0

    if args.setup:
        return run_setup_wizard(args.config)

    if args.check:
        return run_health_check(args.config)

    cfg = load_config_or_die(args.config)
    logger = setup_logging()

    warnings = validate_user(cfg.user)
    if warnings:
        print("config.json - validation warnings:")
        for w in warnings:
            print(f"  ! {w}")
        print()

    print(f"VFS Helper - config: {args.config}")
    print(f"Start URL:   {cfg.start_url}")
    print()
    print("Launching browser (real Chromium / Chrome window, not headless).")
    print("After it opens:")
    print("  1. Solve Cloudflare and log in manually.")
    print("  2. Navigate to the form you want to fill.")
    print("  3. Switch back to THIS terminal and type 'fill' + Enter.")
    print()
    print("The first run after a fresh profile WILL hit Cloudflare. Pass it")
    print("by hand. On subsequent runs the cookie is reused from")
    print(f"{(HERE / cfg.browser.get('user_data_dir', 'browser_profile')).resolve()}")
    print()

    with sync_playwright() as pw:
        try:
            context = launch_context(
                pw, cfg, stealth=not args.no_stealth, logger=logger
            )
        except PlaywrightError as exc:
            msg = str(exc).lower()
            print(f"\nFailed to launch browser: {exc}\n")
            if "executable doesn't exist" in msg or "not found" in msg:
                print(
                    "Looks like Playwright Chromium / Google Chrome is not\n"
                    "installed on this machine. Fix it with:\n"
                    "    python -m playwright install chromium\n"
                    "Or install Google Chrome from https://www.google.com/chrome/\n"
                )
            logger.error("launch failed: %s", exc)
            return 1

        page = get_active_page(context)
        if cfg.start_url:
            try:
                page.goto(cfg.start_url, timeout=60000)
            except Exception as exc:
                print(f"Could not open start URL: {exc}")
                logger.warning("goto start_url failed: %s", exc)

        try:
            run_repl(context, cfg, logger)
        except KeyboardInterrupt:
            print("\nInterrupted, exiting.")
        finally:
            try:
                context.close()
            except Exception:
                pass

    logger.info("=== vfs_helper finished ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
