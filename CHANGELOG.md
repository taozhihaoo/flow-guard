# Changelog

All notable changes to FlowGuard are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

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
- 300+ pytest tests, ruff lint/format, GitHub Actions CI (Linux, Windows,
  macOS; Python 3.12 and 3.13).
