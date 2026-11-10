"""Server-side rendering for the built-in web dashboard.

The dashboard is a single, dependency-free HTML page: no CDN assets, no
build step, no external requests. Translations and the FlowGuard version
are injected into a JSON island so the same template renders in English or
Chinese. All run data is fetched from the existing REST endpoints with the
browser's ``fetch`` API and inserted via ``textContent`` (never
``innerHTML`` for server data), which keeps the page safe against hostile
workflow names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from flowguard import __version__
from flowguard.i18n import Language, detect_language, messages, normalize_language

__all__ = ["render_dashboard"]

_TEMPLATE: Final = Path(__file__).with_name("dashboard.html")


def render_dashboard(lang: str | Language | None = None) -> str:
    """Return the complete dashboard HTML for *lang* (English by default)."""
    language = detect_language() if lang is None else normalize_language(lang)
    catalog = messages(language)
    catalog["__lang__"] = language.value
    catalog["__version__"] = __version__
    # ``</`` cannot appear inside a <script> JSON island.
    payload = json.dumps(catalog, ensure_ascii=False).replace("</", "<\\/")
    html = _TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__FLOWGUARD_I18N__", payload)
        .replace("__LANG__", language.value)
        .replace("__TITLE__", catalog["dash.title"])
    )
