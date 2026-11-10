"""Tests for the bilingual UI string catalog."""

from __future__ import annotations

from flowguard.i18n import (
    DEFAULT_LANGUAGE,
    ENV_VAR,
    Language,
    detect_language,
    messages,
    normalize_language,
)


class TestLanguageResolution:
    def test_default_is_english(self):
        assert DEFAULT_LANGUAGE is Language.EN
        assert detect_language() is Language.EN

    def test_env_variable_selects_chinese(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "zh-CN")
        assert detect_language() is Language.ZH

    @staticmethod
    def _aliases() -> list[tuple[str, Language]]:
        return [
            ("en", Language.EN),
            ("EN-US", Language.EN),
            ("zh", Language.ZH),
            ("zh_CN", Language.ZH),
            ("ZH-Hans", Language.ZH),
            ("chinese", Language.ZH),
            ("klingon", Language.EN),
            ("", Language.EN),
        ]

    def test_aliases_and_unknown_values(self):
        for value, expected in self._aliases():
            assert normalize_language(value) is expected, value

    def test_none_is_english(self):
        assert normalize_language(None) is Language.EN


class TestCatalogCompleteness:
    def test_chinese_has_every_english_key(self):
        en = messages("en")
        zh = messages("zh")
        assert set(en) == set(zh)

    def test_translations_are_not_just_copies(self):
        en = messages("en")
        zh = messages("zh")
        different = [key for key in en if en[key] != zh[key]]
        assert len(different) > 40

    def test_missing_translation_falls_back_to_english(self):
        # messages() merges over the English catalog, so no key can be blank.
        catalog = messages("zh")
        assert all(isinstance(value, str) and value for value in catalog.values())

    def test_status_words_are_translated(self):
        assert messages("en")["status.succeeded"] == "succeeded"
        assert messages("zh")["status.succeeded"] == "成功"

    def test_language_toggle_is_ascii(self):
        # Keeps the English screenshot free of CJK text.
        assert messages("en")["dash.lang.switch"].isascii()
