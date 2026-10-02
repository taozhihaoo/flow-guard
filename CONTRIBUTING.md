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
  regression test.
- Keep the public API typed (the package ships a `py.typed` marker) and
  validated with Pydantic models.
- Update `README.md` and `CHANGELOG.md` for user-visible changes.
- Error messages are part of the product: they must be human-readable and
  point at the offending step, field, or file.
- CI must pass on all supported platforms (Linux, Windows, macOS) and
  Python versions (3.12, 3.13).

## Reporting issues

Include the workflow file (trim it if needed), the command you ran, the
full output, and your platform. For crashes, the traceback too.
