"""Stage registry and dependency-ordered execution plans."""

from __future__ import annotations

from collections.abc import Iterable

from vfe_vision.core.errors import InvalidInputError
from vfe_vision.pipeline.stage import Stage


class StageRegistry:
    def __init__(self, stages: Iterable[Stage]) -> None:
        self._stages: dict[str, Stage] = {}
        for stage in stages:
            if stage.name in self._stages:
                raise ValueError(f"étape en double : {stage.name}")
            self._stages[stage.name] = stage
        for stage in self._stages.values():
            missing = [dep for dep in (*stage.requires, *stage.after) if dep not in self._stages]
            if missing:
                raise ValueError(f"{stage.name} dépend d'étapes inconnues : {missing}")

    @property
    def names(self) -> list[str]:
        return [stage.name for stage in self.plan()]

    def dependents(self, name: str) -> list[str]:
        """Stages that need ``name``'s outputs, directly or transitively (hard dependencies)."""
        found: list[str] = []
        frontier = [name]
        while frontier:
            current = frontier.pop()
            for stage in self._stages.values():
                if current in stage.requires and stage.name not in found:
                    found.append(stage.name)
                    frontier.append(stage.name)
        return found

    def downstream(self, names: Iterable[str]) -> list[str]:
        """Stages that read the results of ``names`` (hard or soft dependencies), directly or
        transitively, in execution order; ``names`` themselves are left out."""
        wanted = set(names)
        found: set[str] = set()
        frontier = list(wanted)
        while frontier:
            current = frontier.pop()
            for stage in self._stages.values():
                if stage.name in found or stage.name in wanted:
                    continue
                if current in stage.requires or current in stage.after:
                    found.add(stage.name)
                    frontier.append(stage.name)
        return [name for name in self.names if name in found]

    def get(self, name: str) -> Stage:
        try:
            return self._stages[name]
        except KeyError as exc:
            raise InvalidInputError(f"Étape inconnue : {name}") from exc

    def plan(self, requested: Iterable[str] | None = None) -> list[Stage]:
        """Topologically ordered stages: the requested ones plus their dependencies."""
        wanted = set(self._stages) if requested is None else set(requested)
        for name in wanted:
            self.get(name)
        ordered: list[Stage] = []
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(name: str) -> None:
            if name in done:
                return
            if name in visiting:
                raise ValueError(f"dépendance circulaire autour de {name}")
            visiting.add(name)
            stage = self._stages[name]
            for dep in stage.requires:
                visit(dep)
            for dep in stage.after:
                if dep in wanted:
                    visit(dep)
            visiting.discard(name)
            done.add(name)
            ordered.append(stage)

        # Registration order is the tie-breaker, so plans are deterministic.
        for name in self._stages:
            if name in wanted:
                visit(name)
        return ordered
