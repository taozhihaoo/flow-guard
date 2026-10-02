"""Tests for the YAML configuration parser (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowguard.config.parser import (
    DEFAULT_FILENAME,
    find_config_file,
    load_config,
    parse_config,
    parse_yaml_text,
)
from flowguard.core.exceptions import ConfigError
from flowguard.core.models import RetryPolicy

VALID_YAML = """\
workflow:
  name: build-project
  steps:
    - name: install
      command: [python, -c, "print('installing')"]

    - name: test
      command: [python, -c, "print('testing')"]
      depends_on: [install]
"""


class TestParsing:
    def test_minimal_dict_config(self):
        workflow = parse_config(
            {
                "workflow": {
                    "name": "demo",
                    "steps": [{"name": "a", "command": ["echo"]}],
                }
            }
        )
        assert workflow.name == "demo"
        assert workflow.steps[0].name == "a"

    def test_yaml_text_parsing(self):
        workflow = parse_yaml_text(VALID_YAML, source="test.yml")
        assert [s.name for s in workflow.steps] == ["install", "test"]
        assert workflow.steps[1].depends_on == ["install"]

    def test_string_command_parses(self):
        workflow = parse_yaml_text(
            "workflow:\n  name: d\n  steps:\n    - name: a\n      command: echo hi\n"
        )
        assert workflow.steps[0].command == "echo hi"

    def test_retry_section_parses_to_policy(self):
        workflow = parse_config(
            {
                "workflow": {
                    "name": "d",
                    "steps": [
                        {
                            "name": "a",
                            "command": ["x"],
                            "retry": {"attempts": 3, "delay": 5},
                        }
                    ],
                }
            }
        )
        assert workflow.steps[0].retry == RetryPolicy(attempts=3, delay=5)

    def test_timeout_and_env_parse(self):
        workflow = parse_config(
            {
                "workflow": {
                    "name": "d",
                    "environment": {"A": "1"},
                    "steps": [
                        {
                            "name": "a",
                            "command": ["x"],
                            "timeout": 12.5,
                            "environment": {"B": "2"},
                        }
                    ],
                }
            }
        )
        assert workflow.environment == {"A": "1"}
        step = workflow.steps[0]
        assert step.timeout == 12.5
        assert step.environment == {"B": "2"}

    def test_artifacts_and_working_dir_parse(self):
        workflow = parse_config(
            {
                "workflow": {
                    "name": "d",
                    "steps": [
                        {
                            "name": "a",
                            "command": ["x"],
                            "working_dir": "sub",
                            "artifacts": ["out/*.bin"],
                        }
                    ],
                }
            }
        )
        assert workflow.steps[0].working_dir == "sub"
        assert workflow.steps[0].artifacts == ["out/*.bin"]

    def test_defaults_applied(self):
        workflow = parse_config(
            {"workflow": {"name": "d", "steps": [{"name": "a", "command": ["x"]}]}}
        )
        step = workflow.steps[0]
        assert step.timeout is None
        assert step.retry.attempts == 1
        assert step.depends_on == []


class TestHumanReadableErrors:
    def test_missing_workflow_section(self):
        with pytest.raises(ConfigError, match="missing required section 'workflow'"):
            parse_config({"not-workflow": {}}, source="cfg.yml")

    def test_top_level_not_mapping(self):
        with pytest.raises(ConfigError, match="top level must be a mapping"):
            parse_config(["nope"], source="cfg.yml")

    def test_workflow_section_not_mapping(self):
        with pytest.raises(ConfigError, match="'workflow' must be a mapping"):
            parse_config({"workflow": 3}, source="cfg.yml")

    def test_missing_name(self):
        with pytest.raises(ConfigError, match=r"'workflow.name' is required"):
            parse_config(
                {"workflow": {"steps": [{"name": "a", "command": ["x"]}]}},
                source="cfg.yml",
            )

    def test_missing_steps(self):
        with pytest.raises(ConfigError, match=r"'workflow.steps' is required"):
            parse_config({"workflow": {"name": "d"}}, source="cfg.yml")

    def test_empty_steps(self):
        with pytest.raises(ConfigError, match="steps' is empty"):
            parse_config({"workflow": {"name": "d", "steps": []}}, source="cfg.yml")

    def test_steps_not_a_list(self):
        with pytest.raises(ConfigError, match="must be a list of steps"):
            parse_config({"workflow": {"name": "d", "steps": "nope"}}, source="cfg.yml")

    def test_step_missing_name_names_the_position(self):
        with pytest.raises(ConfigError, match=r"step #2.*'name' is required"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [
                            {"name": "a", "command": ["x"]},
                            {"command": ["y"]},
                        ],
                    }
                },
                source="cfg.yml",
            )

    def test_step_missing_command_names_the_step(self):
        with pytest.raises(ConfigError, match=r"step 'build'.*'command' is required"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [{"name": "build"}],
                    }
                },
                source="cfg.yml",
            )

    def test_step_not_a_mapping(self):
        with pytest.raises(ConfigError, match=r"step #1 must be a mapping"):
            parse_config(
                {"workflow": {"name": "d", "steps": ["just-a-string"]}},
                source="cfg.yml",
            )

    def test_empty_command_list(self):
        with pytest.raises(ConfigError, match="'command' is an empty list"):
            parse_config(
                {"workflow": {"name": "d", "steps": [{"name": "a", "command": []}]}},
                source="cfg.yml",
            )

    def test_non_string_command_token(self):
        with pytest.raises(ConfigError, match="every command token must be a string"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [{"name": "a", "command": ["echo", 4]}],
                    }
                },
                source="cfg.yml",
            )

    def test_non_string_command(self):
        with pytest.raises(ConfigError, match="must be a string or a list"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [{"name": "a", "command": {"bad": "type"}}],
                    }
                },
                source="cfg.yml",
            )

    def test_duplicate_step_names(self):
        with pytest.raises(ConfigError, match=r"duplicate step name.*'build'"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [
                            {"name": "build", "command": ["x"]},
                            {"name": "build", "command": ["y"]},
                        ],
                    }
                },
                source="cfg.yml",
            )

    def test_unknown_dependency_names_both_steps(self):
        with pytest.raises(ConfigError) as excinfo:
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [{"name": "build", "command": ["x"], "depends_on": ["tset"]}],
                    }
                },
                source="cfg.yml",
            )
        message = str(excinfo.value)
        assert "build" in message
        assert "tset" in message

    def test_cycle_reported_with_path(self):
        with pytest.raises(ConfigError) as excinfo:
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [
                            {"name": "a", "command": ["x"], "depends_on": ["c"]},
                            {"name": "b", "command": ["x"], "depends_on": ["a"]},
                            {"name": "c", "command": ["x"], "depends_on": ["b"]},
                        ],
                    }
                },
                source="cfg.yml",
            )
        message = str(excinfo.value)
        assert "cycle" in message.lower()
        # The concrete cycle is reported and closes back onto itself.
        assert "a -> c -> b -> a" in message

    def test_self_dependency_cycle(self):
        with pytest.raises(ConfigError, match="cycle"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [{"name": "a", "command": ["x"], "depends_on": ["a"]}],
                    }
                },
                source="cfg.yml",
            )

    def test_unknown_field_reported(self):
        with pytest.raises(ConfigError, match="unknown field"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [{"name": "a", "command": ["x"], "cmds": ["x"]}],
                    }
                },
                source="cfg.yml",
            )

    def test_unknown_retry_option_reported(self):
        with pytest.raises(ConfigError, match="unknown retry option"):
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [
                            {
                                "name": "a",
                                "command": ["x"],
                                "retry": {"tries": 3},
                            }
                        ],
                    }
                },
                source="cfg.yml",
            )

    def test_error_includes_source(self):
        with pytest.raises(ConfigError, match=r"my-config\.yml"):
            parse_config({"workflow": {}}, source="my-config.yml")

    def test_error_lists_multiple_problems(self):
        with pytest.raises(ConfigError) as excinfo:
            parse_config(
                {
                    "workflow": {
                        "name": "d",
                        "steps": [
                            {"name": "a", "command": ["x"], "timeout": -1},
                            {"name": "a", "command": ["y"]},
                        ],
                    }
                },
                source="cfg.yml",
            )
        message = str(excinfo.value)
        assert "timeout" in message
        assert "duplicate" in message


class TestFileLoading:
    def test_load_from_file(self, write_config):
        path = write_config(VALID_YAML)
        workflow = load_config(path)
        assert workflow.name == "build-project"

    def test_alternative_file_name(self, tmp_path: Path):
        path = tmp_path / ".flowguard.yaml"
        path.write_text(VALID_YAML, encoding="utf-8")
        assert load_config(path).name == "build-project"

    def test_missing_file_raises_readable_error(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.yml")

    def test_directory_path_raises(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="directory"):
            load_config(tmp_path)

    def test_invalid_yaml_syntax(self, write_config):
        path = write_config("workflow: [unclosed")
        with pytest.raises(ConfigError, match="Invalid YAML syntax"):
            load_config(path)

    def test_empty_file(self, write_config):
        path = write_config("")
        with pytest.raises(ConfigError, match=r"empty document|top level"):
            load_config(path)

    def test_find_config_file_prefers_yml(self, tmp_path: Path):
        (tmp_path / ".flowguard.yml").write_text(VALID_YAML, encoding="utf-8")
        (tmp_path / ".flowguard.yaml").write_text(VALID_YAML, encoding="utf-8")
        assert find_config_file(tmp_path).name == ".flowguard.yml"

    def test_find_config_file_falls_back_to_yaml(self, tmp_path: Path):
        (tmp_path / ".flowguard.yaml").write_text(VALID_YAML, encoding="utf-8")
        assert find_config_file(tmp_path).suffix == ".yaml"

    def test_find_config_file_missing_suggests_init(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="flowguard init"):
            find_config_file(tmp_path)

    def test_default_filename_constant(self):
        assert DEFAULT_FILENAME == ".flowguard.yml"
