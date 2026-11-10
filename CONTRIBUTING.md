# Contributing to FlowGuard

Thanks for your interest in improving FlowGuard! This document covers the
basics; keep proposals small and focused.

## Development setup

Requires Python 3.12+.

```console
git clone https://github.com/flowguard/flowguard.git
cd flowguard
pip install -e ".[dev]"
```

## Running the checks

```console
pytest                 # full test suite
ruff check .           # lint
ruff format .          # formatting (CI enforces --check)
```

The repository dogfoods FlowGuard — you can also run the checks through
FlowGuard itself:

```console
flowguard run
```

## Pull requests

- Add or update tests for any behaviour change. Bug fixes must include a
  regression test (see `tests/test_regressions.py`).
- Keep the public API typed (the package ships a `py.typed` marker) and
  validated with Pydantic models.
- Update **both** `README.md` (English, default) and `README.zh-CN.md`
  (Chinese) plus `CHANGELOG.md` for user-visible changes. Security and
  License sections stay in English in both files.
- UI strings live in `src/flowguard/i18n.py`. Add every key to both the
  English and Chinese catalogs; `tests/test_i18n.py` enforces parity.
- After changing the CLI or dashboard visuals, regenerate the four
  language-matched screenshots with `python scripts/capture_screenshots.py`
  (requires a Chromium-based browser; set `FLOWGUARD_BROWSER` if needed) and
  commit the updated PNGs. `tests/test_docs.py` runs in CI to catch stale
  or wrong-language images.
- Error messages are part of the product: they must be human-readable and
  point at the offending step, field, or file.
- CI must pass on all supported platforms (Linux, Windows, macOS) and
  Python versions (3.12, 3.13).

## Reporting issues

Include the workflow file (trim it if needed), the command you ran, the
full output, and your platform. For crashes, the traceback too.
