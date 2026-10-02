"""Dependency scheduler: DAG construction, topological sorting, cycle detection.

The scheduler is pure and deterministic — it never executes anything. Given a
workflow it builds a directed acyclic graph of step dependencies and answers
scheduling questions for the engine:

* :meth:`DAG.topological_order` — a stable, deterministic execution order.
* :meth:`DAG.levels` — groups of steps that can run in parallel.
* :meth:`DAG.transitive_dependents` — steps invalidated by a failure.
"""

from __future__ import annotations

import heapq
from typing import TYPE_CHECKING

from flowguard.core.exceptions import CycleError, UnknownDependencyError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from flowguard.core.models import Workflow

__all__ = ["DAG"]


class DAG:
    """A directed acyclic graph over step names.

    ``edges[name]`` lists the steps that must complete successfully before
    ``name`` may run (its dependencies). Edges pointing at unknown steps
    raise :class:`UnknownDependencyError` at construction time; cycles are
    raised by :meth:`topological_order` and :meth:`levels` as
    :class:`CycleError` with the concrete cycle path in the message.
    """

    def __init__(self, names: Iterable[str], edges: Mapping[str, Iterable[str]]) -> None:
        self._order: list[str] = list(names)
        self._nodes: set[str] = set(self._order)
        if len(self._nodes) != len(self._order):
            raise ValueError("duplicate node names in DAG")
        self._index: dict[str, int] = {name: i for i, name in enumerate(self._order)}
        self._dependencies: dict[str, list[str]] = {name: [] for name in self._order}
        self._dependents: dict[str, set[str]] = {name: set() for name in self._order}
        for name, deps in edges.items():
            if name not in self._nodes:
                raise UnknownDependencyError(f"dependency edge defined for unknown step {name!r}")
            for dep in deps:
                if dep not in self._nodes:
                    raise UnknownDependencyError(f"step {name!r} depends on unknown step {dep!r}")
                if dep == name:
                    raise CycleError(f"dependency cycle detected: {name} depends on itself")
                self._dependencies[name].append(dep)
                self._dependents[dep].add(name)

    @classmethod
    def from_workflow(cls, workflow: Workflow) -> DAG:
        """Build the dependency graph of a workflow."""
        return cls(
            workflow.step_names,
            {step.name: step.depends_on for step in workflow.steps},
        )

    @property
    def nodes(self) -> list[str]:
        """All step names in their original declaration order."""
        return list(self._order)

    def dependencies(self, name: str) -> list[str]:
        """Direct dependencies of ``name`` in declaration order."""
        return list(self._dependencies[name])

    def direct_dependents(self, name: str) -> set[str]:
        """Steps that directly depend on ``name``."""
        return set(self._dependents[name])

    def topological_order(self) -> list[str]:
        """Return a deterministic topological order (Kahn's algorithm).

        Among simultaneously ready steps the one declared first in the
        workflow is emitted first, so the same workflow always yields the
        same sequence. Raises :class:`CycleError` when the graph is not
        acyclic.
        """
        indegree = {name: len(deps) for name, deps in self._dependencies.items()}
        ready: list[tuple[int, str]] = [
            (self._index[name], name) for name in self._order if indegree[name] == 0
        ]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            _, name = heapq.heappop(ready)
            ordered.append(name)
            for dependent in self._dependents[name]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, (self._index[dependent], dependent))
        if len(ordered) != len(self._nodes):
            raise CycleError("dependency cycle detected: " + " -> ".join(self._find_cycle()))
        return ordered

    def levels(self) -> list[list[str]]:
        """Group steps into parallel execution levels.

        Level 0 contains steps with no dependencies; level N contains steps
        whose dependencies all live in earlier levels. Within a level the
        declaration order is preserved.
        """
        order = self.topological_order()
        depth: dict[str, int] = {}
        for name in order:
            deps = self._dependencies[name]
            depth[name] = max((depth[d] for d in deps), default=-1) + 1
        levels: list[list[str]] = []
        for name in order:
            while len(levels) <= depth[name]:
                levels.append([])
            levels[depth[name]].append(name)
        return levels

    def transitive_dependents(self, names: Iterable[str]) -> set[str]:
        """All steps that (transitively) depend on any of *names*.

        Used by the engine to skip the downstream of a failed step.
        The returned set excludes ``names`` themselves.
        """
        sources = set(names)
        frontier = set(sources)
        seen: set[str] = set()
        while frontier:
            current = frontier.pop()
            for dependent in self._dependents[current]:
                if dependent not in seen:
                    seen.add(dependent)
                    frontier.add(dependent)
        return seen - sources

    def _find_cycle(self) -> list[str]:
        """Return one concrete cycle as a list of node names (best effort).

        The result ``a -> b -> a`` reads "a depends on b depends on a".
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(self._order, WHITE)
        parent: dict[str, str] = {}
        for start in self._order:
            if color[start] != WHITE:
                continue
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                node, processed = stack.pop()
                if processed:
                    color[node] = BLACK
                    continue
                if color[node] != WHITE:
                    continue
                color[node] = GRAY
                stack.append((node, True))
                for dep in self._dependencies[node]:
                    if color[dep] == WHITE:
                        parent[dep] = node
                        stack.append((dep, False))
                    elif color[dep] == GRAY:
                        # Back-edge: node depends on dep, and dep is still on
                        # the DFS stack. Walk the parent chain from node back
                        # to dep to recover the concrete path.
                        path = [node]
                        cursor = node
                        while cursor != dep:
                            cursor = parent.get(cursor, dep)
                            path.append(cursor)
                        path.reverse()  # dep -> ... -> node, each depending on the next
                        path.append(path[0])  # close the loop for display
                        return path
        remaining = [name for name, c in color.items() if c != BLACK]
        return [*remaining, remaining[0]] if remaining else ["<unknown>"]
