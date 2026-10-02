"""Tests for the dependency scheduler / DAG (Phase 5)."""

from __future__ import annotations

import pytest

from flowguard.core.exceptions import CycleError, UnknownDependencyError
from flowguard.core.models import Workflow
from flowguard.core.scheduler import DAG


def build_workflow(spec: dict[str, list[str]]) -> Workflow:
    """``{"a": ["b"]}`` = step a depends on step b."""
    steps = [
        {"name": name, "command": ["echo", name], "depends_on": deps} for name, deps in spec.items()
    ]
    return Workflow.model_validate({"name": "wf", "steps": steps})


class TestConstruction:
    def test_basic_graph(self):
        dag = DAG(["a", "b"], {"a": ["b"]})
        assert dag.nodes == ["a", "b"]
        assert dag.dependencies("a") == ["b"]

    def test_unknown_dependency_rejected(self):
        with pytest.raises(UnknownDependencyError, match="unknown step 'x'"):
            DAG(["a"], {"a": ["x"]})

    def test_edge_for_unknown_node_rejected(self):
        with pytest.raises(UnknownDependencyError):
            DAG(["a"], {"ghost": []})

    def test_duplicate_nodes_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            DAG(["a", "a"], {})

    def test_from_workflow(self):
        workflow = build_workflow({"build": [], "test": ["build"]})
        dag = DAG.from_workflow(workflow)
        assert set(dag.nodes) == {"build", "test"}
        assert dag.dependencies("test") == ["build"]


class TestTopologicalOrder:
    def test_empty_graph(self):
        assert DAG([], {}).topological_order() == []

    def test_linear_chain(self):
        dag = DAG(["a", "b", "c"], {"b": ["a"], "c": ["b"]})
        assert dag.topological_order() == ["a", "b", "c"]

    def test_independent_nodes_keep_declaration_order(self):
        dag = DAG(["z", "m", "a"], {})
        assert dag.topological_order() == ["z", "m", "a"]

    def test_diamond_respects_dependencies(self):
        dag = DAG(
            ["top", "left", "right", "bottom"],
            {"left": ["top"], "right": ["top"], "bottom": ["left", "right"]},
        )
        order = dag.topological_order()
        assert order.index("top") < order.index("left")
        assert order.index("top") < order.index("right")
        assert order.index("left") < order.index("bottom")
        assert order.index("right") < order.index("bottom")

    def test_forward_reference_dependency(self):
        # "a" may depend on steps declared after it.
        dag = DAG(["a", "b"], {"a": ["b"]})
        assert dag.topological_order() == ["b", "a"]

    def test_deterministic_across_calls(self):
        dag = DAG(["x", "y", "z"], {"z": ["x", "y"]})
        assert dag.topological_order() == dag.topological_order()

    def test_two_node_cycle_detected_with_path(self):
        dag = DAG(["a", "b"], {"a": ["b"], "b": ["a"]})
        with pytest.raises(CycleError) as excinfo:
            dag.topological_order()
        assert "a -> b -> a" in str(excinfo.value)

    def test_three_node_cycle_detected_with_path(self):
        dag = DAG(["a", "b", "c"], {"a": ["c"], "b": ["a"], "c": ["b"]})
        with pytest.raises(CycleError) as excinfo:
            dag.topological_order()
        message = str(excinfo.value)
        assert "cycle" in message
        for name in ("a", "b", "c"):
            assert name in message

    def test_self_dependency_detected_at_construction(self):
        with pytest.raises(CycleError, match="depends on itself"):
            DAG(["a"], {"a": ["a"]})

    def test_cycle_in_larger_graph(self):
        dag = DAG(["ok", "a", "b"], {"a": ["b"], "b": ["a"]})
        with pytest.raises(CycleError):
            dag.topological_order()


class TestLevels:
    def test_single_level_for_independent_steps(self):
        dag = DAG(["a", "b", "c"], {})
        assert dag.levels() == [["a", "b", "c"]]

    def test_levels_for_chain(self):
        dag = DAG(["a", "b", "c"], {"b": ["a"], "c": ["b"]})
        assert dag.levels() == [["a"], ["b"], ["c"]]

    def test_levels_for_diamond(self):
        dag = DAG(
            ["top", "left", "right", "bottom"],
            {"left": ["top"], "right": ["top"], "bottom": ["left", "right"]},
        )
        assert dag.levels() == [["top"], ["left", "right"], ["bottom"]]

    def test_levels_match_topological_constraints(self):
        dag = DAG(["a", "b", "c", "d"], {"c": ["a", "b"], "d": ["c"]})
        levels = dag.levels()
        flat = [name for level in levels for name in level]
        assert sorted(flat) == ["a", "b", "c", "d"]
        assert levels[0] == ["a", "b"]
        assert levels[-1] == ["d"]


class TestDependents:
    def test_direct_dependents(self):
        dag = DAG(["a", "b", "c"], {"b": ["a"], "c": ["a"]})
        assert dag.direct_dependents("a") == {"b", "c"}
        assert dag.direct_dependents("b") == set()

    def test_transitive_dependents(self):
        dag = DAG(["a", "b", "c", "d"], {"b": ["a"], "c": ["b"], "d": ["c"]})
        assert dag.transitive_dependents(["a"]) == {"b", "c", "d"}

    def test_transitive_dependents_exclude_sources(self):
        dag = DAG(["a", "b"], {"b": ["a"]})
        assert dag.transitive_dependents(["a", "b"]) == set()

    def test_transitive_dependents_of_multiple(self):
        dag = DAG(["a", "b", "c"], {"c": ["a", "b"]})
        assert dag.transitive_dependents(["a", "b"]) == {"c"}
