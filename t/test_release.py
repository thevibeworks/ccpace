"""Keep published entry points consistent with the package being released."""

import json
from pathlib import Path
import tomllib
from html.parser import HTMLParser

from conftest import cc
from build_agent_docs import artifacts

ROOT = Path(__file__).resolve().parent.parent


def test_release_versions_agree():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert cc.__version__ == version
    for filename in ("plugin.json", "marketplace.json"):
        assert (
            json.loads((ROOT / ".claude-plugin" / filename).read_text())["version"]
            == version
        )
    assert f"## v{version} " in (ROOT / "CHANGELOG.md").read_text()
    assert f'"softwareVersion":"{version}"' in (ROOT / "site/index.html").read_text()


def test_agent_docs_match_sources():
    for filename, expected in artifacts().items():
        assert (ROOT / filename).read_text() == expected, (
            f"run uv run t/build_agent_docs.py: {filename}"
        )


class SiteLinks(HTMLParser):
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if (
                key in ("src", "srcset", "href")
                and value
                and not value.startswith(("https:", "#"))
            ):
                assert (ROOT / "site" / value).exists(), value


def test_site_assets_exist():
    SiteLinks().feed((ROOT / "site/index.html").read_text())
    for name in (
        "calendar-120",
        "calendar-quiet",
        "calendar-paper",
        "calendar-50",
        "calendar-50-quiet",
        "calendar-50-paper",
        "accounts-120",
        "accounts-80",
        "codex-120",
    ):
        assert (ROOT / "site/assets" / f"{name}.png").read_bytes() == (
            ROOT / "docs/calendar-previews" / f"{name}.png"
        ).read_bytes()
