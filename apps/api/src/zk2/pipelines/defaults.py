"""The pipeline a bot gets when it has none of its own.

This is the hybrid RAG path the product shipped with, expressed in the same
node vocabulary the editor uses. One runtime, one set of nodes: a bot with a
custom pipeline and a bot without take the identical code path, so the default
cannot quietly drift away from what the editor produces.
"""

from __future__ import annotations

from zk2.pipelines.dag import DagSpec

DEFAULT_DAG = DagSpec.model_validate(
    {
        "nodes": [
            {
                "id": "dense",
                "type": "retriever_dense",
                "config": {"k": 20},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "bm25",
                "type": "retriever_bm25",
                "config": {"k": 20},
                "position": {"x": 0, "y": 160},
            },
            {
                "id": "fuse",
                "type": "fusion",
                "config": {"method": "rrf", "k": 60, "limit": 30},
                "position": {"x": 260, "y": 80},
            },
            {
                "id": "rerank",
                "type": "rerank",
                "config": {"top_k": 5},
                "position": {"x": 520, "y": 80},
            },
            {
                "id": "context",
                "type": "context_builder",
                "config": {"token_budget": 6000, "emit_sources": True},
                "position": {"x": 780, "y": 80},
            },
            {
                "id": "answer",
                "type": "generate",
                "config": {"cite_sources": True},
                "position": {"x": 1040, "y": 80},
            },
        ],
        "edges": [
            ["dense", "fuse"],
            ["bm25", "fuse"],
            ["fuse", "rerank"],
            ["rerank", "context"],
            ["context", "answer"],
        ],
    }
)
