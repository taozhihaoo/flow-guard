# Changelog

All notable changes to FlowGuard are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-10

### Added

- Redesigned terminal UI: gradient wordmark, rounded panels, colour-coded
  status pills, dependency-graph previews, duration bars, verdict panels,
  and readable failure diagnostics (`cli/presentation.py`).
- Built-in, dependency-free web dashboard at `GET /` with KPI cards, recent
  runs, and step-level detail (`api/dashboard.py`, `api/dashboard.html`).
- Bilingual UI (English default, Simplified Chinese) via `--lang`,
  `FLOWGUARD_LANG`, or `?lang=zh`, backed by a single string catalog
  (`i18n.py`).
- English `README.md` and Chinese `README.zh-CN.md`, each embedding
  language-matched UI screenshots under `docs/screenshots/`.
- `scripts/capture_screenshots.py` regenerates screenshots from the real
  CLI and dashboard; CI validates them via `tests/test_docs.py`.
- Regression, i18n, dashboard, and documentation test suites.

### Fixed

- Field-level validation errors (invalid step names, empty commands,
  non-string environment values) were silently discarded and reported as
  "configuration could not be validated (internal error)".
- `Workflow.environment` now uses the same readable validator as
  `Step.environment`.
- Timeouts killed only the shell / direct child; shell grandchildren kept
  running and held the output pipes open. The executor now terminates the
  whole process tree on POSIX (process group) and Windows (`taskkill /T`).
- Artifact globs accepted absolute paths and `..` traversal, could write
  outside the run folder, and an absolute glob crashed the whole pipeline
  with `NotImplementedError`. Patterns are now sandboxed and failures mark
  the step as failed instead of aborting the run.
- Step logs are now written for every step, not only steps declaring
  artifacts; `--no-artifacts` actually disables all collection.
- `POST /execute` with `path` resolved relative `working_dir` against the
  server's CWD instead of the workflow file's directory.
- Retries discarded earlier attempts' stdout/stderr and reported only the
  last attempt's duration; output is now preserved as a labelled transcript.
- A crashed run remained `running` forever in SQLite; it is now marked
  `failed` before the exception propagates.
- `flowguard init --path nested/dir/file.yml` raised a raw traceback when
  parent directories were missing.
- Concurrent SQLite writes from worker threads could fail intermittently
  with "attempt to write a readonly database" on Windows, because every
  operation opened and closed its own WAL connection. The store now keeps a
  single WAL connection guarded by a lock, with an explicit busy timeout.

### Changed

- Version bumped to `0.2.0`; sdist now ships the Chinese README, docs
  screenshots, and screenshot tooling; the wheel explicitly includes the
  dashboard HTML template.
- GitHub Actions CI adds a docs/screenshot verification job, pip caching,
  concurrency control, and explicit smoke tests for the installed wheel.
  The dev extra now installs `httpx2` so `TestClient` keeps working with
  current Starlette releases.

## [0.1.0] - 2026-10-02

### Added

- Workflow engine: `Workflow`, `Step`, and result models with strict
  Pydantic validation.
- YAML configuration (`.flowguard.yml`) with human-readable validation
  errors for missing fields, invalid commands, duplicate step names,
  unknown dependencies, and dependency cycles.
- Cross-platform subprocess execution (Windows, Linux, macOS) capturing
  stdout, stderr, exit code, and duration.
- Per-step timeouts with process termination.
- Retry policies with attempts, base delay, exponential backoff, and
  delay cap.
- Dependency scheduler: DAG construction, deterministic topological
  sorting, level computation, and cycle detection with the concrete
  cycle path in the error message.
- Parallel execution of independent steps via asyncio + thread pool.
- SQLite storage of runs, step results, logs, durations, and statuses.
- Artifact collection: glob-based file/directory copying plus per-step
  log files under `.flowguard/artifacts/<run-id>/`.
- CLI: `flowguard init`, `validate`, `run`, `history`, `show`, `serve`
  with Rich terminal output.
- REST API (FastAPI): `GET /health`, `GET /runs`, `GET /runs/{id}`,
  `POST /execute`.
- Plugin interface with `before_pipeline`, `before_step`, `after_step`,
  and `after_pipeline` hooks; entry-point and module-path loading.
- A full pytest suite, ruff lint/format, GitHub Actions CI (Linux, Windows,
  macOS; Python 3.12 and 3.13).
