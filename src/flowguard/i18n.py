"""Bilingual UI strings for FlowGuard's CLI and web dashboard.

English is the default language everywhere. A second language can be
selected with the ``FLOWGUARD_LANG`` environment variable, the CLI's
``--lang`` option, or the dashboard's ``?lang=`` query parameter. The
catalog is deliberately a single source of truth so the terminal UI and
the browser UI stay consistent — and so documentation screenshots can be
captured in either language from the very same code path.
"""

from __future__ import annotations

import enum
import os
from typing import Final

__all__ = [
    "DEFAULT_LANGUAGE",
    "ENV_VAR",
    "Language",
    "detect_language",
    "messages",
    "normalize_language",
]


class Language(enum.StrEnum):
    """Supported UI languages."""

    EN = "en"
    ZH = "zh"


DEFAULT_LANGUAGE: Final = Language.EN
#: Environment variable consulted by :func:`detect_language`.
ENV_VAR: Final = "FLOWGUARD_LANG"

_EN: Final[dict[str, str]] = {
    # -- brand / chrome ----------------------------------------------------
    "brand.tagline": "local-first workflow automation",
    "brand.subtitle": "deterministic pipelines · 100% local",
    "brand.docs": "Docs",
    "brand.github": "GitHub",
    "brand.version": "Version",
    "brand.language": "Language",
    # -- CLI: shared -------------------------------------------------------
    "cli.config_error": "Configuration error",
    "cli.execution_error": "Execution error",
    "cli.interrupted": "Interrupted",
    "cli.hint": "Hint",
    "cli.next": "Next",
    "cli.step": "Step",
    "cli.status": "Status",
    "cli.duration": "Duration",
    "cli.attempts": "Attempts",
    "cli.exit": "Exit",
    "cli.load": "Load",
    "cli.started": "Started",
    "cli.workflow": "Workflow",
    "cli.run": "Run",
    "cli.steps": "Steps",
    "cli.levels": "Levels",
    "cli.mode": "Mode",
    "cli.parallel": "parallel",
    "cli.sequential": "sequential",
    "cli.unknown_steps": "Unknown step(s)",
    "cli.available_steps": "Available steps",
    "cli.env_invalid": "expected KEY=VALUE",
    # -- CLI: init ---------------------------------------------------------
    "cli.init.title": "Starter workflow created",
    "cli.init.created": "Created",
    "cli.init.next": "Edit it, then validate and run your pipeline:",
    "cli.init.exists": "already exists. Use --force to overwrite it.",
    "cli.init.write_failed": "Could not write the workflow file",
    # -- CLI: validate -----------------------------------------------------
    "cli.validate.title": "Workflow is valid",
    "cli.validate.file": "Configuration",
    "cli.validate.pipeline": "Pipeline plan",
    "cli.validate.level": "Level {index}",
    "cli.validate.depends_on": "depends on",
    "cli.validate.parallel_hint": "steps in the same level may run in parallel",
    "cli.validate.count": "{count} step(s) in {levels} level(s)",
    # -- CLI: run ----------------------------------------------------------
    "cli.run.title": "Workflow run",
    "cli.run.heading": "Running workflow",
    "cli.run.from_file": "from",
    "cli.run.succeeded": "Pipeline succeeded",
    "cli.run.failed": "Pipeline failed",
    "cli.run.passed": "Passed",
    "cli.run.retries": "Retries",
    "cli.run.total_time": "Total time",
    "cli.run.inspect": "Inspect this run",
    "cli.run.browse": "Browse history",
    "cli.run.no_runs": "No runs recorded yet. Try 'flowguard run'.",
    # -- CLI: history / show ----------------------------------------------
    "cli.history.title": "Run history",
    "cli.history.count": "Showing {count} of {total} run(s)",
    "cli.history.empty": "No runs recorded yet",
    "cli.show.title": "Run detail",
    "cli.show.not_found": "No run with ID",
    "cli.show.failure": "Failure detail",
    "cli.show.stderr": "stderr",
    "cli.show.empty": "(empty)",
    # -- CLI: serve --------------------------------------------------------
    "cli.serve.title": "FlowGuard dashboard is live",
    "cli.serve.listening": "Listening on",
    "cli.serve.database": "Database",
    "cli.serve.open": "Open the dashboard in your browser",
    "cli.serve.endpoints": "API endpoints",
    "cli.serve.warning": (
        "Binding to a non-loopback address exposes an unauthenticated command "
        "executor. Only do this on a trusted network."
    ),
    # -- statuses ----------------------------------------------------------
    "status.succeeded": "succeeded",
    "status.failed": "failed",
    "status.timeout": "timeout",
    "status.skipped": "skipped",
    "status.running": "running",
    "status.pending": "pending",
    # -- dashboard ---------------------------------------------------------
    "dash.title": "FlowGuard · Dashboard",
    "dash.nav.overview": "Overview",
    "dash.nav.runs": "Runs",
    "dash.nav.api": "API",
    "dash.eyebrow": "CONTROL CENTER",
    "dash.headline": "Your pipelines, at a glance.",
    "dash.subhead": "Every run stays on this machine. No cloud, no agents, no surprises.",
    "dash.kpi.runs": "Total runs",
    "dash.kpi.success": "Success rate",
    "dash.kpi.steps": "Steps executed",
    "dash.kpi.avg": "Average duration",
    "dash.kpi.hint_runs": "recorded locally",
    "dash.kpi.hint_success": "of finished runs",
    "dash.kpi.hint_steps": "across all runs",
    "dash.kpi.hint_avg": "per pipeline",
    "dash.section.runs": "Recent runs",
    "dash.section.runs_sub": "Newest first · click a row for step details",
    "dash.section.detail": "Run detail",
    "dash.section.detail_sub": "Step-by-step outcome for the selected run",
    "dash.col.run": "Run ID",
    "dash.col.workflow": "Workflow",
    "dash.col.status": "Status",
    "dash.col.steps": "Steps",
    "dash.col.duration": "Duration",
    "dash.col.started": "Started",
    "dash.col.step": "Step",
    "dash.col.exit": "Exit",
    "dash.col.attempts": "Attempts",
    "dash.empty.title": "No runs yet",
    "dash.empty.body": "Run `flowguard run` to record your first pipeline.",
    "dash.detail.empty": "Select a run to inspect its steps.",
    "dash.btn.refresh": "Refresh",
    "dash.btn.docs": "Documentation",
    "dash.footer": "Local-first · SQLite history · FastAPI",
    "dash.live": "Live",
    "dash.lang.switch": "ZH",
    "dash.all": "All statuses",
}

_ZH: Final[dict[str, str]] = {
    "brand.tagline": "本地优先的工作流自动化引擎",
    "brand.subtitle": "确定性流水线，完全运行在本机",
    "brand.docs": "文档",
    "brand.github": "GitHub",
    "brand.version": "版本",
    "brand.language": "语言",
    "cli.config_error": "配置错误",
    "cli.execution_error": "执行错误",
    "cli.interrupted": "已中断",
    "cli.hint": "提示",
    "cli.next": "下一步",
    "cli.step": "步骤",
    "cli.status": "状态",
    "cli.duration": "耗时",
    "cli.attempts": "次数",
    "cli.exit": "退出",
    "cli.load": "负载",
    "cli.started": "开始",
    "cli.workflow": "工作流",
    "cli.run": "运行",
    "cli.steps": "步骤",
    "cli.levels": "层级",
    "cli.mode": "模式",
    "cli.parallel": "并行",
    "cli.sequential": "串行",
    "cli.unknown_steps": "未知步骤",
    "cli.available_steps": "可用步骤",
    "cli.env_invalid": "格式应为 KEY=VALUE",
    "cli.init.title": "已创建工作流模板",
    "cli.init.created": "已创建",
    "cli.init.next": "编辑后，依次执行校验与运行：",
    "cli.init.exists": "已存在。使用 --force 覆盖。",
    "cli.init.write_failed": "无法写入工作流文件",
    "cli.validate.title": "工作流校验通过",
    "cli.validate.file": "配置文件",
    "cli.validate.pipeline": "流水线计划",
    "cli.validate.level": "第 {index} 层",
    "cli.validate.depends_on": "依赖于",
    "cli.validate.parallel_hint": "同一层的步骤可并行执行",
    "cli.validate.count": "共 {count} 个步骤，{levels} 个层级",
    "cli.run.title": "工作流运行",
    "cli.run.heading": "正在运行工作流",
    "cli.run.from_file": "配置文件",
    "cli.run.succeeded": "流水线执行成功",
    "cli.run.failed": "流水线执行失败",
    "cli.run.passed": "通过",
    "cli.run.retries": "重试",
    "cli.run.total_time": "总耗时",
    "cli.run.inspect": "查看本次运行详情",
    "cli.run.browse": "浏览历史记录",
    "cli.run.no_runs": "尚无运行记录，请先执行 'flowguard run'。",
    "cli.history.title": "运行历史",
    "cli.history.count": "显示 {count} / {total} 条记录",
    "cli.history.empty": "暂无运行记录",
    "cli.show.title": "运行详情",
    "cli.show.not_found": "未找到运行 ID",
    "cli.show.failure": "失败详情",
    "cli.show.stderr": "标准错误",
    "cli.show.empty": "（空）",
    "cli.serve.title": "FlowGuard 仪表盘已启动",
    "cli.serve.listening": "监听地址",
    "cli.serve.database": "数据库",
    "cli.serve.open": "在浏览器中打开仪表盘",
    "cli.serve.endpoints": "API 接口",
    "cli.serve.warning": "绑定到非回环地址会暴露一个未经鉴权的命令执行接口，请仅在可信网络中使用。",
    "status.succeeded": "成功",
    "status.failed": "失败",
    "status.timeout": "超时",
    "status.skipped": "跳过",
    "status.running": "运行中",
    "status.pending": "等待中",
    "dash.title": "FlowGuard · 仪表盘",
    "dash.nav.overview": "总览",
    "dash.nav.runs": "运行记录",
    "dash.nav.api": "接口",
    "dash.eyebrow": "控制台",
    "dash.headline": "流水线状态，一目了然。",
    "dash.subhead": "所有运行记录都保存在本机，无需云端、无需代理。",
    "dash.kpi.runs": "运行总数",
    "dash.kpi.success": "成功率",
    "dash.kpi.steps": "已执行步骤",
    "dash.kpi.avg": "平均耗时",
    "dash.kpi.hint_runs": "本机记录",
    "dash.kpi.hint_success": "已完成运行中",
    "dash.kpi.hint_steps": "累计执行",
    "dash.kpi.hint_avg": "每条流水线",
    "dash.section.runs": "最近运行",
    "dash.section.runs_sub": "按时间倒序 · 点击行查看步骤详情",
    "dash.section.detail": "运行详情",
    "dash.section.detail_sub": "所选运行的逐步结果",
    "dash.col.run": "运行 ID",
    "dash.col.workflow": "工作流",
    "dash.col.status": "状态",
    "dash.col.steps": "步骤数",
    "dash.col.duration": "耗时",
    "dash.col.started": "开始时间",
    "dash.col.step": "步骤",
    "dash.col.exit": "退出码",
    "dash.col.attempts": "重试次数",
    "dash.empty.title": "暂无运行记录",
    "dash.empty.body": "执行 `flowguard run` 记录第一条流水线。",
    "dash.detail.empty": "选择一条运行记录以查看步骤详情。",
    "dash.btn.refresh": "刷新",
    "dash.btn.docs": "文档",
    "dash.footer": "本地优先 · SQLite 历史 · FastAPI",
    "dash.live": "实时",
    "dash.lang.switch": "EN",
    "dash.all": "全部状态",
}

_CATALOGS: Final[dict[Language, dict[str, str]]] = {
    Language.EN: _EN,
    Language.ZH: _ZH,
}


def normalize_language(value: str | Language | None) -> Language:
    """Map a loose language tag to a supported :class:`Language`.

    Unknown or empty values fall back to English, so a typo in an
    environment variable can never break the UI.
    """
    if value is None:
        return DEFAULT_LANGUAGE
    if isinstance(value, Language):
        return value
    tag = value.strip().lower().replace("_", "-")
    if tag.startswith("zh") or tag in {"chinese", "cn"}:
        return Language.ZH
    return Language.EN


def detect_language() -> Language:
    """Resolve the language from the ``FLOWGUARD_LANG`` environment variable."""
    return normalize_language(os.environ.get(ENV_VAR))


def messages(lang: str | Language | None = None) -> dict[str, str]:
    """Return the full string catalog for *lang*, defaulting to English.

    Missing translations fall back to their English text, so adding a new
    key can never produce a blank label in another language.
    """
    resolved = DEFAULT_LANGUAGE if lang is None else normalize_language(lang)
    catalog = dict(_EN)
    if resolved is not Language.EN:
        catalog.update(_CATALOGS[resolved])
    return catalog
