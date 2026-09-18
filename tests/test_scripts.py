"""Regression checks for failures that can prevent first-time setup."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_setup_does_not_request_unrelated_curl_upgrade() -> None:
    setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
    install_lines = [line for line in setup.splitlines() if "apt-get install" in line]
    assert install_lines
    assert all("curl" not in line for line in install_lines)


def test_watchdog_health_check_does_not_require_curl() -> None:
    watchdog = (ROOT / "watchdog.sh").read_text(encoding="utf-8")
    assert "urllib.request.urlopen" in watchdog
    assert "curl -" not in watchdog
