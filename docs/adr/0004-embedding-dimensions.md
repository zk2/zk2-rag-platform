# ADR-0004: Every embedding model normalised to 1536 dimensions

- Status: Accepted
- Date: 2026-09-04

## Context

The plan called for several embedding models, including
`text-embedding-3-large` at its native 3072 dimensions. pgvector refuses to
build an HNSW index above 2000 dimensions: the migration that tried
(`CREATE INDEX ... USING hnsw ((embedding::vector(3072)) ...)`) failed outright,
and without an index dense search degrades to a sequential scan over every
vector in the table.

## Decision

All embeddings are stored at 1536 dimensions. For OpenAI models this is the
`dimensions` request parameter (Matryoshka truncation, which the model was
trained to support); local models use their native width, with 768 as the other
indexed size. Partial HNSW indexes cover `vector_dims() = 1536` and `= 768`.

## Consequences

- One indexed code path, and the model can change without a schema change
- A small quality loss versus 3072-dimensional vectors, in exchange for indexed
  search - the right trade at this data size
- Storage per vector halves
- The choice is enforced in the embedding adapter, not left to the caller

## Alternatives considered

- **`halfvec` with `halfvec_cosine_ops`** (pgvector 0.7+, HNSW up to 4000
  dimensions). Keeps full width at half precision, but adds a column type, a
  second retrieval branch and a quality re-check for fp16. Revisit if evals ever
  show the 1536 truncation costing real recall
- **No index at 3072.** Sequential scan; not an option
