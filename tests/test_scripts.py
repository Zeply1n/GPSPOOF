"""Regression checks for failures that can prevent first-time setup."""
import re
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


def test_pymobiledevice_version_and_api_check_stay_aligned() -> None:
    setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    setup_version = re.search(r'^PMD_VERSION="([^"]+)"$', setup, re.MULTILINE)
    project_version = re.search(r'^\s*"pymobiledevice3==([^"]+)",$', project, re.MULTILINE)
    assert setup_version and project_version
    assert setup_version.group(1) == project_version.group(1) == "4.26.6"
    assert "from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel" in setup
    assert "from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider" in setup
