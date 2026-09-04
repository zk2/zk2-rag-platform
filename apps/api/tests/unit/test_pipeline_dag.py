"""Graph validation: everything wrong with a pipeline is caught before saving."""

from __future__ import annotations

import pytest

from zk2.core.errors import ValidationError
from zk2.pipelines.dag import DagSpec, topological_order
from zk2.pipelines.defaults import DEFAULT_DAG
from zk2.pipelines.nodes import REGISTRY, node_types
from zk2.pipelines.runtime import validate_dag

pytestmark = pytest.mark.unit


def dag(nodes: list[tuple[str, str]], edges: list[tuple[str, str]]) -> DagSpec:
    return DagSpec.model_validate(
        {
            "nodes": [{"id": i, "type": t, "config": {}} for i, t in nodes],
            "edges": edges,
        }
    )


def test_topological_order_follows_the_edges() -> None:
    spec = dag(
        [("a", "retriever_dense"), ("b", "fusion"), ("c", "generate")],
        [("a", "b"), ("b", "c")],
    )
    assert [n.id for n in topological_order(spec)] == ["a", "b", "c"]


def test_parallel_branches_both_run_before_their_join() -> None:
    order = [n.id for n in topological_order(DEFAULT_DAG)]
    assert order.index("dense") < order.index("fuse")
    assert order.index("bm25") < order.index("fuse")
    assert order[-1] == "answer"


def test_cycles_are_rejected_with_the_nodes_involved() -> None:
    spec = dag([("a", "fusion"), ("b", "fusion")], [("a", "b"), ("b", "a")])
    with pytest.raises(ValidationError, match="cycle"):
        topological_order(spec)


def test_edge_to_an_unknown_node_is_rejected() -> None:
    spec = dag([("a", "fusion")], [("a", "ghost")])
    with pytest.raises(ValidationError, match="unknown node"):
        topological_order(spec)


def test_duplicate_node_ids_are_rejected() -> None:
    spec = DagSpec.model_validate(
        {"nodes": [{"id": "a", "type": "fusion"}, {"id": "a", "type": "rerank"}], "edges": []}
    )
    with pytest.raises(ValidationError, match="Duplicate node id"):
        topological_order(spec)


def test_node_ids_must_be_slugs() -> None:
    with pytest.raises(Exception, match="letters, digits"):
        DagSpec.model_validate({"nodes": [{"id": "a b/c", "type": "fusion"}], "edges": []})


def test_unknown_node_type_is_rejected() -> None:
    spec = dag([("a", "summon_daemon"), ("b", "generate")], [("a", "b")])
    with pytest.raises(ValidationError, match="Unknown node type"):
        validate_dag(spec)


def test_invalid_node_config_is_rejected() -> None:
    spec = DagSpec.model_validate(
        {
            "nodes": [
                {"id": "d", "type": "retriever_dense", "config": {"k": 9999}},
                {"id": "g", "type": "generate"},
            ],
            "edges": [["d", "g"]],
        }
    )
    with pytest.raises(ValidationError, match="Invalid config for node d"):
        validate_dag(spec)


def test_a_pipeline_without_generate_is_rejected() -> None:
    spec = dag([("a", "retriever_dense")], [])
    with pytest.raises(ValidationError, match="must end with a generate node"):
        validate_dag(spec)


def test_generate_must_be_last() -> None:
    """Nothing may run after the answer has been streamed."""
    spec = dag([("g", "generate"), ("r", "rerank")], [("g", "r")])
    with pytest.raises(ValidationError, match="must be last"):
        validate_dag(spec)


def test_empty_pipeline_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_dag(DagSpec(nodes=[], edges=[]))


def test_the_default_pipeline_is_valid() -> None:
    assert validate_dag(DEFAULT_DAG)[-1] == "answer"


def test_every_default_node_type_exists_in_the_registry() -> None:
    assert all(n.type in REGISTRY for n in DEFAULT_DAG.nodes)


def test_palette_exposes_a_json_schema_per_node() -> None:
    """The inspector builds its form from these schemas."""
    types = node_types()
    assert {t.type for t in types} == set(REGISTRY)
    for entry in types:
        assert entry.config_schema["type"] == "object"
        assert entry.title and entry.description
