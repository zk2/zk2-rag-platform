"""The pipeline graph: what a saved DAG looks like and whether it makes sense.

A pipeline is data, not code - a JSON document of nodes and edges that the UI
edits and the runtime executes. Everything that can be wrong with it is caught
here, before a version is saved, rather than surfacing as a broken chat turn.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from pydantic import BaseModel, Field, field_validator

from zk2.core.errors import ValidationError


class NodeSpec(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    type: str = Field(..., min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    # Editor-only: canvas position, ignored by the runtime
    position: dict[str, float] | None = None

    @field_validator("id")
    @classmethod
    def _id_is_a_slug(cls, value: str) -> str:
        if not all(ch.isalnum() or ch in "-_" for ch in value):
            msg = "Node ids may contain letters, digits, hyphens and underscores"
            raise ValueError(msg)
        return value


class DagSpec(BaseModel):
    nodes: list[NodeSpec]
    edges: list[tuple[str, str]] = Field(default_factory=list)

    def node(self, node_id: str) -> NodeSpec | None:
        return next((n for n in self.nodes if n.id == node_id), None)


def topological_order(dag: DagSpec) -> list[NodeSpec]:
    """Execution order, or a ValidationError explaining why there is none."""
    ids = [n.id for n in dag.nodes]
    if len(set(ids)) != len(ids):
        duplicate = next(i for i in ids if ids.count(i) > 1)
        raise ValidationError(f"Duplicate node id: {duplicate}")

    known = set(ids)
    incoming: dict[str, int] = dict.fromkeys(ids, 0)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for source, target in dag.edges:
        if source not in known:
            raise ValidationError(f"Edge from unknown node: {source}")
        if target not in known:
            raise ValidationError(f"Edge to unknown node: {target}")
        outgoing[source].append(target)
        incoming[target] += 1

    ready = deque(sorted(node_id for node_id, count in incoming.items() if count == 0))
    order: list[str] = []
    while ready:
        node_id = ready.popleft()
        order.append(node_id)
        for target in sorted(outgoing[node_id]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)

    if len(order) != len(ids):
        stuck = sorted(set(ids) - set(order))
        raise ValidationError(f"Pipeline has a cycle involving: {', '.join(stuck)}")

    by_id = {n.id: n for n in dag.nodes}
    return [by_id[node_id] for node_id in order]
