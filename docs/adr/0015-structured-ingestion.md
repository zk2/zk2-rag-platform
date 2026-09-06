# ADR-0015: Structure comes from the format, and chunks are packed to a budget

- Status: Accepted
- Date: 2026-09-06

## Context

The first ingestion pipeline flattened every document to a single string and
then tried to recover its structure with a regular expression: a line matching
`^\d+(\.\d+)*\.?\s+<short text>$` was treated as a heading, and each heading
opened a chunk.

Both halves of that were wrong, and one corpus of exported notes showed it:

- a numbered **list item** matches the same pattern as a numbered **heading**.
  `18. A good production pattern` and the `1. Router` under it were read as
  siblings, so the section was shredded into nine chunks of 10 to 25 tokens,
  and - because the fake heading took the same level as the real one - each of
  them lost the section it belonged to. The breadcrumb built to preserve
  context was the thing that dropped it
- a heading is not a chunk boundary. Emitting one chunk per heading gave 63
  chunks averaging 119 tokens against a 400-token budget. The budget was a
  ceiling nothing ever reached

Tables of contents were indexed as content: 1144 tokens of dot leaders in one
document, which lexical search matches for every query that quotes a section
title. Nothing folded long character runs, and BPE tokenization is quadratic on
them, so one such upload occupies the worker while every other tenant waits.

## Decision

Loaders return a `ParsedDocument` - a list of segments, each carrying its page
and its heading path - and the chunker **packs** neighbouring segments up to
the token budget, splitting only what is still too big.

Structure is taken from what the format actually declares:

- **PDF**: pages, and headings by type size. The size that sets most of the
  characters is the body; anything meaningfully larger is a heading, ranked
  largest-first into levels. This is the document describing itself
- **Markdown / HTML**: `#` and `<h1>`-`<h6>`, with `<main>` preferred over
  page chrome
- **DOCX**: paragraph styles named `Heading N`
- **XLSX / JSON**: one segment per sheet and per top-level key

Metadata stays honest across a pack: `section` is the deepest path every packed
segment shares, `page`/`page_end` the range actually covered. A chunk claims no
section and no page it only partly covers. Sections whose heading is a table of
contents are dropped, and runs of one repeated character are folded to 32.

Chunk size, overlap and strategy (`semantic`, `per-unit`, `fixed`) are settings
on the organization, next to the embedding model, because sources are shared
between bots: one corpus, one way of cutting it. `CHUNKER_VERSION` is stamped on
every source at ingest and joins the definition of "stale", so a shipped change
to the chunker offers a reindex instead of leaving a silently outdated index.

## Consequences

- On the corpus that exposed the defect: 63 chunks averaging 119 tokens became
  21 averaging 308, with no chunk under 97, and 71% of chunks carrying a real
  heading path where the regex had been losing it
- Citations can name a page, which a reader can turn to, instead of a chunk
  ordinal, which they can only trust
- A packed chunk sometimes covers two sections and then claims neither. This is
  deliberate, and bounded: packing stops at a section boundary once the chunk
  already holds half a budget, so only small sections merge
- Chunking became a supported setting, so it needs a reindex path and a version
  stamp - which the embedding model already needed anyway
- PDF heading detection is a heuristic over type size. It finds nothing in a
  document set entirely in one size, which is the correct answer for such a
  document: pages remain, and the text is still packed to the budget

## Alternatives considered

- **Keep the regex, add a minimum chunk size.** Fixes the fragments, not the
  lost breadcrumbs, and leaves list items outranking real headings
- **Pages only, no heading detection** (what the sibling project does with
  `pdf-parse`). Simpler and honest, but throws away structure this corpus
  really has - two thirds of chunks would lose a usable heading path
- **A layout-aware parser or a hosted document API.** Better on tables, which
  is the one thing still flattened here. A dependency and a bill for a case
  that is not yet the bottleneck; revisit if evals show tables costing recall
