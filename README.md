# FlowGuard

[![CI](https://github.com/flowguard/flowguard/actions/workflows/ci.yml/badge.svg)](https://github.com/flowguard/flowguard/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: typed](https://img.shields.io/badge/typing-typed-blue)](src/flowguard/py.typed)

**FlowGuard is a local workflow automation engine.** Define a pipeline in a
single `.flowguard.yml` file and FlowGuard executes it deterministically on
your machine — with dependencies, parallelism, timeouts, retries, run
history, and an optional REST API. No cloud, no agents, no external
services.

It is a lightweight alternative to CI/CD workflow systems for individual
developers and small teams. It is *not* a cloud CI platform.

```yaml
workflow:
  name: build-project
  steps:
    - name: install
      command:
        - npm install

    - name: test
      command:
        - npm test
      depends_on: [install]
      retry:
        attempts: 3

    - name: build
      command:
        - npm run build
      depends_on: [test]
      timeout: 300
      artifacts:
        - dist/*
```

```console
$ flowguard run
Running workflow build-project (3 step(s)) from .flowguard.yml
┏━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Step    ┃ Status     ┃ Duration ┃ Attempts ┃
┡━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ install │ succeeded  │ 5.2s     │ 1        │
│ test    │ succeeded  │ 12.8s    │ 1        │
│ build   │ succeeded  │ 8.4s     │ 1        │
└─────────┴────────────┴──────────┴──────────┘

✓ Pipeline succeeded (26.5s)
```

## Why FlowGuard?

- **Deterministic** — the same workflow file, the same steps, the same
  ordering guarantees. Steps run only after their `depends_on` steps have
  succeeded; independent steps run in parallel.
- **Local-first** — everything happens on your machine: subprocess
  execution, SQLite run history, artifact files. Nothing leaves your box.
- **Fail loudly, recover politely** — per-step timeouts kill runaway
  commands; per-step retry policies with exponential backoff absorb flaky
  steps; failed steps skip exactly their descendants, never the whole
  pipeline.
- **Inspectable** — every run (stdout, stderr, exit codes, durations,
  retries) is stored in SQLite and queryable from the CLI or the REST API.
- **Extensible** — a small plugin interface hooks into the pipeline and
  step lifecycle.

## Installation

Requires **Python 3.12+**. Works on Windows, Linux, and macOS.

```console
$ pip install flowguard
```

Or from a checkout of this repository:

```console
$ pip install .
```

## Quickstart

```console
$ flowguard init        # write a starter .flowguard.yml
$ flowguard validate    # check it — errors are human-readable
$ flowguard run         # execute it
$ flowguard history     # browse past runs
```

### CLI reference

| Command                 | Description                                              |
| ----------------------- | -------------------------------------------------------- |
| `flowguard init`        | Create a starter `.flowguard.yml` (`--force` overwrites) |
| `flowguard validate`    | Validate a workflow file without running it              |
| `flowguard run [FILE]`  | Execute a workflow                                       |
| `flowguard history`     | List recent runs (`--workflow`, `--status`, `--limit`)   |
| `flowguard show RUN_ID` | Step-level detail for one run                            |
| `flowguard serve`       | Serve the REST API locally (`--host`, `--port`)          |

Useful `run` options:

- `--step NAME` — run only the named steps (plus everything they depend on)
- `--env KEY=VALUE` — environment overrides, repeatable
- `--no-parallel` — force sequential execution
- `--db PATH` — use a specific SQLite history database
- `--no-artifacts` — skip artifact collection for this run

Exit codes: `0` success, `1` workflow or validation failure, `2` usage error.

## Configuration reference

The default file name is `.flowguard.yml` (`.flowguard.yaml` also works).

```yaml
workflow:
  name: my-workflow          # required; letters, digits, . _ -
  environment:               # inherited by every step
    PYTHONUNBUFFERED: "1"

  steps:
    - name: test             # required; unique within the workflow
      command: [pytest, -q]  # required; list of args OR a shell string
      timeout: 600           # seconds; command is killed on expiry
      working_dir: packages/api
      environment:           # merged over workflow/process env
        COVERAGE_FILE: .coverage
      depends_on: [install]  # steps that must succeed first
      retry:                 # re-run on failure or timeout
        attempts: 3          # total attempts (default 1 = no retry)
        delay: 2             # seconds before first retry
        backoff_factor: 2.0  # exponential backoff multiplier
        max_delay: 30        # cap for a single retry delay
      artifacts:             # glob patterns copied after the step
        - reports/*.xml
        - dist/
```

Semantics worth knowing:

- **Commands** — a list (`[npm, install]`) is executed directly, without a
  shell. A string (`"npm install && npm test"`) or a one-element list
  holding a full command line (`[npm install]`) runs through the platform
  shell (`cmd.exe` on Windows, `/bin/sh` elsewhere).
- **Environment** — layers merge in priority order: parent process <
  workflow `environment` < step `environment` < `--env` overrides.
- **Dependencies** — `depends_on` steps must have succeeded. A failed or
  timed-out step marks its transitive dependents `skipped`; independent
  branches keep running.
- **Parallelism** — steps whose dependencies are satisfied run
  concurrently (asyncio + a thread pool); `--no-parallel` serialises.
- **Artifacts** — collected into `.flowguard/artifacts/<run-id>/<step>/`;
  step logs land in `.flowguard/artifacts/<run-id>/logs/`.
- **History** — stored in `.flowguard/flowguard.db` (override with
  `--db` or by passing a path to the API factory).

### Validation errors are for humans

```yaml
workflow:
  name: broken
  steps:
    - name: build
      command: [make]
      depends_on: [test]     # typo: no step called "test"
    - name: build            # duplicate name
      command: [make, all]
```

```text
Invalid workflow configuration in .flowguard.yml:
  - step 'build' (#1): depends on unknown step 'test'
  - duplicate step name(s): 'build'
```

Cycles are reported with the concrete path, e.g.
`dependency cycle detected: a -> b -> c -> a`.

## REST API

FlowGuard ships an optional local API service (FastAPI):

```console
$ flowguard serve            # http://127.0.0.1:8642
```

| Endpoint            | Description                                    |
| ------------------- | ---------------------------------------------- |
| `GET /health`       | Liveness + version                             |
| `GET /runs`         | List runs (`limit`, `workflow`, `status`)      |
| `GET /runs/{id}`    | One run with steps and the event log           |
| `POST /execute`     | Execute a workflow (by `path` or inline `config`) |

```console
$ curl http://127.0.0.1:8642/health
{"status":"ok","version":"0.1.0"}

$ curl -X POST http://127.0.0.1:8642/execute \
    -H "Content-Type: application/json" \
    -d '{"path": ".flowguard.yml"}'
{"run": {...}, "steps": [...], "status": "succeeded"}
```

## Plugins

Plugins observe pipeline execution. Subclass `flowguard.plugins.Plugin`
and override what you need:

```python
# mycompany/flowguard_plugins.py
from flowguard.plugins import PipelineContext, Plugin


class Notify(Plugin):
    name = "notify"

    def before_pipeline(self, context: PipelineContext) -> None:
        print(f"run {context.run_id} starting")

    def after_step(self, context, step, result) -> None:
        if not result.succeeded:
            print(f"step {step.name} failed: {result.error}")

    def after_pipeline(self, context, result) -> None:
        print(f"run {context.run_id}: {result.status.value}")
```

Load it programmatically:

```python
from flowguard.plugins import PluginManager

plugins = PluginManager()
plugins.load_from_spec("mycompany.flowguard_plugins:Notify")
```

or advertise it via the `flowguard.plugins` entry-point group in your
package metadata:

```toml
[project.entry-points."flowguard.plugins"]
notify = "mycompany.flowguard_plugins:Notify"
```

Hook errors never abort a pipeline — they are recorded in the run's event
log. The four hooks are `before_pipeline`, `before_step`, `after_step`,
and `after_pipeline`.

## Architecture

```
src/flowguard/
├── core/
│   ├── models.py       # Workflow, Step, RetryPolicy, results (Pydantic)
│   ├── engine.py       # asyncio engine: scheduling, parallelism, hooks
│   └── scheduler.py    # DAG: topological sort, levels, cycle detection
├── config/
│   └── parser.py       # YAML loading + human-readable validation
├── runner/
│   ├── executor.py     # subprocess execution (Win/Linux/macOS)
│   └── retry.py        # retries with exponential backoff
├── storage/
│   ├── database.py     # SQLite run history
│   └── artifacts.py    # artifact + log collection
├── cli/
│   └── commands.py     # Typer + Rich CLI
├── api/
│   ├── app.py          # FastAPI factory
│   └── routes.py       # REST endpoints
└── plugins/
    ├── base.py         # Plugin interface + PipelineContext
    ├── manager.py      # registration, entry points, safe dispatch
    └── builtin.py      # built-in LoggingPlugin
```

Execution model: the engine computes dependency levels from the DAG, then
repeatedly launches every ready step. Blocking subprocess work (with its
retry loop, timeout, and artifact collection) runs in a
`ThreadPoolExecutor` dispatched by asyncio, so independent steps proceed
concurrently while dependencies are strictly honoured.

## Development

```console
$ pip install -e ".[dev]"
$ pytest                # run the test suite (300+ tests)
$ ruff check .          # lint
$ ruff format --check . # formatting
```

The repository dogfoods FlowGuard: `.flowguard.yml` at the repo root runs
lint and the test suite via FlowGuard itself.

## License

[MIT](LICENSE)
