"""Reusable visual components for the FlowGuard terminal UI.

Everything the CLI prints is built here so the whole application shares one
design language: a cyan-to-violet wordmark, dark slate surfaces, rounded
panels, colour-coded status pills, and compact data tables. Strings come
from :mod:`flowguard.i18n`, which makes the exact same UI renderable in
English or Chinese.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from flowguard import __version__
from flowguard.core.models import Status
from flowguard.i18n import messages

if TYPE_CHECKING:
    from flowguard.core.models import PipelineResult, Workflow
    from flowguard.core.scheduler import DAG

__all__ = [
    "brand_header",
    "error_panel",
    "format_duration",
    "format_timestamp",
    "history_table",
    "init_panel",
    "results_table",
    "run_header",
    "serve_panel",
    "show_header",
    "status_label",
    "status_pill",
    "summary_panel",
    "validate_panel",
]

# -- palette -----------------------------------------------------------------

CYAN = (34, 211, 238)
VIOLET = (167, 139, 250)
BORDER = "#2b3a55"
MUTED = "#7c93b8"
INK = "#e6edf7"

_STATUS_LOOK: dict[Status, tuple[str, str, str]] = {
    # status: (glyph, foreground, pill background)
    Status.SUCCEEDED: ("✓", "#052e1b", "#4ade80"),
    Status.FAILED: ("×", "#3b0a0a", "#f87171"),
    Status.TIMEOUT: ("◷", "#3a2500", "#fbbf24"),
    Status.SKIPPED: ("○", "#27364a", "#94a3b8"),
    Status.RUNNING: ("◐", "#06263a", "#38bdf8"),
    Status.PENDING: ("·", "#1e293b", "#64748b"),
}


def _blend(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> str:
    channels = tuple(round(a + (b - a) * ratio) for a, b in zip(start, end, strict=True))
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _gradient(label: str) -> Text:
    """Render *label* as a cyan → violet gradient wordmark."""
    text = Text()
    for index, char in enumerate(label):
        position = index / max(len(label) - 1, 1)
        text.append(char, style=Style(color=_blend(CYAN, VIOLET, position), bold=True))
    return text


def _label(text: str) -> Text:
    return Text(text, style=f"bold {MUTED}")


def format_duration(ms: float) -> str:
    """Human-friendly duration for tables and summaries."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:04.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m"


def format_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def format_short_timestamp(epoch: float) -> str:
    """Compact timestamp for dense tables (fits an 80-column terminal)."""
    return datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M")


def status_label(status: Status, lang: str = "en") -> str:
    """Translated status word (English values match the persisted status)."""
    return messages(lang)[f"status.{status.value}"]


def status_pill(status: Status, lang: str = "en") -> Text:
    glyph, foreground, background = _STATUS_LOOK[status]
    label = status_label(status, lang)
    return Text(
        f" {glyph} {label} ",
        style=Style(color=foreground, bgcolor=background, bold=True),
        no_wrap=True,
    )


def _duration_bar(value: float, maximum: float, width: int = 8) -> Text:
    filled = max(1, min(width, round(width * value / maximum))) if maximum > 0 else 0
    return Text("█" * filled + "░" * (width - filled), style="bold #22d3ee", no_wrap=True)


# -- chrome ------------------------------------------------------------------


def brand_header(lang: str = "en") -> Panel:
    """The wordmark band shown at the top of every command."""
    strings = messages(lang)
    wordmark = Text("  ⬡  ")
    wordmark.append_text(_gradient("FlowGuard"))
    wordmark.append("   ")
    wordmark.append(f"v{__version__}", style=f"dim {MUTED}")
    subtitle = Text(
        f"  {strings['brand.tagline']}  ·  {strings['brand.subtitle']}",
        style=MUTED,
    )
    return Panel(
        Group(wordmark, subtitle),
        box=box.ROUNDED,
        border_style=BORDER,
        padding=(0, 1),
        expand=True,
    )


def _panel(
    body: RenderableType,
    *,
    title: str | None = None,
    accent: str = "#22d3ee",
    expand: bool = True,
) -> Panel:
    return Panel(
        body,
        title=title,
        title_align="left",
        box=box.ROUNDED,
        border_style=accent,
        padding=(0, 1),
        expand=expand,
    )


def error_panel(
    message: str,
    *,
    lang: str = "en",
    title: str | None = None,
    hint: str | None = None,
) -> Panel:
    """A red diagnostic panel with an optional next-step hint."""
    strings = messages(lang)
    body = Text()
    body.append("  ×  ", style="bold #f87171")
    body.append(message, style=INK)
    if hint:
        body.append("\n\n")
        body.append(f"  {strings['cli.hint']}  ", style="bold #fbbf24")
        body.append(hint, style=MUTED)
    return _panel(Group(body), title=title or strings["cli.config_error"], accent="#f87171")


# -- per-command surfaces ----------------------------------------------------


def init_panel(path: str, lang: str = "en") -> Panel:
    strings = messages(lang)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold #4ade80")
    grid.add_column()
    grid.add_row("✓", f"{strings['cli.init.created']}  [bold]{path}[/]")
    grid.add_row("", "")
    grid.add_row("1", "[bold]flowguard validate[/]")
    grid.add_row("2", "[bold]flowguard run[/]")
    grid.add_row("3", "[bold]flowguard history[/]")
    body = Group(
        grid,
        Text(f"\n{strings['cli.init.next']}", style=MUTED),
    )
    return _panel(body, title=strings["cli.init.title"], accent="#4ade80")


def validate_panel(
    workflow: Workflow,
    config_path: str,
    dag: DAG,
    lang: str = "en",
) -> Panel:
    """Validation result plus a small dependency-graph preview."""
    strings = messages(lang)
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style=f"bold {MUTED}")
    meta.add_column()
    meta.add_row(strings["cli.validate.file"], Text(config_path, style=INK))
    meta.add_row(strings["cli.workflow"], Text(workflow.name, style="bold #22d3ee"))
    levels = dag.levels()
    meta.add_row(
        strings["cli.validate.count"].format(count=len(workflow.steps), levels=len(levels)),
        Text(strings["cli.validate.parallel_hint"], style=f"dim {MUTED}"),
    )

    tree = Tree(f"[bold #22d3ee]{strings['cli.validate.pipeline']}[/]", guide_style=BORDER)
    for index, level in enumerate(levels, start=1):
        branch = tree.add(
            f"[bold {INK}]{strings['cli.validate.level'].format(index=index)}[/] "
            f"[dim]· {len(level)} {strings['cli.steps']}[/]"
        )
        for name in level:
            deps = workflow.get_step(name).depends_on
            suffix = (
                f"  [dim]← {strings['cli.validate.depends_on']} {', '.join(deps)}[/]"
                if deps
                else ""
            )
            branch.add(f"[bold {INK}]{name}[/]{suffix}")
    return _panel(
        Group(meta, Text(""), tree),
        title=f"✓  {strings['cli.validate.title']}",
        accent="#4ade80",
    )


def run_header(
    workflow_name: str,
    config_path: str,
    *,
    step_count: int,
    level_count: int,
    parallel: bool,
    lang: str = "en",
) -> Panel:
    strings = messages(lang)
    # Show just the filename when the workflow lives in the current
    # directory — an absolute temp/CWD path adds noise, not information.
    display_path = config_path
    path_obj = Path(config_path)
    try:
        if path_obj.parent.resolve() == Path.cwd().resolve():
            display_path = path_obj.name
    except OSError:  # pragma: no cover - CWD disappeared
        pass
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(style=f"bold {MUTED}", width=14)
    grid.add_column()
    grid.add_row(strings["cli.workflow"], Text(workflow_name, style="bold #22d3ee"))
    grid.add_row(
        f"{strings['cli.steps']} / {strings['cli.levels']}",
        Text(f"{step_count}  ·  {level_count}", style=INK),
    )
    grid.add_row(
        strings["cli.mode"],
        Text(strings["cli.parallel"] if parallel else strings["cli.sequential"], style="#a78bfa"),
    )
    grid.add_row(strings["cli.run.from_file"], Text(display_path, style=MUTED))
    return _panel(grid, title=f"⌁  {strings['cli.run.title']}", accent="#8b7cf6")


def results_table(result: PipelineResult, lang: str = "en") -> Table:
    strings = messages(lang)
    table = Table(
        box=box.ROUNDED,
        border_style=BORDER,
        header_style=f"bold {MUTED}",
        show_edge=True,
        expand=True,
        padding=(0, 1),
        title=f"[bold #22d3ee]{strings['cli.run']} {result.run_id}[/]",
        title_justify="left",
    )
    table.add_column("#", justify="right", style="dim", width=3, no_wrap=True)
    table.add_column(strings["cli.step"], style=f"bold {INK}", no_wrap=True)
    table.add_column(strings["cli.status"], no_wrap=True)
    table.add_column(strings["cli.duration"], justify="right", no_wrap=True)
    table.add_column(strings["cli.load"], no_wrap=True)
    table.add_column(strings["cli.attempts"], justify="right", no_wrap=True)
    table.add_column(strings["cli.exit"], justify="right", no_wrap=True)

    maximum = max((step.duration_ms for step in result.step_results), default=0.0)
    for index, step in enumerate(result.step_results, start=1):
        table.add_row(
            str(index),
            step.step_name,
            status_pill(step.status, lang),
            format_duration(step.duration_ms),
            _duration_bar(step.duration_ms, maximum),
            str(step.attempts) if step.attempts else "—",
            str(step.exit_code) if step.exit_code is not None else "—",
        )
    return table


def summary_panel(result: PipelineResult, lang: str = "en") -> Panel:
    strings = messages(lang)
    passed = sum(1 for step in result.step_results if step.succeeded)
    total = len(result.step_results)
    retries = sum(max(step.attempts - 1, 0) for step in result.step_results)
    succeeded = result.succeeded

    headline = Text("  ")
    headline.append("✓" if succeeded else "×", style="bold")
    headline.append("  ")
    headline.append(
        strings["cli.run.succeeded"] if succeeded else strings["cli.run.failed"],
        style=f"bold {'#4ade80' if succeeded else '#f87171'}",
    )
    headline.append(f"   {format_duration(result.duration_ms)}", style=MUTED)

    stats = Table.grid(padding=(0, 3), expand=True)
    for _ in range(4):
        stats.add_column()
    stats.add_row(
        _label(strings["cli.run.passed"]),
        Text(f"{passed}/{total}", style="bold #4ade80"),
        _label(strings["cli.run.retries"]),
        Text(str(retries), style=f"bold {INK}"),
    )
    stats.add_row(
        _label(strings["cli.run.total_time"]),
        Text(format_duration(result.duration_ms), style=f"bold {INK}"),
        _label(strings["cli.run"]),
        Text(result.run_id, style="bold #22d3ee"),
    )

    footer = Text("\n")
    footer.append(f"  {strings['cli.next']}  ", style="bold #fbbf24")
    footer.append(f"flowguard show {result.run_id}", style="#22d3ee")
    footer.append("   ·   ", style="dim")
    footer.append("flowguard history", style="#22d3ee")
    return _panel(
        Group(headline, Text(""), stats, footer),
        accent="#4ade80" if succeeded else "#f87171",
    )


def failure_detail_panels(result: PipelineResult, lang: str = "en") -> list[Panel]:
    strings = messages(lang)
    panels: list[Panel] = []
    for step in result.step_results:
        if step.succeeded or step.status is Status.SKIPPED or not step.error:
            continue
        body = Text(step.error, style=INK)
        if step.stderr.strip():
            excerpt = step.stderr.strip()
            if len(excerpt) > 600:
                excerpt = excerpt[:600] + "…"
            body.append("\n\n")
            body.append(f"{strings['cli.show.stderr']}", style=f"bold {MUTED}")
            body.append(f"\n{excerpt}", style="#fca5a5")
        panels.append(
            _panel(
                Group(body),
                title=f"×  {step.step_name}  ·  {strings['cli.show.failure']}",
                accent="#f87171",
            )
        )
    return panels


def history_table(runs: list[dict[str, Any]], lang: str = "en", total: int | None = None) -> Table:
    strings = messages(lang)
    # Fixed column widths keep the full 12-character run ID, the workflow
    # name, and the status pill readable even in a narrow 80-column shell.
    table = Table(
        title=f"[bold #22d3ee]{strings['cli.history.title']}[/]",
        title_justify="left",
        box=box.ROUNDED,
        border_style=BORDER,
        header_style=f"bold {MUTED}",
        expand=False,
        padding=(0, 1),
    )
    table.add_column(strings["cli.run"], style="#22d3ee", width=12, no_wrap=True)
    table.add_column(strings["cli.workflow"], style=f"bold {INK}", width=9, no_wrap=True)
    table.add_column(strings["cli.status"], width=13, no_wrap=True)
    table.add_column(strings["cli.steps"], justify="right", width=5, no_wrap=True)
    table.add_column(strings["cli.duration"], justify="right", width=8, no_wrap=True)
    table.add_column(strings["cli.started"], width=11, no_wrap=True)
    for entry in runs:
        status = Status(entry["status"])
        table.add_row(
            entry["run_id"],
            entry["workflow_name"],
            status_pill(status, lang),
            str(entry["total_steps"]),
            format_duration(entry["duration_ms"] or 0.0),
            format_short_timestamp(entry["started_at"]),
        )
    if total is not None:
        table.caption = strings["cli.history.count"].format(count=len(runs), total=total)
        table.caption_style = f"dim {MUTED}"
    return table


def show_header(record: dict[str, Any], lang: str = "en") -> Panel:
    strings = messages(lang)
    status = Status(record["status"])
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=f"bold {MUTED}", width=14)
    grid.add_column()
    grid.add_row(strings["cli.run"], Text(record["run_id"], style="bold #22d3ee"))
    grid.add_row(strings["cli.workflow"], Text(record["workflow_name"], style=f"bold {INK}"))
    grid.add_row(strings["cli.status"], status_pill(status, lang))
    grid.add_row(strings["cli.started"], Text(format_timestamp(record["started_at"]), style=INK))
    grid.add_row(
        strings["cli.duration"],
        Text(
            format_duration(record["duration_ms"] or 0.0) if record["finished_at"] else "—",
            style=INK,
        ),
    )
    return _panel(
        grid,
        title=f"⌁  {strings['cli.show.title']}",
        accent="#4ade80" if status.is_success else "#f87171",
        expand=False,
    )


def show_steps_table(steps: list[dict[str, Any]], lang: str = "en") -> Table:
    strings = messages(lang)
    table = Table(
        box=box.ROUNDED,
        border_style=BORDER,
        header_style=f"bold {MUTED}",
        expand=True,
        padding=(0, 1),
    )
    table.add_column(strings["cli.step"], style=f"bold {INK}", no_wrap=True)
    table.add_column(strings["cli.status"], no_wrap=True)
    table.add_column(strings["cli.exit"], justify="right", no_wrap=True)
    table.add_column(strings["cli.duration"], justify="right", no_wrap=True)
    table.add_column(strings["cli.attempts"], justify="right", no_wrap=True)
    for entry in steps:
        status = Status(entry["status"])
        table.add_row(
            entry["step_name"],
            status_pill(status, lang),
            str(entry["exit_code"]) if entry["exit_code"] is not None else "—",
            format_duration(entry["duration_ms"] or 0.0),
            str(entry["attempts"]) if entry["attempts"] else "—",
        )
    return table


def show_failure_panel(entry: dict[str, Any], lang: str = "en") -> Panel:
    strings = messages(lang)
    body = Text(entry["error"] or "", style=INK)
    body.append("\n\n")
    body.append(f"{strings['cli.show.stderr']}", style=f"bold {MUTED}")
    body.append(
        f"\n{(entry['stderr'] or '').strip() or strings['cli.show.empty']}",
        style="#fca5a5",
    )
    return _panel(
        Group(body),
        title=f"×  {entry['step_name']}  ·  {strings['cli.show.failure']}",
        accent="#f87171",
        expand=False,
    )


def serve_panel(
    host: str,
    port: int,
    db_path: str,
    *,
    lang: str = "en",
    insecure: bool = False,
) -> Panel:
    strings = messages(lang)
    url = f"http://{host}:{port}/"
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=f"bold {MUTED}", width=14)
    grid.add_column()
    grid.add_row(strings["cli.serve.open"], Text(url, style="bold #22d3ee"))
    grid.add_row(strings["cli.serve.database"], Text(db_path, style=MUTED))
    grid.add_row(
        strings["cli.serve.endpoints"],
        Text("/health  ·  /runs  ·  /runs/{id}  ·  /execute", style="#a78bfa"),
    )
    body: list[RenderableType] = [grid]
    if insecure:
        body.append(Text(""))
        body.append(Text(f"⚠  {strings['cli.serve.warning']}", style="bold #fbbf24"))
    return _panel(
        Group(*body),
        title=f"⌁  {strings['cli.serve.title']}",
        accent="#4ade80" if not insecure else "#fbbf24",
    )
