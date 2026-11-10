"""Documentation consistency checks enforced by CI.

Guards the two requirements that are easy to break silently: the English
README must embed English screenshots, the Chinese README Chinese ones,
and security/licence sections must stay in English in both documents.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"

EN_README = ROOT / "README.md"
ZH_README = ROOT / "README.zh-CN.md"

IMAGES = [
    "dashboard-en.png",
    "dashboard-zh.png",
    "cli-en.png",
    "cli-zh.png",
]


def _images(text: str) -> list[str]:
    return re.findall(r"docs/screenshots/([A-Za-z0-9._-]+\.png)", text)


class TestBilingualReadmes:
    def test_both_readmes_exist(self):
        assert EN_README.is_file()
        assert ZH_README.is_file()

    def test_english_readme_uses_english_screenshots(self):
        images = _images(EN_README.read_text(encoding="utf-8"))
        assert "dashboard-en.png" in images
        assert "cli-en.png" in images
        assert not [name for name in images if name.endswith("-zh.png")]

    def test_chinese_readme_uses_chinese_screenshots(self):
        images = _images(ZH_README.read_text(encoding="utf-8"))
        assert "dashboard-zh.png" in images
        assert "cli-zh.png" in images
        assert not [name for name in images if name.endswith("-en.png")]

    def test_both_link_to_each_other(self):
        assert "README.zh-CN.md" in EN_README.read_text(encoding="utf-8")
        assert "README.md" in ZH_README.read_text(encoding="utf-8")

    @staticmethod
    def _english_sections() -> list[str]:
        return ["## Security", "## License"]

    def test_security_and_license_sections_are_english(self):
        for readme in (EN_README, ZH_README):
            text = readme.read_text(encoding="utf-8")
            for heading in self._english_sections():
                assert heading in text, f"{readme.name} missing {heading}"

    def test_license_file_is_english(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "MIT License" in text
        assert not re.search(r"[\u4e00-\u9fff]", text)


class TestScreenshots:
    def test_all_four_screenshots_exist_and_are_real_pngs(self):
        for name in IMAGES:
            path = SHOTS / name
            assert path.is_file(), f"missing screenshot: {name}"
            data = path.read_bytes()
            assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"not a PNG: {name}"
            assert len(data) > 20_000, f"suspiciously small screenshot: {name}"

    def test_manifest_is_present(self):
        assert (SHOTS / "manifest.json").is_file()
